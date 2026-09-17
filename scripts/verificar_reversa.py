#!/usr/bin/env python3
"""Verificação da Logística Reversa (A17).

Bancos temporários, sem ServiceNow nem Correios — dublês no lugar:

    python3 scripts/verificar_reversa.py

O que se confere: a coleta só fecha conferida ou divergente; custódia
muda no bipe (ou no recebimento), não no "entregue" dos Correios; e o
que o núcleo recebe — fora da área é EXTERNO, conferência é TRATATIVA.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TEMP = tempfile.mkdtemp(prefix="reversa-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["SEPARACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/separacao.db"
os.environ["REVERSA_DATABASE_URL"] = f"sqlite:///{_TEMP}/reversa.db"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

import db.trilha as dbt  # noqa: E402
import db.separacao as dbsep  # noqa: E402
import db.reversa as db  # noqa: E402
import routers.separacao as sep  # noqa: E402
import routers.correios as cor  # noqa: E402
import routers.reversa as rev  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond: bool, descricao: str) -> None:
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def esperar(codigo: int, descricao: str, funcao) -> None:
    try:
        funcao()
    except HTTPException as exc:
        checar(exc.status_code == codigo, f"{descricao} (HTTP {exc.status_code})")
    else:
        checar(False, f"{descricao} (não recusou)")


# ── Dublês ─────────────────────────────────────────────────────────
sep.consultar_chamado = lambda req, numero, cfg=None: {
    "chamado": numero.upper(), "tipo": "INC", "resumo": "Devolução de coletores",
    "destino": "LOJA 123", "solicitante": "x", "estado": "2"}
rastreio = {"encontrado": False, "eventos": [], "entrega": None}
cor.consultar_rastreio = lambda codigo: dict(rastreio)
rev.require_permission = lambda req, modulo, acao: {"username": "tester"}
rev.check_rate_limit = lambda req: None
REQ = object()

dbt.init_db()
dbsep.init_db()
db.init_db()
dbt.gravar_config({"expediente_dias": "0,1,2,3,4,5,6", "expediente_inicio": "00:00",
                   "expediente_fim": "23:59", "feriados": "", "fuso_horas": "0"})


def token(serial: str):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
        if a is None:
            return None
        abertos = s.execute(select(dbt.Intervalo).where(
            dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
        return {"encerrado": a.encerrado,
                "abertos": [(i.estado, i.tipo, i.usuario) for i in abertos]}


# ══════════════════════════════════════════════════════════════════
print("\n[1] Abertura: nasce do chamado, com prazo acordado, fora da área (EXTERNO)")
esperar(400, "coleta sem esperado é recusada",
        lambda: rev.api_criar(rev.ColetaIn(chamado="INC1"), REQ))
d = rev.api_criar(rev.ColetaIn(chamado="inc1", motivo="troca",
                               itens=[rev.EsperadoIn(serial="abc1", modelo="TC21"),
                                      rev.EsperadoIn(etiqueta="et-9", modelo="TC21"),
                                      rev.EsperadoIn(serial="abc3")]), REQ)
NUM = d["numero"]
checar(NUM.startswith("COL-"), f"número {NUM}")
checar(d["loja"] == "LOJA 123", "loja veio do chamado")
checar(d["estado"] == "AG_POSTAGEM_REV", "aguardando postagem")
checar(d["prazo_acordado"] == (date.today() + timedelta(days=5)).isoformat(),
       "prazo acordado padrão = hoje + prazo_postagem_dias")
checar(d["comparacao"]["esperados"] == 3 and d["comparacao"]["recebidos"] == 0, "3 esperados, 0 recebidos")
checar(token(NUM)["abertos"] == [("AG_POSTAGEM_REV", dbt.EXTERNO, "")], "token no núcleo, EXTERNO")
esperar(409, "segunda coleta no mesmo chamado é recusada",
        lambda: rev.api_criar(rev.ColetaIn(chamado="INC1", itens=[rev.EsperadoIn(serial="x")]), REQ))

print("\n[2] Postagem e rastreio: 'entregue' nos Correios leva à fila, não fecha")
esperar(409, "rastrear sem código é recusado", lambda: rev.api_rastrear(NUM, REQ))
esperar(400, "código curto é recusado",
        lambda: rev.api_postagem(NUM, rev.PostagemIn(codigo_rastreio="ABC"), REQ))
d = rev.api_postagem(NUM, rev.PostagemIn(codigo_rastreio="aa123456789br"), REQ)
checar(d["estado"] == "EM_TRANSITO_REV" and d["codigo_rastreio"] == "AA123456789BR", "em trânsito com código")
checar(token(NUM)["abertos"][0][1] == dbt.EXTERNO, "trânsito é EXTERNO")
d = rev.api_rastrear(NUM, REQ)
checar("não consta" in d["ultimo_evento"], "sem evento ainda: diz que não consta")
rastreio.update({"encontrado": True, "eventos": [{"descricao": "Objeto em trânsito", "local": "POA", "data": "2026-09-10"}]})
d = rev.api_rastrear(NUM, REQ)
checar(d["estado"] == "EM_TRANSITO_REV" and "POA" in d["ultimo_evento"], "evento gravado, segue em trânsito")
rastreio.update({"eventos": [{"descricao": "Objeto entregue ao destinatário", "local": "CD", "data": "2026-09-12"}],
                 "entrega": {"entregue": True}})
d = rev.api_rastrear(NUM, REQ)
checar(d["estado"] == "AG_CONFERENCIA_REV", "entregue → fila de conferência (não encerra)")
checar(d["chegou_em"] is not None and d["prazo_conferencia"] is not None, "chegada e prazo de conferência registrados")
checar(token(NUM)["abertos"] == [("AG_CONFERENCIA_REV", dbt.FILA, "")], "fila do CD é FILA")
d = rev.api_rastrear(NUM, REQ)
checar(d["estado"] == "AG_CONFERENCIA_REV", "segundo 'entregue' é idempotente")

print("\n[3] Conferência: bipe casa por série OU etiqueta; sobra vira inesperado")
esperar(409, "bipar antes de assumir é recusado",
        lambda: rev.api_bipar(NUM, rev.BipeIn(serial="ABC1"), REQ))
d = rev.api_assumir(NUM, REQ)
checar(d["estado"] == "EX_CONFERENCIA_REV" and d["conferida_por"] == "tester", "conferência assumida")
checar(token(NUM)["abertos"] == [("EX_CONFERENCIA_REV", dbt.TRATATIVA, "tester")], "conferência é TRATATIVA com dono")
r = rev.api_bipar(NUM, rev.BipeIn(serial="abc1"), REQ)
checar(r["casou"] and r["comparacao"]["recebidos"] == 1, "série casou")
r = rev.api_bipar(NUM, rev.BipeIn(serial="ET-9"), REQ)
checar(r["casou"], "etiqueta casou com o esperado que só tinha etiqueta")
esperar(409, "mesma leitura duas vezes é recusada",
        lambda: rev.api_bipar(NUM, rev.BipeIn(serial="ABC1"), REQ))
r = rev.api_bipar(NUM, rev.BipeIn(serial="ZZZ9"), REQ)
checar(not r["casou"] and len(r["comparacao"]["inesperados"]) == 1, "série fora da lista vira inesperado")
cmp_ = r["comparacao"]
checar(len(cmp_["faltantes"]) == 1 and cmp_["faltantes"][0]["serial"] == "ABC3", "ABC3 segue faltante")
checar(cmp_["divergente"], "coleta está divergente")
esperar(400, "concluir divergente sem observação é recusado",
        lambda: rev.api_concluir(NUM, rev.ConclusaoIn(), REQ))
esperar(409, "cancelar coleta com recebido é recusado",
        lambda: rev.api_cancelar(NUM, rev.CancelaIn(motivo="x"), REQ))
d = rev.api_concluir(NUM, rev.ConclusaoIn(observacao="loja mandou um a menos e um errado"), REQ)
checar(d["estado"] == "DIVERGENTE_REV" and d["encerrada"], "fechou como DIVERGENTE")
t = token(NUM)
checar(t["encerrado"] and not t["abertos"], "token encerrado, sem relógio aberto")
esperar(409, "bipar depois de encerrada é recusado",
        lambda: rev.api_bipar(NUM, rev.BipeIn(serial="ABC3"), REQ))

print("\n[4] Recebimento (A01) casa sozinho e muda a custódia")
d2 = rev.api_criar(rev.ColetaIn(chamado="INC2", codigo_rastreio="BB123456789BR",
                                itens=[rev.EsperadoIn(serial="REC1"), rev.EsperadoIn(serial="REC2")]), REQ)
N2 = d2["numero"]
checar(d2["estado"] == "EM_TRANSITO_REV", "código na abertura: já nasce em trânsito")
res = rev.registrar_recebimento([{"serial": "rec1", "etiqueta": ""}, {"serial": "OUTRA", "etiqueta": ""}], "recebedor")
checar(res["casados"] == 1 and res["coletas"] == [N2], "casou REC1 e só ela")
d2 = rev.api_detalhe(N2, REQ)
checar(d2["estado"] == "AG_CONFERENCIA_REV", "recebimento físico levou a coleta à fila de conferência")
checar(d2["comparacao"]["ok"][0]["origem"] == "RECEBIMENTO", "origem da leitura é o recebimento")
res = rev.registrar_recebimento([{"serial": "REC1"}], "recebedor")
checar(res["casados"] == 0, "recebimento repetido não duplica")
rev.api_assumir(N2, REQ)
rev.api_bipar(N2, rev.BipeIn(serial="REC2"), REQ)
d2 = rev.api_concluir(N2, rev.ConclusaoIn(), REQ)
checar(d2["estado"] == "CONFERIDA_REV", "tudo chegou: CONFERIDA sem exigir observação")

print("\n[5] Cancelar e listar")
d3 = rev.api_criar(rev.ColetaIn(chamado="INC3", itens=[rev.EsperadoIn(serial="C1")]), REQ)
d3 = rev.api_cancelar(d3["numero"], rev.CancelaIn(motivo="loja desistiu"), REQ)
checar(d3["estado"] == "CANCELADA_REV" and token(d3["numero"])["encerrado"], "cancelada e token encerrado")
lst = rev.api_lista(REQ)
checar(lst["contagem"]["DIVERGENTE_REV"] == 1 and lst["contagem"]["CONFERIDA_REV"] == 1
       and lst["contagem"]["CANCELADA_REV"] == 1, "contagem por estado")
checar(lst["faltantes_abertos"] == 1, "1 faltante em aberto (ABC3)")

print("\n[6] 'Chegou sem rastreio': assumir direto de AG_POSTAGEM vale como chegada")
d4 = rev.api_criar(rev.ColetaIn(chamado="INC4", itens=[rev.EsperadoIn(serial="D1")]), REQ)
d4 = rev.api_assumir(d4["numero"], REQ)
checar(d4["estado"] == "EX_CONFERENCIA_REV" and d4["chegou_em"] is not None, "chegada registrada e conferência assumida")

print("\n[7] Invariante: nenhum token com dois relógios abertos")
with dbt.SessionLocal() as s:
    abertos = s.execute(select(dbt.Intervalo.ativo_id).where(dbt.Intervalo.fim.is_(None))).scalars().all()
checar(len(abertos) == len(set(abertos)), "um relógio aberto por ativo, no máximo")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:")
    for f_ in falhas:
        print("  - " + f_)
    sys.exit(1)
print("Logística reversa íntegra.")
