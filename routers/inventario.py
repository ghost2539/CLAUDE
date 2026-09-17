"""Inventário e Contagem (A18) — o retrato contra a prateleira.

O token é o ciclo. Abre com um recorte (um corredor, uma prateleira, o
depósito inteiro), tira o retrato do que o ServiceNow diz estar lá e
congela. Depois alguém conta, bipe a bipe. No fim, o que o retrato tinha
e a contagem não achou é **faltante**; o que a contagem achou e o retrato
não tinha é **sobra** — e a sobra ainda se divide: se o ServiceNow diz
que a série está em outro corredor do mesmo depósito é **local errado**;
em qualquer outra situação (em uso numa loja, inexistente) é
**inesperado**.

Cada diferença vira um token de regularização (A19). O ciclo fecha como
CONFERIDO ou DIVERGENTE e pronto: o inventário mede a divergência
encontrada; quem mede a resolvida é a regularização.

O retrato é lido com a conta de serviço ou com a sessão de quem está
logado — é leitura. Nada aqui escreve no ServiceNow: corrigir cadastro é
resolução de divergência, e acontece lá, com nome e prazo.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.inventario as db
import db.trilha as dbt
from db.inventario import (
    Ciclo, Esperado, Contado, SessionLocal, ROTULO_ESTADO,
    AG_CONTAGEM, EX_CONTAGEM, CONFERIDO, DIVERGENTE, CANCELADO, ESTADOS_FINAIS,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (
    Calendario, duracao_util, prazo_util, mover, encerrar, abrir_ativo,
    TrilhaInvalida, _utc,
)

_log = logging.getLogger("inventario")

router = APIRouter(prefix="/api/inventario", tags=["Inventário"])

_CAMPOS = ("sys_id,serial_number,asset_tag,model,install_status,substatus,"
           "aisle_space_location,stockroom,location")


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


# ══════════════════════════════════════════════════════════════════
#  Núcleo
# ══════════════════════════════════════════════════════════════════

def _abrir_token(c: Ciclo, usuario: str) -> int | None:
    try:
        with dbt.SessionLocal() as st:
            ativo = abrir_ativo(st, serial=c.numero, usuario=usuario,
                                tipo_equipamento="ciclo_inventario", origem="A18")
            mover(st, ativo, estado=AG_CONTAGEM, tipo=dbt.FILA, processo="A18",
                  usuario=usuario, detalhe=f'{{"prefixo": "{c.prefixo}"}}')
            st.commit()
            return ativo.id
    except TrilhaInvalida as exc:
        _log.error("inventario: token %s já existe: %s", c.numero, exc)
    except Exception as exc:  # noqa: BLE001
        _log.error("inventario: %s aberto sem medição: %s", c.numero, exc)
    return None


def _mover_token(c: Ciclo, estado: str, usuario: str) -> None:
    if not c.trilha_ativo_id:
        return
    try:
        with dbt.SessionLocal() as st:
            ativo = st.get(dbt.Ativo, c.trilha_ativo_id)
            if ativo is None:
                return
            if estado in ESTADOS_FINAIS:
                encerrar(st, ativo, estado=estado, processo="A18", usuario=usuario)
            else:
                mover(st, ativo, estado=estado,
                      tipo=dbt.TRATATIVA if estado == EX_CONTAGEM else dbt.FILA,
                      processo="A18", usuario=usuario)
            st.commit()
    except Exception as exc:  # noqa: BLE001
        _log.error("inventario: %s passou a %s sem registrar tempo: %s",
                   c.numero, estado, exc)


# ══════════════════════════════════════════════════════════════════
#  O retrato
# ══════════════════════════════════════════════════════════════════

def _texto(v) -> str:
    """JSONv2 com displayvalue devolve string; sem, pode vir dict."""
    if isinstance(v, dict):
        return str(v.get("display_value") or v.get("value") or "").strip()
    return str(v or "").strip()


def retrato(req: Request, prefixo: str, cfg: dict[str, str]) -> tuple[str, list[dict]]:
    """Lê do ServiceNow o que está em estoque no recorte.

    O status vai na consulta; o corredor é filtrado em Python. O JSONv2
    aceita STARTSWITH sem reclamar e às vezes o ignora — e um retrato
    com corredor a mais viraria uma enxurrada de "faltantes" falsos.
    """
    from routers.servicenow import _sn_session_from_portal, _sn_query_all, HARDWARE_TABLE
    status = (cfg.get("status_estoque") or "6").strip()
    campo = (cfg.get("campo_local") or "aisle_space_location").strip()
    filtro = f"install_status={status}"
    prefixo = (prefixo or "").strip().upper()
    linhas = _sn_query_all(_sn_session_from_portal(req), HARDWARE_TABLE, filtro,
                           _CAMPOS, page_size=500, max_records=50000)
    itens = []
    for r in linhas:
        local = _texto(r.get(campo)).upper()
        if prefixo and not local.startswith(prefixo):
            continue
        itens.append({
            "sys_id": _texto(r.get("sys_id")),
            "serial": _texto(r.get("serial_number")).upper(),
            "etiqueta": _texto(r.get("asset_tag")).upper(),
            "modelo": _texto(r.get("model")),
            "local": local,
            "substatus": _texto(r.get("substatus")),
        })
    return (filtro + (f" · {campo} começa com {prefixo}" if prefixo else ""), itens)


def situacao_no_sistema(req: Request, serial: str, cfg: dict[str, str]) -> dict:
    """O que o ServiceNow diz de uma série que não estava no retrato."""
    from routers.servicenow import (
        _sn_session_from_portal, _sn_query, HARDWARE_TABLE, INSTALL_STATUS_MAP,
        termo_sn,
    )
    serial = termo_sn(serial, "série")
    campo = (cfg.get("campo_local") or "aisle_space_location").strip()
    achados = _sn_query(_sn_session_from_portal(req), HARDWARE_TABLE,
                        f"serial_number={serial}^ORasset_tag={serial}", _CAMPOS, limit=1)
    if not achados:
        return {"existe": False, "texto": "não existe no ServiceNow", "local": "",
                "em_estoque": False}
    r = achados[0]
    status = _texto(r.get("install_status"))
    nomes = {v: k for k, v in INSTALL_STATUS_MAP.items()} if isinstance(INSTALL_STATUS_MAP, dict) else {}
    local = _texto(r.get(campo)).upper()
    loja = _texto(r.get("location"))
    em_estoque = status == (cfg.get("status_estoque") or "6").strip() or status.lower() in ("in stock", "em estoque")
    if em_estoque:
        texto = f"em estoque em {local or 'sem corredor'}"
    else:
        texto = f"{nomes.get(status, status) or 'situação desconhecida'}" + (f" — {loja}" if loja else "")
    return {"existe": True, "texto": texto, "local": local, "em_estoque": em_estoque,
            "modelo": _texto(r.get("model"))}


# ══════════════════════════════════════════════════════════════════
#  Leitura
# ══════════════════════════════════════════════════════════════════

def _buscar(s, numero: str) -> Ciclo:
    c = s.execute(
        select(Ciclo).where(Ciclo.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if c is None:
        raise HTTPException(404, f"Ciclo {numero} não encontrado.")
    return c


def _comparar(esperados: list[Esperado], contados: list[Contado]) -> dict:
    por_esperado = {k.esperado_id: k for k in contados if k.esperado_id}
    ok, faltantes = [], []
    for e in esperados:
        linha = {"id": e.id, "serial": e.serial, "etiqueta": e.etiqueta,
                 "modelo": e.modelo, "local": e.local, "substatus": e.substatus}
        k = por_esperado.get(e.id)
        if k is not None:
            linha.update({"contado_em": _utc(k.contado_em).isoformat(),
                          "contado_por": k.contado_por, "local_contado": k.local})
            ok.append(linha)
        else:
            faltantes.append(linha)
    sobras = [{"serial": k.serial, "local": k.local, "situacao": k.situacao_sistema,
               "contado_por": k.contado_por, "contado_em": _utc(k.contado_em).isoformat()}
              for k in contados if not k.esperado_id]
    por_local: dict[str, dict] = {}
    for e in esperados:
        alvo = por_local.setdefault(e.local or "(sem corredor)", {"local": e.local or "(sem corredor)",
                                                                  "esperados": 0, "contados": 0})
        alvo["esperados"] += 1
        if e.id in por_esperado:
            alvo["contados"] += 1
    return {"ok": ok, "faltantes": faltantes, "sobras": sobras,
            "esperados": len(esperados), "contados": len(contados),
            "por_local": sorted(por_local.values(), key=lambda x: x["local"]),
            "divergente": bool(faltantes or sobras)}


def _prazo(c: Ciclo, cal: Calendario) -> datetime | None:
    try:
        dias = float(db.ler_config().get("prazo_contagem_dias") or 0)
    except ValueError:
        dias = 0
    return prazo_util(_utc(c.aberto_em), dias, cal) if dias > 0 else None


def _resumo(c: Ciclo, cal: Calendario, agora: datetime) -> dict:
    fim = _utc(c.encerrado_em) if c.estado in ESTADOS_FINAIS else None
    prazo = _prazo(c, cal)
    return {
        "prazo": prazo.isoformat() if prazo else None,
        "atrasado": bool(prazo and c.estado not in ESTADOS_FINAIS and prazo < agora),
        "numero": c.numero, "descricao": c.descricao, "prefixo": c.prefixo,
        "filtro": c.filtro,
        "estado": c.estado, "estado_rotulo": ROTULO_ESTADO.get(c.estado, c.estado),
        "encerrado": c.estado in ESTADOS_FINAIS,
        "esperados": c.esperados,
        "aberto_por": c.aberto_por, "aberto_em": _utc(c.aberto_em).isoformat(),
        "contado_por": c.contado_por,
        "iniciado_em": _utc(c.iniciado_em).isoformat() if c.iniciado_em else None,
        "encerrado_em": _utc(c.encerrado_em).isoformat() if c.encerrado_em else None,
        "observacao": c.observacao,
        "segundos_uteis": duracao_util(_utc(c.aberto_em), fim, cal, agora),
        "comparacao": None,
    }


def _detalhe(s, c: Ciclo) -> dict:
    cal, agora = _calendario(), dbt.utcnow()
    d = _resumo(c, cal, agora)
    esperados = s.execute(select(Esperado).where(Esperado.ciclo_id == c.id)
                          .order_by(Esperado.local, Esperado.serial)).scalars().all()
    contados = s.execute(select(Contado).where(Contado.ciclo_id == c.id)
                         .order_by(Contado.id)).scalars().all()
    d["comparacao"] = _comparar(esperados, contados)
    return d


@router.get("/ciclos")
def api_lista(req: Request, estado: str = ""):
    require_permission(req, "inventario", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = select(Ciclo).order_by(Ciclo.aberto_em.desc())
        if estado:
            q = q.where(Ciclo.estado == estado)
        ciclos = []
        for c in s.execute(q.limit(200)).scalars().all():
            d = _resumo(c, cal, agora)
            contados = s.execute(select(Contado).where(Contado.ciclo_id == c.id)).scalars().all()
            d["contados"] = len(contados)
            d["casados"] = sum(1 for k in contados if k.esperado_id)
            d["sobras"] = sum(1 for k in contados if not k.esperado_id)
            d["faltantes"] = (c.esperados - d["casados"]) if c.estado in (DIVERGENTE, CONFERIDO) else None
            ciclos.append(d)
        contagem = {e: 0 for e in ROTULO_ESTADO}
        for c in s.execute(select(Ciclo)).scalars():
            contagem[c.estado] = contagem.get(c.estado, 0) + 1
    return {"ciclos": ciclos, "contagem": contagem}


@router.get("/ciclos/{numero}")
def api_detalhe(numero: str, req: Request):
    require_permission(req, "inventario", "view")
    with SessionLocal() as s:
        return _detalhe(s, _buscar(s, numero))


@router.get("/previa")
def api_previa(req: Request, prefixo: str = ""):
    """Quantos itens o recorte tem, antes de abrir o ciclo."""
    require_permission(req, "inventario", "view")
    check_rate_limit(req)
    filtro, itens = retrato(req, prefixo, db.ler_config())
    por_local: dict[str, int] = {}
    for i in itens:
        por_local[i["local"] or "(sem corredor)"] = por_local.get(i["local"] or "(sem corredor)", 0) + 1
    return {"filtro": filtro, "total": len(itens),
            "por_local": [{"local": k, "quantidade": v} for k, v in sorted(por_local.items())][:200]}


# ══════════════════════════════════════════════════════════════════
#  Escrita
# ══════════════════════════════════════════════════════════════════

class CicloIn(BaseModel):
    descricao: str = ""
    prefixo: str = ""


@router.post("/ciclos")
def api_criar(body: CicloIn, req: Request):
    """Abre o ciclo e tira o retrato. A partir daqui o alvo não se move."""
    sd = require_permission(req, "inventario", "create")
    check_rate_limit(req)
    usuario = sd.get("username", "")
    cfg = db.ler_config()
    prefixo = (body.prefixo or "").strip().upper()

    with SessionLocal() as s:
        ja = s.execute(select(Ciclo).where(Ciclo.prefixo == prefixo,
                                           Ciclo.estado.notin_(ESTADOS_FINAIS))).scalar_one_or_none()
        if ja is not None:
            raise HTTPException(409, f"Já existe o ciclo {ja.numero} aberto para este recorte.")

    filtro, itens = retrato(req, prefixo, cfg)
    if not itens:
        raise HTTPException(409, "O recorte está vazio no ServiceNow — nada para contar.")

    with SessionLocal() as s:
        c = Ciclo(numero=db.proximo_numero(s),
                  descricao=(body.descricao or "").strip() or (f"Corredor {prefixo}" if prefixo else "Depósito inteiro"),
                  prefixo=prefixo, filtro=filtro, esperados=len(itens),
                  aberto_por=usuario, aberto_em=db.utcnow())
        s.add(c)
        s.flush()
        s.add_all(Esperado(ciclo_id=c.id, **i) for i in itens)
        c.trilha_ativo_id = _abrir_token(c, usuario)
        s.commit()
        numero = c.numero
    _log.info("inventario: %s aberto por %s (%s, %d esperados)", numero, usuario, prefixo or "tudo", len(itens))
    return api_detalhe(numero, req)


@router.post("/ciclos/{numero}/iniciar")
def api_iniciar(numero: str, req: Request):
    sd = require_permission(req, "inventario", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado != AG_CONTAGEM:
            raise HTTPException(409, f"O ciclo está em '{ROTULO_ESTADO.get(c.estado)}'.")
        c.estado = EX_CONTAGEM
        c.contado_por = usuario
        c.iniciado_em = db.utcnow()
        _mover_token(c, EX_CONTAGEM, usuario)
        s.commit()
    return api_detalhe(numero, req)


class BipeIn(BaseModel):
    serial: str
    local: str = ""


@router.post("/ciclos/{numero}/bipar")
def api_bipar(numero: str, body: BipeIn, req: Request):
    """Uma leitura. Casa por série ou etiqueta; o que não casa é sobra.

    A sobra consulta o ServiceNow na hora para gravar o que ele diz da
    série — é essa informação que decide, no fechamento, se a sobra é
    local errado ou inesperado, e é o que quem regularizar vai ler.
    """
    sd = require_permission(req, "inventario", "edit")
    usuario = sd.get("username", "")
    serial = (body.serial or "").strip().upper()
    if not serial:
        raise HTTPException(400, "Bipe a série ou a etiqueta.")
    cfg = db.ler_config()
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado != EX_CONTAGEM:
            raise HTTPException(409, "Inicie a contagem antes de bipar.")
        if s.execute(select(Contado).where(Contado.ciclo_id == c.id,
                                           Contado.serial == serial)).scalar_one_or_none():
            raise HTTPException(409, f"{serial} já foi contado neste ciclo.")
        e = s.execute(select(Esperado).where(
            Esperado.ciclo_id == c.id,
            (Esperado.serial == serial) | (Esperado.etiqueta == serial))).scalars().first()
        situacao = ""
        if e is None:
            try:
                situacao = situacao_no_sistema(req, serial, cfg)["texto"]
            except HTTPException as exc:
                situacao = f"não consultado ({exc.detail})"
        s.add(Contado(ciclo_id=c.id, esperado_id=e.id if e else None, serial=serial,
                      local=(body.local or "").strip().upper(), situacao_sistema=situacao,
                      contado_por=usuario))
        s.commit()
        casou = e is not None
    return {**api_detalhe(numero, req), "casou": casou, "situacao": situacao}


class ConclusaoIn(BaseModel):
    observacao: str = ""


@router.post("/ciclos/{numero}/concluir")
def api_concluir(numero: str, body: ConclusaoIn, req: Request):
    sd = require_permission(req, "inventario", "edit")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado != EX_CONTAGEM:
            raise HTTPException(409, "Só uma contagem em andamento pode ser concluída.")
        d = _detalhe(s, c)
        cmp_ = d["comparacao"]
        if cmp_["divergente"] and not (body.observacao or "").strip():
            raise HTTPException(
                400, f"Faltam {len(cmp_['faltantes'])} e sobram {len(cmp_['sobras'])} — "
                     "descreva a contagem antes de fechar.")
        if body.observacao.strip():
            c.observacao = f"{c.observacao}\n{body.observacao.strip()}".strip()
        c.encerrado_em = db.utcnow()
        c.estado = DIVERGENTE if cmp_["divergente"] else CONFERIDO
        _mover_token(c, c.estado, usuario)
        s.commit()
        numero_c, prefixo, divergente = c.numero, c.prefixo, cmp_["divergente"]
        faltantes, sobras = cmp_["faltantes"], cmp_["sobras"]
    _log.info("inventario: %s concluído por %s como %s (%d faltante(s), %d sobra(s))",
              numero_c, usuario, "DIVERGENTE" if divergente else "CONFERIDO",
              len(faltantes), len(sobras))
    if divergente and (db.ler_config().get("abrir_regularizacao") or "1") == "1":
        _abrir_regularizacao(numero_c, prefixo, faltantes, sobras, usuario)
    return api_detalhe(numero, req)


def _abrir_regularizacao(numero: str, prefixo: str, faltantes: list[dict],
                         sobras: list[dict], usuario: str) -> None:
    """Cada diferença vira token de regularização. Falha não desfaz o ciclo."""
    try:
        from routers.regularizacao import abrir_divergencia
    except Exception as exc:  # noqa: BLE001
        _log.warning("inventario: %s divergente, regularização não disponível: %s", numero, exc)
        return
    for f in faltantes:
        try:
            abrir_divergencia(origem="A18", referencia=numero, tipo="FALTANTE", usuario=usuario,
                              serial=f["serial"], etiqueta=f["etiqueta"], modelo=f["modelo"],
                              local_sistema=f["local"],
                              descricao=f"Retrato dizia em {f['local'] or 'estoque'}; contagem não achou.")
        except Exception as exc:  # noqa: BLE001
            _log.error("inventario: %s faltante %s sem regularização: %s", numero, f["serial"], exc)
    for sb in sobras:
        # "em estoque em X" é o mesmo depósito, outro corredor: local errado.
        tipo = "LOCAL_ERRADO" if (sb.get("situacao") or "").startswith("em estoque") else "INESPERADO"
        try:
            abrir_divergencia(origem="A18", referencia=numero, tipo=tipo, usuario=usuario,
                              serial=sb["serial"], local_fisico=sb["local"] or prefixo,
                              local_sistema=sb.get("situacao", ""),
                              descricao=f"Contado em {sb['local'] or prefixo or 'estoque'}; "
                                        f"ServiceNow: {sb.get('situacao') or 'sem informação'}.")
        except Exception as exc:  # noqa: BLE001
            _log.error("inventario: %s sobra %s sem regularização: %s", numero, sb["serial"], exc)


class CancelaIn(BaseModel):
    motivo: str


@router.post("/ciclos/{numero}/cancelar")
def api_cancelar(numero: str, body: CancelaIn, req: Request):
    sd = require_permission(req, "inventario", "edit")
    if not (body.motivo or "").strip():
        raise HTTPException(400, "Informe o motivo.")
    usuario = sd.get("username", "")
    with SessionLocal() as s:
        c = _buscar(s, numero)
        if c.estado in ESTADOS_FINAIS:
            raise HTTPException(409, "O ciclo já está encerrado.")
        c.observacao = f"{c.observacao}\n[cancelado] {body.motivo.strip()}".strip()
        c.encerrado_em = db.utcnow()
        c.estado = CANCELADO
        _mover_token(c, CANCELADO, usuario)
        s.commit()
    return api_detalhe(numero, req)


# ══════════════════════════════════════════════════════════════════
#  Configuração
# ══════════════════════════════════════════════════════════════════

@router.get("/config")
def api_config(req: Request):
    require_permission(req, "inventario", "admin")
    return {"config": db.ler_config(), "padroes": db.PADROES}


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    sd = require_permission(req, "inventario", "admin")
    validos = set(db.PADROES)
    pares = {k: str(v) for k, v in (body or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    _log.info("inventario: parâmetros alterados por %s: %s", sd.get("username", "?"), ", ".join(pares))
    return api_config(req)
