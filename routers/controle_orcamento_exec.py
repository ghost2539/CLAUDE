"""Controle de Orçamento — Execução CAPEX.

Servido em ``/controle-orcamento`` (e ``/controle-orçamento``), com banco
PRÓPRIO e SEPARADO (``db/orcamento_exec.py``).

Barra de inclusão de projetos no topo (Número,
Tipo, Projeto/Demanda, Categoria, Área) e integração com a API de CAPEX do EBS
(``suporte.lojasrenner.com.br/ebs/api/capex/?projetos=...``), que preenche os
valores financeiros:

  saldo_inicial            → Orçamento Aprovado
  comprometido+reservados  → Comprometido
  realizado                → Realizado (Acum.)
  saldo_dia                → a_realizar (gravado, não exibido)
  (empresa, devolucoes, pct_exec, nome_projeto: NÃO são puxados)

"Em andamento" é digitado na tela e NÃO vem do EBS: é o que ainda não está
comprometido lá, mas já está em curso — uma PO aguardando aprovação, por
exemplo. Sincronizar com o EBS não o altera.

O saldo da tela é um só, "Disponível":

  Orçamento Aprovado − Comprometido − Em Andamento − Realizado

As três parcelas descontam dele. A coluna "A Realizar" saiu da tela; o
campo `a_realizar` continua recebendo o saldo do dia do EBS, mas não é
mostrado — dois saldos concorrentes confundiam a leitura.

ACESSO CONTROLADO: exige sessão do portal e permissão do módulo
``orcamento`` — liberada usuário a usuário em Parâmetros → Usuários e
Permissões. Leitura pede ``view``; inclusão, ``create``; alteração, exclusão
e sincronização, ``edit``; a trilha de acesso, ``admin``. Toda abertura de
tela e toda gravação ficam registradas em ``budget_acessos``, no banco do
próprio módulo. As gravações continuam passando pelo rate limit.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi import Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select

from config import get_settings
from db.orcamento_exec import (
    BudgetCategory, BudgetProject, SessionLocal, ensure_db, utcnow,
    listar_acessos, registrar_acesso,
    NIVEIS_MODULO, nivel_do_login, listar_permissoes_modulo,
    definir_permissao_modulo, remover_permissao_modulo,
    OpexItem,
    ler_cambio,
    gravar_cambio,
    MOEDAS_CAMBIO,
)
from core.prefixo import com_prefixo, destino, prefixo
from core.security import (
    check_rate_limit, client_ip, get_session,
)

_cfg = get_settings()
# FORA de static/, de propósito: enquanto o bundle morava lá, o mount
# público entregava 643 KB do módulo — todas as telas, rótulos e o caminho
# da API — a quem nunca digitou senha. A PÁGINA já exigia sessão; o bundle
# dela, não. Aqui a única porta são as rotas abaixo, e elas perguntam quem
# é antes de responder, com a MESMA regra da página.
_DIR = _cfg.STATIC.parent / "bundles" / "controle-orcamento-exec"
_log = logging.getLogger("controle_orcamento_exec")

router = APIRouter(tags=["Controle de Orçamento (Execução)"], include_in_schema=False)

TIPOS = ("CAPEX", "OPEX")
ESTAGIOS = ("Planejamento", "Aprovação", "Em Execução", "Concluído")
PRIORIDADES = ("Alta", "Média", "Baixa")
_MAX_VALOR = Decimal("9999999999999.99")

# ── Config da API de CAPEX do EBS (com padrões; ajustáveis por env) ──
EBS_CAPEX_URL = getattr(_cfg, "EBS_CAPEX_URL", "") or "https://suporte.lojasrenner.com.br/ebs/api/capex/"
EBS_CAPEX_PROXY = getattr(_cfg, "EBS_CAPEX_PROXY", "") or ""
EBS_CAPEX_TIMEOUT = int(getattr(_cfg, "EBS_CAPEX_TIMEOUT", 0) or 30)
EBS_CAPEX_VERIFY = bool(getattr(_cfg, "EBS_CAPEX_VERIFY", False))
EBS_CAPEX_USER = getattr(_cfg, "EBS_CAPEX_USER", "") or ""
EBS_CAPEX_PASS = getattr(_cfg, "EBS_CAPEX_PASS", "") or ""
EBS_CAPEX_TOKEN = getattr(_cfg, "EBS_CAPEX_TOKEN", "") or ""
EBS_CAPEX_TOKEN_SCHEME = getattr(_cfg, "EBS_CAPEX_TOKEN_SCHEME", "") or "Bearer"
EBS_CAPEX_AUTH_HEADER = getattr(_cfg, "EBS_CAPEX_AUTH_HEADER", "") or "Authorization"
EBS_CAPEX_ARS_BRL = float(getattr(_cfg, "EBS_CAPEX_ARS_BRL", 0) or 0)
EBS_CAPEX_UYU_BRL = float(getattr(_cfg, "EBS_CAPEX_UYU_BRL", 0) or 0)
EBS_CAPEX_FX_URL = getattr(_cfg, "EBS_CAPEX_FX_URL", "") or ""
EBS_CAPEX_FX_PROXY = getattr(_cfg, "EBS_CAPEX_FX_PROXY", "") or ""

# Paleta para categorias criadas pela barra de inclusão (evita tudo cinza).
_PALETA_CAT = [
    "#2563eb", "#f97316", "#8b5cf6", "#22c55e", "#0ea5e9", "#ec4899",
    "#14b8a6", "#eab308", "#ef4444", "#6366f1", "#84cc16", "#a16207",
]

# Cache simples de cotação (BRL por 1 peso) por processo.
_fx_cache: dict = {"rates": None, "expira": 0.0}


# ── Página ────────────────────────────────────────────────────────
@lru_cache
def _asset_version() -> str:
    h = hashlib.sha256()
    for name in ("app.js", "app.css"):
        p = _DIR / name
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _page(req: Request | None = None) -> HTMLResponse:
    """A página inteira, com o prefixo do proxy quando houver.

    A tela é servida daqui, e não pelo index do portal, então o prefixo
    precisa ser aplicado aqui também — senão, atrás do proxy num
    subcaminho, ela busca CSS e JS na raiz do domínio e toma 404.
    """
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("{{v}}", _asset_version())
    return HTMLResponse(com_prefixo(html, prefixo(req)))


# ── Controle de acesso ────────────────────────────────────────────
# A tela tem permissão PRÓPRIA (módulo "orcamento"): quem não foi liberado
# não vê a informação, mesmo tendo acesso ao resto do portal.
MODULO = "orcamento"

_SEM_PERMISSAO = """<!doctype html><meta charset="utf-8">
<title>Controle de Orçamento</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f8fafc;color:#1f2937;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}
.c{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:28px 32px;
max-width:460px;box-shadow:0 1px 3px rgba(0,0,0,.08)}h1{font-size:18px;margin:0 0 10px}
p{margin:0 0 8px;line-height:1.5}a{color:#2563eb}</style>
<div class="c"><h1>Acesso não liberado</h1>
<p>Seu usuário está autenticado, mas não tem permissão para o Controle de
Orçamento.</p><p>Solicite a liberação a um administrador do portal.</p>
<p><a href="__BASE__/">Voltar ao portal</a></p></div>"""


def _acesso_pagina(req: Request):
    """Sessão + permissão de leitura. Sem sessão vai para o login do portal;
    com sessão e sem permissão, mostra a página de acesso não liberado."""
    sd = get_session(req, required=False)
    if not sd:
        # Leva o destino junto: depois do login o portal volta para cá, em vez
        # de largar quem digitou o endereço na tela de Bem-vindo.
        return RedirectResponse(
            f"{prefixo(req)}/?next={quote(destino(req), safe='/')}",
            status_code=302)
    try:
        ensure_db()   # a trilha de acesso vive no banco do módulo
    except Exception:  # noqa: BLE001 — banco fora não impede abrir a tela
        pass
    if not _pode(sd, "view"):
        registrar_acesso(sd.get("username", ""), client_ip(req), "negado",
                         "sem acesso liberado ao módulo")
        return HTMLResponse(_SEM_PERMISSAO.replace("__BASE__", prefixo(req)), status_code=403)
    registrar_acesso(sd.get("username", ""), client_ip(req), "abrir", "/controle-orcamento")
    return _page(req)


# Ações da API mapeadas ao nível mínimo próprio do módulo.
_NIVEL_ORDEM = {"view": 1, "edit": 2, "admin": 3}
_ACAO_NIVEL = {"view": "view", "export": "view",
               "create": "edit", "edit": "edit", "admin": "admin"}


def _admin_portal(sd: dict) -> bool:
    """ADMIN do módulo vem do portal (Parâmetros): is_admin ou can_admin em
    'orcamento'. É quem pode liberar acessos deste módulo."""
    if sd.get("is_admin"):
        return True
    return bool((sd.get("permission_map") or {}).get(MODULO, {}).get("can_admin"))


def _nivel_efetivo(sd: dict) -> str:
    """Maior nível do usuário: admin do portal → admin; senão a liberação
    própria do módulo (por login); e, por compatibilidade, a permissão antiga
    do portal para 'orcamento' — assim ninguém perde acesso na migração."""
    if _admin_portal(sd):
        return "admin"
    niveis = []
    n = nivel_do_login(sd.get("username", ""))
    if n:
        niveis.append(n)
    perms = (sd.get("permission_map") or {}).get(MODULO, {})
    if perms.get("can_edit"):
        niveis.append("edit")
    elif perms.get("can_view"):
        niveis.append("view")
    return max(niveis, key=lambda x: _NIVEL_ORDEM.get(x, 0)) if niveis else ""


def _pode(sd: dict, acao: str) -> bool:
    alvo = _ACAO_NIVEL.get(acao, "view")
    return _NIVEL_ORDEM.get(_nivel_efetivo(sd), 0) >= _NIVEL_ORDEM[alvo]


def _exigir(req: Request, acao: str, registro: str = "", detalhe: str = "") -> dict:
    """Permissão PRÓPRIA do módulo (não a grade do portal) + trilha de acesso.
    O login continua vindo do SSO; aqui só decidimos o que ele pode fazer."""
    sd = get_session(req)  # exige sessão (login via SSO)
    if not _pode(sd, acao):
        raise HTTPException(403, "Permissão insuficiente para este módulo.")
    if registro:
        registrar_acesso(sd.get("username", ""), client_ip(req), registro, detalhe)
    return sd


# Caminho canônico da tela. Renomeada para -InfraCSC (Infra CSC) para
# distinguir das demais telas de orçamento. Os caminhos antigos continuam
# respondendo, redirecionando para cá, para não quebrar link já distribuído.
@router.get("/controle-orcamento-InfraCSC", response_class=HTMLResponse)
def pagina_infracsc(req: Request):
    return _acesso_pagina(req)


@router.get("/controle-orcamento-InfraCSC/", response_class=HTMLResponse)
def pagina_infracsc_slash(req: Request):
    return _acesso_pagina(req)


# ── O bundle da tela, com a MESMA permissão dela ──────────────────
# Servido por rota, e não pelo mount de /static, porque lá é público.
# `_exigir(req, "view")` é a mesma porta da página: quem não pode ver a
# tela não recebe o código dela.
_TIPOS_BUNDLE = {"app.js": "application/javascript; charset=utf-8",
                 "app.css": "text/css; charset=utf-8"}


@router.get("/controle-orcamento-exec/{arquivo}")
def bundle(arquivo: str, req: Request):
    """Entrega app.js/app.css a quem tem sessão E permissão de ver a tela.

    Lista fixa de nomes, e não caminho vindo da URL: com `arquivo` virando
    caminho, `..%2f` passearia pelo disco. Aqui um nome fora da lista nem
    chega a virar arquivo.

    A autorização vem ANTES de dizer se o arquivo existe — responder 404 só
    a quem passou na permissão evita usar esta rota para descobrir o que o
    módulo tem.
    """
    _exigir(req, "view")
    tipo = _TIPOS_BUNDLE.get(arquivo)
    if not tipo:
        raise HTTPException(404, "Arquivo não encontrado.")
    caminho = _DIR / arquivo
    if not caminho.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    st = caminho.stat()
    etag = f'"{int(st.st_mtime)}-{st.st_size}-co"'
    if req.headers.get("if-none-match") == etag:
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(
        caminho.read_bytes(), media_type=tipo,
        # Vary: Cookie — o bundle é de quem pediu; cache compartilhado de
        # proxy não pode guardar a resposta de um e servir a outro.
        headers={"ETag": etag, "Cache-Control": "no-cache", "Vary": "Cookie"},
    )


def _redir_canonico(req: Request) -> RedirectResponse:
    return RedirectResponse(f"{prefixo(req)}/controle-orcamento-InfraCSC",
                            status_code=308)


@router.get("/controle-orcamento", response_class=HTMLResponse)
def pagina(req: Request):
    return _redir_canonico(req)


@router.get("/controle-orcamento/", response_class=HTMLResponse)
def pagina_slash(req: Request):
    return _redir_canonico(req)


@router.get("/controle-orçamento", response_class=HTMLResponse)
def pagina_acento(req: Request):
    return _redir_canonico(req)


# ── Validação ─────────────────────────────────────────────────────
def _texto(v: Any, limite: int) -> str:
    return str(v if v is not None else "").strip()[:limite]


def _valor(v: Any) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Valor numérico inválido.")
    if d < 0:
        raise ValueError("Valores não podem ser negativos.")
    if d > _MAX_VALOR:
        raise ValueError("Valor acima do limite permitido.")
    return d


def _valor_signed(v: Any) -> Decimal:
    """Como _valor, mas aceita negativo (ex.: saldo_dia)."""
    if v is None or v == "":
        return Decimal("0")
    try:
        d = Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    if d > _MAX_VALOR:
        d = _MAX_VALOR
    if d < -_MAX_VALOR:
        d = -_MAX_VALOR
    return d


def _opcao(v: Any, opcoes: tuple[str, ...], rotulo: str) -> str:
    v = _texto(v, 40)
    if v not in opcoes:
        raise ValueError(f"{rotulo} inválido(a): {v!r}.")
    return v


def _data(v: Any) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise ValueError("Data inválida (use AAAA-MM-DD).")


class ProjetoIn(BaseModel):
    """Campos editáveis pela tabela.

    `a_realizar` NÃO entra aqui: vem do EBS. `em_andamento` entra — é
    justamente o que o EBS não tem, o que ainda não está comprometido mas já
    está em curso (uma PO aguardando aprovação, por exemplo).
    """
    model_config = ConfigDict(extra="forbid")

    codigo: Optional[str] = None
    nome: Optional[str] = None
    tipo: Optional[str] = None
    categoria: Optional[str] = None
    area: Optional[str] = None
    estagio: Optional[str] = None
    prioridade: Optional[str] = None
    orcamento: Optional[Decimal] = None
    comprometido: Optional[Decimal] = None
    realizado: Optional[Decimal] = None
    em_andamento: Optional[Decimal] = None
    bloqueado: Optional[bool] = None
    vencimento: Optional[date] = None

    @field_validator("codigo", mode="before")
    @classmethod
    def _v_codigo(cls, v):
        return None if v is None else _texto(v, 40)

    @field_validator("nome", mode="before")
    @classmethod
    def _v_nome(cls, v):
        return None if v is None else _texto(v, 300)

    @field_validator("area", mode="before")
    @classmethod
    def _v_area(cls, v):
        return None if v is None else _texto(v, 120)

    @field_validator("tipo", mode="before")
    @classmethod
    def _v_tipo(cls, v):
        return None if v is None else _opcao(v, TIPOS, "Tipo")

    @field_validator("categoria", mode="before")
    @classmethod
    def _v_categoria(cls, v):
        return None if v is None else _texto(v, 60)

    @field_validator("estagio", mode="before")
    @classmethod
    def _v_estagio(cls, v):
        return None if v is None else _opcao(v, ESTAGIOS, "Estágio")

    @field_validator("prioridade", mode="before")
    @classmethod
    def _v_prioridade(cls, v):
        return None if v is None else _opcao(v, PRIORIDADES, "Prioridade")

    @field_validator("orcamento", "comprometido", "realizado", "em_andamento",
                     mode="before")
    @classmethod
    def _v_valor(cls, v):
        return None if v is None else _valor(v)

    @field_validator("vencimento", mode="before")
    @classmethod
    def _v_vencimento(cls, v):
        return _data(v)


class IncluirIn(BaseModel):
    """Barra de inclusão: Número (puxa do EBS), Tipo, Projeto/Demanda,
    Categoria, Área. O nome (Projeto/Demanda) é MANUAL — não vem do EBS."""
    model_config = ConfigDict(extra="forbid")
    numero: str
    tipo: str = "CAPEX"
    projeto_demanda: str = ""
    categoria: str = ""
    area: str = ""

    @field_validator("numero", mode="before")
    @classmethod
    def _v_numero(cls, v):
        v = _texto(v, 200)
        if not v:
            raise ValueError("Informe o número do projeto.")
        return v

    @field_validator("tipo", mode="before")
    @classmethod
    def _v_tipo(cls, v):
        return _opcao(v or "CAPEX", TIPOS, "Tipo")

    @field_validator("projeto_demanda", mode="before")
    @classmethod
    def _v_dem(cls, v):
        return _texto(v, 300)

    @field_validator("categoria", mode="before")
    @classmethod
    def _v_cat(cls, v):
        return _texto(v, 60)

    @field_validator("area", mode="before")
    @classmethod
    def _v_area(cls, v):
        return _texto(v, 120)


_CAMPOS = {
    "codigo": "code", "nome": "name", "tipo": "kind", "categoria": "category",
    "area": "area", "estagio": "stage", "prioridade": "priority",
    "orcamento": "approved_budget", "comprometido": "committed",
    "realizado": "realized", "bloqueado": "locked", "vencimento": "due_date",
    "em_andamento": "em_andamento",
}

_PADRAO = {
    "codigo": "", "nome": "Novo projeto", "tipo": "CAPEX", "categoria": "Outros",
    "area": "", "estagio": "Planejamento", "prioridade": "Média",
    "orcamento": Decimal("0"), "comprometido": Decimal("0"),
    "realizado": Decimal("0"), "em_andamento": Decimal("0"),
    "bloqueado": False, "vencimento": None,
}


def _dict(p: BudgetProject) -> dict:
    return {
        "id": p.id,
        "codigo": p.code,
        "nome": p.name,
        "tipo": p.kind,
        "categoria": p.category,
        "area": p.area,
        "estagio": p.stage,
        "prioridade": p.priority,
        "orcamento": float(p.approved_budget or 0),
        "comprometido": float(p.committed or 0),
        "realizado": float(p.realized or 0),
        "a_realizar": float(p.a_realizar or 0),
        "em_andamento": float(p.em_andamento or 0),
        "bloqueado": bool(p.locked),
        "vencimento": p.due_date.isoformat() if p.due_date else "",
        "ordem": p.sort_order,
        "sincronizado_em": p.synced_at.isoformat() if p.synced_at else None,
        "atualizado_em": p.updated_at.isoformat() if p.updated_at else None,
        "atualizado_por": p.updated_by,
    }


def _categoria_existe(s, nome: str) -> bool:
    return bool(s.scalar(select(BudgetCategory.id).where(BudgetCategory.name == nome)))


def _garantir_categoria(s, nome: str) -> None:
    """Cria a categoria se ainda não existir (a barra de inclusão é rápida),
    já atribuindo uma cor da paleta (evita categorias todas cinza no gráfico)."""
    nome = (nome or "").strip()
    if not nome or _categoria_existe(s, nome):
        return
    total = s.scalar(select(func.count()).select_from(BudgetCategory)) or 0
    proximo = (s.scalar(select(func.max(BudgetCategory.sort_order))) or 0) + 1
    cor = _PALETA_CAT[total % len(_PALETA_CAT)]
    s.add(BudgetCategory(name=nome[:60], color=cor, sort_order=proximo))


def _checar_categoria(s, dados: dict) -> None:
    if "categoria" in dados and dados["categoria"] is not None:
        if not dados["categoria"]:
            raise HTTPException(422, "Categoria obrigatória.")
        if not _categoria_existe(s, dados["categoria"]):
            raise HTTPException(422, f"Categoria inexistente: {dados['categoria']!r}. Cadastre-a em Categorias.")


def _cat_dict(c: BudgetCategory) -> dict:
    return {"id": c.id, "nome": c.name, "cor": c.color, "ordem": c.sort_order}


def _listar_categorias(s) -> list[dict]:
    rows = s.scalars(select(BudgetCategory).order_by(BudgetCategory.sort_order, BudgetCategory.id)).all()
    return [_cat_dict(c) for c in rows]


_COR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class CategoriaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nome: Optional[str] = None
    cor: Optional[str] = None

    @field_validator("nome", mode="before")
    @classmethod
    def _v_nome(cls, v):
        if v is None:
            return None
        v = _texto(v, 60)
        if not v:
            raise ValueError("Nome da categoria obrigatório.")
        return v

    @field_validator("cor", mode="before")
    @classmethod
    def _v_cor(cls, v):
        if v is None:
            return None
        v = _texto(v, 9).lower()
        if not _COR_RE.match(v):
            raise ValueError("Cor inválida (use #rrggbb).")
        return v


def _autor(req: Request) -> str:
    sd = get_session(req, required=False)
    if sd and sd.get("username"):
        return str(sd["username"])[:80]
    return f"publico@{client_ip(req)}"[:80]


def _ordem_projetos():
    return select(BudgetProject).order_by(BudgetProject.sort_order, BudgetProject.id)


def _com_banco(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            ensure_db()
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            _log.error("Banco do Controle de Orçamento (Execução) indisponível: %s", exc, exc_info=True)
            raise HTTPException(503, "Banco de dados do módulo indisponível. Tente novamente mais tarde.")
    return wrapper


# ── Integração EBS (API de CAPEX) ─────────────────────────────────
def _ebs_capex(numeros: list[str]) -> dict[str, dict]:
    """Consulta a API de CAPEX do EBS e devolve {numero: linha_json}.

    Levanta ValueError com mensagem amigável em caso de falha de rede/HTTP.
    """
    numeros = [n.strip() for n in numeros if n and n.strip()]
    if not numeros:
        return {}
    try:
        import requests as _req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        raise ValueError("Pacote 'requests' não instalado no servidor.")

    params = {"projetos": ",".join(numeros)}
    proxies = {"http": EBS_CAPEX_PROXY, "https": EBS_CAPEX_PROXY} if EBS_CAPEX_PROXY else None
    base_headers = {"Accept": "application/json"}

    def _get(session, headers=None, auth=None):
        return session.get(
            EBS_CAPEX_URL, params=params, timeout=EBS_CAPEX_TIMEOUT,
            verify=EBS_CAPEX_VERIFY, proxies=proxies,
            headers=headers or base_headers, auth=auth,
        )

    r = None
    ultimo_erro = None

    # 1) MESMA autenticação do consulta-times: login no EBS (cookies/token) e
    #    reuso da sessão. Re-autentica uma vez se a sessão expirou (401/403).
    try:
        from routers.public_assets import _auth as _ct_auth
        import integracoes.ebs_service as ebs_service
        auth = _ct_auth()
        r = _get(ebs_service._build_session(auth))
        if r.status_code in (401, 403):
            r = _get(ebs_service._build_session(_ct_auth(force=True)))
    except Exception as exc:  # noqa: BLE001 — segue para credencial própria/fallback
        ultimo_erro = exc
        r = None

    # 2) Fallback: credencial própria da API (Basic ou token), se configurada,
    #    ou requisição simples.
    if r is None or r.status_code in (401, 403):
        try:
            with _req.Session() as session:
                headers = dict(base_headers)
                auth = None
                if EBS_CAPEX_TOKEN:
                    headers[EBS_CAPEX_AUTH_HEADER] = f"{EBS_CAPEX_TOKEN_SCHEME} {EBS_CAPEX_TOKEN}".strip()
                elif EBS_CAPEX_USER:
                    auth = (EBS_CAPEX_USER, EBS_CAPEX_PASS)
                r2 = _get(session, headers, auth)
            # usa o fallback só se ele for melhor que o resultado anterior
            if r is None or r2.status_code == 200:
                r = r2
        except Exception as exc:  # noqa: BLE001
            if r is None:
                raise ValueError(f"Falha ao acessar a API de CAPEX: {exc}")

    if r is None:
        raise ValueError(f"Falha ao acessar a API de CAPEX: {ultimo_erro}")
    if r.status_code in (401, 403):
        www = r.headers.get("WWW-Authenticate", "")
        raise ValueError(
            f"API de CAPEX retornou HTTP {r.status_code} (não autorizado)."
            + (f" Esquema exigido: {www}." if www else "")
            + " Confirme se as credenciais de acesso ao EBS (as mesmas do consulta-times) estão configuradas."
        )
    if r.status_code != 200:
        raise ValueError(f"API de CAPEX retornou HTTP {r.status_code}.")
    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        raise ValueError("API de CAPEX retornou resposta não-JSON.")
    out: dict[str, dict] = {}
    for linha in (data.get("projetos") or []):
        chave = str(linha.get("nro_projeto") or "").strip()
        if chave:
            out[chave] = linha
    return out


def _moeda_empresa(empresa: str) -> Optional[str]:
    """Retorna a moeda estrangeira a converter conforme a empresa/país."""
    e = (empresa or "").strip().upper()
    if "ARGENTIN" in e:
        return "ARS"
    if "URUGUAI" in e or "URUGUAY" in e:
        return "UYU"
    return None


def _fx_rates() -> dict:
    """Cotação em REAIS por 1 peso: {'ARS': x, 'UYU': y}.

    Ordem: taxa informada na TELA (Configurações), depois variável de
    ambiente, depois cotação ao vivo (cache 1h). A tela vem primeiro porque é
    a única que quem opera consegue mudar sem deploy — e era o que faltava:
    com a variável zerada e a URL de cotação fora do alcance da rede interna,
    nada convertia e o valor ficava no peso."""
    import time
    rates = {"ARS": (EBS_CAPEX_ARS_BRL or None), "UYU": (EBS_CAPEX_UYU_BRL or None)}
    try:
        for moeda, dados in ler_cambio().items():
            if dados.get("valor"):
                rates[moeda] = float(dados["valor"])
    except Exception as exc:  # noqa: BLE001 — sem banco, segue com env/ao vivo
        _log.info("Câmbio informado na tela indisponível: %s", exc)
    if rates["ARS"] and rates["UYU"]:
        return rates
    now = time.time()
    live = _fx_cache["rates"] if (_fx_cache["rates"] and _fx_cache["expira"] > now) else None
    if live is None and EBS_CAPEX_FX_URL:
        live = {}
        try:
            import requests as _req
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            proxies = {"http": EBS_CAPEX_FX_PROXY, "https": EBS_CAPEX_FX_PROXY} if EBS_CAPEX_FX_PROXY else None
            r = _req.get(EBS_CAPEX_FX_URL, timeout=10, verify=False, proxies=proxies)
            if r.status_code == 200:
                d = r.json()
                for chave, moeda in (("ARSBRL", "ARS"), ("UYUBRL", "UYU")):
                    bid = (d.get(chave) or {}).get("bid")
                    if bid:
                        live[moeda] = float(bid)
                _fx_cache["rates"] = live
                _fx_cache["expira"] = now + 3600
        except Exception as exc:  # noqa: BLE001 — câmbio ao vivo é best-effort
            _log.info("Cotação ao vivo indisponível: %s", exc)
            live = {}
    live = live or {}
    for m in ("ARS", "UYU"):
        if not rates[m]:
            rates[m] = live.get(m)
    return rates


def _reportado(v: Any) -> bool:
    """True só quando o EBS realmente MANDOU um valor para o campo. Ausência
    (chave faltando), None ou "" NÃO contam — assim uma resposta do EBS sem os
    financeiros não zera o que já está gravado."""
    return v is not None and v != ""


def _aplicar_ebs(p: BudgetProject, linha: dict, rates: Optional[dict] = None) -> tuple[str, bool]:
    """Preenche os campos financeiros do projeto a partir da linha do EBS,
    convertendo ARS/UYU→BRL quando a empresa for Argentina/Uruguai.

    SÓ sobrescreve um campo quando o EBS de fato reportou aquele valor. Se a
    linha vier sem os financeiros (ex.: EBS fora, resposta incompleta), NADA é
    alterado — nunca zera o que o usuário já tem. NÃO altera nome/tipo/
    categoria/área. Retorna (aviso, aplicou_algo)."""
    if rates is None:
        rates = _fx_rates()
    moeda = _moeda_empresa(linha.get("empresa"))
    fator = Decimal("1")
    aviso = ""
    if moeda:
        taxa = rates.get(moeda)
        if taxa and taxa > 0:
            fator = Decimal(str(taxa))
        else:
            aviso = f"{linha.get('nro_projeto')}: sem cotação {moeda} — valor mantido na moeda original"

    def conv(v: Decimal) -> Decimal:
        return (v * fator).quantize(Decimal("0.01"))

    aplicou = False
    if _reportado(linha.get("saldo_inicial")):
        p.approved_budget = conv(_valor(linha.get("saldo_inicial")))
        aplicou = True
    if _reportado(linha.get("comprometido")) or _reportado(linha.get("reservados")):
        p.committed = conv(_valor(linha.get("comprometido")) + _valor(linha.get("reservados")))
        aplicou = True
    if _reportado(linha.get("realizado")):
        p.realized = conv(_valor(linha.get("realizado")))
        aplicou = True
    if _reportado(linha.get("saldo_dia")):
        p.a_realizar = conv(_valor_signed(linha.get("saldo_dia")))
        aplicou = True
    if aplicou:
        p.synced_at = utcnow()
    return aviso, aplicou


# ── API ───────────────────────────────────────────────────────────
@router.get("/api/controle-orcamento-exec/sessao")
def sessao(req: Request):
    sd = get_session(req, required=False)
    if not sd:
        return {"usuario": None}
    return {
        "usuario": {"username": sd.get("username"), "display_name": sd.get("display_name")},
        "nivel": _nivel_efetivo(sd),          # view | edit | admin | ""
        "admin_modulo": _pode(sd, "admin"),   # admin do módulo OU do portal libera acessos
    }


@router.get("/api/controle-orcamento-exec/acessos")
@_com_banco
def acessos(req: Request, limit: int = 300, usuario: str = "", acao: str = ""):
    """Trilha de acesso da tela — quem abriu e quem alterou o quê."""
    _exigir(req, "admin")
    return {"acessos": listar_acessos(limit=limit, usuario=usuario, acao=acao)}


# ── Liberações de acesso PRÓPRIAS do módulo ─────────────────────────────
class PermissaoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    login: str
    nivel: str = "view"
    nome: str = ""


def _exigir_admin_modulo(req: Request) -> dict:
    """Gerencia as liberações do módulo. Vale para o ADMIN DO PRÓPRIO MÓDULO
    (nível 'admin' liberado aqui, na tela do módulo) — não depende da grade de
    acessos do portal. O admin do portal (is_admin/can_admin) também passa, o
    que serve para nomear o primeiro admin do módulo."""
    sd = get_session(req)
    if not _pode(sd, "admin"):
        raise HTTPException(403, "Apenas o administrador do módulo pode liberar acessos.")
    return sd


@router.get("/api/controle-orcamento-exec/permissoes")
@_com_banco
def listar_permissoes(req: Request):
    _exigir_admin_modulo(req)
    return {"permissoes": listar_permissoes_modulo(), "niveis": list(NIVEIS_MODULO)}


@router.post("/api/controle-orcamento-exec/permissoes", status_code=201)
@_com_banco
def salvar_permissao(body: PermissaoIn, req: Request):
    sd = _exigir_admin_modulo(req)
    check_rate_limit(req, "api")
    try:
        p = definir_permissao_modulo(body.login, body.nivel, body.nome, sd.get("username", ""))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    registrar_acesso(sd.get("username", ""), client_ip(req), "liberar",
                     f"{p['login']} → {p['nivel']}")
    return p


@router.delete("/api/controle-orcamento-exec/permissoes/{login}")
@_com_banco
def excluir_permissao(login: str, req: Request):
    sd = _exigir_admin_modulo(req)
    if not remover_permissao_modulo(login):
        raise HTTPException(404, "Liberação não encontrada.")
    registrar_acesso(sd.get("username", ""), client_ip(req), "revogar", login)
    return {"ok": True}


# ── Câmbio informado na tela ─────────────────────────────────────────────
class CambioIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ARS: Optional[float] = None
    UYU: Optional[float] = None


@router.get("/api/controle-orcamento-exec/cambio")
@_com_banco
def cambio_ler(req: Request):
    """O que está gravado, mais a taxa que está VALENDO e de onde ela vem.

    A tela precisa das duas coisas: mostrar o campo preenchido e explicar por
    que um projeto da Argentina não converteu."""
    _exigir(req, "view")
    gravado = ler_cambio()
    valendo = _fx_rates()
    ambiente = {"ARS": EBS_CAPEX_ARS_BRL or 0.0, "UYU": EBS_CAPEX_UYU_BRL or 0.0}
    saida = {}
    for moeda in MOEDAS_CAMBIO:
        taxa = valendo.get(moeda)
        if gravado[moeda]["valor"]:
            fonte = "tela"
        elif ambiente[moeda]:
            fonte = "ambiente"
        elif taxa:
            fonte = "cotação ao vivo"
        else:
            fonte = "nenhuma"
        saida[moeda] = {**gravado[moeda], "em_uso": taxa or 0.0, "fonte": fonte}
    return {"cambio": saida, "moedas": list(MOEDAS_CAMBIO)}


@router.put("/api/controle-orcamento-exec/cambio")
@_com_banco
def cambio_gravar(body: CambioIn, req: Request):
    """Grava a taxa em reais por 1 peso. Zero ou vazio APAGA a taxa da moeda,
    que é como se pede para voltar ao ambiente / à cotação ao vivo."""
    sd = _exigir(req, "admin", "cambio", "taxa de câmbio informada na tela")
    dados = body.model_dump(exclude_unset=True)
    informados = {m: v for m, v in dados.items() if m in MOEDAS_CAMBIO}
    if not informados:
        raise HTTPException(422, "Informe ARS e/ou UYU.")
    for moeda, valor in informados.items():
        if valor is not None and float(valor) > 1000:
            # 1 peso valendo mais de mil reais é dígito trocado, não cotação.
            raise HTTPException(422, f"Taxa de {moeda} fora do esperado: {valor}.")
    gravar_cambio(informados, sd.get("username", ""))
    return cambio_ler(req)


# ── OPEX (incluído manualmente, sem EBS; cada país na sua moeda) ─────────
OPEX_PAISES = ("BR", "AR", "UY")
OPEX_MOEDA = {"BR": "BRL", "AR": "ARS", "UY": "UYU"}
OPEX_REGIAO = {"BR": "BR", "AR": "LATAM", "UY": "LATAM"}


def _opex_valida_pais(pais: str) -> str:
    pais = (pais or "").strip().upper()
    if pais not in OPEX_PAISES:
        raise HTTPException(422, "País inválido (use BR, AR ou UY).")
    return pais


def _opex_meses(bruto) -> dict:
    """Normaliza o mapa de meses {1..12: valor} para JSON, ignorando o resto."""
    out = {}
    if isinstance(bruto, dict):
        for k, v in bruto.items():
            try:
                m = int(str(k).strip())
                if 1 <= m <= 12:
                    out[str(m)] = round(float(v or 0), 2)
            except (ValueError, TypeError):
                continue
    return out


class OpexItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    regiao: Optional[str] = None
    pais: str = "BR"
    ano: Optional[int] = None
    bu: str = ""
    fornecedor: str = ""
    conta_contabil: str = ""
    conta_descricao: str = ""
    tipo_despesa: str = ""
    orcado_meses: Optional[dict] = None
    realizado_meses: Optional[dict] = None


class OpexItemPatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    pais: Optional[str] = None
    ano: Optional[int] = None
    bu: Optional[str] = None
    fornecedor: Optional[str] = None
    conta_contabil: Optional[str] = None
    conta_descricao: Optional[str] = None
    tipo_despesa: Optional[str] = None
    orcado_meses: Optional[dict] = None
    realizado_meses: Optional[dict] = None


def _opex_resumo(s, ano: int) -> dict:
    """Orçado e realizado por país (moeda local, sem conversão) + alerta.
    Orçado e realizado saem das séries mensais das próprias linhas."""
    from datetime import date as _date
    itens = s.scalars(select(OpexItem).where(OpexItem.ano == ano)).all()
    orcados = {p: 0.0 for p in OPEX_PAISES}
    realizado = {p: 0.0 for p in OPEX_PAISES}
    for it in itens:
        d = it.to_dict()
        if it.pais in realizado:
            orcados[it.pais] += d["total_orcado"]
            realizado[it.pais] += d["total_realizado"]
    hoje = _date.today()
    decorridos = 12 if ano < hoje.year else (hoje.month if ano == hoje.year else 0)
    resumo = {}
    for p in OPEX_PAISES:
        orc = round(orcados[p], 2)
        real = round(realizado[p], 2)
        pct = (real / orc) if orc > 0 else None
        # Ritmo esperado: fração do ano decorrido. Compara o realizado com ele.
        esperado = orc * (decorridos / 12) if orc > 0 else 0.0
        if orc <= 0:
            alerta = "sem_orcado"
        elif real > orc:
            alerta = "acima"          # estourou o orçado do ano
        elif esperado > 0 and real < esperado * 0.8:
            alerta = "abaixo"         # gastando menos que o ritmo esperado
        elif esperado > 0 and real > esperado * 1.1:
            alerta = "atencao"        # acima do ritmo, mas dentro do ano
        else:
            alerta = "ok"
        resumo[p] = {"pais": p, "moeda": OPEX_MOEDA[p], "regiao": OPEX_REGIAO[p],
                     "orcado": orc, "realizado": real, "pct": pct,
                     "residual": round(orc - real, 2), "alerta": alerta}
    return resumo


@router.get("/api/controle-orcamento-exec/opex")
@_com_banco
def opex_listar(req: Request, ano: Optional[int] = None):
    _exigir(req, "view")
    from datetime import date as _date
    with SessionLocal() as s:
        anos = sorted({a for (a,) in s.execute(
            select(OpexItem.ano).where(OpexItem.ano > 0).distinct()).all()})
        ano = ano or (anos[-1] if anos else _date.today().year)
        itens = s.scalars(select(OpexItem).where(OpexItem.ano == ano)
                          .order_by(OpexItem.sort_order, OpexItem.id)).all()
        return {
            "ano": ano,
            "anos": anos or [ano],
            "paises": list(OPEX_PAISES),
            "moedas": OPEX_MOEDA,
            "itens": [it.to_dict() for it in itens],
            "resumo": _opex_resumo(s, ano),
        }


@router.post("/api/controle-orcamento-exec/opex", status_code=201)
@_com_banco
def opex_incluir(body: OpexItemIn, req: Request):
    sd = _exigir(req, "create", "incluir", "linha OPEX")
    check_rate_limit(req, "api")
    from datetime import date as _date
    pais = _opex_valida_pais(body.pais)
    ano = int(body.ano or _date.today().year)
    with SessionLocal.begin() as s:
        ordem = (s.scalar(select(func.max(OpexItem.sort_order))) or 0) + 1
        it = OpexItem(
            regiao=OPEX_REGIAO[pais], pais=pais, ano=ano,
            bu=body.bu[:120], fornecedor=body.fornecedor[:200],
            conta_contabil=body.conta_contabil[:60], conta_descricao=body.conta_descricao[:200],
            tipo_despesa=body.tipo_despesa[:80],
            orcado_meses=json.dumps(_opex_meses(body.orcado_meses)),
            realizado_meses=json.dumps(_opex_meses(body.realizado_meses)),
            sort_order=ordem, atualizado_por=sd.get("username", ""),
        )
        s.add(it)
        s.flush()
        return it.to_dict()


@router.patch("/api/controle-orcamento-exec/opex/{item_id}")
@_com_banco
def opex_alterar(item_id: int, body: OpexItemPatch, req: Request):
    sd = _exigir(req, "edit")
    with SessionLocal.begin() as s:
        it = s.get(OpexItem, item_id)
        if not it:
            raise HTTPException(404, "Linha não encontrada.")
        dados = body.model_dump(exclude_unset=True)
        if "pais" in dados and dados["pais"] is not None:
            it.pais = _opex_valida_pais(dados["pais"])
            it.regiao = OPEX_REGIAO[it.pais]
        if "ano" in dados and dados["ano"]:
            it.ano = int(dados["ano"])
        for campo, limite in (("bu", 120), ("fornecedor", 200), ("conta_contabil", 60),
                              ("conta_descricao", 200), ("tipo_despesa", 80)):
            if campo in dados and dados[campo] is not None:
                setattr(it, campo, str(dados[campo])[:limite])
        if "orcado_meses" in dados and dados["orcado_meses"] is not None:
            it.orcado_meses = json.dumps(_opex_meses(dados["orcado_meses"]))
        if "realizado_meses" in dados and dados["realizado_meses"] is not None:
            it.realizado_meses = json.dumps(_opex_meses(dados["realizado_meses"]))
        it.atualizado_por = sd.get("username", "")
        s.flush()
        return it.to_dict()


@router.delete("/api/controle-orcamento-exec/opex/{item_id}")
@_com_banco
def opex_excluir(item_id: int, req: Request):
    _exigir(req, "edit", "excluir", f"linha OPEX {item_id}")
    with SessionLocal.begin() as s:
        it = s.get(OpexItem, item_id)
        if not it:
            raise HTTPException(404, "Linha não encontrada.")
        s.delete(it)
    return {"ok": True}


# ── OPEX: planilha modelo e importação ───────────────────────────────────
MESES_ABREV = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN",
               "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")


def _sem_acento(texto: str) -> str:
    """Sem acento e sem caixa: o cabeçalho digitado varia ("PAÍS", "Pais")."""
    import unicodedata
    base = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in base if unicodedata.category(c) != "Mn")

# (chave no modelo, rótulo na planilha, obrigatória?)
COLUNAS_OPEX = (
    ("pais", "País", True),
    ("ano", "Ano", True),
    ("bu", "BU", False),
    ("fornecedor", "Fornecedor", False),
    ("conta_contabil", "Conta contábil", False),
    ("conta_descricao", "Descrição da conta", False),
    ("tipo_despesa", "Tipo de despesa", False),
)


def _rotulos_meses() -> list[tuple[str, str]]:
    """[(chave, rótulo)] das 24 colunas mensais: orçado e realizado."""
    fora = []
    for i, m in enumerate(MESES_ABREV, start=1):
        fora.append((f"orcado_{i}", f"ORÇADO {m}"))
    for i, m in enumerate(MESES_ABREV, start=1):
        fora.append((f"realizado_{i}", f"REALIZADO {m}"))
    return fora


def _chave_coluna(texto: str) -> str:
    """Rótulo da planilha -> chave interna, tolerante a acento, caixa e ao
    asterisco de obrigatória. O modelo escreve "País *" no cabeçalho; sem
    tirar o asterisco aqui, a planilha que o próprio portal gerou voltava
    recusada por "falta a coluna País"."""
    bruto = _sem_acento(str(texto or "")).strip().lower()
    bruto = re.sub(r"\s*\*+\s*$", "", bruto)
    bruto = re.sub(r"\s+", " ", bruto)
    for chave, rotulo, _ in COLUNAS_OPEX:
        if bruto in (_sem_acento(rotulo).lower(), chave):
            return chave
    for chave, rotulo in _rotulos_meses():
        if bruto in (_sem_acento(rotulo).lower(), chave.replace("_", " ")):
            return chave
    return ""


@router.get("/api/controle-orcamento-exec/opex/modelo")
def opex_modelo(req: Request):
    """Planilha modelo do OPEX: uma linha por gasto, 12 meses de orçado e 12
    de realizado. É o mesmo formato que a importação espera de volta."""
    _exigir(req, "view")
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from fastapi.responses import StreamingResponse
    import io as _io

    wb = Workbook()
    ws = wb.active
    ws.title = "OPEX"
    cabecalho = Font(bold=True, color="FFFFFF")
    fundo = PatternFill("solid", fgColor="1F4E79")
    fundo_mes = PatternFill("solid", fgColor="2E75B6")

    colunas = [(c, r, o) for c, r, o in COLUNAS_OPEX]
    colunas += [(c, r, False) for c, r in _rotulos_meses()]
    for col, (_chave, rotulo, obrig) in enumerate(colunas, start=1):
        cel = ws.cell(row=1, column=col, value=rotulo + (" *" if obrig else ""))
        cel.font = cabecalho
        cel.fill = fundo if col <= len(COLUNAS_OPEX) else fundo_mes
        cel.alignment = Alignment(horizontal="center")
        ws.column_dimensions[cel.column_letter].width = max(13, len(cel.value) + 3)

    # Duas linhas de exemplo: uma do Brasil e uma da LATAM, que usam colunas
    # diferentes — é a dúvida que mais aparece.
    ws.cell(row=2, column=1, value="BR")
    ws.cell(row=2, column=2, value=date.today().year)
    ws.cell(row=2, column=3, value="Infra")
    ws.cell(row=2, column=4, value="Fornecedor Exemplo Ltda")
    ws.cell(row=2, column=7, value="Manutenção")
    ws.cell(row=2, column=8, value=1500)
    ws.cell(row=2, column=20, value=1480)

    ws.cell(row=3, column=1, value="AR")
    ws.cell(row=3, column=2, value=date.today().year)
    ws.cell(row=3, column=3, value="Infra")
    ws.cell(row=3, column=4, value="Proveedor Ejemplo S.A.")
    ws.cell(row=3, column=5, value="6110100")
    ws.cell(row=3, column=6, value="Servicios de TI")
    ws.cell(row=3, column=8, value=250000)
    ws.freeze_panes = "A2"

    aba = wb.create_sheet("Instruções")
    for i, linha in enumerate([
        "Uma linha por gasto. A primeira linha é o cabeçalho e não deve ser apagada.",
        "País * aceita BR, AR ou UY. Ano * é o ano do orçamento (ex.: "
        + str(date.today().year) + ").",
        "Cada país fica na SUA moeda: BR em reais, AR em pesos argentinos, "
        "UY em pesos uruguaios. A importação não converte nada.",
        "Conta contábil e Descrição da conta são usadas por AR e UY.",
        "Tipo de despesa é usado pelo Brasil.",
        "ORÇADO JAN..DEZ e REALIZADO JAN..DEZ aceitam só números; vazio conta como zero.",
        "As linhas 2 e 3 são exemplos: apague-as antes de enviar.",
        "Ao enviar, o portal mostra uma prévia antes de gravar.",
    ], start=1):
        aba.cell(row=i, column=1, value=linha)
    aba.column_dimensions["A"].width = 95

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modelo_opex.xlsx"'})


def _numero_opex(v) -> float:
    """Aceita 1234.56, "1.234,56" e "R$ 1.234,56"; vazio vira 0."""
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    texto = re.sub(r"[^\d,.\-]", "", str(v)).strip()
    if not texto:
        return 0.0
    # "1.234,56" (pt-BR) x "1234.56": a vírgula, quando existe, é o decimal.
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return round(float(texto), 2)
    except ValueError:
        return 0.0


def _ler_planilha_opex(nome: str, conteudo: bytes) -> tuple[list[dict], list[str]]:
    """Devolve (linhas, avisos). Nunca levanta por causa do conteúdo: erro de
    linha vira aviso, para a prévia mostrar tudo de uma vez."""
    from openpyxl import load_workbook
    import io as _io

    if not nome.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Envie a planilha em .xlsx (use o modelo).")
    try:
        wb = load_workbook(_io.BytesIO(conteudo), data_only=True, read_only=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, f"Não consegui abrir a planilha: {exc}")
    ws = wb["OPEX"] if "OPEX" in wb.sheetnames else wb[wb.sheetnames[0]]

    linhas_brutas = list(ws.iter_rows(values_only=True))
    if not linhas_brutas:
        raise HTTPException(422, "A planilha está vazia.")

    mapa: dict[int, str] = {}
    for i, valor in enumerate(linhas_brutas[0]):
        chave = _chave_coluna(valor)
        if chave:
            mapa[i] = chave
    faltando = [r for c, r, o in COLUNAS_OPEX if o and c not in mapa.values()]
    if faltando:
        raise HTTPException(
            422, "A planilha não tem a(s) coluna(s) obrigatória(s): "
                 + ", ".join(faltando) + ". Baixe o modelo e use o cabeçalho dele.")

    linhas, avisos = [], []
    for n, bruta in enumerate(linhas_brutas[1:], start=2):
        dados = {mapa[i]: bruta[i] for i in mapa if i < len(bruta)}
        if not any(str(v or "").strip() for v in dados.values()):
            continue   # linha em branco no meio da planilha
        pais = _sem_acento(str(dados.get("pais") or "")).strip().upper()
        if pais not in OPEX_PAISES:
            avisos.append(f"linha {n}: país {dados.get('pais')!r} inválido — ignorada")
            continue
        try:
            ano = int(str(dados.get("ano") or "").strip()[:4])
        except ValueError:
            avisos.append(f"linha {n}: ano {dados.get('ano')!r} inválido — ignorada")
            continue
        orcado = {str(m): _numero_opex(dados.get(f"orcado_{m}")) for m in range(1, 13)}
        realizado = {str(m): _numero_opex(dados.get(f"realizado_{m}")) for m in range(1, 13)}
        linhas.append({
            "linha": n, "pais": pais, "regiao": OPEX_REGIAO[pais], "ano": ano,
            "moeda": OPEX_MOEDA[pais],
            "bu": str(dados.get("bu") or "").strip()[:120],
            "fornecedor": str(dados.get("fornecedor") or "").strip()[:200],
            "conta_contabil": str(dados.get("conta_contabil") or "").strip()[:60],
            "conta_descricao": str(dados.get("conta_descricao") or "").strip()[:200],
            "tipo_despesa": str(dados.get("tipo_despesa") or "").strip()[:80],
            "orcado_meses": {k: v for k, v in orcado.items() if v},
            "realizado_meses": {k: v for k, v in realizado.items() if v},
            "total_orcado": round(sum(orcado.values()), 2),
            "total_realizado": round(sum(realizado.values()), 2),
        })
    return linhas, avisos


@router.post("/api/controle-orcamento-exec/opex/importar")
@_com_banco
async def opex_importar(req: Request, arquivo: UploadFile = File(...),
                        dry_run: bool = True, substituir: bool = False):
    """Lê a planilha e mostra a prévia; só grava com `dry_run=false`.

    `substituir=true` APAGA as linhas OPEX dos anos/países presentes na
    planilha antes de incluir — é a forma de reenviar um ano corrigido sem
    ficar com o antigo e o novo somando juntos."""
    sd = _exigir(req, "create" if dry_run else "edit")
    check_rate_limit(req, "api")
    conteudo = await arquivo.read()
    if len(conteudo) > 8 * 1024 * 1024:
        raise HTTPException(413, "Planilha acima de 8 MB.")
    linhas, avisos = _ler_planilha_opex(arquivo.filename or "", conteudo)

    escopo = sorted({(x["pais"], x["ano"]) for x in linhas})
    resumo = {
        "arquivo": arquivo.filename, "lidas": len(linhas), "avisos": avisos,
        "escopo": [{"pais": p, "ano": a} for p, a in escopo],
        "total_orcado": round(sum(x["total_orcado"] for x in linhas), 2),
        "total_realizado": round(sum(x["total_realizado"] for x in linhas), 2),
    }
    if dry_run:
        # A prévia mostra as 50 primeiras: o suficiente para conferir o
        # cabeçalho e o formato dos números sem devolver a planilha inteira.
        return {**resumo, "dry_run": True, "previa": linhas[:50],
                "substituiria": bool(substituir)}

    if not linhas:
        raise HTTPException(422, "Nenhuma linha válida na planilha. "
                                 + (avisos[0] if avisos else ""))
    apagadas = 0
    with SessionLocal.begin() as s:
        if substituir and escopo:
            for pais, ano in escopo:
                apagadas += s.query(OpexItem).filter(
                    OpexItem.pais == pais, OpexItem.ano == ano).delete()
        ordem = (s.scalar(select(func.max(OpexItem.sort_order))) or 0)
        for x in linhas:
            ordem += 1
            s.add(OpexItem(
                regiao=x["regiao"], pais=x["pais"], ano=x["ano"], bu=x["bu"],
                fornecedor=x["fornecedor"], conta_contabil=x["conta_contabil"],
                conta_descricao=x["conta_descricao"], tipo_despesa=x["tipo_despesa"],
                orcado_meses=json.dumps(x["orcado_meses"]),
                realizado_meses=json.dumps(x["realizado_meses"]),
                sort_order=ordem, atualizado_por=sd.get("username", ""),
            ))
    registrar_acesso(sd.get("username", ""), client_ip(req), "importar",
                     f"OPEX: {len(linhas)} linha(s), {apagadas} substituída(s)")
    return {**resumo, "dry_run": False, "incluidas": len(linhas), "apagadas": apagadas}


@router.get("/api/controle-orcamento-exec/projetos")
@_com_banco
def listar_projetos(req: Request):
    _exigir(req, "view")
    with SessionLocal() as s:
        rows = s.scalars(_ordem_projetos()).all()
        return {
            "projetos": [_dict(p) for p in rows],
            "opcoes": {
                "tipos": list(TIPOS),
                "categorias": _listar_categorias(s),
                "estagios": list(ESTAGIOS),
                "prioridades": list(PRIORIDADES),
            },
        }


@router.post("/api/controle-orcamento-exec/incluir", status_code=201)
@_com_banco
def incluir(body: IncluirIn, req: Request):
    """Inclui um ou mais projetos (número separado por vírgula) e já puxa os
    valores do EBS. O Projeto/Demanda é o informado aqui (não vem do EBS)."""
    _exigir(req, "create", "incluir", f"projeto(s) {body.numero}")
    check_rate_limit(req, "api")
    numeros = [n.strip() for n in body.numero.split(",") if n.strip()]
    if not numeros:
        raise HTTPException(422, "Informe ao menos um número de projeto.")

    # Consulta o EBS (não-fatal: se falhar, inclui com zeros e avisa)
    ebs: dict[str, dict] = {}
    aviso = ""
    try:
        ebs = _ebs_capex(numeros)
    except ValueError as exc:
        aviso = str(exc)

    autor = _autor(req)
    rates = _fx_rates() if ebs else {}
    criados: list[dict] = []
    nao_encontrados: list[str] = []
    avisos_fx: list[str] = []
    with SessionLocal.begin() as s:
        _garantir_categoria(s, body.categoria)
        base_ordem = (s.scalar(select(func.max(BudgetProject.sort_order))) or 0)
        for i, numero in enumerate(numeros, start=1):
            p = BudgetProject(
                code=numero[:40],
                name=body.projeto_demanda,
                kind=body.tipo,
                category=body.categoria or "Outros",
                area=body.area,
                sort_order=base_ordem + i,
                updated_by=autor,
            )
            linha = ebs.get(numero)
            if linha:
                av, _alg = _aplicar_ebs(p, linha, rates)
                if av:
                    avisos_fx.append(av)
            else:
                if not aviso:
                    nao_encontrados.append(numero)
            s.add(p)
            s.flush()
            criados.append(_dict(p))

    partes = [x for x in [aviso] if x]
    if nao_encontrados:
        partes.append("Não encontrado(s) no EBS: " + ", ".join(nao_encontrados))
    partes.extend(avisos_fx)
    return {"projetos": criados, "aviso": " · ".join(partes)}


@router.get("/api/controle-orcamento-exec/sincronizar")
def sincronizar_get(req: Request):
    """Rede de segurança: alguns proxies em subcaminho rebaixam o POST para GET
    num redirect (301/302), e o botão "Atualizar (EBS)" tomava 405. Aqui o GET
    faz o mesmo que o POST, com a MESMA permissão (edit) e o mesmo rate-limit —
    então funciona mesmo que a requisição chegue rebaixada. O certo continua
    sendo o proxy preservar o método (veja deploy/proxy_portal_spare.conf)."""
    return sincronizar(req)


@router.post("/api/controle-orcamento-exec/sincronizar")
@_com_banco
def sincronizar(req: Request):
    """Atualiza os valores financeiros de TODOS os projetos a partir do EBS."""
    _exigir(req, "edit", "sincronizar", "sincronização com o EBS")
    check_rate_limit(req, "api")
    with SessionLocal() as s:
        rows = s.scalars(_ordem_projetos()).all()
        codigos = [p.code.strip() for p in rows if p.code and p.code.strip()]
    if not codigos:
        return {"atualizados": 0, "aviso": "Nenhum projeto com número para sincronizar."}
    try:
        ebs = _ebs_capex(codigos)
    except ValueError as exc:
        raise HTTPException(502, str(exc))

    # Guarda de segurança: se o EBS não devolveu NADA, não mexe em nada.
    if not ebs:
        return {"atualizados": 0, "bloqueados": 0,
                "aviso": "O EBS não retornou dados; nada foi alterado."}

    autor = _autor(req)
    rates = _fx_rates()
    atualizados = 0
    bloqueados = 0
    sem_dados = 0
    nao_encontrados: list[str] = []
    avisos_fx: list[str] = []
    with SessionLocal.begin() as s:
        rows = s.scalars(_ordem_projetos()).all()
        for p in rows:
            if p.locked:
                bloqueados += 1
                continue  # projeto travado: não é alterado pelo EBS
            linha = ebs.get((p.code or "").strip())
            if linha:
                av, aplicou = _aplicar_ebs(p, linha, rates)
                if av:
                    avisos_fx.append(av)
                if aplicou:
                    p.updated_by = autor
                    atualizados += 1
                else:
                    # EBS trouxe a linha, mas sem financeiros: NÃO zera nada.
                    sem_dados += 1
            elif p.code:
                nao_encontrados.append(p.code)
    partes = []
    if bloqueados:
        partes.append(f"{bloqueados} projeto(s) bloqueado(s) não alterado(s)")
    if sem_dados:
        partes.append(f"{sem_dados} projeto(s) sem valores no EBS (mantidos como estavam)")
    if nao_encontrados:
        partes.append("Não encontrado(s) no EBS: " + ", ".join(nao_encontrados))
    partes.extend(avisos_fx)
    return {"atualizados": atualizados, "bloqueados": bloqueados, "aviso": " · ".join(partes)}


@router.post("/api/controle-orcamento-exec/projetos", status_code=201)
@_com_banco
def criar_projeto(body: ProjetoIn, req: Request):
    _exigir(req, "create", "incluir", f"projeto {body.codigo or body.nome or ''}")
    check_rate_limit(req, "api")
    dados = body.model_dump(exclude_none=True)
    with SessionLocal.begin() as s:
        _checar_categoria(s, dados)
        proximo = (s.scalar(select(func.max(BudgetProject.sort_order))) or 0) + 1
        p = BudgetProject(sort_order=proximo, updated_by=_autor(req))
        for campo, coluna in _CAMPOS.items():
            setattr(p, coluna, dados.get(campo, _PADRAO[campo]))
        s.add(p)
        s.flush()
        return _dict(p)


@router.patch("/api/controle-orcamento-exec/projetos/{projeto_id}")
@_com_banco
def atualizar_projeto(projeto_id: int, body: ProjetoIn, req: Request):
    _exigir(req, "edit", "alterar", f"projeto id {projeto_id}")
    check_rate_limit(req, "api")
    dados = body.model_dump(exclude_unset=True)
    with SessionLocal.begin() as s:
        p = s.get(BudgetProject, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        _checar_categoria(s, dados)
        for campo, valor in dados.items():
            if campo == "vencimento":
                p.due_date = valor
            elif valor is not None:
                setattr(p, _CAMPOS[campo], valor)
        p.updated_by = _autor(req)
        s.flush()
        return _dict(p)


@router.delete("/api/controle-orcamento-exec/projetos/{projeto_id}")
@_com_banco
def excluir_projeto(projeto_id: int, req: Request):
    _exigir(req, "edit", "excluir", f"projeto id {projeto_id}")
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        p = s.get(BudgetProject, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        s.delete(p)
    return {"ok": True}


@router.post("/api/controle-orcamento-exec/projetos/{projeto_id}/duplicar", status_code=201)
@_com_banco
def duplicar_projeto(projeto_id: int, req: Request):
    _exigir(req, "create", "incluir", f"duplicar projeto id {projeto_id}")
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        orig = s.get(BudgetProject, projeto_id)
        if not orig:
            raise HTTPException(404, "Projeto não encontrado.")
        copia = BudgetProject(
            sort_order=orig.sort_order,
            updated_by=_autor(req),
            code=(orig.code + "-C")[:40] if orig.code else "",
        )
        for coluna in ("name", "kind", "category", "area", "stage", "priority",
                       "approved_budget", "committed", "realized", "a_realizar", "due_date"):
            setattr(copia, coluna, getattr(orig, coluna))
        s.add(copia)
        s.flush()
        return _dict(copia)


# ── API: categorias ───────────────────────────────────────────────
@router.get("/api/controle-orcamento-exec/categorias")
@_com_banco
def listar_categorias(req: Request):
    _exigir(req, "view")
    with SessionLocal() as s:
        return {"categorias": _listar_categorias(s)}


@router.post("/api/controle-orcamento-exec/categorias", status_code=201)
@_com_banco
def criar_categoria(body: CategoriaIn, req: Request):
    _exigir(req, "create", "incluir", f"categoria {body.nome}")
    check_rate_limit(req, "api")
    if not body.nome:
        raise HTTPException(422, "Nome da categoria obrigatório.")
    with SessionLocal.begin() as s:
        if _categoria_existe(s, body.nome):
            raise HTTPException(409, f"Já existe a categoria {body.nome!r}.")
        proximo = (s.scalar(select(func.max(BudgetCategory.sort_order))) or 0) + 1
        c = BudgetCategory(name=body.nome, color=body.cor or "#9ca3af", sort_order=proximo)
        s.add(c)
        s.flush()
        return _cat_dict(c)


@router.patch("/api/controle-orcamento-exec/categorias/{categoria_id}")
@_com_banco
def atualizar_categoria(categoria_id: int, body: CategoriaIn, req: Request):
    _exigir(req, "edit", "alterar", f"categoria id {categoria_id}")
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        c = s.get(BudgetCategory, categoria_id)
        if not c:
            raise HTTPException(404, "Categoria não encontrada.")
        if body.nome and body.nome != c.name:
            if _categoria_existe(s, body.nome):
                raise HTTPException(409, f"Já existe a categoria {body.nome!r}.")
            antigo = c.name
            c.name = body.nome
            for p in s.scalars(select(BudgetProject).where(BudgetProject.category == antigo)).all():
                p.category = body.nome
        if body.cor:
            c.color = body.cor
        s.flush()
        return _cat_dict(c)


@router.delete("/api/controle-orcamento-exec/categorias/{categoria_id}")
@_com_banco
def excluir_categoria(categoria_id: int, req: Request):
    _exigir(req, "edit", "excluir", f"categoria id {categoria_id}")
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        c = s.get(BudgetCategory, categoria_id)
        if not c:
            raise HTTPException(404, "Categoria não encontrada.")
        em_uso = s.scalar(select(func.count()).select_from(BudgetProject).where(BudgetProject.category == c.name)) or 0
        if em_uso:
            raise HTTPException(409, f"A categoria {c.name!r} está em uso por {em_uso} projeto(s).")
        s.delete(c)
    return {"ok": True}


# ── POs de um projeto, lidas do EBS (consulta, sem gravar nada) ────────
# Toda ID de projeto está atrelada a uma PO. Esta tela responde, para um
# projeto: quais POs existem, quanto valem, que itens e quantidades têm, e
# se já viraram nota fiscal.
#
# NÃO ALIMENTA O DASHBOARD. Nada daqui é gravado: é leitura do EBS na hora,
# para conferir os números antes de decidir se eles entram no painel. Se um
# dia entrarem, vai ser por uma gravação explícita, não por efeito colateral
# de abrir a tela.
#
# O SQL está em consultas/ebs/orcamento_po_do_projeto.sql e
# consultas/ebs/orcamento_po_itens.sql — não aqui. Este endpoint só valida o
# parâmetro, chama pelo nome e devolve.
_RE_PROJETO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,39}$")


def _chamar_ebs(consulta: str, binds: dict, max_rows: int = 2000) -> list[dict]:
    """Uma consulta nomeada do EBS, com os erros traduzidos para a tela."""
    try:
        from integracoes import ebs_oracle
        return ebs_oracle.run_named(consulta, binds, max_rows=max_rows)
    except ImportError as exc:
        raise HTTPException(
            503, "O driver Oracle não está instalado neste servidor "
                 f"(pip install oracledb). Detalhe: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("consulta %s falhou: %s", consulta, exc)
        # Falta de credencial é 503 (configure o serviço), não 502: ninguém
        # recusou nada — a conexão nem chegou a ser tentada.
        if type(exc).__name__ == "EbsOracleSemCredencial":
            raise HTTPException(503, str(exc)) from exc
        from core.mascara import sem_dado_de_acesso
        raise HTTPException(502, sem_dado_de_acesso(str(exc))) from exc


@router.get("/api/controle-orcamento-exec/ebs/projeto/{numero}/pos")
def ebs_pos_do_projeto(numero: str, req: Request):
    """As POs de um projeto, com a NF atrelada quando houver."""
    _exigir(req, "view")
    check_rate_limit(req, "api")
    numero = (numero or "").strip()
    # Entra como bind variable de qualquer jeito; validar antes evita ida ao
    # banco por engano de digitação e dá erro melhor que o do driver.
    if not _RE_PROJETO.match(numero):
        raise HTTPException(422, "Número de projeto inválido.")

    linhas = _chamar_ebs("orcamento_po_do_projeto", {"p_project_number": numero})
    if not linhas:
        raise HTTPException(
            404, f"O projeto {numero} não foi encontrado no EBS, ou não tem "
                 "nenhuma PO com distribuição para ele.")

    executadas = sum(1 for l in linhas if l.get("status_execucao") == "Executada")
    # A chave da NF pode vir vazia mesmo com NF lançada: ela mora num dos
    # GLOBAL_ATTRIBUTE da AP_INVOICES_ALL e qual deles varia por instalação.
    # A tela precisa saber a diferença entre "não tem NF" e "tem NF e não
    # achei a chave", senão alguém conclui que a nota não existe.
    com_nf = [l for l in linhas if (l.get("nf_qtd") or 0)]
    sem_chave = sum(1 for l in com_nf if not (l.get("nf_chave") or "").strip())
    return {
        "projeto": numero,
        "projeto_nome": (linhas[0].get("projeto_nome") or ""),
        "total": len(linhas),
        "executadas": executadas,
        "em_andamento": len(linhas) - executadas,
        "nf_sem_chave": sem_chave,
        "pos": linhas,
    }


@router.get("/api/controle-orcamento-exec/ebs/projeto/{numero}/pos/{po}/itens")
def ebs_itens_da_po(numero: str, po: str, req: Request):
    """Os itens de uma PO dentro do projeto — o detalhe de uma linha da lista."""
    _exigir(req, "view")
    check_rate_limit(req, "api")
    numero, po = (numero or "").strip(), (po or "").strip()
    if not _RE_PROJETO.match(numero):
        raise HTTPException(422, "Número de projeto inválido.")
    if not _RE_PROJETO.match(po):
        raise HTTPException(422, "Número de PO inválido.")

    linhas = _chamar_ebs("orcamento_po_itens",
                         {"p_project_number": numero, "numero_po": po}, max_rows=500)
    if not linhas:
        raise HTTPException(
            404, f"A PO {po} não tem linha ativa para o projeto {numero}.")
    return {"projeto": numero, "po": po, "total": len(linhas), "itens": linhas}
