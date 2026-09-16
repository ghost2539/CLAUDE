"""Automação de encerramento/encaminhamento de chamados entregues.

- Roda SÓ pelo botão, com a sessão SSO de quem clicou: os apontamentos nos
  chamados saem em nome do usuário. Não há agendador nem conta de serviço.
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
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

import config as _config_mod
import db.automacoes as db
from core.security import require_permission, get_session, check_rate_limit
from routers.servicenow import (
    _sn_session_from_portal,
    _sn_query, _sn_query_all, _sn_update, _extract_tracking_code, _TRACKING_RE,
    INCIDENT_TABLE, DEFAULT_QUEUE,
)
from routers.encerramento import FIELDS as ENC_FIELDS, _estado_canonico, CLOSE_CODE, _display
from routers.correios import consultar_rastreio, evento_de_entrega

_cfg = _config_mod.get_settings()
_log = logging.getLogger("automacoes")

router = APIRouter(prefix="/api/automacoes", tags=["Automações"])

# O que conta como entrega vem de um lugar só (routers.correios): é o código
# BDE/BDI/BDR **com o tipo de entrega ao destinatário**. Aqui ficava só a
# lista de códigos, e por isso "Objeto ainda não chegou à unidade" (mesmo
# código, outro tipo) era lido como entregue e o chamado era encerrado.


def _monitor(alvo: str, detalhe: str, usuario: str = "", severidade: str = "erro") -> None:
    """Reporta ao módulo de Monitoramento (silencioso se ele não estiver ativo)."""
    try:
        from routers.monitoramento import registrar_falha
        registrar_falha("automacao", alvo, detalhe, usuario, severidade)
    except Exception:  # noqa: BLE001
        pass


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", " ")


def _ultimo_evento_entregue(rastreio: dict) -> tuple[bool, dict | None]:
    """True se o evento MAIS RECENTE do rastreio for uma entrega ao destinatário."""
    evs = rastreio.get("eventos") or []
    if not evs:
        return False, None
    latest = max(evs, key=lambda e: str(e.get("data") or ""))
    return evento_de_entrega(latest), latest


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


def _aplicar(session, sys_id: str, campos: dict, conferir: dict | None = None) -> dict:
    """Grava campos no incidente e CONFERE relendo o registro.

    O JSONv2 devolve 200 com o registro mesmo quando a instância DESCARTA o
    campo enviado — ACL de escrita do usuário, regra de negócio que devolve o
    grupo, valor de referência recusado. Como `_sn_update` só olha para o
    status e para a presença de `records`, a rotina anunciava "encaminhado"
    com o chamado parado na fila de origem. Relendo, o log passa a dizer a
    verdade: erro com o que foi enviado e o que o ServiceNow manteve.

    `conferir` é {campo: (valores aceitos,)}; sem ele, só grava.
    """
    _sn_update(session, INCIDENT_TABLE, sys_id, campos)
    if not conferir:
        return {}
    # display_value=False: precisamos do valor cru (sys_id do grupo, número do
    # estado), não do rótulo traduzido que o usuário vê na tela.
    recs = _sn_query(session, INCIDENT_TABLE, f"sys_id={sys_id}",
                     ",".join(conferir.keys()), 1, display_value=False)
    if not recs:
        raise RuntimeError(
            "ServiceNow aceitou a escrita mas o chamado não pôde ser relido "
            "para conferência (sys_id=%s)" % sys_id)
    atual = recs[0]
    divergencias = []
    for campo, aceitos in conferir.items():
        ficou = str(_display(atual.get(campo)) or "").strip()
        if ficou not in [str(a).strip() for a in aceitos]:
            divergencias.append("%s: enviado '%s', ServiceNow manteve '%s'"
                                % (campo, campos.get(campo, ""), ficou))
    if divergencias:
        raise RuntimeError("ServiceNow não aplicou a alteração — " + "; ".join(divergencias))
    return atual


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
                destino = regra.get("fila_destino", "")
                gid = _grupo_sys_id(session, destino)
                if not gid:
                    raise RuntimeError("fila destino não encontrada: %s" % destino)
                estado = _estado_canonico(str(_display(inc.get("state")) or ""))
                if estado == "on_hold":
                    # Em Espera costuma recusar a troca de grupo; o ramo de
                    # encerrar já usa essa mesma transição antes de agir.
                    _aplicar(session, sys_id, {"state": "2"})
                _aplicar(session, sys_id, {
                    "assignment_group": gid,
                    # Responsável que não pertence à fila destino faz a regra de
                    # negócio devolver o grupo — quem encaminha na mão limpa.
                    "assigned_to": "",
                    "work_notes": regra.get("mensagem", ""),
                }, {"assignment_group": (gid,)})
                resumo["encaminhados"] += 1
                db.add_log(origem=origem, usuario=usuario, number=number, sys_id=sys_id,
                           subcategoria=subcat, acao="encaminhar", fila_origem=queue,
                           fila_destino=destino, resultado="ok",
                           detalhe="Encaminhado para '%s' (%s)" % (destino, regra.get("nome", "")))
                resumo["acoes"].append({"number": number, "acao": "encaminhar",
                                        "fila_destino": regra.get("fila_destino", "")})
            else:  # encerrar (transição 3->2->6)
                estado = _estado_canonico(str(_display(inc.get("state")) or ""))
                if estado == "on_hold":
                    _aplicar(session, sys_id, {"state": "2"})
                # Tolerância no 7 (Closed): a instância pode avançar sozinha de
                # Resolved para Closed, e isso não é erro.
                _aplicar(session, sys_id, {
                    "state": "6",
                    "u_caused_by_change": "no",
                    "close_code": CLOSE_CODE,
                    "close_notes": regra.get("mensagem", ""),
                }, {"state": ("6", "7")})
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
            _monitor(f"automacao/{acao}/{number}", str(exc)[:400], usuario)

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


def _admin_automacoes(req: Request) -> dict:
    """Quem mexe na CONFIGURAÇÃO da rotina (campo do rastreio): marcado em
    'Administrar' no módulo Automações, ou administrador do portal. Ver a
    aba e manter as REGRAS não exige permissão — é trabalho do time."""
    return require_permission(req, "automacoes", "admin")


def _pode_administrar(req: Request) -> bool:
    try:
        _admin_automacoes(req)
        return True
    except HTTPException:
        return False


@router.get("/regras")
def regras_list(req: Request):
    # As regras (subcategoria → ação) são do time: qualquer usuário autenticado
    # lista, cria, edita e exclui. Só a configuração da rotina é restrita.
    get_session(req)
    return {"regras": db.listar_regras()}


@router.post("/regras")
def regras_add(body: RegraIn, req: Request):
    get_session(req)
    rid = db.salvar_regra(body.model_dump())
    return {"ok": True, "id": rid}


@router.put("/regras/{rid}")
def regras_edit(rid: int, body: RegraIn, req: Request):
    get_session(req)
    db.salvar_regra(body.model_dump(), rid=rid)
    return {"ok": True}


@router.delete("/regras/{rid}")
def regras_del(rid: int, req: Request):
    get_session(req)
    db.excluir_regra(rid)
    return {"ok": True}


# ── Endpoints: logs e config ────────────────────────────────────────────
@router.get("/logs")
def logs_list(req: Request, origem: str = "", q: str = "", limit: int = 500):
    get_session(req)
    return {"logs": db.listar_logs(limit=limit, origem=origem, q=q)}


@router.get("/config")
def config_get(req: Request):
    """Situação da rotina. Não há mais agendador nem credencial de serviço:
    a rotina só roda pelo botão, com a sessão de quem clicou."""
    get_session(req)
    cfg = db.obter_config()
    return {
        "tracking_field": cfg.get("tracking_field") or DEFAULT_TRACKING_FIELD,
        "ultima_execucao": cfg.get("ultima_execucao", ""),
        "ultimo_usuario": cfg.get("ultimo_usuario", ""),
        "somente_leitura": not _pode_administrar(req),
    }


class ConfigIn(BaseModel):
    tracking_field: str = ""


@router.put("/config")
def config_put(body: ConfigIn, req: Request):
    _admin_automacoes(req)
    campo = body.tracking_field.strip()
    if campo and not re.fullmatch(r"[a-z0-9_.]{1,80}", campo):
        raise HTTPException(400, "Campo do rastreio: só letras minúsculas, números, _ e ponto.")
    db.salvar_config({"tracking_field": campo or DEFAULT_TRACKING_FIELD})
    return {"ok": True}


# ── Executar (botão) ────────────────────────────────────────────────────
@router.post("/run")
def run_now(req: Request):
    """Roda a rotina AGORA, com a sessão SSO de quem clicou.

    É a única forma de execução: os apontamentos e encerramentos no
    ServiceNow saem em nome do usuário, como exige a governança. Basta
    estar autenticado — quem não pode encerrar um chamado lá também não
    consegue por aqui. A sessão do usuário não é guardada em lugar nenhum."""
    sd = get_session(req)
    check_rate_limit(req)
    session = _sn_session_from_portal(req)
    usuario = sd.get("username", "")
    resumo = _rodar(session, origem="botao", usuario=usuario)
    try:
        db.salvar_config({"ultima_execucao": datetime.now().strftime("%d/%m/%Y %H:%M"),
                          "ultimo_usuario": usuario})
    except Exception as exc:  # noqa: BLE001 — carimbo é informativo
        _log.warning("automação: não gravei a última execução: %s", exc)
    return {"ok": True, "resumo": resumo}
