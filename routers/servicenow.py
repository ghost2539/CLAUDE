"""ServiceNow router — upload de ativos do recebimento para alm_hardware via SSO + JSONv2."""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, or_

from database import SessionLocal, Asset, ReceiptCycle
from security import require_permission, get_session

router = APIRouter(prefix="/api/servicenow", tags=["ServiceNow"])

# ── ServiceNow constants ─────────────────────────────────────────
SERVICENOW_BASE = "https://renner.service-now.com"
SN_PROXY = os.environ.get("SN_PROXY", "http://10.115.35.45:8888")
HARDWARE_TABLE = "alm_hardware"
MAX_RELAY_HOPS = 5

INSTALL_STATUS_MAP = {
    "in use": "1", "em uso": "1",
    "on order": "2", "em pedido": "2",
    "on maintenance": "3", "em manutencao": "3", "in maintenance": "3",
    "pending install": "4", "instalacao pendente": "4",
    "pending repair": "5", "reparo pendente": "5",
    "in stock": "6", "em estoque": "6",
    "retired": "7", "desativado": "7",
    "missing": "8", "ausente": "8",
    "in transit": "9", "em transito": "9",
    "disposed": "10", "descartado": "10",
}

SUBSTATUS_MAP = {
    "available": "available", "disponivel": "available",
    "reserved": "reserved", "reservado": "reserved",
    "defective": "defective", "defeituoso": "defective",
    "pending transfer": "pending_transfer", "transferencia pendente": "pending_transfer",
    "pre-allocated": "pre_allocated", "pre-alocado": "pre_allocated",
}

REFERENCE_FIELDS = [
    "model", "model_category", "company", "stockroom", "depreciation",
]

REFERENCE_TABLE_MAP = {
    "model": "cmdb_model",
    "model_category": "cmdb_model_category",
    "company": "core_company",
    "stockroom": "alm_stockroom",
    "depreciation": "cmdb_depreciation",
}

REFERENCE_NAME_FIELD = {
    "model": "name",
    "model_category": "name",
    "company": "name",
    "stockroom": "name",
    "depreciation": "name",
}

CURRENCY_MAP = {
    "BRL": "BRL", "R$": "BRL",
    "USD": "USD", "$": "USD",
    "UYU": "UYU", "$U": "UYU",
    "ARS": "ARS",
}

# ── Job tracking (in-memory) ─────────────────────────────────────
_jobs: dict[str, dict] = {}


def _cleanup_old_jobs():
    cutoff = time.time() - 3600
    expired = [k for k, v in _jobs.items() if v.get("started_at", 0) < cutoff]
    for k in expired:
        del _jobs[k]


# ── Pydantic models ──────────────────────────────────────────────

class UploadIn(BaseModel):
    cycle_ids: list[int]
    usuario: str
    senha: str
    stockroom: str = "SPARE - CD324"
    aisle_space: str = ""
    cost_currency: str = "BRL"
    calc_depreciation: bool = True


class TestLoginIn(BaseModel):
    usuario: str
    senha: str


# ── SSO functions (adapted from servicenow_insert.py) ────────────

def _get_http():
    """Lazy import requests + bs4 with clear error."""
    try:
        import requests as _req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        raise HTTPException(501, "Pacote 'requests' não instalado no servidor.")
    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        raise HTTPException(501, "Pacote 'beautifulsoup4' não instalado no servidor.")
    return _req, _BS


def _follow_form_redirects(session, response, BS, hop=0):
    if hop >= MAX_RELAY_HOPS:
        return response
    soup = BS(response.text, "html.parser")
    form = soup.find("form")
    if not form:
        return response
    action = form.get("action", "")
    if not action:
        return response
    if action.startswith("/"):
        from urllib.parse import urlparse
        parsed = urlparse(response.url)
        action = f"{parsed.scheme}://{parsed.netloc}{action}"
    fields = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if name:
            fields[name] = inp.get("value", "")
    if not fields:
        return response
    method = form.get("method", "post").lower()
    if method == "get":
        r = session.get(action, params=fields, allow_redirects=True, timeout=30)
    else:
        r = session.post(action, data=fields, allow_redirects=True, timeout=30)
    return _follow_form_redirects(session, r, BS, hop + 1)


def _login_sso(session, usuario, senha, _req, BS):
    sn_url = f"{SERVICENOW_BASE}/now/nav/ui/classic/params/target/home.do"
    print(f"[SSO] Step 1: GET {sn_url}")
    r1 = session.get(sn_url, allow_redirects=True, timeout=30)
    print(f"[SSO] Step 1 result: status={r1.status_code} url={r1.url}")
    soup = BS(r1.text, "html.parser")
    form = soup.find("form")
    if form:
        login_action = form.get("action", r1.url)
        if login_action.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(r1.url)
            login_action = f"{parsed.scheme}://{parsed.netloc}{login_action}"
        form_fields = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                form_fields[name] = inp.get("value", "")
        print(f"[SSO] Form found: action={login_action} fields={list(form_fields.keys())}")
    else:
        login_action = "https://loginsso.lojasrenner.com.br/oam/server/auth_cred_submit"
        form_fields = {}
        print(f"[SSO] No form found, using default action: {login_action}")

    for uf in ["username", "userid", "user", "login", "j_username"]:
        if uf in form_fields:
            form_fields[uf] = usuario
            break
    else:
        form_fields["username"] = usuario

    for pf in ["password", "passwd", "pass", "j_password"]:
        if pf in form_fields:
            form_fields[pf] = senha
            break
    else:
        form_fields["password"] = senha

    print(f"[SSO] Step 2: POST {login_action}")
    r2 = session.post(login_action, data=form_fields, allow_redirects=True, timeout=30)
    print(f"[SSO] Step 2 result: status={r2.status_code} url={r2.url}")
    r3 = _follow_form_redirects(session, r2, BS)
    print(f"[SSO] Step 3 (redirects): final url={r3.url}")
    ok = "service-now.com" in r3.url
    print(f"[SSO] Result: {'OK' if ok else 'FAILED'}")
    return ok


def _lookup_reference(session, field, display_value, cache, BS):
    if not display_value:
        return ""
    cache_key = f"{field}::{display_value}"
    if cache_key in cache:
        return cache[cache_key]
    table = REFERENCE_TABLE_MAP.get(field)
    name_field = REFERENCE_NAME_FIELD.get(field, "name")
    if not table:
        return display_value
    url = (
        f"{SERVICENOW_BASE}/{table}.do?JSONv2"
        f"&sysparm_action=getRecords"
        f"&sysparm_record_count=1"
        f"&sysparm_query={name_field}={display_value}"
    )
    try:
        r = session.get(url, headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=15)
        if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
            data = r.json()
            records = data.get("records", [])
            if records:
                sys_id = records[0].get("sys_id", "")
                cache[cache_key] = sys_id
                return sys_id
            else:
                cache[cache_key] = display_value
                return display_value
    except Exception:
        pass
    return display_value


def _insert_record(session, record):
    url = f"{SERVICENOW_BASE}/{HARDWARE_TABLE}.do?JSONv2&sysparm_action=insert"
    r = session.post(
        url, json=record,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
        data = r.json()
        records = data.get("records", [])
        if records:
            sys_id = records[0].get("sys_id", "N/A")
            display = records[0].get("display_name", records[0].get("asset_tag", "N/A"))
            return True, sys_id, display
    return False, "", r.text[:200]


def _calculate_depreciation(session, sys_id, BS):
    form_url = f"{SERVICENOW_BASE}/alm_hardware.do?sys_id={sys_id}"
    r = session.get(form_url, timeout=30)
    if r.status_code != 200:
        return False
    soup = BS(r.text, "html.parser")
    calc_link = soup.find("a", string=re.compile(r"Calculate\s+Depreciation", re.IGNORECASE))
    if not calc_link:
        return False
    action_id = calc_link.get("gsft_action_name", "")
    if not action_id:
        return False
    form_tag = soup.find("form", {"name": "alm_hardware.do"})
    if not form_tag:
        form_tag = soup.find("form", {"id": "alm_hardware.do"})
    if not form_tag:
        return False
    payload = {}
    for inp in form_tag.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        inp_type = inp.get("type", "").lower()
        if inp_type in ("hidden", "text", ""):
            payload[name] = inp.get("value", "")
    for sel in form_tag.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        selected = sel.find("option", selected=True)
        if selected:
            payload[name] = selected.get("value", "")
    payload["sys_action"] = action_id
    payload["sys_uniqueValue"] = sys_id
    form_action = form_tag.get("action", "alm_hardware.do")
    if not form_action.startswith("http"):
        form_action = f"{SERVICENOW_BASE}/{form_action.lstrip('/')}"
    r_dep = session.post(form_action, data=payload, allow_redirects=True, timeout=60)
    if r_dep.status_code == 200 and "login" not in r_dep.url.lower() and "oam" not in r_dep.url.lower():
        if "Unknown action" in r_dep.text:
            return False
        return True
    return False


def _parse_date(value):
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    value = str(value).strip()
    from datetime import date as _date
    if isinstance(value, _date):
        return value.strftime("%Y-%m-%d")
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y"]:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
    return str(value)


def _parse_date_with_time(value):
    d = _parse_date(value)
    return f"{d} 12:00" if d else ""


# ── Background upload worker ─────────────────────────────────────

def _upload_worker(job_id: str, assets_data: list[dict], params: dict):
    job = _jobs[job_id]
    _req, BS = _get_http()

    # Create HTTP session
    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })

    # SSO login
    job["phase"] = "login"
    try:
        ok = _login_sso(session, params["usuario"], params["senha"], _req, BS)
    except Exception as e:
        job["status"] = "error"
        job["error"] = f"Falha no login SSO: {e}"
        return
    if not ok:
        job["status"] = "error"
        job["error"] = "Login SSO falhou — verifique usuário e senha."
        return

    job["phase"] = "lookup"
    cache = {}

    # Resolve fixed reference fields once
    stockroom_id = _lookup_reference(session, "stockroom", params["stockroom"], cache, BS)
    depreciation_id = _lookup_reference(session, "depreciation", params["depreciation"], cache, BS)

    state_val = INSTALL_STATUS_MAP.get(params["state"].lower(), params["state"])
    substate_val = SUBSTATUS_MAP.get(params["substate"].lower(), params["substate"])
    currency_val = CURRENCY_MAP.get(params["cost_currency"], params["cost_currency"])

    job["phase"] = "inserting"

    for idx, asset in enumerate(assets_data):
        job["current"] = idx + 1

        # Resolve per-asset reference fields (EBS data)
        company_id = _lookup_reference(session, "company", asset.get("company", ""), cache, BS)
        model_id = _lookup_reference(session, "model", asset.get("model", ""), cache, BS)
        category_id = _lookup_reference(session, "model_category", asset.get("category", ""), cache, BS)

        # Monta record no formato exato da planilha (16 colunas)
        # Campos do EBS (variam por ativo)
        record = {
            "serial_number": asset.get("serial_number", ""),
            "model": model_id,
            "asset_tag": asset.get("tag_number", ""),
            "model_category": category_id,
            "company": company_id,
            "stockroom": stockroom_id,
            "purchase_date": _parse_date(asset.get("dpis") or asset.get("acquisition_date", "")),
            "depreciation_date": _parse_date_with_time(asset.get("dpis") or asset.get("acquisition_date", "")),
            "cost": str(asset.get("cost", "")).replace(",", ".") if asset.get("cost") else "",
        }
        if params.get("aisle_space"):
            record["aisle_space_location"] = params["aisle_space"]
        # Campos fixos (iguais para todos)
        record["install_status"] = state_val
        record["substatus"] = substate_val
        record["cost.currency_type"] = currency_val
        record["acquisition_method"] = params["acquisition_method"]
        record["expenditure_type"] = params["expenditure_type"]
        record["depreciation"] = depreciation_id

        # Remove empty values
        record = {k: v for k, v in record.items() if v}

        result_entry = {
            "idx": idx + 1,
            "serie": asset.get("serial_number", ""),
            "etiqueta": asset.get("tag_number", ""),
            "empresa": asset.get("company", ""),
        }

        try:
            ok, sys_id, display = _insert_record(session, record)
            if ok:
                job["ok_count"] += 1
                result_entry["status"] = "ok"
                result_entry["sys_id"] = sys_id[:20]
                result_entry["display"] = display

                if params.get("calc_depreciation") and sys_id and sys_id != "N/A":
                    dep_ok = _calculate_depreciation(session, sys_id, BS)
                    result_entry["depreciation"] = "ok" if dep_ok else "falhou"
                    if dep_ok:
                        job["dep_ok"] += 1
                    else:
                        job["dep_fail"] += 1
            else:
                job["err_count"] += 1
                result_entry["status"] = "erro"
                result_entry["detail"] = display
        except Exception as e:
            job["err_count"] += 1
            result_entry["status"] = "erro"
            result_entry["detail"] = str(e)[:200]

        job["results"].append(result_entry)

        if (idx + 1) % 50 == 0:
            time.sleep(2)

    job["status"] = "done"
    job["phase"] = "done"


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

@router.get("/recebimentos")
def list_for_upload(
    req: Request,
    status: str = "",
    q: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    lote: str = "",
    limit: int = 500,
):
    """Lista ativos do recebimento para seleção de upload ao ServiceNow."""
    require_permission(req, "servicenow", "view")

    from datetime import date

    with SessionLocal() as s:
        stmt = (
            select(ReceiptCycle)
            .join(Asset)
            .order_by(ReceiptCycle.id.desc())
            .limit(min(limit, 2000))
        )
        if status:
            stmt = stmt.where(ReceiptCycle.status == status.upper())
        if lote:
            stmt = stmt.where(ReceiptCycle.lot_number.ilike(f"%{lote}%"))
        if data_inicio:
            stmt = stmt.where(ReceiptCycle.received_date >= date.fromisoformat(data_inicio))
        if data_fim:
            stmt = stmt.where(ReceiptCycle.received_date <= date.fromisoformat(data_fim))
        if q:
            term = q.upper()
            stmt = stmt.where(or_(
                func.upper(Asset.serial_number).contains(term),
                func.upper(Asset.tag_number).contains(term),
                func.upper(Asset.asset_number).contains(term),
                func.upper(Asset.description).contains(term),
                func.upper(Asset.model).contains(term),
            ))

        cycles = s.scalars(stmt).unique().all()

        return {
            "recebimentos": [
                {
                    "cycle_id": c.id,
                    "asset_id": c.asset_id,
                    "empresa": c.asset.company or "",
                    "serie": c.asset.serial_number or "",
                    "etiqueta": c.asset.tag_number or "",
                    "ativo": c.asset.asset_number or c.asset.asset_id or "",
                    "modelo": c.asset.model or "",
                    "categoria": c.asset.category or "",
                    "descricao": c.asset.description or "",
                    "custo": str(c.asset.cost) if c.asset.cost else "",
                    "dpis": c.asset.dpis.isoformat() if c.asset.dpis else "",
                    "data_aquisicao": c.asset.acquisition_date.isoformat() if c.asset.acquisition_date else "",
                    "status": c.status,
                    "lote": c.lot_number or "",
                    "data_recebimento": c.received_date.isoformat() if c.received_date else "",
                }
                for c in cycles
            ]
        }


@router.get("/statuses")
def list_statuses(req: Request):
    """Lista status distintos dos recebimentos para filtro."""
    require_permission(req, "servicenow", "view")
    with SessionLocal() as s:
        rows = s.execute(
            select(ReceiptCycle.status)
            .distinct()
            .order_by(ReceiptCycle.status)
        ).scalars().all()
        return {"statuses": [r for r in rows if r]}


@router.post("/test-login")
def test_login(body: TestLoginIn, req: Request):
    """Testa conexão SSO com ServiceNow."""
    require_permission(req, "servicenow", "create")
    _req, BS = _get_http()
    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        ok = _login_sso(session, body.usuario, body.senha, _req, BS)
    except Exception as e:
        raise HTTPException(502, f"Erro SSO: {e}")
    if not ok:
        raise HTTPException(401, "Login SSO falhou — verifique usuário e senha.")
    return {"ok": True, "message": "Conexão SSO com ServiceNow estabelecida."}


@router.get("/session-status")
def sn_session_status(req: Request):
    """Check if the ServiceNow session is still valid (uses portal session cookies)."""
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")
    if not sn_cookies:
        return {"active": False, "reason": "no_session"}
    try:
        import requests as _req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = _req.get(
            f"{SERVICENOW_BASE}/api/now/table/sys_user?sysparm_limit=1",
            cookies=sn_cookies,
            timeout=15,
            verify=False,
            allow_redirects=False,
        )
        return {"active": r.status_code == 200}
    except Exception:
        return {"active": False, "reason": "error"}


@router.post("/upload")
def start_upload(body: UploadIn, req: Request):
    """Inicia upload de ativos para o ServiceNow em background."""
    sd = require_permission(req, "servicenow", "create")

    if not body.cycle_ids:
        raise HTTPException(400, "Selecione ao menos um ativo.")

    if not body.usuario or not body.senha:
        raise HTTPException(400, "Informe usuário e senha do ServiceNow.")

    # Verify dependencies
    _get_http()

    # Load asset data from DB
    assets_data = []
    with SessionLocal() as s:
        for cid in body.cycle_ids:
            c = s.get(ReceiptCycle, cid)
            if not c:
                continue
            a = c.asset
            assets_data.append({
                "cycle_id": c.id,
                "serial_number": a.serial_number or "",
                "tag_number": a.tag_number or "",
                "asset_number": a.asset_number or a.asset_id or "",
                "model": a.model or "",
                "category": a.category or "",
                "company": a.company or "",
                "description": a.description or "",
                "cost": str(a.cost) if a.cost else "",
                "dpis": a.dpis.isoformat() if a.dpis else "",
                "acquisition_date": a.acquisition_date.isoformat() if a.acquisition_date else "",
            })

    if not assets_data:
        raise HTTPException(400, "Nenhum ativo válido encontrado.")

    _cleanup_old_jobs()

    job_id = str(uuid.uuid4())[:8]
    _jobs[job_id] = {
        "status": "running",
        "phase": "starting",
        "total": len(assets_data),
        "current": 0,
        "ok_count": 0,
        "err_count": 0,
        "dep_ok": 0,
        "dep_fail": 0,
        "results": [],
        "error": "",
        "started_at": time.time(),
        "started_by": sd["username"],
    }

    params = {
        "usuario": body.usuario,
        "senha": body.senha,
        "stockroom": body.stockroom,
        "aisle_space": body.aisle_space,
        "cost_currency": body.cost_currency,
        "state": "In stock",
        "substate": "available",
        "acquisition_method": "Purchase",
        "expenditure_type": "Capex",
        "depreciation": "SL 5 Years",
        "calc_depreciation": body.calc_depreciation,
    }

    t = threading.Thread(target=_upload_worker, args=(job_id, assets_data, params), daemon=True)
    t.start()

    return {"ok": True, "job_id": job_id, "total": len(assets_data)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, req: Request):
    """Retorna status do job de upload."""
    require_permission(req, "servicenow", "view")
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job não encontrado.")
    return {
        "status": job["status"],
        "phase": job["phase"],
        "total": job["total"],
        "current": job["current"],
        "ok_count": job["ok_count"],
        "err_count": job["err_count"],
        "dep_ok": job["dep_ok"],
        "dep_fail": job["dep_fail"],
        "results": job["results"],
        "error": job["error"],
    }
