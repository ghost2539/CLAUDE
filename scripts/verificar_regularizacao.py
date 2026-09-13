#!/usr/bin/env python3
"""Verificação da Regularização de Ativo (A19). Bancos temporários.

    python3 scripts/verificar_regularizacao.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="reg-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["REGULARIZACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/reg.db"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
import db.trilha as dbt  # noqa: E402
import db.regularizacao as db  # noqa: E402
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


# Sessão simulada: quem é e se administra. Permissão de admin é conferida
# via require_permission(..., "admin"), então o dublê olha o dicionário.
SESSAO = {"username": "ana", "is_admin": False}


def _perm(req, modulo, acao):
    if acao == "admin" and not SESSAO["is_admin"]:
        raise HTTPException(403, "Permissão insuficiente.")
    return dict(SESSAO)


reg.require_permission = _perm
reg.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
db.init_db()
dbt.gravar_config({"expediente_dias": "0,1,2,3,4,5,6", "expediente_inicio": "00:00",
                   "expediente_fim": "23:59", "feriados": "", "fuso_horas": "0"})


def token(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
        ab = s.execute(select(dbt.Intervalo).where(dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
        return {"encerrado": a.encerrado, "abertos": [(i.estado, i.tipo, i.usuario) for i in ab]}


print("\n[1] Porta de entrada dos processos: abre, é idempotente, valida tipo")
n1 = reg.abrir_divergencia(origem="A17", referencia="COL-2026-0001", tipo="FALTANTE",
                           serial="abc1", modelo="TC21", loja="LOJA 1", usuario="sistema")
checar(n1 and n1.startswith("REG-"), f"aberta {n1}")
checar(reg.abrir_divergencia(origem="A17", referencia="COL-2026-0001", tipo="FALTANTE",
                             serial="ABC1", usuario="sistema") is None, "mesma (origem, ref, série, tipo) não duplica")
n2 = reg.abrir_divergencia(origem="A17", referencia="COL-2026-0001", tipo="INESPERADO", serial="ABC1", usuario="sistema")
checar(n2 is not None and n2 != n1, "mesma série com outro tipo é outra divergência")
try:
    reg.abrir_divergencia(origem="A18", referencia="X", tipo="QUALQUER", usuario="s")
    checar(False, "tipo inválido recusado")
except ValueError:
    checar(True, "tipo inválido recusado")
checar(token(n1)["abertos"] == [("AG_TRATATIVA_REG", dbt.FILA, "")], "token no núcleo, FILA sem dono")

print("\n[2] Abrir à mão exige descrição e algo que identifique")
esperar(400, "sem descrição é recusada",
        lambda: reg.api_criar(reg.DivergenciaIn(tipo="FALTANTE", serial="M1"), REQ))
esperar(400, "sem série/etiqueta/modelo é recusada",
        lambda: reg.api_criar(reg.DivergenciaIn(tipo="FALTANTE", descricao="x"), REQ))
d = reg.api_criar(reg.DivergenciaIn(tipo="LOCAL_ERRADO", serial="M1", descricao="visto no IN, sistema diz REP",
                                    local_sistema="REP-A01", local_fisico="IN-B02"), REQ)
checar(d["origem"] == "MANUAL" and d["aberta_por"] == "ana", "manual, aberta por quem clicou")
esperar(409, "manual repetida para a mesma série é recusada",
        lambda: reg.api_criar(reg.DivergenciaIn(tipo="LOCAL_ERRADO", serial="M1", descricao="de novo"), REQ))

print("\n[3] Assumir: dono e prazo obrigatórios; atribuir a outro exige admin")
esperar(403, "não-admin não atribui a outra pessoa",
        lambda: reg.api_assumir(n1, reg.AssumirIn(responsavel="bruno"), REQ))
esperar(400, "prazo no passado é recusado",
        lambda: reg.api_assumir(n1, reg.AssumirIn(prazo=date.today() - timedelta(days=1)), REQ))
esperar(409, "resolver antes de assumir é recusado",
        lambda: reg.api_resolver(n1, reg.ResolverIn(resolucao="ENCONTRADO"), REQ))
d = reg.api_assumir(n1, reg.AssumirIn(), REQ)
checar(d["estado"] == "EX_TRATATIVA_REG" and d["responsavel"] == "ana", "assumida para si")
checar(d["prazo"] is not None, "prazo padrão aplicado")
checar(token(n1)["abertos"] == [("EX_TRATATIVA_REG", dbt.TRATATIVA, "ana")], "TRATATIVA com dono no núcleo")
checar("assumida por ana" in d["historico"], "histórico registrou")

SESSAO["is_admin"] = True
d = reg.api_assumir(n1, reg.AssumirIn(responsavel="bruno", prazo=date.today() + timedelta(days=3), nota="férias da ana"), REQ)
checar(d["responsavel"] == "bruno" and "reatribuída a bruno" in d["historico"], "admin reatribui")
checar(token(n1)["abertos"] == [("EX_TRATATIVA_REG", dbt.TRATATIVA, "bruno")], "relógio passou para o novo dono")
SESSAO["is_admin"] = False

print("\n[4] Anotar e resolver: só o dono (ou admin); perda exige justificativa")
d = reg.api_anotar(n1, reg.NotaIn(nota="liguei na loja"), REQ)
checar("liguei na loja" in d["historico"], "anotação entrou")
esperar(403, "quem não é dono nem admin não resolve",
        lambda: reg.api_resolver(n1, reg.ResolverIn(resolucao="ENCONTRADO"), REQ))
SESSAO["username"] = "bruno"
esperar(400, "resolução desconhecida é recusada",
        lambda: reg.api_resolver(n1, reg.ResolverIn(resolucao="TANTO_FAZ"), REQ))
esperar(400, "perda sem justificativa é recusada",
        lambda: reg.api_resolver(n1, reg.ResolverIn(resolucao="PERDA"), REQ))
d = reg.api_resolver(n1, reg.ResolverIn(resolucao="ENCONTRADO", detalhe="estava na gaiola"), REQ)
checar(d["estado"] == "RESOLVIDA_REG" and d["resolucao"] == "ENCONTRADO", "resolvida")
t = token(n1)
checar(t["encerrado"] and not t["abertos"], "token encerrado")
esperar(409, "anotar em encerrada é recusado", lambda: reg.api_anotar(n1, reg.NotaIn(nota="x"), REQ))

print("\n[5] Cancelar é admin; lista e filtros")
SESSAO["username"] = "ana"
esperar(403, "não-admin não cancela", lambda: reg.api_cancelar(n2, reg.CancelaIn(motivo="x"), REQ))
SESSAO["is_admin"] = True
d = reg.api_cancelar(n2, reg.CancelaIn(motivo="era duplicidade"), REQ)
checar(d["estado"] == "CANCELADA_REG", "cancelada por admin")
SESSAO["is_admin"] = False
lst = reg.api_lista(REQ)
checar(lst["contagem"]["RESOLVIDA_REG"] == 1 and lst["contagem"]["CANCELADA_REG"] == 1
       and lst["contagem"]["AG_TRATATIVA_REG"] == 1, "contagem por estado")
checar(lst["por_origem"] == [{"origem": "MANUAL", "rotulo": "Aberta à mão", "quantidade": 1}], "em aberto por origem")
checar(reg.api_lista(REQ, minhas=1)["divergencias"] == [], "'minhas' vazio para ana (nada em tratativa dela)")
checar(len(reg.api_lista(REQ, tipo="local_errado")["divergencias"]) == 1, "filtro por tipo")

print("\n[6] Invariante: um relógio aberto por token")
with dbt.SessionLocal() as s:
    ab = s.execute(select(dbt.Intervalo.ativo_id).where(dbt.Intervalo.fim.is_(None))).scalars().all()
checar(len(ab) == len(set(ab)), "um relógio aberto por ativo, no máximo")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Regularização íntegra.")
