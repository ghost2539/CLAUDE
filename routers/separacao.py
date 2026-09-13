"""Separação e Expedição (A15) — da demanda do chamado ao equipamento na caixa.

Regras que moldam este módulo, tiradas dos documentos de processo:

- **Toda solicitação nasce de um chamado.** Sem incidente ou requisição
  válida não há pedido, e o destino vem do chamado — não se digita.
- **O pedido é por modelo e quantidade.** A série de cada unidade é
  definida por quem separa, na bancada, bipando o equipamento.
- **Cada tipo de atendimento enxerga um estoque só.** Reposição e
  Inauguração dividem o mesmo depósito; o que separa é o começo do
  espaço/corredor (REP e IN). O mesmo modelo existe nos dois, e é a
  prateleira que decide, não o equipamento.
- **O tempo é do núcleo.** A fila e a bancada usam o mesmo calendário da
  Trilha do Ativo, para que um dia útil signifique a mesma coisa em
  todos os indicadores da área.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.separacao as db
import db.trilha as dbt
from db.separacao import (
    Solicitacao, Item, Unidade, SessionLocal,
    TIPOS_ATENDIMENTO, ROTULO_TIPO, ROTULO_ESTADO,
    AG_SEPARACAO, EX_SEPARACAO, SEPARADA, ENVIADA, CANCELADA,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import Calendario, duracao_util, prazo_util, mover, _utc

_log = logging.getLogger("separacao")

router = APIRouter(prefix="/api/separacao", tags=["Separação"])


# ══════════════════════════════════════════════════════════════════
#  Estoque no ServiceNow
# ══════════════════════════════════════════════════════════════════

def prefixo_do_tipo(cfg: dict[str, str], tipo: str) -> str:
    """Qual prefixo de espaço/corredor este tipo de atendimento consome."""
    estoque = cfg.get(f"estoque_{tipo}", "reposicao")
    chave = ("prefixo_inauguracao" if estoque == "inauguracao"
             else "prefixo_reposicao")
    return (cfg.get(chave) or "").strip()


def montar_filtro(cfg: dict[str, str], tipo: str) -> str:
    """Encoded query do estoque que este tipo de atendimento enxerga.

    O filtro manual, quando preenchido, ganha de tudo: é o escape para o
    caso que os parâmetros não cobrirem, sem precisar de release.
    """
    prefixo = prefixo_do_tipo(cfg, tipo)
    if not prefixo:
        raise HTTPException(
            409, "Estoque não configurado: falta o prefixo de espaço e corredor "
                 "em Parâmetros → Separação.")

    manual = (cfg.get("filtro_manual") or "").strip()
    if manual:
        return (manual
                .replace("$campo", cfg.get("campo_local", ""))
                .replace("$comparacao", cfg.get("comparacao", "STARTSWITH"))
                .replace("$prefixo", prefixo))

    campo = (cfg.get("campo_local") or "").strip()
    if not campo:
        raise HTTPException(
            409, "Estoque não configurado: falta o campo de espaço e corredor "
                 "em Parâmetros → Separação.")
    comp = (cfg.get("comparacao") or "STARTSWITH").strip()
    status = (cfg.get("status_estoque") or "6").strip()
    filtro = f"install_status={status}^{campo}{comp}{prefixo}"

    # Reservado não é saldo: assim que alguém bipa, a unidade some do
    # catálogo de todo mundo. Sem esta cláusula, duas separações
    # simultâneas prometeriam o mesmo equipamento.
    reserva_campo = (cfg.get("reserva_campo") or "").strip()
    reserva_valor = (cfg.get("reserva_valor") or "").strip()
    if reserva_campo and reserva_valor:
        filtro += f"^{reserva_campo}!={reserva_valor}"
    return filtro


def _campos_catalogo() -> str:
    return ("sys_id,serial_number,asset_tag,model,model_category,"
            "install_status,aisle_space_location,stockroom")


def catalogo(req: Request, tipo: str) -> dict:
    """Saldo por modelo no estoque daquele tipo de atendimento."""
    from routers.servicenow import (
        _sn_session_from_portal, _sn_query_all, HARDWARE_TABLE,
    )
    cfg = db.ler_config()
    filtro = montar_filtro(cfg, tipo)
    session = _sn_session_from_portal(req)

    linhas = _sn_query_all(session, HARDWARE_TABLE, filtro,
                           _campos_catalogo(), page_size=500, max_records=20000)

    por_modelo: dict[str, dict] = {}
    for r in linhas:
        modelo = (r.get("model") or "").strip() or "(sem modelo)"
        alvo = por_modelo.setdefault(modelo, {
            "modelo": modelo,
            "categoria": (r.get("model_category") or "").strip(),
            "saldo": 0,
            "locais": set(),
        })
        alvo["saldo"] += 1
        local = (r.get("aisle_space_location") or "").strip()
        if local:
            alvo["locais"].add(local)

    itens = sorted(
        ({**v, "locais": sorted(v["locais"])[:6]} for v in por_modelo.values()),
        key=lambda x: (-x["saldo"], x["modelo"]),
    )
    return {
        "tipo": tipo,
        "rotulo": ROTULO_TIPO.get(tipo, tipo),
        "prefixo": prefixo_do_tipo(cfg, tipo),
        "filtro": filtro,
        "total": sum(i["saldo"] for i in itens),
        "itens": itens,
    }


def localizar_no_estoque(req: Request, serial: str, tipo: str) -> dict:
    """Acha a unidade no estoque daquele tipo, ou explica por que não achou.

    Bipar uma série que não está naquele estoque é o erro mais provável
    da bancada — pegar o coletor do corredor da inauguração para atender
    uma reposição. A mensagem precisa dizer isso, não "não encontrado".
    """
    from routers.servicenow import (
        _sn_session_from_portal, _sn_query, HARDWARE_TABLE, termo_sn,
    )
    serial = termo_sn(serial, "série")
    cfg = db.ler_config()
    session = _sn_session_from_portal(req)
    campos = ("sys_id,serial_number,asset_tag,model,install_status,substatus,"
              "aisle_space_location,stockroom")

    no_estoque = _sn_query(session, HARDWARE_TABLE,
                           f"{montar_filtro(cfg, tipo)}^serial_number={serial}",
                           campos, limit=2)
    if no_estoque:
        return no_estoque[0]

    # Não está no estoque certo. Buscar solto só para dizer onde está.
    solto = _sn_query(session, HARDWARE_TABLE,
                      f"serial_number={serial}", campos, limit=1)
    if not solto:
        raise HTTPException(404, f"Série {serial} não existe no ServiceNow.")

    onde = (solto[0].get("aisle_space_location") or "").strip() or "sem espaço/corredor"
    reserva_valor = (cfg.get("reserva_valor") or "").strip()
    sub = (solto[0].get("substatus") or "").strip()
    if reserva_valor and sub == reserva_valor:
        raise HTTPException(
            409, f"Série {serial} já está reservada para outra separação.")
    raise HTTPException(
        409, f"Série {serial} não está no estoque de "
             f"{ROTULO_TIPO.get(tipo, tipo)} — está em {onde}.")


def _escrever_reserva(req: Request, sys_id: str, serial: str, reservar: bool) -> None:
    """Marca ou solta a reserva da unidade no ServiceNow.

    A escrita usa a sessão do usuário logado, como manda a norma do
    projeto: no ServiceNow o registro tem que sair no nome de quem fez.
    """
    from routers.servicenow import (
        _sn_session_from_portal, _sn_update, HARDWARE_TABLE,
    )
    cfg = db.ler_config()
    campo = (cfg.get("reserva_campo") or "").strip()
    if not campo:
        return
    valor = (cfg.get("reserva_valor" if reservar else "reserva_valor_livre") or "").strip()
    ok = _sn_update(_sn_session_from_portal(req), HARDWARE_TABLE, sys_id,
                    {campo: valor})
    if not ok:
        raise HTTPException(
            502, f"Série {serial}: o ServiceNow não aceitou a "
                 f"{'reserva' if reservar else 'liberação'}. Tente de novo.")


# ══════════════════════════════════════════════════════════════════
#  Chamado de origem
# ══════════════════════════════════════════════════════════════════

def consultar_chamado(req: Request, numero: str, cfg: dict | None = None) -> dict:
    """Valida o chamado e devolve o que a solicitação herda dele.

    `cfg` permite a outro módulo (projetos, reversa) usar os próprios
    prefixos e estados bloqueados sem mexer no estado global.
    """
    from routers.servicenow import _sn_session_from_portal, _sn_query, termo_sn

    numero = termo_sn((numero or "").strip().upper(), "chamado")
    if not numero:
        raise HTTPException(400, "Informe o número do chamado.")

    cfg = {**db.ler_config(), **(cfg or {})}
    prefixos = [p.strip().upper()
                for p in (cfg.get("chamado_prefixos") or "").split(",") if p.strip()]
    prefixo = next((p for p in prefixos if numero.startswith(p)), "")
    if not prefixo:
        raise HTTPException(
            400, f"Chamado deve começar com {' ou '.join(prefixos)}.")

    tabela = "sc_req_item" if prefixo == "RITM" else "incident"
    campos = ("number,short_description,state,location,u_loja,"
              "caller_id,assignment_group,cmdb_ci")
    achados = _sn_query(session=_sn_session_from_portal(req), table=tabela,
                        query=f"number={numero}", fields=campos, limit=1)
    if not achados:
        raise HTTPException(404, f"Chamado {numero} não encontrado no ServiceNow.")

    ch = achados[0]
    estado = str(ch.get("state") or "").strip()
    bloqueados = [e.strip() for e in
                  (cfg.get("chamado_estados_bloqueados") or "").split(",") if e.strip()]
    if estado in bloqueados:
        raise HTTPException(
            409, f"Chamado {numero} está encerrado ou cancelado — não aceita "
                 "nova separação.")

    return {
        "chamado": numero,
        "tipo": prefixo,
        "resumo": (ch.get("short_description") or "").strip(),
        "destino": (ch.get("location") or ch.get("u_loja") or "").strip(),
        "solicitante": (ch.get("caller_id") or "").strip(),
        "estado": estado,
    }


# ══════════════════════════════════════════════════════════════════
#  Tempos — sempre pelo calendário do núcleo
# ══════════════════════════════════════════════════════════════════

def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _calcular_prazo(abertura: datetime, tipo: str, prioridade: str) -> datetime | None:
    """Prazo em dias úteis, pelo mesmo calendário que mede tudo no portal.

    Inauguração tem cadência própria e ganha do resto: um pedido de
    inauguração marcado como urgente continua sendo inauguração.
    """
    cfg = db.ler_config()
    if tipo == "INAUGURACAO_REFORMA":
        chave = "prazo_inauguracao"
    elif (prioridade or "").strip().lower() in ("loja_parada", "loja parada"):
        chave = "prazo_loja_parada"
    else:
        chave = "prazo_normal"
    try:
        dias = float(cfg.get(chave) or 0)
    except ValueError:
        dias = 0
    if dias <= 0:
        return None
    return prazo_util(abertura, dias, _calendario())


def _resumo(s: Solicitacao, cal: Calendario, agora: datetime) -> dict:
    # O pedido só está fechado quando o equipamento sai. Parar o relógio
    # na conclusão da separação esconderia o tempo entre separar e enviar.
    fim = (_utc(s.enviada_em) or _utc(s.separada_em)
           if s.estado in (ENVIADA, CANCELADA) else None)
    return {
        "numero": s.numero,
        "chamado": s.chamado,
        "chamado_resumo": s.chamado_resumo,
        "tipo_atendimento": s.tipo_atendimento,
        "tipo_rotulo": ROTULO_TIPO.get(s.tipo_atendimento, s.tipo_atendimento),
        "destino": s.destino,
        "bu": s.bu,
        "prioridade": s.prioridade,
        "estado": s.estado,
        "estado_rotulo": ROTULO_ESTADO.get(s.estado, s.estado),
        "aberta_em": _utc(s.aberta_em).isoformat(),
        "aberta_por": s.aberta_por,
        "prazo": _utc(s.prazo).isoformat() if s.prazo else None,
        "atrasada": bool(s.prazo and s.estado in (AG_SEPARACAO, EX_SEPARACAO)
                         and _utc(s.prazo) < agora),
        "separada_por": s.separada_por,
        "enviada_em": _utc(s.enviada_em).isoformat() if s.enviada_em else None,
        "segundos_uteis": duracao_util(_utc(s.aberta_em), fim, cal, agora),
        # Preenchido só no detalhe: a fila não carrega item por item.
        "itens": None,
    }


# ══════════════════════════════════════════════════════════════════
#  API — leitura
# ══════════════════════════════════════════════════════════════════

@router.get("/catalogo")
def api_catalogo(req: Request, tipo: str = ""):
    require_permission(req, "separacao", "view")
    check_rate_limit(req)
    if tipo not in TIPOS_ATENDIMENTO:
        raise HTTPException(400, "Tipo de atendimento inválido.")
    return catalogo(req, tipo)


@router.get("/chamado/{numero}")
def api_chamado(numero: str, req: Request):
    require_permission(req, "separacao", "view")
    check_rate_limit(req)
    return consultar_chamado(req, numero)


@router.get("/solicitacoes")
def api_lista(req: Request, tipo: str = "", estado: str = ""):
    require_permission(req, "separacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = select(Solicitacao).order_by(Solicitacao.aberta_em.desc())
        if tipo:
            q = q.where(Solicitacao.tipo_atendimento == tipo)
        if estado:
            q = q.where(Solicitacao.estado == estado)
        linhas = s.execute(q.limit(300)).scalars().all()
        pedidos = [_resumo(x, cal, agora) for x in linhas]
        contagem = {e: 0 for e in ROTULO_ESTADO}
        for x in s.execute(select(Solicitacao)).scalars():
            contagem[x.estado] = contagem.get(x.estado, 0) + 1
    atrasadas = sum(1 for p in pedidos if p["atrasada"])
    return {"solicitacoes": pedidos, "contagem": contagem, "atrasadas": atrasadas}


@router.get("/solicitacoes/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "separacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        ped = _buscar(s, numero)
        dados = _resumo(ped, cal, agora)
        itens = s.execute(
            select(Item).where(Item.solicitacao_id == ped.id)
        ).scalars().all()
        unidades = s.execute(
            select(Unidade).where(Unidade.solicitacao_id == ped.id)
        ).scalars().all()

    por_item: dict[int, list[Unidade]] = {}
    for u in unidades:
        por_item.setdefault(u.item_id, []).append(u)

    dados["itens"] = [{
        "id": i.id, "modelo": i.modelo, "quantidade": i.quantidade,
        "separados": len(por_item.get(i.id, [])),
        "series": [{"serial": u.serial, "por": u.separada_por,
                    "em": _utc(u.separada_em).isoformat()}
                   for u in por_item.get(i.id, [])],
    } for i in itens]
    dados["pedidos"] = sum(i.quantidade for i in itens)
    dados["separados"] = len(unidades)
    return dados


def _buscar(s, numero: str) -> Solicitacao:
    ped = s.execute(
        select(Solicitacao).where(Solicitacao.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if ped is None:
        raise HTTPException(404, f"Solicitação {numero} não encontrada.")
    return ped


# ══════════════════════════════════════════════════════════════════
#  API — escrita
# ══════════════════════════════════════════════════════════════════

class ItemIn(BaseModel):
    modelo: str
    quantidade: int = 1


class SolicitacaoIn(BaseModel):
    chamado: str
    tipo_atendimento: str
    prioridade: str = "normal"
    motivo: str = ""
    itens: list[ItemIn] = []


@router.post("/solicitacoes")
def api_criar(body: SolicitacaoIn, req: Request):
    sd = require_permission(req, "separacao", "create")
    check_rate_limit(req)

    if body.tipo_atendimento not in TIPOS_ATENDIMENTO:
        raise HTTPException(400, "Tipo de atendimento inválido.")
    itens = [i for i in body.itens if i.quantidade > 0 and i.modelo.strip()]
    if not itens:
        raise HTTPException(400, "Inclua ao menos um equipamento na solicitação.")

    # O chamado é validado no ServiceNow antes de gravar qualquer coisa:
    # é ele que define o destino, e pedido sem destino não separa.
    ch = consultar_chamado(req, body.chamado)

    usuario = sd.get("username", "")
    with SessionLocal() as s:
        ja = s.execute(
            select(Solicitacao).where(
                Solicitacao.chamado == ch["chamado"],
                Solicitacao.estado.in_((AG_SEPARACAO, EX_SEPARACAO)))
        ).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(
                409, f"O chamado {ch['chamado']} já tem a solicitação {ja.numero} "
                     "em aberto.")

        abertura = db.utcnow()
        ped = Solicitacao(
            numero=db.proximo_numero(s),
            chamado=ch["chamado"], chamado_tipo=ch["tipo"],
            chamado_resumo=ch["resumo"], destino=ch["destino"],
            tipo_atendimento=body.tipo_atendimento,
            prioridade=(body.prioridade or "normal").strip(),
            motivo=(body.motivo or "").strip(),
            aberta_por=usuario, aberta_em=abertura,
            prazo=_calcular_prazo(abertura, body.tipo_atendimento,
                                  body.prioridade),
        )
        s.add(ped)
        s.flush()
        for i in itens:
            s.add(Item(solicitacao_id=ped.id, modelo=i.modelo.strip(),
                       quantidade=int(i.quantidade)))
        s.commit()
        numero = ped.numero

    _log.info("separacao: %s aberta por %s (chamado %s)",
              numero, usuario, ch["chamado"])
    return api_detalhe(numero, req)


@router.post("/solicitacoes/{numero}/iniciar")
def api_iniciar(numero: str, req: Request):
    """Assume a separação: é aqui que o relógio muda de fila para bancada."""
    sd = require_permission(req, "separacao", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        ped = _buscar(s, numero)
        if ped.estado != AG_SEPARACAO:
            raise HTTPException(
                409, f"A solicitação está em '{ROTULO_ESTADO.get(ped.estado)}' "
                     "e não pode ser iniciada.")
        ped.estado = EX_SEPARACAO
        ped.separada_por = usuario
        s.commit()
    _log.info("separacao: %s assumida por %s", numero.upper(), usuario)
    return api_detalhe(numero, req)


class BipeIn(BaseModel):
    item_id: int
    serial: str


@router.post("/solicitacoes/{numero}/bipar")
def api_bipar(numero: str, body: BipeIn, req: Request):
    """Registra a série de uma unidade separada.

    Quando o equipamento já está na Trilha do Ativo, a leitura também
    move o ativo para EX_SEPARACAO. Equipamento antigo, anterior ao
    núcleo, é aceito sem trilha — a contagem começa nos novos, e recusar
    o antigo pararia a operação por causa de uma regra de medição.
    """
    sd = require_permission(req, "separacao", "edit")
    usuario = sd.get("username", "")
    serial = (body.serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "Bipe a série do equipamento.")

    with SessionLocal() as s:
        ped = _buscar(s, numero)
        if ped.estado != EX_SEPARACAO:
            raise HTTPException(409, "Assuma a separação antes de bipar.")

        item = s.get(Item, body.item_id)
        if item is None or item.solicitacao_id != ped.id:
            raise HTTPException(404, "Item não pertence a esta solicitação.")

        ja = s.execute(
            select(Unidade).where(Unidade.solicitacao_id == ped.id,
                                  Unidade.serial == serial)
        ).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(409, f"A série {serial} já foi bipada neste pedido.")

        separados = len(s.execute(
            select(Unidade).where(Unidade.item_id == item.id)
        ).scalars().all())
        if separados >= item.quantidade:
            raise HTTPException(
                409, f"{item.modelo} já tem as {item.quantidade} unidades pedidas.")

        # Ordem: gravar local sem commit → reservar no ServiceNow → commit.
        # O flush pega a colisão de dois bipes da mesma série ANTES de
        # qualquer escrita externa; e se o ServiceNow recusar, a sessão
        # é descartada e nada fica registrado aqui — o contrário deixaria
        # a unidade contada como separada e ainda disponível no saldo.
        unidade = Unidade(solicitacao_id=ped.id, item_id=item.id, serial=serial,
                          separada_por=usuario)
        s.add(unidade)
        s.flush()
        achado = localizar_no_estoque(req, serial, ped.tipo_atendimento)
        _escrever_reserva(req, achado["sys_id"], serial, reservar=True)
        unidade.sys_id = achado["sys_id"]
        unidade.trilha_ativo_id = _registrar_na_trilha(serial, usuario, ped.numero)
        s.commit()
    _log.info("separacao: %s reservou a série %s (%s)",
              usuario, serial, numero.upper())
    return api_detalhe(numero, req)


def _registrar_na_trilha(serial: str, usuario: str, pedido: str) -> int | None:
    """Move o ativo para EX_SEPARACAO, se ele estiver na trilha.

    Falha aqui não derruba a separação: o equipamento já está na mão de
    quem bipou, e recusar a leitura por causa do registro de tempo
    inverteria a prioridade entre operação e indicador.
    """
    try:
        with dbt.SessionLocal() as st:
            ativo = st.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one_or_none()
            if ativo is None:
                return None
            mover(st, ativo, estado=EX_SEPARACAO, tipo=dbt.TRATATIVA,
                  processo="A15", usuario=usuario,
                  detalhe=f'{{"solicitacao": "{pedido}"}}')
            st.commit()
            return ativo.id
    except Exception as exc:  # noqa: BLE001 — ver docstring
        _log.error("separacao: série %s bipada, mas a trilha não registrou: %s",
                   serial, exc)
        return None


@router.post("/solicitacoes/{numero}/concluir")
def api_concluir(numero: str, req: Request):
    sd = require_permission(req, "separacao", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        ped = _buscar(s, numero)
        if ped.estado != EX_SEPARACAO:
            raise HTTPException(409, "Só uma separação em andamento pode ser concluída.")

        itens = s.execute(select(Item).where(Item.solicitacao_id == ped.id)).scalars().all()
        unidades = s.execute(
            select(Unidade).where(Unidade.solicitacao_id == ped.id)).scalars().all()
        pedidos = sum(i.quantidade for i in itens)
        if len(unidades) < pedidos:
            raise HTTPException(
                409, f"Faltam {pedidos - len(unidades)} equipamento(s) para "
                     "fechar a separação.")

        ped.estado = SEPARADA
        ped.separada_por = usuario
        ped.separada_em = db.utcnow()
        s.commit()
    _log.info("separacao: %s concluída por %s", numero.upper(), usuario)
    return api_detalhe(numero, req)


@router.post("/solicitacoes/{numero}/enviar")
def api_enviar(numero: str, req: Request):
    """Expedição: o equipamento passa a estar em uso, na loja de destino.

    É aqui que a reserva vira alocação: o ativo sai do CD e o local no
    ServiceNow deixa de ser o corredor e passa a ser a loja.
    """
    from routers.servicenow import (
        _sn_session_from_portal, _sn_update, _lookup_reference, _get_http,
        HARDWARE_TABLE,
    )
    sd = require_permission(req, "separacao", "edit")
    usuario = sd.get("username", "")
    cfg = db.ler_config()

    with SessionLocal() as s:
        ped = _buscar(s, numero)
        if ped.estado != SEPARADA:
            raise HTTPException(
                409, "Só uma solicitação já separada pode ser enviada.")
        if not (ped.destino or "").strip():
            raise HTTPException(
                409, "A solicitação não tem destino — confira o chamado de origem.")
        unidades = s.execute(
            select(Unidade).where(Unidade.solicitacao_id == ped.id)).scalars().all()
        destino, pedido_num = ped.destino, ped.numero

    session = _sn_session_from_portal(req)
    _req, BS = _get_http()
    local_id = _lookup_reference(session, "location", destino, {}, BS)
    if not local_id:
        raise HTTPException(
            409, f"O destino '{destino}' não foi encontrado como local no "
                 "ServiceNow. Corrija o local no chamado e tente de novo.")

    campo_local = (cfg.get("envio_campo_local") or "location").strip()
    reserva_campo = (cfg.get("reserva_campo") or "").strip()
    alteracao = {
        "install_status": (cfg.get("envio_status") or "1").strip(),
        campo_local: local_id,
    }
    # A unidade deixa de estar reservada: ela não está mais no CD.
    if reserva_campo:
        alteracao[reserva_campo] = ""

    falhas = []
    for u in unidades:
        if not u.sys_id:
            falhas.append(f"{u.serial} (sem vínculo no ServiceNow)")
            continue
        if not _sn_update(session, HARDWARE_TABLE, u.sys_id, alteracao):
            falhas.append(u.serial)
    if falhas:
        # Parcial é pior que nada: quem operar precisa saber exatamente
        # quais séries ficaram para trás para tratar uma a uma.
        raise HTTPException(
            502, "O ServiceNow não aceitou a mudança destas séries: "
                 + ", ".join(falhas))

    with SessionLocal() as s:
        ped = _buscar(s, numero)
        ped.estado = ENVIADA
        ped.enviada_por = usuario
        ped.enviada_em = db.utcnow()
        s.commit()
    _log.info("separacao: %s enviada por %s para %s (%d unidade(s))",
              pedido_num, usuario, destino, len(unidades))

    # O relógio do equipamento fecha aqui: ENTREGUE é estado final. Sem
    # isto a tratativa de quem bipou ficaria aberta para sempre.
    _fechar_relogios(unidades, usuario, pedido_num)

    # Avisa o atendimento, se ele estiver no ar. O import é tardio e a
    # falha é engolida de propósito: a separação já aconteceu no
    # ServiceNow, e não pode ser desfeita porque o espelho do chamado
    # não respondeu.
    try:
        from routers.atendimento import equipamento_disponivel
        equipamento_disponivel(pedido_num, usuario)
    except Exception as exc:  # noqa: BLE001
        _log.error("separacao: %s enviada, mas o atendimento não foi avisado: %s",
                   pedido_num, exc)

    return api_detalhe(numero, req)


class CancelaIn(BaseModel):
    motivo: str


@router.post("/solicitacoes/{numero}/cancelar")
def api_cancelar(numero: str, body: CancelaIn, req: Request):
    sd = require_permission(req, "separacao", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo do cancelamento.")
    with SessionLocal() as s:
        ped = _buscar(s, numero)
        if ped.estado in (ENVIADA, CANCELADA):
            raise HTTPException(409, "A solicitação já está encerrada.")
        unidades = s.execute(
            select(Unidade).where(Unidade.solicitacao_id == ped.id)).scalars().all()

        # Cancelar sem soltar a reserva deixaria equipamento invisível no
        # saldo para sempre — o pior desfecho possível de um cancelamento.
        for u in unidades:
            if u.sys_id:
                _escrever_reserva(req, u.sys_id, u.serial, reservar=False)

        ped.estado = CANCELADA
        ped.motivo = f"{ped.motivo}\n[cancelada] {body.motivo.strip()}".strip()
        ped.separada_em = db.utcnow()
        ped.separada_por = sd.get("username", "")
        s.commit()
    # As unidades voltam ao estoque, e o relógio volta a ser fila.
    _devolver_relogios(unidades, sd.get("username", ""), numero.upper())
    return api_detalhe(numero, req)


def _fechar_relogios(unidades, usuario: str, pedido: str) -> None:
    """Encerra na trilha cada unidade enviada. Falha vai para o log."""
    from routers.trilha import encerrar
    for u in unidades:
        if not u.trilha_ativo_id:
            continue
        try:
            with dbt.SessionLocal() as st:
                ativo = st.get(dbt.Ativo, u.trilha_ativo_id)
                if ativo is not None and not ativo.encerrado:
                    encerrar(st, ativo, estado="ENTREGUE", processo="A15",
                             usuario=usuario)
                    st.commit()
        except Exception as exc:  # noqa: BLE001
            _log.error("separacao: %s enviada, série %s sem encerrar na trilha: %s",
                       pedido, u.serial, exc)


def _devolver_relogios(unidades, usuario: str, pedido: str) -> None:
    """Cancelamento: a unidade volta a DISPONIVEL (fila), não fica em tratativa."""
    for u in unidades:
        if not u.trilha_ativo_id:
            continue
        try:
            with dbt.SessionLocal() as st:
                ativo = st.get(dbt.Ativo, u.trilha_ativo_id)
                if ativo is not None and not ativo.encerrado:
                    mover(st, ativo, estado="DISPONIVEL", tipo=dbt.FILA,
                          processo="A15", usuario=usuario,
                          detalhe=f'{{"cancelada": "{pedido}"}}')
                    st.commit()
        except Exception as exc:  # noqa: BLE001
            _log.error("separacao: %s cancelada, série %s sem voltar ao estoque: %s",
                       pedido, u.serial, exc)


# ══════════════════════════════════════════════════════════════════
#  API — configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "separacao", "admin")
    cfg = db.ler_config()
    # Devolve o filtro já montado: o parâmetro só serve se dá para ver o
    # que ele produz antes de salvar.
    exemplos = {}
    for t in TIPOS_ATENDIMENTO:
        try:
            exemplos[t] = montar_filtro(cfg, t)
        except HTTPException as exc:
            exemplos[t] = f"(não configurado: {exc.detail})"
    return {"config": cfg, "filtros": exemplos}


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "separacao", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    if "comparacao" in pares and pares["comparacao"] not in ("STARTSWITH", "=", "LIKE"):
        raise HTTPException(400, "Comparação deve ser STARTSWITH, = ou LIKE.")
    db.gravar_config(pares)
    _log.info("separacao: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return api_config(req)
