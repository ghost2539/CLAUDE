#!/usr/bin/env python3
"""A exportação da Base de Recebimentos exporta a BASE.

    python3 scripts/verificar_recebimento_export.py

Havia um botão só, e ele não exportava a base: convertia tudo para o formato
de importação do `alm_hardware` do ServiceNow — colunas em inglês e valores
FIXOS que não estão na base ("In stock", "Available", "SPARE - CD324",
"Purchase", "Capex", "SL 5 Years", quantidade 1). Serve para alimentar o
ServiceNow; não serve para olhar os recebimentos.

Agora são dois botões, e o que importa aqui é o novo: as colunas da tela,
com os valores da tela, com os filtros da tela.

Três coisas que esta verificação sustenta:

*   **As colunas da exportação são as mesmas da tela**, na mesma ordem. A
    lista está escrita em dois lugares (o router e o JS) porque um serve o
    servidor e o outro o navegador; conferi-las uma contra a outra é o que
    impede a planilha de divergir da tela sem ninguém notar.
*   **Nenhum valor inventado.** O que sai é o que está na base. Se um valor
    fixo do modelo do ServiceNow aparecer na exportação da base, é conversão
    vazando.
*   **Os filtros da tela valem.** Quem filtra 200 linhas e clica em exportar
    espera as 200. A listagem e a exportação usam a MESMA consulta, para não
    poderem discordar.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import routers.recebimento as rc  # noqa: E402

app = main.app
cliente = TestClient(app)
_, cookie = security.create_session(
    {"username": "verificador", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


print("[1] As colunas da exportação são as da tela, na mesma ordem")
js = (RAIZ / "modulos" / "recebimento.js").read_text(encoding="utf-8")
# A lista de colunas da tabela da Base, como o JS a escreve.
bloco = js[js.index("async function load() {"):]
bloco = bloco[bloco.index("var cols = ["):bloco.index("].map(function (x) {")]
da_tela = re.findall(r"\['(\w+)',\s*'([^']*)'\]", bloco)
do_router = [(c, r) for c, r in rc.COLUNAS_BASE]
checar(bool(da_tela), f"a tela declara colunas ({len(da_tela)} encontradas)")
checar([c for c, _ in da_tela] == [c for c, _ in do_router],
       "as chaves batem, na mesma ordem")
checar([r for _, r in da_tela] == [r for _, r in do_router],
       "e os rótulos também — a planilha tem o cabeçalho que a tela mostra")
# Nomeadas: são as que a pessoa procura ao abrir o arquivo.
for obrigatoria in ("id", "data_recebimento", "origem_entrada", "po", "nf",
                    "empresa", "imobilizado", "etiqueta", "numero_serie",
                    "descricao", "categoria", "modelo", "status", "local", "lote"):
    checar(obrigatoria in [c for c, _ in do_router], f"coluna {obrigatoria}")


print("\n[2] O arquivo traz a base, e só a base")
import db.portal as _db  # noqa: E402

_db.init_db()
from routers.helpers import cycle_dict  # noqa: E402


def _semear():
    """Dois recebimentos, com valores reconhecíveis."""
    from datetime import date
    with rc.SessionLocal.begin() as s:
        for i, (serie, empresa, cat, status) in enumerate((
                ("SERIE-AAA", "RENNER", "DESKTOP", "RECEBIDO"),
                ("SERIE-BBB", "CAMICADO", "COLETOR", "EM TRIAGEM")), start=1):
            a = rc.Asset(company=empresa, category=cat, model=f"MOD-{i}",
                         serial_number=serie, tag_number=f"ETIQ-{i}",
                         asset_number=f"ATV-{i}", description=f"DESCRICAO {i}")
            s.add(a)
            s.flush()
            s.add(rc.ReceiptCycle(asset_id=a.id, received_date=date(2026, 9, i),
                                  status=status, cycle_number=i,
                                  origem_entrada="FORNECEDOR", po=f"PO-{i}",
                                  nf=f"NF-{i}", open=True, created_by="verificacao"))


_semear()
r = cliente.get("/api/recebimentos/export")
checar(r.status_code == 200, f"HTTP 200 ({r.status_code})")
checar("csv" in r.headers.get("content-type", ""), "vem como CSV")
checar("base_recebimentos" in r.headers.get("content-disposition", ""),
       "com nome que diz o que é")

texto = r.text.lstrip("﻿")
checar(r.text.startswith("﻿"), "com BOM — o Excel em pt-BR abre certo")
linhas = list(csv.reader(io.StringIO(texto), delimiter=";"))
checar(linhas[0] == [rot for _c, rot in do_router],
       "o cabeçalho é o da tela")
checar(len(linhas) == 3, f"duas linhas de dados ({len(linhas) - 1})")
por_serie = {l[do_router.index(("numero_serie", "Nº Série"))]: l for l in linhas[1:]}
checar(set(por_serie) == {"SERIE-AAA", "SERIE-BBB"}, "os dois recebimentos saíram")
linha = por_serie["SERIE-AAA"]
valores = dict(zip([c for c, _ in do_router], linha))
checar(valores["empresa"] == "RENNER" and valores["categoria"] == "DESKTOP",
       "com os valores da base")
checar(valores["po"] == "PO-1" and valores["nf"] == "NF-1",
       "inclusive PO e NF, que são do evento de entrada")
checar(valores["origem_entrada"] == "FORNECEDOR",
       "e a origem como está gravada, sem tradução")

# Nada do modelo do ServiceNow pode vazar para cá.
FIXOS_DO_SERVICENOW = ("In stock", "Available", "SPARE - CD324", "Purchase",
                       "Capex", "SL 5 Years", "Serial Number", "Asset tag",
                       "Model category", "Acquisition method")
for fixo in FIXOS_DO_SERVICENOW:
    checar(fixo not in r.text, f"sem {fixo!r} — isso é do modelo do ServiceNow")


print("\n[3] Os filtros da tela valem para o arquivo")
# Filtrar 200 linhas e receber a base inteira é pior que não filtrar: ninguém
# confere 5 mil linhas para descobrir que o filtro não foi aplicado.
r2 = cliente.get("/api/recebimentos/export?empresa=RENNER")
linhas2 = list(csv.reader(io.StringIO(r2.text.lstrip("﻿")), delimiter=";"))
checar(len(linhas2) == 2, f"filtrando por empresa, vem uma linha ({len(linhas2) - 1})")
checar("SERIE-AAA" in r2.text and "SERIE-BBB" not in r2.text, "e é a certa")
r3 = cliente.get("/api/recebimentos/export?status=EM%20TRIAGEM")
checar("SERIE-BBB" in r3.text and "SERIE-AAA" not in r3.text, "idem por status")
r4 = cliente.get("/api/recebimentos/export?q=SERIE-AAA")
checar("SERIE-AAA" in r4.text and "SERIE-BBB" not in r4.text, "idem pela busca")

# A listagem e a exportação usam a MESMA consulta: separadas, uma podia
# ganhar filtro e a outra não.
fonte = (RAIZ / "routers" / "recebimento.py").read_text(encoding="utf-8")
checar(fonte.count("def _consulta_base(") == 1, "a consulta está escrita uma vez só")
checar(fonte.count("_consulta_base(") >= 3,
       "e a listagem e a exportação chamam a mesma")
lista = cliente.get("/api/recebimentos?empresa=RENNER").json()["registros"]
checar(len(lista) == len(linhas2) - 1,
       "e a tela e o arquivo trazem a mesma quantidade com o mesmo filtro")


print("\n[4] O modelo do ServiceNow continua existindo, separado")
# Não foi removido: alimenta o ServiceNow e alguém depende dele. O que mudou
# é que deixou de ser o ÚNICO, e o botão agora diz o que ele é.
r5 = cliente.get("/api/recebimentos/export-servicenow")
checar(r5.status_code == 200, f"continua respondendo ({r5.status_code})")
checar("Serial Number" in r5.text and "In stock" in r5.text,
       "com as colunas e os valores fixos do formato do ServiceNow")
checar("bf-export" in js and "bf-snow" in js, "e a tela tem os dois botões")
checar("Exportar base" in js, "o da base, em destaque")
checar("Não é a base." in js,
       "e o do ServiceNow diz, no título, que não é a base")
checar("S.baixar('/recebimentos/export?'" in js,
       "o botão da base leva os filtros da tela")


print("\n[5] Exportar é permissão própria")
_, ck = security.create_session({"username": "so-ve", "is_admin": False,
                                 "permission_map": {"recebimento": {"can_view": True}}})
so_ve = TestClient(app)
so_ve.cookies.set("spare_session", ck)
checar(so_ve.get("/api/recebimentos").status_code == 200, "quem tem 'ver' lista")
checar(so_ve.get("/api/recebimentos/export").status_code == 403,
       "mas exportar pede a permissão de exportar")
anon = TestClient(app)
checar(anon.get("/api/recebimentos/export").status_code in (401, 403),
       "e sem sessão não responde")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Exportação da Base de Recebimentos íntegra.")
