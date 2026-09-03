#!/usr/bin/env bash
# Instala o Controle de Orçamento (/tv2) como SERVIÇO PRÓPRIO, isolado do portal.
# Uso: sudo bash scripts/controle_orcamento_instalar.sh   (executar na pasta do projeto)
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/controle-orcamento}"
CONFIG_DIR="${CONFIG_DIR:-/etc/controle_orcamento}"
SERVICE_USER="portalspare"
PORT=8902
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Controle de Orçamento — instalação isolada em $INSTALL_DIR (porta $PORT) ==="

ROOT_OK=0; [ "$(id -u)" = "0" ] && ROOT_OK=1
if [ "$ROOT_OK" = 1 ] && ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
fi
mkdir -p "$INSTALL_DIR/routers" "$INSTALL_DIR/static/controle-orcamento" "$INSTALL_DIR/scripts" "$INSTALL_DIR/data" "$CONFIG_DIR"

# Apenas os arquivos do módulo (nada do restante do portal)
cp "$SRC/controle_orcamento_app.py" "$SRC/config.py" "$SRC/security.py" "$SRC/database_orcamento.py" "$INSTALL_DIR/"
cp "$SRC/requirements-controle-orcamento.txt" "$INSTALL_DIR/"
touch "$INSTALL_DIR/routers/__init__.py"
cp "$SRC/routers/controle_orcamento.py" "$INSTALL_DIR/routers/"
cp "$SRC/static/controle-orcamento/"* "$INSTALL_DIR/static/controle-orcamento/"
cp "$SRC/static/favicon.svg" "$INSTALL_DIR/static/" 2>/dev/null || true
cp "$SRC/scripts/controle_orcamento_dados.py" "$INSTALL_DIR/scripts/"

# Ambiente Python: venv que enxerga os pacotes do sistema (reaproveita o que o portal já tem)
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
    python3 -m venv --system-site-packages "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/python" -c "import fastapi, uvicorn, sqlalchemy, itsdangerous, pydantic" 2>/dev/null \
    || "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements-controle-orcamento.txt"

if [ ! -f "$CONFIG_DIR/environment" ]; then
    cat > "$CONFIG_DIR/environment" <<ENVEOF
# Banco EXCLUSIVO do Controle de Orçamento. Ajuste usuário/senha/host.
CONTROLE_ORCAMENTO_DATABASE_URL=postgresql+psycopg2://portal_spare_app:ALTERAR_SENHA@127.0.0.1:5432/controle_orcamento_db
RATE_LIMIT_API=120/minute
UPLOAD_MAX_MB=5
ENVEOF
    chmod 600 "$CONFIG_DIR/environment"
    echo "Arquivo de ambiente criado em $CONFIG_DIR/environment — AJUSTE a senha antes de iniciar."
fi

chmod 700 "$INSTALL_DIR/data"
if [ "$ROOT_OK" = 1 ]; then
    chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
    cp "$SRC/controle_orcamento.service" /etc/systemd/system/controle_orcamento.service
    systemctl daemon-reload
    systemctl enable controle_orcamento.service
else
    echo "(sem root: usuário de serviço, chown e systemd não configurados)"
fi

echo ""
echo "=== Instalado. Próximos passos ==="
echo "  1. Edite $CONFIG_DIR/environment (senha do banco)"
echo "  2. systemctl start controle_orcamento.service"
echo "  3. curl -s http://127.0.0.1:$PORT/api/controle-orcamento/projetos | head -c 200"
echo "  URL: http://$(hostname -I | awk '{print $1}'):$PORT/tv2"
