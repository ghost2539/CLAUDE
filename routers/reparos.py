"""Reparos (repairs) router — register repairs and dashboard."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from config import get_settings
from db.portal import (
    SessionLocal, Asset, ReceiptCycle, Repair, Movement, Setting,
)
from core.security import require_permission, check_rate_limit
from routers.helpers import find_asset

_cfg = get_settings()
RESULT_STATUS = _cfg.RESULT_STATUS_MAP
CLOSED = _cfg.CLOSED_STATUSES
router = APIRouter(prefix="/api", tags=["Reparos"])


# ── Pydantic models ───────────────────────────────────────────────

class RepairIn(BaseModel):
    imobilizado: str = ""
    etiqueta: str = ""
    numero_serie: str = ""
    triagem_min: int = 0
    reparo_min: int = 0
    pesquisa_min: int = 0
    higienizacao_min: int = 0
    resultado: str
    tecnico: str
    observacao: str = ""

    @field_validator("resultado")
    @classmethod
    def validate_result(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Resultado obrigatório.")
        return v

    @field_validator("tecnico")
    @classmethod
    def validate_tech(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Técnico obrigatório.")
        return v


# ── Endpoints ─────────────────────────────────────────────────────

# O registro manual de reparo saiu: o tempo agora é medido pela trilha
# (Central de Reparos). O dashboard abaixo fica só para o histórico.


@router.get("/reparos/dashboard")
def repair_dashboard(req: Request, data_inicio: str, data_fim: str):
    require_permission(req, "reparos", "view")

    with SessionLocal() as s:
        rows = s.scalars(
            select(Repair)
            .where(Repair.repair_date.between(
                date.fromisoformat(data_inicio),
                date.fromisoformat(data_fim),
            ))
            .order_by(Repair.id.desc())
        ).all()

        cats: dict[str, dict] = {}
        out = []

        for r in rows:
            a = s.get(Asset, r.asset_id)
            d = {
                "data": r.repair_date.isoformat(),
                "imobilizado": a.asset_id or a.asset_number,
                "categoria": a.category,
                "modelo": a.model,
                "triagem_min": r.triage_min,
                "reparo_min": r.repair_min,
                "pesquisa_min": r.research_min,
                "higienizacao_min": r.hygiene_min,
                "total_min": r.total_min,
                "saving": float(r.saving),
                "resultado": r.result,
                "tecnico": r.technician,
            }
            out.append(d)

            key = a.category or "N/D"
            z = cats.setdefault(key, {
                "categoria": key,
                "quantidade": 0,
                "total_min": 0,
                "saving": 0.0,
            })
            z["quantidade"] += 1
            z["total_min"] += r.total_min
            z["saving"] += float(r.saving)

        for z in cats.values():
            z["total_horas"] = z["total_min"] / 60

        total_min = sum(r.total_min for r in rows)
        return {
            "total": len(rows),
            "total_min": total_min,
            "total_horas": total_min / 60,
            "total_saving": sum(float(r.saving) for r in rows),
            "por_categoria": list(cats.values()),
            "registros": out,
        }
