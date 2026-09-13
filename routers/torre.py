"""Torre de Controle (T2) — onde o tempo foi parar.

Três olhares sobre os mesmos intervalos, porque três perguntas
diferentes precisam de recortes diferentes:

- **Área** — onde estão as filas e qual ativo está parado há mais
  tempo. É a pergunta do dia a dia: o que destravar agora.
- **Pessoa** — quanto tempo ficou com quem, separado do que foi espera.
  É o que torna o painel individual justo: ninguém responde por fila.
- **Frente** — consolidação por macroprocesso, para a conversa de
  capacidade.

Tudo sai dos intervalos do núcleo e dos módulos que os alimentam. Não
há número gravado em lugar nenhum: o painel é derivado, e por isso
muda sozinho quando o calendário muda.

O painel individual exige permissão de gestor OU ser a própria pessoa.
Indicador de colaborador não é dado de operação.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select, func

import db.trilha as dbt
from core.security import require_permission, get_session, check_rate_limit
from routers.trilha import Calendario, duracao_util, _utc

_log = logging.getLogger("torre")

router = APIRouter(prefix="/api/torre", tags=["Torre de Controle"])

# Nome legível de cada etapa. O painel fala a língua de quem opera, não
# a do banco: "aguardando triagem" e não AG_TRIAGEM.
ROTULO_ETAPA = {
    "AG_RECEBIMENTO": "Aguardando recebimento",
    "EX_RECEBIMENTO": "Em recebimento",
    "AG_TRIAGEM": "Aguardando triagem",
    "EX_REPARO": "Em reparo",
    "AG_PECAS": "Aguardando peça",
    "AG_TRIAGEM_CONECT": "Aguardando triagem de rede",
    "EX_TRIAGEM_CONECT": "Em triagem de rede",
    "AG_ASSISTENCIA": "Aguardando envio à assistência",
    "EM_ASSISTENCIA": "Na assistência",
    "EX_CONFERENCIA_RETORNO": "Conferindo retorno",
    "AG_CONFIGURACAO": "Aguardando configuração",
    "EX_CONFIGURACAO": "Em configuração",
    "AG_MONTAGEM": "Aguardando montagem",
    "EX_MONTAGEM": "Em montagem",
    "AG_INTERNALIZACAO": "Aguardando internalização",
    "EX_INTERNALIZACAO": "Internalizando",
    "DISPONIVEL": "Disponível em estoque",
    "AG_SEPARACAO": "Aguardando separação",
    "EX_SEPARACAO": "Em separação",
    "AG_DESCARACTERIZACAO": "Aguardando descaracterização",
    "EX_DESCARACTERIZACAO": "Em descaracterização",
    "AG_DEFINICAO_DESTINO": "Aguardando destino",
    "AG_VENDA": "Aguardando venda",
    "AG_DESCARTE": "Aguardando descarte",
    "AG_DOACAO": "Aguardando doação",
    "AG_DEVOLUCAO": "Aguardando devolução",
    "AG_CONFIRMACAO": "Aguardando confirmação do terceiro",
}

# Agrupamento por frente, para a visão de macroprocesso.
FRENTE_DA_ETAPA = {
    "AG_RECEBIMENTO": "Entrada", "EX_RECEBIMENTO": "Entrada",
    "AG_TRIAGEM": "Bancada", "EX_REPARO": "Bancada", "AG_PECAS": "Bancada",
    "AG_TRIAGEM_CONECT": "Bancada", "EX_TRIAGEM_CONECT": "Bancada",
    "AG_ASSISTENCIA": "Fora da área", "EM_ASSISTENCIA": "Fora da área",
    "EX_CONFERENCIA_RETORNO": "Fora da área",
    "AG_DEVOLUCAO": "Fora da área", "AG_CONFIRMACAO": "Fora da área",
    "AG_CONFIGURACAO": "Preparação", "EX_CONFIGURACAO": "Preparação",
    "AG_MONTAGEM": "Preparação", "EX_MONTAGEM": "Preparação",
    "AG_INTERNALIZACAO": "Preparação", "EX_INTERNALIZACAO": "Preparação",
    "DISPONIVEL": "Estoque",
    "AG_SEPARACAO": "Saída", "EX_SEPARACAO": "Saída",
    "AG_DESCARACTERIZACAO": "Destinação", "EX_DESCARACTERIZACAO": "Destinação",
    "AG_DEFINICAO_DESTINO": "Destinação", "AG_VENDA": "Destinação",
    "AG_DESCARTE": "Destinação", "AG_DOACAO": "Destinação",
}

# Estoque não é fila: o equipamento está lá porque ninguém pediu, e esse
# tempo é do planejamento, não um gargalo a destravar.
FORA_DA_FILA = ("DISPONIVEL",)


def _rotulo(estado: str) -> str:
    return ROTULO_ETAPA.get(estado, estado.replace("_", " ").capitalize())


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _limite_alerta() -> int:
    """A partir de quanto tempo um ativo parado vira alerta.

    Duas jornadas de expediente. Não é SLA — é o ponto em que vale a
    pena alguém olhar, e serve enquanto as metas por etapa não existirem.
    """
    cal = _calendario()
    if cal.corrido:
        return 2 * 86400
    jornada = (datetime.combine(datetime.today().date(), cal.fim)
               - datetime.combine(datetime.today().date(), cal.inicio)).total_seconds()
    return int(2 * max(jornada, 3600))


# ══════════════════════════════════════════════════════════════════
#  Painel da área
# ══════════════════════════════════════════════════════════════════

@router.get("/area")
def api_area(req: Request):
    require_permission(req, "torre", "view")
    check_rate_limit(req)
    cal, agora = _calendario(), dbt.utcnow()
    limite = _limite_alerta()

    with dbt.SessionLocal() as s:
        abertos = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None))
        ).all()
        # Concluídos hoje: movimentações para um estado final no dia.
        inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        saidas = s.execute(
            select(func.count(dbt.Movimentacao.id))
            .where(dbt.Movimentacao.quando >= inicio_dia,
                   dbt.Movimentacao.estado_para.in_(
                       ("ENTREGUE", "VENDIDO", "DESCARTADO", "DOADO",
                        "DEVOLVIDO", "SUBSTITUIDO", "CONSUMIDO_EM_MONTAGEM")))
        ).scalar_one()
        entradas = s.execute(
            select(func.count(dbt.Ativo.id))
            .where(dbt.Ativo.criado_em >= inicio_dia)
        ).scalar_one()

    etapas: dict[str, dict] = {}
    parados = []
    em_curso = 0
    for i, a in abertos:
        seg = duracao_util(_utc(i.inicio), None, cal, agora)
        alvo = etapas.setdefault(i.estado, {
            "estado": i.estado, "rotulo": _rotulo(i.estado),
            "frente": FRENTE_DA_ETAPA.get(i.estado, "Outros"),
            "tipo": i.tipo, "quantidade": 0, "soma": 0, "maior": 0,
            "em_alerta": 0,
        })
        alvo["quantidade"] += 1
        alvo["soma"] += seg
        alvo["maior"] = max(alvo["maior"], seg)
        if i.estado not in FORA_DA_FILA:
            em_curso += 1
            if seg > limite:
                alvo["em_alerta"] += 1
            parados.append({
                "serial": a.serial, "modelo": a.modelo,
                "estado": i.estado, "rotulo": _rotulo(i.estado),
                "usuario": i.usuario, "segundos": seg,
                "em_alerta": seg > limite,
            })

    filas = []
    for e in etapas.values():
        e["media"] = int(e["soma"] / e["quantidade"]) if e["quantidade"] else 0
        e.pop("soma")
        filas.append(e)
    filas.sort(key=lambda x: (-x["quantidade"], -x["maior"]))
    parados.sort(key=lambda x: -x["segundos"])

    por_frente: dict[str, dict] = {}
    for e in filas:
        if e["estado"] in FORA_DA_FILA:
            continue
        f = por_frente.setdefault(e["frente"], {"frente": e["frente"],
                                                "quantidade": 0, "em_alerta": 0,
                                                "maior": 0})
        f["quantidade"] += e["quantidade"]
        f["em_alerta"] += e["em_alerta"]
        f["maior"] = max(f["maior"], e["maior"])

    return {
        "atualizado_em": agora.isoformat(),
        "em_curso": em_curso,
        "em_alerta": sum(e["em_alerta"] for e in filas),
        "mais_antigo": parados[0]["segundos"] if parados else 0,
        "entradas_hoje": int(entradas),
        "saidas_hoje": int(saidas),
        "limite_alerta": limite,
        "filas": filas,
        "frentes": sorted(por_frente.values(), key=lambda x: -x["quantidade"]),
        "parados": parados[:15],
        "calendario_corrido": cal.corrido,
    }


# ══════════════════════════════════════════════════════════════════
#  Painel individual
# ══════════════════════════════════════════════════════════════════

@router.get("/pessoa")
def api_pessoa(req: Request, login: str = "", dias: int = 30):
    """Indicadores de uma pessoa.

    Cada um vê o próprio; ver o de outra pessoa exige permissão de
    administrar. Indicador individual não é dado de operação, e deixar
    aberto é o jeito mais rápido de o time deixar de confiar no portal.
    """
    sd = get_session(req)
    require_permission(req, "torre", "view")
    eu = sd.get("username", "")
    alvo = (login or eu).strip() or eu
    if alvo != eu:
        require_permission(req, "torre", "admin")

    dias = max(1, min(int(dias or 30), 365))
    cal, agora = _calendario(), dbt.utcnow()
    corte = agora - timedelta(days=dias)

    with dbt.SessionLocal() as s:
        intervalos = s.execute(
            select(dbt.Intervalo)
            .where(dbt.Intervalo.usuario == alvo,
                   dbt.Intervalo.inicio >= corte)
        ).scalars().all()
        movimentacoes = s.execute(
            select(dbt.Movimentacao)
            .where(dbt.Movimentacao.usuario == alvo,
                   dbt.Movimentacao.quando >= corte)
        ).scalars().all()

    por_etapa: dict[str, dict] = {}
    total = 0
    abertos = 0
    for i in intervalos:
        seg = duracao_util(_utc(i.inicio), _utc(i.fim), cal, agora)
        total += seg
        if i.fim is None:
            abertos += 1
        alvo_e = por_etapa.setdefault(i.estado, {
            "estado": i.estado, "rotulo": _rotulo(i.estado),
            "passagens": 0, "soma": 0,
        })
        alvo_e["passagens"] += 1
        alvo_e["soma"] += seg

    etapas = []
    for e in por_etapa.values():
        e["media"] = int(e["soma"] / e["passagens"]) if e["passagens"] else 0
        e["total"] = e.pop("soma")
        etapas.append(e)
    etapas.sort(key=lambda x: -x["total"])

    return {
        "login": alvo,
        "dias": dias,
        # Só tratativa: fila e espera externa não são desta pessoa, e
        # somar tudo num número só é o que faz o indivíduo desconfiar do
        # indicador.
        "segundos_tratativa": total,
        "passagens": len(intervalos),
        "em_aberto": abertos,
        "movimentacoes": len(movimentacoes),
        "etapas": etapas,
    }


@router.get("/pessoas")
def api_pessoas(req: Request, dias: int = 30):
    """Consolidado por pessoa. Só para quem administra a torre."""
    require_permission(req, "torre", "admin")
    dias = max(1, min(int(dias or 30), 365))
    cal, agora = _calendario(), dbt.utcnow()
    corte = agora - timedelta(days=dias)

    with dbt.SessionLocal() as s:
        intervalos = s.execute(
            select(dbt.Intervalo)
            .where(dbt.Intervalo.tipo == dbt.TRATATIVA,
                   dbt.Intervalo.usuario != "",
                   dbt.Intervalo.inicio >= corte)
        ).scalars().all()

    pessoas: dict[str, dict] = {}
    for i in intervalos:
        p = pessoas.setdefault(i.usuario, {"login": i.usuario, "passagens": 0,
                                           "segundos": 0, "em_aberto": 0})
        p["passagens"] += 1
        p["segundos"] += duracao_util(_utc(i.inicio), _utc(i.fim), cal, agora)
        if i.fim is None:
            p["em_aberto"] += 1

    return {"dias": dias,
            "pessoas": sorted(pessoas.values(), key=lambda x: -x["segundos"])}
