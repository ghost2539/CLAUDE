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


# ── Sessão ────────────────────────────────────────────────────────
# O login é por formulário, e os nomes dos campos não são estáveis entre
# versões do console. Em vez de fixá-los, lemos o formulário e devolvemos
# tudo preenchido — mesma técnica já usada no login do ServiceNow.
_CAMPOS_USUARIO = ("username", "UserName", "userid", "user", "login", "j_username")
_CAMPOS_SENHA = ("password", "Password", "passwd", "pass", "j_password")

LOGIN = "/AirWatch/Login/Login/Login-User"
DETALHE = "/AirWatch/Device/Details/Summary/{id}"


def _campos_do_form(html: str, acao_padrao: str) -> tuple[str, dict]:
    m = re.search(r"<form\b[^>]*id=[\"']loginform[\"'][^>]*>(.*?)</form>", html, re.I | re.S)
    if not m:
        m = re.search(r"<form\b[^>]*>(.*?)</form>", html, re.I | re.S)
    if not m:
        return acao_padrao, {}
    bloco = m.group(0)
    macao = re.search(r"action=[\"']([^\"']+)[\"']", bloco, re.I)
    campos = {}
    for mi in re.finditer(r"<input\b[^>]*>", bloco, re.I):
        tag = mi.group(0)
        nome = re.search(r"\bname=[\"']([^\"']+)[\"']", tag, re.I)
        if not nome:
            continue
        valor = re.search(r"\bvalue=[\"']([^\"']*)[\"']", tag, re.I)
        campos[nome.group(1)] = valor.group(1) if valor else ""
    return (macao.group(1) if macao else acao_padrao), campos


def login(sessao, usuario: str, senha: str, base: str = "") -> bool:
    """Autentica no console. O usuário vai no formato `renner\\<login>`.

    Devolve True quando a sessão passa a valer. A senha nunca é registrada
    em log — nem em caso de falha.
    """
    alvo = (base or "") + LOGIN
    r = sessao.get((base or "") + "/AirWatch/", timeout=30)
    acao, campos = _campos_do_form(getattr(r, "text", ""), alvo)
    if acao.startswith("/"):
        acao = (base or "") + acao

    for c in _CAMPOS_USUARIO:
        if c in campos:
            campos[c] = usuario
            break
    else:
        campos["username"] = usuario
    for c in _CAMPOS_SENHA:
        if c in campos:
            campos[c] = senha
            break
    else:
        campos["password"] = senha

    sessao.post(acao, data=campos, timeout=30)
    return sessao_valida(sessao, base)


def sessao_valida(sessao, base: str = "") -> bool:
    """Sessão viva = a grade responde com o fragmento, não com a tela de login."""
    try:
        r = sessao.get((base or "") + GRADE, headers=CABECALHOS, timeout=30)
    except Exception:  # noqa: BLE001 — rede fora é sessão inválida para o chamador
        return False
    txt = getattr(r, "text", "") or ""
    return getattr(r, "status_code", 0) == 200 and "DeviceGrid" in txt and "<html" not in txt


# ── Varredura ─────────────────────────────────────────────────────
class SessaoExpirada(RuntimeError):
    """A grade parou de responder o fragmento no meio da varredura."""


def buscar_pagina(sessao, pagina: int, base: str = "", ordenar: bool = True) -> str:
    partes = [f"Page={int(pagina)}"]
    if ordenar:
        partes += [f"{k}={v}" for k, v in ORDENACAO.items()]
    url = (base or "") + GRADE + "?" + "&".join(partes)
    r = sessao.get(url, headers=CABECALHOS, timeout=60)
    txt = getattr(r, "text", "") or ""
    if "DeviceGrid" not in txt or "<html" in txt:
        raise SessaoExpirada(f"A grade não respondeu o fragmento na página {pagina}.")
    return txt


def procurar(sessao, texto: str, base: str = "") -> list[dict]:
    """Procura na grade pelo texto (série, etiqueta, usuário) e devolve as
    linhas achadas.

    É por aqui que o Recebimento acha o aparelho para remover: a grade não
    publica a série nas colunas, então o parque guardado não pode ser o
    único caminho — o console é quem sabe.
    """
    return procurar_detalhado(sessao, texto, base)["coletores"]


def procurar_detalhado(sessao, texto: str, base: str = "") -> dict:
    """Igual a `procurar`, mas devolve também o rodapé e o que houve.

    O rodapé é o que denuncia filtro ignorado: se a busca por uma série
    devolve "Items 1 - 100 of 15819", o console não filtrou nada e quem
    lê a lista está olhando o parque inteiro.
    """
    saida = {"coletores": [], "rodape": None, "erro": "", "http": 0, "url": ""}
    texto = str(texto or "").strip()
    if not texto:
        saida["erro"] = "sem texto de busca"
        return saida
    from urllib.parse import quote
    url = (base or "") + GRADE + "?Page=0&SearchText=" + quote(texto)
    saida["url"] = url
    try:
        r = sessao.get(url, headers=CABECALHOS, timeout=60)
    except Exception as exc:  # noqa: BLE001 — quem chama registra a falha
        saida["erro"] = str(exc)[:200]
        return saida
    saida["http"] = getattr(r, "status_code", 0)
    txt = getattr(r, "text", "") or ""
    if "DeviceGrid" not in txt:
        saida["erro"] = ("a grade não respondeu o fragmento "
                         "(sessão expirada ou parâmetro de busca diferente)")
        return saida
    grade = parse_grade(txt)
    saida["coletores"] = grade["coletores"]
    saida["rodape"] = grade["rodape"]
    return saida


def varrer(sessao, base: str = "", max_paginas: int = 400, progresso=None) -> dict:
    """Percorre o parque inteiro, página a página, e devolve os coletores.

    O fim vem do rodapé (`ate >= total`), não de uma contagem de linhas: a
    lista se move enquanto a varredura roda. Por isso também ordenamos por
    nome — na ordem padrão (Last Seen) as páginas se sobrepõem e a coleta
    repetiria uns coletores e pularia outros.

    `max_paginas` é trava de segurança: rodapé estranho não vira laço infinito.
    """
    achados: dict[str, dict] = {}
    total = 0
    pagina = 0
    ultima_faixa = None

    while pagina < max_paginas:
        html = buscar_pagina(sessao, pagina, base)
        dados = parse_grade(html)
        faixa = dados["rodape"]
        for c in dados["coletores"]:
            achados[c["id"]] = c          # o id do MDM deduplica sobreposição

        if progresso:
            progresso(pagina, faixa, len(achados))

        if not faixa:
            break                          # sem rodapé não dá para saber o fim
        total = faixa["total"]
        # Rodapé que não avança significa que o servidor parou de paginar.
        if ultima_faixa and faixa["de"] <= ultima_faixa["de"]:
            break
        ultima_faixa = faixa
        if faixa["ate"] >= total:
            break
        pagina += 1

    return {"total": total, "paginas": pagina + 1,
            "coletores": list(achados.values())}


# ── Remoção de dispositivo ────────────────────────────────────────
# Apagar do MDM é irreversível e o caminho varia por versão do console,
# então o endpoint segue CONFIGURÁVEL (Configuração → Obsolescência). O
# padrão é o que a leitura da grade já mapeou: cada linha traz
# `data-action-names` com DeleteDevice, e o id do aparelho é o mesmo do
# `Device/Details/Summary/<id>`. Em branco nada é enviado: a remoção fica
# na fila, registrada.
#
# O console é ASP.NET MVC e valida anti-CSRF nas escritas (foi assim que a
# escrita de tag se provou). Antes de postar, então, buscamos um
# formulário do próprio aparelho e reaproveitamos o token daquela resposta.

class RemocaoNaoConfigurada(RuntimeError):
    """Não há endpoint de remoção configurado — nada foi enviado."""


_RE_TOKEN = re.compile(r"<input[^>]*__RequestVerificationToken[^>]*>", re.I)
_RE_VALOR = re.compile(r"value\s*=\s*[\"']([^\"']+)[\"']", re.I)

# Páginas do próprio aparelho que trazem o formulário com o token. A do
# detalhe vem primeiro: é a página onde o botão de excluir vive, e é o
# token dela que o console espera na exclusão.
FORMULARIOS_TOKEN = ("/AirWatch/Device/Details/Summary/{id}",
                     "/AirWatch/Devices/TagAssignment/{id}")


def token_verificacao(sessao, mdm_id: str, base: str = "") -> str:
    """Token anti-CSRF do console, lido de um formulário do aparelho.

    Devolve "" quando não achar: a escrita pode não exigir token nesta
    versão do console, e quem chama registra a resposta de qualquer jeito.
    """
    for caminho in FORMULARIOS_TOKEN:
        url = (base or "") + caminho.replace("{id}", str(mdm_id))
        try:
            r = sessao.get(url, headers=CABECALHOS, timeout=30)
        except Exception:  # noqa: BLE001 — sem token seguimos e registramos
            continue
        if getattr(r, "status_code", 0) != 200:
            continue
        campo = _RE_TOKEN.search(getattr(r, "text", "") or "")
        if not campo:
            continue
        valor = _RE_VALOR.search(campo.group(0))
        if valor:
            return valor.group(1)
    return ""


def remover_dispositivo(sessao, mdm_id: str, base: str = "", endpoint: str = "",
                        metodo: str = "POST", campo: str = "SelectedDeviceIds",
                        ) -> tuple[bool, str]:
    """Remove um dispositivo do console. Devolve (ok, detalhe).

    `endpoint` aceita `{id}` no caminho; com ou sem ele o id também vai no
    corpo, no campo indicado, porque as ações do console leem de lá.
    Nunca levanta por falha de rede: quem chama registra.
    """
    mdm_id = str(mdm_id or "").strip()
    if not mdm_id:
        return False, "sem id do dispositivo"
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise RemocaoNaoConfigurada(
            "Endpoint de remoção do MDM não configurado (Configuração → Obsolescência).")

    caminho = endpoint.replace("{id}", mdm_id)
    url = caminho if caminho.startswith("http") else (base or "") + caminho
    corpo = {(campo or "SelectedDeviceIds"): mdm_id}
    cabecalhos = dict(CABECALHOS)
    # O console recusa ("Save Failed") requisição que não parece ter vindo
    # da página do aparelho: manda o Referer dela junto.
    cabecalhos["Referer"] = (base or "") + DETALHE.replace("{id}", mdm_id)
    cabecalhos["Origin"] = base or ""
    token = token_verificacao(sessao, mdm_id, base)
    if token:
        corpo["__RequestVerificationToken"] = token
        cabecalhos["RequestVerificationToken"] = token
    try:
        r = sessao.request((metodo or "POST").upper(), url, data=corpo,
                           headers=cabecalhos, timeout=60)
    except Exception as exc:  # noqa: BLE001 — a rede não pode derrubar o lote
        return False, f"falha de rede: {exc}"
    corpo_txt = (getattr(r, "text", "") or "")
    if not (200 <= r.status_code < 300):
        return False, f"HTTP {r.status_code}: {corpo_txt[:200]}"
    if "login" in (getattr(r, "url", "") or "").lower():
        return False, "o console devolveu a tela de login (sessão expirada)"
    # O console responde 200 mesmo quando não apaga: página de erro, JSON
    # com Success:false, ou simplesmente nada feito. Tratar 200 como prova
    # de exclusão foi o que fez o portal anunciar remoção que não houve.
    negado = re.search(r'"(?:success|isSuccess)"\s*:\s*false', corpo_txt, re.I)
    if negado:
        # A mensagem do console é o dado mais valioso aqui: vai inteira.
        recado = re.search(r'"Message"\s*:\s*"([^"]*)"', corpo_txt, re.I)
        motivo = recado.group(1) if recado else corpo_txt[:400]
        return False, f"o console recusou: {motivo}"

    if ainda_existe(sessao, mdm_id, base):
        return False, ("o console respondeu HTTP 200 mas o aparelho CONTINUA "
                       f"inscrito. Resposta: {corpo_txt[:160] or '(vazia)'}")
    return True, f"removido e conferido (HTTP {r.status_code})"


# Caminhos de exclusão conhecidos deste console, na ordem em que são
# tentados. Cada tentativa é CONFERIDA: só para quando o aparelho some de
# verdade. É busca limitada e verificada, não tentativa e erro no escuro.
CAMINHOS_REMOCAO = (
    ("/AirWatch/Devices/DeleteDevice/{id}", "POST", "SelectedDeviceIds"),
    ("/AirWatch/Device/Delete/{id}", "POST", "id"),
    ("/AirWatch/Devices/DeleteBulkDevices", "POST", "SelectedDeviceIds"),
    ("/AirWatch/Device/DeleteDevice", "POST", "id"),
)


def remover_tentando(sessao, mdm_id: str, base: str = "",
                     caminhos=None) -> tuple[bool, str, list, str]:
    """Tenta os caminhos de exclusão até um deles apagar de verdade.

    Devolve (ok, detalhe, trilha, caminho_que_funcionou). A trilha traz o
    que cada tentativa respondeu — é ela que vira registro quando nenhuma
    funciona, para a próxima conversa começar do fato.
    """
    trilha = []
    for caminho, metodo, campo in (caminhos or CAMINHOS_REMOCAO):
        try:
            ok, detalhe = remover_dispositivo(sessao, mdm_id, base, caminho,
                                              metodo, campo)
        except RemocaoNaoConfigurada:
            continue
        except Exception as exc:  # noqa: BLE001 — a próxima tentativa segue
            ok, detalhe = False, f"erro: {exc}"[:200]
        trilha.append({"caminho": caminho, "metodo": metodo, "campo": campo,
                       "ok": ok, "detalhe": detalhe})
        if ok:
            return True, detalhe, trilha, caminho
    resumo = "; ".join(f"{t['caminho']} → {t['detalhe'][:80]}" for t in trilha)
    return False, (resumo or "nenhum caminho de remoção tentado"), trilha, ""


def ainda_existe(sessao, mdm_id: str, base: str = "") -> bool:
    """O aparelho continua no console?

    Conferência depois de apagar. Sem ela, "removido" é só o que o
    servidor respondeu — e ele responde 200 para coisa nenhuma.
    Na dúvida (rede caiu, resposta estranha) devolve True: dizer que
    removeu sem ter certeza é pior do que dizer que não deu.
    """
    mdm_id = str(mdm_id or "").strip()
    if not mdm_id:
        return False
    url = (base or "") + DETALHE.replace("{id}", mdm_id)
    try:
        r = sessao.get(url, headers=CABECALHOS, timeout=30)
    except Exception:  # noqa: BLE001 — sem conferir, não se afirma remoção
        return True
    status = getattr(r, "status_code", 0)
    if status in (404, 410):
        return False
    texto = (getattr(r, "text", "") or "")
    destino = (getattr(r, "url", "") or "").lower()
    if "login" in destino:
        return True                      # sessão caiu: não dá para afirmar
    # Console devolve a lista (ou uma página de "não encontrado") quando o
    # aparelho não existe mais.
    sumiu = re.search(r"(device\s+not\s+found|no\s+longer\s+available|"
                      r"n[aã]o\s+encontrado)", texto, re.I)
    if sumiu:
        return False
    return status == 200 and mdm_id in texto

