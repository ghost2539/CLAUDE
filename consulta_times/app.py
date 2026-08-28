"""Consulta de Ativos — standalone app (port 8502).

Mirrors the Consulta module from the main portal, using the same EBS
backend and classification rules. Runs independently so other teams
can query assets without full portal access.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add parent directory to path so we can import shared modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from config import get_settings
from database import SessionLocal, init_db
from routers.helpers import apply_class, local_search_one, xlsx_response

_cfg = get_settings()
_static = Path(__file__).resolve().parent / "static"


class QueryIn(BaseModel):
    identificadores: list[str]

    @field_validator("identificadores")
    @classmethod
    def clean_ids(cls, v: list[str]) -> list[str]:
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Consulta de Ativos — Times",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.mount("/static", StaticFiles(directory=str(_static)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (_static / "index.html").read_text(encoding="utf-8")

    @app.post("/api/consulta")
    def query_assets(body: QueryIn):
        qs = body.identificadores
        if not qs:
            return {"resultados": [], "encontrados": 0, "nao_encontrados": 0}

        from modules.logged_ebs_v8 import search_for_user
        from routers.public_assets import _auth

        auth = _auth()
        import ebs_service
        rows = ebs_service.search_many(auth, qs)

        with SessionLocal() as s:
            rows = [apply_class(s, r) for r in rows]

        return {
            "resultados": rows,
            "encontrados": sum(bool(x.get("encontrado")) for x in rows),
            "nao_encontrados": sum(not x.get("encontrado") for x in rows),
        }

    @app.post("/api/consulta/export")
    def export_query(body: QueryIn):
        result = query_assets(body)
        return xlsx_response(result["resultados"], "consulta_ativos.xlsx")

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8502, workers=2)
