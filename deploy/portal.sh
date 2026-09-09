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
# Interpretador: venv do projeto se existir, senão PORTAL_PYTHON, senão o
# python3 do sistema. Nem toda instalação tem venv — e sem esta escolha o
# start falhava com "venv ausente" mesmo com tudo instalado no sistema.
if [ -x "$APP_DIR/venv/bin/python" ]; then
    PY="$APP_DIR/venv/bin/python"
elif [ -n "${PORTAL_PYTHON:-}" ] && [ -x "$PORTAL_PYTHON" ]; then
    PY="$PORTAL_PYTHON"
else
    PY="$(command -v python3 || true)"
fi

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
    if [ ! -x "$PY" ]; then
        echo "ERRO: nenhum Python encontrado."
        echo "      Instale o python3, crie o venv (bash deploy/instalar_usuario.sh)"
        echo "      ou aponte um: PORTAL_PYTHON=/caminho/do/python $0 start"
        exit 1
    fi
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
        echo
        echo "FALHOU AO SUBIR. Últimas linhas do log:"
        echo "----------------------------------------------------------------"
        tail -n 30 "$LOGFILE"
        echo "----------------------------------------------------------------"
        echo
        echo "Para o diagnóstico completo (o que falta configurar):"
        echo "    $APP_DIR/venv/bin/python $APP_DIR/scripts/prevoo.py"
        echo "Log inteiro: $LOGFILE"
        rm -f "$PIDFILE"
        exit 1
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
    # Lê o ambiente só para saber a porta; sem ele o status mentiria.
    [ -r "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
    if rodando; then
        pid="$(cat "$PIDFILE")"
        echo "NO AR — PID $pid"
        ps -o pid,etime,rss,cmd -p "$pid" --no-headers 2>/dev/null
        PORTA="${PORT:-8901}"
        # --noproxy: num servidor com http_proxy no ambiente, o curl mandaria
        # a checagem de 127.0.0.1 para o proxy e reportaria falha falsa.
        code="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 5 \
                "http://127.0.0.1:$PORTA/" 2>/dev/null)"
        echo "  resposta HTTP na porta $PORTA: ${code:-sem resposta}"
    else
        echo "PARADO."
    fi
    echo "  app:    $APP_DIR"
    echo "  python: ${PY:-nenhum}"
    echo "  env:    $ENVFILE"
    echo "  log:    $LOGFILE"
    ;;

instalar-servico|servico)
    # Gera a unit de USUÁRIO já preenchida (caminhos e interpretador reais)
    # e instala em ~/.config/systemd/user. Sem root.
    [ -r "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
    PORTA="${PORT:-8901}"
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT="$UNIT_DIR/portal-spare.service"

    if [ ! -x "$PY" ]; then
        echo "ERRO: nenhum Python encontrado para o ExecStart."; exit 1
    fi
    mkdir -p "$UNIT_DIR"

    cat > "$UNIT" <<UNITEOF
# Portal de Operacoes SPARE — servico de usuario (sem root).
# Gerado por deploy/portal.sh instalar-servico em $(date '+%d/%m/%Y %H:%M').
[Unit]
Description=Portal de Operacoes SPARE
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENVFILE
ExecStart=$PY -m uvicorn main:app --host ${HOST:-0.0.0.0} --port $PORTA --workers ${WORKERS:-1}
Restart=on-failure
RestartSec=5
NoNewPrivileges=true

[Install]
WantedBy=default.target
UNITEOF
    chmod 644 "$UNIT"
    echo "Unit criada: $UNIT"
    echo "   python : $PY"
    echo "   porta  : $PORTA"
    echo

    if ! systemctl --user show-environment >/dev/null 2>&1; then
        echo "AVISO: systemd de usuario indisponivel nesta sessao."
        echo "   Siga com o modo nohup:  $0 start"
        exit 1
    fi

    # O nohup e o systemd brigam pela porta; para o que estiver de pe.
    if rodando; then
        echo "Parando a instancia em nohup antes de ativar o servico..."
        "$0" stop >/dev/null 2>&1
    fi

    systemctl --user daemon-reload
    systemctl --user enable --now portal-spare.service && {
        sleep 3
        systemctl --user --no-pager --lines=8 status portal-spare.service || true
    }
    echo
    echo "Comandos do dia a dia:"
    echo "   systemctl --user status|restart|stop portal-spare"
    echo "   journalctl --user -u portal-spare -f"
    echo
    echo "IMPORTANTE: para o servico continuar no ar depois do logout, alguem"
    echo "com root precisa habilitar UMA VEZ:"
    echo "   sudo loginctl enable-linger \$USER"
    echo "Sem isso, o systemd encerra o servico quando sua sessao terminar."
    ;;

endereco|onde|url)
    [ -r "$ENVFILE" ] && { set -a; . "$ENVFILE"; set +a; }
    PORTA="${PORT:-8901}"
    BIND="${HOST:-0.0.0.0}"

    echo "== Onde o portal atende =="
    if rodando; then
        echo "   estado : NO AR (PID $(cat "$PIDFILE"))"
    else
        echo "   estado : PARADO — suba com: $0 start"
    fi
    echo "   bind   : $BIND:$PORTA"
    if [ "$BIND" = "127.0.0.1" ] || [ "$BIND" = "localhost" ]; then
        echo
        echo "   ATENÇÃO: preso em $BIND — só responde DENTRO do servidor."
        echo "   Para acessar de outra máquina, no $ENVFILE:"
        echo "       HOST=0.0.0.0"
        echo "   e depois: $0 restart"
    fi

    echo
    echo "-- Do próprio servidor"
    echo "   http://127.0.0.1:$PORTA"
    code="$(curl -s -o /dev/null -w '%{http_code}' --noproxy '*' --max-time 5 \
            "http://127.0.0.1:$PORTA/" 2>/dev/null)"
    if [ "$code" = "200" ]; then
        echo "      resposta: 200 — o portal está atendendo"
    else
        echo "      resposta: ${code:-sem resposta}"
    fi

    echo
    echo "-- Da rede (tente estes no navegador)"
    ips="$(hostname -I 2>/dev/null)"
    if [ -z "$ips" ]; then
        ips="$(ip -4 -o addr show scope global 2>/dev/null | awk '{split($4,a,"/"); print a[1]}')"
    fi
    if [ -z "$ips" ]; then
        echo "   (não consegui descobrir o IP — use 'ip a' e monte a URL na mão)"
    else
        for ip in $ips; do echo "   http://$ip:$PORTA"; done
    fi
    nome="$(hostname -f 2>/dev/null || hostname 2>/dev/null)"
    [ -n "$nome" ] && echo "   http://$nome:$PORTA"

    echo
    echo "-- Quem está escutando na porta $PORTA"
    if command -v ss >/dev/null 2>&1; then
        LINHAS="$(ss -tlnp 2>/dev/null | grep ":$PORTA " || true)"
    else
        LINHAS="$(netstat -tlnp 2>/dev/null | grep ":$PORTA " || true)"
    fi
    if [ -n "$LINHAS" ]; then
        echo "$LINHAS" | sed 's/^/   /'
    elif [ "$code" = "200" ]; then
        # ss/netstat sem privilégio não enxerga o socket de outro processo;
        # como o HTTP respondeu, afirmar "ninguém escutando" seria mentira.
        echo "   (ss/netstat não listou — provavelmente sem privilégio para ver"
        echo "    o socket; o HTTP respondeu 200, então está escutando)"
    else
        echo "   ninguém — o processo não está escutando nessa porta"
    fi

    echo
    echo "-- Firewall"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi "^Status: active"; then
        echo "   ufw ATIVO:"
        ufw status 2>/dev/null | sed -n '1,12p' | sed 's/^/     /'
        echo "     liberar:  sudo ufw allow $PORTA/tcp"
    elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
        echo "   firewalld ATIVO — portas: $(firewall-cmd --list-ports 2>/dev/null)"
        echo "     liberar:  sudo firewall-cmd --add-port=$PORTA/tcp --permanent && sudo firewall-cmd --reload"
    elif command -v iptables >/dev/null 2>&1 && [ "$(iptables -S 2>/dev/null | wc -l)" -gt 3 ]; then
        echo "   iptables com regras — confira com: sudo iptables -L -n"
    else
        echo "   nenhum firewall local aparente"
    fi

    echo
    echo "Se responde em 127.0.0.1 mas não pela rede, o problema NÃO é o portal:"
    echo "é firewall local, firewall de rede ou o endereço usado no navegador."
    ;;

logs)
    # Acompanha em tempo real (Ctrl+C para sair). Para só ver o que já
    # aconteceu, use "erros" — este aqui fica preso na tela.
    tail -n "${2:-80}" -f "$LOGFILE"
    ;;

erros|ultimolog)
    # Mostra e SAI: é o que se quer quando o portal não subiu.
    if [ ! -f "$LOGFILE" ]; then
        echo "Sem log ainda em $LOGFILE — o portal nunca chegou a subir."
        echo "Rode:  $0 start"
        exit 1
    fi
    N="${2:-60}"
    echo "== Últimas $N linhas de $LOGFILE =="
    tail -n "$N" "$LOGFILE"
    echo
    echo "== Erros e tracebacks no log =="
    if grep -nE "Traceback|Error|ERROR|Exception|NÃO carregado|FALHOU" "$LOGFILE" \
       | tail -n 20 | sed 's/^/   /'; then :; fi
    grep -qE "Traceback|Error|ERROR|Exception" "$LOGFILE" \
        || echo "   (nenhum)"
    ;;

atualizar)
    # git em rede corporativa: o proxy quebra o fetch, então sai do ambiente
    # só para este comando.
    cd "$APP_DIR" || exit 1
    echo "== Atualizando a partir do GitHub =="
    env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY \
        git pull --ff-only origin "$(git rev-parse --abbrev-ref HEAD)" || {
        echo "ERRO no git pull. Nada foi alterado."; exit 1; }
    if [ -x "$APP_DIR/venv/bin/pip" ]; then
        "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt" || \
            echo "AVISO: falha ao atualizar dependências."
    else
        echo "   (sem venv — dependências do sistema, nada a atualizar)"
    fi
    "$0" restart
    ;;

*)
    echo "Uso: $0 {start|stop|restart|status|endereco|logs|erros|atualizar|instalar-servico}"
    exit 1
    ;;
esac
