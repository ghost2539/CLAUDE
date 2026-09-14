"""Preparação (A06, A07, A08.2) — da bancada de volta ao estoque.

Três estações com a mesma mecânica de fila e bipe, e conteúdos
diferentes. Fecham os caminhos que a bancada abre: coletor apto vai
configurar, sled é montado, e tudo termina internalizado.

`DISPONIVEL` é o único estado sem relógio de ninguém: o equipamento
está no estoque esperando ser pedido, e esse tempo é do planejamento
(G01), não de uma pessoa.

**Ninguém assume equipamento aqui.** O ativo cai na fila da estação e o
relógio corre até alguém concluir a estação — concluir é a saída.

Na internalização quem dá o aceite é o ServiceNow, não a tela: o ativo
só fica disponível quando o cadastro mostrar o estoque do CD e um espaço
de internalização ou reparo. Enquanto não mostrar, ele continua na fila,
com o motivo visível.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

import db.preparacao as db
import db.trilha as dbt
from db.preparacao import (
    Passagem, Composicao, Baseline, SessionLocal,
    ESTACOES, ROTULO_ESTACAO, PROCESSO,
    FILA_DA_ESTACAO, TRATATIVA_DA_ESTACAO, SAIDA_DA_ESTACAO,
    CONFIGURACAO, MONTAGEM, INTERNALIZACAO,
)
from core.security import require_permission, check_rate_limit
from routers.trilha import (Calendario, duracao_util, mover, TrilhaInvalida,
                            garantir_ativo, _utc)

_ORIGEM_ADOCAO = "preparacao"
_log = logging.getLogger("preparacao")

router = APIRouter(prefix="/api/preparacao", tags=["Preparação"])


def _estacao_valida(estacao: str) -> str:
    if estacao not in ESTACOES:
        raise HTTPException(400, "Estação inválida.")
    return estacao


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


# ══════════════════════════════════════════════════════════════════
#  Fila
# ══════════════════════════════════════════════════════════════════

@router.get("/fila")
def api_fila(req: Request, estacao: str = CONFIGURACAO):
    require_permission(req, "preparacao", "view")
    estacao = _estacao_valida(estacao)
    cal, agora = _calendario(), dbt.utcnow()

    with dbt.SessionLocal() as s:
        linhas = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado.in_((FILA_DA_ESTACAO[estacao],
                                             TRATATIVA_DA_ESTACAO[estacao])))
            .order_by(dbt.Intervalo.inicio)
        ).all()

    # `em_curso` é resíduo do fluxo antigo, em que alguém assumia o
    # equipamento. Não se produz mais, mas quem ficou lá precisa aparecer.
    fila, curso = [], []
    for i, a in linhas:
        item = {
            "serial": a.serial, "modelo": a.modelo,
            "tipo_equipamento": a.tipo_equipamento, "origem": a.origem,
            "usuario": i.usuario,
            "segundos": duracao_util(_utc(i.inicio), None, cal, agora),
        }
        (fila if i.estado == FILA_DA_ESTACAO[estacao] else curso).append(item)

    return {
        "estacao": estacao, "rotulo": ROTULO_ESTACAO[estacao],
        "aguardando": fila, "em_curso": curso,
    }


@router.get("/baselines")
def api_baselines(req: Request):
    require_permission(req, "preparacao", "view")
    with SessionLocal() as s:
        linhas = s.execute(
            select(Baseline).order_by(Baseline.vigente.desc(), Baseline.versao.desc())
        ).scalars().all()
    return {"baselines": [{"id": b.id, "versao": b.versao,
                           "descricao": b.descricao, "vigente": b.vigente}
                          for b in linhas]}


# ══════════════════════════════════════════════════════════════════
#  Conclusão da estação — é ela que despacha o ativo
# ══════════════════════════════════════════════════════════════════

@router.get("/ativo/{serial}")
def api_ativo(serial: str, req: Request, estacao: str = CONFIGURACAO):
    """Traz o equipamento para a tela. Não move nada: quem move é a
    conclusão da estação."""
    require_permission(req, "preparacao", "view")
    estacao = _estacao_valida(estacao)
    serial = (serial or "").strip().upper()
    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
    if ativo is None:
        raise HTTPException(404, f"A série {serial} não está na trilha.")
    if ativo.estado_fisico not in (FILA_DA_ESTACAO[estacao],
                                   TRATATIVA_DA_ESTACAO[estacao]):
        raise HTTPException(
            409, f"A série {serial} não está na fila de "
                 f"{ROTULO_ESTACAO[estacao]} — está em "
                 f"{dbt.rotulo_estado(ativo.estado_fisico).lower()}.")
    return {"serial": ativo.serial, "modelo": ativo.modelo,
            "origem": ativo.origem, "tipo_equipamento": ativo.tipo_equipamento,
            "estado_atual": ativo.estado_fisico}


class ConclusaoIn(BaseModel):
    serial: str
    estacao: str = CONFIGURACAO
    # A06
    baseline_id: int | None = None
    firmware: str = ""
    teste_funcional: bool | None = None
    # A07
    carcaca: str = ""
    componentes: list[str] = []
    # A08.2
    conferencia_ok: bool | None = None
    endereco: str = ""
    observacao: str = ""


@router.post("/concluir")
def api_concluir(body: ConclusaoIn, req: Request):
    """Fecha a estação e manda o ativo adiante."""
    sd = require_permission(req, "preparacao", "edit")
    estacao = _estacao_valida(body.estacao)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")

    _exigencias(estacao, body)

    # Conferência reprovada devolve o equipamento para a bancada em vez
    # de seguir para o estoque. Internalizar o que não passou seria
    # transformar um problema conhecido em saldo disponível.
    devolve = (estacao == INTERNALIZACAO and body.conferencia_ok is False)
    proximo = "AG_TRIAGEM" if devolve else SAIDA_DA_ESTACAO[estacao]

    # Internalizar não é dizer que internalizou: é o ServiceNow mostrar o
    # ativo no estoque do CD, num espaço de internalização ou reparo.
    # Enquanto não mostrar, o ativo FICA na fila — recusar aqui é o que
    # impede saldo disponível que ninguém acha na prateleira.
    conferencia = None
    if estacao == INTERNALIZACAO and not devolve:
        conferencia = conferir_no_servicenow(req, serial)
        if not conferencia.get("ok"):
            raise HTTPException(409, conferencia.get("motivo") or
                                "O ServiceNow ainda não mostra o ativo internalizado.")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            # Equipamento anterior ao módulo é adotado aqui; o relógio começa agora.
            ativo = garantir_ativo(s, serial, usuario=usuario, origem=_ORIGEM_ADOCAO)
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha e a adoção "
                                     "automática está desligada em Configuração → Ciclo do ativo.")
        if ativo.estado_fisico not in (FILA_DA_ESTACAO[estacao],
                                       TRATATIVA_DA_ESTACAO[estacao]):
            raise HTTPException(
                409, f"A série {serial} não está na fila de "
                     f"{ROTULO_ESTACAO[estacao]} — está em "
                     f"{dbt.rotulo_estado(ativo.estado_fisico).lower()}.")
        try:
            mover(s, ativo, estado=proximo, tipo=dbt.FILA,
                  processo=PROCESSO[estacao], usuario=usuario,
                  justificativa=(body.observacao or "") if devolve else "")
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()
        ativo_id = ativo.id

    with SessionLocal() as s:
        passagem = Passagem(
            serial=serial, trilha_ativo_id=ativo_id, estacao=estacao,
            baseline_id=body.baseline_id, firmware=(body.firmware or "").strip(),
            teste_funcional=body.teste_funcional,
            carcaca=(body.carcaca or "").strip().upper(),
            componentes=", ".join(c.strip().upper() for c in body.componentes if c.strip()),
            conferencia_ok=body.conferencia_ok,
            # Na internalização o endereço é o que o ServiceNow mostrou,
            # não o que alguém digitou.
            endereco=((conferencia or {}).get("corredor")
                      or (body.endereco or "").strip()),
            observacao=(body.observacao or "").strip(),
            usuario=usuario,
        )
        s.add(passagem)
        s.flush()
        if estacao == MONTAGEM:
            for comp in body.componentes:
                comp = comp.strip().upper()
                if comp:
                    s.add(Composicao(passagem_id=passagem.id,
                                     serial_resultante=serial,
                                     serial_componente=comp))
        s.commit()

    if estacao == MONTAGEM:
        _consumir_componentes(serial, body.componentes, usuario)

    no_servicenow = conferencia

    _log.info("preparacao: %s concluiu %s em %s → %s",
              usuario, serial, estacao, proximo)
    return {"ok": True, "serial": serial, "proximo_estado": proximo,
            "devolvido": devolve, "no_servicenow": no_servicenow}


def _padroes_corredor(cfg: dict) -> list[str]:
    return [p.strip().upper()
            for p in (cfg.get("padroes_corredor") or "").split(",") if p.strip()]


def corredor_aceito(corredor: str, padroes: list[str]) -> bool:
    """O espaço/corredor diz que o ativo foi internalizado?"""
    texto = (corredor or "").strip().upper()
    return bool(texto) and any(p in texto for p in padroes)


def conferir_no_servicenow(req: Request, serial: str) -> dict:
    """Lê o ativo no ServiceNow e diz se ele está mesmo internalizado.

    Internalizado = estoque do CD (`estoque_internalizacao`) e espaço/
    corredor contendo um dos padrões (`padroes_corredor`, INA ou REP).
    Só isso libera o ativo para envio às lojas.
    """
    cfg = db.ler_config()
    if (cfg.get("conferir_no_servicenow") or "").strip().lower() not in ("1", "sim", "true"):
        return {"ativo": False, "ok": True,
                "motivo": "conferência no ServiceNow desligada na configuração"}
    estoque = (cfg.get("estoque_internalizacao") or "").strip()
    padroes = _padroes_corredor(cfg)
    try:
        from routers.servicenow import (
            _sn_session_from_portal, _sn_query, HARDWARE_TABLE, termo_sn,
        )
        import db.separacao as dbsep
        campo = (dbsep.ler_config().get("campo_local") or "aisle_space_location").strip()
        session = _sn_session_from_portal(req)
        achados = _sn_query(session, HARDWARE_TABLE,
                            f"serial_number={termo_sn(serial, 'série')}",
                            f"sys_id,serial_number,stockroom,{campo}",
                            limit=1, display_value=True)
    except Exception as exc:  # noqa: BLE001 — ServiceNow fora do ar
        _log.error("preparacao: conferência de %s não foi possível: %s", serial, exc)
        return {"ativo": True, "ok": False,
                "motivo": f"não foi possível consultar o ServiceNow: {exc}"}

    if not achados:
        return {"ativo": True, "ok": False,
                "motivo": f"a série {serial} não existe no ServiceNow."}

    def _texto(valor):
        if isinstance(valor, dict):
            valor = valor.get("display_value") or valor.get("value") or ""
        return str(valor or "").strip()

    registro = achados[0]
    estoque_sn = _texto(registro.get("stockroom"))
    corredor_sn = _texto(registro.get(campo))
    dados = {"ativo": True, "estoque": estoque_sn, "corredor": corredor_sn,
             "estoque_esperado": estoque, "padroes": padroes}

    if estoque and estoque_sn.upper() != estoque.upper():
        dados["ok"] = False
        dados["motivo"] = (f"o ServiceNow mostra a série {serial} em "
                           f"\"{estoque_sn or '—'}\"; ela precisa estar em "
                           f"\"{estoque}\" para ser internalizada.")
        return dados
    if padroes and not corredor_aceito(corredor_sn, padroes):
        dados["ok"] = False
        dados["motivo"] = (f"o espaço e corredor da série {serial} é "
                           f"\"{corredor_sn or '—'}\"; para internalizar ele "
                           f"precisa conter {' ou '.join(padroes)}.")
        return dados
    dados["ok"] = True
    return dados


class ConferenciaIn(BaseModel):
    serial: str = ""


@router.post("/internalizacao/conferir")
def api_conferir(body: ConferenciaIn, req: Request):
    """Confere a fila de internalização contra o ServiceNow.

    Sem série, varre a fila inteira: o cadastro pode ter sido ajustado
    depois, e quem já estiver internalizado passa a disponível sem
    ninguém precisar bipar de novo.
    """
    sd = require_permission(req, "preparacao", "edit")
    usuario = sd.get("username", "")
    alvo = (body.serial or "").strip().upper()

    with dbt.SessionLocal() as s:
        na_fila = [a.serial for _i, a in s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado == FILA_DA_ESTACAO[INTERNALIZACAO])
            .order_by(dbt.Intervalo.inicio)
        ).all()]
    if alvo:
        if alvo not in na_fila:
            raise HTTPException(
                409, f"A série {alvo} não está na fila de internalização.")
        na_fila = [alvo]

    liberados, pendentes = [], []
    for serial in na_fila:
        resultado = conferir_no_servicenow(req, serial)
        if not resultado.get("ok"):
            pendentes.append({"serial": serial, "motivo": resultado.get("motivo", "")})
            continue
        try:
            with dbt.SessionLocal() as s:
                ativo = s.execute(
                    select(dbt.Ativo).where(dbt.Ativo.serial == serial)
                ).scalar_one_or_none()
                if ativo is None:
                    continue
                mover(s, ativo, estado=SAIDA_DA_ESTACAO[INTERNALIZACAO],
                      tipo=dbt.FILA, processo=PROCESSO[INTERNALIZACAO],
                      usuario=usuario,
                      detalhe='{"conferido_no_servicenow": true}')
                s.commit()
            with SessionLocal() as s:
                s.add(Passagem(serial=serial, estacao=INTERNALIZACAO,
                               conferencia_ok=True,
                               endereco=resultado.get("corredor", ""),
                               observacao="Conferido no ServiceNow",
                               usuario=usuario))
                s.commit()
            liberados.append(serial)
        except TrilhaInvalida as exc:
            pendentes.append({"serial": serial, "motivo": str(exc)})
    _log.info("preparacao: conferência liberou %d e deixou %d pendente(s)",
              len(liberados), len(pendentes))
    return {"liberados": liberados, "pendentes": pendentes,
            "conferidos": len(na_fila)}


def _exigencias(estacao: str, body: ConclusaoIn) -> None:
    if estacao == CONFIGURACAO:
        if not body.baseline_id:
            raise HTTPException(
                400, "Escolha a baseline aplicada: é ela que liga uma falha "
                     "futura à versão de configuração.")
        if body.teste_funcional is not True:
            raise HTTPException(
                400, "O teste funcional precisa passar antes de o coletor "
                     "voltar ao estoque.")
    elif estacao == MONTAGEM:
        if not (body.carcaca or "").strip():
            raise HTTPException(400, "Informe a carcaça utilizada.")
        if body.teste_funcional is not True:
            raise HTTPException(400, "O sled montado precisa passar no teste.")
    elif estacao == INTERNALIZACAO:
        if body.conferencia_ok is None:
            raise HTTPException(400, "Diga se a conferência passou.")
        # O endereço não é digitado: quem diz onde o ativo está é o
        # ServiceNow, conferido antes de liberar.
        if body.conferencia_ok is False and not (body.observacao or "").strip():
            raise HTTPException(
                400, "Conferência reprovada exige o motivo — é o que a "
                     "bancada vai ler ao receber de volta.")


def _consumir_componentes(resultante: str, componentes: list[str],
                          usuario: str) -> None:
    """Encerra a trilha de cada componente que virou parte do sled.

    O componente deixa de existir como unidade. Encerrar em vez de
    apagar mantém a trilha dele legível: dá para responder onde ele foi
    parar meses depois.
    """
    from routers.trilha import encerrar
    for comp in componentes:
        comp = (comp or "").strip().upper()
        if not comp or comp == resultante:
            continue
        try:
            with dbt.SessionLocal() as s:
                ativo = s.execute(
                    select(dbt.Ativo).where(dbt.Ativo.serial == comp)
                ).scalar_one_or_none()
                if ativo is None or ativo.encerrado:
                    continue
                encerrar(s, ativo, estado="CONSUMIDO_EM_MONTAGEM",
                         processo="A07", usuario=usuario,
                         justificativa=f"Consumido na montagem de {resultante}")
                s.commit()
        except Exception as exc:  # noqa: BLE001
            _log.error("preparacao: componente %s não encerrou na trilha: %s",
                       comp, exc)


# ══════════════════════════════════════════════════════════════════
#  Baselines
# ══════════════════════════════════════════════════════════════════

class BaselineIn(BaseModel):
    versao: str
    descricao: str = ""
    vigente: bool = False


@router.post("/baselines")
def api_baseline_add(body: BaselineIn, req: Request):
    sd = require_permission(req, "preparacao", "admin")
    versao = (body.versao or "").strip()
    if not versao:
        raise HTTPException(400, "Informe a versão.")
    with SessionLocal() as s:
        if s.execute(select(Baseline).where(Baseline.versao == versao)).scalar_one_or_none():
            raise HTTPException(409, "Essa versão já existe.")
        if body.vigente:
            # Uma vigente por vez: duas confundem quem configura, e o
            # indicador de aderência perde o denominador.
            for b in s.execute(select(Baseline).where(Baseline.vigente.is_(True))).scalars():
                b.vigente = False
        s.add(Baseline(versao=versao, descricao=(body.descricao or "").strip(),
                       vigente=body.vigente, criada_por=sd.get("username", "")))
        s.commit()
    return api_baselines(req)


@router.put("/baselines/{baseline_id}/vigente")
def api_baseline_vigente(baseline_id: int, req: Request):
    require_permission(req, "preparacao", "admin")
    with SessionLocal() as s:
        alvo = s.get(Baseline, baseline_id)
        if alvo is None:
            raise HTTPException(404, "Baseline não encontrada.")
        for b in s.execute(select(Baseline).where(Baseline.vigente.is_(True))).scalars():
            b.vigente = False
        alvo.vigente = True
        s.commit()
    return api_baselines(req)


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "preparacao", "admin")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    require_permission(req, "preparacao", "admin")
    pares = {k: str(v) for k, v in (body or {}).items() if k in db.PADROES}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    return db.ler_config()
