"""Consulta (asset query) router — search, single lookup, export."""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from db.portal import SessionLocal, ReceiptCycle, Setting
from core.security import get_session, require_permission
from routers.helpers import (
    apply_class, find_asset, local_search_one, xlsx_response,
)
from integracoes import ebs_ativos

router = APIRouter(prefix="/api", tags=["Consulta"])


# ── Pydantic models ───────────────────────────────────────────────

class QueryIn(BaseModel):
    identificadores: list[str]

    @field_validator("identificadores")
    @classmethod
    def clean_ids(cls, v: list[str]) -> list[str]:
        # Deduplicate, strip, remove blanks, cap at 1000
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
            if len(out) >= 1000:
                break
        return out


# ── Internal helpers ──────────────────────────────────────────────

def _erro_ebs(exc: Exception) -> str:
    if isinstance(exc, ImportError):
        return "O driver Oracle não está instalado neste servidor (pip install oracledb)."
    if type(exc).__name__ == "EbsOracleSemCredencial":
        return str(exc)
    from core.mascara import sem_dado_de_acesso
    return "A base do EBS recusou a consulta: " + sem_dado_de_acesso(str(exc))[:300]


def _complementar_com_portal(s, r: dict) -> dict:
    r.setdefault("baixado", "")
    r.setdefault("local_atribuido", "")
    r.setdefault("po", "")
    r.setdefault("nf", "")
    r.setdefault("erro", "")
    if r.get("po") and r.get("nf"):
        return r
    a = find_asset(s, r)
    if not a:
        return r
    c = s.scalar(select(ReceiptCycle).where(ReceiptCycle.asset_id == a.id)
                 .order_by(ReceiptCycle.id.desc()))
    if c:
        r["po"] = r.get("po") or (c.po or "")
        r["nf"] = r.get("nf") or (c.nf or "")
    return r


def consultar_ativos(s, ids: list[str]) -> list[dict]:
    ids = ebs_ativos.limpar(ids)
    if not ids:
        return []
    erro_ebs = ""
    try:
        linhas = ebs_ativos.consultar(ids)
    except Exception as exc:  # noqa: BLE001
        linhas, erro_ebs = [], _erro_ebs(exc)
    por_id = {linha["pesquisado"]: linha for linha in linhas}
    saida = []
    for i in ids:
        r = por_id.get(i)
        if not r or not r.get("encontrado"):
            local = local_search_one(s, i)
            if local.get("encontrado"):
                r = local
                r["erro"] = "Não está no EBS; dados da base local." if not erro_ebs else erro_ebs
            elif r is None:
                r = ebs_ativos.nao_encontrado(i, erro_ebs or "Não encontrado")
        r = apply_class(s, r)
        saida.append(_complementar_com_portal(s, r))
    return saida


def _query_assets(body: QueryIn, req: Request) -> dict:
    require_permission(req, "consulta", "view")
    qs = body.identificadores
    if not qs:
        return {"resultados": [], "encontrados": 0, "nao_encontrados": 0}
    with SessionLocal() as s:
        rows = consultar_ativos(s, qs)
    return {
        "resultados": rows,
        "encontrados": sum(bool(x.get("encontrado")) for x in rows),
        "nao_encontrados": sum(not x.get("encontrado") for x in rows),
    }


def _query_single(identificador: str, req: Request) -> dict:
    """Lookup a single asset and return the enriched result."""
    body = QueryIn(identificadores=[identificador])
    result = _query_assets(body, req)["resultados"]
    if not result:
        raise HTTPException(404, "Não encontrado")
    result = result[0]
    if not result.get("encontrado"):
        raise HTTPException(404, result.get("erro", "Não encontrado"))

    with SessionLocal() as s:
        a = find_asset(s, result)
        c = None
        if a:
            c = s.scalar(
                select(ReceiptCycle)
                .where(ReceiptCycle.asset_id == a.id, ReceiptCycle.open == True)  # noqa: E712
                .order_by(ReceiptCycle.id.desc())
            )
        result["ciclo"] = c.cycle_number if c else None
        result["cycle_id"] = c.id if c else None
    return result


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/consulta")
def query_assets(body: QueryIn, req: Request):
    return _query_assets(body, req)


@router.get("/consulta/single")
def query_single(identificador: str, req: Request):
    return _query_single(identificador, req)


@router.post("/consulta/export")
def export_query(body: QueryIn, req: Request):
    require_permission(req, "consulta", "export")
    rows = _query_assets(body, req)["resultados"]
    return xlsx_response(rows, "consulta_ativos.xlsx")


# ── Colunas visíveis na consulta ────────────────────────────────────
# A visão padrão é da área (Configuração); cada pessoa pode reduzir ou
# reordenar a sua. Guardado em Setting, chave por login.
COLUNAS_CONSULTA = [
    ("empresa", "Empresa (BU)"), ("imobilizado", "Imobilizado"), ("etiqueta", "Etiqueta do Ativo"),
    ("numero_serie", "Nº de Série"), ("descricao", "Descrição do ativo"), ("categoria", "Categoria"),
    ("local_atribuido", "Local atribuído"), ("baixado", "Baixado?"), ("po", "PO"), ("nf", "NF"),
    ("erro", "Erro"),
]
_CHAVES = {c for c, _ in COLUNAS_CONSULTA}


def _colunas_padrao(s) -> list[str]:
    row = s.get(Setting, "consulta_colunas_padrao")
    lista = (row.value or {}).get("colunas") if row else None
    lista = [c for c in (lista or []) if c in _CHAVES]
    return lista or [c for c, _ in COLUNAS_CONSULTA]


@router.get("/consulta/colunas")
def colunas_ler(req: Request):
    sd = get_session(req)
    with SessionLocal() as s:
        padrao = _colunas_padrao(s)
        row = s.get(Setting, f"consulta_colunas:{sd.get('username', '')}")
        minhas = [c for c in ((row.value or {}).get("colunas") or []) if c in _CHAVES] if row else []
    return {"disponiveis": [{"chave": c, "rotulo": r} for c, r in COLUNAS_CONSULTA],
            "padrao": padrao, "minhas": minhas or padrao, "personalizada": bool(minhas)}


@router.put("/consulta/colunas")
def colunas_gravar(payload: dict, req: Request):
    sd = get_session(req)
    lista = [c for c in (payload or {}).get("colunas", []) if c in _CHAVES]
    with SessionLocal.begin() as s:
        chave = f"consulta_colunas:{sd.get('username', '')}"
        row = s.get(Setting, chave)
        if not lista:
            if row is not None:
                s.delete(row)
        else:
            if row is None:
                row = Setting(key=chave)
                s.add(row)
            row.value = {"colunas": lista}
            row.updated_by = sd.get("username", "")
    return colunas_ler(req)
