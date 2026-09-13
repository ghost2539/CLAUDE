#!/usr/bin/env bash
# ============================================================================
#  Instalação do Portal SPARE SEM ROOT — direto do GitHub.
#
#    bash deploy/instalar_usuario.sh
#
#  Cria/atualiza o venv, instala as dependências, prepara as pastas de dados
#  e gera o arquivo de ambiente em ~/.config/portal-spare/environment.
#  Não mexe em /etc, não pede sudo, não instala pacote de sistema.
#
#  Pré-requisitos no servidor: python3 (3.10+), python3-venv e git.
# ============================================================================
set -uo pipefail

APP_DIR="${PORTAL_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENVDIR="$HOME/.config/portal-spare"
ENVFILE="${PORTAL_ENVFILE:-$ENVDIR/environment}"
STATE="$HOME/.local/state/portal-spare"

echo "== Instalação do Portal SPARE (usuário $(whoami), sem root) =="
echo "   app: $APP_DIR"

# ── 1. Python ───────────────────────────────────────────────────────────────
command -v python3 >/dev/null 2>&1 || { echo "ERRO: python3 não encontrado."; exit 1; }
VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "-- Python $VER"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || {
    echo "ERRO: é necessário Python 3.10 ou superior (encontrado $VER)."; exit 1; }

if [ ! -x "$APP_DIR/venv/bin/python" ]; then
    echo "-- Criando o venv"
    python3 -m venv "$APP_DIR/venv" || {
        echo "ERRO: falha ao criar o venv. Falta o pacote python3-venv?"; exit 1; }
fi
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
echo "-- Instalando dependências (pode demorar)"
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || {
    echo "ERRO: falha ao instalar as dependências (rede/proxy?)."; exit 1; }

# ── 2. Pastas de dados ──────────────────────────────────────────────────────
echo "-- Pastas de dados"
mkdir -p "$APP_DIR/data/db" "$APP_DIR/data/uploads" "$STATE" "$ENVDIR"
chmod 700 "$ENVDIR"

# ── 2b. Backup diário (crontab do usuário, idempotente) ─────────────────────
# Sem isto o backup.sh existe e ninguém o chama. Às 02h, com o APP_DIR
# desta instalação (o script sozinho assume /opt/portal-spare-v2).
if command -v crontab >/dev/null 2>&1; then
    LINHA_CRON="0 2 * * * PORTAL_APP_DIR=$APP_DIR $APP_DIR/scripts/backup.sh >> $STATE/backup.log 2>&1"
    if ! crontab -l 2>/dev/null | grep -Fq "$APP_DIR/scripts/backup.sh"; then
        ( crontab -l 2>/dev/null; echo "$LINHA_CRON" ) | crontab - \
            && echo "-- Backup diário agendado (02h) no crontab do usuário" \
            || echo "AVISO: não consegui agendar o backup; agende à mão: $LINHA_CRON"
    else
        echo "-- Backup diário já agendado"
    fi
else
    echo "AVISO: sem crontab neste servidor; agende o backup à mão (scripts/backup.sh)."
fi

# ── 3. Arquivo de ambiente ──────────────────────────────────────────────────
if [ -f "$ENVFILE" ]; then
    echo "-- Arquivo de ambiente já existe (mantido): $ENVFILE"
else
    echo "-- Gerando $ENVFILE"
    SEGREDO="$("$APP_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    cat > "$ENVFILE" <<EOF
# Ambiente do Portal SPARE — NÃO versionar. Gerado em $(date '+%d/%m/%Y %H:%M').

# Banco principal. Em produção é Postgres:
#   DATABASE_URL=postgresql+psycopg2://usuario:senha@host:5432/portal_spare
# Para TESTE sem servidor de banco, o SQLite abaixo já funciona:
DATABASE_URL=sqlite:///$APP_DIR/data/db/portal.db

PORTAL_SESSION_SECRET=$SEGREDO

HOST=0.0.0.0
PORT=8901
WORKERS=1

# Primeiro administrador: coloque aqui o SEU login de rede, senão ninguém
# consegue liberar o acesso de mais ninguém.
INITIAL_ADMIN_LOGIN=ALTERAR_LOGIN_ADMIN

# ── Integrações (preencher conforme for testando) ──
# EBS_LOGIN_URL=
# SN_API_USER=
# SN_API_PASSWORD=
# CORREIOS_USUARIO=
# CORREIOS_CHAVE=

# ── Alertas por e-mail (opcional) ──
# SMTP_HOST=
# SMTP_PORT=25
# ALERTA_EMAIL_TO=raphael.steilein@lojasrenner.com.br
EOF
    chmod 600 "$ENVFILE"
    echo "   >> AJUSTE o INITIAL_ADMIN_LOGIN antes de subir."
fi

# ── 4. Conferência: o app carrega? ──────────────────────────────────────────
echo "-- Conferindo se o portal carrega"
set -a; . "$ENVFILE"; set +a
cd "$APP_DIR" || exit 1
"$APP_DIR/venv/bin/python" - <<'PY'
import logging, sys
logging.basicConfig(level=logging.ERROR)
try:
    import main
except Exception as exc:
    print(f"   FALHOU ao importar a aplicação: {exc}")
    sys.exit(1)

def anda(rotas, achadas):
    for r in rotas:
        orig = getattr(r, "original_router", None)
        if orig is not None:
            anda(orig.routes, achadas); continue
        sub = getattr(r, "routes", None)
        if sub:
            anda(sub, achadas); continue
        p = getattr(r, "path", "")
        if p:
            achadas.add(p)
    return achadas

print(f"   OK — {len(anda(main.app.routes, set()))} rotas registradas.")
PY
[ $? -eq 0 ] || { echo "ERRO: a aplicação não subiu. Verifique o log acima."; exit 1; }

chmod +x "$APP_DIR/deploy/portal.sh" 2>/dev/null

cat <<EOF

== Pronto ==

  1. Revise o ambiente:   $ENVFILE
  2. Suba o portal:       $APP_DIR/deploy/portal.sh start
  3. Confira:             $APP_DIR/deploy/portal.sh status
  4. Acompanhe o log:     $APP_DIR/deploy/portal.sh logs

  Depois, para atualizar a partir do GitHub:
     $APP_DIR/deploy/portal.sh atualizar
EOF
