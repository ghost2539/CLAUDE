"""Reparos (repairs) router — register repairs and dashboard."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from config import get_settings
from database import (
    SessionLocal, Asset, ReceiptCycle, Repair, Movement, Setting,
)
from security import require_permission, check_rate_limit
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

@router.post("/reparos")
def repair_add(body: RepairIn, req: Request):
    sd = require_permission(req, "reparos", "create")
    check_rate_limit(req)

    result_status = RESULT_STATUS.get(body.resultado)

    with SessionLocal.begin() as s:
        rdata = {
            "asset_id": body.imobilizado,
            "asset_number": body.imobilizado,
            "ativo": body.imobilizado,
            "tag_number": body.etiqueta,
            "etiqueta": body.etiqueta,
            "serial_number": body.numero_serie,
            "numero_serie": body.numero_serie,
        }
        a = find_asset(s, rdata)
        if not a:
            raise HTTPException(404, "Ativo não encontrado na base de recebimentos.")

        c = s.scalar(
            select(ReceiptCycle)
            .where(
                ReceiptCycle.asset_id == a.id,
                ReceiptCycle.open == True,  # noqa: E712
            )
            .order_by(ReceiptCycle.id.desc())
        )
        if not c:
            raise HTTPException(404, "Ativo sem ciclo de recebimento aberto.")

        vals = [body.triagem_min, body.reparo_min, body.pesquisa_min, body.higienizacao_min]
        total = sum(vals)

        rate_row = s.get(Setting, "hourly_rate")
        rate = float((rate_row.value if rate_row else {}).get("value", 150))
        saving = round(total / 60 * rate, 2)

        s.add(Repair(
            asset_id=a.id,
            cycle_id=c.id,
            triage_min=vals[0],
            repair_min=vals[1],
            research_min=vals[2],
            hygiene_min=vals[3],
            total_min=total,
            hourly_rate=Decimal(str(rate)),
            saving=Decimal(str(saving)),
            result=body.resultado,
            technician=body.tecnico,
            note=body.observacao,
            created_by=sd["username"],
        ))

        if result_status:
            old = c.status
            c.status = result_status
            c.open = result_status not in CLOSED
            s.add(Movement(
                asset_id=a.id,
                cycle_id=c.id,
                old_status=old,
                new_status=result_status,
                origin="REPARO",
                username=sd["username"],
            ))

    return {"ok": True, "total_min": total, "saving": saving, "status": result_status}


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
