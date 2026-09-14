#!/usr/bin/env python3
"""Verificação da Central de Reparos — backlog, despacho e medição.

    python3 scripts/verificar_bancada.py

O que precisa ser verdade depois de acabar com o "assumir equipamento":
o ativo cai no backlog, o relógio corre lá, e o registro do que foi
feito é a SAÍDA — despacha para venda, assistência ou internalização no
mesmo ato. O que se mede é o tempo parado e o volume por pessoa.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="bnc-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["BANCADA_DATABASE_URL"] = f"sqlite:///{_TEMP}/bancada.db"

from fastapi import HTTPException            # noqa: E402
from sqlalchemy import select                # noqa: E402
import db.trilha as dbt                      # noqa: E402
import db.bancada as db                      # noqa: E402
import routers.bancada as bnc                # noqa: E402
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


bnc.require_permission = lambda req, m, a: {"username": "tecnico1"}
bnc.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
db.init_db()
# Relógio corrido (sem expediente configurado): o teste mede duração, não
# calendário — o calendário tem suíte própria na trilha.
dbt.gravar_config({"expediente_dias": "", "expediente_inicio": "00:00",
                   "expediente_fim": "00:00", "feriados": "", "fuso_horas": "0"})

with db.SessionLocal() as s:
    causa = s.execute(select(db.Causa)).scalars().first()
    if causa is None:
        causa = db.Causa(nome="Não liga", ativa=True)
        s.add(causa)
        s.commit()
    CAUSA_ID = causa.id


def no_backlog(serial, estado="AG_TRIAGEM", horas_atras=0):
    """Põe o ativo no backlog da bancada, opcionalmente parado há N horas."""
    with dbt.SessionLocal() as s:
        a = abrir_ativo(s, serial=serial, usuario="rec", modelo="TC21")
        mover(s, a, estado=estado, tipo=dbt.FILA, processo="A01", usuario="rec")
        s.commit()
    if horas_atras:
        with dbt.SessionLocal() as s:
            a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial.upper())).scalar_one()
            i = s.execute(select(dbt.Intervalo).where(
                dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().first()
            i.inicio = dbt.utcnow() - timedelta(hours=horas_atras)
            s.commit()


def estado(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial.upper())).scalar_one()
        return a.estado_fisico


def registro(serial, **campos):
    corpo = {"serial": serial, "bancada": db.LOJA, "acoes": "trocou fonte",
             "causa_id": CAUSA_ID, "pecas": "N/A", "destino": db.INTERNALIZACAO}
    corpo.update(campos)
    return bnc.api_registrar(bnc.RegistroIn(**corpo), REQ)


print("\n[1] Não existe mais assumir equipamento")
checar(not hasattr(bnc, "api_bipar"), "o endpoint de assumir saiu do módulo")
no_backlog("S1", horas_atras=3)
d = bnc.api_fila(REQ, bancada=db.LOJA)
checar([x["serial"] for x in d["aguardando"]] == ["S1"], "o ativo aparece no backlog sem ninguém puxar")
checar(d["aguardando"][0]["segundos"] >= 3 * 3600 - 5, "o relógio conta desde que ele caiu no backlog")

print("\n[2] O registro é a saída: despacha no mesmo ato")
r = registro("S1")
checar(r["proximo_estado"] == "AG_INTERNALIZACAO", "internalização manda para AG_INTERNALIZACAO")
checar(estado("S1") == "AG_INTERNALIZACAO", "o ativo saiu do backlog")
checar(not bnc.api_fila(REQ, bancada=db.LOJA)["aguardando"], "e o backlog esvaziou")
with db.SessionLocal() as s:
    rep = s.execute(select(db.Reparo).where(db.Reparo.serial == "S1")).scalar_one()
    parado = (rep.fechado_em - rep.aberto_em).total_seconds()
checar(rep.fechado_em is not None, "o reparo fecha no despacho")
checar(parado >= 3 * 3600 - 60, f"o tempo parado conta desde o backlog ({parado:.0f}s)")
checar(rep.tecnico == "tecnico1" and rep.modelo == "TC21", "quem despachou e o modelo ficam gravados")

print("\n[3] Registrar o que foi feito é obrigatório, em qualquer destino")
no_backlog("S2")
esperar(400, "sem descrever o que foi feito", lambda: registro("S2", acoes=" "))
esperar(400, "sem informar peças", lambda: registro("S2", pecas=""))
esperar(400, "venda sem justificativa", lambda: registro("S2", destino=db.VENDA, justificativa=""))
esperar(400, "assistência sem fornecedor", lambda: registro("S2", destino=db.ASSISTENCIA))
esperar(400, "peça sem dizer qual", lambda: registro("S2", destino=db.AGUARDANDO_PECAS))
esperar(400, "causa inexistente", lambda: registro("S2", causa_id=9999))
checar(estado("S2") == "AG_TRIAGEM", "nenhuma recusa moveu o ativo")

print("\n[4] Os três destinos da bancada")
registro("S2", destino=db.VENDA, justificativa="carcaça quebrada, sem valor de reparo")
checar(estado("S2") == "AG_VENDA", "venda vai para a fila de venda")
no_backlog("S3")
registro("S3", destino=db.ASSISTENCIA, fornecedor="Zebra BR")
checar(estado("S3") == "AG_ASSISTENCIA", "assistência externa vai para a fila da assistência")
no_backlog("S4", estado="AG_TRIAGEM_CONECT")
r = bnc.api_registrar(bnc.RegistroIn(
    serial="S4", bancada=db.CONECTIVIDADE, acoes="firmware", causa_id=CAUSA_ID,
    pecas="N/A", destino=db.INTERNALIZACAO), REQ)
checar(estado("S4") == "AG_INTERNALIZACAO", "conectividade destina para os mesmos lugares")

print("\n[5] Aguardando peça é pausa, não destino")
no_backlog("S5")
registro("S5", destino=db.AGUARDANDO_PECAS, pecas_aguardadas="bateria")
checar(estado("S5") == "AG_PECAS", "o ativo fica aguardando peça")
with db.SessionLocal() as s:
    rep5 = s.execute(select(db.Reparo).where(db.Reparo.serial == "S5")).scalar_one()
checar(rep5.fechado_em is None, "o reparo continua aberto: é o mesmo trabalho")
bnc.api_retornar_peca(bnc.RetornoIn(serial="S5", bancada=db.LOJA), REQ)
checar(estado("S5") == "AG_TRIAGEM", "a peça chegou e o ativo volta para o backlog")
with dbt.SessionLocal() as s:
    a5 = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == "S5")).scalar_one()
    i5 = s.execute(select(dbt.Intervalo).where(
        dbt.Intervalo.ativo_id == a5.id, dbt.Intervalo.fim.is_(None))).scalars().first()
checar(i5.tipo == dbt.FILA, "e volta como fila, não como custódia de ninguém")

print("\n[6] Série que nunca passou pelo Recebimento é adotada no registro")
registro("NOVA-NA-MAO", destino=db.INTERNALIZACAO)
checar(estado("NOVA-NA-MAO") == "AG_INTERNALIZACAO", "adotou e despachou")

print("\n[7] O painel mede tempo parado e volume")
d = bnc.api_dashboard(REQ)
checar(d["total"] >= 5, f"conta os equipamentos despachados ({d['total']})")
checar(d["medio_parado"] > 0, "tempo parado médio sai do relógio do núcleo")
checar("saving" not in d, "não há mais saving por hora de bancada: ninguém assume equipamento")
pessoa = [x for x in d["por_tecnico"] if x["chave"] == "tecnico1"]
checar(bool(pessoa) and pessoa[0]["dias"] >= 1 and pessoa[0]["por_dia"] > 0,
       "volume por pessoa traz quantidade, dias e média por dia")
checar(len(d["por_dia"]) >= 1 and sum(x["quantidade"] for x in d["por_dia"]) == d["total"],
       "a série diária fecha com o total")
checar(len(d["por_mes"]) >= 1 and sum(x["quantidade"] for x in d["por_mes"]) == d["total"],
       "a série mensal fecha com o total")
destinos = {x["chave"] for x in d["por_destino"]}
checar({db.VENDA, db.ASSISTENCIA, db.INTERNALIZACAO} <= destinos,
       "o painel separa por destino")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Central de Reparos íntegra.")
