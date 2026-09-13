#!/usr/bin/env bash
# ============================================================================
#  Ambiente de TESTES do Portal SPARE, ao lado da produção, no mesmo servidor.
#
#    sudo bash deploy/instalar_testes.sh            # como root (produção via systemd)
#    bash deploy/instalar_testes.sh                 # sem root (produção do usuário)
#
#  O que faz, nesta ordem:
#    1. clona (ou atualiza) o código do branch de testes em TEST_DIR;
#    2. cria o venv e instala as dependências;
#    3. COPIA os bancos da produção para o ambiente de testes:
#         - Postgres: pg_dump | pg_restore para um banco novo "<nome>_testes";
#         - SQLite (data/db/*.db): cópia consistente com sqlite3 .backup;
#         - uploads, branding e referências;
#    4. gera o arquivo de ambiente do teste a partir do de produção, trocando
#       porta, bancos e ligando AMBIENTE=testes (agendador de automações e
#       e-mails desligados);
#    5. cria o serviço "portal-spare-testes" e sobe na porta 8999.
#
#  Nada é escrito nos bancos nem nos arquivos da produção: a produção só é
#  LIDA (dump e cópia). Rodar de novo atualiza o código; os bancos só são
#  recopiados com --recopiar-bancos.
#
#  Variáveis (todas opcionais):
#    PROD_DIR      pasta da produção            (padrão: /opt/portal-spare-v2, ou a pasta deste repo)
#    PROD_ENVFILE  ambiente da produção         (padrão: /etc/portal_operacoes_spare/environment ou ~/.config/portal-spare/environment)
#    TEST_DIR      pasta do teste               (padrão: /opt/portal-spare-testes com root; ~/portal-spare-testes sem root)
#    TEST_PORT     porta do teste               (padrão: 8999)
#    TEST_BRANCH   branch a testar              (padrão: desenvolvimento)
#    TEST_REPO     URL do git                   (padrão: o remoto "origin" da produção)
#    TEST_ENVDIR   pasta do arquivo de ambiente (padrão: /etc/portal_operacoes_spare_testes ou ~/.config/portal-spare-testes)
# ============================================================================
set -uo pipefail

RECOPIAR=0
for a in "$@"; do
    case "$a" in
        --recopiar-bancos) RECOPIAR=1 ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "Opção desconhecida: $a"; exit 1 ;;
    esac
done

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$(id -u)" -eq 0 ]; then ROOT=1; else ROOT=0; fi

PROD_DIR="${PROD_DIR:-}"
if [ -z "$PROD_DIR" ]; then
    if [ -d /opt/portal-spare-v2 ]; then PROD_DIR=/opt/portal-spare-v2; else PROD_DIR="$AQUI"; fi
fi
PROD_ENVFILE="${PROD_ENVFILE:-}"
if [ -z "$PROD_ENVFILE" ]; then
    if [ -r /etc/portal_operacoes_spare/environment ]; then PROD_ENVFILE=/etc/portal_operacoes_spare/environment
    elif [ -r "$HOME/.config/portal-spare/environment" ]; then PROD_ENVFILE="$HOME/.config/portal-spare/environment"
    fi
fi
# systemd só conta se estiver mesmo no comando (contêiner e WSL têm o
# binário sem o init).
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then SYSTEMD=1; else SYSTEMD=0; fi
if [ "$ROOT" -eq 1 ]; then
    TEST_DIR="${TEST_DIR:-/opt/portal-spare-testes}"
    TEST_ENVDIR="${TEST_ENVDIR:-/etc/portal_operacoes_spare_testes}"
else
    TEST_DIR="${TEST_DIR:-$HOME/portal-spare-testes}"
    TEST_ENVDIR="${TEST_ENVDIR:-$HOME/.config/portal-spare-testes}"
fi
SERVICO=portal-spare-testes
TEST_ENVFILE="$TEST_ENVDIR/environment"
TEST_PORT="${TEST_PORT:-8999}"
TEST_BRANCH="${TEST_BRANCH:-desenvolvimento}"
TEST_REPO="${TEST_REPO:-}"
STATE="${TEST_STATE_DIR:-$HOME/.local/state/portal-spare-testes}"

echo "== Ambiente de TESTES do Portal SPARE =="
echo "   produção:  $PROD_DIR"
echo "   env prod:  ${PROD_ENVFILE:-(não encontrado)}"
echo "   teste:     $TEST_DIR  (porta $TEST_PORT, branch $TEST_BRANCH)"
echo "   env teste: $TEST_ENVFILE"

[ -d "$PROD_DIR" ] || { echo "ERRO: pasta da produção não existe: $PROD_DIR"; exit 1; }
[ -n "$PROD_ENVFILE" ] && [ -r "$PROD_ENVFILE" ] || { echo "ERRO: arquivo de ambiente da produção não legível. Informe PROD_ENVFILE=..."; exit 1; }
if [ "$TEST_DIR" = "$PROD_DIR" ]; then echo "ERRO: TEST_DIR é a própria produção."; exit 1; fi
if command -v ss >/dev/null 2>&1 && ss -tln 2>/dev/null | grep -q ":${TEST_PORT} "; then
    if ! systemctl is-active --quiet "$SERVICO" 2>/dev/null && ! systemctl --user is-active --quiet "$SERVICO" 2>/dev/null; then
        echo "ERRO: a porta $TEST_PORT já está em uso por outro processo."; exit 1
    fi
fi

# Sem proxy no git (rede corporativa quebra o fetch por ele).
git_sem_proxy() { env -u https_proxy -u http_proxy -u HTTPS_PROXY -u HTTP_PROXY git "$@"; }

# Lê um arquivo de ambiente no formato do systemd (KEY=VALUE, aspas
# opcionais) e devolve "export KEY='VALUE'" por linha. Não se usa `source`:
# uma senha com | ; & ou $ viraria comando no bash e a variável sumiria.
env_exports() {
    python3 - "$1" <<'PY'
import shlex, sys
for linha in open(sys.argv[1], encoding="utf-8", errors="replace"):
    linha = linha.strip()
    if not linha or linha.startswith("#") or "=" not in linha:
        continue
    k, _, v = linha.partition("=")
    k = k.strip().removeprefix("export ").strip()
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    if k.replace("_", "").isalnum():
        print(f"export {k}={shlex.quote(v)}")
PY
}

# ── 1. Código ──────────────────────────────────────────────────────────────
if [ -z "$TEST_REPO" ]; then
    TEST_REPO="$(git -C "$PROD_DIR" remote get-url origin 2>/dev/null || true)"
fi
if [ -d "$TEST_DIR/.git" ]; then
    echo "-- Atualizando o código ($TEST_BRANCH)"
    git_sem_proxy -C "$TEST_DIR" fetch origin "$TEST_BRANCH" || { echo "ERRO no git fetch."; exit 1; }
    git_sem_proxy -C "$TEST_DIR" checkout -q "$TEST_BRANCH" && git_sem_proxy -C "$TEST_DIR" reset -q --hard "origin/$TEST_BRANCH" \
        || { echo "ERRO ao posicionar o branch."; exit 1; }
else
    [ -n "$TEST_REPO" ] || { echo "ERRO: não sei de onde clonar. Informe TEST_REPO=https://..."; exit 1; }
    echo "-- Clonando $TEST_REPO ($TEST_BRANCH)"
    git_sem_proxy clone -q --branch "$TEST_BRANCH" "$TEST_REPO" "$TEST_DIR" || {
        echo "AVISO: clone remoto falhou; clonando a partir da pasta da produção."
        git clone -q "$PROD_DIR" "$TEST_DIR" && git_sem_proxy -C "$TEST_DIR" fetch origin "$TEST_BRANCH" \
            && git -C "$TEST_DIR" checkout -q -B "$TEST_BRANCH" "origin/$TEST_BRANCH" || { echo "ERRO ao clonar."; exit 1; }
    }
fi
echo "   commit: $(git -C "$TEST_DIR" log --oneline -1)"

# ── 2. Ferramentas de sistema pelo dnf (Oracle Linux / RHEL) ───────────────
# O que vem do repositório do sistema, vem do dnf: Python, pip, sqlite3,
# cliente do Postgres e git. As bibliotecas Python do portal NÃO vêm de lá:
# o repositório traz SQLAlchemy 1.x e não traz FastAPI, Pydantic 2, psycopg 3
# nem oracledb — o portal exige SQLAlchemy 2 e essas demais.
if [ "$ROOT" -eq 1 ] && command -v dnf >/dev/null 2>&1; then
    FALTAM=""
    command -v python3 >/dev/null 2>&1 || FALTAM="$FALTAM python3"
    python3 -c 'import venv, ensurepip' 2>/dev/null || FALTAM="$FALTAM python3-pip"
    command -v sqlite3 >/dev/null 2>&1 || FALTAM="$FALTAM sqlite"
    command -v git >/dev/null 2>&1 || FALTAM="$FALTAM git"
    case "$(eval "$(env_exports "$PROD_ENVFILE")"; printf '%s' "${DATABASE_URL:-}")" in
        postgres*) command -v pg_dump >/dev/null 2>&1 || FALTAM="$FALTAM postgresql" ;;
    esac
    if [ -n "$FALTAM" ]; then
        echo "-- dnf: instalando$FALTAM"
        dnf install -y -q $FALTAM || echo "AVISO: dnf não conseguiu instalar:$FALTAM (o script segue; pode faltar algo adiante)."
    else
        echo "-- dnf: ferramentas de sistema já presentes"
    fi
fi

command -v python3 >/dev/null 2>&1 || { echo "ERRO: python3 não encontrado (dnf install python3)."; exit 1; }

# Proxy: o pip não enxerga o do dnf/yum sozinho. Se não vier do ambiente,
# usa o que o servidor já tem configurado para o dnf.
if [ -z "${https_proxy:-}${HTTPS_PROXY:-}" ]; then
    PROXY_DNF="$(grep -hE '^\s*proxy\s*=' /etc/dnf/dnf.conf /etc/yum.conf 2>/dev/null | head -1 | sed -E 's/^\s*proxy\s*=\s*//; s/\s+$//')"
    if [ -n "$PROXY_DNF" ] && [ "$PROXY_DNF" != "_none_" ]; then
        PU="$(grep -hE '^\s*proxy_username\s*=' /etc/dnf/dnf.conf /etc/yum.conf 2>/dev/null | head -1 | sed -E 's/^[^=]*=\s*//')"
        PP="$(grep -hE '^\s*proxy_password\s*=' /etc/dnf/dnf.conf /etc/yum.conf 2>/dev/null | head -1 | sed -E 's/^[^=]*=\s*//')"
        if [ -n "$PU" ]; then PROXY_DNF="$(printf '%s' "$PROXY_DNF" | sed -E "s#^(https?://)#\1$PU:$PP@#")"; fi
        export https_proxy="$PROXY_DNF" http_proxy="$PROXY_DNF" HTTPS_PROXY="$PROXY_DNF" HTTP_PROXY="$PROXY_DNF"
        echo "-- Proxy do dnf aplicado ao pip: $(printf '%s' "$PROXY_DNF" | sed -E 's#//[^@]*@#//***@#')"
    fi
fi

mkdir -p "$STATE"
PROD_DATABASE_URL_CEDO="$(eval "$(env_exports "$PROD_ENVFILE")"; printf '%s' "${DATABASE_URL:-}")"
PY_TESTE="$TEST_DIR/venv/bin/python"
VENV_OK=0
if [ -x "$PY_TESTE" ] && "$PY_TESTE" -c 'import fastapi, sqlalchemy, pandas, openpyxl, uvicorn' 2>/dev/null; then
    VENV_OK=1
fi

if [ "$VENV_OK" -eq 0 ]; then
    if [ ! -x "$PY_TESTE" ]; then
        echo "-- Criando o venv"
        python3 -m venv "$TEST_DIR/venv" || { echo "ERRO: falha ao criar o venv (dnf install python3-venv / python3-pip?)."; exit 1; }
    fi
    echo "-- Instalando dependências pelo pip (até 3 min; sem saída para o PyPI, cai para o venv da produção)"
    if timeout 180 "$PY_TESTE" -m pip install -q --timeout 25 --retries 1 -r "$TEST_DIR/requirements.txt" 2>"$STATE/pip.err"; then
        VENV_OK=1
    else
        echo "   pip falhou ($(tail -1 "$STATE/pip.err" 2>/dev/null | cut -c1-120))"
    fi
fi

# O driver que a URL da produção pede tem de existir no venv do teste.
DRIVER_PG=""
case "$PROD_DATABASE_URL_CEDO" in
    postgresql+psycopg2://*|postgres://*|postgresql://*) DRIVER_PG=psycopg2 ;;
    postgresql+psycopg://*) DRIVER_PG=psycopg ;;
esac
if [ "$VENV_OK" -eq 1 ] && [ -n "$DRIVER_PG" ] && ! "$PY_TESTE" -c "import $DRIVER_PG" 2>/dev/null; then
    echo "-- Driver $DRIVER_PG ausente no venv; instalando"
    PACOTE="$DRIVER_PG"; [ "$DRIVER_PG" = "psycopg2" ] && PACOTE="psycopg2-binary"; [ "$DRIVER_PG" = "psycopg" ] && PACOTE="psycopg[binary]"
    timeout 120 "$PY_TESTE" -m pip install -q --timeout 25 --retries 1 "$PACOTE" 2>/dev/null \
        && "$PY_TESTE" -c "import $DRIVER_PG" 2>/dev/null || { echo "   não deu pelo pip; usando o venv da produção"; VENV_OK=0; }
fi

if [ "$VENV_OK" -eq 0 ]; then
    PY_PROD="$PROD_DIR/venv/bin/python"
    [ -x "$PY_PROD" ] || { echo "ERRO: sem rede para o pip e sem venv em $PROD_DIR/venv para copiar."; exit 1; }
    echo "-- Copiando o venv da produção (mesmo Python, mesmo servidor)"
    rm -rf "$TEST_DIR/venv"
    cp -a "$PROD_DIR/venv" "$TEST_DIR/venv" || { echo "ERRO ao copiar o venv."; exit 1; }
    # Os executáveis do venv trazem o caminho antigo no cabeçalho; um "pip"
    # esquecido instalaria na produção. Reescreve tudo para a pasta nova.
    for f in "$TEST_DIR"/venv/bin/*; do
        [ -f "$f" ] && [ ! -L "$f" ] || continue
        head -c 2 "$f" 2>/dev/null | grep -q '#!' || continue
        sed -i "1s#^\#!.*/venv/bin/python[0-9.]*#\#!$TEST_DIR/venv/bin/python#" "$f"
    done
    grep -rlZ "$PROD_DIR/venv" "$TEST_DIR/venv/bin" 2>/dev/null | xargs -0 -r sed -i "s#$PROD_DIR/venv#$TEST_DIR/venv#g"
    # Sem rede o pip já falhou uma vez; não se tenta de novo. Requisito
    # novo que a produção ainda não tenha fica de fora, e o portal sobe sem
    # o módulo que depender dele (o log de boot diz qual).
    FALTANDO="$(cd "$TEST_DIR" && "$PY_TESTE" - <<'PY' 2>/dev/null
import re, importlib.metadata as m
nomes = []
for l in open("requirements.txt", encoding="utf-8"):
    l = l.split("#")[0].strip()
    if not l: continue
    n = re.split(r"[<>=!\[;]", l)[0].strip()
    try: m.version(n)
    except m.PackageNotFoundError: nomes.append(n)
print(" ".join(nomes))
PY
)"
    [ -n "$FALTANDO" ] && echo "   AVISO: sem rede, não instalados (a produção não os tem): $FALTANDO"
    "$PY_TESTE" -c 'import fastapi, sqlalchemy, pandas, openpyxl, uvicorn' || { echo "ERRO: venv copiado não carrega as bibliotecas básicas."; exit 1; }
    if [ -n "$DRIVER_PG" ] && ! "$PY_TESTE" -c "import $DRIVER_PG" 2>/dev/null; then
        echo "ERRO: o venv da produção não tem o driver $DRIVER_PG que a URL do banco pede."; exit 1
    fi
fi
echo "   Python do teste: $("$PY_TESTE" -c 'import sys; print(sys.version.split()[0])') em $TEST_DIR/venv"

# ── 3. Bancos: cópia da produção ──────────────────────────────────────────
mkdir -p "$TEST_DIR/data/db" "$TEST_DIR/data/uploads" "$STATE" "$TEST_ENVDIR"
chmod 700 "$TEST_ENVDIR"

# Lê a produção num subshell: nada vaza para o ambiente do teste.
PROD_DATABASE_URL="$(eval "$(env_exports "$PROD_ENVFILE")"; printf '%s' "${DATABASE_URL:-}")"

BANCOS_JA_COPIADOS=0
[ -s "$TEST_DIR/data/db/.copiado_de_producao" ] && BANCOS_JA_COPIADOS=1
TEST_DATABASE_URL=""

url_pg() { printf '%s' "$1" | sed -E 's|^postgres(ql)?\+[a-z0-9_]+://|postgresql://|; s|^postgres://|postgresql://|'; }

# A URL do banco do teste deriva da de produção: SQLite vira o arquivo da
# pasta do teste; Postgres vira o banco "<nome>_testes" no mesmo servidor.
case "$PROD_DATABASE_URL" in
    sqlite*)
        TEST_DATABASE_URL="sqlite:///$TEST_DIR/data/db/portal.db" ;;
    postgres*)
        # Decompõe a URL depois do ÚLTIMO "@": senha com "/", "?" ou "@"
        # não pode confundir o nome do banco.
        PG_URL="$(url_pg "$PROD_DATABASE_URL")"
        eval "$(PROD_URL="$PROD_DATABASE_URL" PG_URL="$PG_URL" python3 - <<'PY'
import os, shlex
u, pg = os.environ["PROD_URL"], os.environ["PG_URL"]
def partes(url):
    cred, _, resto = url.rpartition("@")          # tudo antes do último @ é credencial
    caminho, _, query = resto.partition("?")      # host:porta/banco ? opções
    host, _, banco = caminho.partition("/")
    return cred, host, banco, query
cred, host, banco, query = partes(u)
teste = banco + "_testes"
novo = f"{cred}@{host}/{teste}" + (f"?{query}" if query else "")
credp, hostp, _, _ = partes(pg)
usuario = cred.split("://", 1)[-1].split(":", 1)[0]
print("PG_NOME=" + shlex.quote(banco))
print("PG_TESTE=" + shlex.quote(teste))
print("PG_USUARIO=" + shlex.quote(usuario))
print("PG_SERVIDOR=" + shlex.quote(f"{credp}@{hostp}"))
print("TEST_DATABASE_URL=" + shlex.quote(novo))
PY
)" ;;
    *)
        echo "ERRO: DATABASE_URL da produção não reconhecido: ${PROD_DATABASE_URL:-(vazio)}"; exit 1 ;;
esac
mascarar() { printf '%s' "$1" | sed -E 's#(://[^:/@]+):.*@#\1:***@#'; }
if [ -z "$TEST_DATABASE_URL" ]; then
    echo "ERRO: não consegui derivar a URL do banco de teste a partir de $(mascarar "$PROD_DATABASE_URL")."; exit 1
fi
if [ "$TEST_DATABASE_URL" = "$PROD_DATABASE_URL" ]; then
    echo "ERRO: a URL do banco de teste ficou igual à de produção ($(mascarar "$PROD_DATABASE_URL")). Abortando."; exit 1
fi
echo "   banco do teste: $(mascarar "$TEST_DATABASE_URL")"

if [ "$BANCOS_JA_COPIADOS" -eq 1 ] && [ "$RECOPIAR" -eq 0 ]; then
    echo "-- Bancos já copiados antes (use --recopiar-bancos para refazer a cópia)"
else
    echo "-- Copiando os bancos da produção (somente leitura na produção)"
    case "$PROD_DATABASE_URL" in
        sqlite*)
            echo "   portal: SQLite"
            ;;
        postgres*)
            echo "   portal: Postgres $PG_NOME → $PG_TESTE"
            command -v pg_dump >/dev/null 2>&1 && command -v pg_restore >/dev/null 2>&1 && command -v psql >/dev/null 2>&1 \
                || { echo "ERRO: pg_dump/pg_restore/psql não instalados (postgresql-client)."; exit 1; }
            DUMP="$STATE/portal_producao.dump"
            pg_dump -Fc --no-owner --no-acl --dbname="$PG_URL" -f "$DUMP" || { echo "ERRO no pg_dump da produção."; exit 1; }
            # Cria o banco como o usuário `postgres` do sistema (entra sem senha
            # pelo socket): o usuário do portal não tem CREATEDB e, pelo
            # pg_hba, só entra com senha no banco de produção.
            PSQL_ADMIN=""
            if [ "$ROOT" -eq 1 ] && id postgres >/dev/null 2>&1 \
               && su -s /bin/sh postgres -c "psql -tAc 'select 1'" >/dev/null 2>&1; then
                PSQL_ADMIN="su -s /bin/sh postgres -c"
            fi
            if [ -n "$PSQL_ADMIN" ]; then
                $PSQL_ADMIN "psql -v ON_ERROR_STOP=1 -q -c \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_TESTE' AND pid<>pg_backend_pid();\"" >/dev/null 2>&1 || true
                $PSQL_ADMIN "psql -v ON_ERROR_STOP=1 -q -c 'DROP DATABASE IF EXISTS \"$PG_TESTE\";' -c 'CREATE DATABASE \"$PG_TESTE\" OWNER \"$PG_USUARIO\";'" \
                    || { echo "ERRO: não consegui criar o banco $PG_TESTE como postgres."; exit 1; }
                # pg_hba: espelha para o banco de testes as regras que citam o
                # banco de produção; senão o restore e o portal caem no ident.
                HBA="$($PSQL_ADMIN "psql -tAc 'show hba_file'" 2>/dev/null | tr -d '[:space:]')"
                if [ -n "$HBA" ] && [ -f "$HBA" ]; then
                    if ! grep -Eq "^\s*(local|host|hostssl|hostnossl)\s+[^#]*\b$PG_TESTE\b" "$HBA"; then
                        cp -p "$HBA" "$HBA.antes-testes.$(date +%Y%m%d%H%M%S)"
                        NOVAS="$(grep -E "^\s*(local|host|hostssl|hostnossl)\s+" "$HBA" | grep -E "^\s*\S+\s+([^ \t]*,)?$PG_NOME(,[^ \t]*)?\s" | sed -E "s/(^\s*\S+\s+)([^ \t]*)/\1$PG_TESTE/")"
                        if [ -n "$NOVAS" ]; then
                            {
                                echo "# Portal SPARE — ambiente de testes (gerado por instalar_testes.sh)"
                                printf '%s\n' "$NOVAS"
                            } > "$STATE/hba_testes.txt"
                            # As regras novas entram ANTES das existentes: no pg_hba vale a primeira que casa.
                            cat "$STATE/hba_testes.txt" "$HBA" > "$STATE/hba_novo.txt" && cat "$STATE/hba_novo.txt" > "$HBA"
                            $PSQL_ADMIN "psql -tAc 'select pg_reload_conf()'" >/dev/null 2>&1
                            echo "   pg_hba.conf: regra do banco de testes acrescentada (cópia da regra de $PG_NOME)"
                        else
                            echo "   AVISO: não achei em $HBA uma regra que cite $PG_NOME; se o restore falhar por autenticação, acrescente uma para $PG_TESTE."
                        fi
                    fi
                fi
            else
                psql --dbname="$PG_SERVIDOR/postgres" -v ON_ERROR_STOP=1 -q -c \
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$PG_TESTE' AND pid<>pg_backend_pid();" >/dev/null 2>&1 || true
                psql --dbname="$PG_SERVIDOR/postgres" -v ON_ERROR_STOP=1 -q -c "DROP DATABASE IF EXISTS \"$PG_TESTE\";" \
                    && psql --dbname="$PG_SERVIDOR/postgres" -v ON_ERROR_STOP=1 -q -c "CREATE DATABASE \"$PG_TESTE\";" || {
                    echo "ERRO: não consegui criar o banco $PG_TESTE com o usuário do portal (sem CREATEDB ou pg_hba)."
                    echo "      Como root este script usa o usuário postgres do sistema; rode com sudo."; exit 1; }
            fi
            pg_restore --no-owner --no-acl --dbname="$PG_SERVIDOR/$PG_TESTE" "$DUMP" 2>"$STATE/pg_restore.err" \
                || { echo "AVISO: pg_restore terminou com avisos (veja $STATE/pg_restore.err)"; }
            rm -f "$DUMP"
            ;;
    esac

    echo "   SQLite dos módulos"
    N=0
    for db in "$PROD_DIR"/data/db/*.db "$PROD_DIR"/data/*.db; do
        [ -e "$db" ] || continue
        nome="$(basename "$db")"
        if command -v sqlite3 >/dev/null 2>&1; then
            sqlite3 "$db" ".backup '$TEST_DIR/data/db/$nome'" 2>/dev/null || cp -p "$db" "$TEST_DIR/data/db/$nome"
        else
            "$TEST_DIR/venv/bin/python" - "$db" "$TEST_DIR/data/db/$nome" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(dst) as b:
    a.backup(b)
PY
        fi
        rm -f "$TEST_DIR/data/db/$nome-wal" "$TEST_DIR/data/db/$nome-shm"
        N=$((N+1))
    done
    echo "   $N banco(s) SQLite copiados"
    for dir in uploads branding referencias; do
        if [ -d "$PROD_DIR/data/$dir" ]; then
            rm -rf "$TEST_DIR/data/$dir"
            cp -a "$PROD_DIR/data/$dir" "$TEST_DIR/data/$dir" && echo "   data/$dir copiado"
        fi
    done
    date '+%d/%m/%Y %H:%M' > "$TEST_DIR/data/db/.copiado_de_producao"
fi

# ── 4. Ambiente do teste ──────────────────────────────────────────────────
echo "-- Gerando $TEST_ENVFILE"
SEGREDO="$("$TEST_DIR/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
{
    echo "# Ambiente de TESTES do Portal SPARE — gerado em $(date '+%d/%m/%Y %H:%M') a partir de $PROD_ENVFILE."
    echo "# Bancos: cópia da produção. Nada aqui aponta para a produção."
    # Herda tudo da produção, menos o que identifica a instância.
    grep -vE '^\s*(#|$)' "$PROD_ENVFILE" | grep -vE '^(DATABASE_URL|[A-Z_]*_DATABASE_URL|PORT|HOST|WORKERS|AMBIENTE|CONSULTA_TIMES_PORTA|CONSULTA_TIMES_HOST|SSL_CERTFILE|SSL_KEYFILE|PORTAL_SESSION_SECRET|SMTP_HOST|ALERTA_EMAIL_TO)='
    echo ""
    echo "AMBIENTE=testes"
    printf "DATABASE_URL='%s'\n" "$(printf '%s' "$TEST_DATABASE_URL" | sed "s/'/'\\\\''/g")"
    echo "PORTAL_SESSION_SECRET=$SEGREDO"
    echo "HOST=0.0.0.0"
    echo "PORT=$TEST_PORT"
    echo "WORKERS=1"
    echo "CONSULTA_TIMES_PORTA=0"
    echo "SMTP_HOST="
    echo "ALERTA_EMAIL_TO="
} > "$TEST_ENVFILE"
chmod 600 "$TEST_ENVFILE"

# Marca o nome da aplicação para ninguém confundir a tela.
( eval "$(env_exports "$TEST_ENVFILE")"; cd "$TEST_DIR" && "$TEST_DIR/venv/bin/python" - <<'PY'
import logging; logging.disable(logging.CRITICAL)
try:
    from db.portal import SessionLocal, Setting, init_db
    init_db()
    with SessionLocal.begin() as s:
        x = s.get(Setting, "visual")
        v = dict(x.value or {}) if x else {}
        for k in ("nome_app", "login_title"):
            base = (v.get(k) or "Portal de Operações - SPARE").replace(" [TESTES]", "")
            v[k] = base + " [TESTES]"
        if x is None:
            x = Setting(key="visual"); s.add(x)
        x.value = v
    print("   nome da aplicação marcado com [TESTES]")
except Exception as exc:
    print(f"   AVISO: não marquei o nome da aplicação: {exc}")
PY
)

# ── 5. Conferência ────────────────────────────────────────────────────────
echo "-- Conferindo se o teste carrega"
( eval "$(env_exports "$TEST_ENVFILE")"; cd "$TEST_DIR" && "$TEST_DIR/venv/bin/python" - <<'PY'
import logging, sys
logging.basicConfig(level=logging.ERROR)
try:
    import main
    from config import get_settings
    c = get_settings()
    assert c.TESTES, "AMBIENTE=testes não pegou"
    assert c.DATABASE_URL.strip(), "DATABASE_URL vazio no ambiente do teste"
    print(f"   OK — porta {c.PORT}, banco {c.DATABASE_URL.split('@')[-1]}")
except Exception as exc:
    print(f"   FALHOU: {exc}")
    import os
    print("   DATABASE_URL lido:", repr((os.environ.get("DATABASE_URL") or "")[:12] + "…"))
    sys.exit(1)
PY
) || { echo "ERRO: a aplicação de teste não subiu."; exit 1; }

# ── 6. Serviço ────────────────────────────────────────────────────────────
UNIT_TXT="[Unit]
Description=Portal de Operacoes SPARE - TESTES (porta $TEST_PORT)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$TEST_DIR
EnvironmentFile=$TEST_ENVFILE
ExecStart=$TEST_DIR/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port $TEST_PORT --workers 1
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes
"
if [ "$ROOT" -eq 1 ] && [ "$SYSTEMD" -eq 1 ]; then
    # Mesmo usuário da produção, se existir: assim o cofre de senhas que só
    # ele lê continua funcionando no teste.
    USUARIO="$(grep -E '^User=' /etc/systemd/system/portal_spare.service 2>/dev/null | cut -d= -f2)"
    if [ -n "$USUARIO" ] && id "$USUARIO" >/dev/null 2>&1; then
        chown -R "$USUARIO":"$USUARIO" "$TEST_DIR" "$TEST_ENVDIR"
        UNIT_TXT="${UNIT_TXT/Type=simple/Type=simple
User=$USUARIO
Group=$USUARIO}"
        # Credenciais do systemd (EBS) iguais às da produção, se houver.
        CREDS="$(grep -E '^LoadCredentialEncrypted=' /etc/systemd/system/portal_spare.service 2>/dev/null || true)"
        [ -n "$CREDS" ] && UNIT_TXT="${UNIT_TXT}${CREDS}
"
    fi
    printf '%s' "$UNIT_TXT" > "/etc/systemd/system/$SERVICO.service"
    systemctl daemon-reload
    systemctl enable --now "$SERVICO" >/dev/null 2>&1 || systemctl restart "$SERVICO"
    systemctl restart "$SERVICO"
    COMO_VER="systemctl status $SERVICO   |   journalctl -u $SERVICO -f"
    COMO_PARAR="systemctl disable --now $SERVICO && rm /etc/systemd/system/$SERVICO.service"
elif [ "$SYSTEMD" -eq 1 ] && systemctl --user show-environment >/dev/null 2>&1; then
    mkdir -p "$HOME/.config/systemd/user"
    printf '%s' "$UNIT_TXT" | sed 's/^NoNewPrivileges=yes/NoNewPrivileges=true/' > "$HOME/.config/systemd/user/$SERVICO.service"
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICO" >/dev/null 2>&1
    systemctl --user restart "$SERVICO"
    COMO_VER="systemctl --user status $SERVICO   |   journalctl --user -u $SERVICO -f"
    COMO_PARAR="systemctl --user disable --now $SERVICO"
else
    PORTAL_APP_DIR="$TEST_DIR" PORTAL_ENVFILE="$TEST_ENVFILE" PORTAL_STATE_DIR="$STATE" bash "$TEST_DIR/deploy/portal.sh" restart
    COMO_VER="PORTAL_APP_DIR=$TEST_DIR PORTAL_ENVFILE=$TEST_ENVFILE PORTAL_STATE_DIR=$STATE $TEST_DIR/deploy/portal.sh status"
    COMO_PARAR="PORTAL_APP_DIR=$TEST_DIR PORTAL_ENVFILE=$TEST_ENVFILE PORTAL_STATE_DIR=$STATE $TEST_DIR/deploy/portal.sh stop"
fi

sleep 4
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:$TEST_PORT/" 2>/dev/null || true)"
cat <<EOF

== Ambiente de testes pronto ==

  URL:          http://$(hostname -f 2>/dev/null || hostname):$TEST_PORT/   (resposta local: ${CODE:-sem resposta})
  código:       $TEST_DIR  ($(git -C "$TEST_DIR" rev-parse --short HEAD), branch $TEST_BRANCH)
  ambiente:     $TEST_ENVFILE
  bancos:       cópia de $(cat "$TEST_DIR/data/db/.copiado_de_producao" 2>/dev/null)
  acompanhar:   $COMO_VER
  atualizar:    rode este script de novo (só o código; --recopiar-bancos para refazer os bancos)
  desligar:     $COMO_PARAR

  Em testes: agendador de automações e e-mails desligados; Consulta Times sem
  segundo listener. ServiceNow, EBS, MDM e Correios continuam reais — o que
  for gravado neles é gravado de verdade.
EOF
