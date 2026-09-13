"""Atendimento a Chamados (A20) — o relógio de quem atende.

O chamado vive no ServiceNow; aqui fica o espelho do que a área mede.
Duas frentes, como no desenho: Loja (frente, retaguarda, inaugurações e
reformas) e Frota móvel (coletores e sleds).

O estado que justifica o módulo é o `AG_EQUIPAMENTO`. Quando a
resolução depende de equipamento a separar, o relógio do atendente
**pausa** e a demanda vira uma solicitação de separação. Sem isso, quem
atende é medido pelo tempo de separação de outra pessoa — que é
exatamente o tipo de indicador que faz o time desistir do indicador.

Quando a separação é enviada, a separação avisa este módulo e o chamado
volta para a fila de atendimento.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

import db.atendimento as db
import db.trilha as dbt
from db.atendimento import (
    Chamado, Movimentacao, Intervalo, Vinculo, SessionLocal,
    LOJA, FROTA, FRENTES, ROTULO_FRENTE, ROTULO_ESTADO,
    AG_ATENDIMENTO, EX_ATENDIMENTO, AG_TERCEIRO, AG_EQUIPAMENTO, RESOLVIDO,
    FILA, TRATATIVA, EXTERNO, TIPO_DO_ESTADO,
    SERIAL, REMESSA, PROJETO, ESPECIES,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import Calendario, duracao_util, prazo_util, _utc

_log = logging.getLogger("atendimento")

router = APIRouter(prefix="/api/atendimento", tags=["Atendimento"])


# ══════════════════════════════════════════════════════════════════
#  Mecânica de estado — mesma régua do núcleo, outro token
# ══════════════════════════════════════════════════════════════════

class AtendimentoInvalido(Exception):
    """Transição que violaria a consistência do espelho."""


def mover(s, ch: Chamado, *, estado: str, usuario: str = "",
          motivo: str = "", quando: datetime | None = None) -> Movimentacao:
    """Fecha o relógio anterior e abre o próximo, numa transação só."""
    if estado not in ROTULO_ESTADO:
        raise AtendimentoInvalido(f"Estado desconhecido: {estado!r}")
    tipo = TIPO_DO_ESTADO[estado]
    if tipo == TRATATIVA and not (usuario or "").strip():
        raise AtendimentoInvalido(
            "Atender exige usuário: é o relógio de alguém que começa a correr.")
    if ch.estado == RESOLVIDO and estado != AG_ATENDIMENTO:
        raise AtendimentoInvalido(
            f"O chamado {ch.numero} está resolvido; reabra antes de movimentar.")

    quando = quando or db.utcnow()
    aberto = _intervalo_aberto(s, ch.id)
    if aberto is not None and _utc(aberto.inicio) > quando:
        raise AtendimentoInvalido(
            "Movimentação anterior ao intervalo aberto — relógio não anda para trás.")

    mov = Movimentacao(chamado_id=ch.id, estado_de=ch.estado, estado_para=estado,
                       usuario=usuario, motivo=motivo, quando=quando)
    s.add(mov)
    s.flush()

    if aberto is not None:
        aberto.fim = quando

    if estado != RESOLVIDO:
        s.add(Intervalo(
            chamado_id=ch.id, estado=estado, tipo=tipo,
            sessao=_proxima_sessao(s, ch.id, estado), inicio=quando,
            usuario=(usuario if tipo == TRATATIVA else ""),
        ))
        # Sem flush, uma segunda movimentação na mesma sessão não veria o
        # intervalo recém-aberto e deixaria dois relógios correndo. Foi
        # exatamente o defeito que a verificação do núcleo pegou.
        s.flush()

    ch.estado = estado
    if estado == EX_ATENDIMENTO:
        ch.atendente = usuario
    if estado == RESOLVIDO:
        ch.resolvido_em = quando
    return mov


def _intervalo_aberto(s, chamado_id: int) -> Intervalo | None:
    return s.execute(
        select(Intervalo)
        .where(Intervalo.chamado_id == chamado_id, Intervalo.fim.is_(None))
        .order_by(Intervalo.inicio.desc())
    ).scalars().first()


def _proxima_sessao(s, chamado_id: int, estado: str) -> int:
    return int(s.execute(
        select(func.count(Intervalo.id))
        .where(Intervalo.chamado_id == chamado_id, Intervalo.estado == estado)
    ).scalar_one()) + 1


# ══════════════════════════════════════════════════════════════════
#  Espelho do ServiceNow
# ══════════════════════════════════════════════════════════════════

def _frente_da_categoria(categoria: str, resumo: str) -> str:
    """Loja ou frota, pela categoria do chamado.

    A categoria é texto livre no ServiceNow, então a lista de palavras
    que indicam frota é parâmetro. Na dúvida, cai em Loja: é a frente
    maior, e errar para o lado dela é mais fácil de perceber.
    """
    cfg = db.ler_config()
    palavras = [p.strip().lower()
                for p in (cfg.get("categorias_frota") or "").split(",") if p.strip()]
    texto = (categoria + " " + resumo).lower()
    return FROTA if any(p in texto for p in palavras) else LOJA


def ler_do_servicenow(req: Request, numero: str) -> dict:
    from routers.servicenow import _sn_session_from_portal, _sn_query, termo_sn

    numero = (numero or "").strip().upper()
    if not numero:
        raise HTTPException(400, "Informe o número do chamado.")
    numero = termo_sn(numero, "chamado")
    tabela = "sc_req_item" if numero.startswith("RITM") else "incident"
    campos = ("number,short_description,state,location,u_loja,caller_id,"
              "category,subcategory,cmdb_ci,assignment_group")
    achados = _sn_query(_sn_session_from_portal(req), tabela,
                        f"number={numero}", campos, limit=1)
    if not achados:
        raise HTTPException(404, f"Chamado {numero} não encontrado no ServiceNow.")
    r = achados[0]
    categoria = " ".join(filter(None, [(r.get("category") or "").strip(),
                                       (r.get("subcategory") or "").strip()]))
    return {
        "numero": numero,
        "tipo": "RITM" if numero.startswith("RITM") else "INC",
        "resumo": (r.get("short_description") or "").strip(),
        "local": (r.get("location") or r.get("u_loja") or "").strip(),
        "solicitante": (r.get("caller_id") or "").strip(),
        "categoria": categoria,
        "estado_sn": str(r.get("state") or "").strip(),
        "ci": (r.get("cmdb_ci") or "").strip(),
    }


# ══════════════════════════════════════════════════════════════════
#  Leitura
# ══════════════════════════════════════════════════════════════════

def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _prazo(frente: str, abertura: datetime) -> datetime | None:
    cfg = db.ler_config()
    try:
        dias = float(cfg.get("prazo_frota" if frente == FROTA else "prazo_loja") or 0)
    except ValueError:
        dias = 0
    return prazo_util(abertura, dias, _calendario()) if dias > 0 else None


def _resumo(ch: Chamado, cal: Calendario, agora: datetime,
            tempos: dict[int, dict] | None = None) -> dict:
    t = (tempos or {}).get(ch.id, {})
    prazo = _prazo(ch.frente, _utc(ch.aberto_em))
    return {
        "numero": ch.numero,
        "tipo": ch.tipo,
        "frente": ch.frente,
        "frente_rotulo": ROTULO_FRENTE.get(ch.frente, ch.frente),
        "resumo": ch.resumo,
        "local": ch.local,
        "solicitante": ch.solicitante,
        "estado": ch.estado,
        "estado_rotulo": ROTULO_ESTADO.get(ch.estado, ch.estado),
        "atendente": ch.atendente,
        "aberto_em": _utc(ch.aberto_em).isoformat(),
        "resolvido_em": _utc(ch.resolvido_em).isoformat() if ch.resolvido_em else None,
        "prazo": prazo.isoformat() if prazo else None,
        "atrasado": bool(prazo and ch.estado != RESOLVIDO and prazo < agora),
        # Total desde a abertura, e o recorte que corre contra a pessoa.
        # Separar os dois é o que torna o painel individual justo.
        "segundos_total": t.get("total", 0),
        "segundos_atendente": t.get(TRATATIVA, 0),
        "segundos_espera": t.get(EXTERNO, 0) + t.get(FILA, 0),
    }


def _tempos(s, cal: Calendario, agora: datetime,
            ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    linhas = s.execute(
        select(Intervalo).where(Intervalo.chamado_id.in_(ids))
    ).scalars().all()
    fora: dict[int, dict] = {}
    for i in linhas:
        alvo = fora.setdefault(i.chamado_id, {"total": 0, FILA: 0,
                                              TRATATIVA: 0, EXTERNO: 0})
        seg = duracao_util(_utc(i.inicio), _utc(i.fim), cal, agora)
        alvo["total"] += seg
        alvo[i.tipo] = alvo.get(i.tipo, 0) + seg
    return fora


def _buscar(s, numero: str) -> Chamado:
    ch = s.execute(
        select(Chamado).where(Chamado.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if ch is None:
        raise HTTPException(
            404, f"O chamado {numero} ainda não foi assumido no portal.")
    return ch


@router.get("/chamados")
def api_lista(req: Request, frente: str = "", estado: str = ""):
    require_permission(req, "atendimento", "view")
    cal, agora = _calendario(), db.utcnow()
    with SessionLocal() as s:
        q = select(Chamado).order_by(Chamado.aberto_em.desc())
        if frente:
            q = q.where(Chamado.frente == frente)
        if estado:
            q = q.where(Chamado.estado == estado)
        linhas = s.execute(q.limit(300)).scalars().all()
        tempos = _tempos(s, cal, agora, [c.id for c in linhas])
        chamados = [_resumo(c, cal, agora, tempos) for c in linhas]
        contagem = {e: 0 for e in ROTULO_ESTADO}
        for c in s.execute(select(Chamado)).scalars():
            contagem[c.estado] = contagem.get(c.estado, 0) + 1
    return {
        "chamados": chamados,
        "contagem": contagem,
        "atrasados": sum(1 for c in chamados if c["atrasado"]),
    }


@router.get("/chamados/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "atendimento", "view")
    cal, agora = _calendario(), db.utcnow()
    with SessionLocal() as s:
        ch = _buscar(s, numero)
        tempos = _tempos(s, cal, agora, [ch.id])
        dados = _resumo(ch, cal, agora, tempos)
        movs = s.execute(
            select(Movimentacao).where(Movimentacao.chamado_id == ch.id)
            .order_by(Movimentacao.quando, Movimentacao.id)
        ).scalars().all()
        intervalos = s.execute(
            select(Intervalo).where(Intervalo.chamado_id == ch.id)
            .order_by(Intervalo.inicio, Intervalo.id)
        ).scalars().all()
        vinculos = s.execute(
            select(Vinculo).where(Vinculo.chamado_id == ch.id)
        ).scalars().all()

    dados["linha_do_tempo"] = [{
        "quando": _utc(m.quando).isoformat(), "de": m.estado_de,
        "para": m.estado_para, "para_rotulo": ROTULO_ESTADO.get(m.estado_para, m.estado_para),
        "usuario": m.usuario, "motivo": m.motivo,
    } for m in movs]
    dados["intervalos"] = [{
        "estado": i.estado, "estado_rotulo": ROTULO_ESTADO.get(i.estado, i.estado),
        "tipo": i.tipo, "sessao": i.sessao, "usuario": i.usuario,
        "aberto": i.fim is None,
        "segundos": duracao_util(_utc(i.inicio), _utc(i.fim), cal, agora),
    } for i in intervalos]
    dados["vinculos"] = [{"especie": v.especie, "valor": v.valor}
                         for v in vinculos]
    return dados


# ══════════════════════════════════════════════════════════════════
#  Escrita
# ══════════════════════════════════════════════════════════════════

@router.post("/chamados/{numero}/assumir")
def api_assumir(numero: str, req: Request):
    """Traz o chamado do ServiceNow e coloca o relógio do atendente a correr."""
    sd = require_permission(req, "atendimento", "edit")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    lido = ler_do_servicenow(req, numero)

    with SessionLocal() as s:
        ch = s.execute(
            select(Chamado).where(Chamado.numero == lido["numero"])
        ).scalar_one_or_none()
        if ch is None:
            ch = Chamado(
                numero=lido["numero"], tipo=lido["tipo"],
                frente=_frente_da_categoria(lido["categoria"], lido["resumo"]),
                resumo=lido["resumo"], local=lido["local"],
                solicitante=lido["solicitante"], categoria=lido["categoria"],
                estado_sn=lido["estado_sn"], lido_em=db.utcnow(),
            )
            s.add(ch)
            s.flush()
            # O chamado nasce em fila e é assumido em seguida, mesmo que
            # nos mesmos segundos: sem a fila, o painel nunca saberia
            # quanto tempo um chamado espera antes de alguém pegar.
            mover(s, ch, estado=AG_ATENDIMENTO)
        else:
            ch.estado_sn = lido["estado_sn"]
            ch.lido_em = db.utcnow()
            if ch.estado == EX_ATENDIMENTO and ch.atendente != usuario:
                raise HTTPException(
                    409, f"{ch.atendente} já está atendendo este chamado.")

        # Vincula o CI do chamado quando o ServiceNow trouxer um: é o
        # vínculo que alimenta o histórico das bancadas.
        if lido.get("ci"):
            _vincular(s, ch, SERIAL, lido["ci"], usuario)

        _tentar(mover, s, ch, estado=EX_ATENDIMENTO, usuario=usuario)
        s.commit()
    _log.info("atendimento: %s assumido por %s", lido["numero"], usuario)
    return api_detalhe(numero, req)


class PausaIn(BaseModel):
    motivo: str


@router.post("/chamados/{numero}/aguardar-terceiro")
def api_aguardar_terceiro(numero: str, body: PausaIn, req: Request):
    """Pausa por espera externa. Não conta contra o atendente."""
    sd = require_permission(req, "atendimento", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Diga o que se está esperando.")
    with SessionLocal() as s:
        ch = _buscar(s, numero)
        if ch.estado != EX_ATENDIMENTO:
            raise HTTPException(409, "Só um chamado em atendimento pode ser pausado.")
        _tentar(mover, s, ch, estado=AG_TERCEIRO,
                usuario=sd.get("username", ""), motivo=body.motivo.strip())
        s.commit()
    return api_detalhe(numero, req)


class EquipamentoIn(BaseModel):
    tipo_atendimento: str
    itens: list[dict] = []
    prioridade: str = "normal"


@router.post("/chamados/{numero}/aguardar-equipamento")
def api_aguardar_equipamento(numero: str, body: EquipamentoIn, req: Request):
    """Abre a separação e pausa o relógio do atendente.

    É o ponto em que os dois módulos se encontram. O atendente não abre
    uma solicitação avulsa: ele declara que precisa de equipamento, e o
    pedido nasce do chamado com destino, resumo e prioridade já
    preenchidos.
    """
    sd = require_permission(req, "atendimento", "edit")
    usuario = sd.get("username", "")

    with SessionLocal() as s:
        ch = _buscar(s, numero)
        if ch.estado != EX_ATENDIMENTO:
            raise HTTPException(
                409, "Assuma o atendimento antes de pedir equipamento.")
        numero_sn = ch.numero

    # A solicitação é criada primeiro: se a separação recusar (chamado
    # encerrado, estoque não configurado), o chamado não pode ficar
    # parado num estado de espera que não tem contrapartida.
    from routers.separacao import api_criar, SolicitacaoIn, ItemIn
    pedido = api_criar(SolicitacaoIn(
        chamado=numero_sn,
        tipo_atendimento=body.tipo_atendimento,
        prioridade=body.prioridade,
        itens=[ItemIn(modelo=i.get("modelo", ""),
                      quantidade=int(i.get("quantidade", 1)))
               for i in body.itens],
    ), req)

    with SessionLocal() as s:
        ch = _buscar(s, numero)
        _vincular(s, ch, REMESSA, pedido["numero"], usuario)
        _tentar(mover, s, ch, estado=AG_EQUIPAMENTO, usuario=usuario,
                motivo=f"Separação {pedido['numero']}")
        s.commit()
    _log.info("atendimento: %s aguardando a separação %s",
              numero_sn, pedido["numero"])
    return api_detalhe(numero, req)


@router.post("/chamados/{numero}/retomar")
def api_retomar(numero: str, req: Request):
    sd = require_permission(req, "atendimento", "edit")
    with SessionLocal() as s:
        ch = _buscar(s, numero)
        if ch.estado not in (AG_ATENDIMENTO, AG_TERCEIRO, AG_EQUIPAMENTO):
            raise HTTPException(409, "O chamado não está em espera.")
        _tentar(mover, s, ch, estado=EX_ATENDIMENTO, usuario=sd.get("username", ""))
        s.commit()
    return api_detalhe(numero, req)


class ResolveIn(BaseModel):
    motivo: str = ""


@router.post("/chamados/{numero}/resolver")
def api_resolver(numero: str, body: ResolveIn, req: Request):
    sd = require_permission(req, "atendimento", "edit")
    with SessionLocal() as s:
        ch = _buscar(s, numero)
        if ch.estado == RESOLVIDO:
            raise HTTPException(409, "O chamado já está resolvido.")
        _tentar(mover, s, ch, estado=RESOLVIDO, usuario=sd.get("username", ""),
                motivo=(body.motivo or "").strip())
        s.commit()
    _log.info("atendimento: %s resolvido por %s", numero.upper(),
              sd.get("username", ""))
    return api_detalhe(numero, req)


class VinculoIn(BaseModel):
    especie: str
    valor: str


@router.post("/chamados/{numero}/vinculos")
def api_vincular(numero: str, body: VinculoIn, req: Request):
    sd = require_permission(req, "atendimento", "edit")
    if body.especie not in ESPECIES:
        raise HTTPException(400, "Espécie de vínculo inválida.")
    if not (body.valor or "").strip():
        raise HTTPException(400, "Informe o valor do vínculo.")
    with SessionLocal() as s:
        ch = _buscar(s, numero)
        _vincular(s, ch, body.especie, body.valor, sd.get("username", ""))
        s.commit()
    return api_detalhe(numero, req)


def _vincular(s, ch: Chamado, especie: str, valor: str, usuario: str) -> None:
    valor = (valor or "").strip().upper()
    if not valor:
        return
    ja = s.execute(
        select(Vinculo).where(Vinculo.chamado_id == ch.id,
                              Vinculo.especie == especie,
                              Vinculo.valor == valor)
    ).scalar_one_or_none()
    if ja is None:
        s.add(Vinculo(chamado_id=ch.id, especie=especie, valor=valor,
                      criado_por=usuario))


def _tentar(funcao, *args, **kwargs):
    """Traduz a recusa do motor em resposta HTTP, sem vazar traceback."""
    try:
        return funcao(*args, **kwargs)
    except AtendimentoInvalido as exc:
        raise HTTPException(409, str(exc))


# ══════════════════════════════════════════════════════════════════
#  Gancho vindo da Separação
# ══════════════════════════════════════════════════════════════════

def equipamento_disponivel(numero_solicitacao: str, usuario: str = "") -> None:
    """Chamado por routers.separacao quando a solicitação é enviada.

    Devolve o chamado para a fila de atendimento em vez de reabrir
    direto na pessoa: quem pediu pode não estar na mesa, e um relógio de
    tratativa correndo sem ninguém trabalhando é pior do que uma fila.
    """
    with SessionLocal() as s:
        vinculo = s.execute(
            select(Vinculo).where(Vinculo.especie == REMESSA,
                                  Vinculo.valor == numero_solicitacao.upper())
        ).scalars().first()
        if vinculo is None:
            return
        ch = s.get(Chamado, vinculo.chamado_id)
        if ch is None or ch.estado != AG_EQUIPAMENTO:
            return
        mover(s, ch, estado=AG_ATENDIMENTO, usuario=usuario,
              motivo=f"Equipamento separado e enviado ({numero_solicitacao})")
        s.commit()
    _log.info("atendimento: chamado devolvido à fila pela separação %s",
              numero_solicitacao)


# ══════════════════════════════════════════════════════════════════
#  Configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "atendimento", "admin")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "atendimento", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("atendimento: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return db.ler_config()
