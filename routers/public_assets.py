"""Conversão EBS → ServiceNow com a credencial de serviço do EBS.

Não é mais aberta: entra quem tem sessão no portal com `consulta:view` ou
quem apresenta o token de API (`PUBLIC_ASSETS_TOKEN`, no cofre) no cabeçalho
`X-Api-Key` — é o caminho para integrações de outros times. Tudo passa pelo
rate limit e fica na tabela `public_ebs_query_audit`.
"""
from __future__ import annotations

import hmac
import io
import logging
import os
import threading
import time

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from config import get_settings
from core.security import get_session, check_rate_limit, client_ip
from db.portal import SessionLocal, PublicEbsQueryAudit
from integracoes.ebs_service import login as ebs_login, search_many as ebs_search_many

_cfg = get_settings()
_log = logging.getLogger("public_assets")
router = APIRouter(prefix="/api/public-assets", tags=["Conversão EBS → ServiceNow"])

_auth_lock = threading.Lock()
_auth_cache: dict = {"value": None, "expires": 0.0}

# Teto por chamada: cada identificador é uma consulta ao EBS em paralelo.
MAX_IDENTIFICADORES = 200
CHAVE_TOKEN = "PUBLIC_ASSETS_TOKEN"


# ── Pydantic models ───────────────────────────────────────────────

class PublicQueryIn(BaseModel):
    identificadores: list[str]

    @field_validator("identificadores")
    @classmethod
    def clean_ids(cls, v: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in v:
            item = str(raw).strip()
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        if not out:
            raise ValueError("Informe ao menos um identificador.")
        if len(out) > MAX_IDENTIFICADORES:
            raise ValueError(f"No máximo {MAX_IDENTIFICADORES} identificadores por chamada.")
        return out


# ── Quem pode chamar ──────────────────────────────────────────────

def _token_configurado() -> str:
    from core import cofre
    return (cofre.obter(CHAVE_TOKEN) or "").strip()


def _autorizar(req: Request) -> str:
    """Devolve quem chamou: o login da sessão ou 'token'. Sem um dos dois, 401."""
    apresentado = (req.headers.get("x-api-key") or "").strip()
    if apresentado:
        esperado = _token_configurado()
        if esperado and hmac.compare_digest(apresentado, esperado):
            return "token"
        raise HTTPException(401, "Token de API inválido.")
    sd = get_session(req, required=False)
    if sd is None:
        raise HTTPException(401, "Sessão não autenticada ou token de API ausente (X-Api-Key).")
    if not sd.get("is_admin") and not (sd.get("permission_map") or {}).get("consulta", {}).get("can_view"):
        raise HTTPException(403, "Permissão insuficiente (consulta).")
    return sd.get("username") or ""


# ── Internal helpers ──────────────────────────────────────────────

def _credential(name: str) -> str:
    """Credencial entregue pelo systemd (LoadCredentialEncrypted).

    Sem ela a consulta ao EBS não tem como acontecer: devolve 503 dizendo
    o que falta, em vez de um 500 mudo. O diretório vem de
    CREDENTIALS_DIRECTORY, que o systemd define para o próprio serviço —
    outro serviço (um ambiente de testes, por exemplo) tem o dele.
    """
    directory = _cfg.CREDENTIALS_DIRECTORY
    path = os.path.join(directory, name) if directory else ""
    if not path or not os.path.isfile(path):
        raise HTTPException(
            503, f"Credencial protegida do EBS não carregada ({name}). O serviço precisa de "
                 f"LoadCredentialEncrypted no unit e CREDENTIALS_DIRECTORY apontando para o "
                 f"diretório dele (atual: {directory or 'em branco'}).")
    with open(path, "r", encoding="utf-8") as handle:
        value = handle.read().strip()
    if not value:
        raise HTTPException(503, f"Credencial protegida do EBS vazia ({name}).")
    return value


def _auth(force: bool = False):
    now = time.time()
    with _auth_lock:
        if not force and _auth_cache["value"] and _auth_cache["expires"] > now:
            return _auth_cache["value"]
        value = ebs_login(
            _credential("ebs_public_username"),
            _credential("ebs_public_password"),
        )
        _auth_cache["value"] = value
        _auth_cache["expires"] = now + 600
        return value


def _normal(value) -> str:
    return " ".join(str(value or "").strip().upper().split())


def _company(value) -> str:
    value = _normal(value)
    for name in ("RENNER", "YOUCOM", "CAMICADO"):
        if name in value:
            return name
    return value.replace("FA_", "")


def _rules(db):
    """Load classification rules as a lookup dict."""
    rows = db.execute(
        text(
            "SELECT description_pattern, company, category, model "
            "FROM classifications WHERE active=true ORDER BY id DESC"
        )
    ).mappings().all()
    result = {}
    for row in rows:
        key = (_normal(row["description_pattern"]), _normal(row["company"]))
        result.setdefault(key, dict(row))
    return result


def _apply_rule(row: dict, rule_map: dict) -> dict:
    desc = _normal(row.get("descricao"))
    comp = _company(row.get("empresa") or row.get("book_type_code"))
    rule = rule_map.get((desc, _normal(comp))) or rule_map.get((desc, ""))
    row["empresa"] = comp
    row["categoria"] = rule["category"] if rule else "NÃO CLASSIFICADA"
    row["modelo"] = rule["model"] if rule else (row.get("descricao") or "")
    return row


def _snow(row: dict) -> dict:
    dpis = row.get("dpis") or ""
    return {
        "serial_number": row.get("numero_serie") or "",
        "model": row.get("modelo") or "",
        "asset_tag": row.get("etiqueta") or "",
        "model_category": row.get("categoria") or "",
        "stockroom": "SPARE - CD324",
        "state": "In stock",
        "substate": "Available",
        "acquisition_method": "Purchase",
        "aisle_and_space": "",
        "company": row.get("empresa") or "",
        "cost": row.get("custo_asset") if row.get("custo_asset") is not None else "",
        "expenditure_type": "Capex",
        "purchased": dpis,
        "quantity": 1,
        "depreciation": "SL 5 Years",
        "depreciation_effective_date": dpis,
        "descricao_ebs": row.get("descricao") or "",
        "ativo": row.get("ativo") or row.get("asset_id") or "",
    }


def _audit(ip: str, usuario: str, ids: list, found: int, missing: int, exported: bool,
           outcome: str = "SUCCESS", error: str = ""):
    try:
        with SessionLocal.begin() as db:
            db.add(PublicEbsQueryAudit(
                ip_address=ip, usuario=usuario[:80], identifiers_count=len(ids),
                found_count=found, missing_count=missing, exported=exported,
                outcome=outcome, error_message=str(error)[:500],
            ))
    except Exception:  # noqa: BLE001 — auditoria não derruba a consulta, mas fica no log
        _log.exception("auditoria da conversão EBS não gravada")


def _execute(ids: list[str], req: Request, exported: bool = False) -> list[dict]:
    usuario = _autorizar(req)
    check_rate_limit(req)
    ip = client_ip(req)
    try:
        raw = ebs_search_many(_auth(), ids)
        if raw and all(
            "Sessão EBS expirada" in str(x.get("erro", ""))
            for x in raw if isinstance(x, dict)
        ):
            raw = ebs_search_many(_auth(True), ids)

        with SessionLocal() as db:
            rule_map = _rules(db)
            converted = [_apply_rule(dict(row), rule_map) for row in raw]

        found = sum(1 for row in converted if row.get("encontrado"))
        _audit(ip, usuario, ids, found, len(ids) - found, exported)
        return converted

    except HTTPException as exc:
        _audit(ip, usuario, ids, 0, len(ids), exported, "ERROR", exc.detail)
        raise
    except Exception as exc:  # noqa: BLE001 — falha da integração vira 502 e auditoria
        _audit(ip, usuario, ids, 0, len(ids), exported, "ERROR", exc)
        _log.error("integração EBS falhou: %s: %s", type(exc).__name__, exc)
        raise HTTPException(
            502,
            "Integração EBS temporariamente indisponível. Tente novamente mais tarde.",
        )


# ── Endpoints ─────────────────────────────────────────────────────

@router.get("/health")
def health(req: Request):
    _autorizar(req)
    try:
        user = _credential("ebs_public_username")
        return {
            "ok": True,
            "module": "public-assets",
            "authentication_required": True,
            "token_configured": bool(_token_configurado()),
            "source": "EBS",
            "credential_configured": bool(user),
            "read_only": True,
            "rate_limit": True,
            "max_identificadores": MAX_IDENTIFICADORES,
        }
    except HTTPException as exc:
        return {"ok": False, "module": "public-assets", "source": "EBS",
                "credential_configured": False, "error": exc.detail}


@router.post("/convert")
def convert(body: PublicQueryIn, req: Request):
    rows = _execute(body.identificadores, req)
    output = []
    for row in rows:
        entry = {
            "pesquisado": row.get("pesquisado", ""),
            "encontrado": bool(row.get("encontrado")),
        }
        if row.get("encontrado"):
            entry.update(_snow(row))
        else:
            entry["erro"] = row.get("erro", "Não encontrado")
        output.append(entry)

    found = sum(1 for row in output if row["encontrado"])
    return {
        "resultados": output,
        "total": len(body.identificadores),
        "encontrados": found,
        "nao_encontrados": len(body.identificadores) - found,
        "origem": "EBS",
    }


@router.post("/export")
def export(body: PublicQueryIn, req: Request):
    rows = [
        _snow(row)
        for row in _execute(body.identificadores, req, True)
        if row.get("encontrado")
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "ServiceNow"

    cols = [
        ("serial_number", "Serial Number"),
        ("model", "Model"),
        ("asset_tag", "Asset tag"),
        ("model_category", "Model category"),
        ("stockroom", "Stockroom"),
        ("state", "State"),
        ("substate", "Substate"),
        ("acquisition_method", "Acquisition method"),
        ("aisle_and_space", "Aisle and space"),
        ("company", "Company"),
        ("cost", "Cost"),
        ("expenditure_type", "Expenditure type"),
        ("purchased", "Purchased"),
        ("quantity", "Quantity"),
        ("depreciation", "Depreciation"),
        ("depreciation_effective_date", "Depreciation effective date"),
    ]

    ws.append([label for _, label in cols])
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="AB4807")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")

    for row in rows:
        ws.append([row.get(key, "") for key, _ in cols])

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    for column in ws.columns:
        width = max(len(str(cell.value or "")) for cell in column) + 2
        ws.column_dimensions[column[0].column_letter].width = min(45, max(12, width))

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="ativos_ebs_servicenow.xlsx"'},
    )
