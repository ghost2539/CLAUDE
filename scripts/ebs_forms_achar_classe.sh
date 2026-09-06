#!/usr/bin/env bash
# Descobre ONDE mora uma classe que o cliente Forms do EBS pede e não acha.
# Só leitura; nada é instalado. Uso:
#   bash scripts/ebs_forms_achar_classe.sh oracle/apps/fnd/i18n/util/NLSUtil
set -u
cd "$(dirname "$0")/.."
CLASSE="${1:-oracle/apps/fnd/i18n/util/NLSUtil}"
CLASSE="${CLASSE//./\/}"; CLASSE="${CLASSE%.class}"
HOME_URL="${EBS_FORMS_HOME_URL:-http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp}"
HOST="$(printf '%s' "$HOME_URL" | sed -E 's#^https?://([^/:]+).*#\1#')"
BASE="http://$HOST/OA_JAVA"
TMP="data/ebs_forms/jars"
mkdir -p "$TMP"
C() { curl -sS --noproxy '*' -m 25 "$@"; }

echo "== 1. a classe é servida solta no codebase?"
COD="$(C -o /dev/null -w '%{http_code}' "$BASE/$CLASSE.class")"
echo "   $BASE/$CLASSE.class -> HTTP $COD"

echo
echo "== 2. lista oficial de jars (o frmservlet em modo applet publica 'archive')"
for U in "http://$HOST/forms/frmservlet?config=EBSPRD" "http://$HOST/forms/frmservlet"; do
  ARCH="$(C -A 'Mozilla/4.0 (compatible; MSIE 8.0; Windows NT 6.1)' "$U" \
        | tr ',' '\n' | grep -oE '[A-Za-z0-9_./-]+\.jar' | sort -u)"
  if [ -n "$ARCH" ]; then echo "   de $U:"; echo "$ARCH" | sed 's/^/     /'; break; fi
done
[ -n "${ARCH:-}" ] || echo "   (o frmservlet não devolveu lista de jars)"

echo
echo "== 3. procurando a classe nos jars do EBS"
CANDIDATOS="$(printf '%s\n' ${ARCH:-} \
  fndaol.jar fndforms.jar fndformsi18n.jar fndewt.jar fndswing.jar fndbalishare.jar \
  fndctx.jar fndutil.jar fndlist.jar fndi18n.jar fndnam.jar fndjewt.jar fndsoa.jar \
  fndtcf.jar fndxml.jar fndsecure.jar fndlookup.jar fndmedia.jar fndhelp.jar fnd.jar \
  | sed 's#.*/##' | sort -u)"
ACHOU=""
for J in $CANDIDATOS; do
  L="$TMP/$J"
  if [ ! -s "$L" ]; then
    S="$(C -o "$L" -w '%{http_code}' "$BASE/oracle/apps/fnd/jar/$J")"
    [ "$S" = "200" ] || { rm -f "$L"; continue; }
  fi
  if unzip -l "$L" 2>/dev/null | grep -q "$CLASSE.class"; then
    echo "   ✔ $J  contém $CLASSE"
    ACHOU="$ACHOU $J"
  fi
done
[ -n "$ACHOU" ] && echo && echo "   Use:  export EBS_FORMS_JARS_EXTRA=\"$(echo $ACHOU | tr ' ' ',')\"" \
                || echo "   ✘ não achei em nenhum jar candidato"
echo
echo "== 4. jars já baixados em $TMP"
ls -1 "$TMP" | tr '\n' ' '; echo
