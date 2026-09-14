#!/usr/bin/env python3
"""Verificação da porta de entrada: venda direta ou triagem.

    python3 scripts/verificar_recebimento_triagem.py

O Recebimento é a porta do Spare e define o próximo destino do ativo.
Venda direta vai esperar o ciclo de venda; triagem exige subcategoria
válida, e é a subcategoria que escolhe o backlog da bancada.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="rec-tri-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")

from fastapi import HTTPException     # noqa: E402
from sqlalchemy import select         # noqa: E402
import db.trilha as dbt               # noqa: E402
from db.portal import SessionLocal, Setting, Base, engine  # noqa: E402
import routers.recebimento as rec     # noqa: E402

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


Base.metadata.create_all(engine)
dbt.init_db()
rec.require_permission = lambda req, m, a: {"username": "recebedor"}
rec.check_rate_limit = lambda req: None
# Os ganchos de fora do recebimento têm suíte própria; aqui interessa a rota.
rec._config_servicenow = lambda: {"ativo": False}
rec._marcar_no_servicenow = lambda itens, req, espaco="": {"ativo": False}
rec._remover_do_mdm = lambda itens, usuario: {"tentados": 0, "removidos": 0, "pendentes": 0}
rec._casar_com_coleta = lambda itens, usuario: {"casados": 0, "coletas": []}
rec._registrar_mdm_no_ciclo = lambda itens, resultado, usuario: 0
REQ = object()

_n = [0]


def item(**campos):
    _n[0] += 1
    base = {"empresa": "1", "ativo": f"A{_n[0]}", "etiqueta": f"RN{_n[0]}",
            "numero_serie": f"SN{_n[0]}", "modelo": "Zebra TC21",
            "categoria": "Coletor", "descricao": "coletor"}
    base.update(campos)
    return rec.BulkSubmitItem(**base)


def enviar(*itens):
    return rec.receipt_bulk_submit(rec.BulkSubmitIn(items=list(itens)), REQ)


def estado(serial):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(
            dbt.Ativo.serial == serial.upper())).scalar_one_or_none()
        return a.estado_fisico if a else ""


print("\n[1] Subcategorias vêm de lista, com a bancada de cada uma")
d = rec.api_subcategorias(REQ)
nomes = {x["nome"] for x in d["subcategorias"]}
checar({"Coletor", "PDV", "Switch"} <= nomes, "a lista padrão cobre as três bancadas")
checar({x["valor"] for x in d["destinos"]} == {"VENDA", "TRIAGEM"},
       "os destinos de entrada são venda direta e triagem")
checar(rec.familia_da_subcategoria("coletor") == "frota", "casa sem depender de caixa")
checar(rec.familia_da_subcategoria("Balança") == "loja", "e sem depender de acento")
checar(rec.familia_da_subcategoria("Nave espacial") == "", "o que não está na lista não passa")

print("\n[2] Triagem manda para o backlog que a subcategoria diz")
i1 = item(subcategoria="Coletor")
i2 = item(subcategoria="PDV")
i3 = item(subcategoria="Switch")
r = enviar(i1, i2, i3)
checar(r["criados"] == 3, f"os três entraram ({r['criados']})")
checar(estado(i1.numero_serie) == "AG_TRIAGEM", "coletor cai no backlog de Mobilidade")
checar(estado(i2.numero_serie) == "AG_TRIAGEM", "PDV cai no backlog de Frente e Retaguarda")
checar(estado(i3.numero_serie) == "AG_TRIAGEM_CONECT", "switch cai no backlog de Conectividade")

print("\n[3] Venda direta pula o reparo")
i4 = item(destino_entrada="VENDA")
enviar(i4)
checar(estado(i4.numero_serie) == "AG_VENDA", "vai direto para a fila de venda")

print("\n[4] Triagem sem subcategoria válida não passa — e nada é gravado")
i5 = item(subcategoria="")
esperar(400, "triagem sem subcategoria", lambda: enviar(i5))
i6 = item(subcategoria="Nave espacial")
esperar(400, "subcategoria fora da lista", lambda: enviar(i6))
i7 = item(destino_entrada="QUALQUER", subcategoria="Coletor")
esperar(400, "destino de entrada inventado", lambda: enviar(i7))
checar(estado(i5.numero_serie) == "" and estado(i6.numero_serie) == "",
       "a recusa vem antes de gravar: nenhum ativo entrou na trilha")

print("\n[5] O que o Recebimento decidiu fica gravado na movimentação")
with dbt.SessionLocal() as s:
    a = s.execute(select(dbt.Ativo).where(
        dbt.Ativo.serial == i2.numero_serie.upper())).scalar_one()
    mov = s.execute(select(dbt.Movimentacao).where(
        dbt.Movimentacao.ativo_id == a.id)).scalars().all()
detalhes = " ".join(m.detalhe or "" for m in mov)
checar("PDV" in detalhes and "TRIAGEM" in detalhes,
       "destino e subcategoria ficam no detalhe da movimentação")

print("\n[6] A área pode trocar a lista sem release")
with SessionLocal.begin() as s:
    s.add(Setting(key=rec.CONFIG_SUBCATEGORIAS,
                  value={"subcategorias": [{"nome": "Totem", "familia": "conectividade"}]}))
checar([x["nome"] for x in rec.config_subcategorias()] == ["Totem"], "a lista configurada vale")
checar(rec.familia_da_subcategoria("Totem") == "conectividade", "e leva à bancada escolhida")
esperar(400, "subcategoria antiga deixa de valer",
        lambda: enviar(item(subcategoria="Coletor")))
i8 = item(subcategoria="Totem")
enviar(i8)
checar(estado(i8.numero_serie) == "AG_TRIAGEM_CONECT", "o ativo segue a lista nova")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Porta de entrada íntegra.")
