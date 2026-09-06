#!/usr/bin/env bash
# Prepara e confere o servidor para o RPA de EBS Forms.
#
# Uso:  bash scripts/ebs_forms_preparar.sh            conferir e compilar
#       bash scripts/ebs_forms_preparar.sh testar     + abrir o Forms na tela virtual e fotografar
#
# Não precisa de root. O que faltar de pacote (Xvfb, fontes, javac) é
# listado com o comando que o administrador deve rodar.
set -u
cd "$(dirname "$0")/.."
APP_DIR="$PWD"
DADOS="$APP_DIR/data/ebs_forms"
mkdir -p "$DADOS/bin" "$DADOS/jars" "$DADOS/capturas" "$DADOS/logs" "$DADOS/depuracao"
chmod 700 "$DADOS"

ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
falta() { printf '  \033[31m✘\033[0m %s\n' "$*"; FALTAS=$((FALTAS+1)); }
aviso() { printf '  \033[33m!\033[0m %s\n' "$*"; }
FALTAS=0

ENV_FILE="${PORTAL_ENV_FILE:-$HOME/.config/portal-spare/environment}"
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi
# Modo isolado: numa cópia à parte (teste no servidor antigo, que roda o
# branch producao), não existe environment do portal. O config.py exige
# banco e segredo de sessão; aqui basta algo local — o RPA não toca o portal.
if [ -z "${DATABASE_URL:-}" ]; then
  export DATABASE_URL="sqlite:///$DADOS/portal_isolado.db"
  export PORTAL_SESSION_SECRET="${PORTAL_SESSION_SECRET:-isolado-$(hostname)}"
  export INITIAL_ADMIN_LOGIN="${INITIAL_ADMIN_LOGIN:-isolado}"
  export COFRE_CORPORATIVO="${COFRE_CORPORATIVO:-nao}"
  echo "  (modo isolado: sem environment do portal; usando banco local em data/ebs_forms/)"
fi

echo "== Java =="
JAVA="${EBS_FORMS_JAVA:-$(command -v java || true)}"
JAVAC="${EBS_FORMS_JAVAC:-$(command -v javac || true)}"
if [ -n "$JAVA" ]; then
  ok "java: $JAVA — $(JAVA_TOOL_OPTIONS= "$JAVA" -version 2>&1 | head -1)"
else
  falta "java não encontrado (dnf install java-21-openjdk)"
fi
if [ -n "$JAVAC" ]; then
  ok "javac: $JAVAC"
else
  falta "javac não encontrado (dnf install java-21-openjdk-devel) — ou copie data/ebs_forms/bin de outra máquina"
fi
if [ -x /usr/bin/javaws ]; then
  aviso "javaws existe ($(rpm -qf /usr/bin/javaws 2>/dev/null || echo 'origem desconhecida')) — não é usado; temos lançador próprio"
fi

echo "== Tela virtual =="
if command -v Xvfb >/dev/null; then
  ok "Xvfb: $(command -v Xvfb)"
elif command -v Xvnc >/dev/null; then
  ok "Xvnc: $(command -v Xvnc) (serve como tela virtual; permite ver por VNC em localhost)"
elif command -v weston >/dev/null && command -v Xwayland >/dev/null; then
  ok "weston + Xwayland: tela virtual headless (RHEL/OL 10, sem Xorg)"
else
  falta "nenhuma tela virtual. RHEL/OL 8-9: dnf install xorg-x11-server-Xvfb. RHEL/OL 10 (sem Xorg): dnf install weston xorg-x11-server-Xwayland"
fi
if command -v fc-list >/dev/null && [ "$(fc-list 2>/dev/null | wc -l)" -gt 0 ]; then
  ok "fontes: $(fc-list 2>/dev/null | wc -l) encontradas"
else
  falta "sem fontes para o Java desenhar texto (dnf install fontconfig dejavu-sans-fonts)"
fi
for lib in libXtst.so.6 libXrender.so.1 libXi.so.6 libfreetype.so.6; do
  if ldconfig -p 2>/dev/null | grep -q "$lib"; then ok "$lib"; else falta "$lib ausente (dnf install libXtst libXrender libXi freetype)"; fi
done
DISPLAY_RPA="${EBS_FORMS_DISPLAY:-:99}"
N="${DISPLAY_RPA#:}"; N="${N%%.*}"
if [ -e "/tmp/.X11-unix/X$N" ]; then aviso "display $DISPLAY_RPA já está em uso (ok se for de uma execução anterior)"; fi

echo "== Rede =="
HOME_URL="${EBS_FORMS_HOME_URL:-http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE}"
HOST="$(printf '%s' "$HOME_URL" | sed -E 's#^https?://([^/:]+).*#\1#')"
if curl -sS --noproxy '*' -m 10 -o /dev/null -w '%{http_code}' "http://$HOST/OA_JAVA/oracle/apps/fnd/jar/fndforms.jar" -r 0-0 2>/dev/null | grep -qE '^(200|206)$'; then
  ok "jars do Forms alcançáveis em http://$HOST/OA_JAVA/"
else
  falta "não alcancei http://$HOST/OA_JAVA/ (rede, proxy ou DNS)"
fi

echo "== Credenciais (cofre) =="
PY="$APP_DIR/venv/bin/python"; [ -x "$PY" ] || PY="${PORTAL_PYTHON:-$(command -v python3)}"
if "$PY" - <<'EOF'
import sys
sys.path.insert(0, ".")
from core.cofre import obter
u = obter("EBS_FORMS_USER", ""); s = obter("EBS_FORMS_PASS", "")
print(f"  usuário do robô: {'definido ('+u+')' if u else 'FALTA'}; senha: {'definida' if s else 'FALTA'}")
sys.exit(0 if (u and s) else 1)
EOF
then ok "credenciais no cofre"; else falta "defina: python3 scripts/cofre.py definir EBS_FORMS_USER  e  ... EBS_FORMS_PASS"; fi

echo "== Lançador =="
if [ -n "$JAVAC" ]; then
  if "$JAVAC" -Xlint:-removal -d "$DADOS/bin" integracoes/ebs_forms_java/LancadorForms.java 2>"$DADOS/logs/javac.log"; then
    ok "LancadorForms compilado em data/ebs_forms/bin"
  else
    falta "javac falhou — veja data/ebs_forms/logs/javac.log"
  fi
elif [ -f "$DADOS/bin/LancadorForms.class" ]; then
  ok "LancadorForms já compilado"
fi

echo
if [ "$FALTAS" -gt 0 ]; then
  echo "Pendências: $FALTAS. Resolva e rode de novo."
else
  echo "Tudo pronto para o RPA."
fi

if [ "${1:-}" = "testar" ]; then
  echo
  echo "== Teste de abertura (SSO → jnlp → Forms na tela virtual → captura) =="
  "$PY" - <<'EOF'
import sys, json
sys.path.insert(0, ".")
import integracoes.ebs_forms as f
def reg(m): print("   ", m, flush=True)
try:
    r = f.testar_abertura(reg)
    print(json.dumps({k: v for k, v in r.items() if k != "resultado"}, ensure_ascii=False, indent=2))
    print("Capturas em data/ebs_forms/capturas/ — abra a última e veja o que o Forms mostrou.")
except f.ErroForms as e:
    print("FALHOU:", e); sys.exit(1)
EOF
fi
