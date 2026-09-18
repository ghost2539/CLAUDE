#!/usr/bin/env python3
"""Nenhuma navegação com caminho absoluto: todas passam pelo prefixo do proxy.

    python3 scripts/verificar_links_prefixo.py

O portal roda num SUBCAMINHO do proxy corporativo
(`https://suporte.lojas.../portal-spare/`). Um caminho absoluto como
`/api/recebimentos/export-servicenow` não resolve contra o portal: resolve
contra a RAIZ do domínio, e cai no sistema do lado. Foi o que aconteceu com
o botão de exportar a base de Recebimentos — ele abria a tela de API do
`suporte.lojas...`, não a do portal.

Por que aconteceu, e é isto que esta verificação impede de voltar: o helper
que põe o prefixo (`apiUrl`) existia em `static/app.js` mas **não era
exportado** em `window.SPARE`. Quem escrevia módulo não tinha como acertar,
e escrevia o caminho absoluto. Um dos módulos chegou a tentar
(`var base = (S.base || '')`) com um `S.base` que também não existia — o
`|| ''` engolia a falta e o resultado era o mesmo caminho absoluto, só que
com a aparência de estar tratado.

`fetch`/`S.api` NÃO são o problema: aquele caminho já passa por `apiUrl`. O
que se procura aqui é NAVEGAÇÃO — `window.location`, `window.open`, `href` —
que é onde o prefixo se perde.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


print("[1] O helper do prefixo está ao alcance de quem escreve módulo")
app = (RAIZ / "static" / "app.js").read_text(encoding="utf-8")
for nome in ("apiUrl: apiUrl", "base: APP_BASE", "baixar: baixar",
             "urlDoPortal: urlDoPortal"):
    checar(nome in app, f"window.SPARE exporta {nome.split(':')[0]}")
checar("function baixar(caminho)" in app and "apiUrl(caminho)" in app,
       "e S.baixar() monta a URL pelo apiUrl, não à mão")


print("\n[2] Nenhuma navegação com caminho absoluto nos módulos")
# `window.location = '/...'`, `window.location.href = '/...'`,
# `window.open('/...')` e `href="/..."` em HTML montado por string.
PADROES = (
    re.compile(r"""window\.location(?:\.href)?\s*=\s*['"]/"""),
    re.compile(r"""window\.open\(\s*['"]/"""),
    re.compile(r"""href\s*=\s*\\?['"]/(?!/)"""),
)
# `//outro.dominio` é URL absoluta de outro serviço, não caminho do portal.
arquivos = sorted((RAIZ / "modulos").glob("*.js")) + [RAIZ / "static" / "app.js"]


def _sem_comentario(texto: str) -> list[str]:
    """As linhas do arquivo com os comentários apagados (mas na mesma posição).

    A primeira versão pulava a linha que COMEÇA com `//` ou `*`. Não bastou:
    um comentário de bloco explicando justamente este defeito, com a linha
    errada citada dentro, foi acusado — e o detector passou a apontar para o
    texto que explica o problema em vez de para o problema. Comentário
    apagado no lugar, preservando o número da linha.
    """
    saida, dentro = [], False
    for linha in texto.splitlines():
        fora = []
        i = 0
        while i < len(linha):
            if dentro:
                fim = linha.find("*/", i)
                if fim == -1:
                    break
                dentro, i = False, fim + 2
                continue
            ini = linha.find("/*", i)
            lin = linha.find("//", i)
            if lin != -1 and (ini == -1 or lin < ini):
                fora.append(linha[i:lin])
                break
            if ini == -1:
                fora.append(linha[i:])
                break
            fora.append(linha[i:ini])
            dentro, i = True, ini + 2
        saida.append("".join(fora))
    return saida


achados: list[str] = []
for arq in arquivos:
    for n, linha in enumerate(_sem_comentario(arq.read_text(encoding="utf-8")), 1):
        for padrao in PADROES:
            if padrao.search(linha):
                achados.append(f"{arq.relative_to(RAIZ)}:{n}: {linha.strip()[:90]}")
                break
for a in achados:
    print("       " + a)
checar(not achados,
       f"nenhum caminho absoluto em navegação ({len(achados)} encontrado(s))")


print("\n[3] Os pontos que estavam quebrados, um a um")
# Nomeados um a um: um `grep` genérico passa a existir, mas estes são os que
# custaram a descoberta, e cada um tem de continuar certo.
pontos = [
    ("modulos/recebimento.js", "S.baixar('/recebimentos/export-servicenow')",
     "exportar a base de Recebimentos — o que foi relatado"),
    ("modulos/servicenow.js", "S.baixar('/servicenow/entrada/planilha-modelo')",
     "planilha modelo da entrada de ativos"),
    ("modulos/gestao_ativos.js", "S.urlDoPortal('/obsolescencia')",
     "painel de Obsolescência (página do portal, não da API)"),
    ("modulos/internalizacao.js", "S.apiUrl('/internalizacao/'",
     "exportação da internalização — usava um S.base que não existia"),
    # Estes dois não foram relatados: a varredura da seção [2] os encontrou
    # no mesmo passo. Ficam nomeados porque quebravam calados — ícone errado
    # na aba e um botão que abria a raiz do domínio.
    ("modulos/parametros_admin.js", "urlDoPortal('/favicon.ico')",
     "ícone da aba"),
    ("modulos/parametros_admin.js", "S.urlDoPortal('/' + chave)",
     "botão 'Abrir a tela' dos dashboards"),
]
for arquivo, marca, o_que in pontos:
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    checar(marca in texto, f"{o_que} ({arquivo})")


print("\n[4] Contraprova: o detector pega o que tem de pegar")
# Sem isto, a seção [2] passaria com um `re` que não casa com nada.
CASOS_RUINS = (
    "window.location = '/api/x';",
    'window.location.href = "/api/x";',
    "window.open('/obsolescencia', '_blank');",
    """c.innerHTML = '<a href="/api/x">baixar</a>';""",
)
CASOS_BONS = (
    "S.baixar('/recebimentos/export');",
    "window.open(S.urlDoPortal('/obsolescencia'), '_blank');",
    "window.open(S.apiUrl('/x/exportar'), '_blank');",
    "var url = URL.createObjectURL(blob); window.open(url, '_blank');",
    "window.open('https://outro.servico/pagina', '_blank');",
    "fetch(apiUrl('/api/x'))",
)
for caso in CASOS_RUINS:
    checar(any(p.search(caso) for p in PADROES), f"acusa: {caso[:52]}")
for caso in CASOS_BONS:
    checar(not any(p.search(caso) for p in PADROES), f"não acusa: {caso[:52]}")

# E a contraprova de verdade: o código ANTERIOR ao conserto seria acusado.
antes = "        window.location = '/api/recebimentos/export-servicenow';"
checar(any(p.search(antes) for p in PADROES),
       "contraprova: a linha exata que quebrou o botão seria acusada hoje")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Links do portal íntegros: tudo passa pelo prefixo do proxy.")
