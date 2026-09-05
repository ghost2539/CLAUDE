#!/usr/bin/env bash
# ============================================================================
#  Controle do Portal SPARE rodando como PROCESSO DE USUÁRIO (sem root).
#
#    ./deploy/portal.sh start | stop | restart | status | logs | atualizar
#
#  Sem systemd: o processo sobe com nohup, o PID fica em
#  ~/.local/state/portal-spare/portal.pid e o log em portal.log.
#  Quem tiver `systemctl --user` disponível pode usar a unit em
#  deploy/portal_spare.user.service, que é mais robusta a queda.
# ============================================================================
set -uo pipefail

APP_DIR="${PORTAL_APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENVFILE="${PORTAL_ENVFILE:-$HOME/.config/portal-spare/environment}"
STATE="${PORTAL_STATE_DIR:-$HOME/.local/state/portal-spare}"
PIDFILE="$STATE/portal.pid"
LOGFILE="$STATE/portal.log"
PY="$APP_DIR/venv/bin/python"

mkdir -p "$STATE"

rodando() {
    [ -f "$PIDFILE" ] || return 1
    local pid; pid="$(cat "$PIDFILE" 2>/dev/null)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

carregar_env() {
    if [ ! -r "$ENVFILE" ]; then
        echo "ERRO: arquivo de ambiente não encontrado: $ENVFILE"
        echo "      Rode deploy/instalar_usuario.sh ou crie o arquivo."
        exit 1
    fi
    # Permissão frouxa em arquivo com senha é problema, não detalhe.
    local modo; modo="$(stat -c '%a' "$ENVFILE" 2>/dev/null || echo '')"
    if [ -n "$modo" ] && [ "$modo" != "600" ]; then
        echo "AVISO: $ENVFILE está com permissão $modo — ajustando para 600."
        chmod 600 "$ENVFILE"
    fi
    set -a; . "$ENVFILE"; set +a
}

case "${1:-status}" in

start)
    if rodando; then echo "Já está rodando (PID $(cat "$PIDFILE"))."; exit 0; fi
    carregar_env
    [ -x "$PY" ] || { echo "ERRO: venv ausente em $APP_DIR/venv. Rode o instalador."; exit 1; }
    cd "$APP_DIR" || exit 1
    PORTA="${PORT:-8901}"
    nohup "$PY" -m uvicorn main:app \
        --host "${HOST:-0.0.0.0}" --port "$PORTA" --workers "${WORKERS:-1}" \
        >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 3
    if rodando; then
        echo "Portal no ar (PID $(cat "$PIDFILE")) — porta $PORTA"
        echo "  log: $LOGFILE"
    else
        echo "FALHOU. Últimas linhas do log:"; tail -n 25 "$LOGFILE"; exit 1
    fi
    ;;

stop)
    if ! rodando; then echo "Não está rodando."; rm -f "$PIDFILE"; exit 0; fi
    pid="$(cat "$PIDFILE")"
    kill "$pid" 2>/dev/null
    for _ in $(seq 1 20); do rodando || break; sleep 0.5; done
    rodando && { echo "Não encerrou; forçando."; kill -9 "$pid" 2>/dev/null; }
    rm -f "$PIDFILE"
    echo "Parado."
    ;;

restart)
    "$0" stop; "$0" start
    ;;

status)
    if rodando; then
        pid="$(cat "$PIDFILE")"
        echo "NO AR — PID $pid"
        ps -o pid,etime,rss,cmd -p "$pid" --no-headers 2>/dev/null
        PORTA="${PORT:-8901}"
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$PORTA/" 2>/dev/null)"
        echo "  resposta HTTP na porta $PORTA: ${code:-sem resposta}"
    else
        echo "PARADO."
    fi
    echo "  app:   $APP_DIR"
    echo "  env:   $ENVFILE"
    echo "  log:   $LOGFILE"
    ;;

logs)
    tail -n "${2:-80}" -f "$LOGFILE"
    ;;

atualizar)
    # git em rede corporativa: o proxy quebra o fetch, então sai do ambiente
    # só para este comando.
    cd "$APP_DIR" || exit 1
    echo "== Atualizando a partir do GitHub =="
    env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY \
        git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)" || {
        echo "ERRO no git pull. Nada foi alterado."; exit 1; }
    if [ -x "$PY" ]; then
        "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || \
            echo "AVISO: falha ao atualizar dependências."
    fi
    "$0" restart
    ;;

*)
    echo "Uso: $0 {start|stop|restart|status|logs|atualizar}"
    exit 1
    ;;
esac
