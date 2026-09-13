"""Consulta (asset query) router — search, single lookup, export."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from db.portal import SessionLocal, ReceiptCycle, Setting
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


# ── Colunas visíveis na consulta ────────────────────────────────────
# A visão padrão é da área (Configuração); cada pessoa pode reduzir ou
# reordenar a sua. Guardado em Setting, chave por login.
COLUNAS_CONSULTA = [
    ("empresa", "Empresa"), ("imobilizado", "Imobilizado"), ("etiqueta", "Etiqueta"),
    ("numero_serie", "Nº Série"), ("descricao", "Descrição"), ("categoria", "Categoria"),
    ("modelo", "Modelo"), ("fonte", "Fonte"), ("erro", "Erro"),
    ("local_atribuido", "Local Atribuído"),
]
_CHAVES = {c for c, _ in COLUNAS_CONSULTA}


def _colunas_padrao(s) -> list[str]:
    row = s.get(Setting, "consulta_colunas_padrao")
    lista = (row.value or {}).get("colunas") if row else None
    lista = [c for c in (lista or []) if c in _CHAVES]
    return lista or [c for c, _ in COLUNAS_CONSULTA]


@router.get("/consulta/colunas")
def colunas_ler(req: Request):
    sd = get_session(req)
    with SessionLocal() as s:
        padrao = _colunas_padrao(s)
        row = s.get(Setting, f"consulta_colunas:{sd.get('username', '')}")
        minhas = [c for c in ((row.value or {}).get("colunas") or []) if c in _CHAVES] if row else []
    return {"disponiveis": [{"chave": c, "rotulo": r} for c, r in COLUNAS_CONSULTA],
            "padrao": padrao, "minhas": minhas or padrao, "personalizada": bool(minhas)}


@router.put("/consulta/colunas")
def colunas_gravar(payload: dict, req: Request):
    sd = get_session(req)
    lista = [c for c in (payload or {}).get("colunas", []) if c in _CHAVES]
    with SessionLocal.begin() as s:
        chave = f"consulta_colunas:{sd.get('username', '')}"
        row = s.get(Setting, chave)
        if not lista:
            if row is not None:
                s.delete(row)
        else:
            if row is None:
                row = Setting(key=chave)
                s.add(row)
            row.value = {"colunas": lista}
            row.updated_by = sd.get("username", "")
    return colunas_ler(req)
