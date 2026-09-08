"""Status router — system health check and dashboard summary."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from sqlalchemy import select, func, text

from db.portal import SessionLocal, ReceiptCycle, Repair, LocalAsset, engine
from core.security import get_session

router = APIRouter(prefix="/api", tags=["Status"])


@router.get("/status")
def system_status(req: Request):
    sd = get_session(req)

    # PostgreSQL connectivity
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        pg = {"connected": True}
    except Exception as e:
        pg = {"connected": False, "error": str(e)}

    # Local asset base
    try:
        with SessionLocal() as s:
            count = s.scalar(
                select(func.count())
                .select_from(LocalAsset)
                .where(LocalAsset.active == True)  # noqa: E712
            ) or 0
        local = {"connected": True, "count": count}
    except Exception as e:
        local = {"connected": False, "error": str(e)}

    return {"ebs": _ebs(sd), "postgres": pg, "local": local,
            "servidor": _saude_servidor()}


@router.get("/dashboard/summary")
def dashboard_summary(req: Request):
    get_session(req)

    today = date.today()
    first = today.replace(day=1)

    with SessionLocal() as s:
        return {
            "recebidos_mes": s.scalar(
                select(func.count())
                .select_from(ReceiptCycle)
                .where(ReceiptCycle.received_date >= first)
            ) or 0,
            "recebidos_ano": s.scalar(
                select(func.count())
                .select_from(ReceiptCycle)
                .where(func.extract("year", ReceiptCycle.received_date) == today.year)
            ) or 0,
            "reparos_mes": s.scalar(
                select(func.count())
                .select_from(Repair)
                .where(Repair.repair_date >= first)
            ) or 0,
            "saving": float(
                s.scalar(
                    select(func.coalesce(func.sum(Repair.saving), 0))
                    .where(Repair.repair_date >= first)
                ) or 0
            ),
        }


def _ebs(sd: dict) -> dict:
    """Situação real do EBS.

    As consultas do portal rodam com a conta de serviço (mesma que o módulo de
    Monitoramento checa); a sessão EBS do próprio usuário só existe em login
    por AD. Apurar só pela sessão do usuário fazia a tela dizer "N/A" mesmo com
    as consultas funcionando.
    """
    if sd.get("auth_source") == "AD" and sd.get("ebs_auth"):
        return {"connected": True, "not_applicable": False,
                "modo": "sessão do usuário"}
    try:
        from routers.public_assets import _auth as _ebs_auth
        ok = bool(_ebs_auth())
        saida = {"connected": ok, "not_applicable": False,
                 "modo": "conta de serviço"}
        if not ok:
            saida["error"] = "conta de serviço não autenticou"
        return saida
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "not_applicable": False,
                "modo": "conta de serviço", "error": str(exc)[:300]}


def _saude_servidor() -> dict | None:
    """Recorte de saúde vindo do Monitoramento (memória, disco, carga, uptime
    e falhas críticas). Módulo ausente ou com defeito => bloco simplesmente não
    aparece, sem quebrar a tela de Status."""
    try:
        from routers.monitoramento import saude_servidor
        return saude_servidor(limite_falhas=5)
    except Exception:  # noqa: BLE001
        return None
