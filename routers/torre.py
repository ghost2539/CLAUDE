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
import math
import threading
import time as _time
from datetime import datetime, timedelta, date, time as _dtime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete

import db.trilha as dbt
from core.security import require_permission, get_session, check_rate_limit
from routers.trilha import Calendario, duracao_util, trilha_do_ativo, _utc

_log = logging.getLogger("torre")

router = APIRouter(prefix="/api/torre", tags=["Torre de Controle"])


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
    # Projetos de loja (A16): o token é o item do projeto.
    "AG_DEFINICAO": "Projetos", "AG_SEPARACAO_PROJ": "Projetos",
    "EX_SEPARACAO_PROJ": "Projetos", "AG_ESTOQUE": "Projetos",
    "AG_CONFIGURACAO_PROJ": "Projetos", "EX_CONFIGURACAO_PROJ": "Projetos",
    "AG_REPARO_PROJ": "Projetos", "PRONTO_PROJ": "Projetos",
    # Logística reversa (A17): o token é a coleta.
    "AG_POSTAGEM_REV": "Reversa", "EM_TRANSITO_REV": "Reversa",
    "AG_CONFERENCIA_REV": "Reversa", "EX_CONFERENCIA_REV": "Reversa",
    # Inventário (A18) e regularização (A19).
    "AG_CONTAGEM_INV": "Inventário", "EX_CONTAGEM_INV": "Inventário",
    "AG_TRATATIVA_REG": "Regularização", "EX_TRATATIVA_REG": "Regularização",
}

# Estoque não é fila: o equipamento está lá porque ninguém pediu, e esse
# tempo é do planejamento, não um gargalo a destravar.
FORA_DA_FILA = ("DISPONIVEL",)

# Tokens sintéticos (item de projeto, coleta, ciclo, divergência) usam o
# mesmo relógio, mas não são equipamento: entram nas filas, não nas
# contagens de entrada de ativo.
ESPECIES_TOKEN = ("item_projeto", "coleta", "ciclo_inventario", "divergencia")


def _rotulo(estado: str) -> str:
    return dbt.rotulo_estado(estado)


def metas_por_estado() -> dict[str, int]:
    """Meta em segundos úteis por estado (só as ativas com horas > 0)."""
    with dbt.SessionLocal() as s:
        return {m.estado: int(m.horas_uteis * 3600)
                for m in s.execute(select(dbt.Meta)).scalars()
                if m.ativa and m.horas_uteis and m.horas_uteis > 0}


def _limite_do_estado(estado: str, metas: dict[str, int], limite_global: int) -> int:
    """A meta do estado quando existir; senão o limite global."""
    return metas.get(estado, limite_global)


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _limite_alerta() -> int:
    """A partir de quanto tempo um ativo parado vira alerta.

    `sla_horas` do calendário, quando preenchido (horas úteis). Em
    branco, duas jornadas de expediente — o ponto em que vale a pena
    alguém olhar, enquanto as metas por etapa não existirem.
    """
    cfg = dbt.ler_config()
    try:
        sla = float(cfg.get("sla_horas") or 0)
    except ValueError:
        sla = 0
    if sla > 0:
        return int(sla * 3600)
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
    metas = metas_por_estado()

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
                        "DEVOLVIDO", "SUBSTITUIDO", "CONSUMIDO_EM_MONTAGEM",
                        "ENVIADO_PROJ", "CONFERIDA_REV", "DIVERGENTE_REV",
                        "CONFERIDO_INV", "DIVERGENTE_INV", "RESOLVIDA_REG")))
        ).scalar_one()
        entradas = s.execute(
            select(func.count(dbt.Ativo.id))
            .where(dbt.Ativo.criado_em >= inicio_dia,
                   dbt.Ativo.tipo_equipamento.notin_(ESPECIES_TOKEN))
        ).scalar_one()

    etapas: dict[str, dict] = {}
    parados = []
    em_curso = 0
    for i, a in abertos:
        seg = duracao_util(_utc(i.inicio), None, cal, agora)
        lim = _limite_do_estado(i.estado, metas, limite)
        alvo = etapas.setdefault(i.estado, {
            "estado": i.estado, "rotulo": _rotulo(i.estado),
            "frente": FRENTE_DA_ETAPA.get(i.estado, "Outros"),
            "tipo": i.tipo, "quantidade": 0, "soma": 0, "maior": 0,
            "em_alerta": 0, "meta": metas.get(i.estado), "limite": lim,
        })
        alvo["quantidade"] += 1
        alvo["soma"] += seg
        alvo["maior"] = max(alvo["maior"], seg)
        if i.estado not in FORA_DA_FILA:
            em_curso += 1
            if seg > lim:
                alvo["em_alerta"] += 1
            parados.append({
                "serial": a.serial, "modelo": a.modelo,
                "token": a.tipo_equipamento in ESPECIES_TOKEN,
                "estado": i.estado, "rotulo": _rotulo(i.estado),
                "usuario": i.usuario, "segundos": seg,
                "em_alerta": seg > lim, "limite": lim,
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

    # % dentro da meta: do que está em curso, quanto ainda não estourou.
    na_meta = em_curso - sum(e["em_alerta"] for e in filas)
    return {
        "atualizado_em": agora.isoformat(),
        "em_curso": em_curso,
        "na_meta_pct": round(100 * na_meta / em_curso) if em_curso else None,
        "metas_definidas": len(metas),
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


@router.get("/ativos/{serial}")
def api_ativo(serial: str, req: Request):
    """Ficha do ativo aberta a partir da Torre, pela permissão da Torre.

    Quem vê a fila precisa abrir o item da fila; exigir também a
    permissão do núcleo deixava um usuário só de Torre sem a ficha.
    """
    require_permission(req, "torre", "view")
    check_rate_limit(req)
    return trilha_do_ativo(serial)


# ══════════════════════════════════════════════════════════════════
#  Metas por etapa
# ══════════════════════════════════════════════════════════════════

@router.get("/metas")
def api_metas(req: Request):
    """Todos os estados conhecidos, com a meta de cada um (ou vazia)."""
    require_permission(req, "torre", "view")
    with dbt.SessionLocal() as s:
        atuais = {m.estado: m for m in s.execute(select(dbt.Meta)).scalars()}
    estados = [e for e in dbt.ROTULO_ESTADO if e in FRENTE_DA_ETAPA and e not in FORA_DA_FILA]
    return {"metas": [{
        "estado": e, "rotulo": _rotulo(e), "frente": FRENTE_DA_ETAPA.get(e, "Outros"),
        "horas_uteis": atuais[e].horas_uteis if e in atuais else None,
        "ativa": atuais[e].ativa if e in atuais else False,
    } for e in estados]}


class MetaIn(BaseModel):
    estado: str
    horas_uteis: float | None = None
    ativa: bool = True


@router.put("/metas")
def api_metas_gravar(body: list[MetaIn], req: Request):
    sd = require_permission(req, "torre", "admin")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    with dbt.SessionLocal() as s:
        for m in body:
            estado = (m.estado or "").strip().upper()
            if estado not in FRENTE_DA_ETAPA:
                continue
            row = s.get(dbt.Meta, estado)
            if m.horas_uteis is None or m.horas_uteis <= 0:
                if row is not None:
                    s.delete(row)
                continue
            if row is None:
                row = dbt.Meta(estado=estado)
                s.add(row)
            row.horas_uteis = float(m.horas_uteis)
            row.ativa = bool(m.ativa)
            row.atualizado_por = usuario
        s.commit()
    _log.info("torre: metas alteradas por %s", usuario)
    return api_metas(req)


# ══════════════════════════════════════════════════════════════════
#  Snapshot diário — a foto que vira filme
# ══════════════════════════════════════════════════════════════════

def gerar_snapshot(dia: date | None = None, agora: datetime | None = None) -> int:
    """Grava a foto de um dia: fila por estado e o que fechou nele.

    Idempotente por (dia, estado): rodar de novo substitui. O "aberto"
    é medido no instante da chamada — por isso a rotina roda no fim do
    expediente; o "fechado" é tudo que terminou naquele dia civil.
    """
    cal = _calendario()
    agora = agora or dbt.utcnow()
    # O dia é o civil local (fuso do calendário): a foto das 23:55 de
    # Brasília não pode cair no dia seguinte só porque o UTC já virou.
    dia = dia or (agora + cal.fuso).date()
    metas = metas_por_estado()
    limite = _limite_alerta()
    ini = datetime.combine(dia, _dtime.min, tzinfo=agora.tzinfo) - cal.fuso
    fim = ini + timedelta(days=1)

    with dbt.SessionLocal() as s:
        abertos = s.execute(
            select(dbt.Intervalo).where(dbt.Intervalo.fim.is_(None))).scalars().all()
        fechados = s.execute(
            select(dbt.Intervalo).where(dbt.Intervalo.fim.isnot(None),
                                        dbt.Intervalo.fim >= ini, dbt.Intervalo.fim < fim)
        ).scalars().all()

        por_estado: dict[str, dict] = {}
        def alvo(e):
            return por_estado.setdefault(e, {"quantidade": 0, "soma": 0, "maior": 0, "fora": 0,
                                             "fechados": [], "fechados_na_meta": 0})
        for i in abertos:
            seg = duracao_util(_utc(i.inicio), None, cal, agora)
            x = alvo(i.estado)
            x["quantidade"] += 1; x["soma"] += seg; x["maior"] = max(x["maior"], seg)
            if i.estado not in FORA_DA_FILA and seg > _limite_do_estado(i.estado, metas, limite):
                x["fora"] += 1
        for i in fechados:
            # Estado final (ENTREGUE, DESCARTADO…) fecha com duração zero:
            # é o registro da saída, não uma fila que se encerrou.
            if i.estado not in FRENTE_DA_ETAPA:
                continue
            seg = duracao_util(_utc(i.inicio), _utc(i.fim), cal, agora)
            x = alvo(i.estado)
            x["fechados"].append(seg)
            if seg <= _limite_do_estado(i.estado, metas, limite):
                x["fechados_na_meta"] += 1

        chave = dia.isoformat()
        s.execute(delete(dbt.Snapshot).where(dbt.Snapshot.dia == chave))
        n = 0
        for estado, x in por_estado.items():
            fech = sorted(x["fechados"])
            p90 = fech[max(0, math.ceil(0.9 * len(fech)) - 1)] if fech else 0
            s.add(dbt.Snapshot(
                dia=chave, estado=estado, frente=FRENTE_DA_ETAPA.get(estado, "Outros"),
                quantidade=x["quantidade"], mais_antigo=x["maior"],
                media_aberto=int(x["soma"] / x["quantidade"]) if x["quantidade"] else 0,
                fora_da_meta=x["fora"], fechados=len(fech),
                fechados_media=int(sum(fech) / len(fech)) if fech else 0,
                fechados_p90=p90, fechados_na_meta=x["fechados_na_meta"]))
            n += 1
        s.commit()
    _log.info("torre: snapshot de %s gravado (%d estado(s))", chave, n)
    return n


@router.post("/snapshot")
def api_snapshot(req: Request):
    require_permission(req, "torre", "admin")
    check_rate_limit(req)
    return {"estados": gerar_snapshot()}


@router.get("/historico")
def api_historico(req: Request, dias: int = 30, frente: str = ""):
    """Série diária: fila, fora da meta, fechados e % na meta — por dia e por frente."""
    require_permission(req, "torre", "view")
    dias = max(1, min(int(dias or 30), 180))
    corte = (dbt.utcnow().date() - timedelta(days=dias)).isoformat()
    with dbt.SessionLocal() as s:
        q = select(dbt.Snapshot).where(dbt.Snapshot.dia >= corte)
        if frente:
            q = q.where(dbt.Snapshot.frente == frente)
        linhas = s.execute(q.order_by(dbt.Snapshot.dia)).scalars().all()

    por_dia: dict[str, dict] = {}
    por_frente: dict[str, dict] = {}
    for r in linhas:
        if r.estado in FORA_DA_FILA:
            continue
        d = por_dia.setdefault(r.dia, {"dia": r.dia, "fila": 0, "fora_da_meta": 0, "fechados": 0,
                                       "fechados_na_meta": 0, "mais_antigo": 0})
        d["fila"] += r.quantidade; d["fora_da_meta"] += r.fora_da_meta
        d["fechados"] += r.fechados; d["fechados_na_meta"] += r.fechados_na_meta
        d["mais_antigo"] = max(d["mais_antigo"], r.mais_antigo)
        f = por_frente.setdefault(r.frente, {"frente": r.frente, "dias": {}})
        fd = f["dias"].setdefault(r.dia, {"fila": 0, "fora_da_meta": 0, "fechados": 0})
        fd["fila"] += r.quantidade; fd["fora_da_meta"] += r.fora_da_meta; fd["fechados"] += r.fechados
    serie = []
    for d in por_dia.values():
        d["na_meta_pct"] = round(100 * (d["fila"] - d["fora_da_meta"]) / d["fila"]) if d["fila"] else None
        d["fechados_na_meta_pct"] = round(100 * d["fechados_na_meta"] / d["fechados"]) if d["fechados"] else None
        serie.append(d)
    frentes = [{"frente": f["frente"], "serie": [{"dia": k, **v} for k, v in sorted(f["dias"].items())]}
               for f in por_frente.values()]
    # Comparação: últimos 7 dias × 7 anteriores.
    def soma(ds, chave):
        return sum(x[chave] for x in ds)
    ult, ant = serie[-7:], serie[-14:-7]
    comparacao = None
    if ult:
        comparacao = {"fechados": soma(ult, "fechados"), "fechados_anterior": soma(ant, "fechados") if ant else None,
                      "fila_media": round(soma(ult, "fila") / len(ult)),
                      "fila_media_anterior": round(soma(ant, "fila") / len(ant)) if ant else None}
    return {"dias": dias, "serie": serie, "frentes": sorted(frentes, key=lambda x: x["frente"]),
            "comparacao": comparacao, "ultimo_snapshot": serie[-1]["dia"] if serie else None}


# ── Agendador: uma foto por dia, na hora configurada ────────────────
_agendador_iniciado = False


def _hora_snapshot() -> _dtime:
    txt = (dbt.ler_config().get("snapshot_hora") or "23:55").strip()
    try:
        h, m = txt.split(":")
        return _dtime(int(h), int(m))
    except ValueError:
        return _dtime(23, 55)


def _loop_snapshot() -> None:
    ultimo = None
    while True:
        try:
            cal = _calendario()
            agora_local = dbt.utcnow() + cal.fuso
            hoje = agora_local.date()
            if ultimo != hoje and agora_local.time() >= _hora_snapshot():
                gerar_snapshot(hoje)
                ultimo = hoje
        except Exception as exc:  # noqa: BLE001 — a foto de um dia falha, a de amanhã sai
            _log.error("torre: snapshot diário falhou: %s", exc)
        _time.sleep(60)


def start_scheduler() -> None:
    global _agendador_iniciado
    if _agendador_iniciado:
        return
    _agendador_iniciado = True
    threading.Thread(target=_loop_snapshot, daemon=True, name="torre-snapshot").start()


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
