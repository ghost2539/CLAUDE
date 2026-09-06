from __future__ import annotations
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from db.portal import init_db
from core.security import (
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
    from routers.public_assets import router as public_assets_router
    from routers.identificacao import router as identificacao_router
    from routers.servicenow import router as servicenow_router
    from routers.correios import router as correios_router
    from routers.consulta_times import router as consulta_times_router
    from routers.encerramento import router as encerramento_router

    app.include_router(auth_router)
    app.include_router(consulta_router)
    app.include_router(recebimento_router)
    app.include_router(reparos_router)
    app.include_router(parametros_router)
    app.include_router(status_router)
    app.include_router(public_assets_router)
    app.include_router(identificacao_router)
    app.include_router(servicenow_router)
    app.include_router(correios_router)
    app.include_router(consulta_times_router)
    app.include_router(encerramento_router)

    # ── Controle de Orçamento — Execução CAPEX em /controle-orcamento ───
    # Banco próprio e integração à API de CAPEX do EBS. Exige login do portal
    # e permissão do módulo "orcamento". Carregamento isolado.
    try:
        from routers.controle_orcamento_exec import router as controle_orcamento_exec_router
        app.include_router(controle_orcamento_exec_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("controle_orcamento_exec").error(
            "Módulo Controle de Orçamento/Execução (/controle-orcamento) NÃO carregado: %s",
            exc, exc_info=True,
        )

    # ── Indicadores (RMR) em /indicadores (código e banco próprios) ─────
    # Carregamento isolado: qualquer erro (arquivo, dependência, banco) é
    # apenas registrado no log e o portal sobe normalmente sem ele.
    try:
        import db.indicadores as _db_indic
        _db_indic.init_db()
        from routers.indicadores import router as indicadores_router, start_scheduler
        app.include_router(indicadores_router)
        start_scheduler()  # recálculo periódico em segundo plano (SN_INDIC_REFRESH_MIN)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("indicadores").error(
            "Módulo Indicadores (/indicadores) NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Telas de TV (cockpit e dashboards) — públicas, só agregado ──────
    # Sem login por decisão de produto: ficam em painel na parede. Os
    # endpoints não devolvem nada individualizado (ver routers/cockpit.py).
    try:
        from routers.cockpit import router as cockpit_router
        app.include_router(cockpit_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("cockpit").error(
            "Módulo Cockpit/TV NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Orçamento do SPARE (CAPEX da área) — banco próprio ──────────────
    # Acesso pelo módulo de permissão "orcamento_spare".
    try:
        import db.orcamento_spare as _db_orc_spare
        _db_orc_spare.init_db()
        from routers.orcamento_spare import router as orcamento_spare_router
        app.include_router(orcamento_spare_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("orcamento_spare").error(
            "Módulo Orçamento SPARE NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Monitoramento (saúde e falhas) — banco próprio ──────────────────
    # Aditivo: só observa. Falha aqui nunca derruba o portal.
    try:
        import db.monitoramento as _db_mon
        _db_mon.init_db()
        from routers.monitoramento import (
            router as monitoramento_router, MonitorFalhasMiddleware,
        )
        app.include_router(monitoramento_router)
        app.add_middleware(MonitorFalhasMiddleware)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("monitoramento").error(
            "Módulo Monitoramento NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Automações (encerramento/encaminhamento) — banco próprio ────────
    # Carregamento isolado (nunca derruba o portal).
    try:
        import db.automacoes as _db_autom
        _db_autom.init_db()
        from routers.automacoes import router as automacoes_router, start_scheduler as _autom_sched
        app.include_router(automacoes_router)
        _autom_sched()  # rotina agendada (07/12/16 por padrão)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("automacoes").error(
            "Módulo Automações NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── EBS Forms (RPA sobre o cliente Oracle Forms) — banco próprio ────
    # Roda em segundo plano numa tela virtual; só a API e a tela entram aqui.
    # Aditivo: falha nele nunca derruba o portal.
    try:
        import db.ebs_forms as _db_forms
        _db_forms.init_db()
        from routers.ebs_forms import router as ebs_forms_router, pagina_router as ebs_forms_pagina
        app.include_router(ebs_forms_router)
        app.include_router(ebs_forms_pagina)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("ebs_forms").error(
            "Módulo EBS Forms NÃO carregado (portal segue sem ele): %s",
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
