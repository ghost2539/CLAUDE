"""Assistência externa e devolução a terceiros (A05, A14).

Última ponta solta da bancada: "assistência externa" mandava o ativo
para AG_ASSISTENCIA e ninguém recolhia.

O que sai daqui está na mão de outra empresa. O relógio corre como
EXTERNO na trilha — conta contra o fluxo, nunca contra alguém do SPARE.
Serve para negociar prazo com o fornecedor, não para avaliar o time.

Substituição é o caso que exige cuidado: o fornecedor devolve outro
equipamento, com outra série. A trilha do antigo encerra e a do novo
começa, com referência cruzada nos dois sentidos — sem isso o histórico
do equipamento some no meio do caminho.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.externo as db
import db.trilha as dbt
from db.externo import (
    Envio, SessionLocal, ASSISTENCIA, DEVOLUCAO, ESPECIES, ROTULO_ESPECIE,
    PROCESSO, FILA, FORA, CONFERENCIA,
    REPARADO, SUBSTITUIDO, SEM_REPARO, RESULTADOS, ROTULO_RESULTADO,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, mover, encerrar, abrir_ativo, TrilhaInvalida, _utc,
)

_log = logging.getLogger("externo")

router = APIRouter(prefix="/api/externo", tags=["Assistência e devolução"])


def _especie_valida(especie: str) -> str:
    if especie not in ESPECIES:
        raise HTTPException(400, "Espécie inválida.")
    return especie


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


@router.get("/fila")
def api_fila(req: Request, especie: str = ASSISTENCIA):
    """O que espera para sair, o que está fora e o que voltou para conferir."""
    require_permission(req, "externo", "view")
    especie = _especie_valida(especie)
    cal, agora = _calendario(), dbt.utcnow()

    with dbt.SessionLocal() as s:
        linhas = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado.in_((FILA[especie], FORA[especie],
                                             CONFERENCIA)))
            .order_by(dbt.Intervalo.inicio)
        ).all()

    with SessionLocal() as s:
        abertos = {e.serial: e for e in s.execute(
            select(Envio).where(Envio.especie == especie,
                                Envio.encerrado.is_(False))
        ).scalars()}

    limite = _limite_atraso()
    aguardando, fora, conferencia = [], [], []
    for i, a in linhas:
        envio = abertos.get(a.serial)
        item = {
            "serial": a.serial, "modelo": a.modelo,
            "fornecedor": envio.fornecedor if envio else "",
            "rma": envio.numero_rma if envio else "",
            "previsao": _utc(envio.previsao).isoformat()
                        if envio and envio.previsao else None,
            "segundos": duracao_util(_utc(i.inicio), None, cal, agora),
        }
        item["atrasado"] = bool(
            envio and envio.previsao and _utc(envio.previsao) + limite < agora)
        if i.estado == FILA[especie]:
            aguardando.append(item)
        elif i.estado == FORA[especie]:
            fora.append(item)
        else:
            # A conferência de retorno é compartilhada; só entra na lista
            # desta espécie quem tem envio aberto aqui.
            if envio is not None:
                conferencia.append(item)

    return {
        "especie": especie, "rotulo": ROTULO_ESPECIE[especie],
        "aguardando": aguardando, "fora": fora, "conferencia": conferencia,
        "atrasados": sum(1 for x in fora if x["atrasado"]),
    }


def _limite_atraso():
    from datetime import timedelta
    try:
        dias = float(db.ler_config().get("alerta_atraso_dias") or 0)
    except ValueError:
        dias = 0
    return timedelta(days=max(dias, 0))


class EnvioIn(BaseModel):
    serial: str
    especie: str = ASSISTENCIA
    fornecedor: str
    numero_rma: str = ""
    contrato: str = ""
    previsao: str = ""
    justificativa: str = ""


@router.post("/enviar")
def api_enviar(body: EnvioIn, req: Request):
    """Despacha o equipamento. Daqui em diante o relógio é do fornecedor."""
    sd = require_permission(req, "externo", "edit")
    check_rate_limit(req)
    especie = _especie_valida(body.especie)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")

    if not (body.fornecedor or "").strip():
        raise HTTPException(400, "Informe o fornecedor.")
    previsao = _data(body.previsao)
    if previsao is None:
        raise HTTPException(
            400, "Informe a previsão de retorno. Equipamento fora sem prazo "
                 "vira equipamento perdido.")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != FILA[especie]:
            raise HTTPException(
                409, f"A série {serial} não está aguardando "
                     f"{ROTULO_ESPECIE[especie].lower()} — está em "
                     f"{ativo.estado_fisico or 'estado desconhecido'}.")
        try:
            mover(s, ativo, estado=FORA[especie], tipo=dbt.EXTERNO,
                  processo=PROCESSO[especie], usuario=usuario)
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()
        ativo_id = ativo.id

    with SessionLocal() as s:
        s.add(Envio(
            especie=especie, serial=serial, trilha_ativo_id=ativo_id,
            fornecedor=body.fornecedor.strip(),
            numero_rma=(body.numero_rma or "").strip(),
            contrato=(body.contrato or "").strip(),
            justificativa=(body.justificativa or "").strip(),
            enviado_em=db.utcnow(), previsao=previsao, aberto_por=usuario,
        ))
        s.commit()
    _log.info("externo: %s enviou %s para %s (%s)",
              usuario, serial, body.fornecedor, especie)
    return {"ok": True, "serial": serial}


def _data(texto: str) -> datetime | None:
    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        from datetime import timezone
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(400, "Data de previsão inválida (use AAAA-MM-DD).")


class RetornoIn(BaseModel):
    serial: str
    especie: str = ASSISTENCIA
    resultado: str = ""
    serial_substituto: str = ""
    custo: float | None = None
    observacao: str = ""


@router.post("/retornar")
def api_retornar(body: RetornoIn, req: Request):
    """Recebe de volta e decide para onde o equipamento vai."""
    sd = require_permission(req, "externo", "edit")
    especie = _especie_valida(body.especie)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")

    if especie == ASSISTENCIA:
        if body.resultado not in RESULTADOS:
            raise HTTPException(400, "Informe o resultado da assistência.")
        if body.resultado == SUBSTITUIDO and not (body.serial_substituto or "").strip():
            raise HTTPException(
                400, "Informe a série do equipamento que veio no lugar — sem "
                     "ela o histórico se perde na troca.")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != FORA[especie]:
            raise HTTPException(409, f"A série {serial} não está fora da área.")
        ativo_id = ativo.id
        modelo, tipo_eq = ativo.modelo, ativo.tipo_equipamento

    substituto = (body.serial_substituto or "").strip().upper()
    proximo = _destino_do_retorno(especie, body.resultado, tipo_eq)

    with dbt.SessionLocal() as s:
        ativo = s.get(dbt.Ativo, ativo_id)
        if especie == DEVOLUCAO or body.resultado == SUBSTITUIDO:
            # O equipamento não volta para o estoque: ou ficou com o
            # terceiro, ou foi trocado por outro.
            encerrar(s, ativo,
                     estado="DEVOLVIDO" if especie == DEVOLUCAO else "SUBSTITUIDO",
                     processo=PROCESSO[especie], usuario=usuario,
                     justificativa=(f"Substituído por {substituto}"
                                    if substituto else "Devolvido ao terceiro"))
        else:
            mover(s, ativo, estado=proximo, tipo=dbt.FILA,
                  processo=PROCESSO[especie], usuario=usuario)
        s.commit()

    # O substituto entra na trilha como ativo novo, apontando para o que
    # ele veio substituir.
    if substituto:
        try:
            with dbt.SessionLocal() as s:
                ja = s.execute(
                    select(dbt.Ativo).where(dbt.Ativo.serial == substituto)
                ).scalar_one_or_none()
                if ja is None:
                    novo = abrir_ativo(s, serial=substituto, usuario=usuario,
                                       modelo=modelo, tipo_equipamento=tipo_eq,
                                       origem="ASSISTENCIA")
                    mover(s, novo, estado="AG_INTERNALIZACAO", tipo=dbt.FILA,
                          processo=PROCESSO[especie], usuario=usuario,
                          detalhe=f'{{"substitui": "{serial}"}}')
                    s.commit()
        except Exception as exc:  # noqa: BLE001
            _log.error("externo: substituto %s não entrou na trilha: %s",
                       substituto, exc)

    with SessionLocal() as s:
        envio = s.execute(
            select(Envio).where(Envio.serial == serial, Envio.especie == especie,
                                Envio.encerrado.is_(False))
            .order_by(Envio.aberto_em.desc())
        ).scalars().first()
        if envio is not None:
            envio.retornado_em = db.utcnow()
            envio.resultado = body.resultado
            envio.serial_substituto = substituto
            envio.custo = body.custo
            envio.observacao = (body.observacao or "").strip()
            envio.encerrado = True
            s.commit()

    _log.info("externo: %s retornou de %s (%s)", serial, especie, body.resultado)
    return {"ok": True, "serial": serial, "proximo_estado": proximo,
            "substituto": substituto or None}


def _destino_do_retorno(especie: str, resultado: str, tipo_eq: str) -> str:
    """Para onde o equipamento vai depois de voltar.

    Coletor reparado precisa de configuração antes de entrar no estoque;
    o resto vai direto para internalização. Sem reparo, vira baixa.
    """
    if especie == DEVOLUCAO:
        return "DEVOLVIDO"
    if resultado == SEM_REPARO:
        return "AG_DESCARACTERIZACAO"
    if (tipo_eq or "").lower() == "frota":
        return "AG_CONFIGURACAO"
    return "AG_INTERNALIZACAO"


@router.get("/historico/{serial}")
def api_historico(serial: str, req: Request):
    require_permission(req, "externo", "view")
    serial = (serial or "").strip().upper()
    with SessionLocal() as s:
        linhas = s.execute(
            select(Envio).where(Envio.serial == serial)
            .order_by(Envio.aberto_em.desc()).limit(20)
        ).scalars().all()
    return {"serial": serial, "envios": [{
        "especie": e.especie, "fornecedor": e.fornecedor, "rma": e.numero_rma,
        "enviado_em": _utc(e.enviado_em).isoformat() if e.enviado_em else None,
        "retornado_em": _utc(e.retornado_em).isoformat() if e.retornado_em else None,
        "resultado": ROTULO_RESULTADO.get(e.resultado, e.resultado),
        "custo": float(e.custo) if e.custo is not None else None,
    } for e in linhas]}


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "externo", "admin")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    require_permission(req, "externo", "admin")
    pares = {k: str(v) for k, v in (body or {}).items() if k in db.PADROES}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    return db.ler_config()
