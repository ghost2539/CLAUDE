#!/usr/bin/env python3
"""Verificação da limpeza de comentários do que o portal entrega.

    python3 scripts/verificar_estatico.py

O que o navegador baixa qualquer pessoa lê. Os comentários do projeto
explicam regra de negócio, nome de tabela do EBS, contorno e incidente
antigo — não podem sair. `core/estatico.py` tira os comentários na entrega
e o fonte no disco continua comentado.

Tirar comentário de código por texto é justamente onde se erra: `//`
dentro de string, `/* */` dentro de aspas, `//` dentro de expressão
regular. Recortar um desses quebra a tela em produção sem erro nenhum no
servidor. Por isso aqui não se confere só "saiu o comentário": cada `.js`
limpo passa pelo `node --check`, e os casos-armadilha são testados um a um.

Precisa do `node` no PATH para a parte de sintaxe. Sem ele, essa parte é
PULADA com aviso — e aí a verificação não provou o principal.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")

from core.estatico import limpar, limpar_arquivo  # noqa: E402

FALHAS: list[str] = []
TOTAL = 0


def checar(cond: object, msg: str) -> None:
    global TOTAL
    TOTAL += 1
    print(f"  {'ok  ' if cond else 'FALHA'} {msg}")
    if not cond:
        FALHAS.append(msg)


ARQUIVOS = sorted(p for p in (RAIZ / "static").rglob("*")
                  if p.suffix.lower() in (".js", ".css", ".html", ".htm"))
JS = [p for p in ARQUIVOS if p.suffix.lower() == ".js"]


print("[1] O que só PARECE comentário não pode ser recortado")
# Cada um destes já é um jeito conhecido de quebrar a tela: o recorte sai
# limpo, o arquivo continua sendo JavaScript válido, e o que quebra é o
# comportamento — o pior tipo de defeito para achar depois.
ARMADILHA_JS = '''
var endereco = "https://exemplo.com/caminho";      // fora: comentário
var texto = "isto // não é comentário";
var bloco = 'nem /* isto */ é';
var achar = /https:\\/\\/[^\\s]+/g;
var classe = /[/*]/;
var modelo = `url ${ dados["a//b"] } e ${ `aninhado ${ x /* nem aqui */ } fim` } fim`;
var divisao = total / 2 / 3;
console.log(endereco, texto, bloco, achar, classe, modelo, divisao);
'''
js = limpar(ARMADILHA_JS, ".js")
checar('"https://exemplo.com/caminho"' in js, "`//` de URL dentro de string fica")
checar('"isto // não é comentário"' in js, "`//` dentro de string fica")
checar("'nem /* isto */ é'" in js, "`/* */` dentro de string fica")
checar("/https:\\/\\/[^\\s]+/g" in js, "`//` escapado dentro de expressão regular fica")
checar("/[/*]/" in js, "`/*` dentro de classe de expressão regular fica")
checar('dados["a//b"]' in js, "`//` em string dentro de `${}` fica")
checar("`aninhado ${ x " in js, "template literal com `${}` aninhado sobrevive")
checar("total / 2 / 3" in js, "divisão não vira expressão regular")
checar("// fora: comentário" not in js, "e o comentário de verdade sai")
checar("/* nem aqui */" not in js, "bloco dentro do `${}` é comentário de verdade e sai")

ARMADILHA_CSS = '''
/* cabeçalho que tem de sair */
.a::after { content: "/*x*/"; }
.b::before { content: "// nem isto"; }
.c { background: url(imagens/fundo.png?v=1); }
.d { background: url(http://interno/i.png); }
'''
css = limpar(ARMADILHA_CSS, ".css")
checar('content: "/*x*/"' in css, "`/* */` dentro de string de CSS fica")
checar('content: "// nem isto"' in css, "`//` dentro de string de CSS fica")
checar("url(imagens/fundo.png?v=1)" in css, "`url()` sem aspas fica inteiro")
checar("url(http://interno/i.png)" in css, "`//` dentro de `url()` sem aspas fica")
checar("cabeçalho que tem de sair" not in css, "e o comentário de verdade sai")

ARMADILHA_HTML = '''<!doctype html>
<!-- comentário interno que não pode sair daqui -->
<p>texto</p>
<script>var s = "a <!-- b"; // sai</script>
<style>.x { content: "<!--"; } /* sai */</style>
'''
html = limpar(ARMADILHA_HTML, ".html")
checar("comentário interno" not in html, "`<!-- -->` da página sai")
checar("<p>texto</p>" in html, "o conteúdo da página fica")
checar('var s = "a <!-- b";' in html, "`<!--` dentro de string do <script> fica")
checar('content: "<!--"' in html, "`<!--` dentro de string do <style> fica")
checar("// sai" not in html and "/* sai */" not in html,
       "comentário dentro de <script> e <style> sai")

# Licença de biblioteca de terceiro não é comentário nosso para apagar.
checar("/*! licença de terceiro */" in limpar("/*! licença de terceiro */\nvar a=1;", ".js"),
       "comentário de licença `/*!` é preservado")


print(f"\n[2] Limpar é idempotente e não come o arquivo ({len(ARQUIVOS)} arquivos)")
for p in ARQUIVOS:
    rel = p.relative_to(RAIZ)
    bruto = p.read_text(encoding="utf-8")
    limpo = limpar(bruto, p.suffix)
    checar(limpar(limpo, p.suffix) == limpo, f"{rel}: limpar(limpar(x)) == limpar(x)")
    # Arquivo vazio ou encolhido demais é sinal de recorte indevido: nenhum
    # arquivo do portal é mais de 60% comentário.
    sobrou = len(limpo) / len(bruto) if bruto else 1.0
    checar(bool(limpo.strip()) and sobrou >= 0.40,
           f"{rel}: sobrou {sobrou*100:.0f}% do conteúdo")


print(f"\n[3] Todo .js limpo continua sendo JavaScript válido ({len(JS)} arquivos)")
if not shutil.which("node"):
    print("  PULADO — `node` não está no PATH; a sintaxe NÃO foi verificada.")
else:
    destino = _TMP / "js"
    destino.mkdir(exist_ok=True)
    for p in JS:
        rel = p.relative_to(RAIZ)
        alvo = destino / str(rel).replace("/", "__")
        alvo.write_text(limpar(p.read_text(encoding="utf-8"), ".js"), encoding="utf-8")
        r = subprocess.run(["node", "--check", str(alvo)], capture_output=True, text=True)
        checar(r.returncode == 0,
               f"{rel}: node --check{'' if r.returncode == 0 else ' → ' + r.stderr.strip()[:200]}")


print("\n[4] O vazamento fechou: o comentário some do que sai")
# Trechos que existem no fonte e não podem chegar ao navegador.
MARCAS = {
    "static/app.css": "Padrão de UI SPARE (handoff CSC TI)",
    "js/app.js": "Alguns proxies acrescentam a barra no fim por REDIRECIONAMENTO",
    "static/index.html": "Coluna de conteúdo",
    "static/login.html": "Tela de login",
}
for rel, marca in MARCAS.items():
    p = RAIZ / rel
    bruto = p.read_text(encoding="utf-8")
    checar(marca in bruto, f"{rel}: o comentário existe no fonte (senão o teste não prova nada)")
    checar(marca not in limpar(bruto, p.suffix), f"{rel}: e não sobrevive à limpeza")


print("\n[5] Pela rota HTTP — é isto que o navegador recebe")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

cliente = TestClient(main.app)

r_css = cliente.get("/static/app.css")
checar(r_css.status_code == 200, f"GET /static/app.css responde 200 ({r_css.status_code})")
checar("/*" not in r_css.text, "a folha entregue não tem um só `/*`")
checar("Padrão de UI SPARE (handoff CSC TI)" not in r_css.text,
       "o cabeçalho comentado da folha não é entregue")
checar("--sp-black: #000000;" in r_css.text, "e o estilo em si continua lá")

# O app.js SAIU de static/. A afirmação inverteu de propósito: o que antes
# se exigia que respondesse 200 agora tem de responder 404, porque enquanto
# ele estivesse ali qualquer visitante levava as rotas dos módulos e os
# nomes das permissões sem digitar senha.
r_js = cliente.get("/static/app.js")
checar(r_js.status_code == 404,
       f"GET /static/app.js NÃO existe mais no mount público ({r_js.status_code})")
r_prot = cliente.get("/js/app.js")
checar(r_prot.status_code == 401,
       f"GET /js/app.js exige sessão ({r_prot.status_code})")

# O login.js é o único JavaScript público, e por isso não pode carregar o
# que era o problema: rota de módulo, permissão, estrutura de menu.
r_login = cliente.get("/static/js/login.js")
checar(r_login.status_code == 200, "o login.js é público (a tela de login precisa dele)")
for proibido in ("sidebar-label", "permission_map", "/modulos/", "buildMenu"):
    checar(proibido not in r_login.text,
           f"o login.js não carrega `{proibido}`")
# `//` ainda aparece em `https://` dentro de string; o que não pode é linha
# de comentário — `//` depois de código ou abrindo a linha.
import re as _re  # noqa: E402

# O app.js não é mais alcançável sem sessão, então a limpeza dele é
# conferida no arquivo do disco passando pela MESMA função que a rota usa.
# Conferir na resposta exigiria uma sessão só para isso, e o que se quer
# provar aqui é a limpeza, não a autorização — essa já foi provada acima.
limpo_js = (limpar_arquivo(RAIZ / "js" / "app.js") or b"").decode("utf-8")
sobrou = [ln for ln in limpo_js.splitlines()
          if _re.match(r"^\s*//", ln) or _re.search(r";\s*//\s", ln)]
checar(not sobrou, f"nenhuma linha de comentário `//` sobrou do app.js ({sobrou[:1]})")
checar("'use strict'" in limpo_js, "e o código em si continua lá")
checar("Alguns proxies acrescentam a barra" not in limpo_js,
       "o comentário sobre o contorno do proxy não é entregue")

r_pag = cliente.get("/")
checar(r_pag.status_code == 200, f"GET / responde 200 ({r_pag.status_code})")
checar("<!--" not in r_pag.text, "a página servida pelo portal não tem `<!--`")
checar("login-screen" in r_pag.text, "e a tela continua inteira")


print("\n[6] O ETag muda — senão o navegador segue com a versão suja")
# Quem já usava o portal tem o arquivo comentado guardado. Com o mesmo
# ETag o servidor responderia "não mudou" e a correção não chegaria.
from starlette.applications import Starlette  # noqa: E402
from starlette.staticfiles import StaticFiles  # noqa: E402

cru = Starlette()
cru.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
etag_cru = TestClient(cru).get("/static/app.css").headers.get("etag")
etag_limpo = r_css.headers.get("etag")
checar(etag_cru and etag_limpo and etag_cru != etag_limpo,
       f"o ETag entregue difere do que o StaticFiles cru daria ({etag_cru} → {etag_limpo})")
checar(cliente.get("/static/app.css",
                   headers={"If-None-Match": etag_limpo}).status_code == 304,
       "com o ETag novo, 304 — o cache continua funcionando")
checar(cliente.get("/static/app.css",
                   headers={"If-None-Match": etag_cru}).status_code == 200,
       "com o ETag antigo (versão suja), 200 — a versão limpa desce")
checar(cliente.get("/static/app.css",
                   headers={"If-Modified-Since": "Wed, 21 Oct 2099 07:28:00 GMT"}
                   ).status_code == 200,
       "`If-Modified-Since` não devolve 304 pela data do arquivo bruto")

r_head = cliente.head("/static/app.css")
checar(r_head.status_code == 200 and not r_head.content
       and r_head.headers.get("content-length") == str(len(r_css.content)),
       "HEAD responde só o cabeçalho, com o tamanho do arquivo limpo")
r_faixa = cliente.get("/static/app.css", headers={"Range": "bytes=0-99"})
checar(r_faixa.status_code == 200 and len(r_faixa.content) == len(r_css.content),
       "pedido de faixa recebe o arquivo inteiro, não um pedaço do arquivo errado")

# O que não é limpável passa intocado, com o 304 de sempre.
r_svg = cliente.get("/static/favicon.svg")
checar(r_svg.status_code == 200
       and r_svg.content == (RAIZ / "static/favicon.svg").read_bytes(),
       "arquivo não limpável (svg) vai byte a byte como está no disco")
checar(cliente.get("/static/favicon.svg",
                   headers={"If-None-Match": r_svg.headers["etag"]}).status_code == 304,
       "e continua respondendo 304 normalmente")
checar(cliente.get("/static/nao-existe.js").status_code == 404,
       "arquivo inexistente continua 404")
checar(limpar_arquivo(RAIZ / "static" / "nao-existe.js") is None,
       "limpar_arquivo devolve None no que não dá para ler (o chamador entrega o original)")


# ── <script> inline: a CSP do portal proíbe, e o sintoma engana ────────
#
# `script-src 'self'` bloqueia todo <script> inline, e o navegador não
# mostra nada na tela — só uma linha no console, que ninguém vê. O sintoma
# é o pior possível: `style-src` PERMITE inline, então o CSS aplica e a
# página abre bonita, com fundo, tipografia e título no lugar, e só o que
# o JavaScript montaria fica faltando. Parece falta de dados; é política.
#
# Foi exatamente assim que o Hub subiu quebrado: testado como arquivo
# (file://, sem CSP) passou; servido pelo portal, veio a tela vazia.
import re as _re  # noqa: E402

print("\n[CSP] Nada servido pelo portal usa <script> inline")

_POLITICA = (RAIZ / "core" / "security.py").read_text(encoding="utf-8")
checar("script-src 'self'" in _POLITICA,
       "a CSP do portal declara script-src 'self' (é o que torna isto obrigatório)")

for _pagina in sorted((RAIZ / "static").rglob("*.html")):
    _texto = _pagina.read_text(encoding="utf-8")
    # <script> COM corpo. <script src="..."></script> é o jeito certo e passa.
    _inline = [m for m in _re.findall(r"<script(?![^>]*\ssrc=)[^>]*>(.*?)</script>",
                                      _texto, _re.S) if m.strip()]
    _nome = _pagina.relative_to(RAIZ)
    checar(not _inline,
           f"{_nome} sem <script> inline"
           + (f" — {len(_inline)} bloco(s) seriam bloqueados" if _inline else ""))

    # E o que ela referencia tem de existir: um src errado dá 404 silencioso,
    # com a mesma tela vazia de novo.
    for _src in _re.findall(r'<script[^>]*\ssrc="([^"]+)"', _texto):
        if _src.startswith(("http://", "https://", "//")):
            checar(False, f"{_nome} busca script de fora ({_src}) — a CSP barra")
            continue
        # Fora a query: `?v=20260905` é quebra de cache e `?v={{v}}` é
        # marcador que a rota substitui — nenhum dos dois é parte do caminho.
        _caminho = _src.split("?", 1)[0]
        _alvo = RAIZ / _caminho.lstrip("/")
        checar(_alvo.is_file(), f"{_nome} -> {_caminho} existe no disco")


print(f"\n{TOTAL - len(FALHAS)} de {TOTAL} verificações passaram.")
if FALHAS:
    print("Falhas:")
    for f in FALHAS:
        print(f"  - {f}")
    sys.exit(1)
print("Entrega de estáticos sem comentário íntegra.")
