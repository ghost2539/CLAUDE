from __future__ import annotations
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from database import init_db
from security import (
    SecurityHeadersMiddleware,
    BotProtectionMiddleware,
    MaxBodyMiddleware,
)

_cfg = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Portal de Operações - SPARE",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # ── Middleware stack (order matters — last added = first executed) ──
    app.add_middleware(MaxBodyMiddleware)
    app.add_middleware(BotProtectionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # ── Static files ────────────────────────────────────────────────────
    app.mount("/static", StaticFiles(directory=_cfg.STATIC), name="static")

    # ── Page routes ─────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (_cfg.STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/tv", response_class=HTMLResponse)
    def tv():
        return (_cfg.STATIC / "tv.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        path = _cfg.STATIC / "favicon.svg"
        if path.exists():
            return FileResponse(path, media_type="image/svg+xml")
        return FileResponse(_cfg.STATIC / "favicon.ico")

    # ── Register routers ────────────────────────────────────────────────
    from routers.auth import router as auth_router
    from routers.consulta import router as consulta_router
    from routers.recebimento import router as recebimento_router
    from routers.reparos import router as reparos_router
    from routers.parametros import router as parametros_router
    from routers.status import router as status_router
    from routers.tv import router as tv_router
    from routers.public_assets import router as public_assets_router
    from routers.identificacao import router as identificacao_router
    from routers.servicenow import router as servicenow_router
    from routers.correios import router as correios_router
    from routers.encerramento import router as encerramento_router

    app.include_router(auth_router)
    app.include_router(consulta_router)
    app.include_router(recebimento_router)
    app.include_router(reparos_router)
    app.include_router(parametros_router)
    app.include_router(status_router)
    app.include_router(tv_router)
    app.include_router(public_assets_router)
    app.include_router(identificacao_router)
    app.include_router(servicenow_router)
    app.include_router(correios_router)
    app.include_router(encerramento_router)

    # ── Controle de Orçamento em /tv2 (código e banco próprios) ─────────
    # Carregamento isolado: qualquer erro (arquivo ausente, dependência,
    # banco) é apenas registrado no log e o portal sobe normalmente sem ele.
    try:
        from routers.controle_orcamento import router as controle_orcamento_router
        app.include_router(controle_orcamento_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("controle_orcamento").error(
            "Módulo Controle de Orçamento (/tv2) NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    ssl_kwargs = {}
    if _cfg.SSL_CERTFILE and _cfg.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = _cfg.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = _cfg.SSL_KEYFILE

    uvicorn.run(
        "main:app",
        host=_cfg.HOST,
        port=_cfg.PORT,
        workers=_cfg.WORKERS,
        **ssl_kwargs,
    )
