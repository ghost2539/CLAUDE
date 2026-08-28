"""TV dashboard router — public endpoint for TV display, no auth required."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime

from fastapi import APIRouter
from sqlalchemy import select, func

from database import SessionLocal, ReceiptCycle, Setting

router = APIRouter(prefix="/api", tags=["TV"])


@router.get("/tv/dashboard")
def tv_dashboard():
    """Public endpoint — no authentication required.

    Returns aggregated data for the TV display panel.
    """
    today = date.today()
    first = today.replace(day=1)

    with SessionLocal() as s:
        year_cycles = s.scalars(
            select(ReceiptCycle).where(
                func.extract("year", ReceiptCycle.received_date) == today.year
            )
        ).all()

        recent = s.scalars(
            select(ReceiptCycle).order_by(ReceiptCycle.id.desc()).limit(8)
        ).all()

        cat = Counter(c.asset.category or "N/D" for c in year_cycles)
        stat = Counter(c.status for c in year_cycles)

        cfg_row = s.get(Setting, "tv")
        cfg = cfg_row.value if cfg_row else {}

        return {
            "config": cfg,
            "recebidos_hoje": sum(
                1 for c in year_cycles if c.received_date == today
            ),
            "recebidos_mes": sum(
                1 for c in year_cycles if c.received_date >= first
            ),
            "recebidos_ano": len(year_cycles),
            "ativos_unicos": len(set(c.asset_id for c in year_cycles)),
            "por_categoria": [
                {"categoria": k, "total": v}
                for k, v in cat.most_common(10)
            ],
            "por_status": [
                {"status": k, "total": v}
                for k, v in stat.items()
            ],
            "ultimos": [
                {
                    "hora": (
                        c.created_at.astimezone().strftime("%H:%M")
                        if c.created_at.tzinfo
                        else c.created_at.strftime("%H:%M")
                    ),
                    "imobilizado": c.asset.asset_id or c.asset.asset_number,
                    "descricao": c.asset.description,
                    "categoria": c.asset.category,
                    "status": c.status,
                }
                for c in recent
            ],
            "updated_at": datetime.now().isoformat(),
        }
