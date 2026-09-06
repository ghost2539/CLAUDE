#!/usr/bin/env bash
# Mostra, no bytecode do lançador Web Start do EBS (fndaol.jar), o trecho que
# valida o parâmetro clientBrowser e o que ele faz com a sessão. Só leitura.
# Uso: bash scripts/ebs_forms_inspecionar.sh [texto-a-procurar]
set -u
cd "$(dirname "$0")/.."
JAR="data/ebs_forms/jars/fndaol.jar"
[ -f "$JAR" ] || { echo "sem $JAR (rode o teste uma vez para baixar os jars)"; exit 1; }
ALVO="${1:-clientBrowser is not valid}"
SAIDA="data/ebs_forms/depuracao/JNLPAppletContext.javap"
mkdir -p data/ebs_forms/depuracao
JAVA_TOOL_OPTIONS= javap -c -p -constants -cp "$JAR" oracle.apps.fnd.formsClient.jnlp.JNLPAppletContext > "$SAIDA" 2>&1
echo "== métodos da classe"
grep -E '^  [a-z].*\(.*\);$' "$SAIDA"
echo
echo "== método que contém: $ALVO"
LINHA=$(grep -n "$ALVO" "$SAIDA" | head -1 | cut -d: -f1)
[ -n "$LINHA" ] || { echo "não achei '$ALVO' em $SAIDA"; exit 1; }
INICIO=$(head -n "$LINHA" "$SAIDA" | grep -nE '^  [a-z].*\(.*\);$' | tail -1 | cut -d: -f1)
FIM=$(tail -n +"$LINHA" "$SAIDA" | grep -nE '^  [a-z].*\(.*\);$' | head -1 | cut -d: -f1)
[ -n "$FIM" ] && FIM=$((LINHA + FIM - 2)) || FIM=$((LINHA + 80))
sed -n "${INICIO},${FIM}p" "$SAIDA" | grep -E 'Method|ldc|invoke|if|getstatic|String |^  [a-z]' | sed -E 's/^ +//' | cut -c1-140
echo
echo "(bytecode completo em $SAIDA)"
