#!/usr/bin/env bash
# ============================================================================
#  Restaura no servidor NOVO um pacote gerado por scripts/backup.sh.
#
#    bash scripts/restaurar.sh /caminho/portal-spare-AAAAMMDD-HHMMSS.tar.gz
#
#  Restaura: Postgres (se houver dump e pg_restore), todos os SQLite,
#  uploads e dados de referência. NÃO restaura o arquivo de ambiente por
#  cima do atual — ele é só extraído ao lado, para você comparar.
#
#  O portal precisa estar PARADO. O script confere e avisa.
# ============================================================================
set -uo pipefail

PKG="${1:-}"
APP_DIR="${PORTAL_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENVFILE="${PORTAL_ENVFILE:-$HOME/.config/portal-spare/environment}"

[ -n "$PKG" ] || { echo "Uso: $0 <pacote.tar.gz>"; exit 1; }
[ -r "$PKG" ] || { echo "ERRO: pacote não encontrado: $PKG"; exit 1; }

echo "== Restauração no Portal SPARE =="
echo "   pacote: $PKG"
echo "   app:    $APP_DIR"

# Restaurar com a aplicação escrevendo corrompe SQLite e conflita no Postgres.
if pgrep -u "$(whoami)" -f "uvicorn main:app" >/dev/null 2>&1; then
    echo "ERRO: o portal está no ar. Pare antes:  $APP_DIR/deploy/portal.sh stop"
    exit 1
fi

if [ -f "$PKG.sha256" ]; then
    echo "-- Conferindo integridade"
    (cd "$(dirname "$PKG")" && sha256sum -c "$(basename "$PKG").sha256") \
        || { echo "ERRO: checksum não confere. Pacote corrompido."; exit 1; }
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$PKG" -C "$WORK" || { echo "ERRO ao extrair."; exit 1; }
# `find | head -1` sob `pipefail` devolve 141 (SIGPIPE); aqui o valor até vem
# certo, mas o padrão é frágil — melhor não usá-lo.
SRC=""
for d in "$WORK"/portal-spare-*; do
    [ -d "$d" ] && { SRC="$d"; break; }
done
[ -d "$SRC" ] || { echo "ERRO: conteúdo inesperado no pacote."; exit 1; }

[ -f "$SRC/VERSAO.txt" ] && { echo "-- Origem do pacote:"; sed 's/^/     /' "$SRC/VERSAO.txt"; }

# ── 1. Postgres ─────────────────────────────────────────────────────────────
if [ -f "$SRC/portal_postgres.dump" ]; then
    if [ -r "$ENVFILE" ]; then set -a; . "$ENVFILE"; set +a; fi
    if [ -z "${DATABASE_URL:-}" ]; then
        echo "-- Postgres: PULADO (DATABASE_URL vazio em $ENVFILE)"
    elif [[ "$DATABASE_URL" == sqlite* ]]; then
        echo "-- Postgres: PULADO (o destino está configurado como SQLite)"
        echo "   O dump continua disponível em: $SRC/portal_postgres.dump"
        cp -p "$SRC/portal_postgres.dump" "$HOME/" 2>/dev/null && \
            echo "   Cópia salva em $HOME/portal_postgres.dump"
    elif command -v pg_restore >/dev/null 2>&1; then
        # Mesmo cuidado do backup.sh: o pg_restore não entende o sufixo de
        # driver do SQLAlchemy e, sem normalizar, tenta o socket local.
        PG_URL="$(printf '%s' "$DATABASE_URL" | sed -E 's|^postgres(ql)?\+[a-z0-9_]+://|postgresql://|; s|^postgres://|postgresql://|')"
        echo "-- Postgres (pg_restore --clean)"
        pg_restore -d "$PG_URL" --clean --if-exists --no-owner \
            "$SRC/portal_postgres.dump" 2>"$WORK/pg.err" \
            && echo "   OK" \
            || { echo "   AVISO: pg_restore reportou erros:"; sed 's/^/     /' "$WORK/pg.err" | head -20; }
    else
        echo "-- Postgres: PULADO (pg_restore não instalado)"
        cp -p "$SRC/portal_postgres.dump" "$HOME/" 2>/dev/null && \
            echo "   Dump salvo em $HOME/portal_postgres.dump para restaurar depois."
    fi
else
    echo "-- Postgres: sem dump no pacote"
fi

# ── 2. SQLite ───────────────────────────────────────────────────────────────
if [ -d "$SRC/sqlite" ]; then
    echo "-- SQLite -> $APP_DIR/data/db/"
    mkdir -p "$APP_DIR/data/db"
    n=0
    for f in "$SRC"/sqlite/*.db; do
        [ -e "$f" ] || continue
        nome="$(basename "$f")"
        # Guarda o que já existia: restauração nunca deve ser irreversível.
        [ -e "$APP_DIR/data/db/$nome" ] && \
            mv "$APP_DIR/data/db/$nome" "$APP_DIR/data/db/$nome.anterior-$(date +%Y%m%d-%H%M%S)"
        cp -p "$f" "$APP_DIR/data/db/$nome" && n=$((n+1))
    done
    echo "   $n banco(s) restaurado(s)"
else
    echo "-- SQLite: nada no pacote"
fi

# ── 3. Arquivos ─────────────────────────────────────────────────────────────
for dir in "data/uploads" "data/referencias"; do
    if [ -d "$SRC/arquivos/$dir" ]; then
        echo "-- $dir"
        mkdir -p "$APP_DIR/$(dirname "$dir")"
        cp -a "$SRC/arquivos/$dir/." "$APP_DIR/$dir/" 2>/dev/null \
            || echo "   AVISO: falha ao copiar $dir"
    fi
done
# Instalação antiga guardava a referência em static/data.
if [ -d "$SRC/arquivos/static/data" ]; then
    echo "-- static/data (layout antigo) -> data/referencias"
    mkdir -p "$APP_DIR/data/referencias"
    cp -a "$SRC/arquivos/static/data/." "$APP_DIR/data/referencias/" 2>/dev/null || true
fi

# ── 4. Ambiente: só para comparar, nunca sobrescrito ────────────────────────
if [ -f "$SRC/environment.txt" ]; then
    cp -p "$SRC/environment.txt" "$HOME/environment-do-servidor-antigo.txt"
    chmod 600 "$HOME/environment-do-servidor-antigo.txt"
    echo "-- Ambiente do servidor antigo salvo em:"
    echo "     $HOME/environment-do-servidor-antigo.txt"
    echo "   Compare com $ENVFILE e traga só o que fizer sentido no servidor novo."
fi

cat <<EOF

== Restauração concluída ==
   Suba o portal:  $APP_DIR/deploy/portal.sh start
   Confira:        $APP_DIR/deploy/portal.sh status
EOF
