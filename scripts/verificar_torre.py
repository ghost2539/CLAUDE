#!/usr/bin/env python3
"""Verificação da Torre: metas por etapa, % dentro da meta, snapshot diário e histórico.

Roda contra um banco temporário — não toca no banco do servidor:

    python3 scripts/verificar_torre.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TEMP = tempfile.mkdtemp(prefix="torre-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"

import db.trilha as dbt  # noqa: E402
from db.trilha import FILA, TRATATIVA  # noqa: E402
from routers.trilha import abrir_ativo, mover, encerrar  # noqa: E402
from routers.trilha import api_config_gravar, ConfigIn  # noqa: E402
import routers.torre as torre  # noqa: E402
from routers.torre import (  # noqa: E402
    api_area, api_metas, api_metas_gravar, MetaIn, gerar_snapshot,
    api_historico, metas_por_estado, _hora_snapshot,
)

UTC = timezone.utc
falhas: list[str] = []
feitos = 0


def checar(condicao: bool, descricao: str) -> None:
    global feitos
    feitos += 1
    print(f"  {'ok  ' if condicao else 'FALHA'} {descricao}")
    if not condicao:
        falhas.append(descricao)


# Sessão de admin falsa: o que interessa aqui é o cálculo, não o cookie.
ADMIN = {"username": "verificacao", "is_admin": True}
torre.require_permission = lambda req, modulo, acao="view": ADMIN
torre.check_rate_limit = lambda req, kind="api": None
import routers.trilha as rt  # noqa: E402
rt.require_permission = lambda req, modulo, acao="view": ADMIN
REQ = SimpleNamespace(cookies={}, headers={}, client=None)

dbt.init_db()
# Calendário corrido, fuso 0: horas úteis = horas de relógio (contas simples).
dbt.gravar_config({"expediente_dias": "", "fuso_horas": "0", "sla_horas": "48"})

# Instante fixo de referência para as contas: 2026-06-10 12:00 UTC.
AGORA = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
dbt.utcnow = lambda: AGORA
torre.dbt.utcnow = dbt.utcnow

# ── 1. Metas ────────────────────────────────────────────────────────
print("\nMetas por etapa")
lista = api_metas(REQ)["metas"]
checar(all(m["horas_uteis"] is None for m in lista), "sem metas gravadas, tudo vem vazio")
checar("DISPONIVEL" not in {m["estado"] for m in lista}, "DISPONIVEL (estoque) não recebe meta")

api_metas_gravar([MetaIn(estado="AG_TRIAGEM", horas_uteis=4),
                  MetaIn(estado="ag_pecas", horas_uteis=100),
                  MetaIn(estado="INEXISTENTE", horas_uteis=1)], REQ)
metas = metas_por_estado()
checar(metas == {"AG_TRIAGEM": 4 * 3600, "AG_PECAS": 100 * 3600},
       "grava em segundos úteis, normaliza caixa e ignora estado desconhecido")
api_metas_gravar([MetaIn(estado="AG_PECAS", horas_uteis=0)], REQ)
checar("AG_PECAS" not in metas_por_estado(), "meta zerada é removida")
api_metas_gravar([MetaIn(estado="AG_PECAS", horas_uteis=10, ativa=False)], REQ)
checar("AG_PECAS" not in metas_por_estado(), "meta inativa não conta")
checar(next(m for m in api_metas(REQ)["metas"] if m["estado"] == "AG_TRIAGEM")["horas_uteis"] == 4,
       "a tela lê de volta a meta gravada")

# ── 2. Área com meta por estado ────────────────────────────────────
print("\nPainel da área")
with dbt.SessionLocal() as s:
    # a: 6h em AG_TRIAGEM (meta 4h → fora); b: 2h (dentro); c: 30h em
    # AG_CONFIGURACAO sem meta (limite global 48h → dentro).
    a = abrir_ativo(s, serial="TOR-A", usuario="u")
    mover(s, a, estado="AG_TRIAGEM", tipo=FILA, quando=AGORA - timedelta(hours=6))
    b = abrir_ativo(s, serial="TOR-B", usuario="u")
    mover(s, b, estado="AG_TRIAGEM", tipo=FILA, quando=AGORA - timedelta(hours=2))
    c = abrir_ativo(s, serial="TOR-C", usuario="u")
    mover(s, c, estado="AG_CONFIGURACAO", tipo=FILA, quando=AGORA - timedelta(hours=30))
    # d: fechado hoje em 3h (na meta); e: fechado hoje em 9h (fora); f: fechado ontem.
    d = abrir_ativo(s, serial="TOR-D", usuario="u")
    mover(s, d, estado="AG_TRIAGEM", tipo=FILA, quando=AGORA - timedelta(hours=5))
    encerrar(s, d, estado="ENTREGUE", quando=AGORA - timedelta(hours=2))
    e = abrir_ativo(s, serial="TOR-E", usuario="u")
    mover(s, e, estado="AG_TRIAGEM", tipo=FILA, quando=AGORA - timedelta(hours=10))
    encerrar(s, e, estado="ENTREGUE", quando=AGORA - timedelta(hours=1))
    f = abrir_ativo(s, serial="TOR-F", usuario="u")
    mover(s, f, estado="AG_TRIAGEM", tipo=FILA, quando=AGORA - timedelta(hours=40))
    encerrar(s, f, estado="ENTREGUE", quando=AGORA - timedelta(hours=30))
    s.commit()

area = api_area(REQ)
tri = next(x for x in area["filas"] if x["estado"] == "AG_TRIAGEM")
cfg_ = next(x for x in area["filas"] if x["estado"] == "AG_CONFIGURACAO")
checar(tri["meta"] == 4 * 3600 and tri["limite"] == 4 * 3600, "etapa com meta usa a meta como limite")
checar(tri["em_alerta"] == 1, "6h em AG_TRIAGEM (meta 4h) está em alerta; 2h não")
checar(cfg_["meta"] is None and cfg_["limite"] == 48 * 3600, "etapa sem meta usa o limite global")
checar(cfg_["em_alerta"] == 0, "30h sem meta e limite 48h não está em alerta")
checar(area["em_curso"] == 3 and area["na_meta_pct"] == 67, "2 de 3 em curso dentro da meta → 67%")
checar(area["metas_definidas"] == 1, "conta só metas ativas")
par = next(p for p in area["parados"] if p["serial"] == "TOR-A")
checar(par["em_alerta"] and par["limite"] == 4 * 3600, "parado carrega o limite da própria etapa")

# ── 3. Snapshot ────────────────────────────────────────────────────
print("\nSnapshot diário")
n = gerar_snapshot(AGORA.date(), AGORA)
checar(n >= 2, "grava um registro por estado")
with dbt.SessionLocal() as s:
    linhas = {r.estado: r for r in s.query(dbt.Snapshot).filter_by(dia="2026-06-10").all()}
st = linhas["AG_TRIAGEM"]
checar(st.quantidade == 2 and st.fora_da_meta == 1 and st.mais_antigo == 6 * 3600,
       "AG_TRIAGEM: 2 abertos, 1 fora da meta, mais antigo 6h")
checar(st.fechados == 2 and st.fechados_na_meta == 1, "fechados hoje: 2 (3h e 9h), 1 na meta")
checar(st.fechados_media == 6 * 3600 and st.fechados_p90 == 9 * 3600, "média 6h, p90 9h")
checar(st.frente == "Bancada", "snapshot carrega a frente")
checar(linhas["AG_CONFIGURACAO"].quantidade == 1 and linhas["AG_CONFIGURACAO"].fora_da_meta == 0,
       "AG_CONFIGURACAO: 1 aberto, dentro do limite global")

n2 = gerar_snapshot(AGORA.date(), AGORA)
with dbt.SessionLocal() as s:
    total = s.query(dbt.Snapshot).filter_by(dia="2026-06-10").count()
checar(n2 == n and total == n, "rodar de novo no mesmo dia substitui, não duplica")

# Ontem: o fechado de ontem (10h, fora da meta) aparece lá, não hoje.
gerar_snapshot(AGORA.date() - timedelta(days=1), AGORA - timedelta(days=1))
with dbt.SessionLocal() as s:
    ontem = {r.estado: r for r in s.query(dbt.Snapshot).filter_by(dia="2026-06-09").all()}
checar(ontem["AG_TRIAGEM"].fechados == 1 and ontem["AG_TRIAGEM"].fechados_na_meta == 0,
       "fechamento de ontem entra no dia de ontem")

# ── 4. Histórico ───────────────────────────────────────────────────
print("\nHistórico")
h = api_historico(REQ, dias=7)
checar([x["dia"] for x in h["serie"]] == ["2026-06-09", "2026-06-10"], "série ordenada por dia")
hoje = h["serie"][-1]
checar(hoje["fila"] == 3 and hoje["fora_da_meta"] == 1 and hoje["na_meta_pct"] == 67,
       "dia de hoje consolida as etapas: fila 3, 1 fora, 67% na meta")
checar(hoje["fechados"] == 2 and hoje["fechados_na_meta_pct"] == 50, "fechados do dia: 2, 50% na meta")
checar({f["frente"] for f in h["frentes"]} == {"Bancada", "Preparação"}, "frentes presentes")
checar(h["comparacao"]["fechados"] == 3 and h["comparacao"]["fechados_anterior"] is None,
       "comparação 7×7 sem semana anterior devolve None")
checar(h["ultimo_snapshot"] == "2026-06-10", "último snapshot informado")
hb = api_historico(REQ, dias=7, frente="Bancada")
checar(hb["serie"][-1]["fila"] == 2, "filtro por frente restringe a série")
checar(api_historico(REQ, dias=-5)["dias"] == 1 and api_historico(REQ, dias=999)["dias"] == 180,
       "dias é limitado entre 1 e 180")

# ── 5. Horário do snapshot ─────────────────────────────────────────
print("\nHorário configurável")
checar(_hora_snapshot().strftime("%H:%M") == "23:55", "padrão 23:55")
api_config_gravar(ConfigIn(snapshot_hora="18:30"), REQ)
checar(_hora_snapshot().strftime("%H:%M") == "18:30", "configuração do calendário altera o horário")
try:
    api_config_gravar(ConfigIn(snapshot_hora="25:00"), REQ)
    checar(False, "horário inválido é recusado")
except Exception as exc:  # noqa: BLE001
    checar(getattr(exc, "status_code", 0) == 400, "horário inválido é recusado")
dbt.gravar_config({"snapshot_hora": "banana"})
checar(_hora_snapshot().strftime("%H:%M") == "23:55", "valor corrompido cai no padrão")


# ── 6. Fuso: o dia da foto é o civil local ─────────────────────────
print("\nFuso do calendário")
dbt.gravar_config({"fuso_horas": "-3"})
# 01:00 UTC do dia 11 = 22:00 do dia 10 em Brasília.
tarde = datetime(2026, 6, 11, 1, 0, tzinfo=UTC)
with dbt.SessionLocal() as s:
    s.query(dbt.Snapshot).delete(); s.commit()
gerar_snapshot(None, tarde)
with dbt.SessionLocal() as s:
    dias = sorted({r.dia for r in s.query(dbt.Snapshot).all()})
checar(dias == ["2026-06-10"], "foto às 22h local grava no dia local, não no UTC")
with dbt.SessionLocal() as s:
    st2 = {r.estado: r for r in s.query(dbt.Snapshot).filter_by(dia="2026-06-10").all()}["AG_TRIAGEM"]
checar(st2.fechados == 2, "janela de fechados do dia segue o fuso local")

print(f"\n{feitos - len(falhas)}/{feitos} verificações ok")
if falhas:
    print("FALHAS:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
