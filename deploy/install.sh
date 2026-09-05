#!/usr/bin/env bash
set -euo pipefail
 
INSTALL_DIR="/opt/portal-spare-v2"
CONFIG_DIR="/etc/portal_operacoes_spare"
SERVICE_USER="portalspare"
PORT=8901
 
echo "=== Portal de Operações SPARE v2 — Instalação ==="
 
if ss -tlnp | grep -q ":${PORT} "; then
    echo "ERRO: Porta ${PORT} já está em uso."
    exit 1
fi
 
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --shell /usr/sbin/nologin --home-dir "$INSTALL_DIR" "$SERVICE_USER"
    echo "Usuário $SERVICE_USER criado."
fi
 
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR/credentials" "$INSTALL_DIR/data/uploads"
 
echo "Copiando arquivos para $INSTALL_DIR ..."
cp -r . "$INSTALL_DIR/"
 
if [ ! -f "$CONFIG_DIR/environment" ]; then
    SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")
    INITPASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(12))")
    cat > "$CONFIG_DIR/environment" <<ENVEOF
DATABASE_URL=postgresql+psycopg://portal_spare_app:ALTERAR@127.0.0.1:5432/portal_operacoes_spare_db
PORTAL_SESSION_SECRET=${SESSION_SECRET}
SESSION_TTL_MINUTES=480
EBS_LOGIN_URL=https://suporte.lojasrenner.com.br/ebs/api/auth/login
EBS_SEARCH_URL=https://suporte.lojasrenner.com.br/ebs/api/estoque/busca-imobilizado
VERIFY_SSL=false
TIMEOUT_SECONDS=15
MAX_WORKERS=10
CREDENTIALS_DIRECTORY=/run/credentials/portal_spare.service
HOST=0.0.0.0
PORT=${PORT}
WORKERS=1
DEFAULT_VALOR_HORA=150.00
INITIAL_ADMIN_LOGIN=ALTERAR_LOGIN_ADMIN
INITIAL_ADMIN_PASSWORD=${INITPASS}
UPLOAD_MAX_MB=50
RATE_LIMIT_LOGIN=5/minute
RATE_LIMIT_API=120/minute
ENVEOF
    chmod 600 "$CONFIG_DIR/environment"
    echo ""
    echo "Arquivo de ambiente criado em $CONFIG_DIR/environment"
    echo "Senha temporária do admin: $INITPASS"
    echo "ALTERE DATABASE_URL e INITIAL_ADMIN_LOGIN antes de iniciar."
fi
 
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod -R 750 "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR/data"
chown root:root "$CONFIG_DIR/environment"
chmod 600 "$CONFIG_DIR/environment"
 
cp "$INSTALL_DIR/deploy/portal_spare.service" /etc/systemd/system/portal_spare.service
systemctl daemon-reload
systemctl enable portal_spare.service
 
echo ""
echo "=== Instalação concluída ==="
echo "URL: http://$(hostname -I | awk '{print $1}'):${PORT}"
echo "TV:  http://$(hostname -I | awk '{print $1}'):${PORT}/tv"
echo ""
echo "Próximos passos:"
echo "  1. Edite $CONFIG_DIR/environment (DATABASE_URL, INITIAL_ADMIN_LOGIN)"
echo "  2. Configure credenciais EBS em $CONFIG_DIR/credentials/"
echo "  3. systemctl start portal_spare.service"
echo "  4. Troque a senha do admin no primeiro acesso."
