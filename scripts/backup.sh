#!/usr/bin/env bash
# ============================================================================
#  Backup TOTAL do Portal de Operações SPARE
#  Uso:   ./scripts/backup.sh [destino]      (padrão: $HOME/backups-portal-spare)
#         ./scripts/backup.sh --listar
#
#  Cobre: Postgres (portal) · todos os SQLite · arquivo de ambiente ·
#         uploads · dados estáticos · referência do commit em produção.
#  É SOMENTE LEITURA no sistema — não altera nada da aplicação.
#
#  Agendar diariamente às 02h (crontab -e):
#    0 2 * * * /opt/portal-spare-v2/scripts/backup.sh >> $HOME/backup-portal.log 2>&1
# ============================================================================
set -uo pipefail

APP_DIR="${PORTAL_APP_DIR:-/opt/portal-spare-v2}"
ENVFILE="${PORTAL_ENVFILE:-/etc/portal_operacoes_spare/environment}"
DEST="${1:-$HOME/backups-portal-spare}"
RET_DAYS="${BACKUP_RETENTION_DAYS:-30}"

if [ "${1:-}" = "--listar" ]; then
  echo "Backups em ${DEST:-$HOME/backups-portal-spare}:"
  ls -lh "${DEST:-$HOME/backups-portal-spare}"/portal-spare-*.tar.gz 2>/dev/null || echo "  (nenhum)"
  exit 0
fi

TS="$(date +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
STAGE="$WORK/portal-spare-$TS"
mkdir -p "$STAGE"
mkdir -p "$DEST"
ERROS=0
aviso() { echo "  [AVISO] $*"; ERROS=$((ERROS+1)); }

echo "== Backup Portal SPARE — $TS =="
echo "   app:     $APP_DIR"
echo "   destino: $DEST"

# Carrega variáveis (DATABASE_URL etc.) sem exportar para o resto do shell
if [ -r "$ENVFILE" ]; then
  set -a; . "$ENVFILE"; set +a
  cp -p "$ENVFILE" "$STAGE/environment.txt" 2>/dev/null \
    && chmod 600 "$STAGE/environment.txt" \
    || aviso "não consegui copiar o ENVFILE"
else
  aviso "ENVFILE não legível ($ENVFILE) — backup segue sem ele"
fi

# ── 1. Postgres (banco do portal) ───────────────────────────────────────────
# A URL da aplicação vem no formato do SQLAlchemy (postgresql+psycopg2://...).
# O pg_dump não entende o sufixo do driver: em vez de reclamar, ele trata a
# string inteira como NOME de banco e tenta o socket local — falhando com uma
# mensagem que não tem nada a ver com a causa. Por isso normalizamos aqui.
url_para_pg_dump() {
  printf '%s' "$1" | sed -E 's|^postgres(ql)?\+[a-z0-9_]+://|postgresql://|; s|^postgres://|postgresql://|'
}

PG_ESPERADO=0   # o portal usa Postgres e o dump precisa entrar no pacote
PG_OK=0         # o dump entrou

if [ -z "${DATABASE_URL:-}" ]; then
  aviso "DATABASE_URL vazio — Postgres NÃO incluído"
elif [[ "$DATABASE_URL" == sqlite* ]]; then
  echo "-- Postgres: não se aplica (o portal está em SQLite)"
elif ! command -v pg_dump >/dev/null 2>&1; then
  aviso "pg_dump não instalado — Postgres NÃO incluído (instale postgresql-client)"
else
  PG_ESPERADO=1
  PG_URL="$(url_para_pg_dump "$DATABASE_URL")"
  echo "-- Postgres (pg_dump)"
  if pg_dump -Fc --no-owner --dbname="$PG_URL" -f "$STAGE/portal_postgres.dump" 2>"$STAGE/pg_dump.err"; then
    rm -f "$STAGE/pg_dump.err"
    PG_OK=1
    echo "   $(du -h "$STAGE/portal_postgres.dump" | cut -f1)"
  else
    # O erro precisa aparecer AGORA: escondido dentro do pacote, passa batido
    # e o backup vai para a estante sem o banco principal.
    aviso "pg_dump FALHOU — o banco do portal NÃO está neste pacote:"
    sed 's/^/          /' "$STAGE/pg_dump.err" | head -8
    rm -f "$STAGE/portal_postgres.dump"
  fi
fi

# ── 2. SQLite (execução CAPEX, indicadores, automações, monitoramento) ──────
echo "-- SQLite"
mkdir -p "$STAGE/sqlite"
copiado=0
for db in "$APP_DIR"/data/db/*.db "$APP_DIR"/data/*.db; do
  [ -e "$db" ] || continue
  nome="$(basename "$db")"
  if command -v sqlite3 >/dev/null 2>&1; then
    # .backup é consistente mesmo com a app escrevendo (WAL)
    sqlite3 "$db" ".backup '$STAGE/sqlite/$nome'" 2>/dev/null || cp -p "$db" "$STAGE/sqlite/$nome"
  else
    cp -p "$db" "$STAGE/sqlite/$nome"
    for extra in "$db-wal" "$db-shm"; do
      [ -e "$extra" ] && cp -p "$extra" "$STAGE/sqlite/"
    done
  fi
  copiado=$((copiado+1))
done
[ "$copiado" -gt 0 ] && echo "   $copiado banco(s) SQLite" || aviso "nenhum SQLite encontrado em $APP_DIR/data"

# ── 3. Arquivos críticos ────────────────────────────────────────────────────
echo "-- Arquivos (uploads, dados estáticos)"
for dir in "data/uploads" "data/referencias"; do
  if [ -d "$APP_DIR/$dir" ]; then
    mkdir -p "$STAGE/arquivos/$(dirname "$dir")"
    cp -a "$APP_DIR/$dir" "$STAGE/arquivos/$dir" 2>/dev/null || aviso "falha ao copiar $dir"
  fi
done

# ── 4. Referência do código em produção (para rollback) ─────────────────────
if [ -d "$APP_DIR/.git" ]; then
  {
    echo "commit=$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null)"
    echo "branch=$(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    echo "data=$TS"
    git -C "$APP_DIR" log --oneline -5 2>/dev/null
  } > "$STAGE/VERSAO.txt"
fi

# ── 5. Empacota + checksum + retenção ───────────────────────────────────────
PKG="$DEST/portal-spare-$TS.tar.gz"
tar -czf "$PKG" -C "$WORK" "portal-spare-$TS" 2>/dev/null || { echo "ERRO: falha ao empacotar"; rm -rf "$WORK"; exit 1; }
chmod 600 "$PKG"
sha256sum "$PKG" > "$PKG.sha256" 2>/dev/null
rm -rf "$WORK"

find "$DEST" -name 'portal-spare-*.tar.gz*' -type f -mtime +"$RET_DAYS" -delete 2>/dev/null

echo "== Concluído: $PKG ($(du -h "$PKG" | cut -f1)) — avisos: $ERROS =="
# Conferência pela flag do passo 1, e não relendo o pacote: `tar | grep -q`
# fazia o grep sair no primeiro acerto, o tar levar SIGPIPE e o `pipefail`
# tratar isso como falha — o aviso disparava justamente quando o dump era
# grande e vinha no começo da listagem.
if [ "$PG_ESPERADO" = 1 ] && [ "$PG_OK" != 1 ]; then
  echo
  echo "  ATENÇÃO: este pacote NÃO tem o banco do portal (Postgres)."
  echo "           Não serve para migrar sozinho. Resolva o erro acima e refaça."
fi
echo
echo "RESTAURAR:"
echo "  tar -xzf $PKG -C /tmp"
echo "  # Postgres:  pg_restore -d \"\$DATABASE_URL\" --clean --no-owner /tmp/portal-spare-$TS/portal_postgres.dump"
echo "  # SQLite:    ./portal.sh stop && cp /tmp/portal-spare-$TS/sqlite/*.db $APP_DIR/data/db/ && ./portal.sh start"
[ "$ERROS" -gt 0 ] && exit 2 || exit 0
