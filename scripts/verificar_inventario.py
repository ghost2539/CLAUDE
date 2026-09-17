#!/usr/bin/env python3
"""Verificação do Inventário e Contagem (A18). Bancos temporários; o
ServiceNow é um dublê que devolve um retrato fixo.

    python3 scripts/verificar_inventario.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="inv-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["INVENTARIO_DATABASE_URL"] = f"sqlite:///{_TEMP}/inv.db"
os.environ["REGULARIZACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/reg.db"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
import db.trilha as dbt  # noqa: E402
import db.inventario as db  # noqa: E402
import db.regularizacao as dbreg  # noqa: E402
import routers.servicenow as sn  # noqa: E402
import routers.inventario as inv  # noqa: E402
import routers.regularizacao as reg  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def esperar(codigo, descricao, funcao):
    try:
        funcao()
    except HTTPException as exc:
        checar(exc.status_code == codigo, f"{descricao} (HTTP {exc.status_code})")
    else:
        checar(False, f"{descricao} (não recusou)")


# ── Dublê do ServiceNow: um depósito com dois corredores e uma loja ──
PARQUE = [
    {"sys_id": "1", "serial_number": "S1", "asset_tag": "T1", "model": "TC21", "install_status": "6", "aisle_space_location": "REP-A01"},
    {"sys_id": "2", "serial_number": "S2", "asset_tag": "T2", "model": "TC21", "install_status": "6", "aisle_space_location": "REP-A01"},
    {"sys_id": "3", "serial_number": "S3", "asset_tag": "T3", "model": "AP505", "install_status": "6", "aisle_space_location": "REP-B02"},
    {"sys_id": "4", "serial_number": "S4", "asset_tag": "T4", "model": "TC21", "install_status": "6", "aisle_space_location": "IN-A01"},
    {"sys_id": "5", "serial_number": "S5", "asset_tag": "T5", "model": "TC21", "install_status": "1", "location": "LOJA 77"},
]


def _query_all(session, table, query, fields, page_size=500, max_records=10000):
    return [r for r in PARQUE if f"install_status={r['install_status']}" == query]


def _query(session, table, query, fields, limit=50, offset=0, display_value=True):
    alvo = query.split("=")[1].split("^")[0]
    return [r for r in PARQUE if r["serial_number"] == alvo or r["asset_tag"] == alvo][:1]


sn._sn_query_all = _query_all
sn._sn_query = _query
sn._sn_session_from_portal = lambda req: object()
inv.require_permission = lambda req, m, a: {"username": "conta"}
inv.check_rate_limit = lambda req: None
reg.require_permission = lambda req, m, a: {"username": "conta"}
REQ = object()

dbt.init_db()
db.init_db()
dbreg.init_db()
dbt.gravar_config({"expediente_dias": "0,1,2,3,4,5,6", "expediente_inicio": "00:00",
                   "expediente_fim": "23:59", "feriados": "", "fuso_horas": "0"})


def token(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
        ab = s.execute(select(dbt.Intervalo).where(dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
        return {"encerrado": a.encerrado, "abertos": [(i.estado, i.tipo, i.usuario) for i in ab]}


print("\n[1] Prévia e abertura: o retrato respeita o corredor e congela")
p = inv.api_previa(REQ, prefixo="rep")
checar(p["total"] == 3 and len(p["por_local"]) == 2, "prévia REP: 3 itens em 2 corredores")
checar(inv.api_previa(REQ)["total"] == 4, "prévia sem prefixo: tudo em estoque (4), loja fora")
esperar(409, "recorte vazio é recusado", lambda: inv.api_criar(inv.CicloIn(prefixo="ZZZ"), REQ))
d = inv.api_criar(inv.CicloIn(prefixo="REP", descricao="Mensal REP"), REQ)
NUM = d["numero"]
checar(NUM.startswith("INV-") and d["esperados"] == 3, f"ciclo {NUM} com 3 esperados")
checar(d["estado"] == "AG_CONTAGEM_INV", "aguardando contagem")
checar(token(NUM)["abertos"] == [("AG_CONTAGEM_INV", dbt.FILA, "")], "token no núcleo, FILA")
esperar(409, "segundo ciclo aberto no mesmo recorte é recusado", lambda: inv.api_criar(inv.CicloIn(prefixo="REP"), REQ))
# O retrato não se move: mexer no parque depois não muda o ciclo.
PARQUE.append({"sys_id": "9", "serial_number": "S9", "asset_tag": "T9", "model": "X", "install_status": "6", "aisle_space_location": "REP-C01"})
checar(inv.api_detalhe(NUM, REQ)["comparacao"]["esperados"] == 3, "retrato congelado (S9 chegou depois e não entra)")

print("\n[2] Contagem: casa por série ou etiqueta; sobra consulta o sistema")
esperar(409, "bipar antes de iniciar é recusado", lambda: inv.api_bipar(NUM, inv.BipeIn(serial="S1"), REQ))
d = inv.api_iniciar(NUM, REQ)
checar(d["estado"] == "EX_CONTAGEM_INV" and d["contado_por"] == "conta", "contagem iniciada")
checar(token(NUM)["abertos"] == [("EX_CONTAGEM_INV", dbt.TRATATIVA, "conta")], "TRATATIVA com dono")
r = inv.api_bipar(NUM, inv.BipeIn(serial="s1", local="REP-A01"), REQ)
checar(r["casou"], "S1 casou por série")
r = inv.api_bipar(NUM, inv.BipeIn(serial="T2", local="REP-A01"), REQ)
checar(r["casou"], "T2 casou por etiqueta")
esperar(409, "S1 de novo é recusado", lambda: inv.api_bipar(NUM, inv.BipeIn(serial="S1"), REQ))
r = inv.api_bipar(NUM, inv.BipeIn(serial="S4", local="REP-B02"), REQ)
checar(not r["casou"] and r["situacao"].startswith("em estoque em IN-A01"), "S4 é sobra: sistema diz em estoque no IN")
r = inv.api_bipar(NUM, inv.BipeIn(serial="S5", local="REP-B02"), REQ)
checar(not r["casou"] and "LOJA 77" in r["situacao"], "S5 é sobra: sistema diz em uso na loja")
r = inv.api_bipar(NUM, inv.BipeIn(serial="NADA", local="REP-B02"), REQ)
checar(not r["casou"] and "não existe" in r["situacao"], "série desconhecida é sobra: não existe no sistema")
cmp_ = r["comparacao"]
checar(len(cmp_["faltantes"]) == 1 and cmp_["faltantes"][0]["serial"] == "S3", "S3 falta")
checar(len(cmp_["sobras"]) == 3 and cmp_["divergente"], "3 sobras, divergente")
checar([l["contados"] for l in cmp_["por_local"]] == [2, 0], "por corredor: A01 completo, B02 zerado")

print("\n[3] Fechar: divergente exige texto; cada diferença vira regularização com o tipo certo")
esperar(400, "concluir divergente sem observação é recusado", lambda: inv.api_concluir(NUM, inv.ConclusaoIn(), REQ))
d = inv.api_concluir(NUM, inv.ConclusaoIn(observacao="corredor B bagunçado"), REQ)
checar(d["estado"] == "DIVERGENTE_INV" and d["encerrado"], "fechou DIVERGENTE")
t = token(NUM)
checar(t["encerrado"] and not t["abertos"], "token encerrado")
lst = reg.api_lista(REQ)
abertas = {(x["serial"], x["tipo"]) for x in lst["divergencias"]}
checar(("S3", "FALTANTE") in abertas, "S3 → FALTANTE")
checar(("S4", "LOCAL_ERRADO") in abertas, "S4 → LOCAL_ERRADO (em estoque, outro corredor)")
checar(("S5", "INESPERADO") in abertas, "S5 → INESPERADO (em uso na loja)")
checar(("NADA", "INESPERADO") in abertas, "NADA → INESPERADO (não existe)")
checar(all(x["origem"] == "A18" and x["referencia"] == NUM for x in lst["divergencias"]), "origem A18 com referência ao ciclo")
checar(len(lst["divergencias"]) == 4, "exatamente 4 divergências")
esperar(409, "bipar depois de fechado é recusado", lambda: inv.api_bipar(NUM, inv.BipeIn(serial="S3"), REQ))

print("\n[4] Ciclo limpo fecha CONFERIDO sem abrir nada")
d = inv.api_criar(inv.CicloIn(prefixo="IN"), REQ)
N2 = d["numero"]
inv.api_iniciar(N2, REQ)
inv.api_bipar(N2, inv.BipeIn(serial="S4", local="IN-A01"), REQ)
d = inv.api_concluir(N2, inv.ConclusaoIn(), REQ)
checar(d["estado"] == "CONFERIDO_INV", "CONFERIDO")
checar(len(reg.api_lista(REQ)["divergencias"]) == 4, "nenhuma divergência nova")

print("\n[5] Cancelar e listar")
d = inv.api_criar(inv.CicloIn(), REQ)
d = inv.api_cancelar(d["numero"], inv.CancelaIn(motivo="feriado"), REQ)
checar(d["estado"] == "CANCELADO_INV" and token(d["numero"])["encerrado"], "cancelado e token encerrado")
lst = inv.api_lista(REQ)
checar(lst["contagem"]["DIVERGENTE_INV"] == 1 and lst["contagem"]["CONFERIDO_INV"] == 1 and lst["contagem"]["CANCELADO_INV"] == 1, "contagem por estado")
linha = [x for x in lst["ciclos"] if x["numero"] == NUM][0]
checar(linha["casados"] == 2 and linha["sobras"] == 3 and linha["faltantes"] == 1, "linha da lista: 2 casados, 3 sobras, 1 faltante")

print("\n[6] Invariante: um relógio aberto por token")
with dbt.SessionLocal() as s:
    ab = s.execute(select(dbt.Intervalo.ativo_id).where(dbt.Intervalo.fim.is_(None))).scalars().all()
checar(len(ab) == len(set(ab)), "um relógio aberto por ativo, no máximo")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Inventário íntegro.")
