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
