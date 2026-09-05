"""Consulta de Ativos — Times, em `/consulta-times`.

Era um aplicativo separado, em serviço e porta próprios (8502). No servidor
novo não há root para manter vários serviços, então virou router do portal:
mesma porta, mesmo processo, mesmo deploy.

Espelha o módulo Consulta do portal (mesmo backend EBS e as mesmas regras de
classificação), mas com tela enxuta, para outros times consultarem ativo sem
precisar de acesso ao portal inteiro.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from config import get_settings
from core.security import check_rate_limit
from db.portal import SessionLocal
from routers.helpers import apply_class, xlsx_response

_cfg = get_settings()
_log = logging.getLogger("consulta_times")
_DIR = _cfg.STATIC / "consulta-times"

router = APIRouter(tags=["Consulta de Ativos — Times"], include_in_schema=False)

LIMITE_IDS = 1000


class ConsultaIn(BaseModel):
    identificadores: list[str]

    @field_validator("identificadores")
    @classmethod
    def limpar(cls, v: list[str]) -> list[str]:
        vistos: set[str] = set()
        out: list[str] = []
        for bruto in v:
            item = str(bruto).strip()
            if item and item not in vistos:
                vistos.add(item)
                out.append(item)
            if len(out) >= LIMITE_IDS:
                break
        return out


# ── Página ──────────────────────────────────────────────────────────────
def _pagina() -> HTMLResponse:
    return HTMLResponse((_DIR / "index.html").read_text(encoding="utf-8"))


@router.get("/consulta-times", response_class=HTMLResponse)
def pagina():
    return _pagina()


@router.get("/consulta-times/", response_class=HTMLResponse)
def pagina_barra():
    return _pagina()


# ── API ─────────────────────────────────────────────────────────────────
def _consultar(ids: list[str]) -> dict:
    if not ids:
        return {"resultados": [], "encontrados": 0, "nao_encontrados": 0}

    from routers.public_assets import _auth
    import integracoes.ebs_service as ebs_service

    linhas = ebs_service.search_many(_auth(), ids)
    with SessionLocal() as s:
        linhas = [apply_class(s, r) for r in linhas]
    return {
        "resultados": linhas,
        "encontrados": sum(bool(x.get("encontrado")) for x in linhas),
        "nao_encontrados": sum(not x.get("encontrado") for x in linhas),
    }


@router.post("/api/consulta-times/consulta")
def consulta(body: ConsultaIn, req: Request):
    check_rate_limit(req, "api")
    return _consultar(body.identificadores)


@router.post("/api/consulta-times/consulta/export")
def consulta_export(body: ConsultaIn, req: Request):
    check_rate_limit(req, "api")
    resultado = _consultar(body.identificadores)
    return xlsx_response(resultado["resultados"], "consulta_ativos.xlsx")
