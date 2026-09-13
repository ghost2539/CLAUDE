"""Consulta (asset query) router — search, single lookup, export."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from db.portal import SessionLocal, ReceiptCycle
from core.security import get_session, require_permission
from routers.helpers import (
    apply_class, find_asset, local_search_one, xlsx_response,
)

router = APIRouter(prefix="/api", tags=["Consulta"])


# ── Pydantic models ───────────────────────────────────────────────

class QueryIn(BaseModel):
    identificadores: list[str]

    @field_validator("identificadores")
    @classmethod
    def clean_ids(cls, v: list[str]) -> list[str]:
        # Deduplicate, strip, remove blanks, cap at 1000
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
            if len(out) >= 1000:
                break
        return out


# ── Internal helpers ──────────────────────────────────────────────

def _query_assets(body: QueryIn, req: Request) -> dict:
    """Shared logic for POST /consulta and POST /consulta/export."""
    sd = require_permission(req, "consulta", "view")
    qs = body.identificadores
    if not qs:
        return {"resultados": [], "encontrados": 0, "nao_encontrados": 0}

    with SessionLocal() as s:
        from integracoes.ebs_logged import search_for_user
        rows = search_for_user(sd, qs, s, local_search_one)
        rows = [apply_class(s, r) for r in rows]

    return {
        "resultados": rows,
        "encontrados": sum(bool(x.get("encontrado")) for x in rows),
        "nao_encontrados": sum(not x.get("encontrado") for x in rows),
    }


def _query_single(identificador: str, req: Request) -> dict:
    """Lookup a single asset and return the enriched result."""
    body = QueryIn(identificadores=[identificador])
    result = _query_assets(body, req)["resultados"]
    if not result:
        raise HTTPException(404, "Não encontrado")
    result = result[0]
    if not result.get("encontrado"):
        raise HTTPException(404, result.get("erro", "Não encontrado"))

    with SessionLocal() as s:
        a = find_asset(s, result)
        c = None
        if a:
            c = s.scalar(
                select(ReceiptCycle)
                .where(ReceiptCycle.asset_id == a.id, ReceiptCycle.open == True)  # noqa: E712
                .order_by(ReceiptCycle.id.desc())
            )
        result["ciclo"] = c.cycle_number if c else None
        result["cycle_id"] = c.id if c else None
    return result


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/consulta")
def query_assets(body: QueryIn, req: Request):
    return _query_assets(body, req)


@router.get("/consulta/single")
def query_single(identificador: str, req: Request):
    return _query_single(identificador, req)


@router.post("/consulta/export")
def export_query(body: QueryIn, req: Request):
    require_permission(req, "consulta", "export")
    rows = _query_assets(body, req)["resultados"]
    return xlsx_response(rows, "consulta_ativos.xlsx")
