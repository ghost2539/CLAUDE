"""Projetos de Loja (A16) — inauguração e reforma, item por item.

A Separação (A15) atende o chamado do dia: alguém pede, alguém separa,
acabou. Um projeto de loja é outra coisa — nasce meses antes, tem data
marcada (a loja abre naquele dia), e cada linha do escopo anda no seu
próprio ritmo. Por isso o token aqui é **o item do projeto**, não o
projeto: o projeto inteiro não pode ficar parado esperando a única linha
que travou no estoque.

O caminho de um item:

```
AG_DEFINICAO → AG_SEPARACAO_PROJ → EX_SEPARACAO_PROJ
                     ↑                     ↓
                 AG_ESTOQUE      AG_CONFIGURACAO_PROJ → EX_CONFIGURACAO_PROJ
                                           ↑                    ↓
                                    AG_REPARO_PROJ  ←──── (reprovou)
                                                            ↓ (aprovou)
                                                        PRONTO_PROJ → ENVIADO_PROJ
```

`AG_DEFINICAO`, `AG_ESTOQUE` e `AG_REPARO_PROJ` entram no núcleo como
intervalo `EXTERNO`: o tempo passa e aparece no total do projeto, mas
não conta contra quem separa nem contra quem configura. Escopo que não
fechou, saldo que não existe e bancada que ainda não devolveu não são
lentidão de quem está na fila.

O estoque é o mesmo da Inauguração da Separação, lido pelas funções de
lá — um só lugar decide o que é prateleira de inauguração.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.projetos as db
import db.trilha as dbt
from db.projetos import (
    Projeto, ItemProjeto, UnidadeProjeto, SessionLocal,
    TIPOS_PROJETO, ROTULO_TIPO, ROTULO_PROJETO, ROTULO_ITEM,
    PLANEJAMENTO, EM_ANDAMENTO, CONCLUIDO, CANCELADO,
    AG_DEFINICAO, AG_SEPARACAO_PROJ, EX_SEPARACAO_PROJ, AG_ESTOQUE,
    AG_CONFIGURACAO_PROJ, EX_CONFIGURACAO_PROJ, AG_REPARO_PROJ,
    PRONTO_PROJ, ENVIADO_PROJ, CANCELADO_PROJ,
    ESTADOS_PAUSA, ESTADOS_FINAIS,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, prazo_util, mover, encerrar, abrir_ativo,
    TrilhaInvalida, _utc,
)

_log = logging.getLogger("projetos")

router = APIRouter(prefix="/api/projetos", tags=["Projetos de Loja"])


# ══════════════════════════════════════════════════════════════════
#  Tempo
# ══════════════════════════════════════════════════════════════════

def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _dias(cfg: dict[str, str], chave: str) -> float:
    try:
        return float(cfg.get(chave) or 0)
    except ValueError:
        return 0.0


def _prazo_do_item(inicio: datetime, cfg: dict[str, str]) -> datetime | None:
    """Prazo do item: a soma das três etapas, a partir da criação.

    O prazo é do item inteiro, não de cada etapa. Um item que atrasou na
    definição mas correu na separação chegou na hora — cobrar etapa por
    etapa transformaria o indicador em armadilha.
    """
    total = (_dias(cfg, "prazo_definicao") + _dias(cfg, "prazo_separacao")
             + _dias(cfg, "prazo_configuracao"))
    if total <= 0:
        return None
    return prazo_util(inicio, total, _calendario())


# ══════════════════════════════════════════════════════════════════
#  Núcleo — o item do projeto é um token como qualquer outro
# ══════════════════════════════════════════════════════════════════

def _tipo_intervalo(estado: str) -> str:
    """Que espécie de relógio cada estado abre.

    Os três estados de exceção são EXTERNO de propósito: o tempo deles é
    real, entra no total do projeto, e não é de ninguém da área.
    """
    if estado in ESTADOS_PAUSA:
        return dbt.EXTERNO
    if estado.startswith("EX_"):
        return dbt.TRATATIVA
    return dbt.FILA


def _abrir_token(item: ItemProjeto, projeto: Projeto, usuario: str) -> int | None:
    """Abre o token do item no núcleo. Falha não impede o projeto."""
    try:
        with dbt.SessionLocal() as st:
            ativo = abrir_ativo(
                st, serial=item.token, usuario=usuario, modelo=item.modelo,
                tipo_equipamento="item_projeto", origem="A16", bu=projeto.bu)
            mover(st, ativo, estado=AG_DEFINICAO, tipo=dbt.EXTERNO,
                  processo="A16", usuario=usuario,
                  detalhe=f'{{"projeto": "{projeto.numero}"}}')
            st.commit()
            return ativo.id
    except TrilhaInvalida as exc:
        _log.error("projetos: token %s já existe na trilha: %s", item.token, exc)
        return None
    except Exception as exc:  # noqa: BLE001 — trilha fora do ar
        _log.error("projetos: item %s criado sem medição: %s", item.token, exc)
        return None


def _mover_token(item: ItemProjeto, estado: str, usuario: str,
                 detalhe: str = "") -> None:
    """Espelha no núcleo a mudança de estado do item.

    Tolerante a falha pelo mesmo motivo de sempre: o projeto já andou, e
    recusar o passo porque o registro de tempo não gravou inverteria a
    prioridade entre operação e indicador.
    """
    if not item.trilha_ativo_id:
        return
    try:
        with dbt.SessionLocal() as st:
            ativo = st.get(dbt.Ativo, item.trilha_ativo_id)
            if ativo is None:
                return
            if estado in ESTADOS_FINAIS:
                encerrar(st, ativo, estado=estado, processo="A16",
                         usuario=usuario)
            else:
                mover(st, ativo, estado=estado, tipo=_tipo_intervalo(estado),
                      processo="A16", usuario=usuario, detalhe=detalhe)
            st.commit()
    except Exception as exc:  # noqa: BLE001 — ver docstring
        _log.error("projetos: item %s passou a %s sem registrar tempo: %s",
                   item.token, estado, exc)


def _mover_unidade(serial: str, estado: str, usuario: str, tipo: str,
                   detalhe: str = "") -> int | None:
    """Move o equipamento de verdade, quando ele está na trilha."""
    try:
        with dbt.SessionLocal() as st:
            ativo = st.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one_or_none()
            if ativo is None:
                return None
            mover(st, ativo, estado=estado, tipo=tipo, processo="A16",
                  usuario=usuario, detalhe=detalhe)
            st.commit()
            return ativo.id
    except Exception as exc:  # noqa: BLE001
        _log.error("projetos: série %s não registrou %s: %s", serial, estado, exc)
        return None


# ══════════════════════════════════════════════════════════════════
#  Leitura
# ══════════════════════════════════════════════════════════════════

def _buscar(s, numero: str) -> Projeto:
    p = s.execute(
        select(Projeto).where(Projeto.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, f"Projeto {numero} não encontrado.")
    return p


def _resumo_item(i: ItemProjeto, unidades: list[UnidadeProjeto],
                 cal: Calendario, agora: datetime) -> dict:
    fim = _utc(i.encerrado_em) if i.estado in ESTADOS_FINAIS else None
    vivas = [u for u in unidades if u.devolvida_em is None]
    return {
        "id": i.id,
        "token": i.token,
        "modelo": i.modelo,
        "quantidade": i.quantidade,
        "area": i.area,
        "estado": i.estado,
        "estado_rotulo": ROTULO_ITEM.get(i.estado, i.estado),
        "pausado": i.estado in ESTADOS_PAUSA,
        "encerrado": i.estado in ESTADOS_FINAIS,
        "responsavel": i.responsavel,
        "observacao": i.observacao,
        "prazo": _utc(i.prazo).isoformat() if i.prazo else None,
        "atrasado": bool(i.prazo and i.estado not in ESTADOS_FINAIS
                         and _utc(i.prazo) < agora),
        "separados": len(vivas),
        "series": [{"serial": u.serial, "por": u.separada_por,
                    "em": _utc(u.separada_em).isoformat()} for u in vivas],
        "devolvidas": [{"serial": u.serial, "motivo": u.devolvida_motivo}
                       for u in unidades if u.devolvida_em is not None],
        "segundos_uteis": duracao_util(_utc(i.criado_em), fim, cal, agora),
    }


def _resumo_projeto(p: Projeto, itens: list[dict], cal: Calendario,
                    agora: datetime) -> dict:
    pendentes = [i for i in itens if not i["encerrado"]]
    return {
        "numero": p.numero,
        "chamado": p.chamado,
        "chamado_resumo": p.chamado_resumo,
        "tipo": p.tipo,
        "tipo_rotulo": ROTULO_TIPO.get(p.tipo, p.tipo),
        "loja": p.loja,
        "bu": p.bu,
        "data_prevista": p.data_prevista.isoformat() if p.data_prevista else None,
        "responsavel": p.responsavel,
        "observacao": p.observacao,
        "estado": p.estado,
        "estado_rotulo": ROTULO_PROJETO.get(p.estado, p.estado),
        "aberto_em": _utc(p.aberto_em).isoformat(),
        "aberto_por": p.aberto_por,
        "itens_total": len(itens),
        "itens_pendentes": len(pendentes),
        "itens_pausados": sum(1 for i in pendentes if i["pausado"]),
        "itens_atrasados": sum(1 for i in itens if i["atrasado"]),
        "equipamentos": sum(i["quantidade"] for i in itens),
        "separados": sum(i["separados"] for i in itens),
        "segundos_uteis": duracao_util(
            _utc(p.aberto_em),
            _utc(p.encerrado_em) if p.estado in (CONCLUIDO, CANCELADO) else None,
            cal, agora),
        "itens": itens,
    }


def _carregar(s, p: Projeto, cal: Calendario, agora: datetime) -> dict:
    itens = s.execute(
        select(ItemProjeto).where(ItemProjeto.projeto_id == p.id)
        .order_by(ItemProjeto.id)
    ).scalars().all()
    unidades = s.execute(
        select(UnidadeProjeto).where(UnidadeProjeto.projeto_id == p.id)
    ).scalars().all()
    por_item: dict[int, list[UnidadeProjeto]] = {}
    for u in unidades:
        por_item.setdefault(u.item_id, []).append(u)
    resumos = [_resumo_item(i, por_item.get(i.id, []), cal, agora) for i in itens]
    return _resumo_projeto(p, resumos, cal, agora)


@router.get("/projetos")
def api_lista(req: Request, tipo: str = "", estado: str = ""):
    require_permission(req, "projetos", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = select(Projeto).order_by(Projeto.aberto_em.desc())
        if tipo:
            q = q.where(Projeto.tipo == tipo)
        if estado:
            q = q.where(Projeto.estado == estado)
        linhas = s.execute(q.limit(200)).scalars().all()
        projetos = []
        for p in linhas:
            d = _carregar(s, p, cal, agora)
            # A lista não carrega item por item: a tela de fila só
            # precisa dos contadores, e trazer tudo deixa a página lenta
            # com dez projetos abertos.
            d.pop("itens")
            projetos.append(d)
        contagem = {e: 0 for e in ROTULO_PROJETO}
        for p in s.execute(select(Projeto)).scalars():
            contagem[p.estado] = contagem.get(p.estado, 0) + 1
    return {"projetos": projetos, "contagem": contagem,
            "em_risco": sum(1 for p in projetos if p["itens_atrasados"])}


@router.get("/projetos/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "projetos", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        return _carregar(s, _buscar(s, numero), cal, agora)


@router.get("/fila")
def api_fila(req: Request, estado: str = ""):
    """Todos os itens de todos os projetos numa fila só.

    É como quem separa e quem configura trabalha: não por projeto, mas
    por "o que está na minha etapa agora".
    """
    require_permission(req, "projetos", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = (select(ItemProjeto, Projeto)
             .join(Projeto, Projeto.id == ItemProjeto.projeto_id)
             .where(ItemProjeto.estado.notin_(ESTADOS_FINAIS))
             .order_by(ItemProjeto.prazo.is_(None), ItemProjeto.prazo))
        if estado:
            q = q.where(ItemProjeto.estado == estado)
        pares = s.execute(q.limit(500)).all()
        ids = [i.id for i, _ in pares]
        unidades = s.execute(
            select(UnidadeProjeto).where(UnidadeProjeto.item_id.in_(ids))
        ).scalars().all() if ids else []

    por_item: dict[int, list[UnidadeProjeto]] = {}
    for u in unidades:
        por_item.setdefault(u.item_id, []).append(u)

    linhas = []
    contagem: dict[str, int] = {}
    for i, p in pares:
        d = _resumo_item(i, por_item.get(i.id, []), cal, agora)
        d.update({"projeto": p.numero, "loja": p.loja,
                  "tipo_rotulo": ROTULO_TIPO.get(p.tipo, p.tipo),
                  "data_prevista": p.data_prevista.isoformat()
                                   if p.data_prevista else None})
        linhas.append(d)
        contagem[i.estado] = contagem.get(i.estado, 0) + 1
    return {"itens": linhas, "contagem": contagem,
            "rotulos": ROTULO_ITEM,
            "atrasados": sum(1 for x in linhas if x["atrasado"])}


@router.get("/catalogo")
def api_catalogo(req: Request):
    """Saldo do estoque de inauguração, lido pelo módulo de Separação."""
    require_permission(req, "projetos", "view")
    check_rate_limit(req)
    from routers.separacao import catalogo
    return catalogo(req, db.ler_config().get("tipo_estoque",
                                             "INAUGURACAO_REFORMA"))


# ══════════════════════════════════════════════════════════════════
#  Escrita — projeto
# ══════════════════════════════════════════════════════════════════

class ItemIn(BaseModel):
    modelo: str
    quantidade: int = 1
    area: str = ""
    observacao: str = ""


class ProjetoIn(BaseModel):
    chamado: str
    tipo: str = "INAUGURACAO"
    data_prevista: date | None = None
    responsavel: str = ""
    observacao: str = ""
    itens: list[ItemIn] = []


def _novo_item(s, p: Projeto, entrada: ItemIn, cfg: dict[str, str],
               usuario: str) -> ItemProjeto:
    item = ItemProjeto(
        projeto_id=p.id, token="",
        modelo=entrada.modelo.strip(),
        quantidade=int(entrada.quantidade),
        area=(entrada.area or "").strip(),
        observacao=(entrada.observacao or "").strip(),
        estado=AG_DEFINICAO,
        criado_em=db.utcnow(),
    )
    s.add(item)
    s.flush()
    # O token só pode ser montado depois do id: é ele que dá o sufixo, e
    # é o que garante que dois itens do mesmo modelo não colidam.
    item.token = f"{p.numero}/{item.id}"
    item.prazo = _prazo_do_item(_utc(item.criado_em), cfg)
    item.trilha_ativo_id = _abrir_token(item, p, usuario)
    return item


@router.post("/projetos")
def api_criar(body: ProjetoIn, req: Request):
    sd = require_permission(req, "projetos", "create")
    check_rate_limit(req)

    if body.tipo not in TIPOS_PROJETO:
        raise HTTPException(400, "Tipo de projeto inválido.")
    itens = [i for i in body.itens if i.modelo.strip() and i.quantidade > 0]

    # Mesma regra do resto da área: nada nasce sem chamado, e é dele que
    # vem a loja de destino — o destino não se digita.
    from routers.separacao import consultar_chamado
    cfg = db.ler_config()
    ch = consultar_chamado(req, body.chamado, {
        "chamado_prefixos": cfg["chamado_prefixos"],
        "chamado_estados_bloqueados": cfg["chamado_estados_bloqueados"]})

    usuario = sd.get("username", "")
    with SessionLocal() as s:
        ja = s.execute(
            select(Projeto).where(
                Projeto.chamado == ch["chamado"],
                Projeto.estado.in_((PLANEJAMENTO, EM_ANDAMENTO)))
        ).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(
                409, f"O chamado {ch['chamado']} já tem o projeto {ja.numero} "
                     "em aberto.")

        p = Projeto(
            numero=db.proximo_numero(s),
            chamado=ch["chamado"], chamado_tipo=ch["tipo"],
            chamado_resumo=ch["resumo"], loja=ch["destino"],
            tipo=body.tipo, data_prevista=body.data_prevista,
            responsavel=(body.responsavel or "").strip(),
            observacao=(body.observacao or "").strip(),
            aberto_por=usuario, aberto_em=db.utcnow(),
        )
        s.add(p)
        s.flush()
        for entrada in itens:
            _novo_item(s, p, entrada, cfg, usuario)
        s.commit()
        numero = p.numero

    _log.info("projetos: %s aberto por %s (%d item(ns), chamado %s)",
              numero, usuario, len(itens), ch["chamado"])
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens")
def api_incluir_item(numero: str, body: ItemIn, req: Request):
    sd = require_permission(req, "projetos", "edit")
    if not body.modelo.strip() or body.quantidade <= 0:
        raise HTTPException(400, "Informe modelo e quantidade.")
    cfg = db.ler_config()
    with SessionLocal() as s:
        p = _buscar(s, numero)
        if p.estado in (CONCLUIDO, CANCELADO):
            raise HTTPException(409, "O projeto já está encerrado.")
        _novo_item(s, p, body, cfg, sd.get("username", ""))
        s.commit()
    return api_detalhe(numero, req)


class EncerraIn(BaseModel):
    motivo: str = ""


@router.post("/projetos/{numero}/cancelar")
def api_cancelar_projeto(numero: str, body: EncerraIn, req: Request):
    """Cancela o projeto e, com ele, todo item que ainda não foi enviado."""
    sd = require_permission(req, "projetos", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo do cancelamento.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        p = _buscar(s, numero)
        if p.estado in (CONCLUIDO, CANCELADO):
            raise HTTPException(409, "O projeto já está encerrado.")
        itens = s.execute(
            select(ItemProjeto).where(ItemProjeto.projeto_id == p.id,
                                      ItemProjeto.estado.notin_(ESTADOS_FINAIS))
        ).scalars().all()
        for i in itens:
            _soltar_unidades(s, i, req, f"projeto cancelado: {body.motivo}")
            i.estado = CANCELADO_PROJ
            i.encerrado_em = db.utcnow()
            _mover_token(i, CANCELADO_PROJ, usuario)
        p.estado = CANCELADO
        p.encerrado_em = db.utcnow()
        p.observacao = f"{p.observacao}\n[cancelado] {body.motivo.strip()}".strip()
        s.commit()
    _log.info("projetos: %s cancelado por %s (%d item(ns))",
              numero.upper(), usuario, len(itens))
    return api_detalhe(numero, req)


# ══════════════════════════════════════════════════════════════════
#  Escrita — item (o token)
# ══════════════════════════════════════════════════════════════════

def _item(s, numero: str, item_id: int) -> tuple[Projeto, ItemProjeto]:
    p = _buscar(s, numero)
    i = s.get(ItemProjeto, item_id)
    if i is None or i.projeto_id != p.id:
        raise HTTPException(404, "Item não pertence a este projeto.")
    return p, i


def _exigir(i: ItemProjeto, *estados: str) -> None:
    if i.estado not in estados:
        esperado = " ou ".join(ROTULO_ITEM.get(e, e) for e in estados)
        raise HTTPException(
            409, f"O item está em '{ROTULO_ITEM.get(i.estado, i.estado)}' — "
                 f"esta ação só vale em {esperado}.")


def _passo(s, i: ItemProjeto, estado: str, usuario: str,
           responsavel: str | None = None, detalhe: str = "") -> None:
    i.estado = estado
    if responsavel is not None:
        i.responsavel = responsavel
    if estado in ESTADOS_FINAIS:
        i.encerrado_em = db.utcnow()
    _mover_token(i, estado, usuario, detalhe)


@router.post("/projetos/{numero}/itens/{item_id}/definir")
def api_definir(numero: str, item_id: int, req: Request):
    """Fecha o escopo do item: ele entra na fila de separação de verdade."""
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        p, i = _item(s, numero, item_id)
        _exigir(i, AG_DEFINICAO)
        _passo(s, i, AG_SEPARACAO_PROJ, usuario)
        if p.estado == PLANEJAMENTO:
            # O primeiro item definido tira o projeto do planejamento: a
            # partir daqui existe trabalho na fila de alguém.
            p.estado = EM_ANDAMENTO
        s.commit()
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/separar")
def api_assumir_separacao(numero: str, item_id: int, req: Request):
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, AG_SEPARACAO_PROJ)
        _passo(s, i, EX_SEPARACAO_PROJ, usuario, responsavel=usuario)
        s.commit()
    return api_detalhe(numero, req)


class BipeIn(BaseModel):
    serial: str


@router.post("/projetos/{numero}/itens/{item_id}/bipar")
def api_bipar(numero: str, item_id: int, body: BipeIn, req: Request):
    """Registra a série de uma unidade separada para o item.

    A ordem é a mesma da Separação e importa pelo mesmo motivo:
    localizar, reservar, e só então gravar. Reserva recusada pelo
    ServiceNow não pode deixar unidade contada aqui e disponível lá.
    """
    from routers.separacao import localizar_no_estoque, _escrever_reserva

    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    serial = (body.serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "Bipe a série do equipamento.")
    tipo_estoque = db.ler_config().get("tipo_estoque", "INAUGURACAO_REFORMA")

    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, EX_SEPARACAO_PROJ)

        vivas = s.execute(
            select(UnidadeProjeto).where(UnidadeProjeto.item_id == i.id,
                                         UnidadeProjeto.devolvida_em.is_(None))
        ).scalars().all()
        if any(u.serial == serial for u in vivas):
            raise HTTPException(409, f"A série {serial} já foi bipada neste item.")
        if len(vivas) >= i.quantidade:
            raise HTTPException(
                409, f"{i.modelo} já tem as {i.quantidade} unidades do item.")

        achado = localizar_no_estoque(req, serial, tipo_estoque)
        _escrever_reserva(req, achado["sys_id"], serial, reservar=True)

        trilha_id = _mover_unidade(
            serial, EX_SEPARACAO_PROJ, usuario, dbt.TRATATIVA,
            detalhe=f'{{"projeto": "{numero.upper()}", "item": "{i.token}"}}')
        s.add(UnidadeProjeto(projeto_id=i.projeto_id, item_id=i.id,
                             serial=serial, sys_id=achado["sys_id"],
                             trilha_ativo_id=trilha_id, separada_por=usuario))
        s.commit()
    _log.info("projetos: %s reservou %s para %s", usuario, serial, numero.upper())
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/concluir-separacao")
def api_concluir_separacao(numero: str, item_id: int, req: Request):
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, EX_SEPARACAO_PROJ)
        vivas = s.execute(
            select(UnidadeProjeto).where(UnidadeProjeto.item_id == i.id,
                                         UnidadeProjeto.devolvida_em.is_(None))
        ).scalars().all()
        if len(vivas) < i.quantidade:
            raise HTTPException(
                409, f"Faltam {i.quantidade - len(vivas)} equipamento(s) "
                     "para fechar a separação do item.")
        _passo(s, i, AG_CONFIGURACAO_PROJ, usuario)
        s.commit()
    return api_detalhe(numero, req)


class PausaIn(BaseModel):
    motivo: str


@router.post("/projetos/{numero}/itens/{item_id}/sem-estoque")
def api_sem_estoque(numero: str, item_id: int, body: PausaIn, req: Request):
    """Não há saldo: o relógio do item passa a não contar contra ninguém.

    Comprar equipamento não depende de quem separa. Deixar o item na
    fila de separação enquanto o pedido de compra anda faria a fila
    parecer lenta por um motivo que não é dela.
    """
    sd = require_permission(req, "projetos", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Diga o que falta.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, AG_SEPARACAO_PROJ, EX_SEPARACAO_PROJ)
        i.observacao = f"{i.observacao}\n[sem estoque] {body.motivo.strip()}".strip()
        _passo(s, i, AG_ESTOQUE, usuario)
        s.commit()
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/retomar")
def api_retomar(numero: str, item_id: int, req: Request):
    """Sai de uma pausa e volta para a fila de separação."""
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, AG_ESTOQUE, AG_REPARO_PROJ)
        _passo(s, i, AG_SEPARACAO_PROJ, usuario)
        s.commit()
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/configurar")
def api_assumir_configuracao(numero: str, item_id: int, req: Request):
    """A etapa de configuração: o equipamento sai da caixa e é preparado."""
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, AG_CONFIGURACAO_PROJ)
        _passo(s, i, EX_CONFIGURACAO_PROJ, usuario, responsavel=usuario)
        s.commit()
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/concluir-configuracao")
def api_concluir_configuracao(numero: str, item_id: int, req: Request):
    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, EX_CONFIGURACAO_PROJ)
        _passo(s, i, PRONTO_PROJ, usuario)
        s.commit()
    return api_detalhe(numero, req)


class ReprovaIn(BaseModel):
    serial: str
    motivo: str


@router.post("/projetos/{numero}/itens/{item_id}/reprovar")
def api_reprovar(numero: str, item_id: int, body: ReprovaIn, req: Request):
    """A unidade não passou na configuração: volta para a bancada.

    A unidade sai do item (solta a reserva e vai para a fila de triagem)
    e o item volta a precisar de equipamento. Enquanto a bancada não
    devolve, o item fica em AG_REPARO_PROJ, que não conta contra
    ninguém — o reparo é outro processo, com relógio próprio.
    """
    from routers.separacao import _escrever_reserva

    sd = require_permission(req, "projetos", "edit")
    serial = (body.serial or "").strip().upper()
    if not serial or not (body.motivo or "").strip():
        raise HTTPException(400, "Informe a série e o motivo da reprovação.")
    usuario = sd.get("username", "")
    estado_reparo = db.ler_config().get("estado_reparo", "AG_TRIAGEM")

    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        _exigir(i, EX_CONFIGURACAO_PROJ, AG_CONFIGURACAO_PROJ)
        u = s.execute(
            select(UnidadeProjeto).where(UnidadeProjeto.item_id == i.id,
                                         UnidadeProjeto.serial == serial,
                                         UnidadeProjeto.devolvida_em.is_(None))
        ).scalar_one_or_none()
        if u is None:
            raise HTTPException(404, f"A série {serial} não está neste item.")

        if u.sys_id:
            _escrever_reserva(req, u.sys_id, serial, reservar=False)
        _mover_unidade(serial, estado_reparo, usuario, dbt.FILA,
                       detalhe=f'{{"reprovado_em": "{i.token}"}}')
        u.devolvida_em = db.utcnow()
        u.devolvida_motivo = body.motivo.strip()

        i.observacao = f"{i.observacao}\n[reprovado {serial}] {body.motivo.strip()}".strip()
        _passo(s, i, AG_REPARO_PROJ, usuario)
        s.commit()
    _log.info("projetos: %s reprovou %s em %s", usuario, serial, numero.upper())
    return api_detalhe(numero, req)


@router.post("/projetos/{numero}/itens/{item_id}/enviar")
def api_enviar(numero: str, item_id: int, req: Request):
    """Expede o item: as unidades passam a estar em uso, na loja."""
    from routers.servicenow import (
        _sn_session_from_portal, _sn_update, _lookup_reference, _get_http,
        HARDWARE_TABLE,
    )
    import db.separacao as dbsep

    sd = require_permission(req, "projetos", "edit")
    usuario = sd.get("username", "")

    with SessionLocal() as s:
        p, i = _item(s, numero, item_id)
        _exigir(i, PRONTO_PROJ)
        if not (p.loja or "").strip():
            raise HTTPException(
                409, "O projeto não tem loja de destino — confira o chamado.")
        unidades = s.execute(
            select(UnidadeProjeto).where(UnidadeProjeto.item_id == i.id,
                                         UnidadeProjeto.devolvida_em.is_(None))
        ).scalars().all()
        loja, token = p.loja, i.token

    # Os parâmetros de envio são os da Separação: um só lugar decide o
    # que "em uso na loja" significa no ServiceNow.
    cfg_sep = dbsep.ler_config()
    session = _sn_session_from_portal(req)
    _req, BS = _get_http()
    local_id = _lookup_reference(session, "location", loja, {}, BS)
    if not local_id:
        raise HTTPException(
            409, f"A loja '{loja}' não foi encontrada como local no ServiceNow.")

    campo_local = (cfg_sep.get("envio_campo_local") or "location").strip()
    reserva_campo = (cfg_sep.get("reserva_campo") or "").strip()
    alteracao = {"install_status": (cfg_sep.get("envio_status") or "1").strip(),
                 campo_local: local_id}
    if reserva_campo:
        alteracao[reserva_campo] = ""

    falhas = [u.serial for u in unidades
              if not u.sys_id
              or not _sn_update(session, HARDWARE_TABLE, u.sys_id, alteracao)]
    if falhas:
        # Parcial é pior que nada: quem operar precisa saber exatamente
        # quais séries ficaram para trás.
        raise HTTPException(
            502, "O ServiceNow não aceitou a mudança destas séries: "
                 + ", ".join(falhas))

    with SessionLocal() as s:
        p, i = _item(s, numero, item_id)
        _passo(s, i, ENVIADO_PROJ, usuario)
        for u in s.execute(
                select(UnidadeProjeto).where(
                    UnidadeProjeto.item_id == i.id,
                    UnidadeProjeto.devolvida_em.is_(None))).scalars():
            _encerrar_unidade(u.serial, "ENTREGUE", usuario)
        # Projeto acabou quando não sobra item pendente.
        pendentes = s.execute(
            select(ItemProjeto).where(ItemProjeto.projeto_id == p.id,
                                      ItemProjeto.estado.notin_(ESTADOS_FINAIS))
        ).scalars().all()
        if not pendentes:
            p.estado = CONCLUIDO
            p.encerrado_em = db.utcnow()
        s.commit()
    _log.info("projetos: %s enviou %s para %s (%d unidade(s))",
              usuario, token, loja, len(unidades))
    return api_detalhe(numero, req)


def _encerrar_unidade(serial: str, estado: str, usuario: str) -> None:
    """Estado final do equipamento real: o relógio dele fecha."""
    try:
        with dbt.SessionLocal() as st:
            ativo = st.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one_or_none()
            if ativo is not None and not ativo.encerrado:
                encerrar(st, ativo, estado=estado, processo="A16", usuario=usuario)
                st.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("projetos: série %s sem encerrar em %s: %s", serial, estado, exc)


def _soltar_unidades(s, i: ItemProjeto, req: Request, motivo: str) -> None:
    """Solta a reserva de tudo que ainda está preso ao item.

    Cancelar sem soltar deixaria equipamento invisível no saldo para
    sempre — o pior desfecho possível de um cancelamento.
    """
    from routers.separacao import _escrever_reserva
    for u in s.execute(
            select(UnidadeProjeto).where(
                UnidadeProjeto.item_id == i.id,
                UnidadeProjeto.devolvida_em.is_(None))).scalars():
        if u.sys_id:
            try:
                _escrever_reserva(req, u.sys_id, u.serial, reservar=False)
            except HTTPException as exc:
                _log.error("projetos: reserva de %s não foi solta: %s",
                           u.serial, exc.detail)
        u.devolvida_em = db.utcnow()
        u.devolvida_motivo = motivo
        # Volta ao estoque na trilha: fila, não tratativa de ninguém.
        _mover_unidade(u.serial, "DISPONIVEL", "", dbt.FILA,
                       detalhe=f'{{"cancelado_em": "{i.token}"}}')


@router.post("/projetos/{numero}/itens/{item_id}/cancelar")
def api_cancelar_item(numero: str, item_id: int, body: EncerraIn, req: Request):
    sd = require_permission(req, "projetos", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo do cancelamento.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        _p, i = _item(s, numero, item_id)
        if i.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "O item já está encerrado.")
        _soltar_unidades(s, i, req, body.motivo.strip())
        i.observacao = f"{i.observacao}\n[cancelado] {body.motivo.strip()}".strip()
        _passo(s, i, CANCELADO_PROJ, usuario)
        s.commit()
    return api_detalhe(numero, req)


# ══════════════════════════════════════════════════════════════════
#  Configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "projetos", "admin")
    return {"config": db.ler_config(), "padroes": db.PADROES}


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "projetos", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("projetos: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return api_config(req)
