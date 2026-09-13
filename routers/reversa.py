"""Logística Reversa (A17) — o caminho de volta, coleta por coleta.

O token é a **Coleta**: o que se combinou que a loja devolveria, com
prazo acordado, código de rastreio quando postar, e a lista do que era
esperado. A coleta só fecha depois de conferida, e fecha como CONFERIDA
ou como DIVERGENTE. "Recebida" a granel não existe aqui, porque é assim
que ativo se perde: não no roubo, na falta de conferência.

```
AG_POSTAGEM_REV → EM_TRANSITO_REV → AG_CONFERENCIA_REV → EX_CONFERENCIA_REV
   (loja)           (Correios)          (fila do CD)          (pessoa)
                                                                   ↓
                                                    CONFERIDA_REV | DIVERGENTE_REV
```

Duas coisas decidem o desenho:

- **Custódia muda quando o recebedor confirma, não quando o entregador
  declara.** O rastreio dos Correios dizendo "entregue" leva a coleta
  para a fila de conferência; quem diz que chegou de verdade é o bipe
  de quem abriu a caixa — ou o Recebimento (A01), que casa sozinho com
  a coleta quando a série bate.
- **Divergência é resultado, não erro.** Faltou ou sobrou, a coleta
  fecha como divergente e cada diferença vira um token de regularização
  (A19), com responsável e prazo. Deixar a coleta aberta "até resolver"
  esconderia o indicador que o módulo existe para mostrar.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.reversa as db
import db.trilha as dbt
from db.reversa import (
    Coleta, Esperado, Recebido, SessionLocal,
    ROTULO_ESTADO, AG_POSTAGEM, EM_TRANSITO, AG_CONFERENCIA, EX_CONFERENCIA,
    CONFERIDA, DIVERGENTE, CANCELADA, ESTADOS_FORA, ESTADOS_FINAIS,
    ORIGEM_CONFERENCIA, ORIGEM_RECEBIMENTO,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, prazo_util, mover, encerrar, abrir_ativo,
    TrilhaInvalida, _utc,
)

_log = logging.getLogger("reversa")

router = APIRouter(prefix="/api/reversa", tags=["Logística Reversa"])


# ══════════════════════════════════════════════════════════════════
#  Núcleo
# ══════════════════════════════════════════════════════════════════

def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _tipo_intervalo(estado: str) -> str:
    if estado in ESTADOS_FORA:
        return dbt.EXTERNO
    if estado == EX_CONFERENCIA:
        return dbt.TRATATIVA
    return dbt.FILA


def _abrir_token(coleta: Coleta, usuario: str) -> int | None:
    try:
        with dbt.SessionLocal() as st:
            ativo = abrir_ativo(st, serial=coleta.numero, usuario=usuario,
                                tipo_equipamento="coleta", origem="A17",
                                bu=coleta.bu)
            mover(st, ativo, estado=AG_POSTAGEM, tipo=dbt.EXTERNO,
                  processo="A17", usuario=usuario,
                  detalhe=f'{{"loja": "{coleta.loja}"}}')
            st.commit()
            return ativo.id
    except TrilhaInvalida as exc:
        _log.error("reversa: token %s já existe na trilha: %s", coleta.numero, exc)
    except Exception as exc:  # noqa: BLE001 — trilha fora do ar
        _log.error("reversa: coleta %s aberta sem medição: %s", coleta.numero, exc)
    return None


def _mover_token(coleta: Coleta, estado: str, usuario: str) -> None:
    """Espelha a coleta no núcleo. Falha não segura o processo."""
    if not coleta.trilha_ativo_id:
        return
    try:
        with dbt.SessionLocal() as st:
            ativo = st.get(dbt.Ativo, coleta.trilha_ativo_id)
            if ativo is None:
                return
            if estado in ESTADOS_FINAIS:
                encerrar(st, ativo, estado=estado, processo="A17", usuario=usuario)
            else:
                mover(st, ativo, estado=estado, tipo=_tipo_intervalo(estado),
                      processo="A17", usuario=usuario)
            st.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("reversa: coleta %s passou a %s sem registrar tempo: %s",
                   coleta.numero, estado, exc)


def _passo(c: Coleta, estado: str, usuario: str) -> None:
    c.estado = estado
    if estado in ESTADOS_FINAIS:
        c.encerrada_em = db.utcnow()
    _mover_token(c, estado, usuario)


def _chegou(c: Coleta, usuario: str, cfg: dict[str, str]) -> None:
    """O pacote está no CD: sai de fora da área e entra na fila.

    Chamado tanto pelo rastreio (Correios disse entregue) quanto pelo
    recebimento físico. Idempotente: a segunda evidência não reabre nem
    duplica.
    """
    if c.estado not in ESTADOS_FORA:
        return
    agora = db.utcnow()
    c.chegou_em = agora
    try:
        dias = float(cfg.get("prazo_conferencia_dias") or 0)
    except ValueError:
        dias = 0
    c.prazo_conferencia = prazo_util(agora, dias, _calendario()) if dias > 0 else None
    _passo(c, AG_CONFERENCIA, usuario)


# ══════════════════════════════════════════════════════════════════
#  Leitura
# ══════════════════════════════════════════════════════════════════

def _buscar(s, numero: str) -> Coleta:
    c = s.execute(
        select(Coleta).where(Coleta.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, f"Coleta {numero} não encontrada.")
    return c


def _comparar(esperados: list[Esperado], recebidos: list[Recebido]) -> dict:
    """Esperado × recebido — o indicador do módulo."""
    por_esperado = {r.esperado_id: r for r in recebidos if r.esperado_id}
    ok, faltantes = [], []
    for e in esperados:
        linha = {"id": e.id, "serial": e.serial, "etiqueta": e.etiqueta,
                 "modelo": e.modelo, "descricao": e.descricao}
        r = por_esperado.get(e.id)
        if r is not None:
            linha.update({"recebido_em": _utc(r.recebido_em).isoformat(),
                          "recebido_por": r.recebido_por, "origem": r.origem})
            ok.append(linha)
        else:
            faltantes.append(linha)
    inesperados = [{"serial": r.serial, "etiqueta": r.etiqueta,
                    "recebido_em": _utc(r.recebido_em).isoformat(),
                    "recebido_por": r.recebido_por, "origem": r.origem}
                   for r in recebidos if not r.esperado_id]
    return {"ok": ok, "faltantes": faltantes, "inesperados": inesperados,
            "esperados": len(esperados), "recebidos": len(recebidos),
            "divergente": bool(faltantes or inesperados)}


def _resumo(c: Coleta, cal: Calendario, agora: datetime) -> dict:
    fim = _utc(c.encerrada_em) if c.estado in ESTADOS_FINAIS else None
    hoje = agora.date()
    return {
        "numero": c.numero,
        "chamado": c.chamado,
        "chamado_resumo": c.chamado_resumo,
        "loja": c.loja,
        "bu": c.bu,
        "motivo": c.motivo,
        "estado": c.estado,
        "estado_rotulo": ROTULO_ESTADO.get(c.estado, c.estado),
        "fora_da_area": c.estado in ESTADOS_FORA,
        "encerrada": c.estado in ESTADOS_FINAIS,
        "prazo_acordado": c.prazo_acordado.isoformat() if c.prazo_acordado else None,
        # A loja atrasou: combinou postar e ainda não postou.
        "postagem_atrasada": bool(c.prazo_acordado and c.estado == AG_POSTAGEM
                                  and c.prazo_acordado < hoje),
        "codigo_rastreio": c.codigo_rastreio,
        "postada_em": _utc(c.postada_em).isoformat() if c.postada_em else None,
        "ultimo_evento": c.ultimo_evento,
        "ultimo_evento_em": c.ultimo_evento_em,
        "chegou_em": _utc(c.chegou_em).isoformat() if c.chegou_em else None,
        "prazo_conferencia": (_utc(c.prazo_conferencia).isoformat()
                              if c.prazo_conferencia else None),
        # O CD atrasou: chegou e ninguém conferiu.
        "conferencia_atrasada": bool(
            c.prazo_conferencia and c.estado in (AG_CONFERENCIA, EX_CONFERENCIA)
            and _utc(c.prazo_conferencia) < agora),
        "aberta_por": c.aberta_por,
        "aberta_em": _utc(c.aberta_em).isoformat(),
        "conferida_por": c.conferida_por,
        "observacao": c.observacao,
        "segundos_uteis": duracao_util(_utc(c.aberta_em), fim, cal, agora),
        "comparacao": None,
    }


def _detalhe(s, c: Coleta) -> dict:
    cal, agora = _calendario(), dbt.utcnow()
    d = _resumo(c, cal, agora)
    esperados = s.execute(
        select(Esperado).where(Esperado.coleta_id == c.id).order_by(Esperado.id)
    ).scalars().all()
    recebidos = s.execute(
        select(Recebido).where(Recebido.coleta_id == c.id).order_by(Recebido.id)
    ).scalars().all()
    d["comparacao"] = _comparar(esperados, recebidos)
    return d


@router.get("/coletas")
def api_lista(req: Request, estado: str = "", loja: str = ""):
    require_permission(req, "reversa", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = select(Coleta).order_by(Coleta.aberta_em.desc())
        if estado:
            q = q.where(Coleta.estado == estado)
        if loja:
            q = q.where(Coleta.loja.ilike(f"%{loja.strip()}%"))
        linhas = s.execute(q.limit(300)).scalars().all()
        coletas = []
        for c in linhas:
            d = _resumo(c, cal, agora)
            esperados = s.execute(
                select(Esperado).where(Esperado.coleta_id == c.id)).scalars().all()
            recebidos = s.execute(
                select(Recebido).where(Recebido.coleta_id == c.id)).scalars().all()
            cmp_ = _comparar(esperados, recebidos)
            d["esperados"] = cmp_["esperados"]
            d["recebidos"] = cmp_["recebidos"]
            d["faltantes"] = len(cmp_["faltantes"])
            d["inesperados"] = len(cmp_["inesperados"])
            coletas.append(d)
        contagem = {e: 0 for e in ROTULO_ESTADO}
        for c in s.execute(select(Coleta)).scalars():
            contagem[c.estado] = contagem.get(c.estado, 0) + 1
    return {
        "coletas": coletas,
        "contagem": contagem,
        "postagem_atrasada": sum(1 for c in coletas if c["postagem_atrasada"]),
        "conferencia_atrasada": sum(1 for c in coletas if c["conferencia_atrasada"]),
        # Faltantes acumulados nas coletas fechadas divergentes: é o
        # número que a gestão quer ver, e o que a regularização caça.
        "faltantes_abertos": sum(c["faltantes"] for c in coletas
                                 if c["estado"] == DIVERGENTE),
    }


@router.get("/coletas/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "reversa", "view")
    with SessionLocal() as s:
        return _detalhe(s, _buscar(s, numero))


# ══════════════════════════════════════════════════════════════════
#  Escrita — abrir e acompanhar
# ══════════════════════════════════════════════════════════════════

class EsperadoIn(BaseModel):
    serial: str = ""
    etiqueta: str = ""
    modelo: str = ""
    descricao: str = ""


class ColetaIn(BaseModel):
    chamado: str
    motivo: str = ""
    prazo_acordado: date | None = None
    codigo_rastreio: str = ""
    itens: list[EsperadoIn] = []


def _consultar_chamado(req: Request, numero: str) -> dict:
    """Valida no ServiceNow pela função da Separação, com os prefixos daqui."""
    from routers.separacao import consultar_chamado
    cfg = db.ler_config()
    return consultar_chamado(req, numero, {
        "chamado_prefixos": cfg["chamado_prefixos"],
        "chamado_estados_bloqueados": cfg["chamado_estados_bloqueados"]})


@router.post("/coletas")
def api_criar(body: ColetaIn, req: Request):
    sd = require_permission(req, "reversa", "create")
    check_rate_limit(req)
    itens = [i for i in body.itens if (i.serial or i.etiqueta or i.modelo).strip()]
    if not itens:
        raise HTTPException(400, "Informe ao menos um equipamento esperado.")

    ch = _consultar_chamado(req, body.chamado)
    usuario = sd.get("username", "")
    cfg = db.ler_config()

    prazo = body.prazo_acordado
    if prazo is None:
        try:
            prazo = date.today() + timedelta(days=int(cfg.get("prazo_postagem_dias") or 0))
        except ValueError:
            prazo = None

    with SessionLocal() as s:
        ja = s.execute(
            select(Coleta).where(Coleta.chamado == ch["chamado"],
                                 Coleta.estado.notin_(ESTADOS_FINAIS))
        ).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(
                409, f"O chamado {ch['chamado']} já tem a coleta {ja.numero} em aberto.")

        c = Coleta(
            numero=db.proximo_numero(s),
            chamado=ch["chamado"], chamado_tipo=ch["tipo"],
            chamado_resumo=ch["resumo"], loja=ch["destino"],
            motivo=(body.motivo or "").strip(),
            prazo_acordado=prazo,
            codigo_rastreio=(body.codigo_rastreio or "").strip().upper(),
            aberta_por=usuario, aberta_em=db.utcnow(),
        )
        s.add(c)
        s.flush()
        for i in itens:
            s.add(Esperado(coleta_id=c.id,
                           serial=(i.serial or "").strip().upper(),
                           etiqueta=(i.etiqueta or "").strip().upper(),
                           modelo=(i.modelo or "").strip(),
                           descricao=(i.descricao or "").strip()))
        c.trilha_ativo_id = _abrir_token(c, usuario)
        # Código informado na abertura: a loja já postou.
        if c.codigo_rastreio:
            c.postada_em = db.utcnow()
            _passo(c, EM_TRANSITO, usuario)
        s.commit()
        numero = c.numero

    _log.info("reversa: %s aberta por %s (loja %s, %d item(ns))",
              numero, usuario, ch["destino"], len(itens))
    return api_detalhe(numero, req)


class PostagemIn(BaseModel):
    codigo_rastreio: str


@router.post("/coletas/{numero}/postagem")
def api_postagem(numero: str, body: PostagemIn, req: Request):
    """A loja postou: registra o código e passa a bola para os Correios."""
    sd = require_permission(req, "reversa", "edit")
    codigo = (body.codigo_rastreio or "").strip().upper()
    if len(codigo) < 10:
        raise HTTPException(400, "Código de rastreio inválido.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado not in (AG_POSTAGEM, EM_TRANSITO):
            raise HTTPException(409, "A coleta já chegou ou já foi encerrada.")
        c.codigo_rastreio = codigo
        if c.estado == AG_POSTAGEM:
            c.postada_em = db.utcnow()
            _passo(c, EM_TRANSITO, usuario)
        s.commit()
    return api_detalhe(numero, req)


@router.post("/coletas/{numero}/rastrear")
def api_rastrear(numero: str, req: Request):
    """Consulta os Correios e atualiza a coleta pelo que eles dizem.

    "Entregue" nos Correios leva para a fila de conferência — não fecha
    nada. Custódia muda quando alguém do CD abre a caixa e bipa.
    """
    from routers.correios import consultar_rastreio

    # Muda estado da coleta: é edição, ainda que a fonte seja uma consulta.
    sd = require_permission(req, "reversa", "edit")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if not c.codigo_rastreio:
            raise HTTPException(409, "A coleta ainda não tem código de rastreio.")
        codigo = c.codigo_rastreio

    r = consultar_rastreio(codigo)

    with SessionLocal() as s:
        c = _buscar(s, numero)
        if r.get("encontrado") and r.get("eventos"):
            ult = r["eventos"][0]
            c.ultimo_evento = (ult.get("descricao") or "").strip()
            if ult.get("local"):
                c.ultimo_evento += f" — {ult['local']}"
            c.ultimo_evento_em = (ult.get("data") or "").strip()
            if c.estado == AG_POSTAGEM:
                # Há evento: a loja postou, mesmo sem avisar.
                c.postada_em = c.postada_em or db.utcnow()
                _passo(c, EM_TRANSITO, usuario)
            if r.get("entrega") and c.estado in ESTADOS_FORA:
                _chegou(c, usuario, db.ler_config())
        else:
            c.ultimo_evento = "Objeto ainda não consta nos Correios"
        s.commit()
    return {**api_detalhe(numero, req), "rastreio": r}


# ══════════════════════════════════════════════════════════════════
#  Escrita — conferência
# ══════════════════════════════════════════════════════════════════

@router.post("/coletas/{numero}/conferir")
def api_assumir(numero: str, req: Request):
    """Assume a conferência. Também vale como 'chegou' se ninguém avisou."""
    sd = require_permission(req, "reversa", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "A coleta já está encerrada.")
        if c.estado in ESTADOS_FORA:
            # O pacote está na mesa de quem clicou: isso é chegada.
            _chegou(c, usuario, db.ler_config())
        if c.estado != AG_CONFERENCIA:
            raise HTTPException(409, f"A coleta está em '{ROTULO_ESTADO.get(c.estado)}'.")
        c.conferida_por = usuario
        _passo(c, EX_CONFERENCIA, usuario)
        s.commit()
    return api_detalhe(numero, req)


class BipeIn(BaseModel):
    serial: str


def _casar(esperados: list[Esperado], recebidos: list[Recebido],
           serial: str) -> Esperado | None:
    """Acha o esperado que esta leitura atende — por série ou etiqueta.

    Etiqueta vale porque a loja muitas vezes só sabe o número colado no
    equipamento; e o mesmo esperado não pode ser atendido duas vezes.
    """
    atendidos = {r.esperado_id for r in recebidos if r.esperado_id}
    for e in esperados:
        if e.id in atendidos:
            continue
        if serial and serial in (e.serial, e.etiqueta):
            return e
    return None


@router.post("/coletas/{numero}/bipar")
def api_bipar(numero: str, body: BipeIn, req: Request):
    sd = require_permission(req, "reversa", "edit")
    usuario = sd.get("username", "")
    serial = (body.serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "Bipe a série ou a etiqueta.")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado != EX_CONFERENCIA:
            raise HTTPException(409, "Assuma a conferência antes de bipar.")
        esperados = s.execute(
            select(Esperado).where(Esperado.coleta_id == c.id)).scalars().all()
        recebidos = s.execute(
            select(Recebido).where(Recebido.coleta_id == c.id)).scalars().all()
        if any(r.serial == serial for r in recebidos):
            raise HTTPException(409, f"{serial} já foi lido nesta coleta.")
        e = _casar(esperados, recebidos, serial)
        s.add(Recebido(coleta_id=c.id, esperado_id=e.id if e else None,
                       serial=serial, etiqueta=(e.etiqueta if e else ""),
                       origem=ORIGEM_CONFERENCIA, recebido_por=usuario))
        s.commit()
        casou = e is not None
    return {**api_detalhe(numero, req), "casou": casou}


class ConclusaoIn(BaseModel):
    observacao: str = ""


@router.post("/coletas/{numero}/concluir")
def api_concluir(numero: str, body: ConclusaoIn, req: Request):
    """Fecha a conferência como CONFERIDA ou DIVERGENTE.

    Divergente exige observação: é o registro que a regularização vai
    ler, e "faltou" sem contexto não ajuda quem for atrás.
    """
    sd = require_permission(req, "reversa", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado != EX_CONFERENCIA:
            raise HTTPException(409, "Só uma conferência em andamento pode ser concluída.")
        d = _detalhe(s, c)
        cmp_ = d["comparacao"]
        if cmp_["divergente"] and not (body.observacao or "").strip():
            raise HTTPException(
                400, f"Faltam {len(cmp_['faltantes'])} e sobraram "
                     f"{len(cmp_['inesperados'])} — descreva o que aconteceu.")
        if body.observacao.strip():
            c.observacao = f"{c.observacao}\n{body.observacao.strip()}".strip()
        c.conferida_por = usuario
        _passo(c, DIVERGENTE if cmp_["divergente"] else CONFERIDA, usuario)
        s.commit()
        numero_c, loja, divergente = c.numero, c.loja, cmp_["divergente"]
        faltantes, inesperados = cmp_["faltantes"], cmp_["inesperados"]

    _log.info("reversa: %s concluída por %s como %s (%d faltante(s), %d inesperado(s))",
              numero_c, usuario, "DIVERGENTE" if divergente else "CONFERIDA",
              len(faltantes), len(inesperados))

    if divergente and (db.ler_config().get("abrir_regularizacao") or "1") == "1":
        _abrir_regularizacao(numero_c, loja, faltantes, inesperados, usuario)
    return api_detalhe(numero, req)


def _abrir_regularizacao(numero: str, loja: str, faltantes: list[dict],
                         inesperados: list[dict], usuario: str) -> None:
    """Cada diferença vira um token de regularização (A19).

    Import tardio e falha engolida: a coleta já fechou como divergente,
    e isso não pode ser desfeito porque o módulo vizinho não respondeu.
    O que não abriu fica no log para abrir à mão.
    """
    try:
        from routers.regularizacao import abrir_divergencia
    except Exception as exc:  # noqa: BLE001 — módulo ainda não existe/no ar
        _log.warning("reversa: %s divergente, regularização não disponível: %s",
                     numero, exc)
        return
    for f in faltantes:
        try:
            abrir_divergencia(origem="A17", referencia=numero, loja=loja,
                              tipo="FALTANTE", serial=f.get("serial", ""),
                              etiqueta=f.get("etiqueta", ""),
                              modelo=f.get("modelo", ""), usuario=usuario)
        except Exception as exc:  # noqa: BLE001
            _log.error("reversa: %s faltante %s sem regularização: %s",
                       numero, f.get("serial") or f.get("etiqueta"), exc)
    for i in inesperados:
        try:
            abrir_divergencia(origem="A17", referencia=numero, loja=loja,
                              tipo="INESPERADO", serial=i.get("serial", ""),
                              etiqueta=i.get("etiqueta", ""), modelo="",
                              usuario=usuario)
        except Exception as exc:  # noqa: BLE001
            _log.error("reversa: %s inesperado %s sem regularização: %s",
                       numero, i.get("serial"), exc)


class CancelaIn(BaseModel):
    motivo: str


@router.post("/coletas/{numero}/cancelar")
def api_cancelar(numero: str, body: CancelaIn, req: Request):
    sd = require_permission(req, "reversa", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo do cancelamento.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "A coleta já está encerrada.")
        recebidos = s.execute(
            select(Recebido).where(Recebido.coleta_id == c.id)).scalars().all()
        if recebidos:
            # Já chegou coisa: cancelar apagaria a evidência. Conclui-se
            # como divergente, com o que faltou registrado.
            raise HTTPException(
                409, "Já há equipamento recebido nesta coleta — conclua a "
                     "conferência em vez de cancelar.")
        c.observacao = f"{c.observacao}\n[cancelada] {body.motivo.strip()}".strip()
        _passo(c, CANCELADA, usuario)
        s.commit()
    return api_detalhe(numero, req)


# ══════════════════════════════════════════════════════════════════
#  Gancho do Recebimento (A01)
# ══════════════════════════════════════════════════════════════════

def registrar_recebimento(itens: list[dict], usuario: str) -> dict:
    """Casa o que chegou pelo Recebimento com as coletas em aberto.

    Chamado por `routers.recebimento` depois de gravar. Uma série que
    bate com um esperado de coleta aberta entra como recebida ali, e a
    coleta — se ainda estava com a loja ou com os Correios — passa para
    a fila de conferência: a caixa está no CD, quem confirmou foi quem
    recebeu.

    Não abre coleta nova e não marca inesperado: recebimento avulso é
    recebimento, não divergência de coleta nenhuma.
    """
    resumo = {"casados": 0, "coletas": []}
    chaves = set()
    for it in itens:
        for k in ("serial", "etiqueta"):
            v = (it.get(k) or "").strip().upper()
            if v:
                chaves.add(v)
    if not chaves:
        return resumo

    cfg = db.ler_config()
    with SessionLocal() as s:
        abertas = s.execute(
            select(Coleta).where(Coleta.estado.notin_(ESTADOS_FINAIS))
        ).scalars().all()
        for c in abertas:
            esperados = s.execute(
                select(Esperado).where(Esperado.coleta_id == c.id)).scalars().all()
            recebidos = s.execute(
                select(Recebido).where(Recebido.coleta_id == c.id)).scalars().all()
            ja = {r.serial for r in recebidos}
            mexeu = False
            for chave in sorted(chaves):
                if chave in ja:
                    continue
                e = _casar(esperados, recebidos, chave)
                if e is None:
                    continue
                r = Recebido(coleta_id=c.id, esperado_id=e.id, serial=chave,
                             etiqueta=e.etiqueta, origem=ORIGEM_RECEBIMENTO,
                             recebido_por=usuario)
                s.add(r)
                recebidos.append(r)
                ja.add(chave)
                # Uma série só chega uma vez: sai do conjunto para não
                # casar também na próxima coleta que a esperava.
                chaves.discard(chave)
                resumo["casados"] += 1
                mexeu = True
            if mexeu:
                _chegou(c, usuario, cfg)
                resumo["coletas"].append(c.numero)
        s.commit()
    if resumo["casados"]:
        _log.info("reversa: recebimento de %s casou %d unidade(s) com %s",
                  usuario, resumo["casados"], ", ".join(resumo["coletas"]))
    return resumo


# ══════════════════════════════════════════════════════════════════
#  Configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "reversa", "admin")
    return {"config": db.ler_config(), "padroes": db.PADROES}


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "reversa", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("reversa: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return api_config(req)
