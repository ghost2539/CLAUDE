#!/usr/bin/env python3
"""Verificação do prefixo de subcaminho (portal atrás do proxy).

    python3 scripts/verificar_prefixo.py

O portal roda em `suporte.lojasrenner.com.br/portal-spare`. O prefixo sai
do `--root-path` do uvicorn (que a unit do systemd já usa) ou do
`APP_BASE_PATH`; na raiz do domínio é vazio e nada muda.

Sem rede e sem banco: só as funções puras de `core/prefixo.py`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")

from core import prefixo as pf  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def req(root_path: str, caminho: str):
    """Requisição de mentira com o que as funções usam.

    Reproduz o Starlette: com `root_path`, `url.path` JÁ vem com o prefixo.
    """
    completo = (root_path.rstrip("/") + caminho) if root_path else caminho
    return SimpleNamespace(scope={"root_path": root_path},
                           url=SimpleNamespace(path=completo))


print("\n[1] O prefixo sai do --root-path, sem variável de ambiente")
pf._cfg.APP_BASE_PATH = ""
checar(pf.prefixo(req("/portal-spare", "/")) == "/portal-spare",
       "root_path vira o prefixo")
checar(pf.prefixo(req("/portal-spare/", "/")) == "/portal-spare",
       "barra sobrando no fim é aparada")
checar(pf.prefixo(req("", "/")) == "", "sem root_path e sem variável, prefixo vazio")
checar(pf.prefixo(None) == "", "sem requisição nenhuma, prefixo vazio")

print("\n[2] APP_BASE_PATH continua valendo quando não há --root-path")
pf._cfg.APP_BASE_PATH = "/portal-spare"
checar(pf.prefixo(req("", "/")) == "/portal-spare", "a variável é o segundo caminho")
checar(pf.prefixo(None) == "/portal-spare", "vale mesmo fora de uma requisição")
checar(pf.prefixo(req("/outro", "/")) == "/outro",
       "com os dois, o root_path da requisição manda")
pf._cfg.APP_BASE_PATH = ""

print("\n[3] A página ganha o prefixo nos arquivos e o <meta> para o JS")
HTML = ('<html><head>\n<link rel="stylesheet" href="/static/app.css">\n'
        '<script defer src="/static/app.js"></script>\n</head><body></body></html>')
saida = pf.com_prefixo(HTML, "/portal-spare")
checar('href="/portal-spare/static/app.css"' in saida, "o CSS leva o prefixo")
checar('src="/portal-spare/static/app.js"' in saida, "o JS leva o prefixo")
checar('<meta name="app-base" content="/portal-spare">' in saida,
       "o <meta> diz o prefixo ao app.js")
checar('"/static/' not in saida.replace("/portal-spare/static/", ""),
       "não sobra nenhum /static sem prefixo")
checar(pf.com_prefixo(HTML, "") == HTML,
       "base vazia devolve a página exatamente como veio")
checar(pf.com_prefixo(saida, "/portal-spare").count("app-base") == 1,
       "aplicar duas vezes não duplica o <meta>")

print("\n[4] O destino do login não duplica o prefixo")
# Este era o defeito: com --root-path, url.path já traz o prefixo, e somar
# de novo mandava o login para /portal-spare/portal-spare/...
checar(pf.destino(req("/portal-spare", "/controle-orcamento"))
       == "/portal-spare/controle-orcamento",
       f"com root_path ({pf.destino(req('/portal-spare', '/controle-orcamento'))})")
pf._cfg.APP_BASE_PATH = "/portal-spare"
checar(pf.destino(req("", "/obsolescencia")) == "/portal-spare/obsolescencia",
       "só com a variável, o prefixo é acrescentado")
pf._cfg.APP_BASE_PATH = ""
checar(pf.destino(req("", "/obsolescencia")) == "/obsolescencia",
       "na raiz do domínio, o caminho vai como está")
checar(pf.destino(req("/portal-spare", "/")) == "/portal-spare/",
       "a raiz do portal com prefixo")

print("\n[5] Barra no fim da API não vira redirecionamento")
import asyncio  # noqa: E402


def passar(path, root_path=""):
    """Roda o middleware e devolve o caminho que ele entrega adiante."""
    visto = {}

    async def app(scope, receive, send):
        visto["path"] = scope.get("path")
        visto["raw"] = scope.get("raw_path")

    mw = pf.BarraFinalMiddleware(app)
    scope = {"type": "http", "path": path, "root_path": root_path,
             "raw_path": path.encode()}
    asyncio.run(mw(scope, None, None))
    return visto


# Como o uvicorn entrega sob --root-path: o caminho vem COM o prefixo.
r = passar("/portal-spare/api/auth/me/", "/portal-spare")
checar(r["path"] == "/portal-spare/api/auth/me",
       f"com prefixo no caminho, a barra é aparada ({r['path']})")
checar(r["raw"] == b"/portal-spare/api/auth/me", "o raw_path acompanha")

# Como o TestClient entrega: sem o prefixo.
r = passar("/api/auth/me/", "/portal-spare")
checar(r["path"] == "/api/auth/me", f"sem prefixo no caminho, idem ({r['path']})")

# Na raiz do domínio, sem prefixo nenhum.
r = passar("/api/auth/me/")
checar(r["path"] == "/api/auth/me", "na raiz do domínio também")

# O que NÃO pode ser tocado: as páginas têm rota própria com barra no fim.
for caminho, raiz in (("/obsolescencia/", ""), ("/portal-spare/obsolescencia/", "/portal-spare"),
                      ("/controle-orcamento/", ""), ("/", ""), ("/portal-spare/", "/portal-spare")):
    r = passar(caminho, raiz)
    checar(r["path"] == caminho, f"página intacta: {caminho}")

# Caminho de API sem barra passa direto, sem mexer.
r = passar("/portal-spare/api/auth/me", "/portal-spare")
checar(r["path"] == "/portal-spare/api/auth/me", "sem barra, nada muda")

print("\n[6] Contorno do proxy que acrescenta barra por redirecionamento")
# Com a chave ligada, a página avisa o front-end para já pedir com a barra.
pf._cfg.API_BARRA_FINAL = True
saida = pf.com_prefixo(HTML, "/portal-spare")
checar('<meta name="api-barra-final" content="1">' in saida,
       "a marca chega à página junto com o prefixo")
so_marca = pf.com_prefixo(HTML, "")
checar('api-barra-final' in so_marca,
       "vale mesmo na raiz do domínio, se alguém precisar")
checar('app-base' not in so_marca, "e sem prefixo não inventa app-base")
checar(pf.com_prefixo(saida, "/portal-spare").count("api-barra-final") == 1,
       "aplicar duas vezes não duplica a marca")
pf._cfg.API_BARRA_FINAL = False
checar('api-barra-final' not in pf.com_prefixo(HTML, "/portal-spare"),
       "desligada, a marca não aparece — é este o padrão")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Prefixo de subcaminho íntegro.")
