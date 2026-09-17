"""Entrega dos arquivos de `static/` sem os comentários do código-fonte.

O que o navegador baixa, qualquer pessoa lê: basta abrir a aba de rede, ou
salvar um HAR e anexar num chamado. Os comentários deste projeto explicam
regra de negócio, nome de tabela, por que tal contorno existe e onde o
sistema já quebrou — informação interna que não tem por que sair daqui.

Então a fonte no disco continua comentada, como deve ser para quem mantém,
e o que sai pela rede é só o necessário para a tela funcionar.

O que NÃO se faz aqui: minificar. Renomear variável ou comprimir espaço é
transformação de verdade, com risco de mudar comportamento, e não é o que
resolve o problema. Tirar comentário resolve, e mantém o arquivo legível o
bastante para depurar um incidente em produção.

Cuidado central: `/*`, `//` e `<!--` dentro de string, de template literal
ou de expressão regular são TEXTO, não comentário. Recortá-los quebraria a
tela. Por isso aqui há um percorredor de estados, não uma expressão regular
— e `scripts/verificar_estatico.py` passa o `node --check` em cada arquivo
limpo para provar que nenhum deles deixou de ser código válido.
"""
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

# Extensões que valem a pena limpar. O resto (imagem, fonte) passa direto.
LIMPAVEIS = {".js", ".css", ".html", ".htm"}

# Comentário que começa com `!` é licença de biblioteca de terceiro. Sai da
# regra: apagar aviso de licença de software que não é nosso é outro problema.
_LICENCA = "!"


def _colapsar_vazias(texto: str) -> str:
    """Três ou mais linhas em branco viram uma.

    Tirar um bloco de comentário de 20 linhas deixa 20 linhas vazias no
    lugar. Linha em branco nunca muda o significado do código — o que não
    se pode é juntar duas linhas, porque em JavaScript o ponto e vírgula
    pode ser implícito e a junção mudaria o programa. Colapsar mantém pelo
    menos uma quebra entre as linhas de código, então é seguro.
    """
    return re.sub(r"\n[ \t]*(?:\n[ \t]*){2,}", "\n\n", texto)


def sem_comentarios_css(texto: str) -> str:
    """CSS: só existe `/* */`, mas ele pode aparecer dentro de string.

    `content: "/* isto é texto */"` é conteúdo da tela, não comentário.
    """
    saida: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c in "\"'":
            fim = _fim_da_string(texto, i, c)
            saida.append(texto[i:fim])
            i = fim
            continue
        # url(...) sem aspas é um literal: o que estiver dentro é endereço.
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
                # As quebras ficam: preservam a numeração das linhas.
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


# Depois destes, uma `/` é divisão. Depois de qualquer outra coisa, é o
# começo de uma expressão regular. É a regra que todo analisador de
# JavaScript usa, porque o caractere sozinho não diz qual dos dois é.
_FIM_DE_EXPRESSAO = re.compile(r"(?:[\w$\])]|\+\+|--)$")
_PALAVRA_FINAL = re.compile(r"(?:^|[^\w$])(return|typeof|instanceof|in|of|new|delete|void|"
                            r"throw|case|do|else|yield|await)$")


def _e_regex(anterior: str) -> bool:
    """Naquele ponto, `/` abre expressão regular ou é divisão?"""
    recorte = anterior.rstrip()
    if not recorte:
        return True
    # `return /x/` é regex embora `return` termine em letra: a palavra
    # reservada não é valor, então o que vem depois só pode ser expressão.
    if _PALAVRA_FINAL.search(recorte):
        return True
    return not _FIM_DE_EXPRESSAO.search(recorte)


def sem_comentarios_js(texto: str) -> str:
    """JavaScript: `//`, `/* */`, e tudo que só PARECE comentário.

    O percorredor precisa saber onde está para não recortar texto: dentro
    de aspas, de crase (template literal, que aninha `${...}` com strings
    dentro) ou de expressão regular, `//` é conteúdo.
    """
    saida: list[str] = []
    i, n = 0, len(texto)
    # Pilha das crases abertas: `${` dentro de template literal volta ao
    # modo normal, e o `}` correspondente volta para o template.
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
    """HTML: tira `<!-- -->` e limpa o que está dentro de <script>/<style>.

    A página tem as duas coisas: comentário de HTML e código embutido, que
    traz os comentários do próprio JavaScript junto. Limpar só um dos dois
    deixaria metade do vazamento de pé.
    """
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


# Limpar a cada requisição é trabalho repetido para um resultado que só muda
# quando o arquivo muda. A chave inclui data e tamanho: editar o arquivo no
# servidor invalida a entrada sozinho, sem precisar reiniciar o serviço.
_cache: dict[tuple[str, float, int], bytes] = {}
_TETO_CACHE = 200


def limpar_arquivo(caminho: Path) -> bytes | None:
    """Conteúdo do arquivo sem comentários, ou None se não der para limpar.

    Falha aqui nunca pode derrubar a tela: se algo der errado, o chamador
    entrega o arquivo original. Vazar comentário é ruim; servir um CSS
    quebrado e deixar todo mundo sem tela é pior.
    """
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


# As páginas servidas por rota Python chegam como TEXTO, já lido do disco —
# não há caminho para servir de chave. O resumo do próprio texto serve: o
# conteúdo é o mesmo em toda visita, e editar o arquivo troca a entrada
# sozinho. Sem isto, cada abertura de tela repete um percurso de milissegundos
# para chegar exatamente ao mesmo resultado.
_cache_texto: dict[tuple[str, str], str] = {}


def limpar_texto(texto: str, sufixo: str) -> str:
    """Como `limpar`, mas guardando o resultado do texto que se repete.

    Falha aqui devolve o texto como veio: o mesmo princípio de
    `limpar_arquivo` — servir a tela vale mais do que esconder o comentário.
    """
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

# Sufixo do ETag. Precisa aparecer no que sai para o navegador, porque é ele
# que diferencia a versão limpa da suja no cache de quem já usava o portal.
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
    """`/static` entregue sem os comentários do código-fonte.

    O `StaticFiles` normal manda o arquivo do disco como ele está. Aqui a
    resposta é remontada com os bytes limpos — mesmo tipo, mesmo caminho,
    só sem o que não deveria sair daqui.

    O ETag muda DE PROPÓSITO. Quem já abriu o portal tem a versão comentada
    guardada no navegador; com o mesmo ETag o servidor responderia "não
    mudou" e o arquivo antigo seguiria em uso por dias, com a correção no ar
    e o vazamento de pé. Com ETag diferente, a primeira visita depois da
    atualização já baixa a versão limpa.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        # As condicionais saem do escopo ANTES de chamar o StaticFiles: ele
        # compara o `If-None-Match` com o ETag do arquivo bruto e devolveria
        # 304 justamente para quem tem a versão suja em cache. O 304 é
        # decidido logo abaixo, contra o ETag que de fato sai daqui.
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
            # Imagem, fonte, ou a limpeza falhou: segue o arquivo original,
            # com o mesmo 304 que o StaticFiles daria sozinho.
            if isinstance(resposta, FileResponse) and self.is_not_modified(
                    resposta.headers, pedido):
                return NotModifiedResponse(resposta.headers)
            return resposta

        # `last-modified` fica de fora: a data do arquivo no disco não
        # descreve o que sai daqui, e um `If-Modified-Since` batendo com ela
        # devolveria 304 para a versão suja — o mesmo buraco do ETag. Quem
        # manda no cache deste conteúdo é o ETag, só ele.
        # `accept-ranges: none` porque o pedaço que o cliente pediria foi
        # medido no arquivo bruto, que tem outro tamanho; responder a faixa
        # entregaria o trecho errado. Sem faixa, ele pede o arquivo inteiro —
        # são dezenas de KB, não um vídeo.
        cabecalhos = {"etag": _etag_limpo(resposta.headers.get("etag", ""), limpo),
                      "accept-ranges": "none"}
        if self.is_not_modified(Headers(cabecalhos), pedido):
            return NotModifiedResponse(Headers(cabecalhos))

        limpa = Response(content=limpo, media_type=resposta.media_type,
                         headers=cabecalhos)
        # HEAD leva só o cabeçalho. O `content-length` já foi medido sobre o
        # corpo inteiro, que é o que o cliente quer saber ao perguntar.
        if str(scope.get("method", "")).upper() == "HEAD":
            limpa.body = b""
        return limpa
