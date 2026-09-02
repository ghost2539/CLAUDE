"""Controle de Orçamento de Portfólio (CAPEX/OPEX) — módulo oculto.

Serve o dashboard React em ``/tv2`` e a API REST usada por ele.
O módulo faz parte do sistema mas NÃO aparece no menu do portal (não está em
``Settings.MODULES`` nem na sidebar de ``static/index.html``): o acesso é feito
diretamente pela URL ``http://<ip-do-servidor>:<porta>/tv2``
(``/controle-orcamento`` redireciona para lá).

Acesso: livre (sem login), como a consulta pública. Se houver sessão ativa,
o usuário é registrado em ``updated_by``; caso contrário registra-se o IP.
As gravações passam pelo rate limit de API por IP.

Os projetos ficam em um banco EXCLUSIVO deste módulo (``database_orcamento.py``,
por padrão SQLite em ``data/controle_orcamento.db``), sem tocar no banco do portal.
Os assets (``static/controle-orcamento/app.js`` e ``app.css``) são gerados a
partir de ``frontend/controle-orcamento`` (``npm run build``) e versionados no
repositório, pois o servidor não possui Node.js.
"""
from __future__ import annotations

import functools
import hashlib
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select

from config import get_settings
from database_orcamento import BudgetCategory, BudgetProject, SessionLocal, ensure_db
from security import check_rate_limit, client_ip, get_session

_cfg = get_settings()
_DIR = _cfg.STATIC / "controle-orcamento"
_log = logging.getLogger("controle_orcamento")

router = APIRouter(tags=["Controle de Orçamento"], include_in_schema=False)

# Nenhuma inicialização em tempo de import: a tabela do banco separado é
# criada na primeira requisição à API (ver _com_banco / ensure_db).

TIPOS = ("CAPEX", "OPEX")
# Categorias: cadastradas pelo usuário (tabela budget_categories)
ESTAGIOS = ("Planejamento", "Aprovação", "Em Execução", "Concluído")
PRIORIDADES = ("Alta", "Média", "Baixa")
_MAX_VALOR = Decimal("9999999999999.99")  # limite do Numeric(18, 2)


# ── Página ────────────────────────────────────────────────────────

@lru_cache
def _asset_version() -> str:
    """Hash curto dos assets compilados, usado para cache-busting."""
    h = hashlib.sha256()
    for name in ("app.js", "app.css"):
        path = _DIR / name
        if path.exists():
            h.update(path.read_bytes())
    return h.hexdigest()[:10]


def _page() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{v}}", _asset_version()))


@router.get("/tv2", response_class=HTMLResponse)
def tv2():
    """Página do módulo — acesso livre, sem autenticação."""
    return _page()


@router.get("/tv2/", response_class=HTMLResponse)
def tv2_slash():
    return _page()


@router.get("/controle-orcamento", response_class=HTMLResponse)
def controle_orcamento():
    """Alias: redireciona para /tv2."""
    return RedirectResponse("/tv2", status_code=302)


@router.get("/controle-orçamento", response_class=HTMLResponse)
def controle_orcamento_acento():
    """Alias com acento: redireciona para /tv2."""
    return RedirectResponse("/tv2", status_code=302)


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
    """Campos editáveis de um projeto. Todos opcionais: no PATCH só os
    enviados são alterados; no POST os ausentes recebem o padrão."""
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
        # existência verificada no banco (categorias são cadastráveis)
        return None if v is None else _texto(v, 60)

    @field_validator("estagio", mode="before")
    @classmethod
    def _v_estagio(cls, v):
        return None if v is None else _opcao(v, ESTAGIOS, "Estágio")

    @field_validator("prioridade", mode="before")
    @classmethod
    def _v_prioridade(cls, v):
        return None if v is None else _opcao(v, PRIORIDADES, "Prioridade")

    @field_validator("orcamento", "comprometido", "realizado", mode="before")
    @classmethod
    def _v_valor(cls, v):
        return None if v is None else _valor(v)

    @field_validator("vencimento", mode="before")
    @classmethod
    def _v_vencimento(cls, v):
        return _data(v)


# Mapeamento campo da API → coluna do modelo
_CAMPOS = {
    "codigo": "code",
    "nome": "name",
    "tipo": "kind",
    "categoria": "category",
    "area": "area",
    "estagio": "stage",
    "prioridade": "priority",
    "orcamento": "approved_budget",
    "comprometido": "committed",
    "realizado": "realized",
    "vencimento": "due_date",
}

_PADRAO = {
    "codigo": "",
    "nome": "Novo projeto",
    "tipo": "CAPEX",
    "categoria": "Outros",
    "area": "",
    "estagio": "Planejamento",
    "prioridade": "Média",
    "orcamento": Decimal("0"),
    "comprometido": Decimal("0"),
    "realizado": Decimal("0"),
    "vencimento": None,
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
        "vencimento": p.due_date.isoformat() if p.due_date else "",
        "ordem": p.sort_order,
        "atualizado_em": p.updated_at.isoformat() if p.updated_at else None,
        "atualizado_por": p.updated_by,
    }


def _categoria_existe(s, nome: str) -> bool:
    return bool(s.scalar(select(BudgetCategory.id).where(BudgetCategory.name == nome)))


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
    """Converte falhas de conexão do banco do módulo em 503 (sem stack trace),
    mantendo o restante do portal alheio a este banco."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            ensure_db()
            return fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — isolar o módulo do restante do portal
            _log.error("Banco do Controle de Orçamento indisponível: %s", exc, exc_info=True)
            raise HTTPException(503, "Banco de dados do módulo indisponível. Tente novamente mais tarde.")
    return wrapper


# ── API ───────────────────────────────────────────────────────────

@router.get("/api/controle-orcamento/sessao")
def sessao(req: Request):
    """Usuário logado no portal, se houver. Sempre 200 (a tela é pública)."""
    sd = get_session(req, required=False)
    if not sd:
        return {"usuario": None}
    return {"usuario": {"username": sd.get("username"), "display_name": sd.get("display_name")}}


@router.get("/api/controle-orcamento/projetos")
@_com_banco
def listar_projetos():
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


@router.post("/api/controle-orcamento/projetos", status_code=201)
@_com_banco
def criar_projeto(body: ProjetoIn, req: Request):
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


@router.patch("/api/controle-orcamento/projetos/{projeto_id}")
@_com_banco
def atualizar_projeto(projeto_id: int, body: ProjetoIn, req: Request):
    check_rate_limit(req, "api")
    dados = body.model_dump(exclude_unset=True)
    with SessionLocal.begin() as s:
        p = s.get(BudgetProject, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        _checar_categoria(s, dados)
        for campo, valor in dados.items():
            if campo == "vencimento":
                p.due_date = valor  # None limpa a data
            elif valor is not None:
                setattr(p, _CAMPOS[campo], valor)
        p.updated_by = _autor(req)
        s.flush()
        return _dict(p)


@router.delete("/api/controle-orcamento/projetos/{projeto_id}")
@_com_banco
def excluir_projeto(projeto_id: int, req: Request):
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        p = s.get(BudgetProject, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        s.delete(p)
    return {"ok": True}


@router.post("/api/controle-orcamento/projetos/{projeto_id}/duplicar", status_code=201)
@_com_banco
def duplicar_projeto(projeto_id: int, req: Request):
    """Cria uma cópia logo após o projeto original (mesmo sort_order)."""
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
                       "approved_budget", "committed", "realized", "due_date"):
            setattr(copia, coluna, getattr(orig, coluna))
        s.add(copia)
        s.flush()
        return _dict(copia)


# ── API: categorias ───────────────────────────────────────────────

@router.get("/api/controle-orcamento/categorias")
@_com_banco
def listar_categorias():
    with SessionLocal() as s:
        return {"categorias": _listar_categorias(s)}


@router.post("/api/controle-orcamento/categorias", status_code=201)
@_com_banco
def criar_categoria(body: CategoriaIn, req: Request):
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


@router.patch("/api/controle-orcamento/categorias/{categoria_id}")
@_com_banco
def atualizar_categoria(categoria_id: int, body: CategoriaIn, req: Request):
    """Renomear propaga o novo nome aos projetos que usam a categoria."""
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


@router.delete("/api/controle-orcamento/categorias/{categoria_id}")
@_com_banco
def excluir_categoria(categoria_id: int, req: Request):
    check_rate_limit(req, "api")
    with SessionLocal.begin() as s:
        c = s.get(BudgetCategory, categoria_id)
        if not c:
            raise HTTPException(404, "Categoria não encontrada.")
        em_uso = s.scalar(select(func.count()).select_from(BudgetProject).where(BudgetProject.category == c.name)) or 0
        if em_uso:
            raise HTTPException(409, f"A categoria {c.name!r} está em uso por {em_uso} projeto(s). Altere a categoria desses projetos antes de excluir.")
        s.delete(c)
    return {"ok": True}
