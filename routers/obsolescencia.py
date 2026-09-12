"""Painel de Obsolescência do parque de coletores.

Tela separada do portal — abre em outra guia, em tela cheia, para ser
apresentada em reunião —, porém integrada a ele: mesma sessão, mesmo
visual e mesmo processo. Quem já está logado no portal entra direto,
sem liberação à parte.

A origem dos dados é o MDM de Coletores (Workspace ONE / AirWatch),
lido em segundo plano pelo portal. Enquanto a primeira coleta não
existe, o painel assume o estado "sem dados": o levantamento do que o
MDM devolve é feito com `scripts/mdm_airwatch_captura.js`, e o
tratamento entra aqui depois, sobre o formato real.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.security import get_session

router = APIRouter()

_DIR = Path(__file__).resolve().parent.parent / "static" / "obsolescencia"


# ── Página ────────────────────────────────────────────────────────
@lru_cache
def _asset_version() -> str:
    """Impede que o navegador sirva um app.js velho depois do deploy."""
    h = hashlib.sha256()
    for nome in ("app.js", "app.css"):
        p = _DIR / nome
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _page() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{v}}", _asset_version()))


def _acesso_pagina(req: Request):
    """Só exige estar logado no portal: a tela é parte do sistema, não um
    módulo à parte. Sem sessão, manda para o login levando o destino."""
    sd = get_session(req, required=False)
    if not sd:
        return RedirectResponse(f"/?next={quote(req.url.path, safe='/')}", status_code=302)
    return _page()


@router.get("/obsolescencia", response_class=HTMLResponse)
def pagina(req: Request):
    return _acesso_pagina(req)


@router.get("/obsolescencia/", response_class=HTMLResponse)
def pagina_barra(req: Request):
    return _acesso_pagina(req)


# ── API ───────────────────────────────────────────────────────────
@router.get("/api/obsolescencia/resumo")
def resumo(req: Request):
    """Situação do parque. Enquanto o coletor do MDM não roda pela
    primeira vez, devolve `pendente` para a tela explicar o que falta."""
    sd = get_session(req)  # exige sessão do portal
    return {
        "pendente": True,
        "coletado_em": None,
        "fonte": "MDM de Coletores (Workspace ONE / AirWatch)",
        "total": 0,
        "usuario": sd.get("display_name") or sd.get("username", ""),
    }
