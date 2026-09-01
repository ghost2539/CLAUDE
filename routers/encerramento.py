"""Encerramento automático de chamados Correios entregues.

Módulo isolado: escreve no ServiceNow (encerra chamados) quando o objeto
foi entregue, para chamados On Hold/In Progress com código de rastreio no
correlation_display. Mantido separado da listagem e do rastreio para não
impactar o que já funciona.

Fluxo de encerramento (exigência do ServiceNow):
  state 3 (On Hold) -> 2 (In Progress) [salva] -> 6 (Resolved) com:
    u_caused_by_change = no
    close_code        = Solved (Permanently)
    close_notes       = texto padrão de entrega
"""
from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

from security import require_permission
from routers.servicenow import (
    _sn_session_from_portal,
    _sn_query,
    _sn_update,
    _extract_tracking_code,
    INCIDENT_TABLE,
    DEFAULT_QUEUE,
)
from routers.correios import consultar_rastreio

router = APIRouter(prefix="/api/servicenow/encerramento", tags=["Encerramento"])

CLOSE_CODE = "Solved (Permanently)"

CLOSE_NOTES = (
    "Olá, tudo bem?\n\n"
    "Conforme código de rastreio, o equipamento foi entregue.\n"
    "Estamos encerrando este chamado.\n\n"
    "Dúvidas de processos de TI? Acesse a nossa FAQ! 🤩\n"
    "Acesse a nossa FAQ através do caminho abaixo:\n"
    "https://renner.sharepoint.com/sites/bibliotecasa?OR=Teams-HL&CT=1756993890685\n"
    "08. 1 Processos e Qualidade Informa | Renner > CSC TI > FAQ TI\n\n"
    "Para esclarecimento de dúvidas, contate o Núcleo de Atendimento ao Cliente "
    "Interno, de segunda à sexta-feira, das 9h às 19h, em nossos canais:\n"
    "💬 WhatsApp Conecta: (51) 99837.3485\n"
    "📱 App Conecta (disponível em Bluebirds, computadores e telefones corporativos)"
)

FIELDS = (
    "sys_id,number,short_description,state,subcategory,"
    "correlation_id,correlation_display"
)


def _norm(s: str) -> str:
    return (s or "").strip().lower().replace("_", " ")


def _display(v):
    if isinstance(v, dict):
        return v.get("display_value", v.get("value", ""))
    return v or ""


def _avaliar(session, inc: dict) -> dict:
    """Decide se um incidente pode ser encerrado. Só leitura."""
    number = _display(inc.get("number"))
    subcat = _norm(_display(inc.get("subcategory")))
    # A consulta usa display_value=true: o estado vem como texto ("On Hold",
    # "In Progress"), não como código numérico. Comparamos pelo texto.
    state = str(_display(inc.get("state")) or "").strip()
    state_norm = state.lower()
    tracking = _extract_tracking_code(inc)

    resultado = {
        "sys_id": _display(inc.get("sys_id")),
        "number": number,
        "subcategory": subcat,
        "state": state,
        "state_norm": state_norm,
        "tracking": tracking,
        "elegivel": False,
        "motivo": "",
        "entrega": None,
    }

    # Regra de subcategoria removida: encerra qualquer chamado entregue que
    # tenha código de rastreio no correlation_display (independe de subcategoria).
    if state_norm not in ("on hold", "in progress"):
        resultado["motivo"] = f"estado '{state}' não é On Hold/In Progress"
        return resultado
    if not tracking:
        resultado["motivo"] = "sem código de rastreio válido"
        return resultado

    try:
        r = consultar_rastreio(tracking)
    except HTTPException as e:
        resultado["motivo"] = f"falha no rastreio: {e.detail}"
        return resultado

    entrega = r.get("entrega")
    if not (entrega and entrega.get("entregue")):
        resultado["motivo"] = "objeto ainda não entregue"
        return resultado

    resultado["elegivel"] = True
    resultado["motivo"] = "entregue — pronto para encerrar"
    resultado["entrega"] = entrega
    return resultado


@router.get("/candidatos")
def candidatos(req: Request, queue: str = DEFAULT_QUEUE):
    """Lista (SÓ LEITURA) os chamados elegíveis para encerramento.

    Não altera nada — serve para conferir antes de executar.
    """
    require_permission(req, "rastreio", "view")
    session = _sn_session_from_portal(req)

    sn_query = (
        f"assignment_group.name={queue}"
        f"^stateIN2,3"
        f"^ORDERBYDESCsys_created_on"
    )
    incidentes = _sn_query_all_safe(session, sn_query)

    avaliados = [_avaliar(session, inc) for inc in incidentes]
    elegiveis = [a for a in avaliados if a["elegivel"]]

    # Resumo diagnóstico: quantos caíram em cada filtro, e exemplos.
    from collections import Counter
    motivos = Counter(a["motivo"] for a in avaliados if not a["elegivel"])
    com_tracking = [a for a in avaliados if a["tracking"]]
    subcats = Counter(a["subcategory"] for a in avaliados)

    return {
        "queue": queue,
        "total_analisados": len(avaliados),
        "total_com_rastreio": len(com_tracking),
        "elegiveis": elegiveis,
        "total_elegiveis": len(elegiveis),
        "motivos_rejeicao": dict(motivos),
        "subcategorias_encontradas": dict(subcats.most_common(15)),
        "amostra_com_rastreio": [
            {"number": a["number"], "subcategory": a["subcategory"],
             "state": a["state"], "tracking": a["tracking"], "motivo": a["motivo"]}
            for a in com_tracking[:10]
        ],
    }


def _sn_query_all_safe(session, sn_query):
    """Busca incidentes On Hold/In Progress da fila (com paginação)."""
    from routers.servicenow import _sn_query_all
    return _sn_query_all(session, INCIDENT_TABLE, sn_query, FIELDS)


class EncerrarIn(BaseModel):
    sys_id: str
    confirmar: bool = False


@router.post("/executar")
def executar(body: EncerrarIn, req: Request):
    """Encerra UM chamado, revalidando tudo no servidor antes de escrever.

    Exige confirmar=True. Faz a transição 3->2->6 com os campos de
    closure. É a ação manual de teste; a rotina automática reutiliza
    a mesma validação.
    """
    require_permission(req, "rastreio", "edit")
    session = _sn_session_from_portal(req)

    recs = _sn_query(session, INCIDENT_TABLE, f"sys_id={body.sys_id}", FIELDS, 1)
    if not recs:
        raise HTTPException(404, "Chamado não encontrado.")
    inc = recs[0]

    aval = _avaliar(session, inc)
    if not aval["elegivel"]:
        raise HTTPException(422, f"Não elegível: {aval['motivo']}")

    if not body.confirmar:
        # Prévia segura: mostra o que faria, sem escrever.
        return {"ok": True, "dry_run": True, "acao": "encerraria", **aval}

    # Passo 1: garantir In Progress (2) — obrigatório antes de resolver.
    # Se está On Hold, precisa passar por In Progress primeiro.
    if aval.get("state_norm") == "on hold":
        _sn_update(session, INCIDENT_TABLE, body.sys_id, {"state": "2"})

    # Passo 2: resolver (6) com os campos de closure obrigatórios.
    _sn_update(session, INCIDENT_TABLE, body.sys_id, {
        "state": "6",
        "u_caused_by_change": "no",
        "close_code": CLOSE_CODE,
        "close_notes": CLOSE_NOTES,
    })

    return {
        "ok": True,
        "encerrado": aval["number"],
        "sys_id": body.sys_id,
        "close_code": CLOSE_CODE,
    }
