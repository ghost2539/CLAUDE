"""Chamados das filas de técnico de campo, com o tempo que ficaram nelas.

Coleta histórica de envio de técnico de campo às lojas. Sai por dois
caminhos, e a lógica é UMA só:

*   o botão **Exportar - DADOS 2**, na aba ServiceNow → Consulta de chamados;
*   `scripts/chamados_campo_lojas.py`, para rodar no servidor sem navegador.

As funções puras estão aqui e são importadas pelos dois. Só o transporte
muda: o botão usa a conta de serviço pelo `_get` do portal, o script usa a
credencial do ambiente. Duas cópias da mesma regra divergem — e regra de
medição que diverge não dá erro, dá número diferente.

DUAS FASES, nesta ordem, que é o que torna a coleta viável
----------------------------------------------------------
1.  **Os chamados**, pela `task_sla` — é onde o grupo de atribuição prende
    esse tipo de atendimento.
2.  **O tempo em fila**, pelo histórico de troca de `assignment_group`
    (`sys_audit`), em LOTES. Uma leitura por lote, não uma por chamado: para
    20 mil chamados são ~130 chamadas em vez de 20 mil.

O QUE SAI ERRADO SEM DAR ERRO
------------------------------
*   **Chamado contado várias vezes.** Uma `task` tem uma linha de `task_sla`
    por SLA. Sem deduplicar, o mesmo atendimento entra três, quatro vezes na
    contagem do mês.
*   **`sys_audit.tablename`** guarda a tabela REAL (`incident`,
    `sc_req_item`), não `task`. Filtrar por `task` não casa com nada, e o
    resultado é zero para todo mundo.
*   **A fila gravada por sys_id** no histórico: comparar com o nome não casa
    e dá zero em tudo.
*   **Fechar a conta no encerramento** em vez da resolução. O encerramento é
    automático dias depois e infla o tempo.
*   **A ordem do E/OU na query.** `^OR` agrupa com a condição anterior; com
    as datas antes das filas, o período valeria só para a última fila.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.security import require_permission
from routers.sn_consulta import MODULO, _blocos, _get, _valor_plano
from routers.sn_tempo_fila import _humano, intervalos_da_fila

router = APIRouter(prefix="/api/sn-consulta/campo-lojas",
                   tags=["ServiceNow - Consulta"])
_log = logging.getLogger("sn_campo_lojas")

# As quatro filas de técnico de campo. Casadas por TRECHO do nome: se a
# instalação tiver sufixo no grupo, a busca por igualdade perderia o chamado.
FILAS = [
    "TI_N2_FLD_ENACEL_LOJAS",
    "TI_N2_FLD_RNR_VSiT",
    "TI_N2_FLD_SKY_LOJAS",
    "TI_N2_FLD_RNR_LOJAS_REMOTO",
]

CAMPO_FILA = "assignment_group"
LOTE_SYS_ID = 150            # sys_id tem 32 caracteres; a query viaja na URL
TETO_CHAMADOS = 100000
_RE_SYS_ID = re.compile(r"^[0-9a-f]{32}$")
_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FMT = "%Y-%m-%d %H:%M:%S"

COLUNAS = [
    ("mes", "Mês"),
    ("numero", "Chamado"),
    ("aberto_em", "Aberto em"),
    ("solicitante", "Solicitante"),
    ("categoria", "Categoria"),
    ("subcategoria", "Subcategoria"),
    ("fila", "Fila"),
    ("horas_na_fila", "Horas na fila"),
    ("tempo_na_fila", "Tempo na fila"),
    ("idas_a_fila", "Idas à fila"),
    ("estado", "Estado"),
    ("fim_contagem", "Fim da contagem"),
    ("fim_origem", "Fim veio de"),
    ("base_medicao", "Base da medição"),
]

# A consulta pela `task` direta: TODO campo é local, nenhum dot-walk. É o que
# faz a coleta caber no tempo — pela `task_sla` são 11 junções por linha
# (task_sla → task → sys_user_group, uma por campo pontilhado), e a diferença
# medida em produção foi de minutos por mês contra segundos.
#
# O conjunto de chamados é o mesmo: são as tasks daquelas filas no período. A
# `task_sla` era o caminho para achá-las, não o dado em si.
CAMPOS_TASK = ",".join([
    "sys_id", "number", "opened_at", "opened_by", "category", "subcategory",
    CAMPO_FILA, "state", "sys_class_name", "resolved_at", "closed_at",
])

CAMPOS_TASK_SLA = ",".join([
    # `task` cru além do `task.sys_id`: em algumas versões o dot-walk do
    # sys_id não volta, e a referência crua traz o sys_id no `value`.
    "task",
    "task.sys_id", "task.number", "task.opened_at",
    "task.caller_id", "task.opened_by", "task.requested_for",
    "task.category", "task.subcategory",
    f"task.{CAMPO_FILA}.name", "task.state",
    "task.resolved_at", "task.closed_at",
])


def _quando(bruto) -> datetime | None:
    texto = _valor_plano(bruto).strip()
    if not texto:
        return None
    for fmt in (_FMT, "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def campo(linha: dict, caminho: str) -> str:
    """Um campo pontilhado, aceitando as formas em que a API o devolve.

    `sysparm_fields=task.sys_id` pode voltar de três jeitos, conforme a
    versão da API e o `sysparm_display_value`:

        {"task.sys_id": "abc..."}                  ← chave pontilhada
        {"task": {"sys_id": "abc..."}}             ← aninhado na referência
        {"task": {"value": "abc..."}}              ← só a referência

    Ler só a primeira descarta a linha inteira em silêncio nas outras duas —
    e o efeito não é erro: é zero chamado, arquivo com cabeçalho e nada.
    """
    if caminho in linha:
        return _valor_plano(linha[caminho])
    if "." not in caminho:
        return ""
    raiz, resto = caminho.split(".", 1)
    valor = linha.get(raiz)
    if isinstance(valor, dict):
        if resto in valor:
            return _valor_plano(valor[resto])
        # `task.sys_id` pedido e só a referência voltou: o `value` de uma
        # referência É o sys_id.
        if resto == "sys_id":
            return _valor_plano(valor.get("value"))
        return ""
    if resto == "sys_id":
        return _valor_plano(valor)
    return ""


def qual_fila(nome: str) -> str:
    """Qual das quatro filas este grupo é. Vazio se nenhuma."""
    alto = (nome or "").upper()
    for f in FILAS:
        if f.upper() in alto:
            return f
    return ""


_cache_ids_filas: dict[str, str] = {}


def ids_das_filas() -> dict[str, str]:
    """sys_id → nome das quatro filas. Uma consulta pequena, guardada.

    Existe por causa de VELOCIDADE, e velocidade aqui é a diferença entre
    responder e tomar 504 do proxy. Filtrar `task.assignment_group.nameLIKE`
    obriga o ServiceNow a juntar `task_sla` → `task` → `sys_user_group` e
    varrer por SUBSTRING, para cada linha, em 18 meses de dados. Filtrando
    pelo sys_id do grupo, vira igualdade num campo indexado.
    """
    if _cache_ids_filas:
        return _cache_ids_filas
    ors = "^OR".join(f"nameLIKE{f}" for f in FILAS)
    dados = _get("/api/now/table/sys_user_group", {
        "sysparm_query": ors, "sysparm_fields": "sys_id,name",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true", "sysparm_limit": "200",
    }, timeout=60)
    for l in dados.get("result") or []:
        nome = _valor_plano(l.get("name"))
        if qual_fila(nome):
            _cache_ids_filas[_valor_plano(l.get("sys_id"))] = nome
    return _cache_ids_filas


def query_task_sla(desde: str, ate: str, ids: dict[str, str] | None = None) -> str:
    """A consulta da fase 1, pelo caminho mais barato que existir.

    Duas coisas mudaram aqui depois de a consulta tomar 504:

    1.  **Pelo sys_id do grupo**, quando dá para resolvê-lo. Igualdade em
        campo indexado, em vez de `LIKE` por substring num nome que está a
        duas referências de distância.
    2.  **Datas literais** em vez de `javascript:gs.dateGenerate(...)`. O
        `javascript:` é avaliado pelo servidor do ServiceNow, e numa
        consulta grande isso pesa sem precisar.

    O caminho por nome fica como reserva: se a conta não lê
    `sys_user_group`, ou se nenhum dos quatro grupos existe com esse nome, a
    consulta ainda funciona — só mais devagar.

    `^OR` agrupa com a condição ANTERIOR; um `^` seguinte começa novo grupo
    em E. Por isso as filas vêm primeiro e as datas depois: invertido, o
    período valeria só para a última fila.
    """
    if ids:
        alvo = f"task.{CAMPO_FILA}IN" + ",".join(sorted(ids))
    else:
        alvo = "^OR".join(f"task.{CAMPO_FILA}.nameLIKE{f}" for f in FILAS)
    return (f"{alvo}"
            f"^task.opened_at>={desde} 00:00:00"
            f"^task.opened_at<={ate} 23:59:59")


def query_task(desde: str, ate: str, ids: dict[str, str] | None = None) -> str:
    """A mesma seleção, direto na `task`: sem junção, sem dot-walk."""
    if ids:
        alvo = f"{CAMPO_FILA}IN" + ",".join(sorted(ids))
    else:
        alvo = "^OR".join(f"{CAMPO_FILA}.nameLIKE{f}" for f in FILAS)
    return (f"{alvo}"
            f"^opened_at>={desde} 00:00:00"
            f"^opened_at<={ate} 23:59:59")


def montar_da_task(linhas: list[dict], ids: dict[str, str] | None = None) -> list[dict]:
    """Uma linha por chamado, a partir da `task`. Campos locais, sem dot-walk."""
    mapa = ids or {}
    saida: list[dict] = []
    vistos: set[str] = set()
    for l in linhas:
        sid = campo(l, "sys_id")
        if not sid or sid in vistos:
            continue
        # A fila sai de três lugares, nesta ordem: o rótulo do grupo, o
        # sys_id traduzido pelo mapa que já temos, ou o valor cru. Depender
        # de um só descarta a linha inteira em silêncio quando ele não vem —
        # e foi assim que a coleta voltou com ZERO chamado.
        bruto = l.get(CAMPO_FILA)
        nome_grupo = _valor_plano(bruto)
        fila = qual_fila(nome_grupo)
        if not fila:
            cru = bruto.get("value") if isinstance(bruto, dict) else bruto
            fila = qual_fila(mapa.get(_valor_plano(cru), ""))
        if not fila:
            continue
        vistos.add(sid)
        aberto = _quando(campo(l, "opened_at"))
        saida.append({
            "sys_id": sid,
            "numero": campo(l, "number"),
            "aberto_em": campo(l, "opened_at"),
            "mes": aberto.strftime("%Y-%m") if aberto else "",
            "solicitante": campo(l, "opened_by"),
            "categoria": campo(l, "category"),
            "subcategoria": campo(l, "subcategory"),
            "fila": fila,
            "estado": campo(l, "state"),
            "tipo": campo(l, "sys_class_name"),
        })
    return saida


def montar_chamados(linhas_sla: list[dict], ids: dict[str, str] | None = None) -> list[dict]:
    """Uma linha por CHAMADO, a partir das linhas de SLA.

    Uma `task` tem uma linha de `task_sla` por SLA. Sem deduplicar por
    `sys_id`, o mesmo atendimento entra várias vezes na contagem do mês.
    """
    por_chamado: dict[str, dict] = {}
    for l in linhas_sla:
        sid = campo(l, "task.sys_id")
        if not sid or sid in por_chamado:
            continue
        fila = qual_fila(campo(l, f"task.{CAMPO_FILA}.name"))
        if not fila:
            # O dot-walk do nome pode não vir; o grupo, sim. Traduz pelo mapa
            # que já temos em vez de descartar a linha.
            fila = qual_fila((ids or {}).get(campo(l, f"task.{CAMPO_FILA}"), ""))
        if not fila:
            continue
        aberto = _quando(campo(l, "task.opened_at"))
        por_chamado[sid] = {
            "sys_id": sid,
            "numero": campo(l, "task.number"),
            "aberto_em": campo(l, "task.opened_at"),
            "mes": aberto.strftime("%Y-%m") if aberto else "",
            "solicitante": (campo(l, "task.caller_id")
                            or campo(l, "task.requested_for")
                            or campo(l, "task.opened_by")),
            "categoria": campo(l, "task.category"),
            "subcategoria": campo(l, "task.subcategory"),
            "fila": fila,
            "estado": campo(l, "task.state"),
            "_aberto": aberto,
            # A RESOLUÇÃO fecha a conta; o encerramento é automático dias
            # depois e inflaria o tempo de todo chamado resolvido na fila.
            "_resolvido": _quando(campo(l, "task.resolved_at")),
            "_encerrado": _quando(campo(l, "task.closed_at")),
        }
    return list(por_chamado.values())


def evento(linha: dict) -> dict:
    """Uma troca de fila, do formato do `sys_audit`."""
    return {"quando": _quando(linha.get("sys_created_on")),
            "de": _valor_plano(linha.get("oldvalue")),
            "para": _valor_plano(linha.get("newvalue"))}


def sys_ids_de_grupo(eventos: dict[str, list[dict]]) -> list[str]:
    """Os valores do histórico que são sys_id e precisam virar nome."""
    return sorted({v for lista in eventos.values() for e in lista
                   for v in (e["de"], e["para"]) if _RE_SYS_ID.match(v or "")})


def aplicar_nomes(eventos: dict[str, list[dict]], nomes: dict[str, str]) -> int:
    """Troca sys_id por nome no histórico. Devolve quantos trocou."""
    trocados = 0
    for lista in eventos.values():
        for e in lista:
            for lado in ("de", "para"):
                novo = nomes.get(e[lado])
                if novo and novo != e[lado]:
                    e[lado] = novo
                    trocados += 1
    return trocados


def medir_chamados(chamados: list[dict], eventos: dict[str, list[dict]]) -> None:
    """Preenche o tempo de fila de cada chamado, na fila DELE.

    São quatro filas; cada chamado é medido na que ele casou, não numa fila
    fixa. A conta do intervalo vem de `sn_tempo_fila`, a mesma que a aba de
    consulta usa.
    """
    agora = datetime.now(timezone.utc)
    for c in chamados:
        fim, origem = c["_resolvido"], "resolvido"
        if fim is None:
            fim, origem = c["_encerrado"], ("encerrado" if c["_encerrado"] else "")
        do_chamado = eventos.get(c["sys_id"]) or []
        if do_chamado:
            r = intervalos_da_fila(do_chamado, c["fila"], c["_aberto"], fim, agora)
            base = "histórico"
        else:
            # Nunca trocou de fila: esteve a vida inteira na fila em que
            # está, e ela é a procurada — foi assim que entrou na lista.
            fechamento = fim or agora
            segundos = (max(0.0, (fechamento - c["_aberto"]).total_seconds())
                        if c["_aberto"] else 0.0)
            r = {"segundos": round(segundos), "horas": round(segundos / 3600, 2),
                 "passagens": 1}
            base = "sem troca de fila"
        c["horas_na_fila"] = r["horas"]
        c["tempo_na_fila"] = _humano(r["segundos"])
        c["idas_a_fila"] = r["passagens"]
        c["fim_contagem"] = fim.strftime(_FMT) if fim else ""
        c["fim_origem"] = origem
        c["base_medicao"] = base if origem else "sem data de encerramento"


def linhas_resumo(chamados: list[dict]) -> list[list]:
    """Resumo por mês e fila — o formato do dado histórico pedido.

    Chamado com tempo zero fica de fora da MÉDIA (não da contagem): ele não
    passou pela fila de verdade e puxaria o número respondendo outra
    pergunta.
    """
    grupos: dict[tuple, list] = defaultdict(list)
    for c in chamados:
        grupos[(c["mes"], c["fila"])].append(c)
    saida = [[], ["RESUMO POR MES E FILA"],
             ["Mes", "Fila", "Chamados", "Horas somadas", "Media (h)"]]
    for (mes, fila), itens in sorted(grupos.items()):
        com_tempo = [i for i in itens if float(i.get("horas_na_fila") or 0) > 0]
        total = sum(float(i["horas_na_fila"]) for i in com_tempo)
        saida.append([mes, fila, len(itens), round(total, 2),
                      round(total / len(com_tempo), 2) if com_tempo else ""])
    return saida


def conferir_datas(desde: str, ate: str) -> None:
    for rotulo, valor in (("desde", desde), ("ate", ate)):
        # O regex sozinho aceita 2025-13-01, e aí a mensagem que sairia era a
        # da comparação ("a data inicial é depois da final") — que manda
        # procurar no lugar errado.
        if not _RE_DATA.match(valor or ""):
            raise HTTPException(422, f"{rotulo} deve ser AAAA-MM-DD; veio {valor!r}")
        try:
            datetime.strptime(valor, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, f"{rotulo} não é uma data: {valor!r}") from None
    if desde > ate:
        raise HTTPException(422, "A data inicial é depois da final.")


# ── O botão: mesma lógica, transporte do portal ────────────────────────
class PeriodoIn(BaseModel):
    desde: str = "2025-01-01"
    ate: str = ""
    # "task" (padrão) lê a própria tabela de chamados: todo campo é local e a
    # consulta volta em segundos. "task_sla" era o caminho original, e cobra
    # 11 junções por linha — minutos por mês em vez de segundos.
    fonte: str = "task"


def _paginar(tabela: str, query: str, campos: str, display: str,
             teto: int = TETO_CHAMADOS) -> list[dict]:
    fora: list[dict] = []
    deslocamento = 0
    while len(fora) < teto:
        pagina = min(1000, teto - len(fora))
        dados = _get(f"/api/now/table/{tabela}", {
            "sysparm_query": query, "sysparm_fields": campos,
            "sysparm_display_value": display,
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": str(pagina), "sysparm_offset": str(deslocamento),
        }, timeout=120)
        linhas = dados.get("result") or []
        fora.extend(linhas)
        if len(linhas) < pagina:
            break
        deslocamento += len(linhas)
    return fora


# Só a LISTA de chamados: uma consulta, sem histórico, sem medição. O tempo
# de fila sai depois, colando esta lista na aba Consulta de chamados — que já
# aceita lista colada e já mede. Duas etapas separadas porque é o que
# funciona: cada uma termina rápido e, quando falha, falha com mensagem.
COLUNAS_LISTA = [
    ("mes", "Mês"),
    ("numero", "Chamado"),
    ("aberto_em", "Aberto em"),
    ("solicitante", "Solicitante"),
    ("categoria", "Categoria"),
    ("subcategoria", "Subcategoria"),
    ("fila", "Fila"),
    ("estado", "Estado"),
]


@router.post("/chamados")
def chamados_do_periodo(corpo: PeriodoIn, req: Request):
    """Os chamados de UMA FATIA do período, em JSON.

    Devolve JSON e não CSV, e uma fatia e não o período inteiro, porque o
    período inteiro tomava **504 do proxy**: a consulta demorava mais que o
    tempo que o proxy espera, a conexão era cortada, e o que chegava ao
    navegador era um download vazio — sem erro, porque o erro vinha do proxy
    e não do portal.

    A tela chama esta rota MÊS A MÊS e junta o resultado. Cada chamada
    termina em segundos, nenhuma chega perto do limite, e o que já veio não
    se perde se uma falhar.
    """
    require_permission(req, MODULO, "export")
    ate = (corpo.ate or "").strip() or datetime.now(timezone.utc).date().isoformat()
    desde = (corpo.desde or "").strip()
    conferir_datas(desde, ate)

    # Pelo sys_id do grupo quando der: é o que faz a consulta caber no tempo.
    ids, aviso = {}, ""
    try:
        ids = ids_das_filas()
        if not ids:
            aviso = ("Nenhum dos quatro grupos foi encontrado em sys_user_group; "
                     "a consulta caiu para a busca por nome, que é mais lenta.")
    except HTTPException as exc:
        aviso = (f"Não deu para resolver os grupos ({exc.detail}); a consulta "
                 "caiu para a busca por nome, que é mais lenta.")

    inicio = time.monotonic()
    pela_task = (corpo.fonte or "task").strip().lower() != "task_sla"
    if pela_task:
        query = query_task(desde, ate, ids)
        brutas = _paginar("task", query, CAMPOS_TASK, "true")
        chamados = montar_da_task(brutas, ids)
    else:
        query = query_task_sla(desde, ate, ids)
        brutas = _paginar("task_sla", query, CAMPOS_TASK_SLA, "true")
        chamados = montar_chamados(brutas, ids)
    for c in chamados:
        for interno in ("_aberto", "_resolvido", "_encerrado"):
            c.pop(interno, None)
    segundos = round(time.monotonic() - inicio, 1)
    _log.info("campo-lojas %s..%s [%s]: %d linhas → %d chamados em %ss (por %s)",
              desde, ate, "task" if pela_task else "task_sla",
              len(brutas), len(chamados), segundos, "sys_id" if ids else "nome")

    return {
        "desde": desde, "ate": ate,
        "chamados": chamados,
        "fonte": "task" if pela_task else "task_sla",
        # Linhas que vieram CONTRA chamados montados: é o par que separa "a
        # consulta não achou" de "achou e eu não consegui ler os campos".
        "linhas_brutas": len(brutas),
        "linhas_sla": len(brutas),
        "segundos": segundos,
        "por_sys_id": bool(ids),
        "filas_resolvidas": sorted(ids.values()),
        "aviso": aviso,
        # Para o caso de vir zero: é o que separa "a consulta não achou" de
        # "vieram linhas e os campos não foram lidos".
        "query": query,
        "amostra_chaves": sorted(brutas[0].keys()) if brutas else [],
    }


@router.post("/contagem")
def contagem(corpo: PeriodoIn, req: Request):
    """Quantos chamados há no período — sem baixar nenhum.

    Sonda rápida, para conferir antes de rodar a coleta inteira: responde em
    segundos se a conta lê a `task_sla`, se os grupos foram resolvidos e se o
    período tem chamados. Sem ela, descobrir isso custava uma coleta longa
    que terminava em 504 sem dizer nada.
    """
    require_permission(req, MODULO, "view")
    ate = (corpo.ate or "").strip() or datetime.now(timezone.utc).date().isoformat()
    desde = (corpo.desde or "").strip()
    conferir_datas(desde, ate)
    ids, erro = {}, ""
    try:
        ids = ids_das_filas()
    except HTTPException as exc:
        erro = str(exc.detail)
    pela_task = (corpo.fonte or "task").strip().lower() != "task_sla"
    tabela = "task" if pela_task else "task_sla"
    query = (query_task(desde, ate, ids) if pela_task
             else query_task_sla(desde, ate, ids))
    total = None
    try:
        dados = _get(f"/api/now/stats/{tabela}",
                     {"sysparm_query": query, "sysparm_count": "true"}, timeout=90)
        total = int(((dados.get("result") or {}).get("stats") or {}).get("count") or 0)
    except HTTPException as exc:
        erro = erro or str(exc.detail)
    return {
        "desde": desde, "ate": ate, "linhas_sla": total, "fonte": tabela,
        "filas_resolvidas": sorted(ids.values()),
        "por_sys_id": bool(ids), "query": query, "erro": erro,
    }


@router.post("/exportar")
def exportar(corpo: PeriodoIn, req: Request):
    """O CSV da coleta, pelas duas fases."""
    require_permission(req, MODULO, "export")
    ate = (corpo.ate or "").strip() or datetime.now(timezone.utc).date().isoformat()
    desde = (corpo.desde or "").strip()
    conferir_datas(desde, ate)

    def conteudo():
        buf = io.StringIO()
        escritor = csv.writer(buf, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        buf.write("﻿")
        escritor.writerow([rot for _c, rot in COLUNAS])
        yield buf.getvalue()
        buf.seek(0), buf.truncate(0)

        try:
            # Fase 1: os chamados.
            sla = _paginar("task_sla", query_task_sla(desde, ate),
                           CAMPOS_TASK_SLA, "true")
            chamados = montar_chamados(sla)
            _log.info("campo-lojas: %d linhas de SLA → %d chamados (%s a %s)",
                      len(sla), len(chamados), desde, ate)
            if not chamados:
                # Arquivo vazio nao diagnostica nada. As tres causas possiveis
                # levam a acoes opostas, e so os numeros abaixo as separam:
                #   - 0 linhas de SLA  -> a consulta nao achou (periodo, nome
                #     da fila, ou a conta sem leitura em task_sla);
                #   - linhas de SLA, 0 chamados -> vieram linhas mas os campos
                #     nao foram lidos, ou o grupo nao casou com as quatro filas.
                # Por isso saem as CHAVES e um exemplo da primeira linha: e o
                # que mostra em que formato o dot-walk voltou.
                escritor.writerow([])
                escritor.writerow(["NENHUM CHAMADO MONTADO - diagnostico:"])
                escritor.writerow(["linhas de task_sla recebidas", len(sla)])
                escritor.writerow(["periodo", f"{desde} a {ate}"])
                escritor.writerow(["filas procuradas", " | ".join(FILAS)])
                if sla:
                    primeira = sla[0]
                    escritor.writerow(["chaves que a API devolveu",
                                       " | ".join(sorted(primeira.keys()))])
                    escritor.writerow(["task.sys_id lido", campo(primeira, "task.sys_id") or "(vazio)"])
                    escritor.writerow(["numero lido", campo(primeira, "task.number") or "(vazio)"])
                    escritor.writerow([f"task.{CAMPO_FILA}.name lido",
                                       campo(primeira, f"task.{CAMPO_FILA}.name") or "(vazio)"])
                    escritor.writerow(["Vieram linhas de SLA mas nenhum chamado foi montado: "
                                       "ou os campos acima estao vazios (o dot-walk voltou "
                                       "noutro formato), ou o grupo nao casou com as filas."])
                else:
                    escritor.writerow(["A consulta nao devolveu linha nenhuma. Confira o "
                                       "periodo, o nome das filas, e se a conta de servico "
                                       "le a tabela task_sla."])
                    escritor.writerow(["query usada", query_task_sla(desde, ate)])
                yield buf.getvalue()
                return

            # Fase 2: o histórico, em lotes. Sem `tablename=`: o sys_audit
            # guarda a tabela real (incident, sc_req_item), e `task` não
            # casaria com nada — zero para todo mundo, sem erro.
            eventos: dict[str, list[dict]] = defaultdict(list)
            ids = [c["sys_id"] for c in chamados]
            fixo = len(f"fieldname={CAMPO_FILA}^documentkeyIN^ORDERBYsys_created_on") + 40
            for bloco in _blocos(ids, LOTE_SYS_ID, reservado=fixo):
                for l in _paginar(
                        "sys_audit",
                        f"fieldname={CAMPO_FILA}^documentkeyIN{','.join(bloco)}"
                        "^ORDERBYsys_created_on",
                        "documentkey,oldvalue,newvalue,sys_created_on", "false",
                        teto=200000):
                    eventos[_valor_plano(l.get("documentkey"))].append(evento(l))

            # A fila pode estar gravada por sys_id; sem traduzir, dá zero.
            alvos = sys_ids_de_grupo(eventos)
            nomes: dict[str, str] = {}
            for bloco in _blocos(alvos, LOTE_SYS_ID, reservado=len("sys_idIN") + 40):
                for l in _paginar("sys_user_group", f"sys_idIN{','.join(bloco)}",
                                  "sys_id,name", "false", teto=len(bloco)):
                    nomes[_valor_plano(l.get("sys_id"))] = _valor_plano(l.get("name"))
            trocados = aplicar_nomes(eventos, nomes)
            _log.info("campo-lojas: %d trocas de fila, %d valores traduzidos",
                      sum(len(v) for v in eventos.values()), trocados)

            medir_chamados(chamados, eventos)
            chamados.sort(key=lambda c: (c["mes"], c["fila"], c["numero"]))
            for c in chamados:
                escritor.writerow([c.get(chave, "") for chave, _r in COLUNAS])
                if buf.tell() > 64 * 1024:
                    yield buf.getvalue()
                    buf.seek(0), buf.truncate(0)
            for linha in linhas_resumo(chamados):
                escritor.writerow(linha)
            # Sem tempo em NENHUM chamado é sintoma, não resultado.
            if not any(float(c.get("horas_na_fila") or 0) for c in chamados):
                escritor.writerow([])
                escritor.writerow(["ATENCAO: todos os chamados deram ZERO na fila. "
                                   "O mais provavel e o nome da fila nao bater com o "
                                   "que esta gravado no historico do ServiceNow."])
            yield buf.getvalue()
        except HTTPException as exc:
            # Com StreamingResponse o 200 já saiu; um erro depois disso viraria
            # download truncado, indistinguível de "nenhum chamado".
            _log.warning("campo-lojas interrompido: %s", exc.detail)
            yield ("\r\n\r\nERRO: a exportacao parou no meio e este arquivo esta "
                   "INCOMPLETO.\r\n" + str(exc.detail).replace("\n", " ") + "\r\n")
        except Exception as exc:  # noqa: BLE001
            _log.error("campo-lojas interrompido: %s", exc, exc_info=True)
            yield ("\r\n\r\nERRO: a exportacao parou no meio e este arquivo esta "
                   "INCOMPLETO.\r\n"
                   f"{type(exc).__name__}: {str(exc)[:300]}".replace("\n", " ") + "\r\n")

    nome = f"campo_lojas_{desde}_a_{ate}.csv"
    return StreamingResponse(
        conteudo(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )
