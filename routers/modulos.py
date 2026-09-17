"""Entrega o JavaScript dos módulos, conferindo quem pode receber cada um.

Antes estes arquivos moravam em `static/modules/` e saíam pelo mount público:
`GET /static/modules/parametros.js` devolvia 135 KB **sem sessão nenhuma**, ou
seja, a tela de administração inteira ficava disponível para qualquer pessoa
que soubesse o endereço. Não era um usuário vendo demais — era a internet.

Por isso os arquivos saíram de `static/`: enquanto estiverem lá, qualquer
mount os publica de novo, e o buraco volta sem ninguém perceber. Fora de
`static/` a única porta é esta, e esta pergunta quem é antes de responder.

A regra de quem recebe o quê é a MESMA do menu (`buildMenu`, em app.js): se o
item aparece para a pessoa, o arquivo tem de ser entregue a ela. Se as duas
regras discordarem, a tela abre e morre em "Falha ao carregar módulo" — um
defeito pior que o original, porque parece intermitente.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

import config as _config_mod
from core.estatico import limpar_arquivo
from core.security import get_session, require_permission

_cfg = _config_mod.get_settings()
_log = logging.getLogger("modulos")

router = APIRouter(tags=["Módulos (JavaScript)"])

RAIZ = _cfg.STATIC.parent
DIR_PORTAL = RAIZ / "modulos"
DIR_TIMES = RAIZ / "modulos-times"

# Nome de módulo é minúsculo, começa por letra e não tem ponto nem barra.
# Assim `../`, `..%2f` e nome com caminho dentro nem chegam a virar arquivo.
import re as _re

_NOME_VALIDO = _re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# Item cuja permissão não tem o nome da rota. Sai do `data-perm` do
# index.html: Gestão de Ativos herda "servicenow" para ninguém perder acesso
# quando a tela muda de lugar no menu.
PERMISSAO_DE = {
    "gestao_ativos": "servicenow",
}

# Módulos que qualquer pessoa logada recebe. Dois motivos diferentes:
#
# - `parametros` porque a aba "Minha conta" mora nele e é de todo mundo.
#   Exigir a permissão "parametros" aqui tirava a conta de todos os que não
#   são admin. As onze telas de administração NÃO estão neste arquivo: elas
#   vivem em `parametros_admin`, logo abaixo, que essa permissão protege.
# - os demais são telas genéricas, sem item de menu com `data-perm`.
SEM_PERMISSAO_PROPRIA = {
    "parametros", "consulta", "identificacao", "orcamento_manutencao",
    "rastreio", "recebimento", "status", "bemvindo",
}

# O que só admin de Parâmetros recebe.
SO_ADMIN_PARAMETROS = {"parametros_admin"}


def _arquivo(diretorio, nome: str):
    """Caminho do módulo dentro do diretório, ou None se não couber lá.

    A conferência é feita no caminho RESOLVIDO, e não só na regex: defesa de
    travessia que depende de uma expressão regular passar é defesa que morre
    no dia em que alguém afrouxar a expressão.
    """
    if not _NOME_VALIDO.match(nome or ""):
        return None
    alvo = (diretorio / f"{nome}.js").resolve()
    try:
        alvo.relative_to(diretorio.resolve())
    except ValueError:
        return None
    return alvo if alvo.is_file() else None


def _autorizar(req: Request, nome: str) -> None:
    """Deixa passar, ou levanta 401/403. Sessão sempre; permissão quando há."""
    if nome in SO_ADMIN_PARAMETROS:
        require_permission(req, "parametros", "view")
        return
    if nome in SEM_PERMISSAO_PROPRIA:
        get_session(req)  # basta estar logado
        return
    chave = PERMISSAO_DE.get(nome, nome)
    if chave in getattr(_cfg, "MODULE_ACTIONS", {}):
        require_permission(req, chave, "view")
        return
    # Módulo sem chave de permissão declarada: sessão é o que se pode exigir.
    # Negar seria inventar uma regra que o menu não aplica, e sumir com uma
    # tela que a pessoa usa hoje.
    get_session(req)


def _entregar(req: Request, caminho) -> Response:
    """Responde o arquivo sem comentários, com ETag e 304."""
    st = caminho.stat()
    # O ETag descreve o que SAI, não o arquivo no disco: o conteúdo entregue
    # é o limpo, e um ETag igual ao do arquivo cru faria o navegador que
    # guardou a versão comentada continuar usando ela.
    etag = f'"{int(st.st_mtime)}-{st.st_size}-mod"'
    if req.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag,
                                                  "Cache-Control": "no-cache"})
    corpo = limpar_arquivo(caminho)
    if corpo is None:
        corpo = caminho.read_bytes()
    return Response(
        corpo,
        media_type="application/javascript; charset=utf-8",
        headers={"ETag": etag, "Cache-Control": "no-cache",
                 # Módulo é de quem pediu: cache compartilhado de proxy não
                 # pode guardar a resposta de um e servir a outro.
                 "Vary": "Cookie"},
    )


@router.get("/modulos/{nome}.js")
def modulo_portal(nome: str, req: Request):
    caminho = _arquivo(DIR_PORTAL, nome)
    # A autorização vem ANTES de dizer se o arquivo existe: responder 404 só
    # para quem passou na permissão evita usar esta rota para descobrir quais
    # módulos o portal tem.
    _autorizar(req, nome)
    if caminho is None:
        raise HTTPException(404, "Módulo não encontrado.")
    return _entregar(req, caminho)


@router.get("/modulos-times/{nome}.js")
def modulo_times(nome: str, req: Request):
    """Espaço Times: a liberação é por login, e o servidor já conferiu isso
    ao servir a página do espaço. Aqui basta a sessão."""
    caminho = _arquivo(DIR_TIMES, nome)
    get_session(req)
    if caminho is None:
        raise HTTPException(404, "Módulo não encontrado.")
    return _entregar(req, caminho)
