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
    # A tela Consulta de Ativos — Times também responde na porta antiga
    # (:8502). É o mesmo processo: um listener a mais, serviço nenhum.
    try:
        from routers.consulta_times import iniciar_espelho
        logging.getLogger("consulta_times").info("espelho: %s", await iniciar_espelho())
    except Exception as exc:  # noqa: BLE001 — espelho é acessório
        logging.getLogger("consulta_times").error(
            "espelho na porta antiga NÃO subiu (portal segue normal): %s", exc)
    yield
    try:
        from routers.consulta_times import parar_espelho
        await parar_espelho()
    except Exception:  # noqa: BLE001
        pass


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

    # ── Orçamento de Manutenção (reparo de coletores e SLEDs) — banco próprio
    # Acesso pelo módulo de permissão "orcamento_manutencao".
    try:
        import db.orcamento_manutencao as _db_orc_manut
        _db_orc_manut.init_db()
        from routers.orcamento_manutencao import router as orcamento_manutencao_router
        app.include_router(orcamento_manutencao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("orcamento_manutencao").error(
            "Módulo Orçamento Manutenção NÃO carregado (portal segue sem ele): %s",
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

    # ── Trilha do Ativo — núcleo de rastreabilidade e relógios ──────────
    # Espinha dos processos da área: o token do ativo, a movimentação
    # imutável e os intervalos de que saem todos os indicadores de tempo.
    # Carregamento isolado como os demais — mas note que, diferente deles,
    # os módulos de processo dependem deste para registrar trilha.
    try:
        import db.trilha as _db_trilha
        _db_trilha.init_db()
        from routers.trilha import router as trilha_router
        app.include_router(trilha_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("trilha").error(
            "Núcleo da Trilha do Ativo NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Separação e Expedição (A15) — banco próprio ─────────────────────
    # Depende do núcleo da Trilha para registrar o tempo, mas carrega
    # isolado: se a trilha não subir, a separação ainda funciona sem
    # medir — operação não para por causa de indicador.
    try:
        import db.separacao as _db_sep
        _db_sep.init_db()
        from routers.separacao import router as separacao_router
        app.include_router(separacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("separacao").error(
            "Módulo Separação NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Atendimento a chamados (A20) — banco próprio ────────────────────
    # Espelha o chamado do ServiceNow para medir o tempo de quem atende e
    # ligar o atendimento à separação. Carregamento isolado: a separação
    # funciona sem ele (só não devolve o chamado à fila sozinha).
    try:
        import db.atendimento as _db_atd
        _db_atd.init_db()
        from routers.atendimento import router as atendimento_router
        app.include_router(atendimento_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("atendimento").error(
            "Módulo Atendimento NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Bancadas de triagem e reparo (A02, A03, A04) ────────────────────
    # Primeiro módulo que usa a Trilha como fonte de estado: a fila é
    # lida dos intervalos abertos do núcleo. Sem a trilha no ar, a fila
    # aparece vazia — por isso carrega isolado e depois dela.
    try:
        import db.bancada as _db_bnc
        _db_bnc.init_db()
        from routers.bancada import router as bancada_router
        app.include_router(bancada_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("bancada").error(
            "Módulo Bancada NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Preparação (A06, A07, A08.2) — banco próprio ────────────────────
    # Fecha os caminhos que a bancada abre: coletor apto vai configurar,
    # sled é montado, e tudo termina internalizado no estoque.
    try:
        import db.preparacao as _db_prp
        _db_prp.init_db()
        from routers.preparacao import router as preparacao_router
        app.include_router(preparacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("preparacao").error(
            "Módulo Preparação NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Destinação (A09 a A13) — banco próprio ──────────────────────────
    # Fecha o caminho do reparo inviável. Guarda anexos comprobatórios em
    # data/uploads/destinacao — é o único módulo que grava arquivo.
    try:
        import db.destinacao as _db_dst
        _db_dst.init_db()
        from routers.destinacao import router as destinacao_router
        app.include_router(destinacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("destinacao").error(
            "Módulo Destinação NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    try:
        from routers.obsolescencia import router as obsolescencia_router
        app.include_router(obsolescencia_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("obsolescencia").error(
            "Painel de Obsolescência NÃO carregado (portal segue sem ele): %s",
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
