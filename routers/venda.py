"""Venda de Ativos (A11) — a fila de venda e o ciclo trimestral.

Chega aqui o que o Recebimento marcou como venda direta e o que a
Central de Reparos decidiu não internalizar. O ativo fica em `AG_VENDA`
na Trilha — parado, com relógio correndo — até entrar num ciclo
trimestral. Quando o ciclo é concluído, os ativos saem e são **baixados**:
encerramento na Trilha, que é o que para o relógio para valer.

Duas coisas de propósito:

- **Ninguém assume ativo aqui também.** O ativo cai na fila sozinho e só
  para de contar quando alguém o inclui num ciclo — o gesto é o mesmo do
  resto do portal: bipar ou informar a série.
- **Concluir exige comprador e documento.** É a venda de patrimônio da
  empresa; baixa sem documento é exposição, não agilidade.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

import db.venda as db
import db.trilha as dbt
from db.venda import (
    Ciclo, Item, SessionLocal, trimestre_de,
    ABERTO, NEGOCIACAO, CONCLUIDO, CANCELADO, ESTADOS_CICLO, ROTULO_CICLO,
    DA_ENTRADA, DO_REPARO,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, mover, encerrar, garantir_ativo,
    TrilhaInvalida, _utc,
)

_log = logging.getLogger("venda")

router = APIRouter(prefix="/api/venda", tags=["Venda de Ativos"])

FILA = "AG_VENDA"          # esperando entrar num ciclo
NO_CICLO = "AG_VENDA_CICLO"  # dentro de um ciclo, esperando a negociação
VENDIDO = "VENDIDO"        # estado final na trilha


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _limite_fila() -> int:
    cfg = db.ler_config()
    try:
        dias = float(cfg.get("alerta_fila_dias") or 0)
    except ValueError:
        dias = 0
    return int(dias * 86400) if dias > 0 else 10 ** 9


# ══════════════════════════════════════════════════════════════════
#  Fila — lida da Trilha
# ══════════════════════════════════════════════════════════════════

@router.get("/fila")
def api_fila(req: Request):
    """Ativos esperando venda, do mais antigo ao mais novo."""
    require_permission(req, "venda", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with dbt.SessionLocal() as s:
        abertos = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado == FILA)
            .order_by(dbt.Intervalo.inicio)
        ).all()
    limite = _limite_fila()
    fila = []
    for intervalo, ativo in abertos:
        segundos = duracao_util(_utc(intervalo.inicio), None, cal, agora)
        fila.append({
            "serial": ativo.serial,
            "modelo": ativo.modelo,
            "origem": ativo.origem,
            "desde": _utc(intervalo.inicio).isoformat(),
            "segundos": segundos,
            "envelhecido": segundos > limite,
        })
    return {"fila": fila, "envelhecidos": sum(1 for x in fila if x["envelhecido"])}


# ══════════════════════════════════════════════════════════════════
#  Ciclos trimestrais
# ══════════════════════════════════════════════════════════════════

def _ciclo_json(c: Ciclo, itens: list[Item]) -> dict:
    return {
        "id": c.id, "trimestre": c.trimestre, "estado": c.estado,
        "estado_rotulo": ROTULO_CICLO.get(c.estado, c.estado),
        "observacao": c.observacao, "comprador": c.comprador,
        "documento": c.documento, "valor_total": round(c.valor_total or 0, 2),
        "aberto_por": c.aberto_por, "aberto_em": _utc(c.aberto_em).isoformat(),
        "concluido_por": c.concluido_por,
        "concluido_em": _utc(c.concluido_em).isoformat() if c.concluido_em else None,
        "quantidade": len(itens),
        "itens": [{
            "id": i.id, "serial": i.serial, "modelo": i.modelo,
            "origem": i.origem, "valor": round(i.valor or 0, 2),
            "incluido_por": i.incluido_por,
            "incluido_em": _utc(i.incluido_em).isoformat(),
            "baixado": i.baixado,
        } for i in itens],
    }


@router.get("/ciclos")
def api_ciclos(req: Request, estado: str = ""):
    require_permission(req, "venda", "view")
    with SessionLocal() as s:
        consulta = select(Ciclo).order_by(Ciclo.trimestre.desc())
        if estado:
            consulta = consulta.where(Ciclo.estado == estado.strip().upper())
        ciclos = s.execute(consulta).scalars().all()
        itens = s.execute(select(Item)).scalars().all()
    por_ciclo: dict[int, list[Item]] = {}
    for i in itens:
        por_ciclo.setdefault(i.ciclo_id, []).append(i)
    return {"ciclos": [_ciclo_json(c, por_ciclo.get(c.id, [])) for c in ciclos]}


@router.get("/ciclos/{ciclo_id}")
def api_ciclo(ciclo_id: int, req: Request):
    require_permission(req, "venda", "view")
    with SessionLocal() as s:
        c = s.get(Ciclo, ciclo_id)
        if c is None:
            raise HTTPException(404, "Ciclo não encontrado.")
        itens = s.execute(
            select(Item).where(Item.ciclo_id == c.id).order_by(Item.id)
        ).scalars().all()
    return _ciclo_json(c, itens)


class CicloIn(BaseModel):
    trimestre: str = ""      # vazio = o trimestre de hoje
    observacao: str = ""


@router.post("/ciclos")
def api_ciclo_abrir(body: CicloIn, req: Request):
    """Abre o ciclo do trimestre. Um por trimestre, e um aberto por vez."""
    sd = require_permission(req, "venda", "edit")
    check_rate_limit(req)
    trimestre = (body.trimestre or "").strip().upper() or trimestre_de(date.today())
    if not _trimestre_valido(trimestre):
        raise HTTPException(400, "Trimestre inválido. Use o formato 2026-T3.")
    with SessionLocal() as s:
        if s.execute(select(Ciclo).where(Ciclo.trimestre == trimestre)).scalar_one_or_none():
            raise HTTPException(409, f"O ciclo {trimestre} já existe.")
        aberto = s.execute(select(Ciclo).where(Ciclo.estado == ABERTO)).scalars().first()
        if aberto is not None:
            raise HTTPException(
                409, f"O ciclo {aberto.trimestre} ainda está aberto. "
                     "Feche-o para negociação antes de abrir outro.")
        c = Ciclo(trimestre=trimestre, estado=ABERTO,
                  observacao=(body.observacao or "").strip(),
                  aberto_por=sd.get("username", ""))
        s.add(c)
        s.commit()
        _log.info("venda: ciclo %s aberto por %s", trimestre, c.aberto_por)
        return _ciclo_json(c, [])


def _trimestre_valido(texto: str) -> bool:
    partes = texto.split("-T")
    if len(partes) != 2 or not partes[0].isdigit() or len(partes[0]) != 4:
        return False
    return partes[1] in ("1", "2", "3", "4")


class ItemIn(BaseModel):
    serial: str
    valor: float = 0.0


@router.post("/ciclos/{ciclo_id}/itens")
def api_incluir(ciclo_id: int, body: ItemIn, req: Request):
    """Inclui um ativo da fila no ciclo. O bipe é a saída da fila."""
    sd = require_permission(req, "venda", "edit")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    serial = (body.serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "Bipe ou informe a série do equipamento.")

    with SessionLocal() as s:
        c = s.get(Ciclo, ciclo_id)
        if c is None:
            raise HTTPException(404, "Ciclo não encontrado.")
        if c.estado != ABERTO:
            raise HTTPException(
                409, f"O ciclo {c.trimestre} está {ROTULO_CICLO[c.estado].lower()}; "
                     "só ciclo aberto recebe ativo.")
        ja = s.execute(select(Item).where(Item.ciclo_id == c.id,
                                          Item.serial == serial)).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(409, f"A série {serial} já está neste ciclo.")

    with dbt.SessionLocal() as st:
        ativo = garantir_ativo(st, serial, usuario=usuario, origem="venda")
        if ativo is None:
            raise HTTPException(
                404, f"A série {serial} não está na trilha e a adoção "
                     "automática está desligada em Configuração → Ciclo do ativo.")
        if ativo.estado_fisico not in (FILA, NO_CICLO):
            raise HTTPException(
                409, f"A série {serial} está em "
                     f"{dbt.rotulo_estado(ativo.estado_fisico).lower()}, "
                     "não na fila de venda.")
        if ativo.estado_fisico == NO_CICLO:
            raise HTTPException(409, f"A série {serial} já está em outro ciclo.")
        intervalo = st.execute(
            select(dbt.Intervalo).where(dbt.Intervalo.ativo_id == ativo.id,
                                        dbt.Intervalo.fim.is_(None))
            .order_by(dbt.Intervalo.inicio)
        ).scalars().first()
        na_fila_desde = _utc(intervalo.inicio) if intervalo else None
        try:
            mover(st, ativo, estado=NO_CICLO, tipo=dbt.FILA, processo="A11",
                  usuario=usuario, detalhe=f'{{"ciclo": "{ciclo_id}"}}')
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        st.commit()
        ativo_id, modelo, origem = ativo.id, ativo.modelo, ativo.origem

    with SessionLocal() as s:
        item = Item(ciclo_id=ciclo_id, serial=serial, trilha_ativo_id=ativo_id,
                    modelo=modelo, valor=float(body.valor or 0),
                    origem=DA_ENTRADA if (origem or "").upper() == "RECEBIMENTO" else DO_REPARO,
                    na_fila_desde=na_fila_desde, incluido_por=usuario)
        s.add(item)
        s.commit()
    _log.info("venda: %s incluiu %s no ciclo %s", usuario, serial, ciclo_id)
    return api_ciclo(ciclo_id, req)


@router.delete("/ciclos/{ciclo_id}/itens/{item_id}")
def api_remover(ciclo_id: int, item_id: int, req: Request):
    """Tira o ativo do ciclo e o devolve à fila de venda."""
    sd = require_permission(req, "venda", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        item = s.get(Item, item_id)
        if item is None or item.ciclo_id != ciclo_id:
            raise HTTPException(404, "Item não encontrado neste ciclo.")
        if item.baixado:
            raise HTTPException(409, "Ativo já baixado: a venda não se desfaz aqui.")
        c = s.get(Ciclo, ciclo_id)
        if c is not None and c.estado == CONCLUIDO:
            raise HTTPException(409, "Ciclo concluído não muda de composição.")
        serial = item.serial
        s.delete(item)
        s.commit()

    with dbt.SessionLocal() as st:
        ativo = st.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
        if ativo is not None and ativo.estado_fisico == NO_CICLO:
            mover(st, ativo, estado=FILA, tipo=dbt.FILA, processo="A11",
                  usuario=usuario, detalhe='{"saiu_do_ciclo": true}')
            st.commit()
    return api_ciclo(ciclo_id, req)


class EstadoIn(BaseModel):
    estado: str
    observacao: str = ""


@router.put("/ciclos/{ciclo_id}/estado")
def api_estado(ciclo_id: int, body: EstadoIn, req: Request):
    """Fecha o ciclo para negociação, ou o cancela."""
    sd = require_permission(req, "venda", "edit")
    estado = (body.estado or "").strip().upper()
    if estado not in (NEGOCIACAO, ABERTO, CANCELADO):
        raise HTTPException(400, "Estado inválido. A conclusão tem endpoint próprio.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = s.get(Ciclo, ciclo_id)
        if c is None:
            raise HTTPException(404, "Ciclo não encontrado.")
        if c.estado == CONCLUIDO:
            raise HTTPException(409, "Ciclo concluído não volta atrás.")
        if estado == ABERTO:
            outro = s.execute(
                select(Ciclo).where(Ciclo.estado == ABERTO, Ciclo.id != c.id)
            ).scalars().first()
            if outro is not None:
                raise HTTPException(
                    409, f"O ciclo {outro.trimestre} está aberto; só um por vez.")
        c.estado = estado
        if (body.observacao or "").strip():
            c.observacao = body.observacao.strip()
        seriais = [i.serial for i in s.execute(
            select(Item).where(Item.ciclo_id == c.id)).scalars()]
        s.commit()

    # Ciclo cancelado devolve os ativos à fila: eles continuam para venda.
    if estado == CANCELADO and seriais:
        with dbt.SessionLocal() as st:
            for serial in seriais:
                ativo = st.execute(
                    select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
                if ativo is not None and ativo.estado_fisico == NO_CICLO:
                    mover(st, ativo, estado=FILA, tipo=dbt.FILA, processo="A11",
                          usuario=usuario, detalhe='{"ciclo_cancelado": true}')
            st.commit()
    return api_ciclo(ciclo_id, req)


class ConclusaoIn(BaseModel):
    comprador: str
    documento: str = ""
    valor_total: float = 0.0
    observacao: str = ""


@router.post("/ciclos/{ciclo_id}/concluir")
def api_concluir(ciclo_id: int, body: ConclusaoIn, req: Request):
    """A venda saiu: os ativos são baixados e o relógio deles para.

    Baixa é encerramento na Trilha, não mudança de fila. Depois disto o
    ativo só volta a existir se for recebido de novo.
    """
    sd = require_permission(req, "venda", "edit")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    cfg = db.ler_config()
    if not (body.comprador or "").strip():
        raise HTTPException(400, "Informe o comprador: é o que sustenta a baixa.")
    exige_doc = str(cfg.get("exigir_documento", "1")).strip().lower() in ("1", "sim", "true")
    if exige_doc and not (body.documento or "").strip():
        raise HTTPException(
            400, "Informe o documento da venda (nota, contrato ou ata).")

    with SessionLocal() as s:
        c = s.get(Ciclo, ciclo_id)
        if c is None:
            raise HTTPException(404, "Ciclo não encontrado.")
        if c.estado == CONCLUIDO:
            raise HTTPException(409, "Ciclo já concluído.")
        if c.estado == CANCELADO:
            raise HTTPException(409, "Ciclo cancelado não conclui.")
        itens = s.execute(select(Item).where(Item.ciclo_id == c.id)).scalars().all()
        if not itens:
            raise HTTPException(409, "Ciclo sem ativos: não há o que baixar.")
        seriais = [i.serial for i in itens]

    baixados, falhas = [], []
    with dbt.SessionLocal() as st:
        for serial in seriais:
            ativo = st.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
            if ativo is None:
                falhas.append(f"{serial}: não está na trilha")
                continue
            if ativo.encerrado:
                baixados.append(serial)
                continue
            try:
                encerrar(st, ativo, estado=VENDIDO, processo="A11", usuario=usuario,
                         justificativa=f"Venda {c.trimestre} — {body.comprador.strip()}")
                baixados.append(serial)
            except TrilhaInvalida as exc:
                falhas.append(f"{serial}: {exc}")
        st.commit()

    with SessionLocal() as s:
        c = s.get(Ciclo, ciclo_id)
        c.estado = CONCLUIDO
        c.comprador = body.comprador.strip()
        c.documento = (body.documento or "").strip()
        c.valor_total = float(body.valor_total or 0)
        if (body.observacao or "").strip():
            c.observacao = body.observacao.strip()
        c.concluido_por = usuario
        c.concluido_em = db.utcnow()
        for item in s.execute(select(Item).where(Item.ciclo_id == c.id)).scalars():
            if item.serial in baixados:
                item.baixado = True
        s.commit()
    _log.info("venda: ciclo %s concluído por %s (%d baixados, %d falhas)",
              ciclo_id, usuario, len(baixados), len(falhas))
    saida = api_ciclo(ciclo_id, req)
    saida["baixados"] = len(baixados)
    saida["falhas"] = falhas
    return saida


# ══════════════════════════════════════════════════════════════════
#  Painel
# ══════════════════════════════════════════════════════════════════

@router.get("/dashboard")
def api_dashboard(req: Request, data_inicio: str = "", data_fim: str = ""):
    """Volume vendido e tempo parado esperando o ciclo."""
    require_permission(req, "venda", "view")
    cal, agora = _calendario(), dbt.utcnow()
    try:
        ini = datetime.fromisoformat(data_inicio) if data_inicio else agora - timedelta(days=365)
        fim = datetime.fromisoformat(data_fim) + timedelta(days=1) if data_fim else agora
    except ValueError:
        raise HTTPException(400, "Datas inválidas.")
    ini, fim = _utc(ini), _utc(fim)

    with SessionLocal() as s:
        ciclos = s.execute(
            select(Ciclo).where(Ciclo.estado == CONCLUIDO,
                                Ciclo.concluido_em >= ini, Ciclo.concluido_em < fim)
        ).scalars().all()
        itens = s.execute(select(Item)).scalars().all()
    por_ciclo: dict[int, list[Item]] = {}
    for i in itens:
        por_ciclo.setdefault(i.ciclo_id, []).append(i)

    linhas, total_itens, total_valor, esperas = [], 0, 0.0, []
    for c in ciclos:
        do_ciclo = por_ciclo.get(c.id, [])
        fechado = _utc(c.concluido_em)
        for i in do_ciclo:
            if i.na_fila_desde:
                esperas.append(duracao_util(_utc(i.na_fila_desde), fechado, cal, agora))
        total_itens += len(do_ciclo)
        total_valor += float(c.valor_total or 0)
        linhas.append({
            "trimestre": c.trimestre, "quantidade": len(do_ciclo),
            "valor_total": round(c.valor_total or 0, 2),
            "comprador": c.comprador,
            "concluido_em": fechado.isoformat(),
        })
    return {
        "de": ini.isoformat(), "ate": fim.isoformat(),
        "ciclos": len(ciclos), "itens": total_itens,
        "valor_total": round(total_valor, 2),
        "espera_media": int(sum(esperas) / len(esperas)) if esperas else 0,
        "por_ciclo": sorted(linhas, key=lambda x: x["trimestre"], reverse=True),
    }


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "venda", "view")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    require_permission(req, "venda", "admin")
    return db.gravar_config(body or {})
