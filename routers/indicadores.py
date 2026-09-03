"""Módulo de Indicadores (RMR) — isolado, servido em /indicadores.

- LEITURA no ServiceNow pela API REST com a CONTA DE SERVIÇO (somente leitura).
- Cálculo dos indicadores do RMR conforme especificação da operação SPARE.
- Armazenamento em banco PRÓPRIO (database_indicadores), separado do portal.
- Página estática em /indicadores (mesma ideia do /tv2), acessível pela URL.

Carregado de forma isolada no main.py: qualquer erro aqui NÃO derruba o portal.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import config as _config_mod
import database_indicadores as db

_cfg = _config_mod.get_settings()
_log = logging.getLogger("indicadores")

router = APIRouter(tags=["Indicadores"], include_in_schema=False)

QUEUE = _cfg.SN_INDIC_QUEUE
TMA_START = _cfg.SN_TMA_START_FIELD


# ── Cliente REST do ServiceNow (conta de serviço, só leitura) ──────────
def _sn_rest_get(table: str, query: str, fields: str,
                 display_value: str = "false", limit: int = 20000) -> list[dict]:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if not _cfg.SN_API_USER or not _cfg.SN_API_PASS:
        raise RuntimeError(
            "Conta de serviço do ServiceNow não configurada "
            "(defina SN_API_USER e SN_API_PASS no ambiente do serviço)."
        )
    proxies = None
    if _cfg.SN_API_PROXY:
        proxies = {"http": _cfg.SN_API_PROXY, "https": _cfg.SN_API_PROXY}

    url = f"{_cfg.SN_API_BASE}/api/now/table/{table}"
    out: list[dict] = []
    offset = 0
    PAGE = 1000
    while len(out) < limit:
        page = min(PAGE, limit - len(out))
        params = {
            "sysparm_query": query,
            "sysparm_fields": fields,
            "sysparm_display_value": display_value,
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": str(page),
            "sysparm_offset": str(offset),
        }
        r = requests.get(
            url, params=params, auth=(_cfg.SN_API_USER, _cfg.SN_API_PASS),
            headers={"Accept": "application/json"}, proxies=proxies,
            verify=_cfg.VERIFY_SSL, timeout=90,
        )
        if r.status_code == 401:
            raise RuntimeError("ServiceNow 401 — usuário/senha da conta de serviço inválidos ou sem papel de API.")
        r.raise_for_status()
        rows = r.json().get("result", [])
        out.extend(rows)
        if len(rows) < page:
            break
        offset += len(rows)
    return out


def _mv(v):
    """Valor plano de um campo (string) — a API pode devolver dict ou string."""
    if isinstance(v, dict):
        return v.get("display_value") or v.get("value") or ""
    return v if v is not None else ""


def _is_true(v) -> bool:
    return str(_mv(v)).strip().lower() in ("true", "1", "sim", "yes")


def _mes(dt_str: str) -> str:
    s = str(dt_str or "")
    return s[:7] if len(s) >= 7 else ""


def _meses_do_ano(ano: int) -> list[str]:
    return [f"{ano}-{m:02d}" for m in range(1, 13)]


# ── Indicador 1: Tickets resolvidos (12 meses) + SLA ───────────────────
def _tickets_e_sla() -> dict:
    ano = datetime.now().year
    query = (
        f"task.assignment_group.name={QUEUE}"
        f"^task.closed_atONThis year@javascript:gs.beginningOfThisYear()@javascript:gs.endOfThisYear()"
        f"^task.stateNOT IN8"
    )
    # has_breached: false = dentro do SLA, true = violou.
    rows = _sn_rest_get(
        "task_sla", query,
        "task,task.closed_at,has_breached",
        display_value="false", limit=20000,
    )

    tickets_por_mes: dict[str, set] = {}
    sla_por_mes: dict[str, dict] = {}
    for r in rows:
        mes = _mes(_mv(r.get("task.closed_at")))
        if not mes:
            continue
        task_id = str(_mv(r.get("task")))
        tickets_por_mes.setdefault(mes, set()).add(task_id)
        d = sla_por_mes.setdefault(mes, {"total": 0, "violado": 0})
        d["total"] += 1
        if _is_true(r.get("has_breached")):
            d["violado"] += 1

    meses = _meses_do_ano(ano)
    tickets = [{"mes": m, "total": len(tickets_por_mes.get(m, set()))} for m in meses]

    sla_mes = []
    tot = viol = 0
    for m in meses:
        d = sla_por_mes.get(m, {"total": 0, "violado": 0})
        dentro = d["total"] - d["violado"]
        pct = round(dentro / d["total"] * 100, 1) if d["total"] else 0
        sla_mes.append({"mes": m, "total": d["total"], "dentro": dentro,
                        "violado": d["violado"], "pct": pct})
        tot += d["total"]
        viol += d["violado"]
    sla_geral = round((tot - viol) / tot * 100, 1) if tot else 0

    return {
        "ano": ano,
        "tickets_por_mes": tickets,
        "tickets_total": sum(t["total"] for t in tickets),
        "sla_por_mes": sla_mes,
        "sla_total": tot,
        "sla_violado": viol,
        "sla_dentro": tot - viol,
        "sla_compliance_pct": sla_geral,
    }


# ── Indicador 2: Top 20 lojas + Top 10 subcategorias ───────────────────
def _top_lojas_categorias() -> dict:
    query = (
        f"task.assignment_group.name={QUEUE}"
        f"^task.closed_atONThis year@javascript:gs.beginningOfThisYear()@javascript:gs.endOfThisYear()"
        f"^task.stateNOT IN8"
    )
    rows = _sn_rest_get(
        "task_sla", query,
        "task,task.location,task.subcategory",
        display_value="true", limit=20000,
    )
    lojas: dict[str, set] = {}
    subs: dict[str, set] = {}
    for r in rows:
        task_id = str(_mv(r.get("task")))
        loja = str(_mv(r.get("task.location"))).strip() or "(sem local)"
        sub = str(_mv(r.get("task.subcategory"))).strip() or "(sem subcategoria)"
        lojas.setdefault(loja, set()).add(task_id)
        subs.setdefault(sub, set()).add(task_id)

    top_lojas = sorted(
        ({"loja": k, "total": len(v)} for k, v in lojas.items()),
        key=lambda x: x["total"], reverse=True,
    )[:20]
    top_subs = sorted(
        ({"subcategoria": k, "total": len(v)} for k, v in subs.items()),
        key=lambda x: x["total"], reverse=True,
    )[:10]
    return {"top_lojas": top_lojas, "top_subcategorias": top_subs}


# ── Indicador 5: TMA Coletor e SLED ────────────────────────────────────
def _classifica_sub(sub: str) -> str | None:
    s = (sub or "").strip().lower()
    if "coletor" in s:
        return "Coletor"
    if "sled" in s:
        return "SLED"
    return None


def _tma() -> dict:
    query = (
        f"assignment_group.name={QUEUE}"
        f"^opened_at>=javascript:gs.monthsAgoStart(12)"
        f"^state!=8"
    )
    fields = f"number,opened_at,closed_at,resolved_at,subcategory,state,{TMA_START}"
    rows = _sn_rest_get("incident", query, fields, display_value="false", limit=20000)

    acc = {"Coletor": [], "SLED": []}
    considerados = {"Coletor": 0, "SLED": 0}
    for r in rows:
        cat = _classifica_sub(_mv(r.get("subcategory")))
        if not cat:
            continue
        opened = _mv(r.get("opened_at"))
        closed = _mv(r.get("closed_at"))
        resolved = _mv(r.get("resolved_at")) or closed
        bouncing = _mv(r.get(TMA_START))
        # Somente abertos e encerrados no MESMO mês.
        if not opened or not closed or _mes(opened) != _mes(closed):
            continue
        considerados[cat] += 1
        if not bouncing or not resolved:
            continue
        try:
            d0 = datetime.strptime(bouncing[:19], "%Y-%m-%d %H:%M:%S")
            d1 = datetime.strptime(resolved[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        dias = (d1 - d0).total_seconds() / 86400.0
        if dias >= 0:
            acc[cat].append(dias)

    def media(cat):
        vals = acc[cat]
        return round(sum(vals) / len(vals), 1) if vals else 0

    return {
        "tma": [
            {"categoria": "Coletor", "dias": media("Coletor"),
             "amostra": len(acc["Coletor"]), "no_mes": considerados["Coletor"]},
            {"categoria": "SLED", "dias": media("SLED"),
             "amostra": len(acc["SLED"]), "no_mes": considerados["SLED"]},
        ],
        "campo_inicio": TMA_START,
    }


# ── Cálculo completo ───────────────────────────────────────────────────
def _calcular_tudo() -> dict:
    resultado: dict = {"gerado_em": datetime.now().isoformat(), "erros": {}}
    for chave, fn in (
        ("tickets_sla", _tickets_e_sla),
        ("top", _top_lojas_categorias),
        ("tma", _tma),
    ):
        try:
            resultado[chave] = fn()
        except Exception as exc:  # noqa: BLE001
            _log.error("Indicador %s falhou: %s", chave, exc, exc_info=True)
            resultado["erros"][chave] = str(exc)
            resultado[chave] = None
    return resultado


# ── Endpoints ──────────────────────────────────────────────────────────
@router.get("/indicadores", response_class=HTMLResponse)
def indicadores_page():
    html = (_cfg.STATIC / "indicadores" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


@router.get("/indicadores/", response_class=HTMLResponse)
def indicadores_page_slash():
    return indicadores_page()


@router.get("/api/indicadores/dados")
def indicadores_dados(referencia: str = ""):
    """Retorna o snapshot mais recente (ou de um mês YYYY-MM), do banco próprio."""
    snap = db.obter_snapshot(referencia) if referencia else db.ultimo_snapshot()
    return {
        "snapshot": snap,
        "referencias": db.listar_referencias(),
        "config": {"queue": QUEUE, "campo_tma": TMA_START},
    }


@router.post("/api/indicadores/atualizar")
def indicadores_atualizar(req: Request, referencia: str = ""):
    """Recalcula os indicadores no ServiceNow (conta de serviço) e grava snapshot."""
    ref = referencia or datetime.now().strftime("%Y-%m")
    dados = _calcular_tudo()
    if all(dados.get(k) is None for k in ("tickets_sla", "top", "tma")):
        raise HTTPException(502, "Falha ao consultar o ServiceNow: " + "; ".join(
            f"{k}: {v}" for k, v in dados.get("erros", {}).items()) or "erro desconhecido")
    try:
        db.salvar_snapshot(ref, dados, criado_por="indicadores")
    except Exception as exc:  # noqa: BLE001
        _log.error("Falha ao gravar snapshot: %s", exc, exc_info=True)
    return {"ok": True, "referencia": ref, "dados": dados}
