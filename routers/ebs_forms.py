"""EBS Forms (RPA) — APIs de operação e administração.

Módulo de permissão `ebs_forms`: `view` acompanha execuções e capturas,
`create` dispara consultas, `admin` edita roteiros e roda o teste de abertura.

Toda rodada do robô é assíncrona: a chamada devolve o id da execução e o
acompanhamento é por `/execucoes/{id}` — abrir o Forms leva de 20 a 60 s e
ninguém quer uma requisição HTTP presa esse tempo.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

import db.ebs_forms as db
import integracoes.ebs_forms as forms
from config import get_settings
from core.security import check_rate_limit, get_session, require_permission

_log = logging.getLogger("ebs_forms")
_DIR = get_settings().STATIC / "ebs-forms"

router = APIRouter(prefix="/api/ebs-forms", tags=["EBS Forms"], include_in_schema=False)

MODULO = "ebs_forms"


def _exigir(req: Request, acao: str) -> dict:
    db.ensure_db()
    return require_permission(req, MODULO, acao)


def _quem(sess: dict) -> str:
    return str(sess.get("username") or sess.get("login") or sess.get("user") or "")


# ── status ──────────────────────────────────────────────────────────────
@router.get("/status")
def status(req: Request):
    _exigir(req, "view")
    d = forms.diagnostico()
    d.update(db.contagens())
    return d


@router.post("/compilar")
def compilar(req: Request):
    """Compila o lançador Java (quando o javac existe no servidor)."""
    _exigir(req, "admin")
    try:
        saida = forms.compilar()
    except forms.ErroForms as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "saida": saida, "compilado": forms.compilado()}


# ── execuções ───────────────────────────────────────────────────────────
def _rodar_em_segundo_plano(execucao_id: int, alvo, *args) -> None:
    def _registrar(msg: str) -> None:
        try:
            db.anexar_log(execucao_id, msg)
        except Exception:  # noqa: BLE001 — log nunca derruba a rodada
            pass

    def _corpo() -> None:
        try:
            r = alvo(_registrar, *args)
            db.concluir_execucao(execucao_id, True, r, capturas=r.get("capturas", []))
            if r.get("encontrado") and r.get("ativo"):
                db.salvar_ativo(r["criterio"], r.get("livro", ""), r["ativo"], execucao_id)
        except forms.ErroForms as exc:
            _registrar(f"ERRO: {exc}")
            db.concluir_execucao(execucao_id, False, erro=str(exc))
        except BaseException as exc:  # noqa: BLE001 — Java/JVM podem falhar de formas criativas
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _log.exception("execução %s falhou", execucao_id)
            _registrar(f"ERRO inesperado: {exc!r}")
            db.concluir_execucao(execucao_id, False, erro=repr(exc))

    threading.Thread(target=_corpo, name=f"ebs-forms-{execucao_id}", daemon=True).start()


class ConsultaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterio: str                      # número do ativo, etiqueta ou série
    livros: Optional[list[str]] = None
    forcar: bool = False               # ignora o que já foi coletado nas últimas 24 h
    detalhe: bool = False              # inclui a tela inteira (campo a campo) na resposta


@router.post("/testar-abertura")
def testar_abertura(req: Request):
    """Entra no EBS, abre o Forms, fotografa e fecha. Primeiro teste em servidor novo."""
    sess = _exigir(req, "admin")
    check_rate_limit(req, "api")
    roteiros = db.roteiros_ativos()
    eid = db.criar_execucao("teste_abertura", {}, _quem(sess))
    _rodar_em_segundo_plano(eid, forms.testar_abertura, roteiros.get("abrir"))
    return {"execucao_id": eid}


@router.post("/consultar")
def consultar(req: Request, body: ConsultaIn):
    sess = _exigir(req, "create")
    check_rate_limit(req, "api")
    criterio = body.criterio.strip()
    if not criterio or len(criterio) > 80:
        raise HTTPException(400, "Informe o número do ativo, a etiqueta ou o número de série.")
    if not body.forcar:
        pronto = db.buscar_ativo(criterio)
        if pronto:
            return {"execucao_id": None, "cache": True, **pronto}
    eid = db.criar_execucao("consulta", {"criterio": criterio, "livros": body.livros}, _quem(sess))
    _rodar_em_segundo_plano(eid, forms.consultar_ativo, criterio, db.roteiros_ativos(),
                            body.livros, body.detalhe)
    return {"execucao_id": eid, "cache": False}


@router.get("/execucoes")
def execucoes(req: Request, limite: int = 50):
    _exigir(req, "view")
    return db.listar_execucoes(max(1, min(limite, 500)))


@router.get("/execucoes/{execucao_id}")
def execucao(req: Request, execucao_id: int):
    _exigir(req, "view")
    e = db.obter_execucao(execucao_id)
    if not e:
        raise HTTPException(404, "Execução não encontrada.")
    return e


@router.get("/capturas/{nome}")
def captura(req: Request, nome: str):
    _exigir(req, "view")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", nome):
        raise HTTPException(400, "Nome inválido.")
    caminho = forms.DIR_CAPTURAS / nome
    if not caminho.is_file():
        raise HTTPException(404, "Captura não encontrada.")
    return FileResponse(caminho, media_type="image/png")


@router.get("/ativos/{criterio}")
def ativo(req: Request, criterio: str, validade_horas: int = 24 * 30):
    _exigir(req, "view")
    a = db.buscar_ativo(criterio, validade_horas)
    if not a:
        raise HTTPException(404, "Ativo ainda não coletado.")
    return a


# ── roteiros ────────────────────────────────────────────────────────────
class RoteiroIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    descricao: str = ""
    passos: list[dict[str, Any]]
    ativo: bool = True


_ACOES = {"esperar", "tecla", "texto", "digitar", "copiar", "foto", "arvore",
          "esperarate", "focarcampo", "focoatual", "lercampo", "clicar", "clicartexto", "menu", "dialogo", "grade", "dados", "se_vazio", "fim_se", "documento"}


@router.get("/roteiros")
def roteiros(req: Request):
    _exigir(req, "view")
    return {"roteiros": db.listar_roteiros(), "acoes": sorted(_ACOES),
            "variaveis": ["{criterio}", "{livro}"]}


@router.put("/roteiros/{nome}")
def salvar_roteiro(req: Request, nome: str, body: RoteiroIn):
    sess = _exigir(req, "admin")
    if not re.fullmatch(r"[a-z0-9_]{1,60}", nome):
        raise HTTPException(400, "Nome do roteiro: letras minúsculas, números e _.")
    for i, p in enumerate(body.passos, 1):
        if (p.get("acao") or "").lower() not in _ACOES:
            raise HTTPException(400, f"Passo {i}: ação '{p.get('acao')}' não existe.")
    db.salvar_roteiro(nome, body.descricao, body.passos, body.ativo, _quem(sess))
    return {"ok": True}


@router.post("/roteiros/{nome}/restaurar")
def restaurar_roteiro(req: Request, nome: str):
    sess = _exigir(req, "admin")
    if not db.restaurar_roteiro(nome, _quem(sess)):
        raise HTTPException(404, "Não há padrão para este roteiro.")
    return {"ok": True}


# ── página ──────────────────────────────────────────────────────────────
# Mesmo padrão de /controle-orcamento: o HTML é servido pelo router (exige
# sessão + permissão do módulo); css e js ficam públicos em static/ebs-forms
# porque não carregam dado nenhum.
@lru_cache
def _asset_version() -> str:
    h = hashlib.sha256()
    for nome in ("app.js", "app.css"):
        p = _DIR / nome
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _page() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{v}}", _asset_version()))


_SEM_PERMISSAO = """<!doctype html><meta charset="utf-8">
<title>EBS Forms (RPA)</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#1f2937;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.c{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:28px 32px;
max-width:460px;box-shadow:0 1px 3px rgba(0,0,0,.08)}h1{font-size:18px;margin:0 0 10px}
p{margin:0 0 8px;line-height:1.5}a{color:#2563eb}</style>
<div class="c"><h1>Acesso não liberado</h1>
<p>Seu usuário está autenticado, mas não tem permissão para o módulo
EBS Forms (RPA).</p><p>Solicite a liberação a um administrador do portal.</p>
<p><a href="/">Voltar ao portal</a></p></div>"""


def _acesso_pagina(req: Request):
    """Sessão + permissão de leitura. Sem sessão vai para o login do portal;
    com sessão e sem permissão, mostra a página de acesso não liberado."""
    sd = get_session(req, required=False)
    if not sd:
        return RedirectResponse("/", status_code=302)
    try:
        require_permission(req, MODULO, "view")
    except HTTPException:
        return HTMLResponse(_SEM_PERMISSAO, status_code=403)
    return _page()


# A página mora fora do prefixo /api/ebs-forms, por isso tem router próprio;
# main.py inclui os dois.
pagina_router = APIRouter(include_in_schema=False)


@pagina_router.get("/ebs-forms", response_class=HTMLResponse)
def pagina(req: Request):
    return _acesso_pagina(req)


@pagina_router.get("/ebs-forms/", response_class=HTMLResponse)
def pagina_slash(req: Request):
    return _acesso_pagina(req)
