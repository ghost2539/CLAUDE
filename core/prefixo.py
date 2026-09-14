"""Prefixo do portal quando ele é servido num subcaminho do proxy.

Servido em `suporte.lojasrenner.com.br/portal-spare`, o navegador precisa
buscar `/portal-spare/static/...` e `/portal-spare/api/...`. Sem o prefixo
ele bate na raiz do domínio e recebe 404 — a tela abre sem estilo.

De onde sai o prefixo, nesta ordem:

1. `root_path` da requisição — é o que o `--root-path /portal-spare` do
   uvicorn coloca no ASGI. Não precisa de variável de ambiente nenhuma:
   quem já roda com `--root-path` está pronto.
2. `APP_BASE_PATH`, quando alguém preferir declarar explicitamente (ou
   quando o serviço não usa `--root-path`).
3. Vazio — o portal está na raiz do domínio e nada muda.

Assim o mesmo código serve a produção (na raiz) e a migração (atrás do
proxy), sem depender de quem edita a unit do systemd.
"""
from __future__ import annotations

from config import get_settings

_cfg = get_settings()


def prefixo(req=None) -> str:
    """Prefixo sem barra no fim: "/portal-spare" ou "" na raiz."""
    if req is not None:
        # `root_path` chega pelo escopo ASGI; em Starlette também está em
        # `req.scope`. Vem sem barra no fim, mas não custa garantir.
        bruto = ""
        try:
            bruto = req.scope.get("root_path") or ""
        except AttributeError:
            bruto = ""
        bruto = str(bruto).rstrip("/")
        if bruto:
            return bruto
    return (_cfg.APP_BASE_PATH or "").rstrip("/")


def destino(req) -> str:
    """Caminho da requisição com o prefixo, sem duplicá-lo.

    Serve para o `?next=` do login. O cuidado é que, com `--root-path`, o
    Starlette já devolve `url.path` COM o prefixo; somar de novo daria
    `/portal-spare/portal-spare/...` e o login voltaria para lugar nenhum.
    """
    base = prefixo(req)
    caminho = req.url.path or "/"
    if base and caminho != base and not caminho.startswith(base + "/"):
        caminho = base + caminho
    return caminho


class BarraFinalMiddleware:
    """Tira a barra do fim dos caminhos de `/api` antes de rotear.

    Alguns proxies acrescentam uma barra ao encaminhar. Nenhuma rota de
    `/api` é registrada com barra no fim, então o Starlette responderia 307
    para o caminho sem ela — e o `Location` desse redirecionamento é uma URL
    ABSOLUTA, montada com o esquema que chegou ao processo. Atrás do proxy
    isso vira `http://` numa página `https://`, o navegador bloqueia como
    conteúdo misto e a chamada morre como "Failed to fetch".

    Aparar a barra aqui resolve na origem: a rota casa de primeira e nenhum
    redirecionamento é emitido. As páginas (fora de `/api`) não são tocadas —
    lá a barra no fim é registrada de propósito.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            caminho = scope.get("path", "")
            if len(caminho) > 1 and caminho.endswith("/"):
                sem_barra = caminho.rstrip("/")
                # Sob `--root-path`, o uvicorn entrega o caminho COM o
                # prefixo; o TestClient entrega sem. O que decide é o que
                # sobra depois do prefixo, então ele é descontado aqui.
                raiz = (scope.get("root_path") or "").rstrip("/")
                resto = sem_barra
                if raiz and resto.startswith(raiz):
                    resto = resto[len(raiz):] or "/"
                # Só a API: as páginas têm rota própria com barra no fim.
                if resto.startswith("/api/") or resto == "/api":
                    scope = dict(scope, path=sem_barra)
                    bruto = scope.get("raw_path")
                    if bruto:
                        scope["raw_path"] = bruto.rstrip(b"/") or b"/"
        await self.app(scope, receive, send)


def com_prefixo(html: str, base: str) -> str:
    """Aplica o prefixo numa página que o portal serve inteira.

    Prefixa os `/static/...` do topo e injeta o `<meta name="app-base">`,
    que é como o JavaScript da página descobre onde ficam a API e os
    módulos. Base vazia devolve o HTML como veio.
    """
    if not base:
        return html
    html = html.replace('="/static/', f'="{base}/static/')
    if 'name="app-base"' not in html:
        html = html.replace(
            "<head>", f'<head>\n    <meta name="app-base" content="{base}">', 1)
    return html
