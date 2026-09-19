from __future__ import annotations
import logging
import os
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse

from config import get_settings
from db.portal import init_db
from core.estatico import EstaticoLimpo
from core.prefixo import BarraFinalMiddleware, com_prefixo, prefixo
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
    # Antes de tudo: proxy que acrescenta barra no fim faria a API cair num
    # 307 com Location absoluto — e http:// numa página https:// é bloqueado.
    app.add_middleware(BarraFinalMiddleware)
    app.add_middleware(MaxBodyMiddleware)
    app.add_middleware(BotProtectionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    # ── Static files ────────────────────────────────────────────────────
    # EstaticoLimpo em vez de StaticFiles: o que o navegador baixa vai sem
    # os comentários do fonte, que explicam regra de negócio, tabela do EBS
    # e incidente antigo. No disco o arquivo continua comentado.
    app.mount("/static", EstaticoLimpo(directory=_cfg.STATIC), name="static")

    # ── JavaScript dos módulos (com permissão) ──────────────────────────
    # Não entra no mount de /static de propósito: lá é público, e era assim
    # que a tela de administração saía sem sessão nenhuma.
    try:
        from routers.modulos import router as modulos_router
        app.include_router(modulos_router)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("startup").error(
            "Módulo de entrega do JavaScript não carregou (as telas não vão "
            "abrir): %s", exc, exc_info=True)

    # ── Page routes ─────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        # O prefixo sai do --root-path do uvicorn ou do APP_BASE_PATH; na
        # raiz do domínio é vazio e a página vai como está.
        html = (_cfg.STATIC / "index.html").read_text(encoding="utf-8")
        return com_prefixo(html, prefixo(request))

    @app.get("/hub-infraCSC", response_class=HTMLResponse)
    @app.get("/hub-infraCSC/", response_class=HTMLResponse)
    def hub_infra_csc():
        """A tela-ponte para os portais do CSC. SEM LOGIN, de propósito.

        É só navegação: o controle de acesso acontece em cada portal de
        destino, e exigir sessão aqui obrigaria a entrar no Spare para
        alcançar um sistema que não é o Spare.

        As duas rotas existem porque quem digita o endereço erra a barra
        final, e um 404 nesse caso parece "a página não existe" — que é
        justamente a conclusão errada.

        Serve por `limpar_texto` para sair sem os comentários do fonte,
        igual ao resto de /static. O arquivo não usa caminho relativo
        nenhum (os destinos são URLs completas e as marcas estão embutidas),
        então não precisa do tratamento de prefixo de proxy.
        """
        from core.estatico import limpar_texto
        html = (_cfg.STATIC / "hub-infraCSC.html").read_text(encoding="utf-8")
        return HTMLResponse(limpar_texto(html, ".html"))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        # O ícone enviado pelo admin geral (Configuração ▸ Visual) vence o
        # padrão. Sem isto o arquivo era gravado e nunca servido — a tela
        # dizia "enviado" e o navegador seguia com o ícone antigo.
        # no-cache: o navegador revalida e troca sem reiniciar nada.
        tipos = {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}
        try:
            from routers.parametros import favicon_atual
            path = favicon_atual() or (_cfg.STATIC / "favicon.svg")
        except Exception:  # noqa: BLE001 — sem o módulo, vale o padrão
            path = _cfg.STATIC / "favicon.svg"
        if not path.exists():
            path = _cfg.STATIC / "favicon.ico"
        return FileResponse(path, media_type=tipos.get(path.suffix, "image/x-icon"),
                            headers={"Cache-Control": "no-cache"})

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
    # O espaço dos times ganhou banco próprio (liberações, listas de estoque
    # e a trilha de acesso). Sem o init_db as tabelas não existem e a tela
    # abre com erro em vez de vazia.
    try:
        import db.consulta_times as _db_ct
        _db_ct.init_db()
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("consulta_times").error(
            "Banco do Consulta Times NÃO iniciado: %s", exc, exc_info=True)
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

    # ── Agendamentos de Fornecedores (menu Entrada) — banco próprio ─────
    try:
        import db.agendamentos_forn as _db_agf
        _db_agf.init_db()
        from routers.agendamentos_forn import router as agendamentos_forn_router
        app.include_router(agendamentos_forn_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("agendamentos_forn").error(
            "Módulo Agendamentos Forn. NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Internalização (menu Entrada) — banco próprio ───────────────────
    try:
        import db.internalizacao as _db_int
        _db_int.init_db()
        from routers.internalizacao import router as internalizacao_router
        app.include_router(internalizacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("internalizacao").error(
            "Módulo Internalização NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── Cofre: o serviço enxerga os segredos? — só diagnóstico ──────────
    try:
        from routers.cofre import router as cofre_router
        app.include_router(cofre_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("cofre_diag").error(
            "Diagnóstico do cofre NÃO carregado (portal segue sem ele): %s",
            exc, exc_info=True,
        )

    # ── EBS Oracle (leitura da base do EBS) — sem banco próprio ────────────────
    # Aditivo e isolado: sem o driver Oracle ou sem credencial no cofre, o
    # módulo simplesmente não carrega e o portal segue igual.
    try:
        from routers.ebs_oracle import router as ebs_oracle_router
        app.include_router(ebs_oracle_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("ebs_oracle").error(
            "Módulo EBS Oracle NÃO carregado (portal segue sem ele): %s",
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

    # ── ServiceNow: consulta de chamados em lote (incidents e RITMs) ────
    # Só leitura, pela conta de serviço, e sem banco próprio: a tela não
    # guarda nada, consulta e devolve. Carregamento isolado como os demais.
    try:
        from routers.sn_consulta import router as sn_consulta_router
        app.include_router(sn_consulta_router)
        # Tempo de fila: lê o histórico de troca de assignment_group. Entra
        # junto porque é opção da mesma tela, mas em módulo próprio — o
        # cálculo dos intervalos é o que erra, e merece ficar isolado.
        from routers.sn_tempo_fila import router as sn_tempo_fila_router
        app.include_router(sn_tempo_fila_router)
        # Coleta das filas de técnico de campo (botão "Exportar - DADOS 2").
        # A mesma lógica que scripts/chamados_campo_lojas.py usa.
        from routers.sn_campo_lojas import router as sn_campo_lojas_router
        app.include_router(sn_campo_lojas_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("sn_consulta").error(
            "Consulta de chamados do ServiceNow NÃO carregada (portal segue sem ela): %s",
            exc, exc_info=True,
        )

    # ── EBS Forms (RPA sobre o cliente Oracle Forms) — banco próprio ────
    # Roda em segundo plano numa tela virtual; só a API entra aqui.
    try:
        import db.ebs_forms as _db_forms
        _db_forms.init_db()
        from routers.ebs_forms import router as ebs_forms_router, pagina_router as ebs_forms_pagina
        app.include_router(ebs_forms_router)
        app.include_router(ebs_forms_pagina)  # tela /ebs-forms (exige login e permissão)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("ebs_forms").error(
            "Módulo EBS Forms NÃO carregado (portal segue sem ele): %s",
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

    # ── Torre
    try:
        from routers.torre import router as torre_router
        app.include_router(torre_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("torre").error(
            "Módulo torre NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Atendimento
    try:
        import db.atendimento as _db_atendimento
        _db_atendimento.init_db()
        from routers.atendimento import router as atendimento_router
        app.include_router(atendimento_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("atendimento").error(
            "Módulo atendimento NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Preparacao
    try:
        import db.preparacao as _db_preparacao
        _db_preparacao.init_db()
        from routers.preparacao import router as preparacao_router
        app.include_router(preparacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("preparacao").error(
            "Módulo preparacao NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Separacao
    try:
        import db.separacao as _db_separacao
        _db_separacao.init_db()
        from routers.separacao import router as separacao_router
        app.include_router(separacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("separacao").error(
            "Módulo separacao NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Projetos
    try:
        import db.projetos as _db_projetos
        _db_projetos.init_db()
        from routers.projetos import router as projetos_router
        app.include_router(projetos_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("projetos").error(
            "Módulo projetos NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Reversa
    try:
        import db.reversa as _db_reversa
        _db_reversa.init_db()
        from routers.reversa import router as reversa_router
        app.include_router(reversa_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("reversa").error(
            "Módulo reversa NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Inventario
    try:
        import db.inventario as _db_inventario
        _db_inventario.init_db()
        from routers.inventario import router as inventario_router
        app.include_router(inventario_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("inventario").error(
            "Módulo inventario NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Regularizacao
    try:
        import db.regularizacao as _db_regularizacao
        _db_regularizacao.init_db()
        from routers.regularizacao import router as regularizacao_router
        app.include_router(regularizacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("regularizacao").error(
            "Módulo regularizacao NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Externo
    try:
        import db.externo as _db_externo
        _db_externo.init_db()
        from routers.externo import router as externo_router
        app.include_router(externo_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("externo").error(
            "Módulo externo NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Venda
    try:
        import db.venda as _db_venda
        _db_venda.init_db()
        from routers.venda import router as venda_router
        app.include_router(venda_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("venda").error(
            "Módulo venda NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Destinacao
    try:
        import db.destinacao as _db_destinacao
        _db_destinacao.init_db()
        from routers.destinacao import router as destinacao_router
        app.include_router(destinacao_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("destinacao").error(
            "Módulo destinacao NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    # ── Trilha
    try:
        import db.trilha as _db_trilha
        _db_trilha.init_db()
        from routers.trilha import router as trilha_router
        app.include_router(trilha_router)
    except Exception as exc:  # noqa: BLE001 — nunca derrubar o portal
        logging.getLogger("trilha").error(
            "Módulo trilha NÃO carregado (portal segue sem ele): %s", exc, exc_info=True)

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    ssl_kwargs = {}
    if _cfg.SSL_CERTFILE and _cfg.SSL_KEYFILE:
        ssl_kwargs["ssl_certfile"] = _cfg.SSL_CERTFILE
        ssl_kwargs["ssl_keyfile"] = _cfg.SSL_KEYFILE

    # Sessão mora na memória do processo; com mais de um worker o login some
    # de forma intermitente. Avisa alto antes de subir, em vez de virar
    # "a sessão do ServiceNow expira sozinha".
    from core.security import avisar_se_multiprocesso
    avisar_se_multiprocesso(_cfg.WORKERS)

    uvicorn.run(
        "main:app",
        host=_cfg.HOST,
        port=_cfg.PORT,
        workers=_cfg.WORKERS,
        **ssl_kwargs,
    )
