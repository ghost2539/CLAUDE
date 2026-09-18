#!/usr/bin/env python3
"""EBS Oracle — base de ACESSO somente-leitura ao Oracle E-Business Suite.

Este módulo entrega apenas a CAMADA DE ACESSO (conexão + credenciais pelo cofre)
e um executor de consultas seguro. As CONSULTAS em si são configuradas por quem
usa (dicionário ``QUERIES`` abaixo, ou passando o SQL direto para ``query()``).

Princípios de segurança (a base é de PRODUÇÃO de outra área):
  • Só-leitura: cada consulta roda em transação ``SET TRANSACTION READ ONLY`` e a
    conexão nunca faz commit (rollback + close no finally). Qualquer DML falha.
  • Timeout por chamada (``call_timeout``) para não travar sessão no banco.
  • Trava de linhas (``max_rows``) para não puxar volume gigante ao explorar.
  • Bind variables sempre (``:param``) — nada de concatenar valor em SQL.

Credenciais: lidas DIRETO do ambiente do processo (os.environ), no mesmo
padrão dos Correios — ORACLE_EBS_DSN / ORACLE_EBS_USER / ORACLE_EBS_PASS
(EBS_ORACLE_* aceitos como alternativa). Sem cofre.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path as _P

import oracledb


# ── Segredos (ambiente primeiro, igual ao Correios; cofre depois) ─
def _secret_multi(nomes, default=None):
    """Primeiro nome que tiver valor: no ambiente, senão no cofre local.

    O serviço injeta as chaves no ambiente do processo (mesmo local dos
    Correios), e é de lá que a conexão que está no ar lê — por isso o
    ambiente continua vindo primeiro, e nada muda para quem já roda.

    O cofre entra como segunda parada porque a tela de Parâmetros procura
    nos dois (`core.cofre.obter` é cofre local → ambiente). Sem esta linha,
    chave gravada pela tela aparecia como "resolvida" e a conexão falhava
    assim mesmo: a tela dizia uma coisa e o portal fazia outra.

    Os nomes reais são ORACLE_EBS_DSN/USER/PASS; EBS_ORACLE_* fica como
    alternativa por compatibilidade.
    """
    for nome in nomes:
        v = os.environ.get(nome)
        if v:
            return v
    # Import aqui dentro: este módulo roda também como script pela linha de
    # comando, e lá o pacote do portal pode não estar no caminho.
    try:
        from core.cofre import obter as _do_cofre
    except Exception:  # noqa: BLE001
        return default
    for nome in nomes:
        v = _do_cofre(nome, "")
        if v:
            return v
    return default


class EbsOracleSemCredencial(RuntimeError):
    """Falta chave para conectar. A mensagem diz qual, e onde gravar."""


def _config() -> dict:
    """Endereço e credencial do banco. Sem eles, erro claro em vez de chute.

    Aqui havia host, instância e usuário escritos como valor padrão. Eram
    dado de acesso e saíram do repositório; o que sobrou era um marcador
    que PARECIA valor e fazia o portal tentar conectar num host que não
    existe — o erro vinha do driver, sobre resolução de nome, e não dizia
    a ninguém que o que faltava era configuração.

    Agora o que falta é nomeado. O valor vem do ambiente do serviço, que é
    como este módulo sempre leu.
    """
    cfg = {
        "user": _secret_multi(("ORACLE_EBS_USER", "EBS_ORACLE_USER"), ""),
        "password": _secret_multi(("ORACLE_EBS_PASS", "EBS_ORACLE_PASS"), ""),
        "dsn": _secret_multi(("ORACLE_EBS_DSN", "EBS_ORACLE_DSN"), ""),
        # Caminho de instalação da máquina, não segredo: padrão pode ficar.
        "lib_dir": _secret_multi(("ORACLE_CLIENT_LIB_DIR", "EBS_ORACLE_CLIENT_LIB_DIR"),
                                 "/usr/lib/oracle/21/client64/lib"),
    }
    faltando = [nome for nome, chave in
                (("ORACLE_EBS_DSN", "dsn"), ("ORACLE_EBS_USER", "user"),
                 ("ORACLE_EBS_PASS", "password")) if not cfg[chave]]
    if faltando:
        raise EbsOracleSemCredencial(
            "Credencial da base do EBS ausente: " + ", ".join(faltando) +
            ". Grave no environment do serviço (data/environment, e reinicie) ou pela tela Parâmetros → Base EBS.")
    return cfg


# ── Cliente Oracle (modo thick com Instant Client) ────────────────
_client_ready = False


def _ensure_client(lib_dir: str) -> None:
    global _client_ready
    if _client_ready:
        return
    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
    except Exception:
        # Já iniciado nesta sessão, ou cai para modo thin — segue.
        pass
    _client_ready = True


# ── Parâmetros de segurança (ajustáveis) ──────────────────────────
DEFAULT_TIMEOUT_S = 60       # tempo máximo por chamada ao banco
DEFAULT_MAX_ROWS = 5000      # teto de linhas por consulta (0 = sem teto)
DEFAULT_ARRAYSIZE = 500      # linhas por fetch (throughput)


# ── Conexão e execução ────────────────────────────────────────────
def get_connection():
    """Abre uma conexão nova (não comita nada; use com ``query``)."""
    c = _config()
    _ensure_client(c["lib_dir"])
    conn = oracledb.connect(user=c["user"], password=c["password"], dsn=c["dsn"])
    try:
        conn.call_timeout = DEFAULT_TIMEOUT_S * 1000  # ms
    except Exception:
        pass
    conn.autocommit = False
    return conn


def _rows_to_dicts(cur, rows) -> list[dict]:
    cols = [d[0].lower() for d in cur.description]
    out: list[dict] = []
    for row in rows:
        r: dict = {}
        for i, val in enumerate(row):
            if val is None:
                r[cols[i]] = None
            elif isinstance(val, oracledb.LOB):
                r[cols[i]] = val.read()
            elif hasattr(val, "isoformat"):
                r[cols[i]] = val.isoformat()
            else:
                r[cols[i]] = val
        out.append(r)
    return out


def query(
    sql: str,
    binds: dict | None = None,
    max_rows: int = DEFAULT_MAX_ROWS,
    read_only: bool = True,
) -> list[dict]:
    """Executa um SELECT e devolve lista de dicts (colunas em minúsculo).

    - ``binds``: dicionário de bind variables (``:param``).
    - ``max_rows``: teto de linhas (0 = sem teto). Segurança ao explorar.
    - ``read_only``: True → ``SET TRANSACTION READ ONLY`` (recomendado).
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.arraysize = DEFAULT_ARRAYSIZE
            if read_only:
                cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(sql, binds or {})
            rows = cur.fetchmany(max_rows) if max_rows else cur.fetchall()
            return _rows_to_dicts(cur, rows)
    finally:
        try:
            conn.rollback()  # garante que nada é persistido
        finally:
            conn.close()


# ── Exploração da base padrão (dicionário de dados) ───────────────
def check_access() -> dict:
    """Valida o acesso: quem sou, em qual banco/instância e a hora do servidor.
    Use isto PRIMEIRO — confirma credenciais/DSN sem tocar em tabela de negócio."""
    return query(
        """
        SELECT SYS_CONTEXT('USERENV','SESSION_USER')  AS session_user,
               SYS_CONTEXT('USERENV','DB_NAME')       AS db_name,
               SYS_CONTEXT('USERENV','INSTANCE_NAME') AS instance_name,
               SYS_CONTEXT('USERENV','SERVER_HOST')   AS server_host,
               TO_CHAR(SYSDATE,'YYYY-MM-DD HH24:MI:SS') AS db_time
        FROM dual
        """
    )[0]


def list_objects(prefix: str, owner: str = "APPS", limit: int = 1000) -> list[dict]:
    """Lista tabelas/views/synonyms ACESSÍVEIS pela conta, por prefixo.
    Ex.: list_objects('PA') → objetos de Projetos que a conta enxerga."""
    return query(
        """
        SELECT owner, object_name, object_type
        FROM all_objects
        WHERE owner = :owner
          AND object_type IN ('TABLE','VIEW','SYNONYM')
          AND object_name LIKE :pat
        ORDER BY object_name
        """,
        {"owner": owner.upper(), "pat": prefix.upper() + "%"},
        max_rows=limit,
    )


def describe(object_name: str, owner: str = "APPS") -> list[dict]:
    """Descreve as colunas de um objeto (nome, tipo, tamanho, aceita nulo)."""
    return query(
        """
        SELECT column_name, data_type, data_length, nullable
        FROM all_tab_columns
        WHERE owner = :owner AND table_name = :name
        ORDER BY column_id
        """,
        {"owner": owner.upper(), "name": object_name.upper()},
        max_rows=0,
    )


def find_objects(termo: str, limit: int = 500) -> list[dict]:
    """Procura objetos (tabela/view/synonym) cujo NOME contém `termo`, em
    QUALQUER owner acessível pela conta. Útil para garimpar a tabela certa,
    ex.: find_objects('FA_ADD') ou find_objects('ATIVO')."""
    return query(
        """
        SELECT owner, object_name, object_type
        FROM all_objects
        WHERE object_type IN ('TABLE','VIEW','SYNONYM')
          AND object_name LIKE :pat
        ORDER BY
          CASE object_type WHEN 'TABLE' THEN 0 WHEN 'VIEW' THEN 1 ELSE 2 END,
          owner, object_name
        """,
        {"pat": "%" + termo.upper() + "%"},
        max_rows=limit,
    )


def sql_livre(texto: str, max_rows: int = 200) -> list[dict]:
    """Roda um SELECT ad-hoc, só-leitura, com trava contra DML.
    Para exploração pelo CLI (`ebs_oracle.py sql "SELECT ..."`)."""
    limpo = texto.strip().rstrip(";").strip()
    inicio = limpo.lstrip("(").lstrip().split(None, 1)[0].lower() if limpo else ""
    if inicio not in ("select", "with"):
        raise ValueError("Só SELECT/WITH é permitido no comando sql.")
    return query(limpo, {}, max_rows=max_rows, read_only=True)


# ── Registro de consultas (VOCÊS configuram aqui) ─────────────────
# ── As consultas ficam em arquivos, não aqui ───────────────────────
# Elas moravam num dicionário `QUERIES` neste arquivo. Saíram por dois
# motivos concretos:
#
# 1.  Quem precisa LER o SQL é quem entende de EBS, e essa pessoa não
#     precisa abrir um módulo Python para achar a consulta no meio de
#     `_secret_multi`, `call_timeout` e tratamento de exceção.
# 2.  `routers/ebs_oracle.py` precisa da LISTA de consultas para montar a
#     tela, e não pode importar este módulo num servidor sem o driver
#     Oracle (`import oracledb` estoura no topo). A saída era ler o
#     dicionário do próprio arquivo-fonte com regex e `exec` — ou seja,
#     executar um pedaço de código deste módulo para não executar o módulo.
#     Com as consultas em arquivo, o router lê a pasta e pronto.
#
# Um arquivo por consulta, em `consultas/ebs/<nome>.sql`. O nome do arquivo
# é o nome da consulta. Comentário de linha (`--`) fica no arquivo e é o
# lugar de explicar o porquê de cada recorte.
PASTA_CONSULTAS = _P(__file__).resolve().parent.parent / "consultas" / "ebs"

# Só nome de arquivo simples entra: nada de subpasta nem de `..`. O nome
# chega por parâmetro em `run_named`, e um dia vai chegar de uma tela.
_RE_NOME_CONSULTA = re.compile(r"^[a-z][a-z0-9_]{0,60}$")


def carregar_consultas(pasta=None) -> dict[str, str]:
    """Lê `consultas/ebs/*.sql`. O nome do arquivo é o nome da consulta."""
    pasta = _P(pasta) if pasta else PASTA_CONSULTAS
    saida: dict[str, str] = {}
    if not pasta.is_dir():
        return saida
    for arq in sorted(pasta.glob("*.sql")):
        if not _RE_NOME_CONSULTA.match(arq.stem):
            continue
        saida[arq.stem] = arq.read_text(encoding="utf-8")
    return saida


def binds_de(sql: str) -> tuple[str, ...]:
    """Os binds que um SQL usa, na ordem em que aparecem.

    Sai do próprio SQL: um bind que a tela peça e a consulta não use — ou o
    contrário — seria erro de digitação em dois lugares em vez de um.
    Comentário é descartado antes, senão um `:coisa` escrito num `--` viraria
    campo no formulário da tela.
    """
    sem_comentario = re.sub(r"--[^\n]*", "", sql)
    return tuple(dict.fromkeys(
        re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", sem_comentario)))


QUERIES: dict[str, str] = carregar_consultas()
BINDS: dict[str, tuple[str, ...]] = {n: binds_de(s) for n, s in QUERIES.items()}


def run_named(name: str, binds: dict | None = None, max_rows: int = DEFAULT_MAX_ROWS):
    if name not in QUERIES:
        raise KeyError(f"Consulta desconhecida: {name}")
    return query(QUERIES[name], binds, max_rows=max_rows)


# ── CLI (compatível com o helper original + exploração) ───────────
def _parse_binds(args: list[str]) -> dict:
    binds: dict = {}
    for arg in args:
        if "=" not in arg:
            continue
        k, v = arg.split("=", 1)
        if v == "NULL":
            binds[k] = None
        elif v.isdigit() and (v == "0" or not v.startswith("0")):
            # int só quando é numérico E não tem zero à esquerda (preserva
            # números de projeto/PO com zeros à frente).
            binds[k] = int(v)
        else:
            binds[k] = v
    return binds


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Uso: ebs_oracle.py <check|list|find|describe|sql|NOME> [args...]"}))
        sys.exit(1)

    cmd = sys.argv[1]
    try:
        if cmd == "check":
            print(json.dumps({"data": check_access()}, default=str))
        elif cmd == "list":
            prefix = sys.argv[2] if len(sys.argv) > 2 else ""
            print(json.dumps({"data": list_objects(prefix)}, default=str))
        elif cmd == "describe":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "describe <OBJETO>"}))
                sys.exit(1)
            print(json.dumps({"data": describe(sys.argv[2])}, default=str))
        elif cmd == "find":
            if len(sys.argv) < 3:
                print(json.dumps({"error": "find <TERMO>"}))
                sys.exit(1)
            print(json.dumps({"data": find_objects(sys.argv[2])}, default=str))
        elif cmd == "sql":
            if len(sys.argv) < 3:
                print(json.dumps({"error": 'sql "SELECT ..." [max_rows]'}))
                sys.exit(1)
            mx = int(sys.argv[3]) if len(sys.argv) > 3 else 200
            print(json.dumps({"data": sql_livre(sys.argv[2], max_rows=mx)}, default=str))
        else:
            print(json.dumps({"data": run_named(cmd, _parse_binds(sys.argv[2:]))}, default=str))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
