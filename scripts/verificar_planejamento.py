#!/usr/bin/env python3
"""Verificação do Planejamento de compras (Orçamento Spare).

Roda contra bancos temporários — não toca no banco do servidor:

    python3 scripts/verificar_planejamento.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TEMP = tempfile.mkdtemp(prefix="pln-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
for m in ("PLANEJAMENTO", "SEPARACAO", "TRILHA"):
    os.environ[f"{m}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{m.lower()}.db"

from core.previsao import prever, necessidade, meses_entre, mes_seguinte  # noqa: E402
import db.planejamento as db  # noqa: E402
import db.separacao as dsep  # noqa: E402
import routers.planejamento as rp  # noqa: E402
from routers.planejamento import (  # noqa: E402
    ItemIn, ConfigIn, HistoricoIn, criar_item, alterar_item, excluir_item, listar_itens,
    listar_modelos, ler_configuracao, gravar_configuracao, historico, gravar_historico,
    previsao, consumo_real_por_modelo,
)

UTC = timezone.utc
falhas: list[str] = []
feitos = 0


def checar(cond: bool, desc: str) -> None:
    global feitos
    feitos += 1
    print(f"  {'ok  ' if cond else 'FALHA'} {desc}")
    if not cond:
        falhas.append(desc)


def erro_http(desc: str, fn, status: int = 400) -> None:
    try:
        fn()
        checar(False, f"{desc} (não recusou)")
    except Exception as exc:  # noqa: BLE001
        checar(getattr(exc, "status_code", 0) == status, desc)


# ── 1. Motor de previsão ───────────────────────────────────────────
print("\nPrevisão (funções puras)")
checar(mes_seguinte("2026-12") == "2027-01" and mes_seguinte("2026-01") == "2026-02", "mês seguinte vira o ano")
checar(len(meses_entre("2025-11", "2026-02")) == 4, "meses_entre inclui as pontas")

p = prever([], 3)
checar(p["metodo"] == "sem histórico" and p["p50"] == [0, 0, 0], "sem histórico prevê zero e diz isso")
p = prever([("2026-01", 10), ("2026-02", 14)], 2)
checar(p["metodo"].startswith("média simples") and p["p50"] == [12, 12], "2 meses: média simples")
checar(p["meses"] == ["2026-03", "2026-04"], "meses previstos seguem o último do histórico")

plano = [("2025-%02d" % m, 10) for m in range(1, 13)]
p = prever(plano, 3)
checar(all(abs(v - 10) < 0.01 for v in p["p50"]) and p["desvio"] == 0, "série constante prevê a constante, sem desvio")
checar(p["p90"] == p["p50"], "sem erro de backtest, P90 = P50")

crescente = [("2025-%02d" % m, 10 + m) for m in range(1, 13)]
p = prever(crescente, 6)
checar(p["p50"][0] > 21 and p["p50"][5] > p["p50"][0], "tendência de alta continua subindo")
checar(p["p50"][5] - p["p50"][4] < p["p50"][1] - p["p50"][0], "…mas amortecida")

sazonal = []
for ano in (2024, 2025):
    for m in range(1, 13):
        sazonal.append((f"{ano}-{m:02d}", 30 if m in (11, 12) else 10))
p = prever(sazonal, 12)
checar("sazonalidade" in p["metodo"], "24 meses ativa sazonalidade")
checar(p["p50"][10] > 2 * p["p50"][0] and p["p50"][11] > 2 * p["p50"][0], "novembro e dezembro previstos bem acima de janeiro")
checar(abs(p["p50"][0] - 10) < 1.5, "janeiro previsto perto do nível fora de pico")
checar(all(v >= 0 for v in prever([("2025-%02d" % m, max(0, 12 - m * 2)) for m in range(1, 13)], 6)["p50"]),
       "queda forte não prevê consumo negativo")

prev = {"meses": ["2026-01", "2026-02", "2026-03"], "p50": [10, 10, 10], "p90": [14, 14, 14]}
n = necessidade(prev, estoque=25, pedidos_abertos=0, lead_time_dias=20, seguranca_dias=15, custo_unitario=100)
checar(n["seguranca_unidades"] == 5, "segurança = média × dias/30")
checar(n["necessidade"] == 10 and n["necessidade_p90"] == 22, "necessidade P50/P90")
checar(n["valor"] == 1000.0, "valor = necessidade × custo")
checar(n["mes_ruptura"] == "2026-03" and n["data_limite_pedido"] == "2026-02-09", "ruptura em março, pedido até 9/2 (20 dias antes)")
checar(n["cobertura_meses"] == 2.5, "cobertura em meses de consumo")
n2 = necessidade(prev, estoque=25, pedidos_abertos=20, lead_time_dias=20, seguranca_dias=15)
checar(n2["necessidade"] == 0 and n2["mes_ruptura"] is None, "pedidos em aberto cobrem o horizonte")

# ── 2. Bancos e sessão ─────────────────────────────────────────────
print("\nItens e configuração")
db.init_db()
dsep.init_db()
ADMIN = {"username": "planejador", "is_admin": True}
LEITOR = {"username": "leitor", "is_admin": False, "permission_map": {"orcamento_spare": {"can_view": True}}}
_sessao = {"atual": ADMIN}
rp.get_session = lambda req, required=True: _sessao["atual"]
rp.check_rate_limit = lambda req, kind="api": None
REQ = SimpleNamespace(cookies={}, headers={}, client=None)

it = criar_item(ItemIn(nome="PDV Dell 3050", modelos=["PDV Dell 3050", "PDV Dell 3050 "], lead_time_dias=45,
                       seguranca_dias=30, custo_unitario=3200, estoque_atual=12, pedidos_abertos=5), REQ)
checar(it["modelos"] == ["PDV Dell 3050"], "modelos deduplicados e sem espaço sobrando")
checar(it["estoque_origem"] == "manual" and it["estoque_atualizado_em"], "estoque manual marca origem e data")
it2 = criar_item(ItemIn(nome="Coletor TC21", modelos=["Coletor Zebra TC21"], custo_unitario=2500, estoque_atual=3), REQ)
erro_http("nome repetido é recusado", lambda: criar_item(ItemIn(nome="pdv dell 3050"), REQ), 409)
erro_http("nome vazio é recusado", lambda: criar_item(ItemIn(nome="  "), REQ), 400)
erro_http("estoque negativo é recusado", lambda: alterar_item(it["id"], ItemIn(estoque_atual=-1), REQ), 400)
_sessao["atual"] = LEITOR
erro_http("quem só lê não cria item", lambda: criar_item(ItemIn(nome="X"), REQ), 403)
checar(len(listar_itens(REQ)["itens"]) == 2, "quem só lê lista os itens")
_sessao["atual"] = ADMIN

cfg = ler_configuracao(REQ)
checar(cfg["horizonte_meses"] == 12 and cfg["data_inicio_sistema"] == "" and not cfg["data_inicio_configurada"],
       "configuração padrão: 12 meses, sem início (Separação vazia)")
erro_http("data de início inválida", lambda: gravar_configuracao(ConfigIn(data_inicio_sistema="2026-13"), REQ))
erro_http("horizonte fora da faixa", lambda: gravar_configuracao(ConfigIn(horizonte_meses=0), REQ))

# ── 3. Consumo real vindo da Separação ─────────────────────────────
print("\nConsumo real (Separação)")
hoje = date.today()
mes_atual = f"{hoje.year:04d}-{hoje.month:02d}"
def mes_menos(ym, k):
    for _ in range(k):
        a, m = int(ym[:4]), int(ym[5:7])
        ym = f"{a - 1:04d}-12" if m == 1 else f"{a:04d}-{m - 1:02d}"
    return ym
m1, m2, m3 = mes_menos(mes_atual, 1), mes_menos(mes_atual, 2), mes_menos(mes_atual, 3)
def dt(ym, dia=10):
    return datetime(int(ym[:4]), int(ym[5:7]), dia, 12, tzinfo=UTC)

with dsep.SessionLocal() as s:
    def sol(numero, tipo, ym, estado=dsep.ENVIADA):
        so = dsep.Solicitacao(numero=numero, chamado="INC1", tipo_atendimento=tipo, estado=estado,
                              aberta_por="u", aberta_em=dt(ym, 1), enviada_em=dt(ym) if estado == dsep.ENVIADA else None)
        s.add(so); s.flush(); return so
    # m3: 2 PDV (2 unidades bipadas) + 1 coletor (sem unidade: vale a quantidade 3)
    a = sol("SEP-1", dsep.FRENTE_RETAGUARDA, m3)
    i1 = dsep.Item(solicitacao_id=a.id, modelo="PDV Dell 3050", quantidade=2); s.add(i1); s.flush()
    s.add_all([dsep.Unidade(solicitacao_id=a.id, item_id=i1.id, serial=f"SN{i}") for i in range(2)])
    i2 = dsep.Item(solicitacao_id=a.id, modelo="Coletor Zebra TC21", quantidade=3); s.add(i2)
    # m2: 4 PDV por Mobilidade (reposição), 1 PDV separada mas não enviada (não conta)
    b = sol("SEP-2", dsep.MOBILIDADE, m2)
    ib = dsep.Item(solicitacao_id=b.id, modelo="PDV Dell 3050", quantidade=4); s.add(ib); s.flush()
    s.add_all([dsep.Unidade(solicitacao_id=b.id, item_id=ib.id, serial=f"SM{i}") for i in range(4)])
    c = sol("SEP-3", dsep.FRENTE_RETAGUARDA, m2, estado=dsep.SEPARADA)
    s.add(dsep.Item(solicitacao_id=c.id, modelo="PDV Dell 3050", quantidade=1))
    # m1: 10 PDV para inauguração — NÃO entra
    d = sol("SEP-4", dsep.INAUGURACAO_REFORMA, m1)
    s.add(dsep.Item(solicitacao_id=d.id, modelo="PDV Dell 3050", quantidade=10))
    # m1: 1 PDV reposição
    e = sol("SEP-5", dsep.FRENTE_RETAGUARDA, m1)
    s.add(dsep.Item(solicitacao_id=e.id, modelo="PDV Dell 3050", quantidade=1))
    s.commit()

real, primeiro = consumo_real_por_modelo()
checar(real["PDV Dell 3050"] == {m3: 2, m2: 4, m1: 1}, "PDV: soma unidades bipadas, ignora não enviada e inauguração")
checar(real["Coletor Zebra TC21"] == {m3: 3}, "sem unidade bipada vale a quantidade pedida")
checar(primeiro == m3, "primeiro mês com envio")
checar(ler_configuracao(REQ)["data_inicio_sistema"] == m3, "sem data configurada, o início é o primeiro envio")
checar("PDV Dell 3050" in listar_modelos(REQ)["modelos"], "modelos conhecidos vêm da Separação")

# ── 4. Histórico consolidado e imputado ────────────────────────────
print("\nHistórico")
m6, m5, m4 = mes_menos(mes_atual, 6), mes_menos(mes_atual, 5), mes_menos(mes_atual, 4)
r = gravar_historico([HistoricoIn(item_id=it["id"], mes=m6, quantidade=5),
                      HistoricoIn(item_id=it["id"], mes=m5, quantidade=6),
                      HistoricoIn(item_id=it["id"], mes=m4, quantidade=7),
                      HistoricoIn(item_id=it["id"], mes=m2, quantidade=99),
                      HistoricoIn(item_id=999, mes=m6, quantidade=1),
                      HistoricoIn(item_id=it["id"], mes="2026/01", quantidade=1)], REQ)
checar(r["gravados"] == 3 and len(r["recusados"]) == 3, "grava os anteriores ao sistema e recusa o resto")
checar(any(x["motivo"] == "mês coberto pela Separação" for x in r["recusados"]), "mês do sistema não aceita imputado")
r = gravar_historico([HistoricoIn(item_id=it["id"], mes=m6, quantidade=8)], REQ)
with db.SessionLocal() as s:
    checar(s.query(db.Historico).filter_by(item_id=it["id"], mes=m6).count() == 1, "regravar o mesmo mês substitui")

h = historico(REQ, meses=12)
linha = next(l for l in h["linhas"] if l["item"]["id"] == it["id"])
por_mes = {p["mes"]: p for p in linha["serie"]}
checar(len(h["meses"]) == 12 and h["meses"][-1] == mes_atual, "grade de 12 meses até o mês atual")
checar(por_mes[m6]["quantidade"] == 8 and por_mes[m6]["origem"] == "imputado", "mês imputado aparece como imputado")
checar(por_mes[m2]["quantidade"] == 4 and por_mes[m2]["origem"] == "real", "mês do sistema traz o real (o 99 imputado é ignorado)")
checar(por_mes[m1]["quantidade"] == 1 and por_mes[mes_atual]["quantidade"] == 0, "mês sem envio no sistema é zero real")

# Importação por planilha
csv = f"item;mes;quantidade\nPDV Dell 3050;{mes_menos(mes_atual, 8)};4\nPDV Dell 3050;{mes_menos(mes_atual, 7)};3\nPDV Dell 3050;{m1};50\nDesconhecido;{m6};2\n".encode()
linhas = rp._ler_planilha("hist.csv", csv)
checar(len(linhas) == 4, "CSV com ; lido")
mmaa = f"{int(m6[5:7]):02d}/{m6[:4]}"
checar(rp._ler_planilha("h.csv", f"Item,Mês,Qtd\nX,{mmaa},2\n".encode())[0]["mes"] == m6, "aceita MM/AAAA e nomes de coluna variados")
erro_http("planilha sem coluna obrigatória", lambda: rp._ler_planilha("h.csv", b"a,b\n1,2\n"))

import asyncio
class _Up:
    filename = "hist.csv"
    async def read(self): return csv
res = asyncio.run(rp.importar_historico(REQ, _Up()))
checar(res["lidas"] == 4 and res["gravadas"] == 2 and res["ignoradas_sistema"] == 1 and res["itens_desconhecidos"] == ["Desconhecido"],
       "importação grava só o que é anterior ao sistema e de item conhecido")

# ── 5. Previsão por item e necessidade ─────────────────────────────
print("\nPrevisão e necessidade")
pv = previsao(REQ, horizonte=6)
pdv = next(x for x in pv["itens"] if x["item"]["id"] == it["id"])
checar(pv["horizonte"] == 6 and len(pdv["previsao"]["p50"]) == 6, "horizonte respeitado")
checar(pdv["historico"][-1]["mes"] == m1, "série termina no último mês fechado")
checar(pdv["historico"][0]["mes"] == mes_menos(mes_atual, 8) and pdv["historico"][0]["quantidade"] == 4,
       "série começa no primeiro mês com consumo (imputado da planilha)")
checar(pdv["previsao"]["meses_historico"] == 8, "8 meses de histórico contados")
checar("média móvel" in pdv["previsao"]["metodo"], "método informado")
nec = pdv["necessidade"]
checar(nec["consumo_previsto"] > 0 and nec["valor"] == nec["necessidade"] * 3200, "valor em reais pelo custo unitário")
col = next(x for x in pv["itens"] if x["item"]["id"] == it2["id"])
checar(col["previsao"]["meses_historico"] == 3 and col["historico"][0]["quantidade"] == 3, "coletor: 3 meses (m3=3, m2=0, m1=0)")
checar(pv["resumo"]["itens"] == 2 and pv["resumo"]["valor"] >= 0, "resumo consolida os itens")

alterar_item(it2["id"], ItemIn(ativo=False), REQ)
checar(len(previsao(REQ)["itens"]) == 1, "item inativo sai da previsão")
excluir_item(it2["id"], REQ)
checar(len(listar_itens(REQ)["itens"]) == 1, "exclusão")
erro_http("excluir inexistente", lambda: excluir_item(it2["id"], REQ), 404)

gravar_configuracao(ConfigIn(data_inicio_sistema=m1, horizonte_meses=9), REQ)
cfg = ler_configuracao(REQ)
checar(cfg["data_inicio_sistema"] == m1 and cfg["data_inicio_configurada"] and cfg["horizonte_meses"] == 9, "configuração gravada")
h = historico(REQ, meses=12)
por_mes = {p["mes"]: p for p in next(l for l in h["linhas"] if l["item"]["id"] == it["id"])["serie"]}
checar(por_mes[m2]["origem"] == "imputado" and por_mes[m2]["quantidade"] == 0, "com início mais recente, o mês anterior volta a ser imputado")
checar(len(previsao(REQ)["itens"][0]["previsao"]["p50"]) == 9, "horizonte configurado vale como padrão")

print(f"\n{feitos - len(falhas)}/{feitos} verificações ok")
if falhas:
    print("FALHAS:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Planejamento íntegro.")
