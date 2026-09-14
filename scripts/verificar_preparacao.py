#!/usr/bin/env python3
"""Verificação da Preparação (A06, A07, A08.2) e da internalização.

    python3 scripts/verificar_preparacao.py

Dois pontos: ninguém assume equipamento — concluir a estação é a saída —
e internalizado é o que o ServiceNow mostra no estoque do CD, num espaço
de internalização ou reparo. Enquanto não mostrar, o ativo fica na fila.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="prp-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["PREPARACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/preparacao.db"
os.environ["SEPARACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/separacao.db"

from fastapi import HTTPException            # noqa: E402
from sqlalchemy import select                # noqa: E402
import db.trilha as dbt                      # noqa: E402
import db.preparacao as db                   # noqa: E402
import db.separacao as dbsep                 # noqa: E402
import routers.servicenow as sn              # noqa: E402
import routers.preparacao as prp             # noqa: E402
from routers.trilha import abrir_ativo, mover  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


def esperar(codigo, d, f):
    try:
        f()
    except HTTPException as exc:
        checar(exc.status_code == codigo, f"{d} (HTTP {exc.status_code})")
    else:
        checar(False, f"{d} (não recusou)")


# ── ServiceNow de dublê: a fonte da verdade da internalização ──────
CADASTRO: dict[str, dict] = {}
CONSULTAS: list[str] = []
sn._sn_session_from_portal = lambda req: object()
sn._get_http = lambda: (None, None)


def _consulta(session, tabela, query, campos, limit=50, offset=0, display_value=True):
    CONSULTAS.append(query)
    serial = query.split("serial_number=")[-1]
    dados = CADASTRO.get(serial.upper())
    return [dados] if dados else []


sn._sn_query = _consulta
prp.require_permission = lambda req, m, a: {"username": "preparador"}
prp.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
db.init_db()
dbsep.init_db()   # a conferência lê dali o nome do campo de corredor
dbt.gravar_config({"expediente_dias": "", "expediente_inicio": "00:00",
                   "expediente_fim": "00:00", "feriados": "", "fuso_horas": "0"})


def na_fila(serial, estado):
    with dbt.SessionLocal() as s:
        a = abrir_ativo(s, serial=serial, usuario="rec", modelo="Zebra TC21")
        mover(s, a, estado=estado, tipo=dbt.FILA, processo="A03", usuario="rec")
        s.commit()


def estado(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(
            dbt.Ativo.serial == serial.upper())).scalar_one_or_none()
        return a.estado_fisico if a else ""


def no_servicenow(serial, estoque, corredor):
    CADASTRO[serial.upper()] = {"sys_id": "sys-" + serial, "serial_number": serial,
                                "stockroom": estoque,
                                "aisle_space_location": corredor}


print("\n[1] Não existe mais assumir equipamento na Preparação")
checar(not hasattr(prp, "api_bipar"), "o endpoint de assumir saiu do módulo")
na_fila("P1", "AG_CONFIGURACAO")
with db.SessionLocal() as s:
    base = db.Baseline(versao="1.0", vigente=True)
    s.add(base)
    s.commit()
    BASE_ID = base.id
d = prp.api_fila(REQ, estacao=db.CONFIGURACAO)
checar([x["serial"] for x in d["aguardando"]] == ["P1"], "o ativo aparece no backlog")
ctx = prp.api_ativo("P1", REQ, estacao=db.CONFIGURACAO)
checar(ctx["serial"] == "P1", "o bipe só traz o equipamento para a tela")
checar(estado("P1") == "AG_CONFIGURACAO", "e não move nada")

print("\n[2] Concluir a estação é a saída")
r = prp.api_concluir(prp.ConclusaoIn(serial="P1", estacao=db.CONFIGURACAO,
                                     baseline_id=BASE_ID, teste_funcional=True), REQ)
checar(r["proximo_estado"] == "AG_INTERNALIZACAO", "configuração manda para internalização")
checar(estado("P1") == "AG_INTERNALIZACAO", "o ativo saiu do backlog da configuração")
esperar(400, "concluir sem baseline", lambda: prp.api_concluir(
    prp.ConclusaoIn(serial="P1", estacao=db.CONFIGURACAO, teste_funcional=True), REQ))

print("\n[3] Internalizar sem o ServiceNow mostrar não passa")
esperar(409, "série que o ServiceNow não conhece", lambda: prp.api_concluir(
    prp.ConclusaoIn(serial="P1", estacao=db.INTERNALIZACAO, conferencia_ok=True), REQ))
no_servicenow("P1", "SPARE - LOJA 5", "REP-A01")
esperar(409, "ativo em outro estoque", lambda: prp.api_concluir(
    prp.ConclusaoIn(serial="P1", estacao=db.INTERNALIZACAO, conferencia_ok=True), REQ))
no_servicenow("P1", "SPARE - CD324", "A-12")
esperar(409, "corredor sem INA nem REP", lambda: prp.api_concluir(
    prp.ConclusaoIn(serial="P1", estacao=db.INTERNALIZACAO, conferencia_ok=True), REQ))
checar(estado("P1") == "AG_INTERNALIZACAO", "em nenhuma recusa o ativo saiu da fila")

print("\n[4] Com o cadastro certo, o ativo fica disponível")
no_servicenow("P1", "SPARE - CD324", "INA-07")
r = prp.api_concluir(prp.ConclusaoIn(serial="P1", estacao=db.INTERNALIZACAO,
                                     conferencia_ok=True), REQ)
checar(estado("P1") == "DISPONIVEL", "o ativo está pronto para envio às lojas")
checar(r["no_servicenow"]["corredor"] == "INA-07", "o endereço vem do ServiceNow")
with db.SessionLocal() as s:
    passagem = s.execute(select(db.Passagem).where(
        db.Passagem.serial == "P1",
        db.Passagem.estacao == db.INTERNALIZACAO)).scalars().first()
checar(passagem is not None and passagem.endereco == "INA-07",
       "a passagem grava o endereço conferido, não um digitado")

print("\n[5] REP também interna; a lista de padrões é configurável")
checar(prp.corredor_aceito("REP-B02", ["INA", "REP"]), "REP entra")
checar(prp.corredor_aceito("ina-1", ["INA", "REP"]), "sem depender de caixa")
checar(not prp.corredor_aceito("", ["INA", "REP"]), "corredor vazio não interna")
na_fila("P2", "AG_INTERNALIZACAO")
no_servicenow("P2", "SPARE - CD324", "REP-B02")
prp.api_concluir(prp.ConclusaoIn(serial="P2", estacao=db.INTERNALIZACAO,
                                 conferencia_ok=True), REQ)
checar(estado("P2") == "DISPONIVEL", "REP libera o ativo")

print("\n[6] A fila inteira pode ser conferida de uma vez")
na_fila("P3", "AG_INTERNALIZACAO")
na_fila("P4", "AG_INTERNALIZACAO")
no_servicenow("P3", "SPARE - CD324", "INA-09")
no_servicenow("P4", "SPARE - CD324", "A-99")
r = prp.api_conferir(prp.ConferenciaIn(), REQ)
checar(r["liberados"] == ["P3"], "libera quem o cadastro já mostra internalizado")
checar(len(r["pendentes"]) == 1 and r["pendentes"][0]["serial"] == "P4",
       "e diz por que o outro ficou")
checar("A-99" in r["pendentes"][0]["motivo"], "o motivo mostra o que está no cadastro")
checar(estado("P3") == "DISPONIVEL" and estado("P4") == "AG_INTERNALIZACAO",
       "só o conferido saiu da fila")
esperar(409, "conferir série fora da fila",
        lambda: prp.api_conferir(prp.ConferenciaIn(serial="P1"), REQ))

print("\n[7] Conferência reprovada devolve para a bancada")
esperar(400, "reprovar sem motivo", lambda: prp.api_concluir(
    prp.ConclusaoIn(serial="P4", estacao=db.INTERNALIZACAO, conferencia_ok=False), REQ))
prp.api_concluir(prp.ConclusaoIn(serial="P4", estacao=db.INTERNALIZACAO,
                                 conferencia_ok=False,
                                 observacao="carcaça trincada"), REQ)
checar(estado("P4") == "AG_TRIAGEM", "volta para o backlog da bancada")

print("\n[8] Sem ServiceNow configurado, a conferência da tela volta a valer")
db.gravar_config({"conferir_no_servicenow": "nao"})
na_fila("P5", "AG_INTERNALIZACAO")
prp.api_concluir(prp.ConclusaoIn(serial="P5", estacao=db.INTERNALIZACAO,
                                 conferencia_ok=True), REQ)
checar(estado("P5") == "DISPONIVEL", "com a conferência desligada, a tela decide")
db.gravar_config({"conferir_no_servicenow": "sim"})

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Preparação e internalização íntegras.")
