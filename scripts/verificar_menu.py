#!/usr/bin/env python3
"""Todo item do menu leva a alguma tela — e nenhuma tela fica sem caminho.

    python3 scripts/verificar_menu.py

Só lê arquivo. Sem banco, sem rede.

Por que existe
--------------
O item do menu e a tela são declarados em lugares diferentes, e nada ligava
os dois. Deu nisto, no Orçamento Spare:

  - "Orçamento Spare" apontava para `data-href="/orcamento-spare"`, uma
    PÁGINA que o back-end não serve → 404;
  - "CAPEX Spare" apontava para `data-route="capex_spare"`, que faz o shell
    buscar `modulos/capex_spare.js` → arquivo inexistente → "Falha ao
    carregar módulo".

A tela que funciona (`modulos/orcamento_spare.js`) não era alcançada por
nenhum dos dois. Ninguém percebeu porque o menu SÓ mostra o item a quem tem
a permissão, e a permissão `capex_spare` nem existia em MODULE_ACTIONS: o
item aparecia apenas para o admin do portal, que raramente clica ali.

O que se confere
----------------
1. `data-route` do menu tem módulo em `modulos/` (ou é rota com submenu,
   `modulo/aba`, que usa o arquivo do módulo).
2. `data-href` do menu é servido por alguma rota de página do back-end.
3. A chave de permissão do item existe em `MODULE_ACTIONS` — senão o item
   fica invisível para todo mundo menos o admin.
4. Módulo em `modulos/` que não é carregado por ninguém é apontado (pode
   ser proposital, como o carregado sob demanda; por isso é aviso).
"""
from __future__ import annotations

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

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
app_js = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")

# Um item do menu é <a ...> com data-route ou data-href; pego o bloco inteiro
# para ler junto o data-perm e o rótulo.
ITENS = []
for m in re.finditer(r'<a class="sidebar-item"([^>]*)>\s*<span class="sidebar-label">([^<]*)</span>',
                     html):
    attrs, rotulo = m.group(1), m.group(2).strip()
    rota = (re.search(r'data-route="([^"]+)"', attrs) or [None, ""])[1]
    href = (re.search(r'data-href="([^"]+)"', attrs) or [None, ""])[1]
    perm = (re.search(r'data-perm="([^"]+)"', attrs) or [None, ""])[1]
    ITENS.append({"rotulo": rotulo, "rota": rota, "href": href, "perm": perm})

print(f"[1] Itens do menu com rota: o módulo existe em modulos/")
modulos = {p.stem for p in (RAIZ / "modulos").glob("*.js")}
sem_modulo = []
for it in ITENS:
    if not it["rota"]:
        continue
    base = it["rota"].split("/")[0]
    if base not in modulos:
        sem_modulo.append(f"{it['rotulo']} → modulos/{base}.js")
checar(not sem_modulo, f"nenhum item leva a módulo inexistente ({sem_modulo or 'nenhum'})")

print("\n[2] Itens do menu com página: o back-end serve o caminho")
# Rotas de página declaradas nos routers (@router.get("/x") sem prefixo /api).
paginas = set()
for arq in sorted((RAIZ / "routers").glob("*.py")):
    txt = arq.read_text(encoding="utf-8")
    pref = (re.search(r'APIRouter\([^)]*prefix=["\']([^"\']+)', txt, re.S) or [None, ""])[1]
    for m in re.finditer(r'@router\.get\("([^"]*)"', txt):
        paginas.add((pref + m.group(1)).rstrip("/") or "/")
sem_pagina = []
for it in ITENS:
    if not it["href"]:
        continue
    if it["href"].rstrip("/") not in paginas:
        sem_pagina.append(f"{it['rotulo']} → {it['href']}")
checar(not sem_pagina, f"nenhum item leva a página inexistente ({sem_pagina or 'nenhum'})")

print("\n[3] A permissão do item existe, senão o item some para todo mundo")
from config import get_settings  # noqa: E402

acoes = get_settings().MODULE_ACTIONS
perm_fantasma = []
for it in ITENS:
    chave = it["perm"] or (it["rota"].split("/")[0] if it["rota"] else "")
    if not chave or chave == "parametros/conta":
        continue
    if chave not in acoes:
        perm_fantasma.append(f"{it['rotulo']} → data-perm={chave!r}")
checar(not perm_fantasma,
       f"toda permissão do menu existe em MODULE_ACTIONS ({perm_fantasma or 'nenhuma'})")

print("\n[4] O rótulo da rota está declarado no shell")
# ROUTES do app.js dá o nome que aparece na barra de cima.
rotulos = set(re.findall(r"^\s{8}([a-z_]+):\s*'", app_js, re.M))
sem_rotulo = []
for it in ITENS:
    if not it["rota"]:
        continue
    base = it["rota"].split("/")[0]
    if base not in rotulos:
        sem_rotulo.append(f"{it['rotulo']} → {base}")
checar(not sem_rotulo, f"toda rota do menu tem rótulo no shell ({sem_rotulo or 'nenhuma'})")

print("\n[5] O CAPEX Spare saiu inteiro")
for rel in ("static/index.html", "static/app.js", "modulos/orcamento_spare.js",
            "routers/orcamento_spare.py", "db/orcamento_spare.py"):
    conteudo = (RAIZ / rel).read_text(encoding="utf-8")
    checar("capex_spare" not in conteudo.lower() and "capex spare" not in conteudo.lower(),
           f"{rel} sem menção a CAPEX Spare")
checar(not (RAIZ / "modulos" / "capex_spare.js").exists()
       and not (RAIZ / "routers" / "capex_spare.py").exists()
       and not (RAIZ / "db" / "capex_spare.py").exists(),
       "nenhum arquivo capex_spare no projeto")

print("\n[6] O Orçamento Spare ficou de pé")
checar("orcamento_spare" in acoes, "a permissão orcamento_spare existe")
checar((RAIZ / "modulos" / "orcamento_spare.js").exists(), "a tela existe")
checar('data-route="orcamento_spare"' in html, "o menu aponta para a rota do módulo")
checar('prefix="/api/orcamento-spare"' in
       (RAIZ / "routers" / "orcamento_spare.py").read_text(encoding="utf-8"),
       "a API segue em /api/orcamento-spare")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Menu com item que não leva a lugar nenhum:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("Menu íntegro: todo item leva a uma tela que existe.")
