"""Módulo de Indicadores (RMR) — isolado, servido em /indicadores.

- LEITURA no ServiceNow pela API REST com a CONTA DE SERVIÇO (somente leitura).
- Cálculo dos indicadores do RMR conforme especificação da operação SPARE.
- Armazenamento em banco PRÓPRIO (database_indicadores), separado do portal.
- Página estática em /indicadores (mesma ideia do /tv2), acessível pela URL.

Carregado de forma isolada no main.py: qualquer erro aqui NÃO derruba o portal.
"""
from __future__ import annotations

import calendar
import logging
import threading
import time
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
SLA_NAME_LIKE = getattr(_cfg, "SN_SLA_NAME_LIKE", "SPARE") or "SPARE"
SLA_STAGE = getattr(_cfg, "SN_SLA_STAGE", "completed") or ""
SLA_EXTRA = getattr(_cfg, "SN_SLA_EXTRA", "") or ""
SLA_DATE = getattr(_cfg, "SN_SLA_DATE_FIELD", "task.closed_at") or "task.closed_at"

# Estados/filtros de incident (configuráveis em config.py)
ST_ABERTO = getattr(_cfg, "SN_STATE_ABERTO", "1,2,3")
ST_ATEND = getattr(_cfg, "SN_STATE_ATENDIMENTO", "1,2")
ST_RESOLV = getattr(_cfg, "SN_STATE_RESOLVIDO", "6,7")
ST_CANCEL = getattr(_cfg, "SN_STATE_CANCELADO", "8")
RESOLVED_DATE = getattr(_cfg, "SN_RESOLVED_DATE_FIELD", "closed_at")
BACKLOG_DATE = getattr(_cfg, "SN_BACKLOG_DATE_FIELD", "opened_at")
STATUS_FIELD = getattr(_cfg, "SN_STATUS_FIELD", "state")
BU_FIELD = getattr(_cfg, "SN_BU_FIELD", "company")
PRIORITIZED_Q = getattr(_cfg, "SN_PRIORITIZED_QUERY", "")
SUB_SLED = getattr(_cfg, "SN_SUB_SLED_LIKE", "sled")
SUB_COLETOR = getattr(_cfg, "SN_SUB_COLETOR_LIKE", "coletor")

# Rótulos legíveis para os estados numéricos de incident.
STATE_LABELS = {
    "1": "Novo", "2": "Em andamento", "3": "Em espera",
    "6": "Resolvido", "7": "Encerrado", "8": "Cancelado",
}


def _inc(extra: str) -> str:
    """Filtro base de incident na fila do SPARE + fragmento extra."""
    return f"assignment_group.name={QUEUE}^{extra}"


# ── Configuração efetiva (defaults do config.py + overrides do banco) ────
# Campos editáveis pela UI (Parâmetros → Configuração Módulos → Indicadores).
_CONFIG_DEFAULTS = {
    "state_aberto": ST_ABERTO,
    "state_atendimento": ST_ATEND,
    "state_resolvido": ST_RESOLV,
    "state_cancelado": ST_CANCEL,
    "resolved_date_field": RESOLVED_DATE,
    "backlog_date_field": BACKLOG_DATE,
    "status_field": STATUS_FIELD,
    "bu_field": BU_FIELD,
    "prioritized_query": PRIORITIZED_Q,
    "sub_sled_like": SUB_SLED,
    "sub_coletor_like": SUB_COLETOR,
}


def _effective_config() -> dict:
    """Defaults sobrescritos pelo que estiver salvo no banco (UI)."""
    cfg = dict(_CONFIG_DEFAULTS)
    try:
        overrides = db.obter_config() or {}
    except Exception:  # noqa: BLE001
        overrides = {}
    for k, v in overrides.items():
        if k in cfg and v not in (None, ""):
            cfg[k] = str(v).strip()
    return cfg


def _sla_base_query() -> str:
    """Filtro base das ANS: só as que têm SPARE no nome (não outras filas),
    concluídas, mais eventual filtro extra (ex.: só Resolução)."""
    q = "sla.nameLIKE" + SLA_NAME_LIKE
    if SLA_STAGE:
        q += "^stage=" + SLA_STAGE
    if SLA_EXTRA:
        q += "^" + SLA_EXTRA
    return q


# ── Cliente REST do ServiceNow (conta de serviço, só leitura) ──────────
def _proxies():
    if _cfg.SN_API_PROXY:
        return {"http": _cfg.SN_API_PROXY, "https": _cfg.SN_API_PROXY}
    return None


def _checar_conta():
    if not _cfg.SN_API_USER or not _cfg.SN_API_PASS:
        raise RuntimeError(
            "Conta de serviço do ServiceNow não configurada "
            "(defina SN_API_USER e SN_API_PASS no ambiente do serviço)."
        )


def _sn_stats(table: str, query: str, group_by: str | None = None,
              display_value: str = "true") -> object:
    """API de AGREGAÇÃO do ServiceNow — conta no servidor, sem baixar linhas.

    Sem group_by → retorna um int (contagem total).
    Com group_by  → retorna lista de (valor, contagem).
    """
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _checar_conta()

    params = {"sysparm_query": query, "sysparm_count": "true"}
    if group_by:
        params["sysparm_group_by"] = group_by
        params["sysparm_display_value"] = display_value

    r = requests.get(
        f"{_cfg.SN_API_BASE}/api/now/stats/{table}", params=params,
        auth=(_cfg.SN_API_USER, _cfg.SN_API_PASS), headers={"Accept": "application/json"},
        proxies=_proxies(), verify=_cfg.VERIFY_SSL, timeout=45,
    )
    if r.status_code == 401:
        raise RuntimeError("ServiceNow 401 — conta de serviço inválida ou sem papel de API.")
    r.raise_for_status()
    res = r.json().get("result")

    if group_by:
        out = []
        for g in (res or []):
            gf = g.get("groupby_fields") or []
            val = gf[0].get("value") if gf else ""
            cnt = int((g.get("stats") or {}).get("count") or 0)
            out.append((val, cnt))
        return out
    return int(((res or {}).get("stats") or {}).get("count") or 0)


def _sn_rest_get(table: str, query: str, fields: str,
                 display_value: str = "false", limit: int = 8000) -> list[dict]:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    _checar_conta()

    proxies = _proxies()
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
            verify=_cfg.VERIFY_SSL, timeout=45,
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


def _mes_janela(ano: int, mes: int) -> str:
    """Fragmento de query para 'campo dentro do mês' via gs.dateGenerate."""
    ultimo = calendar.monthrange(ano, mes)[1]
    ini = f"{ano}-{mes:02d}-01"
    fim = f"{ano}-{mes:02d}-{ultimo:02d}"
    return ("javascript:gs.dateGenerate('%s','00:00:00')" % ini,
            "javascript:gs.dateGenerate('%s','23:59:59')" % fim)


# ── Janela dos últimos 12 meses ────────────────────────────────────────
def _ultimos_12_meses() -> list[dict]:
    """Lista dos últimos 12 meses (mais antigo → atual), cada um com a janela
    de datas (gs.dateGenerate) para uso em queries do ServiceNow."""
    hoje = datetime.now()
    base = hoje.year * 12 + (hoje.month - 1) - 11  # 11 meses atrás
    out = []
    for i in range(12):
        idx = base + i
        ano = idx // 12
        mes = idx % 12 + 1
        ini, fim = _mes_janela(ano, mes)
        out.append({"key": f"{ano}-{mes:02d}", "ano": ano, "mes": mes,
                    "ini": ini, "fim": fim})
    return out


def _conta(table: str, query: str) -> int:
    try:
        return int(_sn_stats(table, query))
    except Exception as exc:  # noqa: BLE001
        _log.warning("contagem falhou (%s): %s", query, exc)
        return 0


# ── KPIs (cartões) ─────────────────────────────────────────────────────
def _kpis(C: dict) -> dict:
    backlog = _conta("incident", _inc(f"stateIN{C['state_aberto']}"))
    ritms = _conta("task", f"assignment_group.name={QUEUE}^numberSTARTSWITHRITM^active=true")
    ag_atend = _conta("incident", _inc(f"stateIN{C['state_atendimento']}"))
    priorizados = None
    if C.get("prioritized_query"):
        priorizados = _conta("incident", _inc(f"stateIN{C['state_aberto']}^{C['prioritized_query']}"))
    return {
        "backlog": backlog,
        "ritms": ritms,
        "ag_atendimento": ag_atend,
        "priorizados": priorizados,
    }


# ── Séries mensais (12 meses) ──────────────────────────────────────────
def _tratado_por_mes(meses: list[dict], C: dict) -> list[dict]:
    """Tickets tratados/resolvidos por mês (incident resolvido/encerrado)."""
    out = []
    for m in meses:
        q = _inc(f"stateIN{C['state_resolvido']}"
                 f"^{C['resolved_date_field']}>={m['ini']}^{C['resolved_date_field']}<={m['fim']}")
        out.append({"mes": m["key"], "total": _conta("incident", q)})
    return out


def _sla_por_mes(meses: list[dict]) -> dict:
    """SLA % por mês (ANS SPARE concluídas dentro do prazo)."""
    base_sla = _sla_base_query()
    serie, tot, viol = [], 0, 0
    for m in meses:
        q = f"{base_sla}^{SLA_DATE}>={m['ini']}^{SLA_DATE}<={m['fim']}"
        try:
            grupos = _sn_stats("task_sla", q, group_by="has_breached", display_value="false")
        except Exception:
            grupos = []
        total_m = sum(c for _, c in grupos)
        viol_m = sum(c for v, c in grupos if str(v).strip().lower() in ("true", "1"))
        dentro = total_m - viol_m
        pct = round(dentro / total_m * 100, 1) if total_m else 0
        serie.append({"mes": m["key"], "total": total_m, "dentro": dentro,
                      "violado": viol_m, "pct": pct})
        tot += total_m
        viol += viol_m
    return {
        "por_mes": serie,
        "compliance_pct": round((tot - viol) / tot * 100, 1) if tot else 0,
        "total": tot, "dentro": tot - viol, "violado": viol,
    }


def _iter_meses(inicio: str, fim: str) -> list[str]:
    """Lista contínua de 'YYYY-MM' de inicio até fim (inclusive)."""
    ya, ma = int(inicio[:4]), int(inicio[5:7])
    yb, mb = int(fim[:4]), int(fim[5:7])
    a, b = ya * 12 + (ma - 1), yb * 12 + (mb - 1)
    return [f"{i // 12}-{i % 12 + 1:02d}" for i in range(a, b + 1)]


def _backlog_por_mes(meses: list[dict], C: dict) -> list[dict]:
    """Backlog (incidentes ABERTOS) agrupado pelo MÊS da data de referência
    (data bouncing). Busca as linhas e agrupa em Python — assim só aparecem
    meses que realmente têm registros com data preenchida, e um campo de data
    inválido não faz a contagem 'vazar' (o ServiceNow ignora condição inválida
    e devolveria o backlog inteiro em todo mês)."""
    field = C["backlog_date_field"]
    q = _inc(f"stateIN{C['state_aberto']}")
    rows = _sn_rest_get("incident", q, f"number,opened_at,{field}",
                        display_value="false", limit=6000)
    cont: dict[str, int] = {}
    for r in rows:
        # Usa a data bouncing; se estiver vazia (ex.: coletores/SLED), cai
        # para a data de ABERTURA do chamado — assim nada fica de fora.
        mes = _mes(_mv(r.get(field))) or _mes(_mv(r.get("opened_at")))
        if not mes:
            continue
        cont[mes] = cont.get(mes, 0) + 1
    if not cont:
        return []
    chaves = sorted(cont.keys())
    # eixo contínuo do 1º mês com dado até o mês atual; no máx. 12 meses.
    fim = datetime.now().strftime("%Y-%m")
    todos = _iter_meses(chaves[0], max(chaves[-1], fim))
    todos = todos[-12:]
    return [{"mes": k, "total": cont.get(k, 0)} for k in todos]


def _por_mes_sub(meses: list[dict], like: str, C: dict) -> list[dict]:
    """Abertos por mês por subcategoria (LIKE), state != cancelado."""
    out = []
    for m in meses:
        q = _inc(f"state!={C['state_cancelado']}^subcategoryLIKE{like}"
                 f"^opened_at>={m['ini']}^opened_at<={m['fim']}")
        out.append({"mes": m["key"], "total": _conta("incident", q)})
    return out


# ── Distribuições (group by) ───────────────────────────────────────────
def _grupo(table: str, query: str, group_by: str) -> list[dict]:
    try:
        pares = _sn_stats(table, query, group_by=group_by, display_value="true")
    except Exception as exc:  # noqa: BLE001
        _log.warning("group_by %s falhou: %s", group_by, exc)
        return []
    out = [{"nome": (str(n).strip() or "(vazio)"), "total": c} for n, c in pares]
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def _abertos_por_status(C: dict) -> list[dict]:
    q = _inc(f"stateIN{C['state_aberto']}")
    campo = C["status_field"]
    dv = "false" if campo == "state" else "true"
    try:
        pares = _sn_stats("incident", q, group_by=campo, display_value=dv)
    except Exception:
        pares = []
    out = []
    for n, c in pares:
        nome = str(n).strip()
        if campo == "state":
            nome = STATE_LABELS.get(nome, nome or "(vazio)")
        out.append({"nome": nome or "(vazio)", "total": c})
    out.sort(key=lambda x: x["total"], reverse=True)
    return out


def _por_localidade(C: dict) -> list[dict]:
    return _grupo("incident", _inc(f"stateIN{C['state_aberto']}"), "location")[:25]


def _por_bu(C: dict) -> list[dict]:
    return _grupo("incident", _inc(f"stateIN{C['state_aberto']}"), C["bu_field"])[:10]


def _por_subcategoria(C: dict) -> list[dict]:
    return _grupo("incident", _inc(f"stateIN{C['state_aberto']}"), "subcategory")[:10]


# ── Cálculo completo ───────────────────────────────────────────────────
def _calcular_tudo() -> dict:
    meses = _ultimos_12_meses()
    C = _effective_config()
    resultado: dict = {
        "gerado_em": datetime.now().isoformat(),
        "meses": [m["key"] for m in meses],
        "config_usada": C,
        "erros": {},
    }
    for chave, fn in (
        ("kpis", lambda: _kpis(C)),
        ("tratado_por_mes", lambda: _tratado_por_mes(meses, C)),
        ("sla", lambda: _sla_por_mes(meses)),
        ("backlog_por_mes", lambda: _backlog_por_mes(meses, C)),
        ("abertos_por_status", lambda: _abertos_por_status(C)),
        ("por_localidade", lambda: _por_localidade(C)),
        ("por_bu", lambda: _por_bu(C)),
        ("por_subcategoria", lambda: _por_subcategoria(C)),
        ("sled_por_mes", lambda: _por_mes_sub(meses, C["sub_sled_like"], C)),
        ("coletor_por_mes", lambda: _por_mes_sub(meses, C["sub_coletor_like"], C)),
    ):
        try:
            resultado[chave] = fn()
        except Exception as exc:  # noqa: BLE001
            _log.error("Indicador %s falhou: %s", chave, exc, exc_info=True)
            resultado["erros"][chave] = str(exc)
            resultado[chave] = None
    return resultado


# ── Agendador em segundo plano ─────────────────────────────────────────
REFRESH_MIN = getattr(_cfg, "SN_INDIC_REFRESH_MIN", 2) or 0
_scheduler_started = False


def _recalcular_e_salvar() -> None:
    ref = datetime.now().strftime("%Y-%m")
    dados = _calcular_tudo()
    db.salvar_snapshot(ref, dados, criado_por="scheduler")
    _log.info("Indicadores: snapshot %s atualizado em segundo plano.", ref)


def _scheduler_loop() -> None:
    time.sleep(15)  # deixa o app terminar de subir
    while True:
        try:
            _recalcular_e_salvar()
        except Exception as exc:  # noqa: BLE001
            _log.error("Agendador de indicadores falhou: %s", exc, exc_info=True)
        time.sleep(max(1, REFRESH_MIN) * 60)


def start_scheduler() -> None:
    """Inicia o recálculo periódico dos indicadores (idempotente). Desligado
    quando SN_INDIC_REFRESH_MIN <= 0."""
    global _scheduler_started
    if _scheduler_started or REFRESH_MIN <= 0:
        return
    _scheduler_started = True
    th = threading.Thread(target=_scheduler_loop, daemon=True,
                          name="indicadores-scheduler")
    th.start()
    _log.info("Indicadores: agendador iniciado (a cada %s min).", REFRESH_MIN)


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


@router.get("/api/indicadores/config")
def indicadores_get_config(req: Request):
    """Config efetiva + defaults + overrides salvos (para a tela de Parâmetros).
    Somente ADMIN."""
    from security import require_permission
    require_permission(req, "parametros", "admin")
    try:
        overrides = db.obter_config() or {}
    except Exception:  # noqa: BLE001
        overrides = {}
    return {
        "efetiva": _effective_config(),
        "defaults": _CONFIG_DEFAULTS,
        "overrides": overrides,
        "campos": list(_CONFIG_DEFAULTS.keys()),
    }


@router.put("/api/indicadores/config")
def indicadores_put_config(payload: dict, req: Request):
    """Salva overrides da config dos indicadores. Somente ADMIN."""
    from security import require_permission
    sd = require_permission(req, "parametros", "admin")
    # Aceita só chaves conhecidas; strings limpas.
    limpo = {}
    for k in _CONFIG_DEFAULTS:
        if k in payload and payload[k] is not None:
            limpo[k] = str(payload[k]).strip()
    try:
        db.salvar_config(limpo, atualizado_por=sd.get("username", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, "Falha ao salvar config: %s" % exc)
    return {"ok": True, "efetiva": _effective_config()}


@router.get("/api/indicadores/diag-backlog")
def indicadores_diag_backlog(req: Request, field: str = ""):
    """Diagnóstico do backlog: mostra, para os incidentes ABERTOS da fila, se o
    campo de data (bouncing) tem valor e a distribuição por mês — para confirmar
    o nome interno correto do campo. Somente ADMIN."""
    from security import require_permission
    require_permission(req, "parametros", "admin")
    C = _effective_config()
    campo = field or C["backlog_date_field"]
    q = _inc(f"stateIN{C['state_aberto']}")
    try:
        rows = _sn_rest_get("incident", q, f"number,opened_at,{campo}",
                            display_value="false", limit=6000)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Falha ao consultar incidentes: %s" % exc)
    total = len(rows)
    com_valor = sum(1 for r in rows if _mv(r.get(campo)))
    # Distribuição usada no gráfico: data bouncing e, se vazia, data de abertura.
    por_mes: dict[str, int] = {}
    for r in rows:
        mes = _mes(_mv(r.get(campo))) or _mes(_mv(r.get("opened_at"))) or "(sem data)"
        por_mes[mes] = por_mes.get(mes, 0) + 1
    dist = sorted(por_mes.items(), key=lambda x: x[0])
    exemplos = [{"number": _mv(r.get("number")), "valor": _mv(r.get(campo))}
                for r in rows[:5]]
    return {
        "campo_testado": campo,
        "backlog_total_aberto": total,
        "com_valor_no_campo": com_valor,
        "sem_valor_no_campo": total - com_valor,
        "campo_parece_valido": com_valor > 0,
        "por_mes": [{"mes": m, "total": c} for m, c in dist],
        "exemplos": exemplos,
    }


@router.get("/api/indicadores/diag-slas")
def indicadores_diag_slas(like: str = ""):
    """Diagnóstico: lista os NOMES de ANS (task_sla) e a contagem de cada um,
    para confirmarmos o filtro correto (o que tem 'SPARE' no nome). Use
    ?like=SPARE para restringir, ou vazio para ver todos ligados à fila."""
    termo = like or SLA_NAME_LIKE
    q = "sla.nameLIKE" + termo if termo else f"task.assignment_group.name={QUEUE}"
    try:
        grupos = _sn_stats("task_sla", q, group_by="sla.name", display_value="true")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Falha ao consultar ANS: %s" % exc)
    grupos.sort(key=lambda x: x[1], reverse=True)
    return {
        "filtro": q,
        "config": {
            "SN_SLA_NAME_LIKE": SLA_NAME_LIKE, "SN_SLA_STAGE": SLA_STAGE,
            "SN_SLA_EXTRA": SLA_EXTRA, "SN_SLA_DATE_FIELD": SLA_DATE,
        },
        "ans": [{"nome": n or "(sem nome)", "total": c} for n, c in grupos],
    }


@router.post("/api/indicadores/atualizar")
def indicadores_atualizar(req: Request, referencia: str = ""):
    """Recalcula os indicadores no ServiceNow (conta de serviço) e grava snapshot."""
    ref = referencia or datetime.now().strftime("%Y-%m")
    dados = _calcular_tudo()
    if all(dados.get(k) is None for k in ("kpis", "tratado_por_mes", "sla")):
        raise HTTPException(502, "Falha ao consultar o ServiceNow: " + "; ".join(
            f"{k}: {v}" for k, v in dados.get("erros", {}).items()) or "erro desconhecido")
    try:
        db.salvar_snapshot(ref, dados, criado_por="indicadores")
    except Exception as exc:  # noqa: BLE001
        _log.error("Falha ao gravar snapshot: %s", exc, exc_info=True)
    return {"ok": True, "referencia": ref, "dados": dados}
