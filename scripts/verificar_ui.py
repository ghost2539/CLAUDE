#!/usr/bin/env python3
"""Checklist de release do padrão de UI SPARE.

Trava o que já foi corrigido uma vez e tende a voltar: fonte ou folha
vinda de fora, raio na estrutura, o toggle de tema sem acessibilidade,
divisor de KPI pelo fundo do contêiner e a numeração da sidebar.

    python3 scripts/verificar_ui.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALHAS: list[str] = []
TOTAL = 0


def checar(cond: object, msg: str) -> None:
    global TOTAL
    TOTAL += 1
    print(f"  {'ok  ' if cond else 'FALHA'} {msg}")
    if not cond:
        FALHAS.append(msg)


def ler(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


TELAS = ("static/index.html", "static/obsolescencia/index.html",
         "static/ebs-forms/index.html", "static/indicadores/index.html")
FOLHAS = ("static/app.css", "static/obsolescencia/app.css", "static/ebs-forms/app.css")

print("[1] Nada vem de fora")
for arq in TELAS:
    html = ler(arq)
    checar("fonts.googleapis" not in html and "fonts.gstatic" not in html,
           f"{arq}: sem CDN de fonte")
    # Link que o usuário clica (Teams, por exemplo) é outra coisa: o que
    # não pode é o navegador BUSCAR script, folha ou fonte fora do portal.
    externos = re.findall(r'<(?:script[^>]*\ssrc|link[^>]*\shref)="(https?://[^"]+)"', html)
    checar(not externos, f"{arq}: nenhum script ou folha externa ({externos[:1]})")
csp = ler("core/security.py")
checar("\"style-src 'self' 'unsafe-inline'; \"" in csp, "CSP: estilo só do próprio domínio")
checar("\"font-src 'self'; \"" in csp, "CSP: fonte só do próprio domínio")

print("\n[2] Arial em todo o sistema")
css = ler("static/app.css")
checar("--sp-sans: Arial, Helvetica, sans-serif;" in css, "token da família é Arial")
checar("--sp-num: var(--sp-sans);" in css and "--sp-label: var(--sp-sans);" in css,
       "número e rótulo usam a mesma família")
for arq in FOLHAS + TELAS:
    texto = ler(arq)
    checar("IBM Plex" not in texto and "Roboto Mono" not in texto and "Inter," not in texto,
           f"{arq}: sem fonte de desvio")
checar(not (RAIZ / "static/fonts").exists(), "pasta de fontes hospedadas removida")
checar("tabular-nums" in css, "alinhamento de dígito por tabular-nums")

print("\n[3] Estrutura reta, controle arredondado")
def bloco(seletor: str) -> str:
    """Corpo da regra cujo seletor começa na coluna 1 — senão uma regra
    aninhada como `.x .btn { }` seria confundida com a do componente."""
    m = re.search(r"(?m)^" + re.escape(seletor) + r"\{([^}]*)\}", css)
    return m.group(1) if m else ""
checar("border-radius: var(--sp-raio-botao)" in bloco(".btn "), "botão com raio de controle")
checar("border-radius: var(--sp-raio-campo)" in bloco(".form-control "), "campo com raio de controle")
checar("border-radius: var(--sp-raio-badge)" in bloco(".badge "), "badge com raio próprio")
checar("border-radius: var(--sp-raio-chip)" in bloco(".integ-pilula "), "pastilha com raio de chip")
for seletor in (".card ", ".table-wrapper ", ".sidebar ", ".topbar "):
    checar("border-radius" not in bloco(seletor), f"{seletor.strip()} segue reto")
checar("box-shadow: 0 1px 2px rgba(171, 72, 7, .28)" in bloco(".btn-primary "),
       "a única sombra do sistema é a do botão primário")
checar("box-shadow" not in bloco(".card "), "cartão sem sombra")

print("\n[4] Toggle de tema")
html = ler("static/index.html")
js = ler("js/app.js")
checar('id="tema-toggle"' in html and 'role="switch"' in html, "toggle com role=switch")
checar('aria-label="Alternar tema escuro"' in html, "toggle tem rótulo acessível")
checar("tema-seg" not in html and "tema-seg" not in css, "o par de botões saiu")
checar("aria-checked" in js and "Mudar para tema claro" in js, "estado e dica acompanham o tema")
checar("'/auth/preferencias'" in js, "a escolha vai para o perfil")
checar("spare-tema" in js, "e o navegador guarda um cache para não piscar")
checar(":root[data-tema=\"escuro\"] .tema-toggle-knob { transform: translateX(26px); }" in css,
       "knob desliza 26px no tema escuro")

print("\n[5] KPI e sidebar")
checar("outline: 1px solid var(--sp-border)" in bloco(".stat-card "),
       "divisor do KPI pelo contorno da célula")
checar("background" not in bloco(".stats-grid ") and "border:" not in bloco(".stats-grid "),
       "a faixa de KPI não pinta fundo nem borda (célula vazia não vira bloco cinza)")
checar("counter-increment" not in css and "counter-reset" not in css,
       "sidebar sem numeração")
checar("padding: 8px 13px;" in bloco(".sidebar-item "), "item do menu alinhado ao topo")

print(f"\n{TOTAL - len(FALHAS)} de {TOTAL} verificações passaram.")
if FALHAS:
    print("Falhas:")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("Padrão de UI íntegro.")
