#!/usr/bin/env python3
"""Remove comentários e docstrings da CÓPIA instalada no servidor.

Por que existe: o servidor novo é compartilhado com outras aplicações e
outras equipes. O repositório continua com os comentários — eles são a
memória do projeto e a norma exige — mas a cópia que fica em disco lá não
deve carregar explicação de regra de negócio, nome de fila, host interno.

    python3 scripts/limpar_comentarios.py            # limpa a pasta atual
    python3 scripts/limpar_comentarios.py --conferir # só lista, não altera

Trata `.py` (comentários e docstrings) e `.js` (linha e bloco), pulando
`data/`, `venv/`, `.git/` e qualquer coisa fora da aplicação.

ARQUIVO ALTERADO NÃO VOLTA SOZINHO: como a limpeza deixa a árvore suja, a
atualização passa a ser
    git checkout -- . && git pull && python3 scripts/limpar_comentarios.py

Nenhum arquivo é gravado sem antes ser conferido: `.py` precisa compilar e
`.js` precisa continuar equilibrado em aspas e chaves. O que falhar fica
como está, e o script avisa.
"""
from __future__ import annotations

import ast
import io
import sys
import tokenize
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
IGNORAR = {".git", "venv", "venv-antigo", "data", "__pycache__", "node_modules",
           "docs", "scripts"}


# ── Python ──────────────────────────────────────────────────────────────
def _linhas_de_docstring(fonte: str) -> set[int]:
    """Linhas ocupadas por docstring de módulo, classe e função."""
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return set()
    alvo: set[int] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)):
            continue
        corpo = getattr(no, "body", [])
        if not corpo:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            fim = primeiro.end_lineno or primeiro.lineno
            alvo.update(range(primeiro.lineno, fim + 1))
            # corpo que só tinha a docstring precisa de um `pass` no lugar,
            # senão o arquivo deixa de compilar
            if len(corpo) == 1 and not isinstance(no, ast.Module):
                alvo.discard(primeiro.lineno)
                alvo.update(range(primeiro.lineno + 1, fim + 1))
    return alvo


def _pass_no_lugar(fonte: str) -> dict[int, str]:
    """Linha -> texto que substitui a docstring que era o corpo inteiro."""
    try:
        arvore = ast.parse(fonte)
    except SyntaxError:
        return {}
    troca: dict[int, str] = {}
    linhas = fonte.splitlines()
    for no in ast.walk(arvore):
        if not isinstance(no, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        corpo = getattr(no, "body", [])
        if len(corpo) != 1:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr) and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            original = linhas[primeiro.lineno - 1]
            recuo = original[: len(original) - len(original.lstrip())]
            troca[primeiro.lineno] = recuo + "pass"
    return troca


def limpar_py(fonte: str) -> str:
    docstrings = _linhas_de_docstring(fonte)
    trocas = _pass_no_lugar(fonte)

    # tokenize identifica comentário com precisão — regex confundiria "#"
    # dentro de string, que é comum em query e em URL.
    sem_comentario: list[str] = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(fonte).readline))
    except (tokenize.TokenError, IndentationError):
        return fonte
    comentarios = {(t.start[0], t.start[1]) for t in tokens if t.type == tokenize.COMMENT}
    fins = {t.start[0]: t.end[1] for t in tokens if t.type == tokenize.COMMENT}

    for numero, linha in enumerate(fonte.splitlines(), 1):
        if numero in trocas:
            sem_comentario.append(trocas[numero])
            continue
        if numero in docstrings:
            continue
        inicio = next((col for (lin, col) in comentarios if lin == numero), None)
        if inicio is not None:
            recortada = linha[:inicio].rstrip()
            if not recortada:
                continue          # linha que era só comentário: some
            sem_comentario.append(recortada)
            continue
        sem_comentario.append(linha)

    saida = "\n".join(sem_comentario)
    while "\n\n\n\n" in saida:
        saida = saida.replace("\n\n\n\n", "\n\n\n")
    return saida.rstrip() + "\n"


# ── JavaScript ──────────────────────────────────────────────────────────
def limpar_js(fonte: str) -> str:
    """Percorre caractere a caractere: dentro de aspas ou de expressão
    regular, `//` não é comentário — é conteúdo."""
    saida: list[str] = []
    i, n = 0, len(fonte)
    aspas = ""          # ' " ou `
    while i < n:
        c = fonte[i]
        prox = fonte[i + 1] if i + 1 < n else ""

        if aspas:
            saida.append(c)
            if c == "\\" and prox:
                saida.append(prox)
                i += 2
                continue
            if c == aspas:
                aspas = ""
            i += 1
            continue

        if c in "'\"`":
            aspas = c
            saida.append(c)
            i += 1
            continue

        if c == "/" and prox == "/":
            while i < n and fonte[i] != "\n":
                i += 1
            continue
        if c == "/" and prox == "*":
            i += 2
            while i < n - 1 and not (fonte[i] == "*" and fonte[i + 1] == "/"):
                i += 1
            i += 2
            continue

        saida.append(c)
        i += 1

    linhas = [l.rstrip() for l in "".join(saida).splitlines()]
    limpo: list[str] = []
    for linha in linhas:
        if not linha.strip() and limpo and not limpo[-1].strip():
            continue        # não acumula linha em branco onde havia comentário
        limpo.append(linha)
    return "\n".join(limpo).rstrip() + "\n"


# ── Conferência ─────────────────────────────────────────────────────────
def _tem_node() -> bool:
    import shutil
    return bool(shutil.which("node"))


def _js_valido(fonte: str) -> bool:
    """Só o próprio interpretador decide se o JS continua válido — contar
    chaves acusa falso, porque comentário costuma ter '{' e '(' dentro."""
    import subprocess, tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as arq:
        arq.write(fonte)
        caminho = arq.name
    try:
        r = subprocess.run(["node", "--check", caminho],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — sem node, nada de mexer em .js
        return False
    finally:
        import os
        os.unlink(caminho)


# ── Execução ────────────────────────────────────────────────────────────
def _arquivos() -> list[Path]:
    saida: list[Path] = []
    for caminho in RAIZ.rglob("*"):
        if not caminho.is_file() or caminho.suffix not in (".py", ".js"):
            continue
        if any(parte in IGNORAR for parte in caminho.relative_to(RAIZ).parts[:-1]):
            continue
        if caminho.relative_to(RAIZ).parts[0] in IGNORAR:
            continue
        saida.append(caminho)
    return sorted(saida)


def main() -> int:
    conferir = "--conferir" in sys.argv
    # Sem node instalado não há como conferir o JS depois de limpo, e limpar
    # sem conferir é como o portal quebra em produção: os .js ficam intactos.
    com_node = _tem_node()
    if not com_node:
        print("  (node não encontrado: os arquivos .js ficam como estão)")
    alterados = falhas = 0
    for caminho in _arquivos():
        if caminho.suffix == ".js" and not com_node:
            continue
        original = caminho.read_text(encoding="utf-8")
        limpo = limpar_py(original) if caminho.suffix == ".py" else limpar_js(original)
        if limpo == original:
            continue

        if caminho.suffix == ".py":
            try:
                compile(limpo, str(caminho), "exec")
            except SyntaxError as exc:
                print(f"  ! {caminho.relative_to(RAIZ)}: limpeza recusada ({exc})")
                falhas += 1
                continue
        elif not _js_valido(limpo):
            print(f"  ! {caminho.relative_to(RAIZ)}: limpeza recusada "
                  f"(node --check reprovou o resultado)")
            falhas += 1
            continue

        alterados += 1
        economia = len(original.splitlines()) - len(limpo.splitlines())
        print(f"  {'(conferindo) ' if conferir else ''}{caminho.relative_to(RAIZ)} "
              f"-{economia} linha(s)")
        if not conferir:
            caminho.write_text(limpo, encoding="utf-8")

    print(f"\n{alterados} arquivo(s) {'a limpar' if conferir else 'limpos'}; "
          f"{falhas} recusado(s).")
    if not conferir and alterados:
        print("Lembre: para atualizar depois, "
              "git checkout -- . && git pull && python3 scripts/limpar_comentarios.py")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
