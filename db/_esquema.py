"""Colunas novas em tabela que já existe.

`create_all` cria tabela que falta e não toca nas que existem: uma coluna
acrescentada ao modelo faz o app subir e quebrar na primeira consulta.
Este helper compara o modelo com o banco e faz `ADD COLUMN` do que falta
— uma coluna por transação, nunca apaga nem recria nada, falha vira log
e a próxima segue. É o mínimo enquanto não há Alembic por banco.

Uso, no `init_db()` de cada módulo, logo depois do `create_all`:

    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "separacao")
"""
from __future__ import annotations

import logging

from sqlalchemy import text

_log = logging.getLogger("esquema")


def _colunas_existentes(conn, tabela: str, dialeto: str) -> set[str]:
    if dialeto == "sqlite":
        return {str(l[1]) for l in conn.exec_driver_sql(f"PRAGMA table_info({tabela})").all()}
    linhas = conn.execute(
        text("SELECT column_name FROM information_schema.columns "
             "WHERE table_name = :t AND table_schema = current_schema()"),
        {"t": tabela}).all()
    return {str(l[0]) for l in linhas}


def migrar_colunas(base, engine, modulo: str = "") -> list[str]:
    """Acrescenta ao banco toda coluna que existe no modelo e não na tabela."""
    acrescentadas: list[str] = []
    dialeto = engine.dialect.name
    for tabela in base.metadata.sorted_tables:
        try:
            with engine.connect() as conn:
                existentes = _colunas_existentes(conn, tabela.name, dialeto)
        except Exception as exc:  # noqa: BLE001
            _log.warning("%s: não li as colunas de %s: %s", modulo, tabela.name, exc)
            continue
        if not existentes:
            continue  # tabela ainda não existe: o create_all cuida
        for coluna in tabela.columns:
            if coluna.name in existentes:
                continue
            tipo = coluna.type.compile(engine.dialect)
            padrao = getattr(coluna.default, "arg", None)
            if isinstance(padrao, bool):
                sufixo = f" DEFAULT {int(padrao)}"
            elif isinstance(padrao, (int, float)):
                sufixo = f" DEFAULT {padrao}"
            elif isinstance(padrao, str):
                sufixo = " DEFAULT '" + padrao.replace("'", "''") + "'"
            else:
                sufixo = ""
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {tabela.name} ADD COLUMN {coluna.name} {tipo}{sufixo}")
                acrescentadas.append(f"{tabela.name}.{coluna.name}")
                _log.info("%s: coluna %s.%s acrescentada", modulo, tabela.name, coluna.name)
            except Exception as exc:  # noqa: BLE001
                _log.error("%s: não acrescentei %s.%s: %s", modulo, tabela.name, coluna.name, exc)
    return acrescentadas
