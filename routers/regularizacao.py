"""Regularização de Ativo (A19) — a divergência com dono e prazo.

Todo processo da área produz divergência. O que este módulo faz é
impedir que ela vire linha de relatório: a divergência é um token, não
sai da fila sem **responsável e prazo**, e não fecha sem dizer **como**
foi resolvida. É esse par de campos que dá autoridade para cobrar — sem
eles, "faltou um coletor" é conversa de corredor.

```
AG_TRATATIVA_REG → EX_TRATATIVA_REG → RESOLVIDA_REG
     (fila)        (responsável+prazo)      ↓
                                       CANCELADA_REG
```

Quem abre é outro processo (`abrir_divergencia`, chamado pela reversa e
pelo inventário por import tardio) ou uma pessoa, à mão. Nos dois casos
a divergência nasce sem dono: assumir é um ato, e é o ato que se mede.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.regularizacao as db
import db.trilha as dbt
from db.regularizacao import (
    Divergencia, SessionLocal, ORIGENS, TIPOS, ROTULO_TIPO, ROTULO_ESTADO,
    RESOLUCOES, AG_TRATATIVA, EX_TRATATIVA, RESOLVIDA, CANCELADA, ESTADOS_FINAIS,
)
from core.security import require_permission, check_rate_limit, get_session
from routers.trilha import (
    Calendario, duracao_util, prazo_util, mover, encerrar, abrir_ativo,
    TrilhaInvalida, _utc,
)

_log = logging.getLogger("regularizacao")

router = APIRouter(prefix="/api/regularizacao", tags=["Regularização"])


# ══════════════════════════════════════════════════════════════════
#  Núcleo
# ══════════════════════════════════════════════════════════════════

def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


def _abrir_token(d: Divergencia, usuario: str) -> int | None:
    try:
        with dbt.SessionLocal() as st:
            ativo = abrir_ativo(st, serial=d.numero, usuario=usuario,
                                modelo=d.modelo, tipo_equipamento="divergencia",
                                origem=d.origem)
            mover(st, ativo, estado=AG_TRATATIVA, tipo=dbt.FILA, processo="A19",
                  usuario=usuario, detalhe=f'{{"referencia": "{d.referencia}"}}')
            st.commit()
            return ativo.id
    except TrilhaInvalida as exc:
        _log.error("regularizacao: token %s já existe: %s", d.numero, exc)
    except Exception as exc:  # noqa: BLE001
        _log.error("regularizacao: %s aberta sem medição: %s", d.numero, exc)
    return None


def _mover_token(d: Divergencia, estado: str, usuario: str) -> None:
    if not d.trilha_ativo_id:
        return
    try:
        with dbt.SessionLocal() as st:
            ativo = st.get(dbt.Ativo, d.trilha_ativo_id)
            if ativo is None:
                return
            if estado in ESTADOS_FINAIS:
                encerrar(st, ativo, estado=estado, processo="A19", usuario=usuario)
            else:
                # Em tratativa é TRATATIVA: tem dono, e o tempo é dele.
                mover(st, ativo, estado=estado,
                      tipo=dbt.TRATATIVA if estado == EX_TRATATIVA else dbt.FILA,
                      processo="A19", usuario=usuario)
            st.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("regularizacao: %s passou a %s sem registrar tempo: %s",
                   d.numero, estado, exc)


def _anotar(d: Divergencia, usuario: str, texto: str) -> None:
    carimbo = db.utcnow().strftime("%Y-%m-%d %H:%M")
    d.historico = f"{d.historico}\n[{carimbo} {usuario}] {texto}".strip()


# ══════════════════════════════════════════════════════════════════
#  Porta de entrada dos outros processos
# ══════════════════════════════════════════════════════════════════

def abrir_divergencia(*, origem: str, referencia: str, tipo: str, usuario: str,
                      serial: str = "", etiqueta: str = "", modelo: str = "",
                      loja: str = "", local_sistema: str = "",
                      local_fisico: str = "", descricao: str = "") -> str | None:
    """Abre uma divergência a partir de outro processo. Devolve o número.

    Idempotente por (origem, referência, série, tipo): o processo que
    reprocessa não abre a mesma divergência duas vezes. Devolve None
    quando já existia.
    """
    origem = (origem or "MANUAL").upper()
    tipo = (tipo or "").upper()
    if tipo not in TIPOS:
        raise ValueError(f"Tipo de divergência inválido: {tipo}")
    serial = (serial or "").strip().upper()
    etiqueta = (etiqueta or "").strip().upper()
    with SessionLocal() as s:
        if serial or etiqueta:
            ja = s.execute(
                select(Divergencia).where(
                    Divergencia.origem == origem,
                    Divergencia.referencia == (referencia or ""),
                    Divergencia.tipo == tipo,
                    Divergencia.serial == serial,
                    Divergencia.etiqueta == etiqueta,
                    Divergencia.estado.notin_(ESTADOS_FINAIS))
            ).scalar_one_or_none()
            if ja is not None:
                return None
        d = Divergencia(
            numero=db.proximo_numero(s), origem=origem,
            referencia=(referencia or "").strip().upper(), tipo=tipo,
            serial=serial, etiqueta=etiqueta, modelo=(modelo or "").strip(),
            loja=(loja or "").strip(), local_sistema=(local_sistema or "").strip(),
            local_fisico=(local_fisico or "").strip(),
            descricao=(descricao or "").strip(),
            aberta_por=usuario, aberta_em=db.utcnow(),
        )
        s.add(d)
        s.flush()
        _anotar(d, usuario, f"aberta por {ORIGENS.get(origem, origem)} ({d.referencia or 'sem referência'})")
        d.trilha_ativo_id = _abrir_token(d, usuario)
        s.commit()
        numero = d.numero
    _log.info("regularizacao: %s aberta (%s %s %s)", numero, origem, tipo,
              serial or etiqueta or modelo)
    return numero


# ══════════════════════════════════════════════════════════════════
#  Leitura
# ══════════════════════════════════════════════════════════════════

def _buscar(s, numero: str) -> Divergencia:
    d = s.execute(
        select(Divergencia).where(Divergencia.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, f"Divergência {numero} não encontrada.")
    return d


def _resumo(d: Divergencia, cal: Calendario, agora: datetime,
            limite_sem_dono: int) -> dict:
    fim = _utc(d.encerrada_em) if d.estado in ESTADOS_FINAIS else None
    seg = duracao_util(_utc(d.aberta_em), fim, cal, agora)
    return {
        "numero": d.numero,
        "origem": d.origem,
        "origem_rotulo": ORIGENS.get(d.origem, d.origem),
        "referencia": d.referencia,
        "tipo": d.tipo,
        "tipo_rotulo": ROTULO_TIPO.get(d.tipo, d.tipo),
        "serial": d.serial, "etiqueta": d.etiqueta, "modelo": d.modelo,
        "loja": d.loja,
        "local_sistema": d.local_sistema, "local_fisico": d.local_fisico,
        "descricao": d.descricao,
        "estado": d.estado,
        "estado_rotulo": ROTULO_ESTADO.get(d.estado, d.estado),
        "encerrada": d.estado in ESTADOS_FINAIS,
        "responsavel": d.responsavel,
        "prazo": _utc(d.prazo).isoformat() if d.prazo else None,
        "atrasada": bool(d.prazo and d.estado == EX_TRATATIVA and _utc(d.prazo) < agora),
        # Sem dono há tempo demais: é o alerta da fila, e é do gestor.
        "sem_dono_alerta": d.estado == AG_TRATATIVA and seg > limite_sem_dono,
        "resolucao": d.resolucao,
        "resolucao_rotulo": RESOLUCOES.get(d.resolucao, d.resolucao),
        "resolucao_detalhe": d.resolucao_detalhe,
        "historico": d.historico,
        "aberta_por": d.aberta_por,
        "aberta_em": _utc(d.aberta_em).isoformat(),
        "assumida_em": _utc(d.assumida_em).isoformat() if d.assumida_em else None,
        "encerrada_em": _utc(d.encerrada_em).isoformat() if d.encerrada_em else None,
        "segundos_uteis": seg,
    }


def _limite_sem_dono(cfg: dict[str, str], cal: Calendario) -> int:
    try:
        dias = float(cfg.get("alerta_sem_dono_dias") or 0)
    except ValueError:
        dias = 0
    if dias <= 0:
        return 10 ** 9
    if cal.corrido:
        return int(dias * 86400)
    jornada = (datetime.combine(date.today(), cal.fim)
               - datetime.combine(date.today(), cal.inicio)).total_seconds()
    return int(dias * max(jornada, 3600))


@router.get("/divergencias")
def api_lista(req: Request, estado: str = "", origem: str = "", tipo: str = "",
              responsavel: str = "", minhas: int = 0):
    sd = require_permission(req, "regularizacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    limite = _limite_sem_dono(db.ler_config(), cal)
    with SessionLocal() as s:
        q = select(Divergencia).order_by(
            Divergencia.prazo.is_(None), Divergencia.prazo, Divergencia.aberta_em)
        if estado:
            q = q.where(Divergencia.estado == estado)
        if origem:
            q = q.where(Divergencia.origem == origem.upper())
        if tipo:
            q = q.where(Divergencia.tipo == tipo.upper())
        if minhas:
            q = q.where(Divergencia.responsavel == sd.get("username", ""))
        elif responsavel:
            q = q.where(Divergencia.responsavel == responsavel)
        linhas = s.execute(q.limit(500)).scalars().all()
        divergencias = [_resumo(d, cal, agora, limite) for d in linhas]
        contagem = {e: 0 for e in ROTULO_ESTADO}
        por_origem: dict[str, int] = {}
        for d in s.execute(select(Divergencia)).scalars():
            contagem[d.estado] = contagem.get(d.estado, 0) + 1
            if d.estado not in ESTADOS_FINAIS:
                por_origem[d.origem] = por_origem.get(d.origem, 0) + 1
    return {
        "divergencias": divergencias,
        "contagem": contagem,
        "por_origem": [{"origem": k, "rotulo": ORIGENS.get(k, k), "quantidade": v}
                       for k, v in sorted(por_origem.items(), key=lambda x: -x[1])],
        "atrasadas": sum(1 for d in divergencias if d["atrasada"]),
        "sem_dono": sum(1 for d in divergencias if d["sem_dono_alerta"]),
        "rotulos": {"tipos": ROTULO_TIPO, "estados": ROTULO_ESTADO,
                    "resolucoes": RESOLUCOES, "origens": ORIGENS},
    }


@router.get("/divergencias/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "regularizacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    limite = _limite_sem_dono(db.ler_config(), cal)
    with SessionLocal() as s:
        return _resumo(_buscar(s, numero), cal, agora, limite)


@router.get("/responsaveis")
def api_responsaveis(req: Request):
    """Quem pode ser dono de uma divergência: usuários ativos do portal.

    Lê a base de usuários só para nome e login — nada de senha, nada de
    permissão. É o mínimo para um seletor.
    """
    require_permission(req, "regularizacao", "view")
    from db.portal import SessionLocal as PortalSession, User
    with PortalSession() as s:
        usuarios = s.execute(
            select(User.login, User.display_name)
            .where(User.active == True)  # noqa: E712
            .order_by(User.display_name, User.login)
        ).all()
    return {"responsaveis": [{"login": u[0], "nome": u[1] or u[0]} for u in usuarios]}


# ══════════════════════════════════════════════════════════════════
#  Escrita
# ══════════════════════════════════════════════════════════════════

class DivergenciaIn(BaseModel):
    tipo: str
    serial: str = ""
    etiqueta: str = ""
    modelo: str = ""
    loja: str = ""
    local_sistema: str = ""
    local_fisico: str = ""
    descricao: str = ""
    referencia: str = ""


@router.post("/divergencias")
def api_criar(body: DivergenciaIn, req: Request):
    """Abre à mão. Pede descrição: quem abre sabe o que viu."""
    sd = require_permission(req, "regularizacao", "create")
    check_rate_limit(req)
    if body.tipo.upper() not in TIPOS:
        raise HTTPException(400, "Tipo de divergência inválido.")
    if not (body.serial or body.etiqueta or body.modelo).strip():
        raise HTTPException(400, "Informe série, etiqueta ou modelo.")
    if not (body.descricao or "").strip():
        raise HTTPException(400, "Descreva a divergência.")
    numero = abrir_divergencia(
        origem="MANUAL", referencia=body.referencia, tipo=body.tipo,
        usuario=sd.get("username", ""), serial=body.serial, etiqueta=body.etiqueta,
        modelo=body.modelo, loja=body.loja, local_sistema=body.local_sistema,
        local_fisico=body.local_fisico, descricao=body.descricao)
    if numero is None:
        raise HTTPException(409, "Já existe divergência aberta para esta série.")
    return api_detalhe(numero, req)


class AssumirIn(BaseModel):
    responsavel: str = ""
    prazo: date | None = None
    nota: str = ""


@router.post("/divergencias/{numero}/assumir")
def api_assumir(numero: str, body: AssumirIn, req: Request):
    """Dá dono e prazo. Sem os dois, não sai da fila.

    Atribuir a outra pessoa exige permissão de administrar: distribuir
    trabalho é papel do gestor. Assumir para si, qualquer um que edita.
    """
    sd = require_permission(req, "regularizacao", "edit")
    eu = sd.get("username", "")
    responsavel = (body.responsavel or eu).strip()
    if responsavel != eu:
        require_permission(req, "regularizacao", "admin")

    cfg = db.ler_config()
    agora = db.utcnow()
    if body.prazo is not None:
        prazo = datetime.combine(body.prazo, datetime.max.time().replace(microsecond=0),
                                 tzinfo=agora.tzinfo)
        if prazo < agora:
            raise HTTPException(400, "O prazo não pode ser no passado.")
    else:
        try:
            dias = float(cfg.get("prazo_padrao_dias") or 0)
        except ValueError:
            dias = 0
        if dias <= 0:
            raise HTTPException(400, "Informe o prazo.")
        prazo = prazo_util(agora, dias, _calendario())

    with SessionLocal() as s:
        d = _buscar(s, numero)
        if d.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "A divergência já está encerrada.")
        primeira = d.estado == AG_TRATATIVA
        anterior = d.responsavel
        d.responsavel = responsavel
        d.prazo = prazo
        d.assumida_em = d.assumida_em or agora
        _anotar(d, eu, (f"assumida por {responsavel}" if primeira
                        else f"reatribuída a {responsavel}")
                + f", prazo {prazo.date().isoformat()}"
                + (f" — {body.nota.strip()}" if body.nota.strip() else ""))
        if primeira:
            d.estado = EX_TRATATIVA
            _mover_token(d, EX_TRATATIVA, responsavel)
        elif anterior != responsavel:
            # Troca de dono no meio: fecha o relógio de um, abre o do
            # outro, no mesmo estado. É uma sessão nova do mesmo estado.
            _mover_token(d, EX_TRATATIVA, responsavel)
        s.commit()
    return api_detalhe(numero, req)


class NotaIn(BaseModel):
    nota: str


@router.post("/divergencias/{numero}/anotar")
def api_anotar(numero: str, body: NotaIn, req: Request):
    sd = require_permission(req, "regularizacao", "edit")
    if not (body.nota or "").strip():
        raise HTTPException(400, "Escreva a anotação.")
    with SessionLocal() as s:
        d = _buscar(s, numero)
        if d.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "A divergência já está encerrada.")
        _anotar(d, sd.get("username", ""), body.nota.strip())
        s.commit()
    return api_detalhe(numero, req)


class ResolverIn(BaseModel):
    resolucao: str
    detalhe: str = ""


@router.post("/divergencias/{numero}/resolver")
def api_resolver(numero: str, body: ResolverIn, req: Request):
    """Fecha dizendo como. Só o responsável ou quem administra."""
    sd = require_permission(req, "regularizacao", "edit")
    eu = sd.get("username", "")
    resolucao = (body.resolucao or "").upper().strip()
    if resolucao not in RESOLUCOES:
        raise HTTPException(400, "Informe como a divergência foi resolvida.")
    if resolucao == "PERDA" and not (body.detalhe or "").strip():
        # Perda é a resolução que a auditoria vai perguntar. Sem texto
        # não passa.
        raise HTTPException(400, "Perda reconhecida exige a justificativa.")
    with SessionLocal() as s:
        d = _buscar(s, numero)
        if d.estado != EX_TRATATIVA:
            raise HTTPException(409, "Só uma divergência em tratativa pode ser resolvida.")
        if d.responsavel != eu and not sd.get("is_admin"):
            require_permission(req, "regularizacao", "admin")
        d.resolucao = resolucao
        d.resolucao_detalhe = (body.detalhe or "").strip()
        d.encerrada_por = eu
        d.encerrada_em = db.utcnow()
        d.estado = RESOLVIDA
        _anotar(d, eu, f"resolvida: {RESOLUCOES[resolucao]}"
                + (f" — {d.resolucao_detalhe}" if d.resolucao_detalhe else ""))
        _mover_token(d, RESOLVIDA, eu)
        s.commit()
    _log.info("regularizacao: %s resolvida por %s como %s", numero.upper(), eu, resolucao)
    return api_detalhe(numero, req)


class CancelaIn(BaseModel):
    motivo: str


@router.post("/divergencias/{numero}/cancelar")
def api_cancelar(numero: str, body: CancelaIn, req: Request):
    """Cancelar é para divergência que não era divergência. Exige admin."""
    sd = require_permission(req, "regularizacao", "admin")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo.")
    eu = sd.get("username", "")
    with SessionLocal() as s:
        d = _buscar(s, numero)
        if d.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "A divergência já está encerrada.")
        d.encerrada_por = eu
        d.encerrada_em = db.utcnow()
        d.estado = CANCELADA
        _anotar(d, eu, f"cancelada: {body.motivo.strip()}")
        _mover_token(d, CANCELADA, eu)
        s.commit()
    return api_detalhe(numero, req)


# ══════════════════════════════════════════════════════════════════
#  Configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "regularizacao", "admin")
    return {"config": db.ler_config(), "padroes": db.PADROES}


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "regularizacao", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("regularizacao: parâmetros alterados por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return api_config(req)
