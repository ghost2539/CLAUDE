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

# ── 3. Arquivo de ambiente ──────────────────────────────────────────────────
if [ -f "$ENVFILE" ]; then
    echo "-- Arquivo de ambiente já existe (mantido): $ENVFILE"
else
    echo "-- Gerando $ENVFILE a partir de deploy/environment.modelo"
    cp "$APP_DIR/deploy/environment.modelo" "$ENVFILE"
    chmod 600 "$ENVFILE"

    # O segredo de sessão nasce NO COFRE: o environment é lido por qualquer
    # um que abra o arquivo e entra em backup.
    SEGREDO="$("$APP_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    if "$APP_DIR/venv/bin/python" "$APP_DIR/scripts/cofre.py" definir \
            PORTAL_SESSION_SECRET --valor "$SEGREDO" >/dev/null 2>&1; then
        echo "   segredo de sessão gerado e guardado no cofre"
    else
        echo "   AVISO: não consegui gravar no cofre; gravando no arquivo."
        sed -i "s|^PORTAL_SESSION_SECRET=.*|PORTAL_SESSION_SECRET=$SEGREDO|" "$ENVFILE"
    fi
    unset SEGREDO

    # Banco: SQLite local já preenchido, para o portal subir sem servidor de
    # banco. Trocar para Postgres é editar uma linha.
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=sqlite:///$APP_DIR/data/db/portal.db|" "$ENVFILE"

    echo "   >> Falta preencher o que está marcado com [PREENCHER]:"
    grep -n "\[PREENCHER\]" "$ENVFILE" | sed 's/^/      /' | head -20
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
     Segredos NÃO vão nele — guarde no cofre:
        $APP_DIR/venv/bin/python scripts/cofre.py definir NOME
        $APP_DIR/venv/bin/python scripts/cofre.py conferir
  2. Suba o portal:       $APP_DIR/deploy/portal.sh start
  3. Confira:             $APP_DIR/deploy/portal.sh status
  4. Acompanhe o log:     $APP_DIR/deploy/portal.sh logs

  Depois, para atualizar a partir do GitHub:
     $APP_DIR/deploy/portal.sh atualizar
EOF
