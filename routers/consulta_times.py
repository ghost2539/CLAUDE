"""Consulta Times — o portal num espaço menor, em `/consulta-times`.

Era uma página à parte, pública dentro da rede. Agora é o mesmo portal
(mesma sessão, mesmas telas) aberto num espaço próprio: só Consulta e
as três telas do ServiceNow — Entrada, Saída e Movimentação interna —
com o usuário logado gravando no ServiceNow em nome próprio.

Quem entra: quem tem liberação por login nesta tela (dada por quem
administra o módulo) ou permissão `consulta_times` no portal. Quem
não tem, vê "acesso não liberado" — nunca a tela de outra área.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, field_validator

from config import get_settings
from core.security import get_session, check_rate_limit, client_ip
from db.portal import SessionLocal
import db.consulta_times as dbct
from routers.helpers import apply_class, xlsx_response

_cfg = get_settings()
_log = logging.getLogger("consulta_times")

router = APIRouter(tags=["Consulta Times"], include_in_schema=False)

MODULO = "consulta_times"
LIMITE_IDS = 1000
_NIVEL = {"view": 1, "edit": 2, "admin": 3}

_SEM_ACESSO = """<!doctype html><meta charset="utf-8"><title>Consulta de Ativos</title>
<style>body{font-family:Inter,Segoe UI,Arial,sans-serif;background:#090B0D;color:#E8E8E8;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.c{background:#111419;border:1px solid #232830;border-radius:3px;padding:28px 32px;max-width:460px}
h1{font-size:18px;margin:0 0 10px}p{margin:0 0 8px;line-height:1.5;color:#B5B5B5}a{color:#C79105}</style>
<div class="c"><h1>Acesso não liberado</h1>
<p>Seu usuário está autenticado, mas não foi liberado para a Consulta de Ativos.</p>
<p>Peça a liberação a um administrador da área.</p><p><a href="/">Voltar</a></p></div>"""


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


def _exigir(req: Request, minimo: str) -> dict:
    sd = get_session(req)
    n = nivel_efetivo(sd)
    if _NIVEL.get(n, 0) < _NIVEL[minimo]:
        raise HTTPException(403, "Acesso não liberado à Consulta de Ativos.")
    return sd


# ── Página ─────────────────────────────────────────────────────────
def _pagina() -> HTMLResponse:
    html = (_cfg.STATIC / "index.html").read_text(encoding="utf-8")
    # O mesmo shell do portal, marcado como espaço "times": o app.js
    # mostra só o menu deste espaço e cai na Consulta ao entrar.
    return HTMLResponse(html.replace("<body", '<body data-espaco="times"', 1))


@router.get("/consulta-times", response_class=HTMLResponse)
@router.get("/consulta-times/", response_class=HTMLResponse)
def pagina(req: Request):
    sd = get_session(req, required=False)
    if not sd:
        return RedirectResponse(f"/?next={quote('/consulta-times', safe='/')}", status_code=302)
    try:
        dbct.init_db()
    except Exception:  # noqa: BLE001
        pass
    if not nivel_efetivo(sd):
        dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "negado", "/consulta-times")
        return HTMLResponse(_SEM_ACESSO, status_code=403)
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "abrir", "/consulta-times")
    return _pagina()


# ── Consulta (a mesma do portal, sob a liberação deste espaço) ─────
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


def _consultar(ids: list[str], req: Request) -> dict:
    from routers.consulta import _query_assets, QueryIn
    res = _query_assets(QueryIn(identificadores=ids), req)
    with SessionLocal() as s:
        res["resultados"] = [apply_class(s, r) for r in res.get("resultados", [])]
    return res


@router.post("/api/consulta-times/consulta")
def consulta(body: ConsultaIn, req: Request):
    sd = _exigir(req, "view")
    check_rate_limit(req)
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "consulta", f"{len(body.identificadores)} id(s)")
    return _consultar(body.identificadores, req)


@router.post("/api/consulta-times/consulta/export")
def consulta_export(body: ConsultaIn, req: Request):
    sd = _exigir(req, "view")
    check_rate_limit(req)
    res = _consultar(body.identificadores, req)
    dbct.registrar_acesso(sd.get("username", ""), client_ip(req), "exportar", f"{len(body.identificadores)} id(s)")
    return xlsx_response(res.get("resultados", []), "consulta_ativos.xlsx")


# ── Liberações (admin do módulo) ───────────────────────────────────
@router.get("/api/consulta-times/eu")
def eu(req: Request):
    sd = get_session(req)
    return {"nivel": nivel_efetivo(sd), "login": sd.get("username", "")}


@router.get("/api/consulta-times/liberacoes")
def liberacoes(req: Request):
    _exigir(req, "admin")
    return {"liberacoes": dbct.listar(), "niveis": list(dbct.NIVEIS)}


class LiberacaoIn(BaseModel):
    login: str
    nivel: str = "view"
    nome: str = ""


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


# ── Listas das telas do ServiceNow, editáveis por quem administra o espaço ──
class ListasIn(BaseModel):
    estoques: list[str] = []
    corredores: list[str] = []


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
