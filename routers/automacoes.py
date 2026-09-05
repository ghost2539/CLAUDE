"""Automação de encerramento/encaminhamento de chamados entregues.

- Roda 3x ao dia (config AUTOMACOES_HORARIOS) e/ou por botão na aba Correios.
- Só age em chamados cujo ÚLTIMO evento de rastreio é ENTREGUE (evita fechar
  entregas com problema que o portal marca como entregue).
- Casa a subcategoria com as REGRAS configuráveis (Parâmetros → Automações) e
  encerra ou encaminha para outra fila, com a mensagem da regra.
- Escreve no ServiceNow com a SESSÃO DO USUÁRIO (mesmo esquema do consulta-
  times), não com a conta de serviço. A rotina agendada usa a sessão salva do
  usuário que a ativou (ou uma sessão viva dele); se não houver sessão válida,
  a execução é registrada como adiada.
- Todas as ações vão para um LOG em banco isolado (consulta em Parâmetros).

Carregado de forma isolada no main.py: erro aqui não derruba o portal.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

import config as _config_mod
import database_automacoes as db
from security import require_permission, get_session, SESSIONS
from routers.servicenow import (
    _sn_session_from_portal, _sn_session_from_cookies, _sn_session_valida,
    _sn_query, _sn_query_all, _sn_update, _extract_tracking_code, _TRACKING_RE,
    INCIDENT_TABLE, DEFAULT_QUEUE,
)
from routers.encerramento import FIELDS as ENC_FIELDS, _estado_canonico, CLOSE_CODE, _display
from routers.correios import consultar_rastreio

_cfg = _config_mod.get_settings()
_log = logging.getLogger("automacoes")

router = APIRouter(prefix="/api/automacoes", tags=["Automações"])

# Códigos de evento dos Correios que representam ENTREGUE ao destinatário.
_DELIVERED_CODES = {"BDE", "BDI"}


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", " ")


def _ultimo_evento_entregue(rastreio: dict) -> tuple[bool, dict | None]:
    """True se o evento MAIS RECENTE do rastreio for uma entrega ao destinatário."""
    evs = rastreio.get("eventos") or []
    if not evs:
        return False, None
    latest = max(evs, key=lambda e: str(e.get("data") or ""))
    code = str(latest.get("codigo") or "").upper()
    return code in _DELIVERED_CODES, latest


DEFAULT_TRACKING_FIELD = "sys_tags"


def _tracking_from(inc: dict, field: str) -> str:
    """Extrai o código de rastreio do campo configurado (padrão sys_tags).
    Se vazio, tenta o correlation_display como reserva."""
    val = inc.get(field, "")
    if isinstance(val, dict):
        val = val.get("display_value", val.get("value", ""))
    if val:
        m = _TRACKING_RE.search(str(val))
        if m:
            return m.group(0).upper()
    return _extract_tracking_code(inc) if field != "correlation_display" else ""


def _match_regra(subcategoria: str, regras: list[dict]) -> dict | None:
    n = _norm(subcategoria)
    if not n:
        return None
    for r in regras:
        if not r.get("ativo"):
            continue
        aliases = [_norm(a) for a in re.split(r"[\n;,]+", r.get("subcategorias", "")) if a.strip()]
        for a in aliases:
            if a and (a == n or a in n or n in a):
                return r
    return None


def _grupo_sys_id(session, nome: str) -> str:
    recs = _sn_query(session, "sys_user_group", f"name={nome}", "sys_id,name", 1, display_value=False)
    if recs:
        sid = recs[0].get("sys_id", "")
        return sid.get("value", "") if isinstance(sid, dict) else (sid or "")
    return ""


# ── Núcleo da rotina ────────────────────────────────────────────────────
def _rodar(session, origem: str, usuario: str) -> dict:
    """Percorre a fila SPARE, aplica as regras e registra tudo no log."""
    regras = db.listar_regras()
    cfg = db.obter_config()
    tracking_field = (cfg.get("tracking_field") or DEFAULT_TRACKING_FIELD).strip()
    queue = DEFAULT_QUEUE
    sn_query = f"assignment_group.name={queue}^stateIN2,3^ORDERBYDESCsys_created_on"
    fields = ENC_FIELDS
    if tracking_field and tracking_field not in fields:
        fields = fields + "," + tracking_field
    try:
        incidentes = _sn_query_all(session, INCIDENT_TABLE, sn_query, fields)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Falha ao consultar o ServiceNow: %s" % exc)

    resumo = {"analisados": 0, "encerrados": 0, "encaminhados": 0,
              "ignorados": 0, "erros": 0, "acoes": []}

    for inc in incidentes:
        resumo["analisados"] += 1
        number = _display(inc.get("number"))
        sys_id = _display(inc.get("sys_id"))
        subcat = _display(inc.get("subcategory"))
        tracking = _tracking_from(inc, tracking_field)

        if not tracking:
            resumo["ignorados"] += 1
            continue
        try:
            rastreio = consultar_rastreio(tracking)
        except Exception:  # noqa: BLE001
            resumo["ignorados"] += 1
            continue
        entregue, _ev = _ultimo_evento_entregue(rastreio)
        if not entregue:
            resumo["ignorados"] += 1
            continue

        regra = _match_regra(subcat, regras)
        if not regra:
            resumo["ignorados"] += 1
            continue

        acao = regra.get("acao", "encerrar")
        try:
            if acao == "encaminhar":
                gid = _grupo_sys_id(session, regra.get("fila_destino", ""))
                if not gid:
                    raise RuntimeError("fila destino não encontrada: %s" % regra.get("fila_destino"))
                _sn_update(session, INCIDENT_TABLE, sys_id, {
                    "assignment_group": gid,
                    "work_notes": regra.get("mensagem", ""),
                })
                resumo["encaminhados"] += 1
                db.add_log(origem=origem, usuario=usuario, number=number, sys_id=sys_id,
                           subcategoria=subcat, acao="encaminhar", fila_origem=queue,
                           fila_destino=regra.get("fila_destino", ""), resultado="ok",
                           detalhe="Encaminhado (%s)" % regra.get("nome", ""))
                resumo["acoes"].append({"number": number, "acao": "encaminhar",
                                        "fila_destino": regra.get("fila_destino", "")})
            else:  # encerrar (transição 3->2->6)
                estado = _estado_canonico(str(_display(inc.get("state")) or ""))
                if estado == "on_hold":
                    _sn_update(session, INCIDENT_TABLE, sys_id, {"state": "2"})
                _sn_update(session, INCIDENT_TABLE, sys_id, {
                    "state": "6",
                    "u_caused_by_change": "no",
                    "close_code": CLOSE_CODE,
                    "close_notes": regra.get("mensagem", ""),
                })
                resumo["encerrados"] += 1
                db.add_log(origem=origem, usuario=usuario, number=number, sys_id=sys_id,
                           subcategoria=subcat, acao="encerrar", fila_origem=queue,
                           fila_destino="", resultado="ok",
                           detalhe="Encerrado (%s)" % regra.get("nome", ""))
                resumo["acoes"].append({"number": number, "acao": "encerrar"})
        except Exception as exc:  # noqa: BLE001
            resumo["erros"] += 1
            db.add_log(origem=origem, usuario=usuario, number=number, sys_id=sys_id,
                       subcategoria=subcat, acao=acao, fila_origem=queue,
                       fila_destino=regra.get("fila_destino", ""), resultado="erro",
                       detalhe=str(exc)[:400])

    return resumo


# ── Endpoints: regras ───────────────────────────────────────────────────
class RegraIn(BaseModel):
    nome: str = ""
    subcategorias: str = ""
    acao: str = "encerrar"
    fila_destino: str = ""
    mensagem: str = ""
    ativo: bool = True
    ordem: int = 100


@router.get("/regras")
def regras_list(req: Request):
    require_permission(req, "parametros", "admin")
    return {"regras": db.listar_regras()}


@router.post("/regras")
def regras_add(body: RegraIn, req: Request):
    require_permission(req, "parametros", "admin")
    rid = db.salvar_regra(body.model_dump())
    return {"ok": True, "id": rid}


@router.put("/regras/{rid}")
def regras_edit(rid: int, body: RegraIn, req: Request):
    require_permission(req, "parametros", "admin")
    db.salvar_regra(body.model_dump(), rid=rid)
    return {"ok": True}


@router.delete("/regras/{rid}")
def regras_del(rid: int, req: Request):
    require_permission(req, "parametros", "admin")
    db.excluir_regra(rid)
    return {"ok": True}


# ── Endpoints: logs e config ────────────────────────────────────────────
@router.get("/logs")
def logs_list(req: Request, origem: str = "", q: str = "", limit: int = 500):
    require_permission(req, "parametros", "view")
    return {"logs": db.listar_logs(limit=limit, origem=origem, q=q)}


@router.get("/config")
def config_get(req: Request):
    require_permission(req, "parametros", "admin")
    cfg = db.obter_config()
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "horarios": cfg.get("horarios") or _cfg.AUTOMACOES_HORARIOS,
        "tracking_field": cfg.get("tracking_field") or DEFAULT_TRACKING_FIELD,
        "usuario": cfg.get("usuario", ""),
        "tem_sessao": bool(cfg.get("sn_cookies")),
        "ultima_execucao": cfg.get("ultima_execucao", ""),
    }


class ConfigIn(BaseModel):
    enabled: bool = False
    horarios: str = ""
    tracking_field: str = ""


@router.put("/config")
def config_put(body: ConfigIn, req: Request):
    sd = require_permission(req, "parametros", "admin")
    dados = {"enabled": bool(body.enabled)}
    if body.horarios.strip():
        dados["horarios"] = body.horarios.strip()
    if body.tracking_field.strip():
        dados["tracking_field"] = body.tracking_field.strip()
    # Captura a sessão SN do usuário que ativou, para a rotina rodar como ele.
    if sd.get("sn_cookies"):
        dados["sn_cookies"] = sd["sn_cookies"]
        dados["usuario"] = sd.get("username", "")
    db.salvar_config(dados)
    return {"ok": True}


# ── Executar (botão) ────────────────────────────────────────────────────
@router.post("/run")
def run_now(req: Request):
    """Roda a rotina AGORA, com a sessão do usuário logado (botão)."""
    sd = require_permission(req, "rastreio", "edit")
    session = _sn_session_from_portal(req)
    # Aproveita para atualizar a sessão salva (mantém a rotina agendada viva).
    try:
        db.salvar_config({"sn_cookies": sd.get("sn_cookies"), "usuario": sd.get("username", "")})
    except Exception:  # noqa: BLE001
        pass
    resumo = _rodar(session, origem="botao", usuario=sd.get("username", ""))
    return {"ok": True, "resumo": resumo}


# ── Agendador ───────────────────────────────────────────────────────────
_scheduler_started = False


def _horarios() -> set[int]:
    cfg = db.obter_config()
    txt = cfg.get("horarios") or _cfg.AUTOMACOES_HORARIOS
    horas = set()
    for p in str(txt).split(","):
        p = p.strip()
        if p.isdigit():
            horas.add(int(p))
    return horas


def _sessao_para_rotina():
    """Sessão SN para a rotina: usa a salva do usuário; se inválida, procura uma
    sessão viva do mesmo usuário no portal. Retorna (session, usuario) ou (None, '')."""
    cfg = db.obter_config()
    usuario = cfg.get("usuario", "")
    cookies = cfg.get("sn_cookies")
    if cookies:
        s = _sn_session_from_cookies(cookies)
        if s and _sn_session_valida(s):
            return s, usuario
    # Procura sessão viva do mesmo usuário (ou qualquer uma com cookies válidos).
    candidatos = []
    for sd in list(SESSIONS.values()):
        if sd.get("sn_cookies"):
            if usuario and sd.get("username") == usuario:
                candidatos.insert(0, sd)
            else:
                candidatos.append(sd)
    for sd in candidatos:
        s = _sn_session_from_cookies(sd.get("sn_cookies"))
        if s and _sn_session_valida(s):
            # atualiza a sessão salva
            try:
                db.salvar_config({"sn_cookies": sd["sn_cookies"], "usuario": sd.get("username", usuario)})
            except Exception:  # noqa: BLE001
                pass
            return s, sd.get("username", usuario)
    return None, usuario


def _scheduler_loop() -> None:
    time.sleep(30)
    ultima_hora = None
    while True:
        try:
            agora = datetime.now()
            cfg = db.obter_config()
            if cfg.get("enabled") and agora.minute == 0 and agora.hour in _horarios():
                marca = agora.strftime("%Y-%m-%d %H")
                if marca != ultima_hora:
                    ultima_hora = marca
                    session, usuario = _sessao_para_rotina()
                    if session is None:
                        db.add_log(origem="agendador", usuario=usuario or "", number="",
                                   sys_id="", subcategoria="", acao="ignorado",
                                   fila_origem="", fila_destino="", resultado="adiado",
                                   detalhe="Sem sessão do ServiceNow válida no horário.")
                    else:
                        resumo = _rodar(session, origem="agendador", usuario=usuario or "")
                        db.salvar_config({"ultima_execucao": agora.strftime("%d/%m/%Y %H:%M")})
                        _log.info("Automação (agendador): %s", resumo)
        except Exception as exc:  # noqa: BLE001
            _log.error("Agendador de automações falhou: %s", exc, exc_info=True)
        time.sleep(30)


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    th = threading.Thread(target=_scheduler_loop, daemon=True, name="automacoes-scheduler")
    th.start()
    _log.info("Automações: agendador iniciado (horas %s).", _cfg.AUTOMACOES_HORARIOS)
