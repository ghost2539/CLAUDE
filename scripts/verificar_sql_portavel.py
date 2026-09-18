#!/usr/bin/env python3
"""O SQL do portal roda no SQLite do servidor, não só no Postgres.

    python3 scripts/verificar_sql_portavel.py

Só lê o código e roda contra um SQLite temporário. Sem rede, sem tocar em
banco de servidor.

Por que esta verificação existe
-------------------------------
O servidor antigo rodava Postgres; o novo roda SQLite. Construção que só o
Postgres entende passou anos sem dar sinal e só quebrou depois da migração,
com o banco já carregado — o que fazia parecer dado mal migrado:

    ORDER BY ... DESC NULLS LAST   -> near "NULLS": syntax error
    SELECT ... FOR UPDATE          -> near "FOR": syntax error

E não basta "o SQLite aceita": o RHEL do servidor traz a **3.26**, de 2018.
`NULLS LAST` só entrou na 3.30, `RETURNING` na 3.35. Uma máquina de
desenvolvimento com SQLite novo roda as duas coisas e não acusa nada — por
isso a conferência aqui é sobre o TEXTO do SQL, não sobre executar.

Onde o SQLAlchemy salva, e onde não salva
-----------------------------------------
Pelo ORM, várias dessas construções são traduzidas: `.ilike()` vira
`lower(a) LIKE lower(b)` e `.with_for_update()` é simplesmente omitido no
SQLite. O que NÃO é traduzido é SQL escrito à mão dentro de `text()` ou
`exec_driver_sql()` — ali o que está escrito é o que vai para o banco. É
neste ponto que a varredura aperta.
"""
from __future__ import annotations

import ast
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


PASTAS = ("routers", "db", "core", "integracoes")


def _arquivos() -> list[Path]:
    fora = []
    for pasta in PASTAS:
        fora.extend(sorted((RAIZ / pasta).glob("*.py")))
    return fora


print("[1] Chamadas do ORM que geram sintaxe só do Postgres")
# Por AST: comentário e docstring citam esses nomes de propósito (para
# explicar o defeito), e um detector por texto acusaria a própria explicação.
SO_POSTGRES = {"nullslast", "nullsfirst"}
achados: list[str] = []
for arq in _arquivos():
    try:
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        falhas.append(f"{arq.name} não compila: {exc}")
        continue
    for no in ast.walk(arvore):
        if (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
                and no.func.attr in SO_POSTGRES):
            achados.append(f"{arq.relative_to(RAIZ)}:{no.lineno} .{no.func.attr}()")
checar(not achados, f"nenhum .nullslast()/.nullsfirst() ({achados or 'nenhum'})")
if achados:
    print("       use db._esquema.nulos_por_ultimo() / nulos_primeiro()")

print("\n[2] SQL escrito à mão, que o SQLAlchemy não traduz")
# Cada padrão vem com a versão do SQLite que passou a aceitá-lo (ou None
# quando o SQLite não tem a construção de jeito nenhum).
PROIBIDOS = [
    (re.compile(r"\bFOR\s+UPDATE\b", re.I), "FOR UPDATE", "não existe no SQLite"),
    (re.compile(r"\bNULLS\s+(LAST|FIRST)\b", re.I), "NULLS LAST/FIRST", "SQLite só na 3.30"),
    (re.compile(r"\bRETURNING\b", re.I), "RETURNING", "SQLite só na 3.35"),
    (re.compile(r"\bDISTINCT\s+ON\b", re.I), "DISTINCT ON", "não existe no SQLite"),
    (re.compile(r"\bFILTER\s*\(\s*WHERE\b", re.I), "FILTER (WHERE)", "SQLite só na 3.30"),
    (re.compile(r"\bILIKE\b", re.I), "ILIKE", "não existe no SQLite"),
    (re.compile(r"::\s*(?:text|int|integer|numeric|date|timestamp|boolean)\b", re.I),
     "cast com ::", "sintaxe do Postgres"),
]


def _literais_sql(arvore: ast.AST):
    """Strings passadas para text() / exec_driver_sql(), com a linha."""
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        nome = ""
        if isinstance(no.func, ast.Name):
            nome = no.func.id
        elif isinstance(no.func, ast.Attribute):
            nome = no.func.attr
        if nome not in ("text", "exec_driver_sql"):
            continue
        for arg in no.args:
            for parte in ast.walk(arg):
                if isinstance(parte, ast.Constant) and isinstance(parte.value, str):
                    yield no.lineno, parte.value


encontrados: list[str] = []
for arq in _arquivos():
    try:
        arvore = ast.parse(arq.read_text(encoding="utf-8"))
    except SyntaxError:
        continue
    for linha, sql in _literais_sql(arvore):
        for regex, rotulo, porque in PROIBIDOS:
            if regex.search(sql):
                encontrados.append(
                    f"{arq.relative_to(RAIZ)}:{linha} {rotulo} ({porque})")
checar(not encontrados,
       f"nenhuma construção só-Postgres em SQL cru ({encontrados or 'nenhuma'})")
for e in encontrados:
    print("       -", e)

print("\n[3] O detector pega o que tem de pegar")
_FONTE = '''
from sqlalchemy import text
def f(s):
    return s.execute(text("SELECT n FROM t WHERE p = :p FOR UPDATE"))
def g(s):
    return s.execute(text("SELECT a FROM t ORDER BY b DESC NULLS LAST"))
def h(s):
    return s.execute(text("SELECT a FROM t WHERE n ILIKE :x"))
def ok(s):
    return s.execute(text("SELECT a FROM t WHERE p = :p ORDER BY id DESC"))
'''
_arv = ast.parse(_FONTE)
_pegos = []
for _linha, _sql in _literais_sql(_arv):
    for _regex, _rotulo, _ in PROIBIDOS:
        if _regex.search(_sql):
            _pegos.append(_rotulo)
checar(sorted(_pegos) == ["FOR UPDATE", "ILIKE", "NULLS LAST/FIRST"],
       f"pega os 3 defeitos plantados e nada além ({sorted(_pegos)})")

print("\n[4] A sequência da caixa funciona no SQLite (Identificação)")
import os  # noqa: E402

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")

from sqlalchemy import text as _text  # noqa: E402

import db.portal as _dbp  # noqa: E402

_dbp.init_db()
# Importar o router cria `box_sequences` (`_ensure_tables()` roda na
# importação) — é o mesmo caminho que o portal percorre ao subir.
import routers.identificacao as _ident  # noqa: E402,F401
print(f"       (SQLite desta máquina: {sqlite3.sqlite_version}; o do servidor é 3.26)")


def _consumir(ano: str) -> int:
    """O mesmo trecho que routers/identificacao.py usa para numerar a caixa."""
    with _dbp.SessionLocal.begin() as s:
        avancou = s.execute(
            _text("UPDATE box_sequences SET next_number = next_number + 1 "
                  "WHERE prefix = :p"), {"p": ano}).rowcount
        if avancou:
            return s.execute(
                _text("SELECT next_number FROM box_sequences WHERE prefix = :p"),
                {"p": ano}).scalar() - 1
        s.execute(_text("INSERT INTO box_sequences (prefix, next_number) "
                        "VALUES (:p, :n)"), {"p": ano, "n": 2})
        return 1


_numeros = [_consumir("2026") for _ in range(5)]
checar(_numeros == [1, 2, 3, 4, 5], f"numera 1..5 sem repetir nem pular ({_numeros})")
checar(len(set(_numeros)) == len(_numeros), "nenhum número sai duas vezes")
_outro = _consumir("2027")
checar(_outro == 1, f"ano novo recomeça do 1 ({_outro})")
checar(_consumir("2026") == 6, "e o ano anterior continua de onde parou")

# E a forma antiga, que é a que quebrava: confirma que o erro era real.
with _dbp.SessionLocal() as s:
    try:
        s.execute(_text("SELECT next_number FROM box_sequences "
                        "WHERE prefix = :p FOR UPDATE"), {"p": "2026"}).fetchone()
        checar(False, "FOR UPDATE deveria falhar no SQLite (não falhou)")
    except Exception as exc:  # noqa: BLE001
        checar("FOR" in str(exc) or "syntax" in str(exc).lower(),
               f"a forma antiga realmente quebra aqui ({type(exc).__name__})")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("SQL que não roda no banco do servidor:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("O SQL do portal roda nos dois bancos.")
