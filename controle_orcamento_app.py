"""Controle de Orçamento (/tv2) como SERVIÇO PRÓPRIO, independente do portal.

Sobe apenas o módulo (página /tv2 + API + banco separado), sem carregar o
restante do Portal SPARE. Assim uma atualização do portal nunca afeta esta
tela, e ela pode ser instalada sozinha em outro servidor.

Execução:
    python -m uvicorn controle_orcamento_app:app --host 0.0.0.0 --port 8902

Variáveis de ambiente (arquivo /etc/controle_orcamento/environment no systemd):
    CONTROLE_ORCAMENTO_DATABASE_URL  URL do banco do módulo (obrigatória em produção;
                                     sem ela usa SQLite em data/controle_orcamento.db)
    UPLOAD_MAX_MB, RATE_LIMIT_API    opcionais (mesmos nomes do portal)

Arquivos necessários (além deste): config.py, security.py, database_orcamento.py,
routers/__init__.py, routers/controle_orcamento.py, static/controle-orcamento/*,
static/favicon.svg.
"""
from __future__ import annotations

import os
import secrets

# config.py exige estas variáveis; aqui elas não são usadas para nada
# (não há banco do portal nem sessões de login neste serviço).
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PORTAL_SESSION_SECRET", secrets.token_urlsafe(32))

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from config import get_settings  # noqa: E402
from security import (  # noqa: E402
    BotProtectionMiddleware, MaxBodyMiddleware, SecurityHeadersMiddleware,
)
from routers.controle_orcamento import router  # noqa: E402

_cfg = get_settings()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Controle de Orçamento de Portfólio",
        docs_url=None, redoc_url=None, openapi_url=None,
    )
    app.add_middleware(MaxBodyMiddleware)
    app.add_middleware(BotProtectionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory=_cfg.STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def raiz():
        return RedirectResponse("/tv2", status_code=302)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(_cfg.STATIC / "favicon.svg", media_type="image/svg+xml")

    @app.get("/saude", include_in_schema=False)
    def saude():
        return {"ok": True, "servico": "controle-orcamento"}

    app.include_router(router)
    return app


app = create_app()
