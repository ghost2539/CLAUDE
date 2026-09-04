"""Controle de Orçamento — Execução CAPEX (clone independente do /tv2).

Servido em ``/controle-orcamento`` (e ``/controle-orçamento``), com banco
PRÓPRIO e SEPARADO (``database_orcamento_exec.py``). NÃO compartilha dados nem
código de estado com o /tv2.

Novidade em relação ao /tv2: barra de inclusão de projetos no topo (Número,
Tipo, Projeto/Demanda, Categoria, Área) e integração com a API de CAPEX do EBS
(``suporte.lojasrenner.com.br/ebs/api/capex/?projetos=...``), que preenche os
valores financeiros:

  saldo_inicial            → Orçamento Aprovado
  comprometido+reservados  → Comprometido
  realizado                → Realizado (Acum.)
  saldo_dia                → A Realizar
  (empresa, devolucoes, pct_exec, nome_projeto: NÃO são puxados)

Acesso livre (sem login), como o /tv2. As gravações passam pelo rate limit.
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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import func, select

from config import get_settings
from database_orcamento_exec import (
    BudgetCategory, BudgetProject, SessionLocal, ensure_db, utcnow,
)
from security import check_rate_limit, client_ip, get_session

_cfg = get_settings()
_DIR = _cfg.STATIC / "controle-orcamento-exec"
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


def _page() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{v}}", _asset_version()))


@router.get("/controle-orcamento", response_class=HTMLResponse)
def pagina():
    return _page()


@router.get("/controle-orcamento/", response_class=HTMLResponse)
def pagina_slash():
    return _page()


@router.get("/controle-orçamento", response_class=HTMLResponse)
def pagina_acento():
    return _page()


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
    """Campos editáveis pela tabela. a_realizar NÃO entra aqui: vem do EBS."""
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
    "realizado": "realized", "vencimento": "due_date",
}

_PADRAO = {
    "codigo": "", "nome": "Novo projeto", "tipo": "CAPEX", "categoria": "Outros",
    "area": "", "estagio": "Planejamento", "prioridade": "Média",
    "orcamento": Decimal("0"), "comprometido": Decimal("0"),
    "realizado": Decimal("0"), "vencimento": None,
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
        import ebs_service
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
    Prioriza valores fixados por env; senão tenta ao vivo (cache 1h)."""
    import time
    rates = {"ARS": (EBS_CAPEX_ARS_BRL or None), "UYU": (EBS_CAPEX_UYU_BRL or None)}
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


def _aplicar_ebs(p: BudgetProject, linha: dict, rates: Optional[dict] = None) -> str:
    """Preenche os campos financeiros do projeto a partir da linha do EBS,
    convertendo ARS/UYU→BRL quando a empresa for Argentina/Uruguai.
    NÃO altera nome/tipo/categoria/área. Retorna aviso (ou "")."""
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

    comprometido = _valor(linha.get("comprometido"))
    reservados = _valor(linha.get("reservados"))
    p.approved_budget = conv(_valor(linha.get("saldo_inicial")))
    p.committed = conv(comprometido + reservados)
    p.realized = conv(_valor(linha.get("realizado")))
    p.a_realizar = conv(_valor_signed(linha.get("saldo_dia")))
    p.synced_at = utcnow()
    return aviso


# ── API ───────────────────────────────────────────────────────────
@router.get("/api/controle-orcamento-exec/sessao")
def sessao(req: Request):
    sd = get_session(req, required=False)
    if not sd:
        return {"usuario": None}
    return {"usuario": {"username": sd.get("username"), "display_name": sd.get("display_name")}}


@router.get("/api/controle-orcamento-exec/projetos")
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


@router.post("/api/controle-orcamento-exec/incluir", status_code=201)
@_com_banco
def incluir(body: IncluirIn, req: Request):
    """Inclui um ou mais projetos (número separado por vírgula) e já puxa os
    valores do EBS. O Projeto/Demanda é o informado aqui (não vem do EBS)."""
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
                av = _aplicar_ebs(p, linha, rates)
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


@router.post("/api/controle-orcamento-exec/sincronizar")
@_com_banco
def sincronizar(req: Request):
    """Atualiza os valores financeiros de TODOS os projetos a partir do EBS."""
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

    autor = _autor(req)
    rates = _fx_rates()
    atualizados = 0
    nao_encontrados: list[str] = []
    avisos_fx: list[str] = []
    with SessionLocal.begin() as s:
        rows = s.scalars(_ordem_projetos()).all()
        for p in rows:
            linha = ebs.get((p.code or "").strip())
            if linha:
                av = _aplicar_ebs(p, linha, rates)
                if av:
                    avisos_fx.append(av)
                p.updated_by = autor
                atualizados += 1
            elif p.code:
                nao_encontrados.append(p.code)
    partes = []
    if nao_encontrados:
        partes.append("Não encontrado(s) no EBS: " + ", ".join(nao_encontrados))
    partes.extend(avisos_fx)
    return {"atualizados": atualizados, "aviso": " · ".join(partes)}


@router.post("/api/controle-orcamento-exec/projetos", status_code=201)
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


@router.patch("/api/controle-orcamento-exec/projetos/{projeto_id}")
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
                p.due_date = valor
            elif valor is not None:
                setattr(p, _CAMPOS[campo], valor)
        p.updated_by = _autor(req)
        s.flush()
        return _dict(p)


@router.delete("/api/controle-orcamento-exec/projetos/{projeto_id}")
@_com_banco
def excluir_projeto(projeto_id: int, req: Request):
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
def listar_categorias():
    with SessionLocal() as s:
        return {"categorias": _listar_categorias(s)}


@router.post("/api/controle-orcamento-exec/categorias", status_code=201)
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


@router.patch("/api/controle-orcamento-exec/categorias/{categoria_id}")
@_com_banco
def atualizar_categoria(categoria_id: int, body: CategoriaIn, req: Request):
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
