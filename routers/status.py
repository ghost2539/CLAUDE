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

    # EBS connectivity
    ebs = {
        "connected": (
            bool(sd.get("ebs_auth")) if sd.get("auth_source") == "AD" else False
        ),
        "not_applicable": sd.get("auth_source") != "AD",
    }

    return {"ebs": ebs, "postgres": pg, "local": local}


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
