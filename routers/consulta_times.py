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

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, field_validator

from config import get_settings
from core.prefixo import com_prefixo, prefixo
from core.security import check_rate_limit, client_ip, get_session, require_permission

import db.consulta_times as dbct
from db.portal import SessionLocal
from routers.helpers import apply_class, xlsx_response

_cfg = get_settings()
_log = logging.getLogger("consulta_times")
_DIR = _cfg.STATIC / "consulta-times"

router = APIRouter(tags=["Consulta de Ativos — Times"], include_in_schema=False)

# Nome do módulo no mapa de permissões do portal: é por ele que
# `nivel_efetivo` lê a liberação de quem entra pelo portal.
MODULO = "consulta_times"
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
def _pagina(req=None) -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(com_prefixo(html, prefixo(req)))


class LiberacaoIn(BaseModel):
    login: str
    nivel: str = "view"
    nome: str = ""

# ── Listas das telas do ServiceNow, editáveis por quem administra o espaço ──
class ListasIn(BaseModel):
    estoques: list[str] = []
    corredores: list[str] = []


def _exigir(req: Request, minimo: str) -> dict:
    sd = get_session(req)
    n = nivel_efetivo(sd)
    if _NIVEL.get(n, 0) < _NIVEL[minimo]:
        raise HTTPException(403, "Acesso não liberado à Consulta de Ativos.")
    return sd


# ── Nível efetivo ──────────────────────────────────────────────────
def nivel_efetivo(sd: dict) -> str:
    """Admin do portal → admin. Senão, a maior entre a liberação por login
    e a permissão `consulta_times` do portal."""
    if sd.get("is_admin"):
        return "admin"
    niveis = []
    n = dbct.nivel_do_login(sd.get("username", ""))
    if n:
        niveis.append(n)
    p = (sd.get("permission_map") or {}).get(MODULO, {})
    if p.get("can_admin"):
        niveis.append("admin")
    elif p.get("can_edit") or p.get("can_create"):
        niveis.append("edit")
    elif p.get("can_view"):
        niveis.append("view")
    return max(niveis, key=lambda x: _NIVEL.get(x, 0)) if niveis else ""


_NIVEL = {"view": 1, "edit": 2, "admin": 3}


@router.get("/consulta-times", response_class=HTMLResponse)
def pagina(req: Request):
    return _pagina(req)


@router.get("/consulta-times/", response_class=HTMLResponse)
def pagina_barra(req: Request):
    return _pagina(req)


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


# ── Espelho na porta 8502 ───────────────────────────────────────────────
# O aplicativo antigo atendia em :8502 e os times têm esse endereço salvo.
# Em vez de um segundo serviço (não há root para isso), o MESMO processo
# abre um segundo listener servindo só esta tela — o portal inteiro continua
# exclusivo da 8901. Falhar aqui (porta ocupada, por exemplo) nunca derruba
# o portal: o espelho é acessório.
_servidor_espelho = None


def criar_app_espelho():
    """App enxuto: só a tela Consulta de Ativos — Times e seus dois endpoints."""
    from fastapi import FastAPI
    from fastapi.responses import RedirectResponse
    from core.estatico import EstaticoLimpo
    from core.security import (
        BotProtectionMiddleware, MaxBodyMiddleware, SecurityHeadersMiddleware,
    )

    app = FastAPI(title="Consulta de Ativos — Times", docs_url=None,
                  redoc_url=None, openapi_url=None)
    app.add_middleware(MaxBodyMiddleware)
    app.add_middleware(BotProtectionMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # O espelho serve os MESMOS arquivos de /static; sem o limpador aqui,
    # o vazamento continuaria aberto pela porta antiga.
    app.mount("/static", EstaticoLimpo(directory=_cfg.STATIC), name="static")
    app.include_router(router)

    @app.get("/")
    def raiz():
        return RedirectResponse("/consulta-times", status_code=302)

    return app


async def iniciar_espelho() -> str:
    """Sobe o listener do espelho como tarefa do próprio processo."""
    global _servidor_espelho
    import asyncio

    import uvicorn

    porta = int(getattr(_cfg, "CONSULTA_TIMES_PORTA", 0) or 0)
    if porta <= 0 or _servidor_espelho is not None:
        return "desligado" if porta <= 0 else "já ligado"
    host = getattr(_cfg, "CONSULTA_TIMES_HOST", "") or _cfg.HOST

    # A porta é reservada AQUI, não pelo uvicorn: quando ele não consegue
    # abrir a porta, chama sys.exit(1) — e isso derrubaria o portal inteiro
    # por causa de um acessório (o aplicativo antigo ainda de pé, por exemplo).
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, porta))
        sock.listen(128)
        sock.set_inheritable(True)
    except OSError as exc:
        sock.close()
        _log.warning("Consulta Times NÃO espelhada em %s:%s (%s). O portal segue "
                     "normal na %s; pare quem ocupa a porta e reinicie.",
                     host, porta, exc, _cfg.PORT)
        return f"indisponível ({exc})"

    ssl_kwargs = {}
    if _cfg.SSL_CERTFILE and _cfg.SSL_KEYFILE:
        ssl_kwargs = {"ssl_certfile": _cfg.SSL_CERTFILE, "ssl_keyfile": _cfg.SSL_KEYFILE}
    config = uvicorn.Config(criar_app_espelho(), log_level="warning",
                            access_log=False, **ssl_kwargs)
    _servidor_espelho = uvicorn.Server(config)
    _servidor_espelho.install_signal_handlers = lambda: None  # o sinal é do portal

    async def _servir() -> None:
        try:
            await _servidor_espelho.serve(sockets=[sock])
        except BaseException as exc:  # noqa: BLE001 — inclusive SystemExit
            if isinstance(exc, asyncio.CancelledError):
                raise
            _log.error("espelho da Consulta Times parou: %s", exc)
        finally:
            try:
                sock.close()
            except OSError:
                pass

    asyncio.create_task(_servir())
    _log.info("Consulta Times espelhada em %s:%s", host, porta)
    return f"{host}:{porta}"


async def parar_espelho() -> None:
    global _servidor_espelho
    if _servidor_espelho is not None:
        _servidor_espelho.should_exit = True
        _servidor_espelho = None


# ── Liberações (admin do módulo) ───────────────────────────────────
@router.get("/api/consulta-times/eu")
def eu(req: Request):
    sd = get_session(req)
    return {"nivel": nivel_efetivo(sd), "login": sd.get("username", "")}


@router.get("/api/consulta-times/liberacoes")
def liberacoes(req: Request):
    _exigir(req, "admin")
    return {"liberacoes": dbct.listar(), "niveis": list(dbct.NIVEIS)}


@router.post("/api/consulta-times/liberacoes")
def liberar(body: LiberacaoIn, req: Request):
    sd = _exigir(req, "admin")
    check_rate_limit(req)
    try:
        r = dbct.liberar(body.login, body.nivel, body.nome, sd.get("username", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "liberar", f"{r['login']} → {r['nivel']}")
    return r


@router.delete("/api/consulta-times/liberacoes/{login}")
def revogar(login: str, req: Request):
    sd = _exigir(req, "admin")
    if not dbct.revogar(login):
        raise HTTPException(404, "Login não está na lista.")
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "revogar", login)
    return {"ok": True}


@router.get("/api/consulta-times/gestao-ativos")
def listas_ler(req: Request):
    """Listas do espaço Times, do banco DELE — o portal não entra aqui."""
    _exigir(req, "view")
    return dbct.ler_listas()


@router.put("/api/consulta-times/gestao-ativos")
def listas_gravar(body: ListasIn, req: Request):
    """Configuração exclusiva do espaço: gravar aqui não mexe no portal."""
    sd = _exigir(req, "admin")
    dbct.gravar_listas({"estoques": body.estoques, "corredores": body.corredores})
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "configurar", "listas do ServiceNow")
    return listas_ler(req)


@router.get("/api/consulta-times/stockrooms")
def stockrooms(req: Request):
    """Estoques do ServiceNow (alm_stockroom) para o espaço Times.

    Os estoques do SPARE ficam de fora: são da nossa área, e este espaço é
    dos outros times. O filtro é por nome, sem distinguir maiúsculas.
    """
    _exigir(req, "view")
    from routers.servicenow import _sn_session_from_portal, _sn_query_all
    sessao = _sn_session_from_portal(req)
    linhas = _sn_query_all(sessao, "alm_stockroom", "", "name", page_size=500, max_records=5000)
    nomes = sorted({(r.get("name") or "").strip() for r in linhas if (r.get("name") or "").strip()})
    fora = [n for n in nomes if "spare" in n.lower()]
    return {"estoques": [n for n in nomes if "spare" not in n.lower()],
            "excluidos": len(fora), "total": len(nomes)}


@router.get("/api/consulta-times/acessos")
def acessos(req: Request, limit: int = 300):
    _exigir(req, "admin")
    return {"acessos": dbct.listar_acessos(max(1, min(limit, 1000)))}
