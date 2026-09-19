from __future__ import annotations

import re as _re

from config import get_settings
from core.estatico import limpar_texto

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

    Aqui também saem os comentários da página. Toda tela servida por rota
    Python (index, cockpit, indicadores, obsolescência, times, ebs-forms)
    lê o arquivo e passa por esta função antes de responder — é o ponto por
    onde todas passam, então limpar aqui cobre todas de uma vez, sem mexer
    em cada rota. `/static` tem o seu próprio limpador na entrega.
    """
    # Antes do prefixo, de propósito: assim o que se reescreve depois é só
    # o HTML que vai de fato para o navegador, e um href comentado não gera
    # substituição à toa.
    html = limpar_texto(html, ".html")
    # O ícone da aba tem rota própria (/favicon.ico), e é ela que serve o
    # arquivo enviado pelo admin. Página que aponta direto para o arquivo
    # padrão em /static nunca vê o ícone novo — foi o que acontecia no
    # Controle de Orçamento. Aqui o alvo é trocado na entrega, sem mexer
    # no HTML de cada módulo.
    # O `type` vai junto: a rota entrega o que o admin enviou, que pode ser
    # PNG ou ICO, e um type mentindo svg+xml confunde o navegador.
    html = _re.sub(r'="/static/favicon\.svg"(\s+type="[^"]*")?', '="/favicon.ico"', html)

    marcas = ""
    if base:
        # TODO caminho absoluto de uma página servida por este portal é
        # deste portal — não existe href="/x" que aponte para outro sistema
        # (esse viria com http:// ou //). Então todos levam o prefixo, e não
        # só /static: href="/", "/controle-orcamento", "/obsolescencia" e
        # "/orcamento-spare" iam para a raiz do domínio e davam 404.
        # O (?!{base}/) evita prefixar duas vezes quem já veio pronto.
        html = _re.sub(r'(href|src|action)="/(?!/)(?!' + _re.escape(base.lstrip("/")) + r'/)',
                       rf'\1="{base}/', html)
        if 'name="app-base"' not in html:
            marcas += f'\n    <meta name="app-base" content="{base}">'
    # O contorno da barra vale mesmo sem prefixo: quem decide é a chave.
    if _cfg.API_BARRA_FINAL and 'name="api-barra-final"' not in html:
        marcas += '\n    <meta name="api-barra-final" content="1">'
    if marcas:
        html = html.replace("<head>", "<head>" + marcas, 1)
    return html
