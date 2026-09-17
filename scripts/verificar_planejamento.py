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
for m in ("PLANEJAMENTO", "SEPARACAO", "TRILHA", "OBSOLESCENCIA"):
    os.environ[f"{m}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{m.lower()}.db"

from core.previsao import prever, necessidade, meses_entre, mes_seguinte  # noqa: E402
import db.planejamento as db  # noqa: E402
import db.separacao as dsep  # noqa: E402
import routers.planejamento as rp  # noqa: E402
from routers.planejamento import (  # noqa: E402
    ItemIn, ConfigIn, HistoricoIn, criar_item, alterar_item, excluir_item, listar_itens,
    listar_modelos, ler_configuracao, gravar_configuracao, historico, gravar_historico,
    previsao, consumo_real_por_modelo,
    AcordoIn, SubstituicaoIn, listar_acordos, criar_acordo, alterar_acordo, excluir_acordo,
    gravar_plano, previsao_obsolescencia, situacao_acordo,
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

# ── 6. Acordos de compra ───────────────────────────────────────────
print("\nAcordos de compra")
checar(situacao_acordo(None, 60) == "sem_vencimento", "sem vencimento")
checar(situacao_acordo(date.today() - timedelta(days=1), 60) == "vencido", "vencido ontem")
checar(situacao_acordo(date.today() + timedelta(days=30), 60) == "vence_em_breve", "vence em 30 dias com alerta de 60")
checar(situacao_acordo(date.today() + timedelta(days=90), 60) == "vigente", "vigente")

venc = (date.today() + timedelta(days=200)).isoformat()
ac = criar_acordo(AcordoIn(item_ebs="123456", descricao="PDV Dell 3050 completo", valor=2950.5, fornecedor="3729 - AIDC", vencimento=venc), REQ)
checar(ac["fornecedor_codigo"] == "3729" and ac["fornecedor_nome"] == "AIDC" and ac["fornecedor"] == "3729 - AIDC", "fornecedor '3729 - AIDC' separado em código e nome")
checar(ac["situacao"] == "vigente" and ac["vencimento"] == venc, "vencimento e situação")
erro_http("mesmo item e fornecedor é recusado", lambda: criar_acordo(AcordoIn(item_ebs="123456", valor=1, fornecedor_codigo="3729", fornecedor_nome="AIDC"), REQ), 409)
erro_http("item vazio é recusado", lambda: criar_acordo(AcordoIn(item_ebs=" ", valor=1), REQ))
erro_http("data inválida é recusada", lambda: criar_acordo(AcordoIn(item_ebs="1", valor=1, vencimento="31/02/2026x"), REQ))
ac2 = criar_acordo(AcordoIn(item_ebs="123456", valor=3100, fornecedor_codigo="4001", fornecedor_nome="Outro", vencimento=(date.today() - timedelta(days=5)).isoformat()), REQ)
checar(ac2["situacao"] == "vencido", "segundo fornecedor, vencido")
la = listar_acordos(REQ)
checar(la["resumo"]["total"] == 2 and la["resumo"]["vencidos"] == 1, "listagem e resumo")
alterar_acordo(ac2["id"], AcordoIn(vencimento=(date.today() + timedelta(days=10)).isoformat()), REQ)
checar(listar_acordos(REQ)["resumo"]["vencem_em_breve"] == 1, "alterar vencimento muda a situação")
_sessao["atual"] = LEITOR
erro_http("quem só lê não cria acordo", lambda: criar_acordo(AcordoIn(item_ebs="9", valor=1), REQ), 403)
_sessao["atual"] = ADMIN

# Custo do item cai no acordo quando o item não tem custo.
it3 = criar_item(ItemIn(nome="PDV via acordo", modelos=["Zebra X"], item_ebs="123456", estoque_atual=0), REQ)
pv = previsao(REQ, horizonte=3)
x3 = next(x for x in pv["itens"] if x["item"]["id"] == it3["id"])
checar(x3["acordo"] and x3["acordo"]["fornecedor"] == "3729 - AIDC", "acordo vigente com vencimento mais distante é o escolhido")
gravar_configuracao(ConfigIn(data_inicio_sistema=m1), REQ)
gravar_historico([HistoricoIn(item_id=it3["id"], mes=mes_menos(mes_atual, k), quantidade=10) for k in range(2, 9)], REQ)
pv = previsao(REQ, horizonte=3)
x3 = next(x for x in pv["itens"] if x["item"]["id"] == it3["id"])
checar(x3["necessidade"]["necessidade"] > 0 and abs(x3["necessidade"]["valor"] - x3["necessidade"]["necessidade"] * 2950.5) < 0.01,
       "sem custo no item, o valor usa o acordo (2950,50)")
alterar_item(it3["id"], ItemIn(custo_unitario=100), REQ)
x3 = next(x for x in previsao(REQ, horizonte=3)["itens"] if x["item"]["id"] == it3["id"])
checar(abs(x3["necessidade"]["valor"] - x3["necessidade"]["necessidade"] * 100) < 0.01, "custo digitado no item vence o acordo")

csv_ac = ("Item;Descrição;Valor;Fornecedor;Vencimento\n"
          "777001;Coletor TC21;2.450,00;3729 - AIDC;31/12/2027\n"
          "777001;Coletor TC21;2.500,00;5100 - Outro;2027-06-30\n"
          "123456;PDV Dell 3050;2.999,00;3729 - AIDC;2028-01-31\n"
          "999;Sem data;10;1 - X;\n"
          "888;Data ruim;10;1 - X;xx/yy\n").encode("utf-8")
class _UpAc:
    filename = "acordos.csv"
    async def read(self): return csv_ac
res = asyncio.run(rp.importar_acordos(REQ, _UpAc()))
checar(res["lidas"] == 5 and res["novos"] == 3 and res["atualizados"] == 1 and len(res["recusados"]) == 1,
       "importação: 3 novos, 1 atualizado (123456/3729), 1 recusado por data")
la = listar_acordos(REQ)
a1 = next(a for a in la["acordos"] if a["item_ebs"] == "777001" and a["fornecedor_codigo"] == "3729")
checar(a1["valor"] == 2450.0 and a1["vencimento"] == "2027-12-31", "valor em formato brasileiro e data DD/MM/AAAA lidos")
a2 = next(a for a in la["acordos"] if a["item_ebs"] == "123456" and a["fornecedor_codigo"] == "3729")
checar(a2["valor"] == 2999.0 and a2["vencimento"] == "2028-01-31" and a2["descricao"] == "PDV Dell 3050", "acordo existente atualizado pela planilha")
excluir_acordo(a2["id"], REQ)
erro_http("excluir inexistente", lambda: excluir_acordo(a2["id"], REQ), 404)

# ── 7. Obsolescência: substituição por BU ──────────────────────────
print("\nObsolescência por BU")
import db.obsolescencia as dob
dob.init_db()
with dob.SessionLocal() as s:
    col = dob.Coleta(usuario="u", situacao="concluida"); s.add(col); s.flush()
    n = 0
    def add(modelo, bu, bu_nome, pais, qtd, obsoleto=True, situacao=dob.ATIVO):
        global n
        for _ in range(qtd):
            n += 1
            s.add(dob.Coletor(mdm_id=f"m{n}", serie=f"S{n}", bu=bu, bu_nome=bu_nome, pais=pais, loja="001", e_loja=(bu != "CD"),
                              modelo=modelo, obsoleto=obsoleto, situacao=situacao, visto_na_coleta=col.id))
    add("Zebra TC20", "LJR", "Renner", "BR", 12)
    add("Zebra TC20", "CM", "Camicado", "BR", 5)
    add("Zebra TC20", "CD", "Centro de Distribuição", "BR", 3)
    add("Zebra MC33", "LJR", "Renner", "BR", 4)
    add("Zebra MC33", "LJRAR", "Renner Argentina", "AR", 2)
    add("Zebra TC21", "LJR", "Renner", "BR", 30, obsoleto=False)       # não é obsoleto
    add("Zebra TC20", "YC", "Youcom", "BR", 7, situacao=dob.SUMIU)     # sumiu: não conta
    s.commit()

ob = previsao_obsolescencia(REQ)
tc20 = next(l for l in ob["linhas"] if l["modelo"] == "Zebra TC20")
checar(ob["resumo"]["aparelhos"] == 26 and ob["resumo"]["modelos"] == 2, "26 aparelhos obsoletos ativos em 2 modelos (TC21 e sumidos fora)")
checar(tc20["por_bu"] == {"LJR": 12, "CM": 5, "CD": 3}, "TC20 por BU, CD incluído")
checar({b["bu"] for b in ob["bus"]} == {"LJR", "CM", "CD", "LJRAR"}, "BUs presentes")
checar(tc20["sem_custo"] and ob["resumo"]["sem_custo"] == 2 and ob["resumo"]["valor"] == 0, "sem plano: sem custo, valor zero, alerta")

itc = criar_item(ItemIn(nome="Coletor TC21 novo", modelos=["Coletor Zebra TC21"], item_ebs="777001"), REQ)
ob = gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra TC20", item_id=itc["id"], mes_alvo="2027-03"),
                   SubstituicaoIn(modelo_obsoleto="Zebra MC33", custo_unitario=2000, percentual=50, mes_alvo="2027-03")], REQ)
tc20 = next(l for l in ob["linhas"] if l["modelo"] == "Zebra TC20")
mc33 = next(l for l in ob["linhas"] if l["modelo"] == "Zebra MC33")
checar(tc20["custo"] == 2450.0 and tc20["acordo"]["fornecedor"] == "3729 - AIDC", "custo vem do item substituto → acordo vigente mais distante (2450, AIDC)")
checar(tc20["unidades"] == 20 and tc20["valor"] == 20 * 2450.0, "TC20: 20 unidades × 2450")
checar(mc33["unidades_por_bu"] == {"LJR": 2, "LJRAR": 1} and mc33["valor"] == 3 * 2000.0, "MC33: 50% arredondado para cima por BU, custo digitado")
ljr = next(b for b in ob["por_bu"] if b["bu"] == "LJR")
checar(ljr["aparelhos"] == 16 and ljr["unidades"] == 14 and ljr["valor"] == 12 * 2450.0 + 2 * 2000.0, "totais da BU Renner")
checar(ob["por_mes"] == [{"mes": "2027-03", "unidades": 23, "valor": 20 * 2450.0 + 6000.0}], "calendário de compra por mês")
checar(ob["resumo"]["sem_custo"] == 0 and ob["resumo"]["sem_mes"] == 0, "com plano completo, sem alertas")
ob = gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra MC33", ativo=False)], REQ)
checar(next(l for l in ob["linhas"] if l["modelo"] == "Zebra MC33")["unidades"] == 0 and ob["resumo"]["valor"] == 20 * 2450.0, "plano inativo não compra")
erro_http("mês alvo inválido", lambda: gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra TC20", mes_alvo="03/2027")], REQ))
erro_http("percentual fora da faixa", lambda: gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra TC20", percentual=120)], REQ))
erro_http("item substituto inexistente", lambda: gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra TC20", item_id=9999)], REQ), 404)
_sessao["atual"] = LEITOR
checar(previsao_obsolescencia(REQ)["resumo"]["aparelhos"] == 26, "quem só lê vê a previsão de obsolescência")
erro_http("quem só lê não grava plano", lambda: gravar_plano([SubstituicaoIn(modelo_obsoleto="Zebra TC20")], REQ), 403)
_sessao["atual"] = ADMIN

print(f"\n{feitos - len(falhas)}/{feitos} verificações ok")
if falhas:
    print("FALHAS:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Planejamento íntegro.")
