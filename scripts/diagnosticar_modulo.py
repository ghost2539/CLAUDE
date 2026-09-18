#!/usr/bin/env python3
"""Acha a LINHA que faz a tela dar Internal Server Error.

    python3 scripts/diagnosticar_modulo.py orcamento_manutencao
    python3 scripts/diagnosticar_modulo.py --listar

Rode no servidor, com o ambiente do portal carregado. Só lê.

Para que serve
--------------
O caso é sempre o mesmo: o banco chegou, `SELECT COUNT(*)` responde
milhares de linhas, e a tela dá 500. O dado está lá — o que não passa é a
LEITURA de volta pelo ORM, e o erro vem sem dizer qual linha.

Acontece, entre outros motivos, quando a data foi gravada num formato que
o leitor de SQLite do SQLAlchemy não aceita (com fuso, com `T` no meio, ou
sem os microssegundos), ou quando o texto de uma coluna não é o tipo que o
modelo espera. Um banco vindo de outro servidor é a origem natural disso.

Como acha
---------
1. Lê a tabela pelo driver cru (`sqlite3`), que aceita quase tudo. Se isso
   falhar, o problema é o ARQUIVO, e o recado é outro.
2. Lê a mesma tabela pelo ORM. Se passar, a tabela está sã.
3. Se quebrar, vai linha a linha para isolar a culpada, e mostra o id, a
   coluna, o valor cru e o erro — o suficiente para corrigir com um UPDATE.

Não corrige nada. O que fazer com a linha é decisão de quem conhece o dado.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
LISTAR = "--listar" in sys.argv


def _modulos() -> list[str]:
    return sorted(p.stem for p in (RAIZ / "db").glob("*.py")
                  if not p.stem.startswith("_"))


if LISTAR or not ARGS:
    print("Módulos de banco disponíveis:\n")
    for m in _modulos():
        print("   ", m)
    print(f"\n    python3 {Path(__file__).relative_to(RAIZ)} <modulo>")
    sys.exit(0 if LISTAR else 1)

NOME = ARGS[0]
if NOME not in _modulos():
    print(f"ERRO: não existe db/{NOME}.py. Use --listar para ver os nomes.")
    sys.exit(1)

try:
    mod = importlib.import_module(f"db.{NOME}")
except Exception as exc:  # noqa: BLE001
    print(f"ERRO ao importar db.{NOME}: {type(exc).__name__}: {exc}")
    sys.exit(1)

url = getattr(mod, "DATABASE_URL", "")
if not url:
    print(f"db.{NOME} não declara DATABASE_URL — nada a diagnosticar aqui.")
    sys.exit(1)

print(f"Módulo : {NOME}")
print(f"Banco  : {url if not url.startswith('sqlite') else url.split('///', 1)[-1]}")

arquivo = Path(url.split("///", 1)[1]) if url.startswith("sqlite") else None
if arquivo is not None:
    if not arquivo.is_file():
        print("\nO ARQUIVO NÃO EXISTE. A tela abre vazia (ou o módulo cria na subida).")
        sys.exit(1)
    print(f"Tamanho: {arquivo.stat().st_size/1048576:.1f} MB")
    con = sqlite3.connect(f"file:{arquivo}?mode=ro", uri=True)
    integro = con.execute("PRAGMA integrity_check").fetchone()[0]
    print(f"Integridade: {integro[:70]}")
    if integro != "ok":
        print("\nO ARQUIVO está danificado. Antes de qualquer coisa:")
        print(f"    python3 scripts/recuperar_sqlite.py {arquivo}")
        con.close()
        sys.exit(1)
    con.close()

Base = getattr(mod, "Base", None)
SessionLocal = getattr(mod, "SessionLocal", None)
if Base is None or SessionLocal is None:
    print(f"\ndb.{NOME} não expõe Base/SessionLocal — não sei ler por aqui.")
    sys.exit(1)

modelos = {}
for classe in Base.registry.mappers:
    cls = classe.class_
    modelos[cls.__tablename__] = cls

from sqlalchemy import select, text  # noqa: E402

print(f"\n{'tabela':28} {'linhas':>8}  leitura pelo ORM")
problemas: list[tuple[str, str]] = []
for tabela in sorted(modelos):
    cls = modelos[tabela]
    with SessionLocal() as s:
        try:
            bruto = s.connection().exec_driver_sql(
                f'SELECT COUNT(*) FROM "{tabela}"').scalar()
        except Exception as exc:  # noqa: BLE001
            print(f"{tabela:28} {'?':>8}  TABELA NÃO LIDA: {type(exc).__name__}: {exc}")
            problemas.append((tabela, f"{type(exc).__name__}: {exc}"))
            continue

    with SessionLocal() as s:
        try:
            s.execute(select(cls)).scalars().all()
            print(f"{tabela:28} {bruto:>8}  ok")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"{tabela:28} {bruto:>8}  QUEBRA: {type(exc).__name__}: {str(exc)[:70]}")
            problemas.append((tabela, f"{type(exc).__name__}: {exc}"))

    # Isola a linha culpada: uma por vez, pelo rowid, e reporta as primeiras.
    print(f"\n   procurando a linha em {tabela}…")
    achadas = 0
    with SessionLocal() as s:
        conn = s.connection()
        ids = [r[0] for r in conn.exec_driver_sql(
            f'SELECT rowid FROM "{tabela}" ORDER BY rowid').all()]
    consulta = text(f'SELECT * FROM "{tabela}" WHERE rowid = :r')
    for rid in ids:
        with SessionLocal() as s:
            try:
                s.execute(select(cls).from_statement(consulta), {"r": rid}).scalars().all()
            except Exception as exc:  # noqa: BLE001
                achadas += 1
                print(f"   rowid {rid}: {type(exc).__name__}: {str(exc)[:110]}")
                with sqlite3.connect(f"file:{arquivo}?mode=ro", uri=True) as raw:
                    raw.row_factory = sqlite3.Row
                    linha = raw.execute(
                        f'SELECT * FROM "{tabela}" WHERE rowid = ?', (rid,)).fetchone()
                    if linha is not None:
                        for chave in linha.keys():
                            valor = linha[chave]
                            if isinstance(valor, str) and len(valor) > 60:
                                valor = valor[:60] + "…"
                            print(f"        {chave:24} = {valor!r}")
                if achadas >= 5:
                    print("   (parando nas 5 primeiras)")
                    break
    print()

print()
if problemas:
    print(f"{len(problemas)} tabela(s) que o código NÃO consegue ler:")
    for t, e in problemas:
        print(f"  - {t}: {e[:100]}")
    print("\nÉ isto que vira o Internal Server Error na tela.")
    sys.exit(1)
print("Todas as tabelas deste módulo são lidas pelo código. Se a tela ainda")
print("dá 500, o erro não está na leitura do banco — pegue o traceback:")
print("    sudo journalctl -u portal_spare -n 120 --no-pager")
