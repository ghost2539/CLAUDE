"""Quanto tempo o chamado ficou numa fila — pelo histórico, não por um campo.

O portal já tinha uma medida de atendimento: `SN_TMA_START_FIELD`
(`u_data_bouncing`), um campo que marca quando o chamado entrou. Um campo só
responde "entrou quando?", não "ficou quanto tempo?", e erra inteiro no caso
que mais acontece: o chamado que vai para a fila, sai, e VOLTA. O campo
guarda uma data; a segunda passagem sobrescreve a primeira ou não aparece.

Aqui a conta sai do histórico de mudanças do `assignment_group`: cada
mudança é um par (valor antigo → valor novo) com hora. Com a sequência
inteira dá para montar os intervalos e somar os que pertencem à fila
procurada — quantas vezes forem.

FONTES POSSÍVEIS, em ordem de preferência
-----------------------------------------
1.  `metric_instance` — se a instalação tem a *Metric Definition* de
    "Assignment group" ligada, o próprio ServiceNow já calculou a duração de
    cada passagem. É a fonte mais barata e a mais confiável, porque é o
    número que o próprio ServiceNow usa.
2.  `sys_audit` — o log de auditoria de campo: `fieldname=assignment_group`,
    com `oldvalue`, `newvalue` e `sys_created_on`. Existe sempre que a
    auditoria do campo estiver ligada, e é o que a aba "Histórico" do
    formulário mostra.
3.  Nada disso. Aí não se inventa número: a resposta é "não sei", que é
    diferente de zero.

Qual existe NESTA instalação não se adivinha — `/tempo-fila/fontes`
pergunta. Zero e "não medi" são coisas diferentes e a tela separa as duas:
um chamado com 0 h de SPARE porque nunca passou por lá e um chamado sem
histórico guardado não podem virar a mesma linha na planilha.

O QUE TORNA O NÚMERO ERRADO
---------------------------
*   **O fim do último intervalo.** Um chamado fechado há dois anos, cuja
    última fila foi SPARE, continuaria "na fila" até hoje se o intervalo
    final fosse fechado em `agora`. Ele fecha em `closed_at`/`resolved_at`
    quando o chamado está encerrado.
*   **O começo.** Antes da primeira mudança o chamado esteve na fila que é o
    `oldvalue` dessa primeira mudança — não na fila atual.
*   **Ida e volta.** Somar só a primeira passagem subestima o atendimento de
    quem recebeu o chamado de volta.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.security import require_permission
from routers.sn_consulta import (
    LOTE_NUMEROS, MODULO, TABELAS, _blocos, _get, _valor_plano,
)

router = APIRouter(prefix="/api/sn-consulta/tempo-fila", tags=["ServiceNow - Consulta"])
_log = logging.getLogger("sn_tempo_fila")

# O campo cuja troca marca entrada e saída de fila.
CAMPO_FILA = "assignment_group"
# Formato dos carimbos do ServiceNow com `display_value=false`: sempre UTC,
# sempre assim. Com display_value=true viria no fuso e no formato do usuário
# da integração, que muda o resultado sem avisar.
_FMT = "%Y-%m-%d %H:%M:%S"
# `documentkey` é o sys_id do registro auditado.
_RE_SYS_ID = re.compile(r"^[0-9a-f]{32}$")


def _quando(bruto) -> datetime | None:
    texto = _valor_plano(bruto).strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:19], _FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        _log.debug("carimbo fora do formato esperado: %r", texto)
        return None


# ── O cálculo ──────────────────────────────────────────────────────────
def intervalos_da_fila(eventos: list[dict], fila: str, abertura: datetime | None,
                       fim: datetime | None, agora: datetime | None = None) -> dict:
    """Os intervalos em que o chamado esteve numa fila cujo nome contém `fila`.

    `eventos`: [{quando, de, para}] — as trocas de fila, em qualquer ordem.
    `fim`: quando o chamado encerrou; `None` se ainda está aberto.

    Função pura de propósito: é aqui que mora o erro de medição, e assim ela
    se confere com casos escritos à mão, sem ServiceNow nenhum.
    """
    agora = agora or datetime.now(timezone.utc)
    alvo = (fila or "").strip().upper()
    ordenados = sorted([e for e in eventos if e.get("quando")], key=lambda e: e["quando"])

    # Fecha no encerramento, não em `agora`: um chamado encerrado há dois anos
    # cuja última fila foi SPARE mostraria dois anos de fila.
    limite = fim or agora

    # Antes da primeira troca o chamado esteve na fila de ONDE ele saiu, não
    # na fila atual. Sem histórico nenhum não há trecho inicial a montar: a
    # sequência é vazia e quem decide é o chamador.
    trechos: list[tuple[str, datetime, datetime]] = []
    if ordenados:
        inicio = abertura or ordenados[0]["quando"]
        trechos.append((ordenados[0].get("de") or "", inicio, ordenados[0]["quando"]))
        for atual, seguinte in zip(ordenados, ordenados[1:]):
            trechos.append((atual.get("para") or "", atual["quando"], seguinte["quando"]))
        trechos.append((ordenados[-1].get("para") or "", ordenados[-1]["quando"], limite))

    segundos, passagens = 0.0, []
    for grupo, comeco, termino in trechos:
        if alvo not in (grupo or "").upper():
            continue
        # Um carimbo depois do outro por desencontro de relógio não pode virar
        # tempo negativo e reduzir o total.
        duracao = max(0.0, (min(termino, limite) - comeco).total_seconds())
        segundos += duracao
        passagens.append({"de": comeco.isoformat(), "ate": min(termino, limite).isoformat(),
                          "segundos": round(duracao)})
    return {
        "segundos": round(segundos),
        "horas": round(segundos / 3600, 2),
        "passagens": len(passagens),
        "detalhe": passagens,
        "trechos": len(trechos),
    }


def _humano(segundos: float) -> str:
    """'3d 04:12' — planilha lê número, gente lê isto."""
    d = timedelta(seconds=int(segundos))
    dias, resto = d.days, d.seconds
    return (f"{dias}d " if dias else "") + f"{resto // 3600:02d}:{(resto % 3600) // 60:02d}"


# ── Onde estão as trocas de fila ───────────────────────────────────────
def _audit_por_chamado(sys_ids: list[str], tabela: str) -> dict[str, list[dict]]:
    """As trocas de `assignment_group`, do `sys_audit`, por sys_id.

    O `documentkey` é o sys_id do registro. A consulta vai em blocos pelo
    mesmo motivo da lista de chamados: a query viaja na URL.
    """
    saida: dict[str, list[dict]] = {}
    for bloco in _blocos(sys_ids, LOTE_NUMEROS):
        dados = _get("/api/now/table/sys_audit", {
            "sysparm_query": (f"tablename={tabela}^fieldname={CAMPO_FILA}"
                              "^documentkeyIN" + ",".join(bloco)
                              + "^ORDERBYsys_created_on"),
            "sysparm_fields": "documentkey,oldvalue,newvalue,sys_created_on",
            # UTC e formato fixo. Com display_value=true o carimbo sai no fuso
            # do usuário da integração e a conta muda sem ninguém mexer nela.
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": "10000",
        }, timeout=90)
        for linha in dados.get("result") or []:
            chave = _valor_plano(linha.get("documentkey"))
            saida.setdefault(chave, []).append({
                "quando": _quando(linha.get("sys_created_on")),
                "de": _valor_plano(linha.get("oldvalue")),
                "para": _valor_plano(linha.get("newvalue")),
            })
    return saida


@router.get("/fontes")
def fontes(req: Request, tabela: str = "incident"):
    """Qual fonte de histórico esta instalação tem, e se a conta a lê.

    Rode antes de confiar nos números. Sem isto, "0 h de fila" pode ser
    "nunca passou pela fila" ou "a auditoria do campo está desligada" — e as
    duas leituras levam a decisões opostas.
    """
    require_permission(req, MODULO, "view")
    if tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {tabela}")
    achados = []

    def sondar(nome: str, rotulo: str, params: dict, explica: str):
        try:
            dados = _get(f"/api/now/table/{nome}", params, timeout=45)
            linhas = dados.get("result") or []
            achados.append({
                "fonte": nome, "rotulo": rotulo, "acessivel": True,
                "tem_dado": bool(linhas), "amostra": len(linhas), "explica": explica,
                # O valor de exemplo diz se a fila vem por NOME ou por sys_id —
                # procurar "SPARE" num sys_id não acha nada, e o resultado
                # seria "nenhum chamado passou pela fila".
                "exemplo": ({k: _valor_plano(v) for k, v in linhas[0].items()}
                            if linhas else {}),
            })
        except HTTPException as exc:
            achados.append({"fonte": nome, "rotulo": rotulo, "acessivel": False,
                            "tem_dado": False, "erro": exc.detail, "explica": explica})

    sondar("sys_audit", "Auditoria de campo", {
        "sysparm_query": f"tablename={tabela}^fieldname={CAMPO_FILA}^ORDERBYDESCsys_created_on",
        "sysparm_fields": "documentkey,oldvalue,newvalue,sys_created_on",
        "sysparm_display_value": "false", "sysparm_limit": "1",
    }, "As trocas de fila com hora. É desta que o cálculo sai.")
    sondar("metric_instance", "Métrica de duração por fila", {
        "sysparm_query": "definition.field=" + CAMPO_FILA + "^ORDERBYDESCsys_created_on",
        "sysparm_fields": "id,field_value,duration,start,end,calculation_complete",
        "sysparm_display_value": "false", "sysparm_limit": "1",
    }, "Duração já calculada pelo próprio ServiceNow, se a Metric Definition "
       "de assignment group estiver ligada. Mais barata que a auditoria.")

    audit = achados[0]
    pode = audit.get("acessivel") and audit.get("tem_dado")
    return {
        "tabela": tabela,
        "fontes": achados,
        "pode_medir": bool(pode),
        "recado": (
            "A auditoria de assignment_group responde: dá para medir."
            if pode else
            "Sem auditoria de assignment_group acessível, o tempo de fila não "
            "pode ser calculado — e o portal vai dizer 'não medido' em vez de "
            "devolver zero. Peça a leitura de sys_audit para a conta de "
            "serviço, ou a ativação da auditoria do campo."
        ),
    }


class TempoFilaIn(BaseModel):
    tabela: str = "incident"
    # Os chamados vêm por sys_id porque é assim que o sys_audit os endereça
    # (documentkey). A tela já os tem na resposta da consulta.
    chamados: list[dict] = Field(default_factory=list)
    fila: str = "SPARE"


def medir(tabela: str, chamados: list[dict], fila: str) -> dict[str, dict]:
    """Por sys_id: quanto tempo na fila, quantas passagens, e se deu para medir.

    `chamados`: [{sys_id, opened_at, closed_at}] — os carimbos vêm da própria
    consulta, sem ida extra ao ServiceNow.
    """
    ids = [c["sys_id"] for c in chamados
           if _RE_SYS_ID.match((c.get("sys_id") or "").strip())]
    eventos = _audit_por_chamado(ids, tabela) if ids else {}
    agora = datetime.now(timezone.utc)
    saida: dict[str, dict] = {}
    for c in chamados:
        sid = (c.get("sys_id") or "").strip()
        do_chamado = eventos.get(sid) or []
        if not do_chamado:
            # Sem histórico não se afirma nada. Poderia supor "ficou a vida
            # toda na fila atual", mas suposição vira número na planilha e
            # número na planilha vira decisão.
            saida[sid] = {"medido": False, "segundos": None, "horas": None,
                          "passagens": None, "legivel": "",
                          "motivo": "sem histórico de troca de fila para este chamado"}
            continue
        r = intervalos_da_fila(
            do_chamado, fila,
            _quando(c.get("opened_at")),
            _quando(c.get("closed_at")) or _quando(c.get("resolved_at")),
            agora)
        saida[sid] = {"medido": True, "segundos": r["segundos"], "horas": r["horas"],
                      "passagens": r["passagens"], "legivel": _humano(r["segundos"]),
                      "motivo": ""}
    return saida


@router.post("")
def calcular(corpo: TempoFilaIn, req: Request):
    """O tempo de fila de uma lista de chamados já consultados."""
    require_permission(req, MODULO, "view")
    if corpo.tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {corpo.tabela}")
    if not (corpo.fila or "").strip():
        raise HTTPException(422, "Informe o nome (ou parte) da fila a medir.")
    if len(corpo.chamados) > 50000:
        raise HTTPException(422, "Lista grande demais; meça em partes.")
    por_id = medir(corpo.tabela, corpo.chamados, corpo.fila)
    medidos = [v for v in por_id.values() if v["medido"]]
    passou = [v for v in medidos if v["segundos"]]
    total = sum(v["segundos"] for v in passou)
    return {
        "fila": corpo.fila,
        "por_sys_id": por_id,
        "pedidos": len(corpo.chamados),
        "medidos": len(medidos),
        "sem_historico": len(por_id) - len(medidos),
        # Chamado que nunca passou pela fila não entra na média: ele arrastaria
        # o número para baixo respondendo outra pergunta.
        "passaram_pela_fila": len(passou),
        "media_horas": round(total / len(passou) / 3600, 2) if passou else None,
        "total_horas": round(total / 3600, 2),
    }
