"""Cliente do MDM de Coletores (Workspace ONE / AirWatch).

O console é ASP.NET MVC e a grade de coletores volta em HTML, não em JSON.
A extração aqui é feita pelos atributos `data-property` e `data-ats-id` das
células — não pela posição da coluna, que o usuário pode reordenar na tela.

Contrato levantado por diagnóstico contra o ambiente real (12/09/2026);
ver `docs/MDM_OBSOLESCENCIA.md`.
"""
from __future__ import annotations

import re

# A grade só responde com o fragmento quando a requisição se identifica como
# AJAX; sem isso o servidor devolve a página inteira e ignora os parâmetros.
CABECALHOS = {"X-Requested-With": "XMLHttpRequest"}

GRADE = "/AirWatch/Device/List/Search"

# `Page` é BASE ZERO: Page=0 é a primeira página (o paginador do HTML marca
# data-page="0" no botão "1"). Ordenar por nome deixa a lista estável entre
# as páginas — o padrão é Last Seen, que se reordena sozinho e faria a
# varredura repetir e pular coletores.
ORDENACAO = {"Sort": "DeviceFriendlyName", "Order": "Ascending"}


_RE_RODAPE = re.compile(r"Items?\s+([\d.,]+)\s*[-–]\s*([\d.,]+)\s+of\s+([\d.,]+)", re.I)
_RE_LINHA = re.compile(r"<tr\b[^>]*data-view-url=[\"']([^\"']+)[\"'][^>]*>", re.I)
_RE_ID = re.compile(r"Device/Details/Summary/(\d+)")


def _num(txt: str) -> int:
    return int(re.sub(r"[.,]", "", str(txt)))


def rodape(html: str) -> dict | None:
    """(de, ate, total) do rodapé "Items 1 - 100 of 15819".

    É o único sinal confiável de progresso: contar linhas não diz quantas
    faltam, e a lista se move enquanto a varredura roda.
    """
    m = _RE_RODAPE.search(html or "")
    if not m:
        return None
    return {"de": _num(m.group(1)), "ate": _num(m.group(2)), "total": _num(m.group(3))}


def _texto(html: str) -> str:
    t = re.sub(r"<[^>]*>", " ", html or "")
    t = t.replace("&nbsp;", " ").replace("&#32;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def _por_ats(bloco: str, ats: str) -> str:
    """Texto do elemento marcado com este data-ats-id, respeitando aninhamento.

    Parar no primeiro "<" não serve: o último contato, por exemplo, guarda o
    valor num <span> dentro do <div> marcado, e a leitura voltava vazia.
    """
    m = re.search(r"data-ats-id\s*=\s*[\"']" + re.escape(ats) + r"[\"']", bloco, re.I)
    if not m:
        return ""
    abre = bloco.rfind("<", 0, m.start())
    mt = re.match(r"<([a-zA-Z][\w-]*)", bloco[abre:]) if abre != -1 else None
    if not mt:
        return ""
    fim_abre = bloco.find(">", m.end())
    if fim_abre == -1:
        return ""
    if bloco[fim_abre - 1] == "/":      # elemento sem conteúdo
        return ""
    re_tag = re.compile(r"</?" + re.escape(mt.group(1)) + r"\b", re.I)
    prof, pos = 1, fim_abre + 1
    while True:
        mm = re_tag.search(bloco, pos)
        if not mm:
            return _texto(bloco[fim_abre + 1:])
        prof += -1 if bloco[mm.start() + 1] == "/" else 1
        if prof == 0:
            return _texto(bloco[fim_abre + 1:mm.start()])
        pos = mm.end()


def _celula(bloco: str, propriedade: str) -> str:
    """Conteúdo de uma célula, achada pelo data-property (não pela posição)."""
    m = re.search(
        r"<td\b[^>]*data-property=[\"']" + re.escape(propriedade) + r"[\"'][^>]*>(.*?)</td>",
        bloco, re.I | re.S)
    return m.group(1) if m else ""


def _fatiar_linhas(html: str) -> list[str]:
    """Corta o HTML nos <tr> que têm data-view-url — são as linhas de coletor.
    O resto da grade (cabeçalho, filtros, paginador) não tem esse atributo."""
    marcas = [m.start() for m in _RE_LINHA.finditer(html or "")]
    fora = []
    for i, ini in enumerate(marcas):
        fim = marcas[i + 1] if i + 1 < len(marcas) else len(html)
        fora.append(html[ini:fim])
    return fora


def parse_linha(bloco: str) -> dict:
    """Um coletor a partir do <tr> da grade."""
    mid = _RE_ID.search(bloco)
    celula_nome = _celula(bloco, "FriendlyName")
    celula_plat = _celula(bloco, "Platform")
    celula_visto = _celula(bloco, "LastPingDate")

    # "/ L234 / ljr234_coletor" — o último pedaço é o Group ID, que é o
    # usuário do coletor e carrega a loja.
    caminho = ""
    mp = re.search(r"class=[\"'][^\"']*PartialPath[^\"']*[\"'][^>]*>(.*?)<", celula_nome, re.I | re.S)
    if mp:
        caminho = _texto(mp.group(1))
    grupo = caminho.split("/")[-1].strip() if caminho else ""

    # Data absoluta fica no tooltip da célula; o texto visível é relativo ("7s").
    mdata = re.search(r"(\d{1,2}/\d{1,2}/\d{4}[^<]*)", celula_visto)

    return {
        "id": mid.group(1) if mid else "",
        "nome": _texto(re.search(r"id=[\"']FriendlyName[\"'][^>]*>(.*?)</span>",
                                 celula_nome, re.I | re.S).group(1))
                if re.search(r"id=[\"']FriendlyName[\"']", celula_nome, re.I) else "",
        "usuario": grupo,
        "caminho_og": _por_ats(celula_nome, "og-path") or caminho,
        "plataforma": _por_ats(celula_plat, "platform"),
        "modelo": _por_ats(celula_plat, "display-model"),
        "versao_os": _por_ats(celula_plat, "os-version"),
        "visto_relativo": _por_ats(celula_visto, "device-last-seen"),
        "visto_em": (mdata.group(1).strip() if mdata else ""),
        "propriedade": _por_ats(celula_nome, "ownership"),
        "gerenciamento": _por_ats(celula_nome, "management"),
        "conformidade": _por_ats(_celula(bloco, "ComplianceStatusName"),
                                 "device-compliance-status"),
        "acoes": (re.search(r'data-action-names="([^"]*)"', bloco, re.I).group(1).split()
                  if re.search(r'data-action-names="([^"]*)"', bloco, re.I) else []),
    }


def parse_grade(html: str) -> dict:
    """Todos os coletores de uma página, mais o rodapé para saber o progresso."""
    linhas = [parse_linha(b) for b in _fatiar_linhas(html)]
    return {"rodape": rodape(html), "coletores": [c for c in linhas if c["id"]]}


def paginas(total: int, por_pagina: int) -> range:
    """Índices de página (base zero) para varrer o parque inteiro."""
    if total <= 0 or por_pagina <= 0:
        return range(0)
    return range((total + por_pagina - 1) // por_pagina)
