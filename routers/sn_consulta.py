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

# ── Consulta por lista de chamados ─────────────────────────────────────
# O caso de uso que originou a tela: "tenho esta lista de 5 mil chamados,
# me traga estes campos deles".
#
# A encoded query vai na URL (`sysparm_query=numberIN INC1,INC2,...`), e URL
# tem limite de tamanho. Cinco mil números dão uns 60 KB — nenhum servidor
# aceita, e o erro que volta é 414 ou um 400 genérico que não explica nada.
# Por isso a lista é partida em blocos e cada bloco vira uma consulta; os
# resultados se somam.
#
# 250 números por bloco dão ~3 KB de query, bem abaixo dos ~8 KB que
# servidor e proxy costumam aceitar, e mantêm o número de idas ao
# ServiceNow razoável (5 mil chamados = 20 blocos).
LOTE_NUMEROS = 250
# Teto da lista colada. Não é "o máximo que dá": é onde paramos para uma
# colagem errada (a planilha inteira, em vez da coluna) não virar centenas
# de idas ao ServiceNow.
TETO_NUMEROS = 50000
# `number` é o campo do número do chamado nas três tabelas oferecidas —
# `incident`, `sc_req_item` e `task` herdam todas de `task`.
CAMPO_NUMERO = "number"
_RE_NUMERO = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,39}$")


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
    """O que a pessoa lê: o rótulo quando existe, senão o valor."""
    if isinstance(v, dict):
        return str(v.get("display_value") or v.get("value") or "")
    return "" if v is None else str(v)


def _valor_cru(v) -> str:
    """O valor guardado: UTC nas datas, código nas escolhas, sys_id nas referências.

    A medição de tempo precisa disto e não do rótulo: o rótulo de uma data
    sai no fuso e no formato do usuário da integração, e a conta de horas
    mudaria sozinha se esse usuário fosse reconfigurado.
    """
    if isinstance(v, dict):
        return str(v.get("value") or "")
    return "" if v is None else str(v)


# ── Descoberta de campos ───────────────────────────────────────────────
# (momento, campos, diagnóstico da sonda)
_cache_campos: dict[str, tuple[float, list[dict], dict]] = {}
_VALIDADE_CACHE = 30 * 60  # o dicionário do ServiceNow não muda no dia a dia


def _hierarquia(tabela: str) -> list[str]:
    """A tabela e as que ela herda, da filha para a mãe.

    `incident` guarda `number`, `opened_at` e `short_description` na `task`,
    não nela mesma. Sem subir a hierarquia o dicionário devolve só os campos
    próprios da incident, e some da tela justamente o número do chamado e a
    data de abertura — que é como este defeito apareceu.

    O nome da mãe sai de `super_class.name`, com `display_value=false`.
    Antes era `super_class` com `display_value=true`, e aí voltava o RÓTULO:
    `super_class` é referência a `sys_db_object`, cuja coluna de exibição é
    `label`. Para incident volta "Task" (com T maiúsculo) e a consulta
    seguinte virava `name=Task` — que só casa se a comparação do banco
    ignorar maiúscula. Para uma tabela cujo rótulo não é o nome ("Requested
    Item" para `sc_req_item`), não casa nunca. O dot-walk entrega o `name`
    direto e acaba com a dependência de sorte.

    A cadeia é perguntada ao ServiceNow em vez de ficar escrita aqui porque
    instalação customizada insere tabela no meio.
    """
    cadeia, atual, visto = [], tabela, set()
    while atual and atual not in visto and len(cadeia) < 10:
        cadeia.append(atual)
        visto.add(atual)
        dados = _get("/api/now/table/sys_db_object", {
            "sysparm_query": f"name={atual}",
            "sysparm_fields": "super_class.name",
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": "1",
        }, timeout=30)
        linhas = dados.get("result") or []
        primeira = linhas[0] if linhas else {}
        # O dot-walk pode voltar na chave "super_class.name" ou aninhado em
        # "super_class", conforme a versão da API. Aceita as duas.
        bruto = primeira.get("super_class.name")
        if bruto is None:
            pai = primeira.get("super_class")
            bruto = pai.get("name") if isinstance(pai, dict) else None
        atual = _valor_plano(bruto).strip().lower()
    return cadeia


def _campos_do_dicionario(tabela: str, nomes: list[str] | None = None) -> list[dict]:
    nomes = nomes or _hierarquia(tabela) or [tabela]
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


def _marcar_o_que_a_conta_le(tabela: str, campos: list[dict]) -> tuple[list[dict], dict]:
    """Anota em cada campo se a conta de serviço conseguiu lê-lo. Não remove.

    A versão anterior DESCARTAVA o que a sonda não confirmasse. A intenção
    era boa — campo barrado pela ACL vira coluna vazia no arquivo e parece
    dado faltando no chamado. O efeito foi pior: um campo sumido da tela é
    indistinguível de um defeito, e quando a sonda falhou por outro motivo
    (a hierarquia não subiu, um bloco deu erro) o que se viu foi uma tela
    sem o número do chamado e sem a data de abertura, sem nenhuma pista do
    porquê. Esconder não avisa; marcar avisa.

    Então agora todo campo do dicionário é oferecido, com `lido` dizendo o
    que a sonda achou:
        True  — a conta leu o campo num registro real;
        False — a conta NÃO leu; provavelmente ACL, e a coluna virá vazia;
        None  — não deu para saber (a sonda falhou, ou a tabela está vazia).

    A diferença entre False e None é o que faltava: "a permissão barra" e
    "não consegui perguntar" levam a ações diferentes.
    """
    if not campos:
        return campos, {"sondou": False, "motivo": "nenhum campo no dicionário"}
    nomes = [c["campo"] for c in campos]
    legiveis: set[str] = set()
    # Só os campos que a sonda REALMENTE perguntou. É o que separa "a conta
    # não lê" de "não deu para perguntar".
    perguntados: set[str] = set()
    erros: list[str] = []
    blocos_ok = 0
    vazia = False
    # Em blocos: a URL tem limite de tamanho, e uma tabela larga passa de
    # 400 campos.
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
            # Antes isto era um `continue` com log em nível info: cem campos
            # sumiam da tela e não sobrava sinal nenhum. Como a lista vem
            # ordenada por nome, o bloco perdido é uma faixa alfabética
            # inteira — `number` e `opened_at` caem no mesmo bloco.
            erros.append(f"campos {i + 1}–{i + len(bloco)}: {exc.detail}")
            _log.warning("sonda de campos falhou em %s (bloco %d): %s",
                         tabela, i, exc.detail)
            continue
        linhas = dados.get("result") or []
        if not linhas:
            vazia = True
            break
        blocos_ok += 1
        perguntados.update(bloco)
        legiveis.update(linhas[0].keys())

    if vazia or not blocos_ok:
        # Sem nada com que comparar, todo campo fica em "não sei" — e a tela
        # oferece todos. Melhor oferecer a mais e a coluna vir vazia do que
        # esconder campo que a conta lê.
        for c in campos:
            c["lido"] = None
        return campos, {
            "sondou": False,
            "motivo": ("a tabela não tem nenhum registro para comparar" if vazia
                       else "nenhum bloco da sonda respondeu"),
            "erros": erros,
        }

    sem_resposta = 0
    for c in campos:
        if c["campo"] in legiveis:
            c["lido"] = True
        elif c["campo"] in perguntados:
            c["lido"] = False
        else:
            # Bloco que falhou: dizer "a conta não lê" sobre um campo que nem
            # chegou a ser perguntado seria inventar diagnóstico.
            c["lido"] = None
            sem_resposta += 1
    return campos, {
        "sondou": True,
        "blocos_ok": blocos_ok,
        "blocos_erro": len(erros),
        "erros": erros,
        "sem_resposta": sem_resposta,
    }


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


def _campos_da_tabela(tabela: str, recarregar: bool = False) -> tuple[list[dict], dict, bool]:
    """Os campos da tabela, do cache ou recém-descobertos.

    Estava duplicado em dois lugares — a tela e a validação da consulta. Duas
    cópias do mesmo trecho é uma edição de distância de a tela oferecer um
    campo que a validação recusa.
    """
    guardado = _cache_campos.get(tabela)
    if guardado and not recarregar and time.time() - guardado[0] < _VALIDADE_CACHE:
        return guardado[1], guardado[2], True
    cadeia = _hierarquia(tabela) or [tabela]
    campos, sonda = _marcar_o_que_a_conta_le(
        tabela, _campos_do_dicionario(tabela, cadeia))
    sonda["hierarquia"] = cadeia

    # Rede de segurança. As colunas padrão da tabela existem — `number` e
    # `opened_at` estão na `task`, de onde incident e sc_req_item herdam. Se
    # a descoberta não as trouxe, o defeito é da descoberta, e a resposta
    # certa não é a tela ficar sem o número do chamado: é oferecê-las assim
    # mesmo e DIZER que a descoberta veio incompleta.
    achados = {c["campo"] for c in campos}
    faltando = [c for c in TABELAS[tabela]["campos_padrao"] if c not in achados]
    for nome in faltando:
        campos.append({"campo": nome, "rotulo": nome, "tipo": "",
                       "tabela": "", "lido": None, "do_padrao": True})
        _log.warning("campo padrão %s não veio do dicionário de %s; "
                     "oferecido assim mesmo", nome, tabela)
    sonda["padrao_ausentes"] = faltando
    campos.sort(key=lambda c: c["campo"])

    _cache_campos[tabela] = (time.time(), campos, sonda)
    return campos, sonda, False


@router.get("/campos")
def listar_campos(req: Request, tabela: str, recarregar: bool = False):
    """Os campos desta tabela, com o que a sonda achou sobre cada um."""
    require_permission(req, MODULO, "view")
    if tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {tabela}")
    campos, sonda, do_cache = _campos_da_tabela(tabela, recarregar)
    # A tela precisa saber se um campo do conjunto padrão não veio: é o aviso
    # que faltou quando `number` e `opened_at` sumiram sem explicação.
    padrao = list(TABELAS[tabela]["campos_padrao"])
    return {
        "tabela": tabela,
        "total": len(campos),
        "campos": campos,
        "campos_padrao": padrao,
        # O que a descoberta não achou e o portal repôs pela rede de
        # segurança: a tela avisa, em vez de a coluna simplesmente sumir.
        "padrao_ausentes": sonda.get("padrao_ausentes", []),
        "nao_lidos": sum(1 for c in campos if c.get("lido") is False),
        "sem_resposta": sum(1 for c in campos if c.get("lido") is None),
        "sonda": sonda,
        "hierarquia": sonda.get("hierarquia", []),
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
    # A lista de chamados, como a pessoa colou: separada por vírgula, espaço,
    # ponto e vírgula ou quebra de linha (colar uma coluna do Excel cai aqui).
    # Vazio = a consulta é só pelos filtros.
    numeros: str = ""
    ordenar_por: str = ""
    ordem: str = "desc"
    pagina: int = 1
    por_pagina: int = 100
    # Valor de referência em vez do código interno (state=6 → "Resolvido").
    # É o que serve para ler; quem for cruzar com outro sistema desliga.
    exibir_rotulos: bool = True
    # Medir quanto tempo cada chamado ficou numa fila. Fora do fluxo normal
    # porque custa uma leitura do histórico (sys_audit) por bloco de chamados
    # — quem só quer a lista não paga por isso.
    tempo_fila: str = ""


def _campos_validos(tabela: str) -> dict[str, dict]:
    return {c["campo"]: c for c in _campos_da_tabela(tabela)[0]}


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


# Colunas que o portal CALCULA — não existem no ServiceNow. Ficam no fim da
# visão, na ordem em que se lê: quanto tempo, em quantas idas, e se deu para
# medir. A última é obrigatória: sem ela, "0 h" e "não sei" viram a mesma
# célula na planilha, e a média sai errada sem ninguém perceber.
COLUNAS_TEMPO = [
    ("tempo_fila_horas", "Horas na fila"),
    ("tempo_fila_legivel", "Tempo na fila"),
    ("tempo_fila_passagens", "Idas à fila"),
    # O estado do chamado ao lado do tempo, porque muda a leitura do número:
    # um chamado cancelado com 40 h de fila não é atendimento de 40 h.
    ("estado_chamado", "Estado"),
    ("tempo_fila_base", "Base da medição"),
]
# Para medir é preciso o sys_id (é como o histórico endereça o chamado), os
# carimbos de abertura e encerramento (fecham o primeiro e o último
# intervalo), o ESTADO (para separar cancelado) e a FILA ATUAL (para o
# chamado que nunca trocou de fila). Entram na consulta mesmo sem estarem
# entre as colunas escolhidas, e não aparecem na saída por isso.
#
# `assignment_group.name` e não `assignment_group`: o dot-walk traz o NOME
# mesmo quando o campo volta como sys_id, e é o nome que se compara com
# "SPARE".
CAMPOS_PARA_MEDIR = ["sys_id", "opened_at", "closed_at", "resolved_at",
                     "state", "assignment_group.name"]


def _rotulos(campos: list[str], validos: dict) -> dict:
    """Rótulo de cada coluna, inclusive as que o portal calcula."""
    calculadas = dict(COLUNAS_TEMPO)
    return {c: calculadas.get(c) or validos.get(c.split(".")[0], {}).get("rotulo", c)
            for c in campos}


def _linhas_com_tempo(tabela: str, brutas: list[dict], fila: str) -> list[dict]:
    """Acrescenta as colunas de tempo de fila às linhas já lidas.

    Os carimbos e o código do estado saem do valor CRU (UTC, código), e o
    estado mostrado sai do rótulo. É por isso que a consulta pede
    `display_value=all` quando mede: os dois vêm na mesma resposta.
    """
    from routers.sn_tempo_fila import medir
    chamados = [{"sys_id": _valor_cru(l.get("sys_id")),
                 "opened_at": _valor_cru(l.get("opened_at")),
                 "closed_at": _valor_cru(l.get("closed_at")),
                 "resolved_at": _valor_cru(l.get("resolved_at")),
                 "estado": _valor_cru(l.get("state")),
                 "estado_rotulo": _valor_plano(l.get("state")),
                 "fila_atual": _valor_plano(l.get("assignment_group.name"))
                               or _valor_plano(l.get("assignment_group"))}
                for l in brutas]
    por_id = medir(tabela, chamados, fila)
    for linha in brutas:
        m = por_id.get(_valor_cru(linha.get("sys_id"))) or {}
        linha["tempo_fila_horas"] = "" if m.get("horas") is None else m["horas"]
        linha["tempo_fila_legivel"] = m.get("legivel") or ""
        linha["tempo_fila_passagens"] = ("" if m.get("passagens") is None
                                         else m["passagens"])
        linha["estado_chamado"] = m.get("estado") or ""
        # "Cancelado" vence as outras bases: é o que mais muda a leitura do
        # número, e quem olha a planilha precisa ver isso na mesma célula.
        linha["tempo_fila_base"] = (
            "Cancelado" if m.get("cancelado")
            else (m.get("base") if m.get("medido") else (m.get("motivo") or "não medido")))
    return brutas


def _numeros_pedidos(texto: str) -> list[str]:
    """Os chamados que a pessoa colou, normalizados e sem repetição.

    Aceita o que sai de uma colagem de verdade: vírgula, ponto e vírgula,
    espaço, tabulação e quebra de linha, tudo misturado — colar uma coluna do
    Excel cai aqui, e colar uma célula com aspas também.

    A ordem é preservada porque é a ordem da planilha de quem pediu: na hora
    de conferir o que não foi encontrado, "a linha 300" ainda quer dizer
    alguma coisa.
    """
    saida: list[str] = []
    vistos: set[str] = set()
    for bruto in re.split(r"[\s,;]+", (texto or "").strip()):
        n = bruto.strip().strip("\"'").upper()
        if not n:
            continue
        # O número entra na encoded query, onde `^` e `=` são estrutura e a
        # vírgula separa a lista. Recusar aqui é melhor que deixar um número
        # torto mudar o sentido da consulta inteira.
        if not _RE_NUMERO.match(n):
            raise HTTPException(422, (
                f"Número de chamado inválido: {n[:60]!r}. Esperado algo como "
                "INC1234567 ou RITM1234567 — letras, números, ponto, hífen "
                "ou sublinhado."
            ))
        if n not in vistos:
            vistos.add(n)
            saida.append(n)
    if len(saida) > TETO_NUMEROS:
        raise HTTPException(422, (
            f"{len(saida)} chamados na lista, e o teto é {TETO_NUMEROS}. "
            "Confira se não foi colada a planilha inteira no lugar da coluna "
            "dos números; se forem todos mesmo, consulte em partes."
        ))
    return saida


def _blocos(numeros: list[str], tamanho: int = LOTE_NUMEROS):
    for i in range(0, len(numeros), tamanho):
        yield numeros[i:i + tamanho]


def _query_do_bloco(bloco: list[str], base: str) -> str:
    """`numberIN ...` do bloco, somado ao resto dos filtros."""
    q = CAMPO_NUMERO + "IN" + ",".join(bloco)
    return (q + "^" + base) if base else q


def _percorrer(tabela: str, base: str, numeros: list[str], campos: list[str],
               rotulos, teto: int):
    """Todas as linhas do resultado — por lista de chamados ou pelos filtros.

    Sem lista, é a varredura de sempre. Com lista, é um `numberIN` por bloco,
    e cada bloco ainda pagina por `sys_id`: um bloco de 250 números devolve no
    máximo 250 linhas (o número é único), mas a paginação sai de graça e cobre
    o caso de alguém mexer no tamanho do lote.
    """
    if not numeros:
        yield from _paginar_por_sys_id(tabela, base, campos, rotulos, teto)
        return
    trazidos = 0
    for bloco in _blocos(numeros):
        if trazidos >= teto:
            return
        for linha in _paginar_por_sys_id(tabela, _query_do_bloco(bloco, base),
                                         campos, rotulos, teto - trazidos):
            yield linha
            trazidos += 1


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

    base = _montar_query(corpo, validos)
    numeros = _numeros_pedidos(corpo.numeros)
    por_pagina = max(1, min(int(corpo.por_pagina or 100), TETO_TELA))
    pagina = max(1, int(corpo.pagina or 1))

    if numeros:
        return _buscar_por_lista(corpo, base, numeros, escolhidos, validos, por_pagina)

    query = base
    if corpo.ordenar_por:
        ordem = "ORDERBYDESC" if (corpo.ordem or "desc").lower() == "desc" else "ORDERBY"
        campo_ordem = _conferir_campo(corpo.ordenar_por, validos, "Campo de ordenação")
        query = (query + "^" if query else "") + ordem + campo_ordem

    fila = (corpo.tempo_fila or "").strip()
    pedidos = list(dict.fromkeys(escolhidos + CAMPOS_PARA_MEDIR)) if fila else escolhidos
    dados = _get(f"/api/now/table/{corpo.tabela}", {
        "sysparm_query": query,
        "sysparm_fields": ",".join(pedidos),
        # Medindo, pede-se `all`: cada campo volta com `value` (UTC nas
        # datas, código nas escolhas) E `display_value` (o rótulo). Antes a
        # medição desligava os rótulos, e a planilha inteira vinha com
        # código cru — o preço de medir era perder a leitura de todo o resto.
        "sysparm_display_value": "all" if fila else (
            "true" if corpo.exibir_rotulos else "false"),
        "sysparm_exclude_reference_link": "true",
        "sysparm_limit": str(por_pagina),
        "sysparm_offset": str((pagina - 1) * por_pagina),
    })
    brutas = dados.get("result") or []
    if fila:
        brutas = _linhas_com_tempo(corpo.tabela, brutas, fila)
        escolhidos = escolhidos + [c for c, _ in COLUNAS_TEMPO]
    linhas = [{c: _valor_plano(l.get(c)) for c in escolhidos} for l in brutas]

    # A contagem é do servidor: a pessoa precisa saber que a busca dela pega
    # 12 mil chamados ANTES de mandar exportar.
    total = _contar(corpo.tabela, query)
    return {
        "tabela": corpo.tabela,
        "campos": escolhidos,
        "rotulos": _rotulos(escolhidos, validos),
        "linhas": linhas,
        "pagina": pagina,
        "por_pagina": por_pagina,
        "total": total,
        "query": query,
        "por_lista": False,
        "pedidos": 0,
        "nao_encontrados": [],
        "nao_encontrados_total": 0,
        "teto_exportacao": TETO_EXPORTACAO,
        "teto_numeros": TETO_NUMEROS,
    }


def _buscar_por_lista(corpo: ConsultaIn, base: str, numeros: list[str],
                      escolhidos: list[str], validos: dict, por_pagina: int) -> dict:
    """Consulta por lista de chamados: a lista inteira, em blocos.

    Aqui não há paginação por offset como na busca por filtro, e o motivo é
    que a pergunta é outra. Quem cola 5 mil números quer saber duas coisas:
    quantos foram encontrados e QUAIS não foram. A segunda só se responde
    percorrendo a lista toda — não dá para adivinhar na página 1 que o
    chamado do bloco 17 não existe.

    Então percorre-se tudo, uma vez. Para não segurar 5 mil linhas na
    memória à toa, só as primeiras `por_pagina` são guardadas inteiras; das
    demais fica apenas o número, que é o que a conferência precisa.

    Um chamado "não encontrado" pode ser três coisas, e a tela diz isso: não
    existe, está em outra tabela (um RITM procurado em `incident`), ou os
    filtros do formulário o excluíram.
    """
    fila = (corpo.tempo_fila or "").strip()
    amostra_bruta: list[dict] = []
    achados: set[str] = set()
    campos_pedidos = list(dict.fromkeys(
        escolhidos + [CAMPO_NUMERO] + (CAMPOS_PARA_MEDIR if fila else [])))
    rotulos = "all" if fila else corpo.exibir_rotulos
    for linha in _percorrer(corpo.tabela, base, numeros, campos_pedidos,
                            rotulos, TETO_EXPORTACAO):
        achados.add(_valor_plano(linha.get(CAMPO_NUMERO)).upper())
        if len(amostra_bruta) < por_pagina:
            amostra_bruta.append(linha)
    if fila:
        # Só a amostra é medida: medir 5 mil chamados para mostrar 100 na tela
        # seriam 20 leituras do histórico para nada. A exportação mede tudo.
        amostra_bruta = _linhas_com_tempo(corpo.tabela, amostra_bruta, fila)
        escolhidos = escolhidos + [c for c, _ in COLUNAS_TEMPO]
    amostra = [{c: _valor_plano(l.get(c)) for c in escolhidos} for l in amostra_bruta]
    faltando = [n for n in numeros if n not in achados]
    return {
        "tabela": corpo.tabela,
        "campos": escolhidos,
        "rotulos": _rotulos(escolhidos, validos),
        "linhas": amostra,
        "tempo_fila": fila,
        "tempo_fila_so_amostra": bool(fila) and len(achados) > len(amostra),
        "pagina": 1,
        "por_pagina": por_pagina,
        "total": len(achados),
        "query": _query_do_bloco(numeros[:3], base) + (" …" if len(numeros) > 3 else ""),
        "por_lista": True,
        "pedidos": len(numeros),
        "blocos": (len(numeros) + LOTE_NUMEROS - 1) // LOTE_NUMEROS,
        # A lista inteira de faltantes pode ser enorme; a tela mostra as
        # primeiras e o arquivo exportado leva todas.
        "nao_encontrados": faltando[:200],
        "nao_encontrados_total": len(faltando),
        "teto_exportacao": TETO_EXPORTACAO,
        "teto_numeros": TETO_NUMEROS,
    }


def _paginar_por_sys_id(tabela: str, query: str, campos: list[str],
                        rotulos, teto: int):
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
            # `rotulos` pode ser True/False ou a string "all" (medindo).
            "sysparm_display_value": (rotulos if isinstance(rotulos, str)
                                      else ("true" if rotulos else "false")),
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
    base = _montar_query(corpo, validos)
    numeros = _numeros_pedidos(corpo.numeros)
    fila = (corpo.tempo_fila or "").strip()
    # As colunas do arquivo: as escolhidas, mais as calculadas quando se mede.
    colunas = escolhidos + ([c for c, _ in COLUNAS_TEMPO] if fila else [])

    def linhas():
        buf = io.StringIO()
        # `;` e BOM: é o que o Excel em português abre com as colunas
        # separadas sem ninguém passar pelo assistente de importação.
        escritor = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        buf.write("﻿")
        rot = _rotulos(colunas, validos)
        escritor.writerow([rot[c] for c in colunas])
        escritor.writerow(colunas)
        yield buf.getvalue()
        buf.seek(0), buf.truncate(0)

        contados = 0
        achados: set[str] = set()
        # Com lista, o número entra nos campos pedidos mesmo que não esteja
        # entre as colunas escolhidas: é por ele que se sabe quem faltou.
        pedidos = list(dict.fromkeys(
            escolhidos
            + ([CAMPO_NUMERO] if numeros else [])
            + (CAMPOS_PARA_MEDIR if fila else [])))
        rotulos = "all" if fila else corpo.exibir_rotulos

        # A medição vai em lotes: uma leitura do histórico por lote, em vez de
        # uma por chamado. O lote é do mesmo tamanho do bloco da lista, pelo
        # mesmo motivo — a consulta ao histórico também viaja na URL.
        def medidos():
            lote: list[dict] = []
            for reg in _percorrer(corpo.tabela, base, numeros, pedidos,
                                  rotulos, TETO_EXPORTACAO):
                if not fila:
                    yield reg
                    continue
                lote.append(reg)
                if len(lote) >= LOTE_NUMEROS:
                    yield from _linhas_com_tempo(corpo.tabela, lote, fila)
                    lote = []
            if lote:
                yield from _linhas_com_tempo(corpo.tabela, lote, fila)

        for registro in medidos():
            if numeros:
                achados.add(_valor_plano(registro.get(CAMPO_NUMERO)).upper())
            escritor.writerow([_valor_plano(registro.get(c)) for c in colunas])
            contados += 1
            if buf.tell() > 64 * 1024:
                yield buf.getvalue()
                buf.seek(0), buf.truncate(0)
        if buf.tell():
            yield buf.getvalue()
            buf.seek(0), buf.truncate(0)

        # O que foi pedido e não voltou. Vai no MESMO arquivo, no fim, porque
        # é o que fecha a conferência: a pessoa colou 5 mil números e precisa
        # saber quais dos 5 mil não estão ali — e um segundo arquivo se perde.
        if numeros:
            faltando = [n for n in numeros if n not in achados]
            escritor.writerow([])
            escritor.writerow([f"Pedidos: {len(numeros)}",
                               f"Encontrados: {len(achados)}",
                               f"Nao encontrados: {len(faltando)}"])
            if faltando:
                escritor.writerow(["Nao encontrados nesta tabela "
                                   "(pode ser outra tabela, ou os filtros excluiram):"])
                for n in faltando:
                    escritor.writerow([n])
                    if buf.tell() > 64 * 1024:
                        yield buf.getvalue()
                        buf.seek(0), buf.truncate(0)
            yield buf.getvalue()
            buf.seek(0), buf.truncate(0)

        # Um arquivo truncado calado é pior que um erro: a pessoa fecha o
        # mês com 50 mil de 63 mil chamados e não tem como saber.
        if contados >= TETO_EXPORTACAO:
            escritor.writerow([])
            escritor.writerow([f"ATENCAO: parou no teto de {TETO_EXPORTACAO} registros. "
                               "Estreite o filtro (por periodo, por exemplo) e exporte em partes."])
            yield buf.getvalue()

    def linhas_protegidas():
        """O mesmo fluxo, mas nenhum erro vira arquivo vazio.

        Com `StreamingResponse` o HTTP 200 e os cabeçalhos já saíram quando a
        primeira linha é gerada. Uma exceção depois disso não vira erro na
        tela: vira download truncado — e foi assim que uma exportação chegou
        vazia sem ninguém saber por quê. Quem baixa não tem como distinguir
        "nenhum chamado" de "quebrou no meio".

        Então o erro vai PARA DENTRO do arquivo, na última linha, onde quem
        abrir a planilha vê.
        """
        try:
            yield from linhas()
        except HTTPException as exc:
            _log.warning("exportação interrompida: %s", exc.detail)
            yield ("\r\n\r\nERRO: a exportacao parou no meio e este arquivo "
                   "esta INCOMPLETO.\r\n" + str(exc.detail).replace("\n", " ") + "\r\n")
        except Exception as exc:  # noqa: BLE001
            _log.error("exportação interrompida: %s", exc, exc_info=True)
            yield ("\r\n\r\nERRO: a exportacao parou no meio e este arquivo "
                   "esta INCOMPLETO.\r\n"
                   f"{type(exc).__name__}: {str(exc)[:300]}".replace("\n", " ") + "\r\n")

    nome = f"{corpo.tabela}-{time.strftime('%Y%m%d-%H%M')}.csv"
    return StreamingResponse(
        linhas_protegidas(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
