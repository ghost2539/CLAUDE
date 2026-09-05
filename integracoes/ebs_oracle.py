#!/usr/bin/env python3
"""EBS Oracle — base de ACESSO somente-leitura ao Oracle E-Business Suite.

Este módulo entrega apenas a CAMADA DE ACESSO (conexão + credenciais pelo cofre)
e um executor de consultas seguro. As CONSULTAS em si são configuradas por quem
usa (dicionário ``QUERIES`` abaixo, ou passando o SQL direto para ``query()``).

Princípios de segurança (a base é PRODUÇÃO — EBSPRD):
  • Só-leitura: cada consulta roda em transação ``SET TRANSACTION READ ONLY`` e a
    conexão nunca faz commit (rollback + close no finally). Qualquer DML falha.
  • Timeout por chamada (``call_timeout``) para não travar sessão no banco.
  • Trava de linhas (``max_rows``) para não puxar volume gigante ao explorar.
  • Bind variables sempre (``:param``) — nada de concatenar valor em SQL.

Credenciais: vêm do COFRE do EBS (separado do cofre dos Correios), via
``from vcreports_secrets import s`` — exatamente como no helper que já funciona.
"""
from __future__ import annotations

import json
import os
import sys

import oracledb


# ── Segredos (cofre do EBS) ───────────────────────────────────────
def _secret(nome: str, default=None):
    """Lê um segredo do cofre do EBS. Fallback para env só em transição/dev."""
    try:
        from vcreports_secrets import s  # cofre do EBS (separado do dos Correios)
        val = s(nome, default)
        if val not in (None, ""):
            return val
    except Exception:
        pass
    return os.environ.get(nome, default)


def _config() -> dict:
    return {
        "user": _secret("ORACLE_EBS_USER", "inframon"),
        "password": _secret("ORACLE_EBS_PASS"),
        "dsn": _secret("ORACLE_EBS_DSN", "rac04-scan:1521/EBSPRD"),
        "lib_dir": _secret("ORACLE_CLIENT_LIB_DIR", "/usr/lib/oracle/21/client64/lib"),
    }


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


# ── Registro de consultas (VOCÊS configuram aqui) ─────────────────
# Preencha com as consultas de negócio. Sempre use bind variables (:param).
# Exemplo (deixado comentado de propósito):
#
# QUERIES = {
#     "saldo": "SELECT ... FROM APPS.PA_PROJECTS_ALL WHERE segment1 = :p_project_number",
# }
QUERIES: dict[str, str] = {}


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
        print(json.dumps({"error": "Uso: ebs_oracle.py <check|list|describe|NOME> [args...]"}))
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
        else:
            print(json.dumps({"data": run_named(cmd, _parse_binds(sys.argv[2:]))}, default=str))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": str(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
