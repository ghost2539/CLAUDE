"""ServiceNow router — upload de ativos do recebimento para alm_hardware via SSO + JSONv2."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func, or_

from db.portal import SessionLocal, Asset, ReceiptCycle, Setting
from core.security import require_permission, get_session, check_rate_limit

_log = logging.getLogger("servicenow")

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
    "location": "cmn_location",
}

REFERENCE_NAME_FIELD = {
    "model": "name",
    "model_category": "name",
    "company": "name",
    "stockroom": "name",
    "depreciation": "name",
    "location": "name",
}

CURRENCY_MAP = {
    "BRL": "BRL", "R$": "BRL",
    "USD": "USD", "$": "USD",
    "UYU": "UYU", "$U": "UYU",
    "ARS": "ARS",
}

# Conversão do nome da empresa do EBS para o nome no ServiceNow (core_company).
# Ex.: CAMIC_FIS → Camicado. Ajustável via env SN_COMPANY_MAP="A=B;C=D".
COMPANY_ALIASES = {
    "CAMIC_FIS": "Camicado", "CAMICADO": "Camicado", "CAMIC": "Camicado",
    "YOUCOM": "Youcom", "YOU_FIS": "Youcom", "YCM_FIS": "Youcom",
    "ASHUA": "Ashua", "ASHUA_FIS": "Ashua",
    "RENNER": "Renner Brasil", "RENNER_FIS": "Renner Brasil", "LR_FIS": "Renner Brasil",
}
try:
    for _par in (os.getenv("SN_COMPANY_MAP", "") or "").split(";"):
        if "=" in _par:
            _k, _v = _par.split("=", 1)
            if _k.strip():
                COMPANY_ALIASES[_k.strip().upper()] = _v.strip()
except Exception:  # noqa: BLE001
    pass


def _normaliza_company(v: str) -> str:
    s = (v or "").strip()
    if not s:
        return s
    u = s.upper()
    if u in COMPANY_ALIASES:
        return COMPANY_ALIASES[u]
    if "CAMIC" in u:
        return "Camicado"
    if "YOU" in u or "YCM" in u:
        return "Youcom"
    if "ASHUA" in u:
        return "Ashua"
    if "RENNER" in u or u.startswith("LR"):
        return "Renner Brasil"
    return s

# ── Job tracking (in-memory) ─────────────────────────────────────
_jobs: dict[str, dict] = {}


def _cleanup_old_jobs():
    cutoff = time.time() - 3600
    expired = [k for k, v in _jobs.items() if v.get("started_at", 0) < cutoff]
    for k in expired:
        del _jobs[k]


# ── Pydantic models ──────────────────────────────────────────────

class UploadIn(BaseModel):
    cycle_ids: list[int] = []
    origem: str = "selecao"          # selecao | status | lista | itens
    status: str = ""                 # para origem=status
    identificadores: list[str] = []  # para origem=lista (consulta EBS)
    itens: list[dict] = []           # para origem=itens (linhas já consultadas)
    usuario: str = ""
    senha: str = ""
    stockroom: str = "SPARE - CD324"
    aisle_space: str = ""
    calc_depreciation: bool = True   # sempre calcula; mantido por compatibilidade


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


def _lookup_ref_field(session, table, name_field, value, like=False):
    """Busca 1 registro de uma tabela de referência por um campo de nome.
    Retorna o sys_id ou ''. Case-insensitive (padrão do ServiceNow)."""
    value = (value or "").strip()
    if not value:
        return ""
    op = "LIKE" if like else "="
    q = f"{name_field}{op}{value}"
    try:
        recs = _sn_query(session, table, q, "sys_id," + name_field, limit=1, display_value=False)
    except Exception:  # noqa: BLE001
        return ""
    if recs:
        sid = recs[0].get("sys_id", "")
        return sid.get("value", "") if isinstance(sid, dict) else (sid or "")
    return ""


# Campos de nome candidatos por tipo de referência. O 'model' costuma ser
# exibido por display_name (não 'name'), o que causava "modelo não encontrado"
# mesmo existindo no SN.
_REF_NAME_CANDIDATES = {
    "model": ["display_name", "name"],
    "model_category": ["name"],
    "stockroom": ["name"],
    "company": ["name"],
}


def _resolve_ref(session, field, value):
    """(sys_id, achou?) robusto para model/model_category/stockroom/company.
    Tenta correspondência exata (em vários campos de nome) e, por fim, LIKE."""
    value = (value or "").strip()
    if not value:
        return "", False
    table = REFERENCE_TABLE_MAP.get(field)
    if not table:
        return value, True
    campos = _REF_NAME_CANDIDATES.get(field, [REFERENCE_NAME_FIELD.get(field, "name")])
    for nf in campos:
        sid = _lookup_ref_field(session, table, nf, value, like=False)
        if sid:
            return sid, True
    # fallback tolerante (contém) — só se a exata falhar
    sid = _lookup_ref_field(session, table, campos[0], value, like=True)
    if sid:
        return sid, True
    return "", False


_TERMO_SN_RE = re.compile(r"^[A-Za-z0-9._/ -]{1,80}$")


def termo_sn(valor: str, rotulo: str = "valor") -> str:
    """Um valor vindo do usuário que vai entrar numa encoded query.

    A query do ServiceNow é montada por concatenação; `^`, `=`, `,` e `!`
    mudam o significado dela. Uma série bipada como
    `X^ORinstall_status=6` faria a busca casar outro registro — e a
    reserva e o envio cairiam no sys_id errado.

    Veio junto com os módulos de Separação, Atendimento, Inventário e
    Preparação, que a usam antes de concatenar. Acrescentada aqui sozinha,
    sem trocar o resto do arquivo: o servidor tem correções próprias neste
    módulo que não podem ser perdidas.
    """
    valor = (valor or "").strip()
    if not _TERMO_SN_RE.match(valor):
        raise HTTPException(400, f"{rotulo.capitalize()} com caracteres inválidos.")
    return valor


def _sn_query(session, table, query="", fields="", limit=50, offset=0, display_value=True):
    """Query ServiceNow via JSONv2 API (works with SSO cookies).
    Returns list of records."""
    params = [
        f"sysparm_action=getRecords",
        f"sysparm_record_count={limit}",
    ]
    if query:
        params.append(f"sysparm_query={query}")
    if fields:
        params.append(f"sysparm_fields={fields}")
    if offset:
        params.append(f"sysparm_first_row={offset}")
    if display_value:
        params.append("displayvalue=true")
    url = f"{SERVICENOW_BASE}/{table}.do?JSONv2&{'&'.join(params)}"
    r = session.get(url, headers={
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }, timeout=30)
    if r.status_code == 200 and "login" not in r.url.lower():
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            data = r.json()
            return data.get("records", [])
    if r.status_code == 401 or "login" in r.url.lower():
        raise HTTPException(409, "Sessão ServiceNow expirou. Reconecte.")
    raise HTTPException(502, f"ServiceNow retornou {r.status_code}")


def _sn_query_all(session, table, query="", fields="", page_size=500, max_records=10000):
    """Busca todos os registros paginando até esgotar a fila.

    O JSONv2 devolve no máximo sysparm_record_count por chamada; aqui
    percorremos as páginas via sysparm_first_row até o ServiceNow parar
    de retornar registros (ou até max_records, como trava de segurança).
    """
    todos = []
    offset = 0
    while offset < max_records:
        lote = _sn_query(session, table, query, fields, page_size, offset)
        if not lote:
            break
        todos.extend(lote)
        if len(lote) < page_size:
            break
        offset += page_size
    return todos


def _sn_update(session, table, sys_id, updates):
    """Update a record via JSONv2 API (works with SSO cookies)."""
    url = f"{SERVICENOW_BASE}/{table}.do?JSONv2&sysparm_action=update&sysparm_query=sys_id={sys_id}"
    r = session.post(url, json=updates, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
    }, timeout=30)
    if r.status_code == 200 and "login" not in r.url.lower():
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            data = r.json()
            records = data.get("records", [])
            if records:
                return True
    if r.status_code == 401 or "login" in r.url.lower():
        raise HTTPException(409, "Sessão ServiceNow expirou. Reconecte.")
    raise HTTPException(502, f"ServiceNow retornou {r.status_code}: {r.text[:200]}")


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


# Nome interno da ação e o texto do botão, em qualquer idioma. O nome
# interno ("...calculate_depreciation...") não é traduzido; o texto é.
_RE_ANCORA = re.compile(r"<a\b[^>]*>", re.I)
_RE_ACAO_NOME = re.compile(r'gsft_action_name\s*=\s*["\']([^"\']+)["\']', re.I)
_RE_DEPRECIA = re.compile(r"deprecia", re.I)      # depreciation / depreciação / depreciación


def achar_acao_depreciacao(html: str) -> str:
    """Nome da ação de calcular depreciação na página, ou "".

    Procura em duas frentes: o nome interno da ação (que o ServiceNow não
    traduz) e o texto visível do link (que ele traduz). Trabalha sobre o
    HTML cru de propósito — assim dá para verificar com uma página de
    exemplo em cada idioma, sem depender do parser.
    """
    texto = html or ""
    candidatos: list[tuple[int, str]] = []
    for m in _RE_ANCORA.finditer(texto):
        tag = m.group(0)
        acao = _RE_ACAO_NOME.search(tag)
        if not acao:
            continue
        nome = acao.group(1)
        # Texto do link: até o </a> correspondente (basta o suficiente).
        fim = texto.find("</a>", m.end())
        rotulo = re.sub(r"<[^>]*>", " ", texto[m.end():fim] if fim > 0 else "")
        casa_nome = bool(_RE_DEPRECIA.search(nome))
        casa_rotulo = bool(_RE_DEPRECIA.search(rotulo))
        if not (casa_nome or casa_rotulo):
            continue
        # Prefere quem também fala em calcular: "Calculate Depreciation",
        # "Calcular depreciação" — e não "Depreciation schedule".
        peso = 2 if re.search(r"calc", nome + " " + rotulo, re.I) else 1
        candidatos.append((peso, nome))
    if not candidatos:
        return ""
    candidatos.sort(key=lambda x: -x[0])
    return candidatos[0][1]


def _depreciacao_passos(session, sys_id, BS, executar: bool = True) -> dict:
    """O cálculo da depreciação, passo a passo e com o motivo da parada.

    O caminho é o formulário da interface, não a API: são cinco pontos
    onde ele pode parar (página não abre, link ausente, formulário
    ausente, sessão caiu no login, ação desconhecida). Devolver só
    "não deu" transforma um diagnóstico de dois minutos em três rodadas
    de tentativa e erro — foi exatamente o que aconteceu.
    """
    passos: list[dict] = []
    resultado = {"sys_id": sys_id, "ok": False, "passos": passos, "motivo": ""}

    def parar(motivo: str) -> dict:
        resultado["motivo"] = motivo
        return resultado

    form_url = f"{SERVICENOW_BASE}/alm_hardware.do?sys_id={sys_id}"
    try:
        r = session.get(form_url, timeout=30)
    except Exception as exc:  # noqa: BLE001 — rede/certificado com a causa
        passos.append({"passo": "abrir formulário", "ok": False, "detalhe": str(exc)[:200]})
        return parar(f"não foi possível abrir o formulário do ativo: {exc}")
    caiu_no_login = "login" in (getattr(r, "url", "") or "").lower() or \
                    "oam" in (getattr(r, "url", "") or "").lower()
    passos.append({"passo": "abrir formulário", "ok": r.status_code == 200 and not caiu_no_login,
                   "http": r.status_code, "url": getattr(r, "url", ""),
                   "tamanho": len(getattr(r, "text", "") or "")})
    if r.status_code != 200:
        return parar(f"o formulário do ativo respondeu HTTP {r.status_code}")
    if caiu_no_login:
        return parar("a sessão do portal não abre o formulário do ServiceNow "
                     "(a resposta foi a tela de login). O cálculo da depreciação "
                     "usa a interface, não a API.")

    soup = BS(r.text, "html.parser")
    # A ação é achada pelo NOME INTERNO e pelo texto em qualquer idioma: o
    # ServiceNow de cada usuário pode estar em português, e procurar só por
    # "Calculate Depreciation" fazia a depreciação não rodar para quem usa
    # a interface traduzida.
    action_id = achar_acao_depreciacao(r.text)
    passos.append({"passo": "achar a ação de calcular depreciação",
                   "ok": bool(action_id), "acao": action_id})
    if not action_id:
        return parar("o formulário abriu, mas não tem a ação de calcular "
                     "depreciação (nem em inglês nem em português) — "
                     "verifique se o usuário do portal tem esse botão no "
                     "ServiceNow.")
    form_tag = soup.find("form", {"name": "alm_hardware.do"})
    if not form_tag:
        form_tag = soup.find("form", {"id": "alm_hardware.do"})
    passos.append({"passo": "achar o formulário do ativo", "ok": bool(form_tag)})
    if not form_tag:
        return parar("a página não trouxe o formulário do ativo.")
    if not executar:
        resultado["ok"] = True
        resultado["motivo"] = ("o caminho está inteiro; o cálculo rodaria "
                               "(diagnóstico não executa a ação)")
        return resultado
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
    try:
        r_dep = session.post(form_action, data=payload, allow_redirects=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        passos.append({"passo": "executar a ação", "ok": False, "detalhe": str(exc)[:200]})
        return parar(f"falha de rede ao executar a ação: {exc}")
    url_dep = (getattr(r_dep, "url", "") or "").lower()
    desconhecida = "Unknown action" in (getattr(r_dep, "text", "") or "")
    ok = (r_dep.status_code == 200 and "login" not in url_dep
          and "oam" not in url_dep and not desconhecida)
    passos.append({"passo": "executar a ação", "ok": ok, "http": r_dep.status_code,
                   "url": getattr(r_dep, "url", ""),
                   "acao_desconhecida": desconhecida})
    if ok:
        resultado["ok"] = True
        resultado["motivo"] = "depreciação calculada"
        return resultado
    if desconhecida:
        return parar('o ServiceNow respondeu "Unknown action" à ação de calcular.')
    if "login" in url_dep or "oam" in url_dep:
        return parar("a sessão caiu no login ao executar a ação.")
    return parar(f"a ação respondeu HTTP {r_dep.status_code}")


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

    # Autenticação: preferir a sessão SSO do portal (cookies já validados no
    # login). Só cai para usuário/senha se, por algum motivo, não houver cookies.
    job["phase"] = "login"
    if params.get("sn_cookies"):
        session.cookies.update(params["sn_cookies"])
    else:
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

    # Referências fixas (iguais para todos os ativos)
    stockroom_id, stockroom_ok = _resolve_ref(session, "stockroom", params["stockroom"])
    depreciation_id = _lookup_reference(session, "depreciation", params["depreciation"], cache, BS)
    aisle = (params.get("aisle_space") or "").strip()

    state_val = INSTALL_STATUS_MAP.get(params["state"].lower(), params["state"])
    substate_val = SUBSTATUS_MAP.get(params["substate"].lower(), params["substate"])
    currency_val = CURRENCY_MAP.get("BRL", "BRL")  # sempre BRL

    # Pré-carrega os registros JÁ existentes no ServiceNow (para ATUALIZAR em
    # vez de inserir). Mapas por asset_tag e por serial (maiúsculas).
    from types import SimpleNamespace
    itens_dup = [SimpleNamespace(asset_tag=a.get("tag_number", ""),
                                 serial_number=a.get("serial_number", ""))
                 for a in assets_data]
    por_tag, por_ser = {}, {}
    try:
        for rec in _hardware_records(session, itens_dup):
            if rec.get("asset_tag"):
                por_tag[rec["asset_tag"].upper()] = rec
            if rec.get("serial_number"):
                por_ser[rec["serial_number"].upper()] = rec
    except Exception:  # noqa: BLE001 — se falhar, segue tratando todos como novos
        pass

    job["phase"] = "inserting"

    for idx, asset in enumerate(assets_data):
        job["current"] = idx + 1

        result_entry = {
            "idx": idx + 1,
            "serie": asset.get("serial_number", ""),
            "etiqueta": asset.get("tag_number", ""),
            "empresa": asset.get("company", ""),
        }

        # ── Validações — NÃO sobe/atualiza o ativo se algo estiver irregular ─
        motivos: list[str] = []

        if asset.get("_ebs_nao_encontrado"):
            job["err_count"] += 1
            result_entry["status"] = "erro"
            result_entry["motivos"] = ["Ativo não encontrado no EBS"]
            result_entry["detail"] = "Ativo não encontrado no EBS"
            job["results"].append(result_entry)
            continue

        tag_u = str(asset.get("tag_number") or "").strip().upper()
        ser_u = str(asset.get("serial_number") or "").strip().upper()
        existente = (por_tag.get(tag_u) if tag_u else None) or (por_ser.get(ser_u) if ser_u else None)

        model_id, model_ok = _resolve_ref(session, "model", asset.get("model", ""))
        if not model_ok:
            motivos.append("Regularizar Modelo (Não encontrou o modelo no SN)")

        category_id, category_ok = _resolve_ref(session, "model_category", asset.get("category", ""))
        if not category_ok:
            motivos.append("Regularizar Categoria de Hardware (Não encontrou a categoria)")

        if not stockroom_ok:
            motivos.append("Regularizar Stockroom (Não encontrou o estoque)")

        if not aisle:
            motivos.append("Regularizar Espaço e Corredor (Não encontrou o Aisle and Space)")

        if motivos:
            job["err_count"] += 1
            result_entry["status"] = "erro"
            result_entry["motivos"] = motivos
            result_entry["detail"] = "; ".join(motivos)
            job["results"].append(result_entry)
            continue

        company_nome = _normaliza_company(asset.get("company", ""))
        company_id = _lookup_reference(session, "company", company_nome, cache, BS)

        # Campos de subida (aplicados tanto na inclusão quanto na atualização):
        # o ativo está conosco, então stockroom/company/aisle/status/depreciação
        # passam a refletir os dados de subida.
        payload = {
            "model": model_id,
            "model_category": category_id,
            "company": company_id,
            "stockroom": stockroom_id,
            "aisle_space_location": aisle,
            "install_status": state_val,
            "substatus": substate_val,
            "depreciation": depreciation_id,
            "purchase_date": _parse_date(asset.get("dpis") or asset.get("acquisition_date", "")),
            "depreciation_date": _parse_date_with_time(asset.get("dpis") or asset.get("acquisition_date", "")),
            "cost": str(asset.get("cost", "")).replace(",", ".") if asset.get("cost") else "",
            "cost.currency_type": currency_val,
            "acquisition_method": params["acquisition_method"],
            "expenditure_type": params["expenditure_type"],
        }

        try:
            if existente:
                # ── ATUALIZA o registro existente ───────────────────────
                sys_id = existente["sys_id"]
                update = {k: v for k, v in payload.items() if v}
                # Completa/corrige identificadores divergentes ou ausentes.
                if asset.get("asset_tag") or asset.get("tag_number"):
                    update["asset_tag"] = asset.get("tag_number", "") or existente.get("asset_tag", "")
                if asset.get("serial_number"):
                    update["serial_number"] = asset.get("serial_number", "")
                _sn_update(session, HARDWARE_TABLE, sys_id, update)
                job["ok_count"] += 1
                result_entry["status"] = "ok"
                result_entry["sys_id"] = str(sys_id)[:20]
                result_entry["display"] = "Atualizado (já existia no SN)"
                if sys_id:
                    dep_ok = _calculate_depreciation(session, sys_id, BS)
                    result_entry["depreciation"] = "ok" if dep_ok else "falhou"
                    job["dep_ok" if dep_ok else "dep_fail"] += 1
            else:
                # ── INSERE novo registro ────────────────────────────────
                record = dict(payload)
                record["serial_number"] = asset.get("serial_number", "")
                record["asset_tag"] = asset.get("tag_number", "")
                record = {k: v for k, v in record.items() if v}
                ok, sys_id, display = _insert_record(session, record)
                if ok:
                    job["ok_count"] += 1
                    result_entry["status"] = "ok"
                    result_entry["sys_id"] = sys_id[:20]
                    result_entry["display"] = display
                    if sys_id and sys_id != "N/A":
                        dep_ok = _calculate_depreciation(session, sys_id, BS)
                        result_entry["depreciation"] = "ok" if dep_ok else "falhou"
                        job["dep_ok" if dep_ok else "dep_fail"] += 1
                else:
                    job["err_count"] += 1
                    result_entry["status"] = "erro"
                    result_entry["motivos"] = [display]
                    result_entry["detail"] = display
        except Exception as e:
            job["err_count"] += 1
            result_entry["status"] = "erro"
            result_entry["motivos"] = [str(e)[:200]]
            result_entry["detail"] = str(e)[:200]

        job["results"].append(result_entry)

        if (idx + 1) % 50 == 0:
            time.sleep(2)

    job["status"] = "done"
    job["phase"] = "done"


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

def config_gestao_ativos() -> dict:
    from db.portal import SessionLocal as _Portal, Setting
    with _Portal() as s:
        row = s.get(Setting, "gestao_ativos")
    cfg = dict(GESTAO_ATIVOS_PADRAO)
    for k, v in ((row.value or {}) if row else {}).items():
        if k in cfg and isinstance(v, list):
            cfg[k] = [str(x).strip() for x in v if str(x).strip()]
    return cfg


# Listas que as telas de Entrada, Saída e Movimentação interna oferecem.
# Vêm de Configuração → Configuração Módulos (chave gestao_ativos) e
# valem na hora, sem reiniciar. Os padrões são os de sempre.
GESTAO_ATIVOS_PADRAO = {
    "estoques": ["SPARE - CD324", "SPARE-ADM15", "SPARE-CD504"],
    "corredores": [],
    "anotacoes": [],
}


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
def list_statuses(req: Request, sem_removido: bool = False):
    """Lista status distintos dos recebimentos. Deduplica variações de caixa/
    espaço e, com sem_removido=true, oculta os status 'Removido'."""
    require_permission(req, "servicenow", "view")
    with SessionLocal() as s:
        rows = s.execute(select(ReceiptCycle.status).distinct()).scalars().all()
    vistos: dict[str, str] = {}
    for r in rows:
        if not r:
            continue
        limpo = r.strip()
        norm = limpo.upper()
        if sem_removido and norm in ("REMOVIDO", "REMOVIDOS"):
            continue
        if norm and norm not in vistos:
            vistos[norm] = limpo
    return {"statuses": sorted(vistos.values(), key=lambda x: x.upper())}


@router.post("/test-login")
def test_login(body: TestLoginIn, req: Request):
    """Testa conexão SSO com ServiceNow e salva cookies na sessão do portal.

    Tenta primeiro com o proxy configurado; se o proxy estiver inacessível,
    repete a tentativa em conexão direta.
    """
    sd = require_permission(req, "servicenow", "create")
    _req, BS = _get_http()

    tentativas = [SN_PROXY, None] if SN_PROXY else [None]
    erros = []

    for proxy in tentativas:
        session = _req.Session()
        session.verify = False
        if proxy:
            session.proxies = {"https": proxy, "http": proxy}
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        origem = f"proxy {proxy}" if proxy else "conexão direta"
        try:
            ok = _login_sso(session, body.usuario, body.senha, _req, BS)
        except Exception as e:
            msg = f"{origem}: {type(e).__name__}: {e}"
            print(f"[SSO] Falha via {msg}")
            erros.append(msg)
            continue
        if not ok:
            raise HTTPException(401, "Login SSO falhou — verifique usuário e senha.")
        sd["sn_cookies"] = dict(session.cookies)
        return {
            "ok": True,
            "message": f"Conexão SSO com ServiceNow estabelecida ({origem}).",
            "via": origem,
        }

    raise HTTPException(502, "Erro SSO — " + " | ".join(erros))


@router.get("/proxy-check")
def proxy_check(req: Request):
    """Diagnóstico: testa se o proxy e o ServiceNow estão acessíveis."""
    require_permission(req, "servicenow", "view")
    _req, _ = _get_http()

    resultado = {"proxy_configurado": SN_PROXY or "(nenhum)"}

    for nome, proxy in (("com_proxy", SN_PROXY), ("direto", None)):
        if nome == "com_proxy" and not SN_PROXY:
            continue
        sess = _req.Session()
        sess.verify = False
        if proxy:
            sess.proxies = {"https": proxy, "http": proxy}
        try:
            r = sess.get(f"{SERVICENOW_BASE}/login.do", timeout=15, allow_redirects=True)
            resultado[nome] = {"ok": True, "status": r.status_code, "url_final": r.url}
        except Exception as e:
            resultado[nome] = {"ok": False, "erro": f"{type(e).__name__}: {e}"}
        finally:
            sess.close()

    return resultado


# O keep-alive precisa ser AUDITÁVEL. Sem isto, "a sessão está expirando"
# não tem como ser investigado: o selo fica verde, o ping roda, e ninguém
# sabe se ele está realmente renovando ou se falha há horas. Estes campos
# ficam na sessão do portal (memória do processo) e saem no diagnóstico.
CHAVE_KEEPALIVE = "sn_keepalive"


def _registrar_ping(sd: dict, estado: str, detalhe: str = "") -> dict:
    """Guarda o resultado do ping e devolve o histórico curto."""
    from datetime import datetime, timezone
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    info = sd.get(CHAVE_KEEPALIVE) or {}
    info["ultimo_ping"] = agora
    info["ultimo_estado"] = estado
    info["ultimo_detalhe"] = detalhe
    info["pings"] = int(info.get("pings") or 0) + 1
    if estado == "ativa":
        info["ultima_renovacao"] = agora
        info["renovacoes"] = int(info.get("renovacoes") or 0) + 1
        info["falhas_seguidas"] = 0
    else:
        info["falhas_seguidas"] = int(info.get("falhas_seguidas") or 0) + 1
        info["ultima_falha"] = agora
        info["ultima_falha_motivo"] = f"{estado}: {detalhe}" if detalhe else estado
    sd[CHAVE_KEEPALIVE] = info
    return info


@router.get("/session-status")
def sn_session_status(req: Request):
    """Diz se a sessão do ServiceNow está viva — e a mantém viva.

    É o keep-alive do usuário NOMINAL: o portal guarda os cookies de SSO de
    quem entrou e, a cada ping, faz uma chamada autenticada ao ServiceNow.
    Essa chamada é o que reinicia o relógio de inatividade de lá; a resposta
    traz os cookies renovados, que voltam para a sessão do portal.

    Três estados, e a diferença entre os dois últimos é o que faltava:

    *   `ativa`        — respondeu autenticado; os cookies foram renovados.
    *   `expirada`     — o ServiceNow mandou para a tela de login.
    *   `desconhecida` — o ping não chegou lá (rede, proxy, timeout).

    `desconhecida` era devolvido como ATIVA. A intenção era não derrubar o
    selo por um soluço de rede; o efeito era pior: com o ping falhando há
    horas, o selo continuava verde, nada renovava, e a sessão morria sem que
    nada na tela indicasse o porquê. Agora o estado sai como é, e a tela
    mostra amarelo em vez de verde.
    """
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")
    if not sn_cookies:
        return {"active": False, "estado": "sem_sessao", "reason": "no_session",
                "keepalive": sd.get(CHAVE_KEEPALIVE) or {}}
    try:
        from integracoes import http as http_saida
        sess = http_saida.sessao("servicenow", proxy=SN_PROXY or None)
        sess.cookies.update(sn_cookies)
        url = f"{SERVICENOW_BASE}/sys_user.do?JSONv2&sysparm_action=getRecords&sysparm_record_count=1"
        r = sess.get(url, headers={
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=15, allow_redirects=True)
        # A sessão está viva enquanto NÃO for redirecionada para a tela de
        # login do SSO. Não exigimos content-type JSON: alguns endpoints
        # válidos respondem text/html mesmo autenticados — isso causava o
        # falso "sessão expirada" enquanto as consultas funcionavam.
        final = (r.url or "").lower()
        redirecionou_login = (
            "login.do" in final or "ssologin" in final
            or "/login" in final or "oauth" in final
        )
        expirado = r.status_code in (401, 403) or redirecionou_login
        if not expirado:
            # O que mantém a sessão viva: os cookies que VOLTARAM. Sem
            # guardá-los, o próximo ping reenvia os antigos e a renovação
            # nunca se acumula.
            sd["sn_cookies"] = dict(sess.cookies)
            info = _registrar_ping(sd, "ativa")
            return {"active": True, "estado": "ativa", "keepalive": info}
        info = _registrar_ping(sd, "expirada", f"HTTP {r.status_code} → {final[:120]}")
        return {"active": False, "estado": "expirada", "reason": "expired",
                "keepalive": info}
    except Exception as exc:  # noqa: BLE001
        _log.warning("keep-alive do ServiceNow não completou: %s", exc)
        info = _registrar_ping(sd, "desconhecida", f"{type(exc).__name__}: {str(exc)[:160]}")
        # NÃO é "ativa". Erro de rede repetido significa que nada está sendo
        # renovado, e é isso que a tela precisa mostrar.
        return {"active": False, "estado": "desconhecida", "reason": "probe_error",
                "keepalive": info}


@router.get("/keepalive-diagnostico")
def sn_keepalive_diagnostico(req: Request):
    """O histórico do keep-alive desta sessão, para responder 'está rodando?'.

    Sem isto, a única evidência de que o keep-alive funciona é a ausência de
    reclamação — e quando a reclamação vem, não há o que olhar.
    """
    sd = get_session(req)
    info = dict(sd.get(CHAVE_KEEPALIVE) or {})
    info["tem_cookies"] = bool(sd.get("sn_cookies"))
    info["usuario"] = sd.get("username", "")
    # O intervalo que a tela usa, para conferir se bate com o que se vê nos
    # carimbos: pings espaçados demais são ping que não está rodando.
    info["intervalo_esperado_segundos"] = 180
    return info


@router.post("/upload")
def start_upload(body: UploadIn, req: Request):
    """Inicia upload de ativos para o ServiceNow em background."""
    sd = require_permission(req, "servicenow", "create")

    # Aisle and Space é obrigatório na entrada de estoque.
    if not (body.aisle_space or "").strip():
        raise HTTPException(400, "Informe o Aisle and Space (obrigatório).")

    # A autenticação usa a sessão do ServiceNow do próprio login do portal
    # (SSO). Não são mais solicitados usuário/senha nesta tela.
    sn_cookies = sd.get("sn_cookies")
    if not sn_cookies and not (body.usuario and body.senha):
        raise HTTPException(
            409,
            "Sessão ServiceNow não ativa. Saia e entre novamente no portal "
            "(Logon AD) para renovar o acesso ao ServiceNow.",
        )

    # Verify dependencies
    _get_http()

    def _asset_dict_from_cycle(c) -> dict:
        a = c.asset
        return {
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
        }

    origem = (body.origem or "selecao").lower()
    assets_data: list[dict] = []

    if origem == "itens":
        # Linhas já consultadas/selecionadas na pré-visualização.
        for it in (body.itens or []):
            assets_data.append({
                "serial_number": it.get("serial_number", ""),
                "tag_number": it.get("tag_number", ""),
                "asset_number": it.get("asset_number", ""),
                "model": it.get("model", ""),
                "category": it.get("category", ""),
                "company": it.get("company", ""),
                "description": it.get("description", ""),
                "cost": str(it.get("cost") or ""),
                "dpis": it.get("dpis", "") or "",
                "acquisition_date": it.get("acquisition_date", "") or "",
            })
        if not assets_data:
            raise HTTPException(400, "Nenhum ativo selecionado.")
    elif origem == "lista":
        # Consulta o EBS pelos identificadores e converte via cadastro de
        # modelos (classificação). Sem cadastro, o ativo sobe sem modelo/
        # categoria e o relatório orienta a regularizar.
        from routers.consulta import _query_assets, QueryIn
        from routers.helpers import apply_class
        ids = [str(x).strip() for x in (body.identificadores or []) if str(x).strip()]
        if not ids:
            raise HTTPException(400, "Informe ao menos um ativo na lista.")
        try:
            res = _query_assets(QueryIn(identificadores=ids), req)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, "Falha ao consultar o EBS: %s" % exc)
        with SessionLocal() as s:
            for r in res.get("resultados", []):
                if r.get("encontrado"):
                    r = apply_class(s, r)
                    assets_data.append({
                        "serial_number": r.get("numero_serie", ""),
                        "tag_number": r.get("etiqueta", ""),
                        "asset_number": r.get("ativo") or r.get("imobilizado", ""),
                        "model": r.get("modelo", ""),
                        "category": r.get("categoria", ""),
                        "company": r.get("empresa", ""),
                        "description": r.get("descricao", ""),
                        "cost": str(r.get("custo_asset") or ""),
                        "dpis": r.get("dpis", "") or "",
                        "acquisition_date": "",
                    })
                else:
                    assets_data.append({
                        "serial_number": "", "tag_number": r.get("pesquisado", ""),
                        "asset_number": "", "model": "", "category": "",
                        "company": "", "description": "", "cost": "", "dpis": "",
                        "acquisition_date": "", "_ebs_nao_encontrado": True,
                    })
    elif origem == "status":
        st = (body.status or "").strip()
        if not st:
            raise HTTPException(400, "Selecione o status da base de recebimento.")
        alvo = st.upper()
        with SessionLocal() as s:
            rows = s.scalars(select(ReceiptCycle)).all()
            for c in rows:
                if not c.asset:
                    continue
                stt = (c.status or "").strip().upper()
                if stt in ("REMOVIDO", "REMOVIDOS"):
                    continue  # nunca sobe removidos
                if alvo != "__TODOS__" and stt != alvo:
                    continue
                assets_data.append(_asset_dict_from_cycle(c))
    else:  # selecao
        if not body.cycle_ids:
            raise HTTPException(400, "Selecione ao menos um ativo.")
        with SessionLocal() as s:
            for cid in body.cycle_ids:
                c = s.get(ReceiptCycle, cid)
                if c and c.asset:
                    assets_data.append(_asset_dict_from_cycle(c))

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
        "sn_cookies": sn_cookies,
        "stockroom": body.stockroom,
        "aisle_space": body.aisle_space,
        "state": "In stock",
        "substate": "available",
        "acquisition_method": "Purchase",
        "expenditure_type": "Capex",
        "depreciation": "SL 5 Years",
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


# ═══════════════════════════════════════════════════════════════════
# INCIDENTES — consulta a tabela de incidentes via Table API
# ═══════════════════════════════════════════════════════════════════

INCIDENT_TABLE = "incident"
DEFAULT_QUEUE = "TI_N2_FLD_RNR_LOJAS_SPARE"


def _sn_session_from_portal(req):
    """Retorna requests.Session com cookies SN do portal (login SSO já feito)."""
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")
    if not sn_cookies:
        raise HTTPException(409, "Sessão ServiceNow não ativa. Saia e entre novamente no portal (Logon AD).")
    _req, _ = _get_http()
    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    session.cookies.update(sn_cookies)
    return session


def _sn_session_from_cookies(sn_cookies: dict):
    """Monta uma requests.Session a partir de cookies SN salvos (usada pela
    rotina automática, fora de um request). Retorna None se sem cookies."""
    if not sn_cookies:
        return None
    _req, _ = _get_http()
    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    session.cookies.update(sn_cookies)
    return session


def _sn_session_valida(session) -> bool:
    """Confere se a sessão SN ainda responde (não caiu no login)."""
    try:
        url = f"{SERVICENOW_BASE}/sys_user.do?JSONv2&sysparm_action=getRecords&sysparm_record_count=1"
        r = session.get(url, headers={"Accept": "application/json",
                                      "X-Requested-With": "XMLHttpRequest"}, timeout=15)
        final = (r.url or "").lower()
        return r.status_code == 200 and "login.do" not in final and "/login" not in final
    except Exception:  # noqa: BLE001
        return False


@router.get("/incidents")
def list_incidents(
    req: Request,
    queue: str = DEFAULT_QUEUE,
    state: str = "",
    priority: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
):
    """Lista incidentes de uma fila (assignment_group) do ServiceNow."""
    require_permission(req, "servicenow", "view")

    _req, _ = _get_http()
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")

    if not sn_cookies:
        raise HTTPException(409, "Sessão ServiceNow não ativa. Faça login na aba Entrada de Estoque.")

    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    session.cookies.update(sn_cookies)

    query_parts = [f"assignment_group.name={queue}"]
    if state:
        query_parts.append(f"state={state}")
    if priority:
        query_parts.append(f"priority={priority}")
    if q:
        query_parts.append(f"short_descriptionLIKE{q}^ORnumberLIKE{q}^ORcaller_id.nameLIKE{q}")
    query_parts.append("ORDERBYDESCsys_created_on")
    sn_query = "^".join(query_parts)

    fields = (
        "sys_id,number,short_description,state,priority,urgency,"
        "category,subcategory,assignment_group,assigned_to,"
        "caller_id,opened_at,sys_created_on,sys_updated_on,"
        "resolved_at,closed_at,close_code,close_notes"
    )

    incidents = _sn_query(session, INCIDENT_TABLE, sn_query, fields, min(limit, 200), offset)
    total = len(incidents)

    return {
        "incidents": incidents,
        "total": total,
        "limit": limit,
        "offset": offset,
        "queue": queue,
    }


@router.get("/incidents/count")
def count_incidents(
    req: Request,
    queue: str = DEFAULT_QUEUE,
):
    """Retorna contagem de incidentes por estado na fila."""
    require_permission(req, "servicenow", "view")

    _req, _ = _get_http()
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")

    if not sn_cookies:
        raise HTTPException(409, "Sessão ServiceNow não ativa. Faça login na aba Entrada de Estoque.")

    session = _req.Session()
    session.verify = False
    if SN_PROXY:
        session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    })
    session.cookies.update(sn_cookies)

    sn_query = f"assignment_group.name={queue}"

    records = _sn_query(session, INCIDENT_TABLE, sn_query, "sys_id,state", limit=5000, display_value=True)

    by_state: dict = {}
    for rec in records:
        state_label = rec.get("state", "Desconhecido") or "Desconhecido"
        by_state[state_label] = by_state.get(state_label, 0) + 1

    total = len(records)
    return {"queue": queue, "total": total, "by_state": by_state}


# ═══════════════════════════════════════════════════════════════════
# CHECAGEM DE EXISTÊNCIA NO SERVICENOW (alm_hardware)
# ═══════════════════════════════════════════════════════════════════

class ExisteItem(BaseModel):
    id: int | None = None
    asset_tag: str = ""
    serial_number: str = ""


class ExisteIn(BaseModel):
    itens: list[ExisteItem] = []


def _hardware_existentes(session, itens: list) -> tuple[set, set]:
    """Consulta alm_hardware e devolve (tags_existentes, seriais_existentes)
    em MAIÚSCULAS, a partir de uma lista de itens {asset_tag, serial_number}.
    Consulta em blocos para não estourar a URL."""
    def _chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    tags_ok, ser_ok = set(), set()
    for grupo in _chunks(itens, 50):
        tags = [str(getattr(i, "asset_tag", "") or "").strip() for i in grupo]
        sers = [str(getattr(i, "serial_number", "") or "").strip() for i in grupo]
        tags = [t for t in tags if t]
        sers = [s for s in sers if s]
        partes = []
        if tags:
            partes.append("asset_tagIN" + ",".join(tags))
        if sers:
            partes.append("serial_numberIN" + ",".join(sers))
        if not partes:
            continue
        q = "^OR".join(partes) if len(partes) == 2 else partes[0]
        recs = _sn_query(session, HARDWARE_TABLE, q,
                         "asset_tag,serial_number", limit=200, display_value=False)
        for r in recs:
            t = str(r.get("asset_tag") or "").strip().upper()
            s = str(r.get("serial_number") or "").strip().upper()
            if t:
                tags_ok.add(t)
            if s:
                ser_ok.add(s)
    return tags_ok, ser_ok


def _hardware_records(session, itens: list) -> list[dict]:
    """Busca no alm_hardware os registros que casam por asset_tag/serial de uma
    lista de itens. Retorna dicts com sys_id, asset_tag, serial_number."""
    def _chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def _plain(v):
        return v.get("value", "") if isinstance(v, dict) else (v or "")

    out = []
    for grupo in _chunks(itens, 50):
        tags = [str(getattr(i, "asset_tag", "") or "").strip() for i in grupo]
        sers = [str(getattr(i, "serial_number", "") or "").strip() for i in grupo]
        tags = [t for t in tags if t]
        sers = [s for s in sers if s]
        partes = []
        if tags:
            partes.append("asset_tagIN" + ",".join(tags))
        if sers:
            partes.append("serial_numberIN" + ",".join(sers))
        if not partes:
            continue
        q = "^OR".join(partes) if len(partes) == 2 else partes[0]
        recs = _sn_query(session, HARDWARE_TABLE, q,
                         "sys_id,asset_tag,serial_number", limit=200, display_value=False)
        for r in recs:
            out.append({
                "sys_id": _plain(r.get("sys_id")),
                "asset_tag": str(_plain(r.get("asset_tag"))).strip(),
                "serial_number": str(_plain(r.get("serial_number"))).strip(),
            })
    return out


class EntradaPreviewIn(BaseModel):
    origem: str = "status"           # status | lista
    status: str = ""
    identificadores: list[str] = []


@router.post("/entrada/preview")
def entrada_preview(body: EntradaPreviewIn, req: Request):
    """Carrega os ativos para conferência ANTES de subir. Retorna as linhas já
    convertidas (modelo/categoria/company) e se já existem no ServiceNow."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)
    origem = (body.origem or "status").lower()
    rows: list[dict] = []

    if origem == "lista":
        from routers.consulta import _query_assets, QueryIn
        from routers.helpers import apply_class
        ids = [str(x).strip() for x in (body.identificadores or []) if str(x).strip()]
        if not ids:
            raise HTTPException(400, "Informe ao menos um ativo na lista.")
        try:
            res = _query_assets(QueryIn(identificadores=ids), req)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, "Falha ao consultar o EBS: %s" % exc)
        with SessionLocal() as s:
            for r in res.get("resultados", []):
                if r.get("encontrado"):
                    r = apply_class(s, r)
                    rows.append({
                        "encontrado": True,
                        "serial_number": r.get("numero_serie", ""),
                        "tag_number": r.get("etiqueta", ""),
                        "asset_number": r.get("ativo") or r.get("imobilizado", ""),
                        "model": r.get("modelo", ""),
                        "category": r.get("categoria", ""),
                        "company": _normaliza_company(r.get("empresa", "")),
                        "description": r.get("descricao", ""),
                        "cost": str(r.get("custo_asset") or ""),
                        "dpis": r.get("dpis", "") or "",
                        "acquisition_date": "",
                    })
                else:
                    rows.append({"encontrado": False, "tag_number": r.get("pesquisado", ""),
                                 "serial_number": "", "model": "", "category": "",
                                 "company": "", "description": ""})
    elif origem == "status":
        st = (body.status or "").strip()
        if not st:
            raise HTTPException(400, "Selecione o status.")
        alvo = st.upper()
        with SessionLocal() as s:
            for c in s.scalars(select(ReceiptCycle)).all():
                if not c.asset:
                    continue
                stt = (c.status or "").strip().upper()
                if stt in ("REMOVIDO", "REMOVIDOS"):
                    continue
                if alvo != "__TODOS__" and stt != alvo:
                    continue
                a = c.asset
                rows.append({
                    "encontrado": True,
                    "cycle_id": c.id,
                    "serial_number": a.serial_number or "",
                    "tag_number": a.tag_number or "",
                    "asset_number": a.asset_number or a.asset_id or "",
                    "model": a.model or "",
                    "category": a.category or "",
                    "company": _normaliza_company(a.company or ""),
                    "description": a.description or "",
                    "cost": str(a.cost) if a.cost else "",
                    "dpis": a.dpis.isoformat() if a.dpis else "",
                    "acquisition_date": a.acquisition_date.isoformat() if a.acquisition_date else "",
                })
    else:
        raise HTTPException(400, "Origem inválida.")

    # Marca quais já existem no ServiceNow (por asset_tag/serial).
    from types import SimpleNamespace
    itens = [SimpleNamespace(asset_tag=r.get("tag_number", ""), serial_number=r.get("serial_number", ""))
             for r in rows if r.get("encontrado")]
    tags_ok, ser_ok = set(), set()
    try:
        tags_ok, ser_ok = _hardware_existentes(session, itens)
    except Exception:  # noqa: BLE001
        pass
    for r in rows:
        t = str(r.get("tag_number") or "").strip().upper()
        s2 = str(r.get("serial_number") or "").strip().upper()
        r["existe_sn"] = bool((t and t in tags_ok) or (s2 and s2 in ser_ok))

    return {"rows": rows, "total": len(rows)}


@router.get("/resolve-check")
def resolve_check(req: Request, field: str, value: str):
    """Diagnóstico: mostra como o SN resolve um valor de referência
    (model, model_category, stockroom, company). Ajuda a achar por que um
    modelo existente não é encontrado. Somente para conferência."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)
    table = REFERENCE_TABLE_MAP.get(field)
    if not table:
        raise HTTPException(400, "field inválido. Use model, model_category, stockroom ou company.")
    campos = _REF_NAME_CANDIDATES.get(field, [REFERENCE_NAME_FIELD.get(field, "name")])
    tentativas = []
    for nf in campos:
        sid = _lookup_ref_field(session, table, nf, value, like=False)
        tentativas.append({"campo": nf, "operador": "=", "achou": bool(sid), "sys_id": sid})
    like_sid = _lookup_ref_field(session, table, campos[0], value, like=True)
    tentativas.append({"campo": campos[0], "operador": "LIKE", "achou": bool(like_sid), "sys_id": like_sid})
    sid, ok = _resolve_ref(session, field, value)
    return {"field": field, "value": value, "tabela": table,
            "tentativas": tentativas, "resolvido": ok, "sys_id": sid}


@router.post("/hardware-exists")
def hardware_exists(body: ExisteIn, req: Request):
    """Para cada item {asset_tag, serial_number} informa se JÁ existe no
    ServiceNow (alm_hardware) — por asset_tag OU serial_number. Usado na Base
    de Recebimentos ('Localizado no SN?') e na validação da Entrada de Estoque."""
    require_permission(req, "recebimento", "view")
    session = _sn_session_from_portal(req)
    itens = body.itens or []
    if not itens:
        return {"resultados": []}
    tags_ok, ser_ok = _hardware_existentes(session, itens)
    out = []
    for i in itens:
        t = str(i.asset_tag or "").strip().upper()
        s = str(i.serial_number or "").strip().upper()
        existe = bool((t and t in tags_ok) or (s and s in ser_ok))
        out.append({"id": i.id, "existe": existe})
    return {"resultados": out}


# ═══════════════════════════════════════════════════════════════════
# SAÍDA DE ESTOQUE — busca e movimentação de ativos no alm_hardware
# ═══════════════════════════════════════════════════════════════════

VALID_STOCKROOMS = ["SPARE-ADM15", "SPARE-CD324", "SPARE-CD504"]

BU_MAP = {
    "renner": "Renner Brasil",
    "youcom": "Youcom",
    "camicado": "Camicado",
    "ashua": "Ashua",
}


class SaidaSearchIn(BaseModel):
    asset_tag: str = ""
    serial_number: str = ""
    stockroom: str = ""


class SaidaMovIn(BaseModel):
    sys_id: str
    install_status: str = "In transit"
    location: str = ""
    aisle_space: str = ""
    notes: str = ""


@router.post("/saida/search")
def saida_search(body: SaidaSearchIn, req: Request):
    """Busca ativo no alm_hardware por asset_tag OU serial_number.
    Sem filtro de stockroom por padrão (busca global para verificar se existe)."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    if not body.asset_tag and not body.serial_number:
        raise HTTPException(400, "Informe Asset Tag ou Número de Série.")

    query_parts = []
    if body.asset_tag and body.serial_number:
        query_parts.append(f"asset_tag={body.asset_tag}^ORserial_number={body.serial_number}")
    elif body.asset_tag:
        query_parts.append(f"asset_tag={body.asset_tag}")
    else:
        query_parts.append(f"serial_number={body.serial_number}")

    if body.stockroom:
        query_parts.append(f"stockroom.name={body.stockroom}")

    sn_query = "^".join(query_parts)

    fields = (
        "sys_id,asset_tag,serial_number,display_name,model,model_category,"
        "company,stockroom,install_status,substatus,assigned_to,"
        "location,cost,purchase_date"
    )

    assets = _sn_query(session, HARDWARE_TABLE, sn_query, fields, limit=20)

    return {"assets": assets}


@router.get("/saida/locations")
def saida_locations(req: Request):
    """Retorna lista de lojas/locais do ServiceNow (cmn_location)."""
    require_permission(req, "servicenow", "view")
    import json as _json
    # Cadastro de referência: fica em data/referencias/ (fora de static/,
    # para não ficar acessível sem login).
    from config import get_settings
    loc_path = get_settings().DATA / "referencias" / "locations_sn.json"
    try:
        with open(loc_path, "r", encoding="utf-8") as f:
            return {"locations": _json.load(f)}
    except FileNotFoundError:
        return {"locations": []}


@router.post("/saida/move")
def saida_move(body: SaidaMovIn, req: Request):
    """Atualiza status e local de um ativo (saída de estoque)."""
    require_permission(req, "servicenow", "edit")
    session = _sn_session_from_portal(req)

    if not body.sys_id:
        raise HTTPException(400, "sys_id obrigatório.")

    update = {"install_status": INSTALL_STATUS_MAP.get(body.install_status.lower(), body.install_status)}

    if body.location:
        cache = {}
        _, BS = _get_http()
        loc_id = _lookup_reference(session, "location", body.location, cache, BS)
        update["location"] = loc_id

    if body.aisle_space:
        update["aisle_space_location"] = body.aisle_space

    if body.notes:
        update["work_notes"] = body.notes

    _sn_update(session, HARDWARE_TABLE, body.sys_id, update)

    return {"ok": True, "message": "Ativo atualizado com sucesso."}


class MovInternaIn(BaseModel):
    sys_id: str
    stockroom: str = ""
    install_status: str = ""
    aisle_space: str = ""
    notes: str = ""


@router.post("/mov-interna/move")
def mov_interna_move(body: MovInternaIn, req: Request):
    """Movimentação interna: troca o estoque (stockroom), status, espaço/corredor
    e observação de um ativo — sem alterar loja/location."""
    require_permission(req, "servicenow", "edit")
    session = _sn_session_from_portal(req)

    if not body.sys_id:
        raise HTTPException(400, "sys_id obrigatório.")
    faltando = []
    if not (body.stockroom or "").strip():
        faltando.append("Estoque destino")
    if not (body.install_status or "").strip():
        faltando.append("Novo Status")
    if not (body.aisle_space or "").strip():
        faltando.append("Aisle and Space")
    if not (body.notes or "").strip():
        faltando.append("Observações")
    if faltando:
        raise HTTPException(400, "Campos obrigatórios: " + ", ".join(faltando) + ".")

    cache = {}
    _, BS = _get_http()
    stock_id = _lookup_reference(session, "stockroom", body.stockroom.strip(), cache, BS)
    if not stock_id or stock_id == body.stockroom.strip():
        raise HTTPException(400, "Regularizar Stockroom (Não encontrou o estoque '%s')." % body.stockroom)

    update = {
        "stockroom": stock_id,
        "install_status": INSTALL_STATUS_MAP.get(body.install_status.lower(), body.install_status),
        "aisle_space_location": body.aisle_space.strip(),
        "work_notes": body.notes.strip(),
    }
    _sn_update(session, HARDWARE_TABLE, body.sys_id, update)
    return {"ok": True, "message": "Ativo movimentado com sucesso."}


class SaidaSearchLoteIn(BaseModel):
    identificadores: list = []


@router.post("/saida/search_lote")
def saida_search_lote(body: SaidaSearchLoteIn, req: Request):
    """Busca vários ativos de uma vez por asset_tag OU serial_number.

    Aceita uma lista sem limite; consulta o ServiceNow em blocos e
    deduplica por sys_id. Retorna também os identificadores não achados.
    """
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    ids = []
    seen = set()
    for raw in body.identificadores:
        v = str(raw or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            ids.append(v)
    if not ids:
        raise HTTPException(400, "Informe ao menos um identificador.")

    fields = (
        "sys_id,asset_tag,serial_number,display_name,model,model_category,"
        "company,stockroom,install_status,substatus,assigned_to,"
        "location,aisle_space_location,cost,purchase_date"
    )

    def _plain(v):
        if isinstance(v, dict):
            return v.get("value") or v.get("display_value") or ""
        return v or ""

    assets_by_id = {}
    CHUNK = 40
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        lista = ",".join(chunk)
        sn_query = f"asset_tagIN{lista}^ORserial_numberIN{lista}"
        try:
            found = _sn_query(
                session, HARDWARE_TABLE, sn_query, fields,
                limit=len(chunk) * 4 + 20,
            )
        except Exception:
            found = []
        for a in found:
            sid = _plain(a.get("sys_id"))
            if sid:
                assets_by_id[str(sid)] = a

    assets = list(assets_by_id.values())

    achados = set()
    for a in assets:
        achados.add(str(_plain(a.get("asset_tag"))).strip().lower())
        achados.add(str(_plain(a.get("serial_number"))).strip().lower())
    nao_encontrados = [x for x in ids if x.strip().lower() not in achados]

    return {
        "assets": assets,
        "solicitados": len(ids),
        "nao_encontrados": nao_encontrados,
    }


# ═══════════════════════════════════════════════════════════════════
# CHAMADOS CORREIOS — busca incidentes com correlation_display
# ═══════════════════════════════════════════════════════════════════

_TRACKING_RE = re.compile(r'[A-Za-z]{2}\d{9}[A-Za-z]{2}')


def _extract_tracking_code(incident: dict) -> str:
    """Extract Correios tracking code (XX000000000XX) from correlation_display.

    Aceita maiúsculas ou minúsculas e normaliza para maiúsculas (a API
    dos Correios espera o código em caixa alta).
    """
    val = incident.get("correlation_display", "")
    if isinstance(val, dict):
        val = val.get("display_value", val.get("value", ""))
    if val:
        val = str(val).strip()
        m = _TRACKING_RE.search(val)
        if m:
            return m.group(0).upper()
    return ""


@router.get("/chamados-correios")
def chamados_correios(
    req: Request,
    queue: str = DEFAULT_QUEUE,
    limit: int = 50,
    offset: int = 0,
):
    """Lista incidentes com código de rastreio (correlation_id ou correlation_display)."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    sn_query = (
        f"assignment_group.name={queue}"
        f"^stateNOT IN6,7,8"
        f"^ORDERBYDESCsys_created_on"
    )

    fields = (
        "sys_id,number,short_description,state,priority,"
        "correlation_id,correlation_display,"
        "caller_id,opened_at,resolved_at,closed_at"
    )

    # Busca a fila inteira paginando; o filtro de rastreio é feito em
    # Python porque o JSONv2 não suporta LIKE/ENDSWITH de forma confiável.
    all_incidents = _sn_query_all(session, INCIDENT_TABLE, sn_query, fields)

    incidents = []
    for inc in all_incidents:
        tracking = _extract_tracking_code(inc)
        if tracking:
            inc["_tracking_code"] = tracking
            incidents.append(inc)

    total = len(incidents)
    page = incidents[offset:offset + limit]

    return {
        "incidents": page,
        "total": total,
        "total_fila": len(all_incidents),
    }


@router.get("/chamados-correios/debug")
def chamados_correios_debug(
    req: Request,
    queue: str = DEFAULT_QUEUE,
):
    """Debug: percorre a fila inteira e mostra o que o ServiceNow devolve,
    para diagnosticar por que o filtro de rastreio não casa."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    sn_query = (
        f"assignment_group.name={queue}"
        f"^stateNOT IN6,7,8"
        f"^ORDERBYDESCsys_created_on"
    )

    incidents = _sn_query_all(
        session, INCIDENT_TABLE, sn_query,
        fields="number,correlation_id,correlation_display,state,short_description",
    )

    com_rastreio = []
    sem_rastreio = []
    for inc in incidents:
        cd = inc.get("correlation_display", "")
        if isinstance(cd, dict):
            cd = cd.get("display_value", cd.get("value", ""))
        registro = {
            "number": inc.get("number", ""),
            "correlation_display": cd,
            "correlation_display_type": type(inc.get("correlation_display")).__name__,
            "correlation_id": inc.get("correlation_id", ""),
            "state": inc.get("state", ""),
        }
        if _extract_tracking_code(inc):
            registro["tracking"] = _extract_tracking_code(inc)
            com_rastreio.append(registro)
        else:
            sem_rastreio.append(registro)

    return {
        "queue": queue,
        "total_na_fila": len(incidents),
        "com_rastreio": len(com_rastreio),
        "sem_rastreio": len(sem_rastreio),
        "regex_usado": _TRACKING_RE.pattern,
        "exemplos_com_rastreio": com_rastreio[:10],
        "exemplos_sem_rastreio": sem_rastreio[:20],
    }


# ═══════════════════════════════════════════════════════════════════
# RELATÓRIOS — métricas de desempenho (task_sla, incident_sla, incident)
# ═══════════════════════════════════════════════════════════════════

@router.get("/relatorios/tickets")
def relatorios_tickets(req: Request, queue: str = DEFAULT_QUEUE):
    """Chamados resolvidos nos últimos 12 meses (task_sla).
    Filters: Assignment Group, Task Closed this year, Task State not Canceled.
    Groups by month for chart display."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    year = datetime.now().year

    sn_query = (
        f"task.assignment_group.name={queue}"
        f"^task.closed_atONThis year@javascript:gs.beginningOfThisYear()@javascript:gs.endOfThisYear()"
        f"^task.stateNOT IN8"
    )

    records = _sn_query(session, "task_sla", sn_query, "sys_id,task.closed_at", limit=5000, display_value=True)

    by_month: dict = {}
    for rec in records:
        closed = rec.get("task.closed_at", "") or ""
        if closed:
            month_key = closed[:7]  # "YYYY-MM"
            by_month[month_key] = by_month.get(month_key, 0) + 1

    return {"total": len(records), "by_month": by_month, "year": year, "queue": queue}


@router.get("/relatorios/sla")
def relatorios_sla(req: Request, queue: str = DEFAULT_QUEUE):
    """SLA compliance (incident_sla).
    Filters: Assignment group, SLA definition P1-P5 TI_N2_FLD_RNR_CD464_SPARE,
    Business elapsed percentage >= 100 (breached)."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    sla_definitions = [
        "P1 TI_N2_FLD_RNR_CD464_SPARE",
        "P2 TI_N2_FLD_RNR_CD464_SPARE",
        "P3 TI_N2_FLD_RNR_CD464_SPARE",
        "P4 TI_N2_FLD_RNR_CD464_SPARE",
        "P5 TI_N2_FLD_RNR_CD464_SPARE",
    ]
    sla_filter = "^OR".join(f"sla.name={d}" for d in sla_definitions)

    total_query = (
        f"task.assignment_group.name={queue}"
        f"^{sla_filter}"
    )

    all_records = _sn_query(session, "incident_sla", total_query, "sys_id,sla,business_percentage", limit=5000, display_value=True)

    total = len(all_records)
    breached = sum(1 for r in all_records if float(r.get("business_percentage") or 0) >= 100)
    met = total - breached
    pct = round((met / total * 100), 1) if total > 0 else 0

    by_priority: dict = {}
    for sla_def in sla_definitions:
        prio = sla_def.split()[0]
        prio_records = [r for r in all_records if (r.get("sla") or "") == sla_def]
        pt = len(prio_records)
        pb = sum(1 for r in prio_records if float(r.get("business_percentage") or 0) >= 100)
        by_priority[prio] = {"total": pt, "breached": pb, "met": pt - pb}

    return {
        "total": total,
        "breached": breached,
        "met": met,
        "compliance_pct": pct,
        "by_priority": by_priority,
        "queue": queue,
    }


@router.get("/relatorios/tma")
def relatorios_tma(req: Request, queue: str = DEFAULT_QUEUE):
    """TMA para Coletores e SLEDs (incident table).
    Filters: Assignment group, Subcategory in sled/RFID sled/coletor.
    Calculates avg time between reassignment_count (bouncing) and closed_at."""
    require_permission(req, "servicenow", "view")
    session = _sn_session_from_portal(req)

    categories = {
        "coletor": "subcategory=coletor",
        "sled": "subcategoryINsled,rfid sled,RFID sled",
    }

    results = {}
    for cat_key, cat_filter in categories.items():
        sn_query = (
            f"assignment_group.name={queue}"
            f"^{cat_filter}"
            f"^closed_atISNOTEMPTY"
            f"^opened_atISNOTEMPTY"
        )

        fields = "sys_id,number,opened_at,closed_at,reassignment_count,subcategory"

        try:
            incidents = _sn_query(session, INCIDENT_TABLE, sn_query, fields, limit=500, display_value=False)
        except Exception as e:
            results[cat_key] = {"count": 0, "avg_hours": 0, "error": str(e)}
            continue

        total_hours = 0
        valid = 0
        for inc in incidents:
            try:
                opened = datetime.strptime(inc["opened_at"], "%Y-%m-%d %H:%M:%S")
                closed = datetime.strptime(inc["closed_at"], "%Y-%m-%d %H:%M:%S")
                diff = (closed - opened).total_seconds() / 3600
                if diff > 0:
                    total_hours += diff
                    valid += 1
            except (ValueError, KeyError):
                continue

        avg = round(total_hours / valid, 1) if valid > 0 else 0
        results[cat_key] = {"count": len(incidents), "sample": valid, "avg_hours": avg}

    return {"tma": results, "queue": queue}


# ═══════════════════════════════════════════════════════════════════
# RELATÓRIOS — cache para TV (salva dados em Setting)
# ═══════════════════════════════════════════════════════════════════

@router.post("/relatorios/refresh-tv")
def refresh_tv_cache(req: Request):
    """Busca todos os relatórios e salva cache para o TV display."""
    require_permission(req, "servicenow", "view")

    tickets = relatorios_tickets(req)
    sla = relatorios_sla(req)
    tma = relatorios_tma(req)

    cache_data = {
        "tickets_resolved": tickets["total"],
        "tickets_by_month": tickets["by_month"],
        "sla_compliance_pct": sla["compliance_pct"],
        "sla_total": sla["total"],
        "sla_breached": sla["breached"],
        "sla_met": sla["met"],
        "sla_by_priority": sla["by_priority"],
        "tma": tma["tma"],
        "updated_at": datetime.now().isoformat(),
    }

    with SessionLocal.begin() as s:
        row = s.get(Setting, "sn_tv_cache")
        if row:
            row.value = cache_data
        else:
            s.add(Setting(key="sn_tv_cache", value=cache_data))

    return {"ok": True, "data": cache_data}


# Padrões da marcação automática no recebimento. Ficam aqui porque é
# aqui que mora o contrato com o ServiceNow; o valor efetivo vem da
# configuração (Parâmetros → Configuração Módulos).
RECEBIMENTO_STOCKROOM_PADRAO = "SPARE - CD324"
RECEBIMENTO_STATUS_PADRAO = "6"          # In stock
RECEBIMENTO_DEPRECIACAO_PADRAO = "SL 5 Years"


def _campo_item(item, *nomes: str) -> str:
    """Lê um campo do item, aceitando dict ou objeto (compatibilidade)."""
    for n in nomes:
        v = item.get(n) if isinstance(item, dict) else getattr(item, n, None)
        if v:
            return str(v).strip()
    return ""


def _registro_individual(session, tag: str, serie: str) -> dict | None:
    """Procura UM ativo por etiqueta ou série. Rede contra duplicidade."""
    partes = []
    if tag:
        partes.append(f"asset_tag={termo_sn(tag, 'etiqueta')}")
    if serie:
        partes.append(f"serial_number={termo_sn(serie, 'série')}")
    if not partes:
        return None
    consulta = "^OR".join(partes)
    try:
        achados = _sn_query(session, HARDWARE_TABLE, consulta,
                            "sys_id,asset_tag,serial_number", limit=1,
                            display_value=False)
    except Exception:  # noqa: BLE001 — sem resposta, quem chama decide
        return None
    if not achados:
        return None

    def _plain(v):
        return v.get("value", "") if isinstance(v, dict) else (v or "")

    r = achados[0]
    return {"sys_id": _plain(r.get("sys_id")),
            "asset_tag": str(_plain(r.get("asset_tag"))).strip(),
            "serial_number": str(_plain(r.get("serial_number"))).strip()}


def _depreciar(session, sys_id: str, BS, rotulo: str, resumo: dict,
               tag: str = "", serie: str = "", linha: dict | None = None) -> None:
    """Roda o cálculo de depreciação do ativo recém-escrito.

    Nunca silencia: sem sys_id o registro é reprocurado pelos
    identificadores, e o que não depreciar entra em `falhas`. Depreciação
    que falha sem ninguém saber é exatamente o que a área reclamou.
    """
    if not sys_id or sys_id == "N/A":
        achado = _registro_individual(session, tag, serie)
        sys_id = (achado or {}).get("sys_id", "")
        if not sys_id:
            resumo["falhas"].append(
                f"{rotulo}: subiu, mas o ServiceNow não devolveu o sys_id — "
                "depreciação não calculada")
            resumo["sem_depreciacao"] = resumo.get("sem_depreciacao", 0) + 1
            if linha is not None:
                linha["depreciacao"] = "não calculada (sem sys_id)"
            return
        if linha is not None:
            linha["sys_id"] = sys_id
    try:
        if _calculate_depreciation(session, sys_id, BS):
            resumo["depreciados"] = resumo.get("depreciados", 0) + 1
            if linha is not None:
                linha["depreciacao"] = "calculada"
            return
    except Exception as exc:  # noqa: BLE001 — o ativo já subiu; isto é aviso
        _log.error("servicenow: depreciação de %s falhou: %s", rotulo, exc)
        resumo["falhas"].append(f"{rotulo}: cálculo da depreciação falhou — {exc}")
        resumo["sem_depreciacao"] = resumo.get("sem_depreciacao", 0) + 1
        if linha is not None:
            linha["depreciacao"] = f"falhou: {str(exc)[:120]}"
        return
    _log.error("servicenow: o ServiceNow não calculou a depreciação de %s "
               "(sys_id %s)", rotulo, sys_id)
    resumo["falhas"].append(
        f"{rotulo}: o ServiceNow não calculou a depreciação (sys_id {sys_id}). "
        "Verifique se a sessão do portal ainda abre o formulário do ativo.")
    resumo["sem_depreciacao"] = resumo.get("sem_depreciacao", 0) + 1
    if linha is not None:
        linha["depreciacao"] = "não calculada"


def marcar_recebidos_em_estoque(session, itens: list, stockroom: str = "",
                                install_status: str = "", aisle_space: str = "",
                                criar: bool = True, depreciacao: str = "") -> dict:
    """Reflete no ServiceNow o que chegou fisicamente ao CD.

    Quem já existe é ATUALIZADO com os dados do recebimento (modelo,
    categoria, identificadores) e passa a estar em estoque, no CD, no
    espaço e corredor informados. Quem não existe é CRIADO ali mesmo:
    equipamento no CD que o ServiceNow não conhece é ativo invisível, e
    esperar a Entrada de Estoque deixava a diferença aberta.

    Nunca se cria ativo pela metade: **custo, data de aquisição e
    depreciação são obrigatórios**, e o cálculo da depreciação roda logo
    depois da escrita. Item sem esses dados não sobe — entra em `falhas`
    dizendo o que falta, e o ativo continua fora do ServiceNow em vez de
    entrar como registro incompleto.

    Antes de criar, o ativo é procurado uma segunda vez, um a um: um
    índice vazio (consulta em lote que falhou, campo lido errado) faria o
    ativo existente virar duplicata, e duplicata em cadastro de
    patrimônio dá trabalho para o resto da vida.

    `aisle_space` é obrigatório: gravar "em estoque" sem dizer onde é o
    que faz o inventário não fechar depois. A escrita usa a sessão de
    quem está logado — no ServiceNow o registro sai no nome de quem
    recebeu.
    """
    # `por_item` é o que a tela mostra: uma linha por série, dizendo o que
    # o ServiceNow fez com ela. Contador agregado não responde "e o MEU
    # coletor?", que é a pergunta que sempre aparece.
    resumo = {"encontrados": 0, "atualizados": 0, "criados": 0,
              "nao_encontrados": 0, "incompletos": 0, "depreciados": 0,
              "sem_depreciacao": 0, "falhas": [], "por_item": []}
    if not itens:
        return resumo

    stockroom = (stockroom or RECEBIMENTO_STOCKROOM_PADRAO).strip()
    status = (install_status or RECEBIMENTO_STATUS_PADRAO).strip()
    status = INSTALL_STATUS_MAP.get(status.lower(), status)
    aisle = (aisle_space or "").strip()
    if not aisle:
        raise HTTPException(400, "Informe o Espaço e Corredor antes de subir ao ServiceNow.")

    registros = _hardware_records(session, itens)
    resumo["encontrados"] = len(registros)

    _req, BS = _get_http()
    cache: dict = {}
    stockroom_id = _lookup_reference(session, "stockroom", stockroom, cache, BS)
    if not stockroom_id:
        # Sem o estoque resolvido não se escreve nada: gravar só o status
        # deixaria o ativo "em estoque" sem dizer em qual, que é pior do
        # que não mexer.
        resumo["falhas"].append(
            f"estoque '{stockroom}' não encontrado no ServiceNow")
        return resumo

    # Depreciação é obrigatória, então ela é resolvida antes: sem o plano
    # no ServiceNow não há como subir ativo completo, e subir incompleto
    # não é opção.
    plano = (depreciacao or RECEBIMENTO_DEPRECIACAO_PADRAO).strip()
    depreciation_id = _lookup_reference(session, "depreciation", plano, cache, BS)
    if not depreciation_id:
        resumo["falhas"].append(
            f"plano de depreciação '{plano}' não encontrado no ServiceNow")
        return resumo

    # Índice do que já existe, por etiqueta e por série.
    por_tag = {r["asset_tag"].upper(): r for r in registros if r.get("asset_tag")}
    por_serie = {r["serial_number"].upper(): r for r in registros if r.get("serial_number")}

    base = {"install_status": status, "stockroom": stockroom_id,
            "aisle_space_location": aisle, "depreciation": depreciation_id,
            "cost.currency_type": CURRENCY_MAP.get("BRL", "BRL")}

    for item in itens:
        tag = _campo_item(item, "etiqueta", "asset_tag", "tag_number")
        serie = _campo_item(item, "serial", "serial_number", "numero_serie")
        modelo = _campo_item(item, "modelo", "model")
        categoria = _campo_item(item, "categoria", "category")
        rotulo = tag or serie or "(sem identificador)"
        linha = {"serial": serie, "etiqueta": tag, "acao": "", "motivo": "",
                 "sys_id": "", "depreciacao": ""}
        resumo["por_item"].append(linha)

        custo = _campo_item(item, "custo", "custo_asset", "cost")
        aquisicao = _campo_item(item, "dpis", "data_aquisicao", "acquisition_date",
                                "purchase_date")

        existente = por_tag.get(tag.upper()) if tag else None
        if existente is None and serie:
            existente = por_serie.get(serie.upper())
        # Segunda busca, individual: o índice do lote pode ter vindo vazio.
        if existente is None and (tag or serie):
            existente = _registro_individual(session, tag, serie)
            if existente is not None:
                resumo["encontrados"] += 1

        # Ativo incompleto não sobe. Vale para criar E para atualizar: o
        # recebimento é o momento em que esses dados existem.
        faltando = []
        if not custo:
            faltando.append("custo")
        if not _parse_date(aquisicao):
            faltando.append("data de aquisição (DPIS)")
        if faltando:
            resumo["incompletos"] += 1
            linha["acao"] = "não subiu"
            linha["motivo"] = f"falta {' e '.join(faltando)}"
            resumo["falhas"].append(
                f"{rotulo}: não subiu porque falta {' e '.join(faltando)}. "
                "Custo e depreciação são obrigatórios.")
            continue

        # Modelo e categoria só entram quando o ServiceNow os reconhece:
        # mandar texto livre num campo de referência apaga o valor atual.
        dados = dict(base)
        dados["cost"] = str(custo).replace(",", ".")
        dados["purchase_date"] = _parse_date(aquisicao)
        dados["depreciation_date"] = _parse_date_with_time(aquisicao)
        if modelo:
            mid = _lookup_reference(session, "model", modelo, cache, BS)
            if mid:
                dados["model"] = mid
        if categoria:
            cid = _lookup_reference(session, "model_category", categoria, cache, BS)
            if cid:
                dados["model_category"] = cid

        try:
            if existente is not None:
                alteracao = dict(dados)
                # Completa identificador que faltava no registro do SN.
                if tag and not existente.get("asset_tag"):
                    alteracao["asset_tag"] = tag
                if serie and not existente.get("serial_number"):
                    alteracao["serial_number"] = serie
                if _sn_update(session, HARDWARE_TABLE, existente["sys_id"], alteracao):
                    resumo["atualizados"] += 1
                    linha["acao"] = "atualizado"
                    linha["sys_id"] = existente["sys_id"]
                    _depreciar(session, existente["sys_id"], BS, rotulo, resumo,
                               tag, serie, linha)
                else:
                    linha["acao"] = "falhou"
                    linha["motivo"] = "o ServiceNow não confirmou a atualização"
                    resumo["falhas"].append(f"{rotulo}: o ServiceNow não confirmou a atualização")
            elif criar:
                if not (tag or serie):
                    resumo["falhas"].append("item sem etiqueta e sem série não pode ser criado")
                    continue
                registro = dict(dados)
                if tag:
                    registro["asset_tag"] = tag
                if serie:
                    registro["serial_number"] = serie
                ok, novo_sys_id, detalhe = _insert_record(session, registro)
                if ok:
                    resumo["criados"] += 1
                    linha["acao"] = "criado"
                    linha["sys_id"] = novo_sys_id
                    _depreciar(session, novo_sys_id, BS, rotulo, resumo,
                               tag, serie, linha)
                else:
                    linha["acao"] = "falhou"
                    linha["motivo"] = detalhe
                    resumo["falhas"].append(f"{rotulo}: falha ao criar — {detalhe}")
            else:
                resumo["nao_encontrados"] += 1
                linha["acao"] = "não encontrado"
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 — um item ruim não derruba o lote
            linha["acao"] = "falhou"
            linha["motivo"] = str(exc)[:200]
            resumo["falhas"].append(f"{rotulo}: {exc}")

    return resumo


@router.get("/diagnostico/depreciacao")
def diagnostico_depreciacao(req: Request, serie: str = "", etiqueta: str = ""):
    """Por que a depreciação não foi calculada — sem executar nada.

    Percorre o mesmo caminho do cálculo e para onde ele pararia, dizendo
    em qual passo e por quê.
    """
    require_permission(req, "servicenow", "view")
    serie = (serie or "").strip()
    etiqueta = (etiqueta or "").strip()
    # O que o servidor REALMENTE recebeu. Sem este eco, "informei a série"
    # e "a série não chegou" viram discussão em vez de diagnóstico.
    recebido = {"serie": serie, "etiqueta": etiqueta,
                "url": str(getattr(req, "url", ""))}
    if not serie and not etiqueta:
        return {"encontrado": False, "informado": False, "recebido": recebido,
                "motivo": "o servidor recebeu a consulta SEM série e SEM "
                          "etiqueta. Se você informou, o dado se perdeu no "
                          "caminho — use a tela em Status → Diagnóstico de "
                          "ativo, que envia direto."}
    session = _sn_session_from_portal(req)
    _req, BS = _get_http()
    achado = _registro_individual(session, etiqueta, serie)
    if not achado or not achado.get("sys_id"):
        return {"encontrado": False, "informado": True, "recebido": recebido,
                "motivo": f"o ativo não foi encontrado no ServiceNow por "
                          f"série \"{serie or '—'}\" / etiqueta \"{etiqueta or '—'}\" "
                          "— sem ativo no cadastro não há o que depreciar."}
    saida = _depreciacao_passos(session, achado["sys_id"], BS, executar=False)
    saida["encontrado"] = True
    saida["recebido"] = recebido
    saida["asset_tag"] = achado.get("asset_tag", "")
    saida["serial_number"] = achado.get("serial_number", "")
    return saida


def _marcar_existe_sn(session, rows: list[dict]) -> None:
    """Marca quais linhas já existem no ServiceNow (por asset_tag/serial).

    A mesma conferência que a pré-visualização faz inline; aqui ela é
    função porque a origem por planilha também precisa dela, e quem sobe
    um arquivo tem de ver "já existe" antes de mandar, não depois.
    """
    from types import SimpleNamespace
    itens = [SimpleNamespace(asset_tag=r.get("tag_number", ""), serial_number=r.get("serial_number", ""))
             for r in rows if r.get("encontrado")]
    tags_ok, ser_ok = set(), set()
    try:
        tags_ok, ser_ok = _hardware_existentes(session, itens)
    except Exception:  # noqa: BLE001 — sem o ServiceNow a conferência some, a tela não
        pass
    for r in rows:
        t = str(r.get("tag_number") or "").strip().upper()
        s2 = str(r.get("serial_number") or "").strip().upper()
        r["existe_sn"] = bool((t and t in tags_ok) or (s2 and s2 in ser_ok))


# ── Origem por planilha ───────────────────────────────────────────
# As colunas são as que sobem para o ServiceNow. O modelo para baixar e o
# arquivo enviado falam a mesma língua: quem preenche vê exatamente o que
# vai gravar.
COLUNAS_PLANILHA_ENTRADA: list[tuple[str, str, bool]] = [
    ("tag_number", "Asset Tag", True),
    ("serial_number", "Número de Série", True),
    ("asset_number", "Imobilizado", False),
    ("model", "Modelo", True),
    ("category", "Categoria", False),
    ("company", "Empresa", False),
    ("description", "Descrição", False),
    ("cost", "Custo", False),
    ("dpis", "DPIS (AAAA-MM-DD)", False),
    ("acquisition_date", "Data de aquisição (AAAA-MM-DD)", False),
]


@router.get("/entrada/planilha-modelo")
def entrada_planilha_modelo(req: Request):
    """Planilha modelo com as colunas que sobem para o ServiceNow."""
    require_permission(req, "servicenow", "view")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from fastapi.responses import StreamingResponse
    import io as _io

    wb = Workbook()
    ws = wb.active
    ws.title = "Ativos"
    cabecalho = Font(bold=True, color="FFFFFF")
    fundo = PatternFill("solid", fgColor="C06010")
    for col, (_chave, rotulo, obrig) in enumerate(COLUNAS_PLANILHA_ENTRADA, start=1):
        cel = ws.cell(row=1, column=col, value=rotulo + (" *" if obrig else ""))
        cel.font = cabecalho
        cel.fill = fundo
        ws.column_dimensions[cel.column_letter].width = max(16, len(cel.value) + 4)
    ws.cell(row=2, column=1, value="RN000123")
    ws.cell(row=2, column=2, value="SN123456789")
    ws.cell(row=2, column=4, value="PDV Dell 3050")
    ws.freeze_panes = "A2"
    aba = wb.create_sheet("Instruções")
    for i, linha in enumerate([
        "Uma linha por ativo. A primeira linha é o cabeçalho e não deve ser apagada.",
        "Colunas com * são obrigatórias: Asset Tag, Número de Série e Modelo.",
        "Datas em AAAA-MM-DD. Custo apenas com números.",
        "A linha 2 é um exemplo: apague-a antes de enviar.",
        "Depois de enviar, confira a pré-visualização e selecione o que vai subir.",
    ], start=1):
        aba.cell(row=i, column=1, value=linha)
    aba.column_dimensions["A"].width = 90

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modelo_entrada_ativos.xlsx"'})


def ler_planilha_entrada(nome: str, conteudo: bytes) -> list[dict]:
    """Lê a planilha enviada e devolve as linhas no formato da pré-visualização.

    Aceita XLSX e CSV. O cabeçalho é casado pelo rótulo do modelo ou pelo
    nome técnico da coluna, sem diferenciar acento nem caixa.
    """
    import pandas as pd
    import io as _io

    nome = (nome or "").lower()
    if nome.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(_io.BytesIO(conteudo), dtype=str)
    else:
        texto = conteudo.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        df = pd.read_csv(_io.StringIO(texto), sep=sep, dtype=str)

    def limpar(t: str) -> str:
        t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
        return t.strip().lower().rstrip("*").strip()

    colunas = {limpar(c): c for c in df.columns}
    mapa: dict[str, str] = {}
    for chave, rotulo, _obrig in COLUNAS_PLANILHA_ENTRADA:
        for candidato in (rotulo, chave):
            if limpar(candidato) in colunas:
                mapa[chave] = colunas[limpar(candidato)]
                break
    faltando = [r for c, r, obrig in COLUNAS_PLANILHA_ENTRADA if obrig and c not in mapa]
    if faltando:
        raise HTTPException(400, "Planilha sem a(s) coluna(s): " + ", ".join(faltando) +
                                 ". Baixe o modelo e use o cabeçalho dele.")

    linhas: list[dict] = []
    for _, r in df.iterrows():
        def pega(chave: str) -> str:
            if chave not in mapa:
                return ""
            v = r[mapa[chave]]
            if v is None or (isinstance(v, float) and v != v):   # NaN
                return ""
            return str(v).strip()

        tag, serie, modelo = pega("tag_number"), pega("serial_number"), pega("model")
        if not (tag or serie or modelo):
            continue                      # linha em branco no meio da planilha
        falta = [rot for chave, rot in (("tag_number", "Asset Tag"), ("serial_number", "Número de Série"),
                                        ("model", "Modelo")) if not pega(chave)]
        linhas.append({
            "encontrado": not falta,
            "origem_planilha": True,
            "tag_number": tag, "serial_number": serie, "model": modelo,
            "asset_number": pega("asset_number"),
            "category": pega("category"),
            "company": _normaliza_company(pega("company")),
            "description": pega("description"),
            "cost": pega("cost"),
            "dpis": pega("dpis"),
            "acquisition_date": pega("acquisition_date"),
            "erro": ("Faltando: " + ", ".join(falta)) if falta else "",
        })
    if not linhas:
        raise HTTPException(400, "A planilha não tem nenhuma linha preenchida.")
    return linhas


@router.post("/entrada/planilha")
async def entrada_planilha(req: Request, arquivo: UploadFile = File(...)):
    """Recebe a planilha preenchida e devolve as linhas para conferência."""
    require_permission(req, "servicenow", "view")
    check_rate_limit(req)
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")
    if len(conteudo) > 10 * 1024 * 1024:
        raise HTTPException(400, "Arquivo acima de 10 MB.")
    rows = ler_planilha_entrada(arquivo.filename or "", conteudo)
    session = _sn_session_from_portal(req)
    _marcar_existe_sn(session, rows)
    return {"rows": rows, "total": len(rows)}


@router.get("/gestao-ativos/config")
def gestao_ativos_config(req: Request):
    require_permission(req, "servicenow", "view")
    return config_gestao_ativos()
