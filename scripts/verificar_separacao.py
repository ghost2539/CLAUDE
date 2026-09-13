#!/usr/bin/env python3
"""Verificação da Separação (A15) — o que ela deixa no núcleo.

Bancos temporários, ServiceNow de dublê:

    python3 scripts/verificar_separacao.py

O ponto central: o relógio da unidade bipada tem que FECHAR no envio e
voltar a fila no cancelamento. Sem isso a frente "Saída" da Torre infla
para sempre e o painel de quem separou mente.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="sep-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["SEPARACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/separacao.db"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
import db.trilha as dbt  # noqa: E402
import db.separacao as db  # noqa: E402
import routers.servicenow as sn  # noqa: E402
import routers.separacao as sep  # noqa: E402
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


anot = {"reservas": [], "liberacoes": [], "updates": [], "queries": []}
sn._sn_session_from_portal = lambda req: object()
sn._get_http = lambda: (None, None)
sn._lookup_reference = lambda session, t, nome, cache, BS: "loc-1"
sn._sn_update = lambda session, t, sid, alt: anot["updates"].append((sid, alt)) or True


def _query(session, table, query, fields, limit=50, offset=0, display_value=True):
    anot["queries"].append(query)
    if table == "incident":
        return [{"number": "INC1", "short_description": "loja parada", "state": "2", "location": "LOJA 5"}]
    serial = query.split("serial_number=")[-1]
    return [{"sys_id": "sys-" + serial, "serial_number": serial, "substatus": "available",
             "aisle_space_location": "REP-A01"}]


sn._sn_query = _query
sep._escrever_reserva = lambda req, sid, serial, reservar: (anot["reservas"] if reservar else anot["liberacoes"]).append(serial)
sep.require_permission = lambda req, m, a: {"username": "sep"}
sep.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
db.init_db()
dbt.gravar_config({"expediente_dias": "0,1,2,3,4,5,6", "expediente_inicio": "00:00",
                   "expediente_fim": "23:59", "feriados": "", "fuso_horas": "0"})


def token(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one()
        ab = s.execute(select(dbt.Intervalo).where(dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
        return {"estado": a.estado_fisico, "encerrado": a.encerrado,
                "abertos": [(i.estado, i.tipo, i.usuario) for i in ab]}


with dbt.SessionLocal() as s:
    for ser in ("U1", "U2", "U3"):
        a = abrir_ativo(s, serial=ser, usuario="rec", modelo="TC21")
        mover(s, a, estado="DISPONIVEL", tipo=dbt.FILA, processo="A08", usuario="rec")
    s.commit()

print("\n[1] Sanitização: termo com operador do ServiceNow é recusado antes de consultar")
esperar(400, "chamado com ^ é recusado", lambda: sep.consultar_chamado(REQ, "INC1^ORactive=true"))
esperar(400, "série com = é recusada", lambda: sep.localizar_no_estoque(REQ, "X=1", "MOBILIDADE"))
checar(not any("^OR" in q or "X=1" in q for q in anot["queries"]), "nada disso chegou ao ServiceNow")

print("\n[2] Pedido → bipe: relógio TRATATIVA de quem bipou")
d = sep.api_criar(sep.SolicitacaoIn(chamado="inc1", tipo_atendimento="MOBILIDADE",
                                    itens=[sep.ItemIn(modelo="TC21", quantidade=2)]), REQ)
NUM = d["numero"]
checar(d["destino"] == "LOJA 5", "destino veio do chamado")
sep.api_iniciar(NUM, REQ)
item_id = sep.api_detalhe(NUM, REQ)["itens"][0]["id"]
sep.api_bipar(NUM, sep.BipeIn(item_id=item_id, serial="u1"), REQ)
sep.api_bipar(NUM, sep.BipeIn(item_id=item_id, serial="U2"), REQ)
checar(anot["reservas"] == ["U1", "U2"], "duas reservas no ServiceNow")
checar(token("U1")["abertos"] == [("EX_SEPARACAO", dbt.TRATATIVA, "sep")], "U1 em tratativa de quem bipou")

print("\n[3] Envio FECHA o relógio das unidades")
sep.api_concluir(NUM, REQ)
d = sep.api_enviar(NUM, REQ)
checar(d["estado"] == "ENVIADA", "pedido enviado")
checar(all(a[1]["install_status"] == "1" and a[1]["location"] == "loc-1" for a in anot["updates"]), "unidades em uso na loja no ServiceNow")
for ser in ("U1", "U2"):
    t = token(ser)
    checar(t["encerrado"] and t["estado"] == "ENTREGUE" and not t["abertos"], f"{ser} encerrada como ENTREGUE, sem relógio aberto")

print("\n[4] Cancelamento DEVOLVE a unidade ao estoque (fila), soltando a reserva")
d = sep.api_criar(sep.SolicitacaoIn(chamado="INC1", tipo_atendimento="MOBILIDADE",
                                    itens=[sep.ItemIn(modelo="TC21", quantidade=1)]), REQ)
N2 = d["numero"]
sep.api_iniciar(N2, REQ)
item_id = sep.api_detalhe(N2, REQ)["itens"][0]["id"]
sep.api_bipar(N2, sep.BipeIn(item_id=item_id, serial="U3"), REQ)
sep.api_cancelar(N2, sep.CancelaIn(motivo="loja desistiu"), REQ)
checar(anot["liberacoes"] == ["U3"], "reserva de U3 solta")
t = token("U3")
checar(not t["encerrado"] and t["abertos"] == [("DISPONIVEL", dbt.FILA, "")], "U3 de volta a DISPONIVEL como fila")

print("\n[5] Invariante: um relógio aberto por ativo")
with dbt.SessionLocal() as s:
    ab = s.execute(select(dbt.Intervalo.ativo_id).where(dbt.Intervalo.fim.is_(None))).scalars().all()
checar(len(ab) == len(set(ab)), "um relógio aberto por ativo, no máximo")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Separação íntegra.")
