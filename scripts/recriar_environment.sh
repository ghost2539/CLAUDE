#!/usr/bin/env bash
# Recria o arquivo de ambiente do serviço a partir do modelo do repositório,
# SEM perder o que já está lá.
#
#     bash scripts/recriar_environment.sh              # mostra o que faria
#     bash scripts/recriar_environment.sh --aplicar    # grava
#
# Por que preservar: o arquivo em produção costuma ter linhas que o modelo
# não tem — credenciais que o time injetou, ajustes de máquina. Sobrescrever
# pelo modelo puro apagaria o que está funcionando, e o sintoma só apareceria
# no próximo login de alguém.
#
# Nenhum valor é impresso. O script fala por nomes de variável.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELO="$RAIZ/deploy/environment.servidor-novo"
DESTINO="${PORTAL_ENV_FILE:-/var/www/vcreports/portal-spare/data/environment}"
APLICAR="${1:-}"

[ -f "$MODELO" ] || { echo "Modelo não encontrado: $MODELO" >&2; exit 1; }

# Nomes definidos num arquivo de ambiente, uma por linha.
nomes() {
    [ -f "$1" ] || return 0
    sed -n 's/^[[:space:]]*\(export[[:space:]]\+\)\?\([A-Za-z_][A-Za-z0-9_]*\)=.*/\2/p' "$1" | sort -u
}

echo "Modelo : $MODELO"
echo "Destino: $DESTINO"
echo

if [ ! -f "$DESTINO" ]; then
    echo "O destino não existe — será criado a partir do modelo, sem nada a preservar."
    EXTRAS=""
else
    EXTRAS="$(comm -23 <(nomes "$DESTINO") <(nomes "$MODELO") || true)"
    if [ -n "$EXTRAS" ]; then
        echo "Variáveis que existem hoje e NÃO estão no modelo (serão preservadas):"
        echo "$EXTRAS" | sed 's/^/  - /'
        # Um nome que CONTÉM um nome conhecido quase sempre é erro de
        # digitação — foi assim que SAPP_BASE_PATH derrubou a tela inteira e
        # ninguém viu, porque nada reclama de variável que ninguém lê.
        SUSPEITAS=""
        while IFS= read -r extra; do
            [ -n "$extra" ] || continue
            while IFS= read -r conhecida; do
                [ -n "$conhecida" ] || continue
                case "$extra" in
                    *"$conhecida"*) SUSPEITAS="$SUSPEITAS  ! $extra  (parece $conhecida)"$'\n' ;;
                esac
            done < <(nomes "$MODELO")
        done <<< "$EXTRAS"
        if [ -n "$SUSPEITAS" ]; then
            echo
            echo "ATENÇÃO — nomes que parecem erro de digitação. O portal não lê"
            echo "nenhum deles, e o script vai preservá-los como estão:"
            printf '%s' "$SUSPEITAS"
            echo "Corrija no arquivo gravado, ou apague a linha."
        fi
    else
        echo "Nenhuma variável exclusiva do arquivo atual — o modelo cobre tudo."
    fi
    PERDIDAS="$(comm -13 <(nomes "$DESTINO") <(nomes "$MODELO") || true)"
    if [ -n "$PERDIDAS" ]; then
        echo
        echo "Variáveis que o modelo traz e hoje faltam (serão acrescentadas):"
        echo "$PERDIDAS" | sed 's/^/  + /'
    fi
fi

NOVO="$(mktemp)"
trap 'rm -f "$NOVO"' EXIT
cat "$MODELO" > "$NOVO"

if [ -n "${EXTRAS:-}" ]; then
    {
        echo
        echo "# ── Preservado do arquivo anterior em $(date '+%d/%m/%Y %H:%M') ─────────"
        echo "# Linhas que não existem no modelo. Reveja: credencial aqui é exceção"
        echo "# temporária, válida só enquanto o cofre não responde ao serviço."
        while IFS= read -r nome; do
            [ -n "$nome" ] || continue
            grep -E "^[[:space:]]*(export[[:space:]]+)?${nome}=" "$DESTINO" | tail -1
        done <<< "$EXTRAS"
    } >> "$NOVO"
fi

echo
if [ "$APLICAR" != "--aplicar" ]; then
    echo "Nada foi gravado. Para aplicar:"
    echo "  bash scripts/recriar_environment.sh --aplicar"
    echo
    echo "Prévia dos NOMES do arquivo que seria gravado:"
    nomes "$NOVO" | sed 's/^/  /'
    exit 0
fi

if [ -f "$DESTINO" ]; then
    COPIA="${DESTINO}.bak-$(date '+%Y%m%d-%H%M%S')"
    cp -p "$DESTINO" "$COPIA"
    chmod 600 "$COPIA"
    echo "Cópia do anterior: $COPIA"
fi

cat "$NOVO" > "$DESTINO"
chmod 600 "$DESTINO"
echo "Gravado: $DESTINO"
echo
echo "Agora reinicie e confira:"
echo "  sudo systemctl restart portal-spare"
echo "  curl -s http://127.0.0.1:8901/ | grep meta"
