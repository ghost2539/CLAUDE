"""Consulta de chamados do ServiceNow — incidents e RITMs, em lote.

A tela existe para uma pergunta que hoje se responde exportando do próprio
ServiceNow uma lista de cada vez: "destes N mil chamados, quero estes campos".

Três decisões que valem o comentário:

1.  **Quem lê é a conta de serviço** (o usuário `zabbix`, `SN_API_USER` no
    cofre), e SÓ lê. A regra do portal é que escrita no ServiceNow sai com a
    sessão de quem clicou; consulta em lote é leitura pura, e a sessão SSO
    do usuário não aguenta 5 mil registros sem expirar no meio.

2.  **Os campos oferecidos são os que essa conta enxerga de verdade.** Não
    basta listar o dicionário: um campo pode existir e a ACL barrar a
    leitura — aí a coluna vem vazia na exportação e parece dado faltando no
    chamado. Por isso, depois de ler o dicionário, pedimos um registro real
    com todos aqueles campos: o que volta é o que a conta lê. O ServiceNow
    descarta em silêncio o que a ACL nega, e é esse silêncio que vira a
    lista da tela.

3.  **A paginação da exportação anda por `sys_id`, não por offset.** Com
    `sysparm_offset` o resultado escorrega: os chamados continuam sendo
    criados e fechados enquanto a exportação roda, e uma linha que muda de
    página aparece duas vezes ou não aparece nenhuma. Ordenando por `sys_id`
    e pedindo `sys_id>último`, cada página é estável e o custo não cresce
    com a profundidade — que é o que permite passar de 5 mil sem o
    ServiceNow degradar.

A query nunca chega pronta da tela. A tela manda linhas de filtro
(campo, operador, valor); o campo é conferido contra a lista lida do
dicionário e o operador contra uma lista fechada. Assim `^` e `=` vindos de
um valor digitado não conseguem mudar o sentido da busca.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import get_settings
from core.security import require_permission

router = APIRouter(prefix="/api/sn-consulta", tags=["ServiceNow - Consulta"])

_cfg = get_settings()
_log = logging.getLogger("sn_consulta")

# A permissão é a mesma do módulo ServiceNow da barra lateral: quem vê a
# tela consulta; exportar é ação à parte, porque é o que sai do portal.
MODULO = "automacoes"


# ── As tabelas oferecidas ──────────────────────────────────────────────
# A tela do ServiceNow abre por `incident.do` e `task_list.do`; o `.do` é a
# página, a tabela por trás é esta. `task` é a tabela-mãe: um RITM e um
# incidente são os dois uma `task`, e é por ela que `task_list.do` lista os
# dois juntos. Fica oferecida para reproduzir exatamente aquela visão.
TABELAS: dict[str, dict] = {
    "incident": {
        "rotulo": "Incidentes",
        "tela": "incident.do",
        "campos_padrao": ["number", "opened_at", "short_description", "state",
                          "priority", "category", "subcategory",
                          "assignment_group", "assigned_to", "caller_id"],
    },
    "sc_req_item": {
        "rotulo": "RITMs (itens requisitados)",
        "tela": "sc_req_item.do",
        "campos_padrao": ["number", "opened_at", "short_description", "state",
                          "stage", "cat_item", "request",
                          "assignment_group", "assigned_to", "requested_for"],
    },
    "task": {
        "rotulo": "Tarefas — tabela-mãe (incidentes + RITMs juntos)",
        "tela": "task_list.do",
        "campos_padrao": ["number", "sys_class_name", "opened_at",
                          "short_description", "state", "priority",
                          "assignment_group", "assigned_to"],
    },
}

# Teto de segurança da exportação. Não é o "máximo que dá": é onde paramos
# de graça para não deixar uma consulta larga demais ocupar a conta de
# serviço por muito tempo. A resposta diz quando bateu no teto, em vez de
# entregar um arquivo truncado calado.
TETO_EXPORTACAO = 50000
PAGINA_REST = 1000
# A tela mostra uma amostra; o arquivo é que leva tudo. Trazer 5 mil linhas
# para o navegador trava a aba e não ajuda ninguém a conferir nada.
TETO_TELA = 1000


# ── Operadores aceitos ─────────────────────────────────────────────────
# Lista fechada. O valor da direita é o que entra na encoded query do
# ServiceNow. `precisa_valor=False` são os que não levam valor nenhum —
# se aceitassem, um valor colado ali iria para a query sem uso e sem
# conferência.
OPERADORES: dict[str, dict] = {
    "igual":        {"rotulo": "é igual a",       "sn": "=",          "valor": True},
    "diferente":    {"rotulo": "é diferente de",  "sn": "!=",         "valor": True},
    "contem":       {"rotulo": "contém",          "sn": "LIKE",       "valor": True},
    "nao_contem":   {"rotulo": "não contém",      "sn": "NOTLIKE",    "valor": True},
    "comeca":       {"rotulo": "começa com",      "sn": "STARTSWITH", "valor": True},
    "termina":      {"rotulo": "termina com",     "sn": "ENDSWITH",   "valor": True},
    "maior_igual":  {"rotulo": "a partir de (>=)", "sn": ">=",        "valor": True},
    "menor_igual":  {"rotulo": "até (<=)",        "sn": "<=",         "valor": True},
    "em":           {"rotulo": "está em (lista)", "sn": "IN",         "valor": True},
    "nao_em":       {"rotulo": "não está em",     "sn": "NOT IN",     "valor": True},
    "vazio":        {"rotulo": "está vazio",      "sn": "ISEMPTY",    "valor": False},
    "preenchido":   {"rotulo": "está preenchido", "sn": "ISNOTEMPTY", "valor": False},
}

# O que um valor de filtro NÃO pode conter. `^` separa condições e `=`
# separa campo de valor: um valor com qualquer um dos dois deixa de ser
# valor e vira estrutura da query. A vírgula é liberada só em IN/NOT IN,
# onde ela é o separador legítimo da lista.
_PROIBIDO_NO_VALOR = ("^", "=")
# `javascript:` numa encoded query é avaliado pelo servidor do ServiceNow.
# Não há uso legítimo disto vindo de uma caixa de texto da tela.
_RE_SCRIPT = re.compile(r"javascript\s*:", re.I)
_RE_CAMPO = re.compile(r"^[a-z0-9_.]{1,80}$")


# ── Cliente REST (conta de serviço, leitura) ───────────────────────────
def _sessao():
    from integracoes import http as http_saida
    return http_saida.sessao("servicenow", proxy=_cfg.SN_API_PROXY or None)


def _checar_conta():
    if not _cfg.SN_API_USER or not _cfg.SN_API_PASS:
        raise HTTPException(503, (
            "Conta de serviço do ServiceNow não configurada. Ela é quem lê os "
            "chamados nesta tela. Grave no cofre: SN_API_USER e SN_API_PASS."
        ))


def _get(caminho: str, params: dict, timeout: int = 60) -> dict:
    _checar_conta()
    s = _sessao()
    try:
        r = s.get(f"{_cfg.SN_API_BASE}{caminho}", params=params,
                  auth=(_cfg.SN_API_USER, _cfg.SN_API_PASS),
                  headers={"Accept": "application/json"}, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        # A mensagem do requests traz a URL, e a URL traz a query inteira.
        # Nada aí é segredo, mas também não ajuda quem está na tela.
        _log.warning("ServiceNow não respondeu: %s", exc)
        raise HTTPException(502, "ServiceNow não respondeu. Tente de novo em instantes.")
    if r.status_code == 401:
        raise HTTPException(502, (
            "ServiceNow recusou a conta de serviço (401). Confira SN_API_USER / "
            "SN_API_PASS no cofre e se a conta tem papel de API."
        ))
    if r.status_code == 403:
        raise HTTPException(502, (
            "A conta de serviço não tem acesso a esta tabela (403). Peça a "
            "liberação de leitura para o usuário da integração."
        ))
    if r.status_code >= 400:
        raise HTTPException(502, f"ServiceNow retornou {r.status_code}.")
    try:
        return r.json()
    except ValueError:
        raise HTTPException(502, "ServiceNow devolveu resposta que não é JSON.")


def _valor_plano(v) -> str:
    """O campo pode vir string ou {value, display_value} — sempre texto."""
    if isinstance(v, dict):
        return str(v.get("display_value") or v.get("value") or "")
    return "" if v is None else str(v)


# ── Descoberta de campos ───────────────────────────────────────────────
_cache_campos: dict[str, tuple[float, list[dict]]] = {}
_VALIDADE_CACHE = 30 * 60  # o dicionário do ServiceNow não muda no dia a dia


def _hierarquia(tabela: str) -> list[str]:
    """A tabela e as que ela herda, da filha para a mãe.

    `incident` guarda `short_description` na `task`, não nela mesma: sem
    subir a hierarquia, o dicionário devolve meia dúzia de campos e a tela
    parece quebrada. A cadeia é perguntada ao `sys_db_object` em vez de
    ficar escrita aqui, porque instância customizada insere tabela no meio.
    """
    cadeia, atual, visto = [], tabela, set()
    while atual and atual not in visto and len(cadeia) < 10:
        cadeia.append(atual)
        visto.add(atual)
        dados = _get("/api/now/table/sys_db_object", {
            "sysparm_query": f"name={atual}",
            "sysparm_fields": "super_class",
            "sysparm_display_value": "true",
            "sysparm_limit": "1",
        }, timeout=30)
        linhas = dados.get("result") or []
        atual = _valor_plano((linhas[0] if linhas else {}).get("super_class")) if linhas else ""
    return cadeia


def _campos_do_dicionario(tabela: str) -> list[dict]:
    nomes = _hierarquia(tabela) or [tabela]
    dados = _get("/api/now/table/sys_dictionary", {
        "sysparm_query": "nameIN" + ",".join(nomes) + "^elementISNOTEMPTY^ORDERBYelement",
        "sysparm_fields": "element,column_label,internal_type,name",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": "2000",
    })
    saida, vistos = [], set()
    for linha in dados.get("result") or []:
        nome = _valor_plano(linha.get("element"))
        if not nome or nome in vistos or not _RE_CAMPO.match(nome):
            continue
        vistos.add(nome)
        saida.append({
            "campo": nome,
            "rotulo": _valor_plano(linha.get("column_label")) or nome,
            "tipo": _valor_plano(linha.get("internal_type")),
            "tabela": _valor_plano(linha.get("name")),
        })
    return saida


def _filtrar_pelo_que_a_conta_le(tabela: str, campos: list[dict]) -> list[dict]:
    """Descarta o que a ACL nega à conta de serviço.

    O ServiceNow não erra quando a ACL barra um campo: ele simplesmente não
    manda a chave. Então pedimos um registro real com TODOS os campos e
    ficamos com as chaves que voltaram — que é a definição prática de "o que
    o usuário da integração enxerga".

    Sem registro nenhum na tabela não há o que comparar, e aí vale a lista
    do dicionário inteira: melhor oferecer a mais e o campo vir vazio do que
    esconder campo que a conta lê.
    """
    if not campos:
        return campos
    nomes = [c["campo"] for c in campos]
    legiveis: set[str] = set()
    # Em blocos: a URL tem limite de tamanho, e uma tabela larga passa de
    # 400 campos. Um bloco que estoure não derruba a descoberta inteira.
    for i in range(0, len(nomes), 100):
        bloco = nomes[i:i + 100]
        try:
            dados = _get(f"/api/now/table/{tabela}", {
                "sysparm_query": "ORDERBYDESCsys_created_on",
                "sysparm_fields": ",".join(bloco),
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": "1",
            }, timeout=45)
        except HTTPException as exc:
            _log.info("sonda de campos falhou em %s (bloco %d): %s", tabela, i, exc.detail)
            continue
        linhas = dados.get("result") or []
        if not linhas:
            return campos
        legiveis.update(linhas[0].keys())
    if not legiveis:
        return campos
    return [c for c in campos if c["campo"] in legiveis]


@router.get("/tabelas")
def listar_tabelas(req: Request):
    require_permission(req, MODULO, "view")
    return {"tabelas": [
        {"tabela": nome, "rotulo": d["rotulo"], "tela": d["tela"],
         "campos_padrao": d["campos_padrao"]}
        for nome, d in TABELAS.items()
    ], "operadores": [
        {"chave": k, "rotulo": v["rotulo"], "precisa_valor": v["valor"]}
        for k, v in OPERADORES.items()
    ]}


@router.get("/campos")
def listar_campos(req: Request, tabela: str, recarregar: bool = False):
    """Os campos que a conta de serviço lê nesta tabela."""
    require_permission(req, MODULO, "view")
    if tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {tabela}")
    agora = time.time()
    guardado = _cache_campos.get(tabela)
    if guardado and not recarregar and agora - guardado[0] < _VALIDADE_CACHE:
        campos, do_cache = guardado[1], True
    else:
        campos = _filtrar_pelo_que_a_conta_le(tabela, _campos_do_dicionario(tabela))
        _cache_campos[tabela] = (agora, campos)
        do_cache = False
    return {
        "tabela": tabela,
        "total": len(campos),
        "campos": campos,
        "campos_padrao": TABELAS[tabela]["campos_padrao"],
        "do_cache": do_cache,
        "conta": _cfg.SN_API_USER or "",
    }


# ── Montagem da query ──────────────────────────────────────────────────
class Filtro(BaseModel):
    campo: str = ""
    operador: str = "igual"
    valor: str = ""


class ConsultaIn(BaseModel):
    tabela: str = "incident"
    campos: list[str] = Field(default_factory=list)
    filtros: list[Filtro] = Field(default_factory=list)
    ordenar_por: str = ""
    ordem: str = "desc"
    pagina: int = 1
    por_pagina: int = 100
    # Valor de referência em vez do código interno (state=6 → "Resolvido").
    # É o que serve para ler; quem for cruzar com outro sistema desliga.
    exibir_rotulos: bool = True


def _campos_validos(tabela: str) -> dict[str, dict]:
    guardado = _cache_campos.get(tabela)
    if guardado and time.time() - guardado[0] < _VALIDADE_CACHE:
        campos = guardado[1]
    else:
        campos = _filtrar_pelo_que_a_conta_le(tabela, _campos_do_dicionario(tabela))
        _cache_campos[tabela] = (time.time(), campos)
    return {c["campo"]: c for c in campos}


def _conferir_campo(nome: str, validos: dict, rotulo: str) -> str:
    nome = (nome or "").strip()
    if not nome:
        raise HTTPException(422, f"{rotulo} vazio.")
    # Campo pontilhado (`assignment_group.name`) é legítimo e muito usado:
    # confere-se a raiz, que é o que existe no dicionário desta tabela.
    raiz = nome.split(".")[0]
    if not _RE_CAMPO.match(nome) or raiz not in validos:
        raise HTTPException(422, (
            f"{rotulo} desconhecido nesta tabela: {nome}. A lista de campos é "
            "a que a conta de serviço consegue ler."
        ))
    return nome


def _conferir_valor(valor: str, operador: str) -> str:
    valor = (valor or "").strip()
    if _RE_SCRIPT.search(valor):
        raise HTTPException(422, "Valor de filtro não pode conter script.")
    proibidos = _PROIBIDO_NO_VALOR
    if operador not in ("em", "nao_em") and "," in valor:
        # Fora de IN/NOT IN a vírgula não separa nada; deixá-la passar só
        # esconde que a pessoa quis uma lista e escolheu o operador errado.
        raise HTTPException(422, (
            "Vírgula no valor só vale com os operadores de lista "
            "(está em / não está em)."
        ))
    for c in proibidos:
        if c in valor:
            raise HTTPException(422, (
                f"Valor de filtro não pode conter '{c}' — esse caractere é "
                "estrutura da consulta do ServiceNow, não conteúdo."
            ))
    return valor


def _montar_query(corpo: ConsultaIn, validos: dict) -> str:
    partes: list[str] = []
    for f in corpo.filtros:
        if not (f.campo or "").strip():
            continue
        op = OPERADORES.get(f.operador)
        if not op:
            raise HTTPException(422, f"Operador desconhecido: {f.operador}")
        campo = _conferir_campo(f.campo, validos, "Campo de filtro")
        if not op["valor"]:
            partes.append(f"{campo}{op['sn']}")
            continue
        valor = _conferir_valor(f.valor, f.operador)
        if not valor:
            raise HTTPException(422, f"O filtro em '{campo}' está sem valor.")
        partes.append(f"{campo}{op['sn']}{valor}")
    return "^".join(partes)


def _contar(tabela: str, query: str) -> int:
    """Conta no servidor, sem baixar linha nenhuma.

    É o que responde "são 5 mil ou 50 mil?" antes de a pessoa decidir
    exportar — e sem gastar a conta de serviço trazendo o que não vai ser
    usado.
    """
    dados = _get(f"/api/now/stats/{tabela}", {
        "sysparm_query": query, "sysparm_count": "true",
    }, timeout=45)
    res = dados.get("result") or {}
    return int((res.get("stats") or {}).get("count") or 0)


@router.post("/buscar")
def buscar(corpo: ConsultaIn, req: Request):
    """Uma página do resultado, para conferir na tela antes de exportar."""
    require_permission(req, MODULO, "view")
    if corpo.tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {corpo.tabela}")
    validos = _campos_validos(corpo.tabela)

    escolhidos = [c for c in (corpo.campos or []) if (c or "").strip()]
    if not escolhidos:
        escolhidos = list(TABELAS[corpo.tabela]["campos_padrao"])
    escolhidos = [_conferir_campo(c, validos, "Campo") for c in escolhidos]

    query = _montar_query(corpo, validos)
    if corpo.ordenar_por:
        ordem = "ORDERBYDESC" if (corpo.ordem or "desc").lower() == "desc" else "ORDERBY"
        campo_ordem = _conferir_campo(corpo.ordenar_por, validos, "Campo de ordenação")
        query = (query + "^" if query else "") + ordem + campo_ordem

    por_pagina = max(1, min(int(corpo.por_pagina or 100), TETO_TELA))
    pagina = max(1, int(corpo.pagina or 1))
    dados = _get(f"/api/now/table/{corpo.tabela}", {
        "sysparm_query": query,
        "sysparm_fields": ",".join(escolhidos),
        "sysparm_display_value": "true" if corpo.exibir_rotulos else "false",
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": str(por_pagina),
        "sysparm_offset": str((pagina - 1) * por_pagina),
    })
    linhas = [{c: _valor_plano(l.get(c)) for c in escolhidos}
              for l in (dados.get("result") or [])]

    # A contagem é do servidor: a pessoa precisa saber que a busca dela pega
    # 12 mil chamados ANTES de mandar exportar.
    total = _contar(corpo.tabela, query)
    return {
        "tabela": corpo.tabela,
        "campos": escolhidos,
        "rotulos": {c: validos.get(c.split(".")[0], {}).get("rotulo", c) for c in escolhidos},
        "linhas": linhas,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total": total,
        "query": query,
        "teto_exportacao": TETO_EXPORTACAO,
    }


def _paginar_por_sys_id(tabela: str, query: str, campos: list[str],
                        rotulos: bool, teto: int):
    """Percorre o resultado inteiro sem usar offset.

    Offset escorrega: entre a página 3 e a página 4 alguém abre um chamado, o
    conjunto inteiro desloca e uma linha aparece duas vezes — ou some. Numa
    exportação de 5 mil linhas isso não é hipótese, é o caso comum.

    Ordenando por `sys_id` e pedindo `sys_id>último da página`, cada página é
    decidida por um valor imutável. De quebra, o banco do ServiceNow resolve
    isso por índice: a página 50 custa o mesmo que a página 1, enquanto
    `sysparm_offset=50000` faz o servidor contar 50 mil linhas para
    descartá-las.
    """
    pedidos = list(dict.fromkeys(campos + ["sys_id"]))
    ultimo, trazidos = "", 0
    while trazidos < teto:
        faixa = f"sys_id>{ultimo}^" if ultimo else ""
        dados = _get(f"/api/now/table/{tabela}", {
            "sysparm_query": f"{faixa}{query}^ORDERBYsys_id" if query
                             else f"{faixa}ORDERBYsys_id",
            "sysparm_fields": ",".join(pedidos),
            "sysparm_display_value": "true" if rotulos else "false",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": str(min(PAGINA_REST, teto - trazidos)),
        }, timeout=90)
        linhas = dados.get("result") or []
        if not linhas:
            return
        for l in linhas:
            yield l
        trazidos += len(linhas)
        # `sys_id` com display_value ligado ainda volta como o próprio id:
        # não é campo de referência, então não há rótulo para trocar.
        ultimo = _valor_plano(linhas[-1].get("sys_id"))
        if not ultimo:
            _log.warning("exportação de %s parou: página sem sys_id", tabela)
            return
        if len(linhas) < PAGINA_REST:
            return


@router.post("/exportar")
def exportar(corpo: ConsultaIn, req: Request):
    """O resultado inteiro, só com os campos escolhidos, em CSV."""
    require_permission(req, MODULO, "export")
    if corpo.tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {corpo.tabela}")
    validos = _campos_validos(corpo.tabela)

    escolhidos = [c for c in (corpo.campos or []) if (c or "").strip()]
    if not escolhidos:
        escolhidos = list(TABELAS[corpo.tabela]["campos_padrao"])
    escolhidos = [_conferir_campo(c, validos, "Campo") for c in escolhidos]
    query = _montar_query(corpo, validos)

    def linhas():
        buf = io.StringIO()
        # `;` e BOM: é o que o Excel em português abre com as colunas
        # separadas sem ninguém passar pelo assistente de importação.
        escritor = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        buf.write("﻿")
        escritor.writerow([validos.get(c.split(".")[0], {}).get("rotulo", c)
                           for c in escolhidos])
        escritor.writerow(escolhidos)
        yield buf.getvalue()
        buf.seek(0), buf.truncate(0)

        contados = 0
        for registro in _paginar_por_sys_id(corpo.tabela, query, escolhidos,
                                            corpo.exibir_rotulos, TETO_EXPORTACAO):
            escritor.writerow([_valor_plano(registro.get(c)) for c in escolhidos])
            contados += 1
            if buf.tell() > 64 * 1024:
                yield buf.getvalue()
                buf.seek(0), buf.truncate(0)
        if buf.tell():
            yield buf.getvalue()
            buf.seek(0), buf.truncate(0)
        # Um arquivo truncado calado é pior que um erro: a pessoa fecha o
        # mês com 50 mil de 63 mil chamados e não tem como saber.
        if contados >= TETO_EXPORTACAO:
            escritor.writerow([])
            escritor.writerow([f"ATENCAO: parou no teto de {TETO_EXPORTACAO} registros. "
                               "Estreite o filtro (por periodo, por exemplo) e exporte em partes."])
            yield buf.getvalue()

    nome = f"{corpo.tabela}-{time.strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        linhas(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
