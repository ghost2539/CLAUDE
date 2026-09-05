#!/usr/bin/env bash
set -euo pipefail

# ── Gera certificado autoassinado para HTTPS local ──────────────
# Uso: ./generate-cert.sh [hostname]
# Ex:  ./generate-cert.sh portalspare.local

HOSTNAME="${1:-portalspare.local}"
CERT_DIR="/etc/portal_operacoes_spare/ssl"
DAYS=365

mkdir -p "$CERT_DIR"

openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$CERT_DIR/portal.key" \
    -out "$CERT_DIR/portal.crt" \
    -days "$DAYS" \
    -subj "/CN=${HOSTNAME}" \
    -addext "subjectAltName=DNS:${HOSTNAME},DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/portal.key"
chmod 644 "$CERT_DIR/portal.crt"

echo "Certificado gerado:"
echo "  Cert: $CERT_DIR/portal.crt"
echo "  Key:  $CERT_DIR/portal.key"
echo "  Host: $HOSTNAME"
echo "  Validade: $DAYS dias"
echo ""
echo "Para ativar HTTPS, adicione ao environment:"
echo "  SSL_CERTFILE=$CERT_DIR/portal.crt"
echo "  SSL_KEYFILE=$CERT_DIR/portal.key"
echo ""
echo "Para acessar via nome (sem IP):"
echo "  1. Adicione no DNS do servidor ou no /etc/hosts de cada máquina:"
echo "     <IP_DO_SERVIDOR>  $HOSTNAME"
echo "  2. Acesse: https://$HOSTNAME:8901"
echo ""
echo "NOTA: Certificado autoassinado — os navegadores mostrarão aviso."
echo "Para uso interno, aceite o aviso ou instale o .crt como CA confiável."
