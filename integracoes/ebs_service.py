"""EBS (Oracle E-Business Suite) integration service.

Handles authentication and asset lookup against the corporate ERP.
All configuration (URLs, timeouts, SSL, worker count) comes from
config.get_settings() — nothing is read from os.environ here.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import Any

import requests

from config import get_settings

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    """Return a stripped string, or empty string for None."""
    if value is None:
        return ""
    return str(value).strip()


def _first(data: dict[str, Any] | Any, *keys: str) -> Any:
    """Return the first non-empty value found among *keys* in *data*."""
    if not isinstance(data, dict):
        return None
    for key in keys:
        val = data.get(key)
        if val not in (None, ""):
            return val
    return None


# ---------------------------------------------------------------------------
# Public helpers (used by other modules)
# ---------------------------------------------------------------------------

def normalize_company(book_type_code: str | None) -> str:
    """Derive the company name (RENNER / YOUCOM / CAMICADO) from a book code."""
    s = _clean(book_type_code).upper()
    for company in ("RENNER", "YOUCOM", "CAMICADO"):
        if company in s:
            return company
    return s.replace("FA_", "") if s else ""


def parse_date(value: Any) -> str | None:
    """Normalise a date value (str or date) to ISO-8601 (YYYY-MM-DD)."""
    if not value:
        return None
    if isinstance(value, date):
        return value.isoformat()

    s = _clean(value)
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S")
    for fmt in formats:
        try:
            return datetime.strptime(s[:19], fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def login(username: str, password: str) -> dict[str, Any]:
    """Authenticate against EBS and return cookies + bearer token.

    Tries JSON body first; falls back to form-encoded if the server
    rejects the first attempt.

    Raises
    ------
    ValueError
        When credentials are rejected (HTTP 4xx on both attempts).
    """
    cfg = get_settings()
    session = requests.Session()

    payload = {"username": username, "password": password}

    response = session.post(
        cfg.EBS_LOGIN_URL,
        json=payload,
        timeout=cfg.TIMEOUT,
        verify=cfg.VERIFY_SSL,
    )

    if response.status_code >= 400:
        # Fallback: form-encoded
        response = session.post(
            cfg.EBS_LOGIN_URL,
            data=payload,
            timeout=cfg.TIMEOUT,
            verify=cfg.VERIFY_SSL,
        )

    if response.status_code >= 400:
        raise ValueError("Usuário ou senha AD inválidos.")

    token: str | None = None
    try:
        body = response.json()
        token = (
            _first(body, "token", "access_token", "jwt")
            or _first(
                body.get("data", {}) if isinstance(body, dict) else {},
                "token",
                "access_token",
            )
        )
    except (ValueError, AttributeError):
        pass

    return {
        "cookies": requests.utils.dict_from_cookiejar(session.cookies),
        "token": token,
    }


# ---------------------------------------------------------------------------
# Session / response parsing internals
# ---------------------------------------------------------------------------

def _build_session(auth: dict[str, Any]) -> requests.Session:
    """Create a requests.Session pre-loaded with auth cookies/token."""
    session = requests.Session()

    for name, value in (auth.get("cookies") or {}).items():
        session.cookies.set(name, value)

    token = auth.get("token")
    if token:
        session.headers["Authorization"] = f"Bearer {token}"

    return session


def _flatten(payload: Any) -> list[dict[str, Any]]:
    """Unwrap the EBS response envelope into a flat list of result dicts."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ("resultados", "results", "data", "items", "ativos"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]

    return [payload]


def _extract(raw: dict[str, Any], query: str) -> dict[str, Any]:
    """Normalise a single EBS busca-imobilizado response into a flat dict.

    The EBS response nests primary fields under an ``ebs`` key and places
    complementary data (valor_contabil, atribuicoes, estoque, movimentacoes,
    po_compra) at the root level.
    """
    if not isinstance(raw, dict):
        return {
            "pesquisado": query,
            "encontrado": False,
            "erro": "Formato de resposta EBS invalido.",
            "fonte": "EBS",
        }

    ebs: dict[str, Any] = raw.get("ebs") if isinstance(raw.get("ebs"), dict) else raw

    # --- Complementary collections -----------------------------------------
    financial: list[dict[str, Any]] = raw.get("valor_contabil") or ebs.get("valor_contabil") or []
    assignments: list[dict[str, Any]] = raw.get("atribuicoes") or ebs.get("atribuicoes") or []

    if isinstance(financial, dict):
        financial = [financial]
    if isinstance(assignments, dict):
        assignments = [assignments]

    # --- Book type code ----------------------------------------------------
    book = _first(ebs, "book_type_code", "bookTypeCode")

    if not book:
        for row in financial:
            if not isinstance(row, dict):
                continue
            book = _first(row, "book_type_code", "bookTypeCode")
            if book:
                break

    # --- Core fields -------------------------------------------------------
    cost_asset = _first(ebs, "custo_asset", "cost", "asset_cost")
    dpis = _first(
        ebs,
        "dpis",
        "date_placed_in_service",
        "data_ativacao",
        "data_aquisicao",
    )

    # --- Primary financial record ------------------------------------------
    primary_financial: dict[str, Any] = {}
    normalized_book = _clean(book).upper()

    for row in financial:
        if not isinstance(row, dict):
            continue
        row_book = _clean(_first(row, "book_type_code", "bookTypeCode")).upper()
        if row_book == normalized_book:
            primary_financial = row
            break

    if not primary_financial and financial:
        first_fin = financial[0]
        primary_financial = first_fin if isinstance(first_fin, dict) else {}

    # --- Primary assignment ------------------------------------------------
    primary_assignment: dict[str, Any] = {}
    if assignments:
        first_assign = assignments[0]
        primary_assignment = first_assign if isinstance(first_assign, dict) else {}

    # --- Identifiers -------------------------------------------------------
    asset_id = _first(ebs, "asset_id", "assetId", "id_ativo")
    asset_number = _first(
        ebs, "asset_number", "ativo", "numero_ativo", "imobilizado",
    )

    # --- Build result dict -------------------------------------------------
    result: dict[str, Any] = {
        "pesquisado": query,
        "encontrado": bool(raw.get("encontrado", True)),
        "modo_busca": _clean(raw.get("modo_busca") or ebs.get("modo_busca") or "numero"),
        "empresa": normalize_company(book),
        "book_type_code": _clean(book),
        "asset_id": _clean(asset_id),
        "ativo": _clean(asset_number),
        "imobilizado": _clean(raw.get("imobilizado") or asset_id or asset_number),
        "etiqueta": _clean(_first(ebs, "tag_number", "etiqueta", "tag")),
        "numero_serie": _clean(_first(ebs, "serial_number", "numero_serie", "serial")),
        "descricao": _clean(
            _first(
                ebs,
                "asset_desc",
                "description",
                "descricao",
                "descricao_bem",
                "item_description",
            )
        ),
        "custo_asset": cost_asset,
        "dpis": parse_date(dpis),
        # Financial
        "livro_contabil": _clean(_first(primary_financial, "book_type_code")),
        "custo_contabil": _first(primary_financial, "custo", "cost"),
        "depreciacao_acumulada": _first(
            primary_financial,
            "depreciacao_acumulada",
            "accumulated_depreciation",
        ),
        "valor_residual": _first(
            primary_financial,
            "valor_contabil_liquido",
            "net_book_value",
            "valor_residual",
        ),
        "depreciacao_ytd": _first(primary_financial, "depreciacao_ytd"),
        "period_counter": _first(primary_financial, "period_counter"),
        # Assignment
        "local_atribuido": _clean(
            _first(primary_assignment, "local_atribuido", "location", "local")
        ),
        "conta_despesas": _clean(
            _first(primary_assignment, "conta_despesas", "expense_account")
        ),
        "funcionario_id": _first(primary_assignment, "funcionario_id"),
        # Raw collections
        "valor_contabil": financial,
        "atribuicoes": assignments,
        "estoque": raw.get("estoque"),
        "movimentacoes": raw.get("movimentacoes") or [],
        "po_compra": raw.get("po_compra") or [],
        "fonte": "EBS",
        "raw": raw,
    }

    return result


# ---------------------------------------------------------------------------
# Public API — single and batch search
# ---------------------------------------------------------------------------

def search_one(auth: dict[str, Any], query: str) -> dict[str, Any]:
    """Look up a single asset in EBS by its number (GET request).

    Returns a normalised result dict.  Raises ``PermissionError`` when
    the session token has expired (HTTP 401/403).
    """
    cfg = get_settings()
    session = _build_session(auth)
    query = _clean(query)

    if not query:
        return {
            "pesquisado": query,
            "encontrado": False,
            "erro": "Identificador vazio.",
            "fonte": "EBS",
        }

    try:
        response = session.get(
            cfg.EBS_SEARCH_URL,
            params={"numero": query},
            timeout=cfg.TIMEOUT,
            verify=cfg.VERIFY_SSL,
            headers={"Accept": "application/json"},
        )

        # --- Auth errors (propagate so callers can re-authenticate) --------
        if response.status_code in (401, 403):
            raise PermissionError("Sessao EBS expirada.")

        # --- 404 — asset not found -----------------------------------------
        if response.status_code == 404:
            return {
                "pesquisado": query,
                "encontrado": False,
                "erro": "Ativo nao encontrado no EBS.",
                "fonte": "EBS",
                "modo_busca": "numero",
            }

        # --- Other HTTP errors ---------------------------------------------
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = (
                        payload.get("detail")
                        or payload.get("message")
                        or payload.get("erro")
                        or ""
                    )
            except ValueError:
                detail = response.text[:300]

            message = f"EBS retornou HTTP {response.status_code}"
            if detail:
                message += f": {detail}"

            return {
                "pesquisado": query,
                "encontrado": False,
                "erro": message,
                "fonte": "EBS",
                "modo_busca": "numero",
            }

        # --- Parse JSON body -----------------------------------------------
        try:
            payload = response.json()
        except ValueError:
            return {
                "pesquisado": query,
                "encontrado": False,
                "erro": "O EBS retornou uma resposta que nao e JSON.",
                "fonte": "EBS",
                "modo_busca": "numero",
            }

        rows = _flatten(payload)

        if not rows:
            return {
                "pesquisado": query,
                "encontrado": False,
                "erro": "O EBS retornou uma resposta vazia.",
                "fonte": "EBS",
                "modo_busca": "numero",
            }

        result = _extract(rows[0], query)
        result["modo_busca"] = "numero"
        return result

    except PermissionError:
        raise
    except Exception as exc:
        return {
            "pesquisado": query,
            "encontrado": False,
            "erro": f"Falha na consulta GET ao EBS: {exc}",
            "fonte": "EBS",
            "modo_busca": "numero",
        }


def search_many(
    auth: dict[str, Any],
    queries: list[str],
) -> list[dict[str, Any]]:
    """Look up multiple assets in parallel using a thread pool.

    Returns a list aligned with *queries* — same length, same order.
    Individual failures are captured in-place (``encontrado: False``).
    """
    cfg = get_settings()
    count = len(queries)
    results: list[dict[str, Any] | None] = [None] * count

    pool_size = min(cfg.MAX_WORKERS, max(1, count))

    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        future_to_index = {
            executor.submit(search_one, auth, q): (i, q)
            for i, q in enumerate(queries)
        }

        for future in as_completed(future_to_index):
            idx, original_query = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as exc:
                results[idx] = {
                    "pesquisado": original_query,
                    "encontrado": False,
                    "erro": str(exc),
                    "fonte": "EBS",
                }

    return results  # type: ignore[return-value]
