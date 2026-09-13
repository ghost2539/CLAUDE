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

try:
    import oracledb
except ImportError:  # driver ausente: configurar ainda funciona; conectar avisa
    oracledb = None  # type: ignore[assignment]


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


# ── Configuração pelo portal (Configuração → Configuração Módulos) ──
# Lida a cada conexão: mudar host, serviço ou usuário vale na próxima
# consulta, sem reiniciar. A senha fica cifrada no banco de monitoramento
# (mesmo esquema da SMTP) e o cofre, quando tiver ORACLE_EBS_PASS, ganha.
CHAVE_PORTAL = "ebs_oracle"
_PADRAO = {"host": "rac04-scan", "porta": "1521", "servico": "EBSPRD",
           "usuario": "inframon", "lib_dir": "/usr/lib/oracle/21/client64/lib"}


def _do_portal() -> dict:
    try:
        import db.monitoramento as _mon
        return _mon.obter_config(CHAVE_PORTAL) or {}
    except Exception:  # noqa: BLE001 — sem banco do portal, segue no cofre/env
        return {}


def _chave_cofre(c: dict) -> str:
    """Nome da chave da senha no cofre, configurável pelo portal.

    O cofre só o processo do serviço lê; a tela guarda apenas a
    REFERÊNCIA. Assim a senha continua onde deve estar e mesmo assim
    ninguém precisa mexer em código para apontar outra chave."""
    return (c.get("cofre_chave") or "").strip() or "ORACLE_EBS_PASS"


def _senha(c: dict) -> tuple[str, str]:
    """(senha, fonte). Cofre (pela chave configurada) > cifrada no portal > ambiente."""
    do_cofre = _secret(_chave_cofre(c))
    if do_cofre:
        return str(do_cofre), "cofre"
    try:
        from core.notificador import decifrar
        s = decifrar(c.get("senha_algo", ""), c.get("senha_cifrada", ""))
        if s:
            return s, "portal"
    except Exception:  # noqa: BLE001
        pass
    amb = os.environ.get("ORACLE_EBS_PASS", "")
    return (amb, "ambiente") if amb else ("", "nenhuma")


def _config() -> dict:
    c = _do_portal()
    host = (c.get("host") or "").strip()
    porta = str(c.get("porta") or "").strip()
    servico = (c.get("servico") or "").strip()
    if host and servico:
        dsn = f"{host}:{porta or '1521'}/{servico}"
    else:
        dsn = _secret("ORACLE_EBS_DSN", f"{_PADRAO['host']}:{_PADRAO['porta']}/{_PADRAO['servico']}")
    senha, _fonte = _senha(c)
    return {
        "user": (c.get("usuario") or "").strip() or _secret("ORACLE_EBS_USER", _PADRAO["usuario"]),
        "password": senha,
        "dsn": dsn,
        "lib_dir": (c.get("lib_dir") or "").strip() or _secret("ORACLE_CLIENT_LIB_DIR", _PADRAO["lib_dir"]),
    }


def config_publica() -> dict:
    """Para a tela: tudo menos a senha, mais de onde a senha vem."""
    c = _do_portal()
    efetiva = _config()
    host, _, resto = efetiva["dsn"].partition(":")
    porta, _, servico = resto.partition("/")
    _s, fonte = _senha(c)
    chave = _chave_cofre(c)
    return {"host": host, "porta": porta, "servico": servico, "usuario": efetiva["user"],
            "lib_dir": efetiva["lib_dir"], "senha_definida": fonte != "nenhuma",
            "senha_fonte": fonte, "cofre_chave": chave,
            "cofre_disponivel": bool(_secret(chave))}


def salvar_configuracao(dados: dict, senha: str | None = None) -> dict:
    """Grava host/porta/serviço/usuário/lib_dir; a senha só quando enviada."""
    import db.monitoramento as _mon
    novo = {k: str(dados.get(k, "")).strip() for k in ("host", "porta", "servico", "usuario", "lib_dir", "cofre_chave")}
    if senha:
        from core.notificador import cifrar
        algo, blob = cifrar(senha)
        novo["senha_algo"], novo["senha_cifrada"] = algo, blob
    _mon.salvar_config(novo, CHAVE_PORTAL)
    return config_publica()


def testar_conexao() -> dict:
    """Abre, faz SELECT 1 FROM DUAL, fecha. Devolve ok, latência e o erro cru."""
    import time as _t
    ini = _t.perf_counter()
    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT SYSDATE FROM DUAL")
            cur.fetchone()
        finally:
            conn.close()
        return {"ok": True, "ms": int((_t.perf_counter() - ini) * 1000), "dsn": _config()["dsn"]}
    except Exception as exc:  # noqa: BLE001 — o erro é o que a tela precisa mostrar
        return {"ok": False, "ms": int((_t.perf_counter() - ini) * 1000),
                "dsn": _config()["dsn"], "erro": str(exc)[:300]}


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
    if oracledb is None:
        raise RuntimeError("Driver Oracle (python-oracledb) não instalado neste servidor.")
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
            elif oracledb is not None and isinstance(val, oracledb.LOB):
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
