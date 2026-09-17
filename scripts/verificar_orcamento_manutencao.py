#!/usr/bin/env python3
"""Verificação do Orçamento de Manutenção: modelo, categoria e importação.

    python3 scripts/verificar_orcamento_manutencao.py

Roda contra um SQLite temporário, sem tocar em banco de produção e sem
rede. Cobre o que a área pediu: o modelo sai do prefixo da série, a
categoria fica só em Coletor ou SLED, o lote de reparo informado na prévia
chega à base, e o painel devolve consumo por categoria e por modelo.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Banco isolado, antes de importar o módulo (a URL é lida na importação).
_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["ORCAMENTO_MANUTENCAO_DATABASE_URL"] = f"sqlite:///{_TMP/'manut.db'}"

import db.orcamento_manutencao as dbm  # noqa: E402
import routers.orcamento_manutencao as om  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


print("\n[1] O modelo sai do prefixo da série")
CASOS = [
    ("HF550X0012345", "HF550", "COLETOR"),
    ("EF500A99", "EF500", "COLETOR"),
    ("EF501B10", "EF501", "COLETOR"),
    ("S70123456", "S70", "COLETOR"),
    ("RFR900XYZ", "SLED RFR900", "SLED"),
    ("RFR901XYZ", "SLED RFR901", "SLED"),
]
for serie, modelo, familia in CASOS:
    checar(om.modelo_da_serie(serie) == (modelo, familia),
           f"{serie} → {modelo} ({familia}) — veio {om.modelo_da_serie(serie)}")
checar(om.modelo_da_serie("EF500123")[0] == "EF500"
       and om.modelo_da_serie("EF501123")[0] == "EF501",
       "EF500 e EF501 não se confundem")
checar(om.modelo_da_serie("RFR900A")[0] == "SLED RFR900"
       and om.modelo_da_serie("RFR901A")[0] == "SLED RFR901",
       "RFR900 e RFR901 não se confundem")
checar(om.modelo_da_serie(" hf550 x1 ")[0] == "HF550",
       "série com espaço e minúscula continua reconhecida")
checar(om.modelo_da_serie("ZZ999") == ("", ""), "série desconhecida não inventa modelo")
checar(om.modelo_da_serie("") == ("", ""), "série vazia não inventa modelo")

print("\n[2] Categoria é só Coletor ou SLED")
for texto in ("Coletor", "COLETOR - BLUEBIRD", "Coletor HF550X", "Coletor S70"):
    checar(om.normalizar_categoria(texto) == ("Coletor", "COLETOR"),
           f"{texto!r} → Coletor")
for texto in ("Sled RFID", "SLED RFR901", "sled"):
    checar(om.normalizar_categoria(texto) == ("SLED", "SLED"), f"{texto!r} → SLED")

print("\n[3] Série manda; a categoria da planilha é o socorro")
checar(om.resolver_modelo("HF550X01", "Sled RFID") == ("HF550", "Coletor", "COLETOR"),
       "série HF550 vence a categoria errada da planilha")
checar(om.resolver_modelo("SEMPREFIXO", "Coletor HF550X") == ("HF550", "Coletor", "COLETOR"),
       "sem prefixo na série, o modelo é lido do texto da categoria (base antiga)")
checar(om.resolver_modelo("ZZ1", "Coletor") == ("", "Coletor", "COLETOR"),
       "sem modelo em lugar nenhum, a categoria ainda sai certa")
checar(om.resolver_modelo("RFR901A", "") == ("SLED RFR901", "SLED", "SLED"),
       "planilha sem categoria: a série resolve tudo")

print("\n[4] Isenção dos 60 % segue o modelo")
checar(om._isento_60("HF550", "Coletor"), "HF550 é isento")
checar(om._isento_60("SLED RFR901", "SLED"), "RFR901 é isento")
checar(om._isento_60("S70", "Coletor"), "S70 é isento")
checar(not om._isento_60("EF500", "Coletor"), "EF500 não é isento")
checar(not om._isento_60("SLED RFR900", "SLED"), "RFR900 não é isento")
checar(om._isento_60("", "Coletor HF550X"),
       "base antiga, com o modelo dentro da categoria, continua isenta")

print("\n[5] Valor de compra padrão: pelo modelo, com a configuração de hoje")
cfg = {"valor_compra_padrao": dict(dbm.CONFIG_PADRAO["valor_compra_padrao"])}
checar(om._padrao_categoria(cfg, "Coletor", "HF550") == 4589.79,
       "HF550 usa o preço de 'Coletor HF550X'")
checar(om._padrao_categoria(cfg, "Coletor", "EF500") == 4978.29,
       "EF500 cai no preço genérico de Coletor")
checar(om._padrao_categoria(cfg, "SLED", "SLED RFR901") == 3735.95,
       "RFR901 usa o preço próprio")
checar(om._padrao_categoria(cfg, "SLED", "SLED RFR900") == 3731.51,
       "RFR900 usa o preço do Sled RFID")
cfg2 = {"valor_compra_padrao": {**cfg["valor_compra_padrao"], "EF500": 1234.0}}
checar(om._padrao_categoria(cfg2, "Coletor", "EF500") == 1234.0,
       "um preço cadastrado por modelo tem prioridade")

# ── Importação, contra banco de verdade ────────────────────────────────
print("\n[6] Importação: modelo gravado e lote informado na prévia")
import io  # noqa: E402

import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

dbm.init_db()
R = dbm.Reparo


class _Arquivo:
    """O mínimo que o endpoint usa de UploadFile."""

    def __init__(self, nome: str, dados: bytes):
        self.filename = nome
        self.file = io.BytesIO(dados)


class _Req:
    """Requisição de mentira: o endpoint só a passa para permissão e limite."""
    headers: dict = {}
    client = None


om._exigir = lambda req, acao: {"login": "teste"}
om.check_rate_limit = lambda req, nome: None


def planilha(linhas: list[dict]) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(linhas).to_excel(buf, index=False)
    return buf.getvalue()


LINHAS = [
    {"RMA": "R1", "SÉRIE": "HF550X0001", "CATEGORIA": "Coletor HF550X",
     "ORÇAMENTO": 1000, "STATUS ORÇAMENTO": "Aprovado", "MÊS CONTRATO": "2026-03"},
    {"RMA": "R2", "SÉRIE": "RFR901ABC", "CATEGORIA": "Sled RFID",
     "ORÇAMENTO": 500, "STATUS ORÇAMENTO": "Aprovado", "MÊS CONTRATO": "2026-03"},
    {"RMA": "R3", "SÉRIE": "EF500ZZZ", "CATEGORIA": "Coletor",
     "ORÇAMENTO": 200, "STATUS ORÇAMENTO": "Aguardando Aprovação", "MÊS CONTRATO": "2026-03"},
]

previa = om.importar(_Req(), _Arquivo("teste.xlsx", planilha(LINHAS)),
                     substituir=False, aba="", dry_run=True, lote="", lotes="")
checar(previa["lidas"] == 3 and previa["incluidas"] == 3, "prévia lê as três linhas")
por_rma = {x["rma"]: x for x in previa["previa"]}
checar(por_rma["R1"]["modelo"] == "HF550" and por_rma["R1"]["categoria"] == "Coletor",
       f"prévia já mostra modelo e categoria ({por_rma['R1']})")
checar(por_rma["R2"]["modelo"] == "SLED RFR901" and por_rma["R2"]["categoria"] == "SLED",
       "sled reconhecido pela série, categoria reduzida")
with dbm.SessionLocal() as s:
    checar(s.scalar(select(dbm.func.count(R.id)) if False else select(R.id)) is None,
           "prévia não grava nada")

# Envio de verdade, com o lote informado na tela e um ajuste por linha.
final = om.importar(_Req(), _Arquivo("teste.xlsx", planilha(LINHAS)),
                    substituir=False, aba="", dry_run=False,
                    lote="LOTE-2026-03", lotes='{"R3": "LOTE-ESPECIAL"}')
checar(final["incluidas"] == 3, "as três entram na base")
with dbm.SessionLocal() as s:
    gravados = {r.rma: r for r in s.scalars(select(R)).all()}
checar(gravados["R1"].modelo == "HF550" and gravados["R1"].categoria == "Coletor",
       f"R1 gravado com modelo ({gravados['R1'].modelo}/{gravados['R1'].categoria})")
checar(gravados["R2"].modelo == "SLED RFR901" and gravados["R2"].familia == "SLED",
       "R2 gravado como SLED RFR901")
checar(gravados["R3"].modelo == "EF500", "R3 gravado como EF500")
checar(gravados["R1"].lote_prime == "LOTE-2026-03",
       f"o lote da prévia foi aplicado ({gravados['R1'].lote_prime!r})")
checar(gravados["R3"].lote_prime == "LOTE-ESPECIAL",
       "o ajuste linha a linha vence o lote geral")

# A planilha que já traz lote não é sobrescrita pelo campo da tela.
COM_LOTE = [{**LINHAS[0], "RMA": "R4", "SÉRIE": "S70ABC", "LOTE PRIME": "DA-PLANILHA"}]
om.importar(_Req(), _Arquivo("t2.xlsx", planilha(COM_LOTE)), substituir=False,
            aba="", dry_run=False, lote="DA-TELA", lotes="")
with dbm.SessionLocal() as s:
    r4 = s.scalar(select(R).where(R.rma == "R4"))
checar(r4.lote_prime == "DA-PLANILHA", "o lote da planilha manda no da tela")
checar(r4.modelo == "S70", "S70 reconhecido")

# Sem lote nenhum, o que já está gravado não é apagado.
om.importar(_Req(), _Arquivo("t3.xlsx", planilha([{k: v for k, v in LINHAS[0].items()}])),
            substituir=False, aba="", dry_run=False, lote="", lotes="")
with dbm.SessionLocal() as s:
    r1 = s.scalar(select(R).where(R.rma == "R1"))
checar(r1.lote_prime == "LOTE-2026-03",
       f"reimportar sem lote não apaga o que estava gravado ({r1.lote_prime!r})")

print("\n[7] Painel: consumo por categoria e por modelo")
resumo = om.resumo(_Req(), ano=2026, mes="2026-03")
por_modelo = {m["modelo"]: m for m in resumo["modelos"]}
checar("HF550" in por_modelo and "SLED RFR901" in por_modelo,
       f"os modelos aparecem no painel ({sorted(por_modelo)})")
checar(por_modelo["HF550"]["consumo"] == 1000.0,
       f"consumo do HF550 ({por_modelo['HF550']['consumo']})")
checar(por_modelo["SLED RFR901"]["consumo"] == 500.0, "consumo do RFR901")
checar(por_modelo["EF500"]["consumo"] == 0.0 and por_modelo["EF500"]["pendentes"] == 1,
       "pendente não vira consumo, mas aparece na conta")
# Coletor = HF550 (1.000) + S70 (1.000); o EF500 está pendente, não consome.
checar(resumo["gerais"]["COLETOR"]["consumo"] == 2000.0
       and resumo["gerais"]["SLED"]["consumo"] == 500.0,
       f"consumo por categoria ({resumo['gerais']})")
checar(por_modelo["S70"]["consumo"] == 1000.0, "consumo do S70")
soma = round(sum(m["consumo"] for m in resumo["modelos"]), 2)
checar(soma == round(resumo["gerais"]["COLETOR"]["consumo"]
                     + resumo["gerais"]["SLED"]["consumo"], 2),
       f"a soma dos modelos fecha com a das categorias ({soma})")
part = por_modelo["HF550"]["participacao"]
checar(abs(part - (1000.0 / 2500.0)) < 1e-6, f"participação do HF550 ({part})")
checar(abs(sum(m["participacao"] or 0 for m in resumo["modelos"]) - 1.0) < 1e-6,
       "as participações somam 100 % do consumo do período")
consumos = [m["consumo"] for m in resumo["modelos"]]
checar(consumos == sorted(consumos, reverse=True),
       f"a lista vem do maior consumo para o menor ({consumos})")

print("\n[7.1] Cards Aguardando aprovação e Aguardando devolução abrem nos modelos")
# A tela monta uma linha por categoria que abre nos modelos daquela categoria
# (om-catrow / om-modrow). Sem `modelos` no retorno, a linha não abre.
# Os cards são um retrato de agora, sem filtro de ano, e as seções acima já
# deixaram reparos na base — por isso a conferência é sobre a diferença.
def _card(painel, chave, categoria, familia):
    for g in painel[chave]:
        if (g["categoria"], g["familia"]) == (categoria, familia):
            return g
    return {}

_antes = om.resumo(_Req(), ano=2026, mes="2026-03")
_apr0 = _card(_antes, "aguardando_aprovacao", "Coletor", "COLETOR")
_dev0 = _card(_antes, "aguardando_devolucao", "Coletor", "COLETOR")
_sled0 = _card(_antes, "aguardando_devolucao", "SLED", "SLED")

with dbm.SessionLocal.begin() as s:
    s.add_all([
        R(rma="AP1", serie="HF550AA1", categoria="Coletor", familia="COLETOR",
          modelo="HF550", orcamento=300, ano=2026, mes_referencia="2026-03",
          status="AGUARDANDO_APROVACAO", status_retorno="EM_MANUTENCAO"),
        R(rma="AP2", serie="HF550AA2", categoria="Coletor", familia="COLETOR",
          modelo="HF550", orcamento=200, ano=2026, mes_referencia="2026-03",
          status="AGUARDANDO_APROVACAO", status_retorno="EM_MANUTENCAO"),
        R(rma="AP3", serie="EF500AA1", categoria="Coletor", familia="COLETOR",
          modelo="EF500", orcamento=100, ano=2026, mes_referencia="2026-03",
          status="AGUARDANDO_APROVACAO", status_retorno="EM_MANUTENCAO"),
        R(rma="DV1", serie="RFR901A1", categoria="SLED", familia="SLED",
          modelo="SLED RFR901", orcamento=0, ano=2026, mes_referencia="2026-03",
          status="APROVADO", status_retorno="EM_MANUTENCAO"),
    ])

painel = om.resumo(_Req(), ano=2026, mes="2026-03")

checar(all("modelos" in g for g in painel["aguardando_aprovacao"]),
       "toda categoria de Aguardando aprovação traz a lista de modelos")
cole = _card(painel, "aguardando_aprovacao", "Coletor", "COLETOR")
checar(cole.get("qtde", 0) - _apr0.get("qtde", 0) == 3,
       f"os três novos entram no Coletor ({_apr0.get('qtde', 0)} -> {cole.get('qtde')})")
checar(abs((cole.get("valor", 0) - _apr0.get("valor", 0)) - 600.0) < 1e-6,
       f"o valor da categoria acompanha ({cole.get('valor')})")
mods_apr = {m["modelo"]: m for m in cole.get("modelos", [])}
checar({"HF550", "EF500"} <= set(mods_apr),
       f"os modelos da categoria aparecem ({sorted(mods_apr)})")
checar(mods_apr.get("HF550", {}).get("qtde") == 2
       and abs(mods_apr.get("HF550", {}).get("valor", 0) - 500.0) < 1e-6,
       f"HF550 com qtde e valor próprios ({mods_apr.get('HF550')})")
checar(sum(m["qtde"] for m in mods_apr.values()) == cole.get("qtde"),
       "a soma dos modelos fecha com a qtde da categoria")
checar(abs(sum(m["valor"] for m in mods_apr.values()) - cole.get("valor", 0)) < 1e-6,
       "e a soma dos valores também fecha")
qtds = [m["qtde"] for m in cole.get("modelos", [])]
checar(qtds == sorted(qtds, reverse=True),
       f"os modelos vêm do maior para o menor ({qtds})")

checar(all(isinstance(g.get("modelos"), list) for g in painel["aguardando_devolucao"]),
       "Aguardando devolução também traz a lista de modelos")
dcole = _card(painel, "aguardando_devolucao", "Coletor", "COLETOR")
checar(dcole.get("total", 0) - _dev0.get("total", 0) == 3
       and dcole.get("ag_aprovacao", 0) - _dev0.get("ag_aprovacao", 0) == 3,
       f"os três novos entram como aguardando aprovação ({dcole.get('total')})")
mods_dev = {m["modelo"]: m for m in dcole.get("modelos", [])}
checar(mods_dev.get("HF550", {}).get("ag_aprovacao") == 2,
       f"o modelo repete as colunas da categoria ({mods_dev.get('HF550')})")
checar(sum(m["total"] for m in mods_dev.values()) == dcole.get("total"),
       "a soma dos modelos fecha com o total da categoria")
for _col in ("ag_manutencao", "ag_orcamento", "ag_aprovacao", "reprovado"):
    checar(sum(m[_col] for m in mods_dev.values()) == dcole.get(_col),
           f"coluna {_col} fecha entre modelo e categoria")
dsled = _card(painel, "aguardando_devolucao", "SLED", "SLED")
checar(dsled.get("ag_manutencao", 0) - _sled0.get("ag_manutencao", 0) == 1,
       f"APROVADO conta como aguardando manutenção ({dsled.get('ag_manutencao')})")

# Limpa o que este bloco criou para não contaminar as seções seguintes.
with dbm.SessionLocal.begin() as s:
    for rma in ("AP1", "AP2", "AP3", "DV1"):
        alvo = s.scalar(select(R).where(R.rma == rma))
        if alvo is not None:
            s.delete(alvo)

print("\n[8] Recalcular preenche a base antiga")
with dbm.SessionLocal.begin() as s:
    s.add(R(rma="ANTIGO", serie="SEMPREFIXO1", categoria="Coletor HF550X",
            familia="COLETOR", modelo="", orcamento=100, ano=2026,
            mes_referencia="2026-03", status="APROVADO"))
r = om.recalcular(_Req())
with dbm.SessionLocal() as s:
    antigo = s.scalar(select(R).where(R.rma == "ANTIGO"))
checar(antigo.modelo == "HF550", f"modelo deduzido do texto antigo ({antigo.modelo!r})")
checar(antigo.categoria == "Coletor", f"categoria reduzida ({antigo.categoria!r})")
checar(r["modelos_preenchidos"] >= 1, "o recálculo conta quantos ajustou")

print("\n[9] Banco que já existe ganha a coluna sem perder dado")
import sqlite3  # noqa: E402

# Retrato do banco de produção antes desta mudança: tabela criada pelo ORM,
# com linha gravada, e então a coluna nova removida.
_ALVO = Path(str(dbm.DATABASE_URL).replace("sqlite:///", ""))
con = sqlite3.connect(_ALVO)
con.execute("DROP INDEX IF EXISTS ix_manut_reparo_modelo")
con.execute("ALTER TABLE manut_reparo DROP COLUMN modelo")
con.commit()
checar("modelo" not in {c[1] for c in con.execute("PRAGMA table_info(manut_reparo)")},
       "banco antigo montado, sem a coluna modelo")
antes_linhas = con.execute("SELECT COUNT(*) FROM manut_reparo").fetchone()[0]
con.close()

dbm._ready = False
dbm._engine = None
dbm._factory = None
acrescentadas = dbm.migrar_colunas(dbm.get_engine())
checar("manut_reparo.modelo" in acrescentadas,
       f"a migração acrescenta a coluna ({acrescentadas})")
con = sqlite3.connect(_ALVO)
checar(con.execute("SELECT COUNT(*) FROM manut_reparo").fetchone()[0] == antes_linhas,
       f"nenhuma linha se perdeu ({antes_linhas})")
con.close()
checar(dbm.migrar_colunas(dbm.get_engine()) == [],
       "rodar de novo não faz nada (idempotente)")
depois = om.recalcular(_Req())
checar(depois["modelos_preenchidos"] >= 1,
       f"o recálculo repõe os modelos apagados ({depois})")
with dbm.SessionLocal() as s:
    conferir = s.scalar(select(R).where(R.rma == "R1"))
checar(conferir.modelo == "HF550", f"R1 voltou a ter modelo ({conferir.modelo!r})")

print("\n[10] A base que já existia ganha o modelo só por subir o módulo")
# Reparos antigos: gravados sem modelo, com o modelo dentro da categoria
# (como a planilha histórica fazia) ou só na série.
with dbm.SessionLocal.begin() as s:
    s.execute(dbm.Reparo.__table__.update().values(modelo="", categoria="Coletor HF550X"))
    s.add(R(rma="ANTIGO-SERIE", serie="RFR900ZZ9", categoria="", familia="",
            modelo="", orcamento=50, ano=2026, mes_referencia="2026-03",
            status="APROVADO"))
    s.add(R(rma="ANTIGO-SEM-PISTA", serie="ZZZ999", categoria="Outro treco",
            familia="OUTRO", modelo="", orcamento=10, ano=2026,
            mes_referencia="2026-03", status="APROVADO"))
with dbm.SessionLocal() as s:
    vazios = s.scalars(select(R).where(R.modelo == "")).all()
checar(len(vazios) >= 3, f"base antiga montada: {len(vazios)} reparos sem modelo")

# É isto que acontece quando o serviço sobe com a versão nova.
mudaram = dbm.preencher_modelos()
checar(mudaram >= 3, f"a subida preencheu {mudaram} reparo(s)")

with dbm.SessionLocal() as s:
    achados = {r.rma: (r.modelo, r.categoria, r.familia) for r in s.scalars(select(R)).all()}
checar(achados["R1"][0] == "HF550",
       f"modelo veio da série mesmo com a categoria antiga ({achados['R1']})")
checar(achados["R1"][1] == "Coletor", "categoria reduzida junto")
checar(achados["ANTIGO-SERIE"] == ("SLED RFR900", "SLED", "SLED"),
       f"série resolve sozinha, sem categoria ({achados['ANTIGO-SERIE']})")
checar(achados["ANTIGO-SEM-PISTA"][0] == "",
       "sem pista nenhuma, não inventa modelo")
checar(achados["ANTIGO-SEM-PISTA"][1] == "Outro treco",
       f"e não mexe na categoria que não sabe ler ({achados['ANTIGO-SEM-PISTA']})")
checar(dbm.preencher_modelos() == 0,
       "subir de novo não reescreve nada (idempotente)")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Orçamento de Manutenção íntegro.")
