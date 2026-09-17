#!/usr/bin/env python3
"""Carrega um dump PostgreSQL (SQL puro, --inserts) num banco SQLite.

    python3 scripts/converter_pg_para_sqlite.py ~/portal.sql            # simula
    python3 scripts/converter_pg_para_sqlite.py ~/portal.sql --gravar   # grava

Serve para o caso em que o servidor de origem roda Postgres e o de destino
está configurado com SQLite. Não é um conversor genérico: é o caminho
estreito que funciona para ESTE portal, e recusa o que não sabe fazer.

Como funciona, e por que assim:

- O ESQUEMA não é convertido. O dump do Postgres traz `SET`, sequências,
  `OWNER TO` e tipos que o SQLite não entende, e traduzir isso à mão é onde
  esse tipo de migração costuma estragar dado em silêncio. Em vez disso,
  quem cria as tabelas é o próprio portal: suba ele uma vez com o banco
  vazio e o `create_all` monta o esquema certo, do jeito que o código espera.
  Aqui só entram as LINHAS.

- As datas são o motivo de isto ser um script e não um comando. O Postgres
  grava com fuso (`2026-09-17 14:30:00-03`) e o leitor de SQLite do
  SQLAlchemy não entende esse formato: ele quebra na LEITURA, muito depois,
  quando alguém abre a tela. Cada literal de data vira UTC e perde o
  sufixo, que é exatamente como o portal grava quando está em SQLite.

- Tabela que existe no dump e não no SQLite é reportada, não inventada.
  Se o esquema não bate, o certo é descobrir por quê, não criar tabela
  torta e descobrir na tela.

Sem `--gravar` ele não escreve nada: lê o dump, diz quantas linhas achou
por tabela e o que faria. É a forma de conferir antes de mexer no banco.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

GRAVAR = "--gravar" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
if not ARGS:
    print(__doc__)
    sys.exit(1)
DUMP = Path(ARGS[0]).expanduser()
if not DUMP.is_file():
    print(f"ERRO: não achei o dump: {DUMP}")
    sys.exit(1)


def caminho_do_sqlite() -> Path:
    """O arquivo que o portal usa hoje, lido da própria configuração."""
    import config as _config_mod
    url = _config_mod.get_settings().DATABASE_URL
    if not url.startswith("sqlite"):
        print("ERRO: o DATABASE_URL deste servidor NÃO é SQLite:")
        print("      " + re.sub(r"://[^@]*@", "://…@", url))
        print("      Este script só serve para destino SQLite. Com Postgres")
        print("      no destino, use o dump direto, sem converter.")
        sys.exit(1)
    return Path(url.split("///", 1)[1])


# ── Leitura do dump ─────────────────────────────────────────────────────
def statements(texto: str):
    """Quebra o arquivo em comandos, respeitando aspas.

    Não dá para quebrar no `;` e pronto: campo de texto tem ponto e vírgula
    dentro, e um INSERT partido no meio viraria comando inválido — ou, pior,
    um comando válido com dado faltando.
    """
    buf: list[str] = []
    i, n = 0, len(texto)
    aspas = False
    while i < n:
        c = texto[i]
        if aspas:
            if c == "'":
                # '' é uma aspa escapada, não o fim da string.
                if i + 1 < n and texto[i + 1] == "'":
                    buf.append("''")
                    i += 2
                    continue
                aspas = False
            elif c == "\\" and i + 1 < n:
                buf.append(texto[i:i + 2])
                i += 2
                continue
            buf.append(c)
            i += 1
            continue
        if c == "'":
            aspas = True
            buf.append(c)
            i += 1
            continue
        if c == ";":
            yield "".join(buf).strip()
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    resto = "".join(buf).strip()
    if resto:
        yield resto


_RE_INSERT = re.compile(r'^INSERT\s+INTO\s+(?:public\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?\s',
                        re.IGNORECASE)
# Data/hora com fuso, que é o que o SQLite não sabe ler de volta.
_RE_TS = re.compile(
    r"'(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(\.\d+)?([+-]\d{2})(?::?(\d{2}))?'")


def _para_utc(m: re.Match) -> str:
    data, hora, frac, sinal_h, sinal_m = m.groups()
    dt = datetime.fromisoformat(f"{data} {hora}{frac or ''}")
    desloc = timedelta(hours=abs(int(sinal_h)), minutes=int(sinal_m or 0))
    if sinal_h.startswith("-"):
        dt = dt + desloc
    else:
        dt = dt - desloc
    return "'" + dt.strftime("%Y-%m-%d %H:%M:%S.%f") + "'"


def adaptar(sql: str) -> str:
    """Do dialeto do Postgres para o que o SQLite aceita."""
    sql = re.sub(r'\bpublic\.', '', sql)
    sql = _RE_TS.sub(_para_utc, sql)
    # Sem fuso, mas sem os microssegundos que o leitor do SQLAlchemy espera.
    sql = re.sub(r"'(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})'",
                 r"'\1 \2.000000'", sql)
    return sql


print(f"Lendo {DUMP}  ({DUMP.stat().st_size/1048576:.1f} MB)")
texto = DUMP.read_text(encoding="utf-8", errors="replace")

if re.search(r"^COPY\s+", texto, re.MULTILINE):
    print()
    print("ERRO: o dump usa COPY, não INSERT.")
    print("      Gere de novo no servidor de origem COM --inserts:")
    print("        pg_dump --format=plain --no-owner --no-acl --clean \\")
    print("                --if-exists --inserts --dbname=\"$PG_URL\" -f ~/portal.sql")
    sys.exit(1)

por_tabela: dict[str, list[str]] = {}
ignorados = 0
for cmd in statements(texto):
    m = _RE_INSERT.match(cmd)
    if not m:
        ignorados += 1
        continue
    por_tabela.setdefault(m.group(1).lower(), []).append(adaptar(cmd))

if not por_tabela:
    print("Nenhum INSERT encontrado. O dump está vazio ou não é do formato esperado.")
    sys.exit(1)

destino = caminho_do_sqlite()
print(f"Destino: {destino}")
if not destino.is_file():
    print()
    print("ERRO: o arquivo do banco não existe ainda.")
    print("      Suba o portal UMA vez para ele criar as tabelas, pare, e rode de novo.")
    sys.exit(1)

con = sqlite3.connect(destino)
existentes = {r[0].lower() for r in
              con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

print(f"\n{'tabela':28} {'no dump':>9}  {'no banco hoje':>13}")
faltando = []
for t in sorted(por_tabela):
    atual = "—"
    if t in existentes:
        atual = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    else:
        faltando.append(t)
    print(f"{t:28} {len(por_tabela[t]):>9}  {str(atual):>13}")

if faltando:
    print("\nTabelas que o dump traz e o banco NÃO tem:")
    for t in faltando:
        print("  -", t)
    print("Suba o portal uma vez para criar o esquema, ou confira se é o banco certo.")

if not GRAVAR:
    print(f"\n({ignorados} comandos que não são INSERT foram ignorados — "
          "esquema, sequências e permissões vêm do próprio portal.)")
    print("\nSimulação. Nada foi gravado. Para gravar de verdade:")
    print(f"    python3 {Path(__file__).relative_to(RAIZ)} {DUMP} --gravar")
    sys.exit(0)

# ── Gravação ────────────────────────────────────────────────────────────
print("\nGravando…")
con.execute("PRAGMA foreign_keys=OFF")   # a ordem do dump não garante pai antes de filho
total, falhas = 0, []
for t in sorted(por_tabela):
    if t not in existentes:
        continue
    ok = 0
    for cmd in por_tabela[t]:
        try:
            con.execute(cmd)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            if len(falhas) < 20:
                falhas.append(f"{t}: {type(exc).__name__}: {exc} | {cmd[:160]}")
    con.commit()
    total += ok
    print(f"  {t:28} {ok:>9} linha(s)")
con.execute("PRAGMA foreign_keys=ON")

print(f"\n{total} linha(s) gravadas.")
if falhas:
    print(f"\n{len(falhas)} falha(s) (mostrando as primeiras):")
    for f in falhas:
        print("  -", f)
    sys.exit(1)

print("\nConfira as contagens contra o servidor de origem antes de liberar.")
