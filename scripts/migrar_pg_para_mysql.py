#!/usr/bin/env python3
"""
Copia os DADOS do banco atual (PostgreSQL) para o novo (MySQL), preservando
os mesmos registros. NÃO cria o schema — quem cria as tabelas é o próprio app
(ao subir apontando a DATABASE_URL para o MySQL, o init_db faz o create_all).
Este script apenas COPIA as linhas, tabela a tabela.

Uso:
    python3 scripts/migrar_pg_para_mysql.py \
        "postgresql+psycopg://user:pass@host_antigo/portal" \
        "mysql+pymysql://user:pass@host_novo/portal"

Ou por variáveis de ambiente SRC_DATABASE_URL / DST_DATABASE_URL.

Seguro de repetir: por padrão faz TRUNCATE/limpeza da tabela de destino antes
de copiar (idempotente). Use --append para só inserir sem limpar.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, MetaData, select, insert, text


def _naive_utc(v):
    """MySQL DATETIME é 'naive'; converte datetimes com timezone para UTC naive."""
    if isinstance(v, datetime) and v.tzinfo is not None:
        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    append = "--append" in sys.argv

    src_url = (args[0] if len(args) > 0 else os.getenv("SRC_DATABASE_URL", "")).strip()
    dst_url = (args[1] if len(args) > 1 else os.getenv("DST_DATABASE_URL", "")).strip()
    if not src_url or not dst_url:
        print("Uso: migrar_pg_para_mysql.py <URL_ORIGEM_PG> <URL_DESTINO_MYSQL>")
        return 2

    print(f"ORIGEM : {src_url.split('@')[-1]}")
    print(f"DESTINO: {dst_url.split('@')[-1]}")
    print("-" * 60)

    src = create_engine(src_url, future=True)
    dst = create_engine(dst_url, future=True)

    # Reflete o schema dos DOIS lados (as tabelas já existem no destino,
    # criadas pelo app). Copiamos só as tabelas que existem em ambos.
    src_md = MetaData()
    src_md.reflect(bind=src)
    dst_md = MetaData()
    dst_md.reflect(bind=dst)

    dst_tables = set(dst_md.tables.keys())
    ordem = [t for t in src_md.sorted_tables if t.name in dst_tables]
    ignoradas = [t.name for t in src_md.sorted_tables if t.name not in dst_tables]
    if ignoradas:
        print(f"(!) Tabelas na origem sem correspondente no destino (ignoradas): {ignoradas}")

    total_geral = 0
    with dst.begin() as dconn:
        # Desliga checagem de FK durante a carga (MySQL).
        try:
            dconn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        except Exception:
            pass

        # Limpa destino em ordem inversa (respeitando FKs) se não for --append.
        if not append:
            for t in reversed(ordem):
                dconn.execute(dst_md.tables[t.name].delete())

        for t in ordem:
            dst_table = dst_md.tables[t.name]
            cols = [c.name for c in dst_table.columns]
            with src.connect() as sconn:
                rows = sconn.execute(select(t)).mappings().all()
            if not rows:
                print(f"{t.name:32s} 0 linhas")
                continue
            dados = []
            for r in rows:
                d = {k: _naive_utc(r[k]) for k in cols if k in r}
                dados.append(d)
            # Inserção em lotes.
            LOTE = 500
            for i in range(0, len(dados), LOTE):
                dconn.execute(insert(dst_table), dados[i:i + LOTE])
            print(f"{t.name:32s} {len(dados)} linhas")
            total_geral += len(dados)

        # Reajusta o AUTO_INCREMENT das tabelas com PK 'id' para max(id)+1,
        # senão os próximos INSERTs do app colidiriam com ids já copiados.
        for t in ordem:
            dst_table = dst_md.tables[t.name]
            if "id" in dst_table.columns:
                try:
                    mx = dconn.execute(
                        text(f"SELECT COALESCE(MAX(id),0) FROM `{t.name}`")
                    ).scalar() or 0
                    dconn.execute(text(f"ALTER TABLE `{t.name}` AUTO_INCREMENT = {int(mx) + 1}"))
                except Exception as exc:  # noqa: BLE001
                    print(f"(!) AUTO_INCREMENT de {t.name} não ajustado: {exc}")

        try:
            dconn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        except Exception:
            pass

    print("-" * 60)
    print(f"Concluído. {total_geral} linhas copiadas.")
    print("Dica: confira contagens com SELECT COUNT(*) nas principais tabelas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
