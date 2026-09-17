#!/usr/bin/env python3
"""Verificação do espaço Consulta Times e da entrada por planilha.

    python3 scripts/verificar_consulta_times.py

Cobre o que a área pediu: a configuração do espaço é dele (mexer lá não
muda o portal e vice-versa), os estoques vêm do alm_stockroom sem os do
SPARE, a Obsolescência não aparece no espaço, e a Entrada de Ativos
aceita planilha além da lista.
"""
from __future__ import annotations
import os, re, sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_T = tempfile.mkdtemp(prefix="ct-verif-")
for v in sorted(set(re.findall(r"([A-Z_]*DATABASE_URL)", (RAIZ / "config.py").read_text()))):
    os.environ[v] = f"sqlite:///{_T}/{v.lower()}.db"
os.environ["PORTAL_SESSION_SECRET"] = "verificacao-local"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")

import db.consulta_times as dbct          # noqa: E402
import routers.consulta_times as rct      # noqa: E402
import routers.servicenow as sn           # noqa: E402
from routers.servicenow import config_gestao_ativos, ler_planilha_entrada  # noqa: E402
from fastapi import HTTPException         # noqa: E402
from types import SimpleNamespace         # noqa: E402

falhas, feitos = [], 0
def checar(c, d):
    global feitos; feitos += 1
    print(("  ok   " if c else "  FALHA ") + d)
    if not c: falhas.append(d)

def erro_http(desc, fn, status=400):
    try:
        fn(); checar(False, f"{desc} (não recusou)")
    except HTTPException as e:
        checar(e.status_code == status, desc)

REQ = SimpleNamespace(cookies={}, headers={}, client=None)
ADMIN = {"username": "admin.times", "is_admin": True}
rct._exigir = lambda req, acao="view": ADMIN
rct.dbct.registrar_acesso = lambda *a, **k: None
dbct.init_db()
import db.portal as dbp                   # noqa: E402
dbp.init_db()

# ── 1. Configuração do espaço é do espaço ──────────────────────────
print("\n[1] Configuração isolada do portal")
checar(rct.listas_ler(REQ) == {"estoques": [], "corredores": []},
       "o espaço começa sem estoques (os do SPARE não são dele)")
portal_antes = config_gestao_ativos()
rct.listas_gravar(rct.ListasIn(estoques=["LOJA 101", "LOJA 102"], corredores=["A-1"]), REQ)
checar(rct.listas_ler(REQ)["estoques"] == ["LOJA 101", "LOJA 102"], "gravou no banco do espaço")
checar(config_gestao_ativos() == portal_antes, "o portal NÃO mudou")

from db.portal import SessionLocal as PS, Setting  # noqa: E402
with PS.begin() as s:
    row = s.get(Setting, "gestao_ativos") or Setting(key="gestao_ativos")
    row.value = {"estoques": ["SPARE - CD324", "SPARE-ADM15"], "corredores": [], "anotacoes": []}
    s.add(row)
checar(config_gestao_ativos()["estoques"] == ["SPARE - CD324", "SPARE-ADM15"], "portal alterado")
checar(rct.listas_ler(REQ)["estoques"] == ["LOJA 101", "LOJA 102"], "o espaço NÃO mudou")

rct.listas_gravar(rct.ListasIn(estoques=[" LOJA 103 ", "", "LOJA 103"], corredores=[]), REQ)
checar(rct.listas_ler(REQ)["estoques"] == ["LOJA 103", "LOJA 103"], "espaços em branco removidos ao gravar")

# ── 2. Estoques do ServiceNow sem os do SPARE ──────────────────────
print("\n[2] Estoques vindos do alm_stockroom")
sn._sn_session_from_portal = lambda req: object()
tabelas = {}
def _consulta(sessao, tabela, query="", fields="", **k):
    tabelas["tabela"] = tabela
    return [{"name": n} for n in ["LOJA 101", "SPARE - CD324", "loja 102", "Spare-ADM15",
                                  "CD MATRIZ", "ESTOQUE SPARE CD504", "", "LOJA 101"]]
sn._sn_query_all = _consulta
r = rct.stockrooms(REQ)
checar(tabelas["tabela"] == "alm_stockroom", "consulta a tabela alm_stockroom")
checar(r["estoques"] == ["CD MATRIZ", "LOJA 101", "loja 102"], "sem SPARE, sem repetido, sem vazio, ordenado")
checar(r["excluidos"] == 3 and r["total"] == 6, "conta quantos ficaram de fora")

# ── 3. Módulos do espaço são arquivos próprios ─────────────────────
print("\n[3] Módulos separados")
portal_dir, times_dir = RAIZ / "modulos", RAIZ / "modulos-times"
for nome in ("consulta", "gestao_ativos", "servicenow", "consulta_times"):
    checar((times_dir / f"{nome}.js").exists(), f"{nome}.js existe no espaço Times")
ga = (times_dir / "gestao_ativos.js").read_text()
checar("'obsolescencia', 'Obsolescência'" not in ga, "Gestão de Ativos do espaço não tem a aba Obsolescência")
checar("'obsolescencia', 'Obsolescência'" in (portal_dir / "gestao_ativos.js").read_text(),
       "…e o portal continua com ela")
checar("/modulos-times/servicenow.js" in ga, "o espaço carrega o servicenow.js dele")
snt = (times_dir / "servicenow.js").read_text()
checar("'/consulta-times/gestao-ativos'" in snt and "/servicenow/gestao-ativos/config" not in snt,
       "as telas do espaço leem a configuração do espaço")
checar("/servicenow/gestao-ativos/config" in (portal_dir / "servicenow.js").read_text(),
       "…e as do portal leem a do portal")
checar("SPARE - CD324" not in snt, "nenhum estoque do SPARE embutido no espaço")
app_js = (RAIZ / "static/app.js").read_text()
checar("'/modulos-times/'" in app_js, "o carregador aponta para a pasta do espaço")

# ── 3b. Acesso Consulta Times: quatro blocos, montados de uma vez ──
print("\n[3b] Tela Acesso Consulta Times")
for lado, caminho in (("portal", portal_dir), ("times", times_dir)):
    ct = (caminho / "consulta_times.js").read_text()
    for titulo in ("Liberação de acesso", "Usuários liberados",
                   "Estoques, corredores e espaços", "Trilha de acesso"):
        checar(f"cartao('{titulo}'" in ct, f"{lado}: bloco {titulo}")
    titulos = sorted(set(re.findall(r"cartao\('([^']+)'", ct)))
    checar(titulos == ["Estoques, corredores e espaços", "Liberação de acesso",
                       "Trilha de acesso", "Usuários liberados"],
           f"{lado}: só esses quatro blocos, nada mais")
    checar("Anotações sugeridas" not in ct and "anotacoes" not in ct,
           f"{lado}: sem anotações sugeridas")
    # Montar por partes entre awaits era o que duplicava blocos na tela.
    checar("Promise.all" in ct, f"{lado}: busca os dados antes de montar")
    checar("document.getElementById" not in ct, f"{lado}: usa referência de elemento, não id")

# O hash não pode disparar uma segunda navegação da mesma tela.
checar("_hashInterno" in app_js and "if (_hashInterno) { _hashInterno = false; return; }" in app_js,
       "hashchange ignora a troca de hash feita pelo próprio nav()")
checar("$('#page-content').replaceWith(content)" in app_js,
       "cada navegação troca o nó do conteúdo, isolando render antigo")

# ── 4. Rótulo Espaço e Corredor ────────────────────────────────────
print("\n[4] Espaço e Corredor")
for nome, texto in (("portal", (portal_dir / "servicenow.js").read_text()), ("times", snt)):
    checar("Aisle and Space" not in texto and "Aisle/Space" not in texto, f"{nome}: rótulo antigo removido")
    checar("Espaço e Corredor" in texto, f"{nome}: rótulo novo presente")
    checar('id="sn-aisle" class="form-control"' in texto, f"{nome}: continua campo de texto")
    checar("Informe o Espaço e Corredor (obrigatório)" in texto, f"{nome}: segue obrigatório")

# ── 5. Entrada por planilha ────────────────────────────────────────
print("\n[5] Entrada de Ativos por planilha")
cab = "Asset Tag *;Número de Série *;Modelo *;Categoria;Empresa\n"
linhas = ler_planilha_entrada("ativos.csv", (cab + "RN1;SN1;PDV Dell;Micro;RENNER\n"
                                             "RN2;SN2;Coletor TC21;;\n"
                                             ";;;;\n").encode("utf-8"))
checar(len(linhas) == 2, "linha em branco no meio é ignorada")
checar(linhas[0]["tag_number"] == "RN1" and linhas[0]["model"] == "PDV Dell" and linhas[0]["encontrado"],
       "campos lidos pelo rótulo do modelo")
checar(linhas[0]["origem_planilha"] is True, "linha marcada como vinda de planilha")
inc = ler_planilha_entrada("a.csv", (cab + "RN3;;Coletor\n").encode("utf-8"))
checar(not inc[0]["encontrado"] and "Número de Série" in inc[0]["erro"],
       "linha sem obrigatório vem marcada com o motivo")
tec = ler_planilha_entrada("a.csv", b"tag_number,serial_number,model\nRN9,SN9,PDV\n")
checar(tec[0]["serial_number"] == "SN9", "aceita tambem o nome tecnico da coluna")
erro_http("planilha sem coluna obrigatória", lambda: ler_planilha_entrada("a.csv", b"a,b\n1,2\n"))
erro_http("planilha sem nenhuma linha", lambda: ler_planilha_entrada("a.csv", cab.encode("utf-8")))

sn.require_permission = lambda req, mod, acao="view": ADMIN
modelo = sn.entrada_planilha_modelo(REQ)
import io as _io
from openpyxl import load_workbook  # noqa: E402
import asyncio  # noqa: E402
async def _ler(resp):
    pedacos = []
    async for p_ in resp.body_iterator:
        pedacos.append(p_ if isinstance(p_, bytes) else str(p_).encode())
    return b"".join(pedacos)
buf = asyncio.run(_ler(modelo))
wb = load_workbook(_io.BytesIO(buf))
cabecalhos = [c.value for c in wb["Ativos"][1]]
obrigatorias = [r + " *" for _c, r, ob in sn.COLUNAS_PLANILHA_ENTRADA if ob]
checar(obrigatorias == ["Asset Tag *", "Número de Série *", "Modelo *"] and
       all(o in cabecalhos for o in obrigatorias), "modelo marca as três colunas obrigatórias com *")
checar(not any(c.endswith(" *") for c in cabecalhos if c not in obrigatorias), "as demais colunas não levam *")
checar(len(cabecalhos) == len(sn.COLUNAS_PLANILHA_ENTRADA), "modelo traz todas as colunas que sobem")
checar("Instruções" in wb.sheetnames, "modelo tem aba de instruções")
lidas = ler_planilha_entrada("m.xlsx", buf)
checar(lidas and lidas[0]["tag_number"] == "RN000123", "o modelo baixado é lido de volta pelo próprio importador")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Consulta Times e planilha íntegros.")
