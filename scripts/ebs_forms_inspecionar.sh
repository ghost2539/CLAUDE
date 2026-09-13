#!/usr/bin/env bash
# Mostra o bytecode de um método do lançador Web Start do EBS (fndaol.jar).
# Só leitura. Uso:
#   bash scripts/ebs_forms_inspecionar.sh <metodo|texto> [classe]
# Ex.: bash scripts/ebs_forms_inspecionar.sh 'createSession('
#      bash scripts/ebs_forms_inspecionar.sh 'isJavaWebStartEnabled(' oracle.apps.fnd.formsClient.FNDApplet
set -u
cd "$(dirname "$0")/.."
JAR="data/ebs_forms/jars/fndaol.jar"
[ -f "$JAR" ] || { echo "sem $JAR (rode o teste uma vez para baixar os jars)"; exit 1; }
ALVO="${1:-createSession(}"
CLASSE="${2:-oracle.apps.fnd.formsClient.jnlp.JNLPAppletContext}"
SAIDA="data/ebs_forms/depuracao/$(basename "$CLASSE").javap"
mkdir -p data/ebs_forms/depuracao
[ -s "$SAIDA" ] || JAVA_TOOL_OPTIONS= javap -c -p -constants -cp "$JAR:data/ebs_forms/jars/fndforms.jar" "$CLASSE" > "$SAIDA" 2>&1
# Primeiro tenta como cabeçalho de método; senão, como texto dentro de um método.
LINHA=$(grep -nE "^  .*[ .]${ALVO//(/\\(}" "$SAIDA" | grep -E '\);$' | head -1 | cut -d: -f1)
[ -n "$LINHA" ] || LINHA=$(grep -nF "$ALVO" "$SAIDA" | head -1 | cut -d: -f1)
[ -n "$LINHA" ] || { echo "não achei '$ALVO' em $CLASSE"; grep -E '^  [a-z].*\(.*\);$' "$SAIDA"; exit 1; }
INICIO=$(head -n "$LINHA" "$SAIDA" | grep -nE '^  [a-z].*\(.*\);$' | tail -1 | cut -d: -f1)
FIM=$(tail -n +"$((LINHA+1))" "$SAIDA" | grep -nE '^  [a-z].*\(.*\);$' | head -1 | cut -d: -f1)
[ -n "$FIM" ] && FIM=$((LINHA + FIM - 1)) || FIM=$((LINHA + 150))
echo "== $CLASSE :: linhas $INICIO-$FIM"
sed -n "${INICIO},${FIM}p" "$SAIDA" | grep -E 'Method|ldc|invoke|if|getstatic|getfield|String |^  [a-z]|athrow|return' | sed -E 's/^ +//; s#// (Method|InterfaceMethod|Field|String|class) #// #' | cut -c1-150
