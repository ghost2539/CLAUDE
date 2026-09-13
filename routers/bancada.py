"""Bancadas de Triagem e Reparo (A02, A03, A04).

Primeiro módulo que usa a Trilha como fonte de estado, e não só como
registro: a fila da bancada é lida dos intervalos abertos do núcleo, e
o bipe move o ativo por lá. O que fica no banco próprio é o conteúdo do
reparo — causa, peças, destino.

Três coisas que o desenho exige e que o código faz questão de garantir:

- **`AG_PECAS` só sai por bipe de retorno ou por ADMIN.** É a fila que
  mais envelhece, e sair dela sem o equipamento na mão é o jeito mais
  fácil de fabricar um indicador bonito.
- **`EX_REPARO` é acumulativo.** Parar para esperar peça e voltar é o
  mesmo trabalho; a espera fica num relógio à parte.
- **A causa é lista fechada.** Texto livre aqui vira três grafias da
  mesma falha e a ponte com mobilidade (G12) perde o insumo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

import db.bancada as db
import db.trilha as dbt
from db.bancada import (
    Reparo, Causa, SessionLocal,
    BANCADAS, ROTULO_BANCADA, PROCESSO,
    FILA_DA_BANCADA, TRATATIVA_DA_BANCADA,
    APTO, AGUARDANDO_PECAS, ASSISTENCIA, INVIAVEL, DESTINOS,
    ROTULO_DESTINO, PROXIMO_ESTADO, FROTA, LOJA, CONECTIVIDADE,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, mover, TrilhaInvalida, _utc,
)

_log = logging.getLogger("bancada")

router = APIRouter(prefix="/api/bancada", tags=["Bancada"])


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _bancada_valida(bancada: str) -> str:
    if bancada not in BANCADAS:
        raise HTTPException(400, "Bancada inválida.")
    return bancada


# ══════════════════════════════════════════════════════════════════
#  Fila — lida da Trilha
# ══════════════════════════════════════════════════════════════════

@router.get("/fila")
def api_fila(req: Request, bancada: str = FROTA):
    """Ativos parados na fila desta bancada, do mais antigo ao mais novo."""
    require_permission(req, "bancada", "view")
    bancada = _bancada_valida(bancada)
    cal, agora = _calendario(), dbt.utcnow()
    estado = FILA_DA_BANCADA[bancada]
    em_curso = TRATATIVA_DA_BANCADA[bancada]

    with dbt.SessionLocal() as s:
        abertos = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado.in_((estado, em_curso, "AG_PECAS")))
            .order_by(dbt.Intervalo.inicio)
        ).all()

    fila, curso, pecas = [], [], []
    for intervalo, ativo in abertos:
        linha = {
            "serial": ativo.serial,
            "modelo": ativo.modelo,
            "tipo_equipamento": ativo.tipo_equipamento,
            "origem": ativo.origem,
            "estado": intervalo.estado,
            "usuario": intervalo.usuario,
            "desde": _utc(intervalo.inicio).isoformat(),
            "segundos": duracao_util(_utc(intervalo.inicio), None, cal, agora),
        }
        if intervalo.estado == estado:
            fila.append(linha)
        elif intervalo.estado == em_curso:
            curso.append(linha)
        else:
            pecas.append(linha)

    # A fila de peças é da bancada inteira, não de uma só; filtrar por
    # bancada exigiria olhar de onde o ativo veio, e nesse caso o dado
    # certo é o reparo aberto — que é o que se faz aqui.
    if pecas:
        with SessionLocal() as sb:
            donos = {
                r.serial: r.bancada for r in sb.execute(
                    select(Reparo).where(
                        Reparo.serial.in_([p["serial"] for p in pecas]),
                        Reparo.fechado_em.is_(None))
                ).scalars()
            }
        pecas = [p for p in pecas if donos.get(p["serial"], bancada) == bancada]

    limite = _limite_pecas()
    for p in pecas:
        p["envelhecido"] = p["segundos"] > limite

    return {
        "bancada": bancada,
        "rotulo": ROTULO_BANCADA[bancada],
        "aguardando": fila,
        "em_curso": curso,
        "aguardando_pecas": pecas,
        "envelhecidos": sum(1 for p in pecas if p["envelhecido"]),
    }


def _limite_pecas() -> int:
    cfg = db.ler_config()
    cal = _calendario()
    try:
        dias = float(cfg.get("alerta_pecas_dias") or 0)
    except ValueError:
        dias = 0
    if dias <= 0:
        return 10 ** 9
    if cal.corrido:
        return int(dias * 86400)
    jornada = (datetime.combine(datetime.today().date(), cal.fim)
               - datetime.combine(datetime.today().date(), cal.inicio)).total_seconds()
    return int(dias * max(jornada, 1))


# ══════════════════════════════════════════════════════════════════
#  Contexto do ativo no bipe
# ══════════════════════════════════════════════════════════════════

@router.get("/ativo/{serial}")
def api_ativo(serial: str, req: Request, bancada: str = FROTA):
    """O que o técnico precisa ver antes de mexer no equipamento.

    Reincidência e histórico entram aqui porque mudam a decisão: um
    aparelho que voltou três vezes em 90 dias não merece o mesmo reparo
    da primeira vez.
    """
    require_permission(req, "bancada", "view")
    bancada = _bancada_valida(bancada)
    serial = (serial or "").strip().upper()
    cfg = db.ler_config()
    try:
        janela = int(cfg.get("janela_reincidencia") or 90)
    except ValueError:
        janela = 90
    corte = dbt.utcnow() - timedelta(days=janela)

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()

    with SessionLocal() as sb:
        historico = sb.execute(
            select(Reparo).where(Reparo.serial == serial)
            .order_by(Reparo.aberto_em.desc()).limit(10)
        ).scalars().all()
        total = sb.execute(
            select(func.count(Reparo.id)).where(Reparo.serial == serial)
        ).scalar_one()
        recentes = sb.execute(
            select(func.count(Reparo.id))
            .where(Reparo.serial == serial, Reparo.aberto_em >= corte)
        ).scalar_one()
        nomes = {c.id: c.nome for c in sb.execute(select(Causa)).scalars()}

    return {
        "serial": serial,
        "na_trilha": ativo is not None,
        "modelo": ativo.modelo if ativo else "",
        "tipo_equipamento": ativo.tipo_equipamento if ativo else "",
        "origem": ativo.origem if ativo else "",
        "estado_atual": ativo.estado_fisico if ativo else "",
        "reincidencia_total": int(total),
        "reincidencia_janela": int(recentes),
        "janela_dias": janela,
        "historico": [{
            "quando": _utc(r.aberto_em).isoformat(),
            "bancada": ROTULO_BANCADA.get(r.bancada, r.bancada),
            "causa": nomes.get(r.causa_id, ""),
            "destino": ROTULO_DESTINO.get(r.destino, r.destino),
            "tecnico": r.tecnico,
        } for r in historico],
    }


@router.get("/causas")
def api_causas(req: Request, bancada: str = ""):
    require_permission(req, "bancada", "view")
    with SessionLocal() as s:
        linhas = s.execute(
            select(Causa).where(Causa.ativa.is_(True)).order_by(Causa.nome)
        ).scalars().all()
    return {"causas": [
        {"id": c.id, "nome": c.nome, "bancada": c.bancada}
        for c in linhas
        if not bancada or not c.bancada or c.bancada == bancada
    ]}


# ══════════════════════════════════════════════════════════════════
#  Bipe e registro
# ══════════════════════════════════════════════════════════════════

class BipeIn(BaseModel):
    serial: str
    bancada: str = FROTA


@router.post("/bipar")
def api_bipar(body: BipeIn, req: Request):
    """Assume o equipamento: o relógio sai da fila e passa para a pessoa."""
    sd = require_permission(req, "bancada", "edit")
    check_rate_limit(req)
    bancada = _bancada_valida(body.bancada)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")
    if not serial:
        raise HTTPException(400, "Bipe a série do equipamento.")

    destino_estado = TRATATIVA_DA_BANCADA[bancada]
    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(
                404, f"A série {serial} não está na trilha. Equipamento "
                     "anterior ao módulo não tem fila — registre a entrada "
                     "pelo Recebimento antes.")
        if ativo.estado_fisico == destino_estado:
            raise HTTPException(
                409, f"A série {serial} já está na bancada com "
                     f"{_quem_esta_com(s, ativo.id) or 'alguém'}.")
        try:
            mover(s, ativo, estado=destino_estado, tipo=dbt.TRATATIVA,
                  processo=PROCESSO[bancada], usuario=usuario)
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()
        ativo_id, modelo, tipo = ativo.id, ativo.modelo, ativo.tipo_equipamento

    # Abre o registro do reparo se ainda não houver um em aberto. Bipar
    # de novo depois de uma pausa continua o mesmo reparo.
    with SessionLocal() as sb:
        aberto = sb.execute(
            select(Reparo).where(Reparo.serial == serial,
                                 Reparo.fechado_em.is_(None))
        ).scalar_one_or_none()
        if aberto is None:
            sb.add(Reparo(serial=serial, trilha_ativo_id=ativo_id,
                          bancada=bancada, modelo=modelo,
                          tipo_equipamento=tipo, tecnico=usuario))
            sb.commit()
    _log.info("bancada: %s assumiu a série %s (%s)", usuario, serial, bancada)
    return api_ativo(serial, req, bancada)


def _quem_esta_com(s, ativo_id: int) -> str:
    linha = s.execute(
        select(dbt.Intervalo).where(dbt.Intervalo.ativo_id == ativo_id,
                                    dbt.Intervalo.fim.is_(None))
    ).scalars().first()
    return linha.usuario if linha else ""


class RegistroIn(BaseModel):
    serial: str
    bancada: str = FROTA
    acoes: str
    causa_id: int
    pecas: str
    destino: str
    justificativa: str = ""
    fornecedor: str = ""
    pecas_aguardadas: str = ""
    fabricante: str = ""
    garantia_vigente: bool | None = None


@router.post("/registrar")
def api_registrar(body: RegistroIn, req: Request):
    """Fecha a passagem pela bancada e manda o ativo para o próximo estado."""
    sd = require_permission(req, "bancada", "edit")
    bancada = _bancada_valida(body.bancada)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")

    if body.destino not in DESTINOS:
        raise HTTPException(400, "Destino inválido.")
    if not (body.acoes or "").strip():
        raise HTTPException(400, "Descreva o que foi feito.")
    # "N/A" explícito é obrigatório: em branco não distingue "não trocou
    # peça" de "esqueceu de preencher", e a diferença importa no custo.
    if not (body.pecas or "").strip():
        raise HTTPException(
            400, 'Informe as peças trocadas, ou "N/A" se não houve troca.')
    if body.destino == AGUARDANDO_PECAS and not (body.pecas_aguardadas or "").strip():
        raise HTTPException(400, "Diga qual peça está sendo aguardada.")
    if body.destino == ASSISTENCIA and not (body.fornecedor or "").strip():
        raise HTTPException(400, "Informe o fornecedor da assistência.")
    if body.destino == INVIAVEL and not (body.justificativa or "").strip():
        raise HTTPException(
            400, "Reparo inviável exige justificativa: é o que sustenta a baixa.")

    with SessionLocal() as sb:
        if sb.get(Causa, body.causa_id) is None:
            raise HTTPException(400, "Causa inválida.")

    proximo = PROXIMO_ESTADO.get((bancada, body.destino))
    if proximo is None:
        raise HTTPException(400, "Destino não previsto para esta bancada.")
    # Espera de peça é externa: não corre contra o técnico, corre contra
    # o fluxo. Assistência idem.
    tipo = (dbt.EXTERNO if body.destino in (AGUARDANDO_PECAS, ASSISTENCIA)
            else dbt.FILA)

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != TRATATIVA_DA_BANCADA[bancada]:
            raise HTTPException(
                409, "Bipe o equipamento antes de registrar o reparo.")
        try:
            mover(s, ativo, estado=proximo, tipo=tipo,
                  processo=PROCESSO[bancada], usuario=usuario,
                  detalhe=f'{{"destino": "{body.destino}"}}')
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()

    with SessionLocal() as sb:
        reparo = sb.execute(
            select(Reparo).where(Reparo.serial == serial,
                                 Reparo.fechado_em.is_(None))
            .order_by(Reparo.aberto_em.desc())
        ).scalars().first()
        if reparo is None:
            reparo = Reparo(serial=serial, bancada=bancada, tecnico=usuario)
            sb.add(reparo)
        reparo.acoes = body.acoes.strip()
        reparo.causa_id = body.causa_id
        reparo.pecas = body.pecas.strip()
        reparo.destino = body.destino
        reparo.justificativa = (body.justificativa or "").strip()
        reparo.fornecedor = (body.fornecedor or "").strip()
        reparo.pecas_aguardadas = (body.pecas_aguardadas or "").strip()
        reparo.fabricante = (body.fabricante or "").strip()
        reparo.garantia_vigente = body.garantia_vigente
        reparo.tecnico = usuario
        # Aguardando peça não fecha o reparo: o mesmo trabalho continua
        # quando a peça chegar, e fechar aqui inventaria dois reparos.
        if body.destino != AGUARDANDO_PECAS:
            reparo.fechado_em = db.utcnow()
        sb.commit()

    _log.info("bancada: %s registrou %s em %s → %s",
              usuario, serial, bancada, body.destino)
    return {"ok": True, "serial": serial, "proximo_estado": proximo,
            "destino_rotulo": ROTULO_DESTINO[body.destino]}


class RetornoIn(BaseModel):
    serial: str
    bancada: str = FROTA


@router.post("/retornar-peca")
def api_retornar_peca(body: RetornoIn, req: Request):
    """A peça chegou: o ativo volta para a bancada e o reparo continua.

    Só sai de AG_PECAS por aqui ou por correção de ADMIN na trilha —
    esta é a fila que mais envelhece, e sair dela sem o equipamento na
    mão é o jeito mais fácil de fabricar um indicador bonito.
    """
    sd = require_permission(req, "bancada", "edit")
    bancada = _bancada_valida(body.bancada)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != "AG_PECAS":
            raise HTTPException(
                409, f"A série {serial} não está aguardando peça.")
        try:
            mover(s, ativo, estado=TRATATIVA_DA_BANCADA[bancada],
                  tipo=dbt.TRATATIVA, processo=PROCESSO[bancada],
                  usuario=usuario)
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()
    _log.info("bancada: peça chegou para %s, %s retomou", serial, usuario)
    return api_ativo(serial, req, bancada)


# ══════════════════════════════════════════════════════════════════
#  Causas e configuração
# ══════════════════════════════════════════════════════════════════

class CausaIn(BaseModel):
    nome: str
    bancada: str = ""
    ativa: bool = True


@router.post("/causas")
def api_causa_add(body: CausaIn, req: Request):
    require_permission(req, "bancada", "admin")
    nome = (body.nome or "").strip()
    if not nome:
        raise HTTPException(400, "Informe o nome da causa.")
    with SessionLocal() as s:
        if s.execute(select(Causa).where(Causa.nome == nome)).scalar_one_or_none():
            raise HTTPException(409, "Essa causa já existe.")
        s.add(Causa(nome=nome, bancada=body.bancada or "", ativa=body.ativa))
        s.commit()
    return api_causas(req)


@router.put("/causas/{causa_id}")
def api_causa_edit(causa_id: int, body: CausaIn, req: Request):
    require_permission(req, "bancada", "admin")
    with SessionLocal() as s:
        c = s.get(Causa, causa_id)
        if c is None:
            raise HTTPException(404, "Causa não encontrada.")
        # Causa não se apaga: reparo antigo aponta para ela, e o
        # histórico precisa continuar legível. Desativar tira da lista.
        c.nome = (body.nome or c.nome).strip()
        c.bancada = body.bancada or ""
        c.ativa = body.ativa
        s.commit()
    return api_causas(req)


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "bancada", "admin")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "bancada", "admin")
    pares = {k: str(v) for k, v in (body or {}).items() if k in db.PADROES}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("bancada: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return db.ler_config()
