#!/usr/bin/env python3
"""Verificação da Venda de Ativos (A11) — fila, ciclo trimestral e baixa.

    python3 scripts/verificar_venda.py

O que precisa ser verdade: o ativo cai na fila sozinho, o relógio corre
até alguém incluí-lo num ciclo, e a baixa só acontece com comprador e
documento. Concluir encerra o ativo na Trilha — é isso que para o
relógio de verdade.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="vnd-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["VENDA_DATABASE_URL"] = f"sqlite:///{_TEMP}/venda.db"

from fastapi import HTTPException            # noqa: E402
from sqlalchemy import select                # noqa: E402
import db.trilha as dbt                      # noqa: E402
import db.venda as db                        # noqa: E402
import routers.venda as vnd                  # noqa: E402
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


vnd.require_permission = lambda req, m, a: {"username": "vendedor"}
vnd.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
db.init_db()
dbt.gravar_config({"expediente_dias": "", "expediente_inicio": "00:00",
                   "expediente_fim": "00:00", "feriados": "", "fuso_horas": "0"})


def na_fila(serial, dias_atras=0, origem="RECEBIMENTO"):
    with dbt.SessionLocal() as s:
        a = abrir_ativo(s, serial=serial, usuario="rec", modelo="Zebra TC21",
                        origem=origem)
        mover(s, a, estado=vnd.FILA, tipo=dbt.FILA, processo="A01", usuario="rec")
        s.commit()
    if dias_atras:
        with dbt.SessionLocal() as s:
            a = s.execute(select(dbt.Ativo).where(
                dbt.Ativo.serial == serial.upper())).scalar_one()
            i = s.execute(select(dbt.Intervalo).where(
                dbt.Intervalo.ativo_id == a.id,
                dbt.Intervalo.fim.is_(None))).scalars().first()
            i.inicio = dbt.utcnow() - timedelta(days=dias_atras)
            s.commit()


def ativo(serial):
    with dbt.SessionLocal() as s:
        return s.execute(select(dbt.Ativo).where(
            dbt.Ativo.serial == serial.upper())).scalar_one_or_none()


print("\n[1] A fila se enche sozinha e mostra há quanto tempo cada um espera")
na_fila("V1", dias_atras=200)
na_fila("V2")
d = vnd.api_fila(REQ)
checar([x["serial"] for x in d["fila"]] == ["V1", "V2"], "do mais antigo ao mais novo")
checar(d["fila"][0]["segundos"] >= 199 * 86400, "o relógio conta desde a entrada na fila")
checar(d["envelhecidos"] == 1, "quem passou do ciclo é sinalizado")

print("\n[2] Ciclo trimestral: um por trimestre, um aberto por vez")
c1 = vnd.api_ciclo_abrir(vnd.CicloIn(trimestre="2026-T1"), REQ)
checar(c1["trimestre"] == "2026-T1" and c1["estado"] == "ABERTO", "o ciclo abre")
esperar(409, "segundo ciclo aberto ao mesmo tempo",
        lambda: vnd.api_ciclo_abrir(vnd.CicloIn(trimestre="2026-T2"), REQ))
esperar(400, "trimestre com formato inválido",
        lambda: vnd.api_ciclo_abrir(vnd.CicloIn(trimestre="2026-T9"), REQ))
checar(db.trimestre_de(__import__("datetime").date(2026, 8, 3)) == "2026-T3",
       "o rótulo do trimestre sai da data")

print("\n[3] Incluir no ciclo tira o ativo da fila — sem ninguém assumir nada")
vnd.api_incluir(c1["id"], vnd.ItemIn(serial="V1", valor=150), REQ)
checar(ativo("V1").estado_fisico == vnd.NO_CICLO, "o ativo sai da fila e entra no ciclo")
checar([x["serial"] for x in vnd.api_fila(REQ)["fila"]] == ["V2"], "a fila diminui")
esperar(409, "incluir a mesma série duas vezes",
        lambda: vnd.api_incluir(c1["id"], vnd.ItemIn(serial="V1"), REQ))
esperar(400, "incluir sem informar série",
        lambda: vnd.api_incluir(c1["id"], vnd.ItemIn(serial="  "), REQ))
with dbt.SessionLocal() as s:
    a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == "FORA")).scalar_one_or_none()
na_fila("FORA_DA_FILA")
with dbt.SessionLocal() as s:
    a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == "FORA_DA_FILA")).scalar_one()
    mover(s, a, estado="AG_TRIAGEM", tipo=dbt.FILA, processo="A02", usuario="t")
    s.commit()
esperar(409, "incluir ativo que não está na fila de venda",
        lambda: vnd.api_incluir(c1["id"], vnd.ItemIn(serial="FORA_DA_FILA"), REQ))

print("\n[4] Tirar do ciclo devolve o ativo à fila")
item_id = vnd.api_ciclo(c1["id"], REQ)["itens"][0]["id"]
vnd.api_remover(c1["id"], item_id, REQ)
checar(ativo("V1").estado_fisico == vnd.FILA, "o ativo volta a aguardar venda")
# Voltar para a fila reabre o relógio agora; envelhece de novo para medir
# a espera até a venda no passo [8].
with dbt.SessionLocal() as s:
    _a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == "V1")).scalar_one()
    _i = s.execute(select(dbt.Intervalo).where(
        dbt.Intervalo.ativo_id == _a.id, dbt.Intervalo.fim.is_(None))).scalars().first()
    _i.inicio = dbt.utcnow() - timedelta(days=100)
    s.commit()
vnd.api_incluir(c1["id"], vnd.ItemIn(serial="V1", valor=150), REQ)
vnd.api_incluir(c1["id"], vnd.ItemIn(serial="V2", valor=90), REQ)

print("\n[5] Concluir exige comprador e documento")
vnd.api_estado(c1["id"], vnd.EstadoIn(estado="NEGOCIACAO"), REQ)
checar(vnd.api_ciclo(c1["id"], REQ)["estado"] == "NEGOCIACAO", "o ciclo fecha para negociação")
esperar(409, "incluir ativo em ciclo que já está em negociação",
        lambda: vnd.api_incluir(c1["id"], vnd.ItemIn(serial="FORA_DA_FILA"), REQ))
esperar(400, "concluir sem comprador",
        lambda: vnd.api_concluir(c1["id"], vnd.ConclusaoIn(comprador=" ", documento="NF 1"), REQ))
esperar(400, "concluir sem documento",
        lambda: vnd.api_concluir(c1["id"], vnd.ConclusaoIn(comprador="Sucata SA"), REQ))
checar(not ativo("V1").encerrado, "nenhuma recusa baixou ativo")

print("\n[6] A venda baixa os ativos: encerramento na Trilha")
r = vnd.api_concluir(c1["id"], vnd.ConclusaoIn(
    comprador="Sucata SA", documento="NF 1234", valor_total=240), REQ)
checar(r["baixados"] == 2 and not r["falhas"], f"os dois ativos foram baixados ({r['baixados']})")
checar(ativo("V1").encerrado and ativo("V1").estado_fisico == "VENDIDO", "V1 encerrado como vendido")
checar(ativo("V2").encerrado, "V2 também")
with dbt.SessionLocal() as s:
    a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == "V1")).scalar_one()
    abertos = s.execute(select(dbt.Intervalo).where(
        dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
checar(not abertos, "o relógio parou: nenhum intervalo aberto")
checar(all(i["baixado"] for i in vnd.api_ciclo(c1["id"], REQ)["itens"]), "os itens ficam marcados")
esperar(409, "concluir de novo", lambda: vnd.api_concluir(
    c1["id"], vnd.ConclusaoIn(comprador="Outro", documento="NF 2"), REQ))
esperar(409, "reabrir ciclo concluído",
        lambda: vnd.api_estado(c1["id"], vnd.EstadoIn(estado="ABERTO"), REQ))

print("\n[7] Ciclo cancelado devolve os ativos para a fila")
na_fila("V3")
c2 = vnd.api_ciclo_abrir(vnd.CicloIn(trimestre="2026-T2"), REQ)
vnd.api_incluir(c2["id"], vnd.ItemIn(serial="V3"), REQ)
vnd.api_estado(c2["id"], vnd.EstadoIn(estado="CANCELADO"), REQ)
checar(ativo("V3").estado_fisico == vnd.FILA, "o ativo cancelado volta a aguardar venda")
esperar(409, "concluir ciclo cancelado", lambda: vnd.api_concluir(
    c2["id"], vnd.ConclusaoIn(comprador="X", documento="NF 3"), REQ))

print("\n[8] Painel: volume vendido e espera até a venda")
d = vnd.api_dashboard(REQ)
checar(d["ciclos"] == 1 and d["itens"] == 2, "conta o ciclo concluído e seus ativos")
checar(d["valor_total"] == 240, "soma o valor da venda")
checar(d["espera_media"] >= 49 * 86400, f"a espera média sai do núcleo ({d['espera_media']}s)")
checar(d["por_ciclo"][0]["trimestre"] == "2026-T1", "a lista traz o ciclo")

print("\n[9] Ciclo vazio não conclui")
c3 = vnd.api_ciclo_abrir(vnd.CicloIn(trimestre="2026-T3"), REQ)
esperar(409, "concluir ciclo sem ativos", lambda: vnd.api_concluir(
    c3["id"], vnd.ConclusaoIn(comprador="X", documento="NF 4"), REQ))

print("\n[10] Documento pode deixar de ser obrigatório por configuração")
db.gravar_config({"exigir_documento": "0"})
vnd.api_incluir(c3["id"], vnd.ItemIn(serial="V3"), REQ)
r = vnd.api_concluir(c3["id"], vnd.ConclusaoIn(comprador="Leilão"), REQ)
checar(r["baixados"] == 1, "sem exigir documento, conclui")
db.gravar_config({"exigir_documento": "1"})

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Venda de Ativos íntegra.")
