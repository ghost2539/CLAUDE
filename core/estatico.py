from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

_log = logging.getLogger("estatico")


LIMPAVEIS = {".js", ".css", ".html", ".htm"}


_LICENCA = "!"


def _colapsar_vazias(texto: str) -> str:
    
    return re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", texto)


def sem_comentarios_css(texto: str) -> str:

    saida: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c in "\"'":
            fim = _fim_da_string(texto, i, c)
            saida.append(texto[i:fim])
            i = fim
            continue
        
        if texto.startswith("url(", i):
            fim = texto.find(")", i)
            fim = n if fim < 0 else fim + 1
            saida.append(texto[i:fim])
            i = fim
            continue
        if texto.startswith("/*", i):
            fim = texto.find("*/", i + 2)
            fim = n if fim < 0 else fim + 2
            trecho = texto[i:fim]
            if trecho.startswith("/*" + _LICENCA):
                saida.append(trecho)
            else:
                
                saida.append("\n" * trecho.count("\n"))
            i = fim
            continue
        saida.append(c)
        i += 1
    return _colapsar_vazias("".join(saida))


def _fim_da_string(texto: str, inicio: int, aspa: str) -> int:
    """Índice logo depois da aspa que fecha, respeitando a barra invertida."""
    i = inicio + 1
    n = len(texto)
    while i < n:
        c = texto[i]
        if c == "\\":
            i += 2
            continue
        if c == aspa:
            return i + 1
        i += 1
    return n



_FIM_DE_EXPRESSAO = re.compile(r"(?:[\w$\])]|\+\+|--)$")
_PALAVRA_FINAL = re.compile(r"(?:^|[^\w$])(return|typeof|instanceof|in|of|new|delete|void|"
                            r"throw|case|do|else|yield|await)$")


def _e_regex(anterior: str) -> bool:
    """Naquele ponto, `/` abre expressão regular ou é divisão?"""
    recorte = anterior.rstrip()
    if not recorte:
        return True
    
    if _PALAVRA_FINAL.search(recorte):
        return True
    return not _FIM_DE_EXPRESSAO.search(recorte)


def sem_comentarios_js(texto: str) -> str:
    
    saida: list[str] = []
    i, n = 0, len(texto)
    
    pilha: list[str] = []
    while i < n:
        c = texto[i]

        if pilha and pilha[-1] == "`":
            if c == "\\":
                saida.append(texto[i:i + 2])
                i += 2
                continue
            if c == "`":
                pilha.pop()
                saida.append(c)
                i += 1
                continue
            if texto.startswith("${", i):
                pilha.append("${")
                saida.append("${")
                i += 2
                continue
            saida.append(c)
            i += 1
            continue

        if c in "\"'":
            fim = _fim_da_string(texto, i, c)
            saida.append(texto[i:fim])
            i = fim
            continue

        if c == "`":
            pilha.append("`")
            saida.append(c)
            i += 1
            continue

        if c == "}" and pilha and pilha[-1] == "${":
            pilha.pop()
            saida.append(c)
            i += 1
            continue

        if texto.startswith("//", i):
            fim = texto.find("\n", i)
            fim = n if fim < 0 else fim
            i = fim
            continue

        if texto.startswith("/*", i):
            fim = texto.find("*/", i + 2)
            fim = n if fim < 0 else fim + 2
            trecho = texto[i:fim]
            if trecho.startswith("/*" + _LICENCA):
                saida.append(trecho)
            else:
                saida.append("\n" * trecho.count("\n"))
            i = fim
            continue

        if c == "/" and _e_regex("".join(saida[-80:])):
            fim = _fim_da_regex(texto, i)
            if fim > i:
                saida.append(texto[i:fim])
                i = fim
                continue

        saida.append(c)
        i += 1
    return _colapsar_vazias("".join(saida))


def _fim_da_regex(texto: str, inicio: int) -> int:
    """Índice depois da `/` que fecha a expressão regular, e das bandeiras.

    Devolve o próprio `inicio` quando não fecha na mesma linha: aí não era
    expressão regular, era divisão, e nada deve ser tratado como literal.
    """
    i = inicio + 1
    n = len(texto)
    classe = False
    while i < n:
        c = texto[i]
        if c == "\\":
            i += 2
            continue
        if c == "\n":
            return inicio
        if c == "[":
            classe = True
        elif c == "]":
            classe = False
        elif c == "/" and not classe:
            i += 1
            while i < n and texto[i].isalpha():
                i += 1
            return i
        i += 1
    return inicio


_ABRE_SCRIPT = re.compile(r"<script\b[^>]*>", re.I)
_ABRE_ESTILO = re.compile(r"<style\b[^>]*>", re.I)


def sem_comentarios_html(texto: str) -> str:
    
    saida: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        if texto.startswith("<!--", i):
            fim = texto.find("-->", i + 4)
            fim = n if fim < 0 else fim + 3
            saida.append("\n" * texto[i:fim].count("\n"))
            i = fim
            continue
        m = _ABRE_SCRIPT.match(texto, i) or _ABRE_ESTILO.match(texto, i)
        if m:
            etiqueta = "script" if m.group(0)[1:7].lower() == "script" else "style"
            fechamento = re.compile(r"</" + etiqueta + r"\s*>", re.I)
            f = fechamento.search(texto, m.end())
            fim_corpo = f.start() if f else n
            corpo = texto[m.end():fim_corpo]
            limpo = (sem_comentarios_js(corpo) if etiqueta == "script"
                     else sem_comentarios_css(corpo))
            saida.append(m.group(0))
            saida.append(limpo)
            i = fim_corpo
            continue
        saida.append(texto[i])
        i += 1
    return _colapsar_vazias("".join(saida))


def limpar(texto: str, sufixo: str) -> str:
    """Despacha pelo tipo do arquivo. Tipo desconhecido volta como veio."""
    s = sufixo.lower()
    if s == ".js":
        return sem_comentarios_js(texto)
    if s == ".css":
        return sem_comentarios_css(texto)
    if s in (".html", ".htm"):
        return sem_comentarios_html(texto)
    return texto



_cache: dict[tuple[str, float, int], bytes] = {}
_TETO_CACHE = 200


def limpar_arquivo(caminho: Path) -> bytes | None:
    
    try:
        st = caminho.stat()
        chave = (str(caminho), st.st_mtime, st.st_size)
        em_cache = _cache.get(chave)
        if em_cache is not None:
            return em_cache
        bruto = caminho.read_text(encoding="utf-8")
        saida = limpar(bruto, caminho.suffix).encode("utf-8")
        if len(_cache) >= _TETO_CACHE:
            _cache.clear()
        _cache[chave] = saida
        return saida
    except Exception as exc:  # noqa: BLE001
        _log.warning("não consegui limpar %s, entrego como está: %s", caminho, exc)
        return None



_cache_texto: dict[tuple[str, str], str] = {}


def limpar_texto(texto: str, sufixo: str) -> str:
    
    try:
        chave = (sufixo.lower(),
                 hashlib.blake2b(texto.encode("utf-8"), digest_size=16).hexdigest())
        em_cache = _cache_texto.get(chave)
        if em_cache is not None:
            return em_cache
        saida = limpar(texto, sufixo)
        if len(_cache_texto) >= _TETO_CACHE:
            _cache_texto.clear()
        _cache_texto[chave] = saida
        return saida
    except Exception as exc:  # noqa: BLE001
        _log.warning("não consegui limpar o texto (%s), entrego como veio: %s", sufixo, exc)
        return texto


# ── Entrega pela rede ───────────────────────────────────────────────────

_SUFIXO_ETAG = "-limpo"


def _etag_limpo(etag_bruto: str, conteudo: bytes) -> str:
    """ETag da versão limpa: o do arquivo bruto com um sufixo dentro das aspas."""
    if etag_bruto.endswith('"'):
        return etag_bruto[:-1] + _SUFIXO_ETAG + '"'
    if etag_bruto:
        return etag_bruto + _SUFIXO_ETAG
    # Sem ETag de origem (não deve acontecer), um resumo do conteúdo serve.
    return '"' + hashlib.blake2b(conteudo, digest_size=16).hexdigest() + _SUFIXO_ETAG + '"'


class EstaticoLimpo(StaticFiles):
    

    async def get_response(self, path: str, scope: Scope) -> Response:
        
        pedido = Headers(scope=scope)
        escopo = dict(scope, headers=[
            (nome, valor) for nome, valor in scope["headers"]
            if nome.lower() not in (b"if-none-match", b"if-modified-since")
        ])
        resposta = await super().get_response(path, escopo)

        limpo = None
        if isinstance(resposta, FileResponse):
            caminho = Path(resposta.path)
            if caminho.suffix.lower() in LIMPAVEIS:
                limpo = limpar_arquivo(caminho)

        if limpo is None:
            
            if isinstance(resposta, FileResponse) and self.is_not_modified(
                    resposta.headers, pedido):
                return NotModifiedResponse(resposta.headers)
            return resposta

        
        cabecalhos = {"etag": _etag_limpo(resposta.headers.get("etag", ""), limpo),
                      "accept-ranges": "none"}
        if self.is_not_modified(Headers(cabecalhos), pedido):
            return NotModifiedResponse(Headers(cabecalhos))

        limpa = Response(content=limpo, media_type=resposta.media_type,
                         headers=cabecalhos)
        
        if str(scope.get("method", "")).upper() == "HEAD":
            limpa.body = b""
        return limpa
