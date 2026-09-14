#!/usr/bin/env python3
"""Verificação do Controle de Orçamento: a coluna "Em andamento".

    python3 scripts/verificar_controle_orcamento.py

"Em andamento" é o que ainda NÃO está comprometido no EBS mas já está em
curso — uma PO aguardando aprovação, por exemplo. É digitado na tela, e
sincronizar com o EBS não pode apagá-lo. Roda contra um SQLite temporário.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["ORCAMENTO_EXEC_DATABASE_URL"] = f"sqlite:///{_TMP/'orcamento_exec.db'}"

import db.orcamento_exec as dbo  # noqa: E402
import routers.controle_orcamento_exec as co  # noqa: E402
from sqlalchemy import select  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


dbo.init_db()
P = dbo.BudgetProject

print("\n[1] A coluna existe e sai na API")
with dbo.SessionLocal.begin() as s:
    s.add(P(code="PRJ-1", name="Troca de coletores", kind="CAPEX",
            approved_budget=Decimal("100000"), committed=Decimal("30000"),
            realized=Decimal("20000"), a_realizar=Decimal("50000"),
            em_andamento=Decimal("15000")))
with dbo.SessionLocal() as s:
    linha = s.scalar(select(P).where(P.code == "PRJ-1"))
    saida = co._dict(linha)
checar(saida["em_andamento"] == 15000.0,
       f"em_andamento vai para a tela ({saida.get('em_andamento')})")
checar(saida["a_realizar"] == 50000.0, "a_realizar continua vindo do EBS")
checar("em_andamento" in co._CAMPOS and co._CAMPOS["em_andamento"] == "em_andamento",
       "o campo é gravável pela tela")

print("\n[2] Em andamento é editável; a realizar não")
corpo = co.ProjetoIn(em_andamento=12500.9)
checar(corpo.em_andamento == Decimal("12500.90"),
       f"aceita o valor da tela ({corpo.em_andamento})")
# Mesma régua dos outros campos de dinheiro do módulo: nada de negativo.
try:
    co.ProjetoIn(em_andamento=-1)
    checar(False, "valor negativo é recusado")
except Exception:
    checar(True, "valor negativo é recusado")
try:
    co.ProjetoIn(a_realizar=10)
    checar(False, "a_realizar NÃO pode ser enviado pela tela")
except Exception:
    checar(True, "a_realizar NÃO pode ser enviado pela tela")
checar(co._PADRAO["em_andamento"] == Decimal("0"),
       "projeto novo nasce com em andamento zerado")

print("\n[3] Sincronizar com o EBS não apaga o que foi digitado")
with dbo.SessionLocal.begin() as s:
    alvo = s.scalar(select(P).where(P.code == "PRJ-1"))
    co._aplicar_ebs(alvo, {"saldo_inicial": "120000", "comprometido": "40000",
                           "reservados": "5000", "realizado": "25000",
                           "saldo_dia": "50000", "empresa": "RENNER"},
                    rates={})
with dbo.SessionLocal() as s:
    depois = s.scalar(select(P).where(P.code == "PRJ-1"))
checar(float(depois.committed) == 45000.0,
       f"comprometido = comprometido + reservados ({depois.committed})")
checar(float(depois.a_realizar) == 50000.0, "a_realizar veio do saldo do dia")
checar(float(depois.em_andamento) == 15000.0,
       f"em andamento sobreviveu à sincronização ({depois.em_andamento})")

print("\n[4] Banco que já existe ganha a coluna sem perder dado")
alvo_db = Path(str(dbo.DATABASE_URL).replace("sqlite:///", ""))
con = sqlite3.connect(alvo_db)
con.execute("ALTER TABLE budget_projects DROP COLUMN em_andamento")
con.commit()
cols = {c[1] for c in con.execute("PRAGMA table_info(budget_projects)")}
checar("em_andamento" not in cols, "banco antigo montado, sem a coluna")
antes = con.execute("SELECT COUNT(*) FROM budget_projects").fetchone()[0]
con.close()

dbo._ready = False
dbo._ensure_coluna_a_realizar()
con = sqlite3.connect(alvo_db)
cols = {c[1] for c in con.execute("PRAGMA table_info(budget_projects)")}
checar("em_andamento" in cols, "a migração acrescentou a coluna")
checar(con.execute("SELECT COUNT(*) FROM budget_projects").fetchone()[0] == antes,
       f"nenhuma linha se perdeu ({antes})")
zerados = con.execute("SELECT COUNT(*) FROM budget_projects "
                      "WHERE em_andamento IS NULL OR em_andamento = 0").fetchone()[0]
checar(zerados == antes, "as linhas antigas ficam com em andamento zerado")
con.close()
dbo._ensure_coluna_a_realizar()
checar(True, "rodar a migração de novo não quebra")

print("\n[5] A conta da tela fecha")
# A tela calcula: disponível = a realizar − em andamento. O valor foi
# zerado pela migração da seção [4], então é digitado de novo — que é
# exatamente o que o usuário faria depois de subir a versão.
dbo._engine = None
dbo._factory = None
with dbo.SessionLocal.begin() as s:
    s.scalar(select(P).where(P.code == "PRJ-1")).em_andamento = Decimal("15000")
with dbo.SessionLocal() as s:
    d = co._dict(s.scalar(select(P).where(P.code == "PRJ-1")))
disponivel = d["a_realizar"] - d["em_andamento"]
checar(disponivel == 35000.0,
       f"disponível = {d['a_realizar']} − {d['em_andamento']} = {disponivel}")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Controle de Orçamento íntegro.")
