#!/usr/bin/env python3
"""Verificação das POs/NFs do agendamento de fornecedores.

Prova o que o pedido pedia: várias POs por agendamento, a MESMA NF cobrindo
mais de uma, as colunas antigas `po`/`nf` seguindo a primeira linha, e o
agendamento velho (só com as colunas soltas) voltando com uma linha de pedido.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="agf-pedidos-verif-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP}/portal.db"
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-com-64-caracteres-de-sobra-aqui")
os.environ["INITIAL_ADMIN_LOGIN"] = "admin.teste"
os.environ["AMBIENTE"] = "testes"
for _modulo in ("TRILHA", "OBSOLESCENCIA", "REVERSA", "AGENDAMENTOS_FORN",
                "INTERNALIZACAO"):
    os.environ[f"{_modulo}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{_modulo.lower()}.db"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")

from fastapi.testclient import TestClient            # noqa: E402

from main import app                                 # noqa: E402
import core.security as sec                          # noqa: E402
import db.agendamentos_forn as agfdb                 # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d) -> None:
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


c = TestClient(app).__enter__()   # com lifespan: init_db cria as tabelas
_, cookie = sec.create_session({"username": "admin.teste", "is_admin": True,
                                "permissions": ["admin"], "permission_map": {},
                                "user_id": 1, "ebs_auth": None})
CK = {sec.COOKIE_SESSAO: cookie}

BASE = {"bu": "Renner", "fornecedor": "Positivo", "volumes": 3,
        "estoque_destino": "REPOSICAO", "data_agendada": "2026-10-01"}


print("\n1) Criar com 2 POs cobertas pela MESMA NF")
corpo = dict(BASE)
corpo["pedidos"] = [
    {"po": "4512345", "nf": "998877", "fornecedor_ebs": "POSITIVO SA", "status_ebs": "APPROVED"},
    {"po": "4512346", "nf": "998877"},
]
corpo["equipamentos"] = [{"descricao": "Positivo Master CE800", "quantidade": 4}]
r = c.post("/api/agendamentos-forn", cookies=CK, json=corpo)
checar(r.status_code == 201, f"POST responde 201 (veio {r.status_code}: {r.text[:200]})")
novo = r.json()
ident = novo["id"]
checar(len(novo.get("pedidos", [])) == 2, "criação devolve as 2 linhas de pedido")
checar(novo["po"] == "4512345" and novo["nf"] == "998877",
       "colunas antigas po/nf recebem a PRIMEIRA PO/NF")

print("\n2) Reabrir o agendamento (GET) e conferir as duas POs")
r = c.get(f"/api/agendamentos-forn/{ident}", cookies=CK)
checar(r.status_code == 200, f"GET responde 200 (veio {r.status_code})")
d = r.json()
pos = [p["po"] for p in d["pedidos"]]
nfs = {p["nf"] for p in d["pedidos"]}
checar(pos == ["4512345", "4512346"], f"voltam as duas POs na ordem: {pos}")
checar(nfs == {"998877"}, f"a mesma NF cobre as duas: {nfs}")
checar(d["pedidos"][0]["fornecedor_ebs"] == "POSITIVO SA"
       and d["pedidos"][0]["status_ebs"] == "APPROVED",
       "o que o EBS respondeu fica gravado na linha")
checar(len(d["equipamentos"]) == 1, "equipamentos continuam vindo junto")

print("\n3) Editar (PATCH) trocando a SEGUNDA PO")
corpo2 = dict(BASE)
corpo2["pedidos"] = [{"po": "4512345", "nf": "998877"},
                     {"po": "4599999", "nf": "998877"}]
corpo2["equipamentos"] = [{"descricao": "Positivo Master CE800", "quantidade": 4}]
r = c.patch(f"/api/agendamentos-forn/{ident}", cookies=CK, json=corpo2)
checar(r.status_code == 200, f"PATCH responde 200 (veio {r.status_code}: {r.text[:200]})")
d = r.json()
checar([p["po"] for p in d["pedidos"]] == ["4512345", "4599999"],
       "a lista foi substituída pela nova (sem sobra da antiga)")
checar(d["po"] == "4512345" and d["nf"] == "998877",
       "po/nf do agendamento seguem a PRIMEIRA linha")
with agfdb.SessionLocal() as s:
    from sqlalchemy import select
    linhas = s.scalars(select(agfdb.Pedido).where(
        agfdb.Pedido.agendamento_id == ident)).all()
checar(len(linhas) == 2, f"no banco sobraram só 2 linhas de pedido ({len(linhas)})")

print("\n4) PATCH trocando a PRIMEIRA PO: as colunas antigas acompanham")
corpo3 = dict(BASE)
corpo3["pedidos"] = [{"po": "4500001", "nf": "112233"},
                     {"po": "4599999", "nf": "998877"}]
r = c.patch(f"/api/agendamentos-forn/{ident}", cookies=CK, json=corpo3)
d = r.json()
checar(d["po"] == "4500001" and d["nf"] == "112233",
       f"po/nf viraram a nova primeira linha ({d['po']}/{d['nf']})")
r = c.get("/api/agendamentos-forn?busca=4500001", cookies=CK)
checar(r.json()["total"] == 1, "a busca acha o agendamento pela nova primeira PO")

print("\n5) Chamada antiga (só po/nf, sem lista) ainda grava uma linha")
corpo4 = dict(BASE)
corpo4["po"] = "4512000"
corpo4["nf"] = "555000"
r = c.post("/api/agendamentos-forn", cookies=CK, json=corpo4)
checar(r.status_code == 201, f"POST antigo responde 201 (veio {r.status_code}: {r.text[:200]})")
d = r.json()
checar([(p["po"], p["nf"]) for p in d["pedidos"]] == [("4512000", "555000")],
       "po/nf soltos viram a primeira (e única) linha de pedido")

print("\n6) Agendamento gravado ANTES da tabela de pedidos")
from datetime import date  # noqa: E402
with agfdb.SessionLocal.begin() as s:
    velho = agfdb.Agendamento(bu="Youcom", nf="700700", po="4577777", volumes=1,
                              fornecedor="Antigo Ltda", estoque_destino="REPOSICAO",
                              data_agendada=date(2026, 9, 1), status="AGENDADO")
    s.add(velho)
    s.flush()
    id_velho = velho.id
r = c.get(f"/api/agendamentos-forn/{id_velho}", cookies=CK)
checar(r.json()["pedidos"] == [], "sem semear, o agendamento velho vem sem linha")
agfdb._semear_pedidos()          # é o que roda no init_db de cada subida
r = c.get(f"/api/agendamentos-forn/{id_velho}", cookies=CK)
d = r.json()
checar([(p["po"], p["nf"]) for p in d["pedidos"]] == [("4577777", "700700")],
       "depois de semear, ele volta com uma linha de pedido")
agfdb._semear_pedidos()          # idempotência
r = c.get(f"/api/agendamentos-forn/{id_velho}", cookies=CK)
checar(len(r.json()["pedidos"]) == 1, "semear duas vezes não duplica a linha")

print("\n7) Validações da lista")
def recusa(pedidos, o_que, **extra):
    corpo = dict(BASE)
    corpo["pedidos"] = pedidos
    corpo.update(extra)
    r = c.post("/api/agendamentos-forn", cookies=CK, json=corpo)
    checar(r.status_code == 422, f"{o_que} → 422 (veio {r.status_code})")

recusa([{"po": "", "nf": "1"}], "linha sem PO")
recusa([{"po": "4" * 41, "nf": "1"}], "PO maior que a coluna (40)")
recusa([{"po": "451", "nf": "9" * 41}], "NF maior que a coluna (40)")
recusa([{"po": "451", "nf": "1"}, {"po": "451", "nf": "2"}], "PO repetida")
recusa([{"po": f"45{n:05d}"} for n in range(51)], "mais de 50 POs")
recusa([], "nenhuma PO e nenhuma coluna antiga")

corpo5 = dict(BASE)
corpo5["pedidos"] = [{"po": "4512348"}]      # NF ainda não emitida
r = c.post("/api/agendamentos-forn", cookies=CK, json=corpo5)
checar(r.status_code == 201, f"PO sem NF é aceita (veio {r.status_code}: {r.text[:200]})")
checar(r.json()["nf"] == "", "agendamento sem NF fica com a coluna vazia")

print("\n8) Consulta de PO: BU sem EBS nem chega ao banco")
r = c.get("/api/agendamentos-forn/po/4512345?bu=Youcom", cookies=CK)
checar(r.status_code == 422, f"bu=Youcom → 422 explicando (veio {r.status_code})")
checar("EBS" in r.json().get("detail", ""), f"a mensagem diz por quê: {r.json().get('detail')}")
r = c.get("/api/agendamentos-forn/po/pó inválida!?bu=Renner", cookies=CK)
checar(r.status_code == 422, f"PO com formato inválido → 422 (veio {r.status_code})")

print(f"\n{feitos - len(falhas)}/{feitos} verificações passaram.")
if falhas:
    print("FALHAS:")
    for f in falhas:
        print(" -", f)
    sys.exit(1)
print("Tudo certo.")
