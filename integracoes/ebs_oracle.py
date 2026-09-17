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
# Preencha com as consultas de negócio. Sempre use bind variables (:param).
# As consultas do módulo Gestão de Compras (oracle_helper.py do time), tal e
# qual: os nomes e os binds são os de lá, de propósito. Elas continuam aqui
# porque quem as usa é a Base EBS — a ponte HTTP que também as chamava saiu
# do portal junto com a aba Gestão de Compras.
QUERIES: dict[str, str] = {
    "saldo": """
WITH proj AS (
    SELECT project_id, segment1 AS nro_projeto, name AS nome_projeto
    FROM APPS.PA_PROJECTS_ALL WHERE segment1 = :p_project_number
)
SELECT DISTINCT p.nro_projeto, p.nome_projeto, NVL(bl.burdened_cost, 0) AS burdened_cost,
    bl.creation_date AS dt_criacao_linha, bv.version_number
FROM proj p
JOIN APPS.PA_TASKS t ON t.project_id = p.project_id
JOIN APPS.PA_RESOURCE_ASSIGNMENTS ra ON ra.task_id = t.task_id
JOIN APPS.PA_BUDGET_LINES bl ON bl.resource_assignment_id = ra.resource_assignment_id
JOIN APPS.PA_BUDGET_VERSIONS bv ON bv.budget_version_id = bl.budget_version_id
WHERE (bv.current_flag = 'Y' OR bv.budget_status_code = 'B')
  AND (NVL(bl.raw_cost,0)>0 OR NVL(bl.burdened_cost,0)>0 OR NVL(bl.project_raw_cost,0)>0 OR NVL(bl.project_burdened_cost,0)>0)
""",
    "po": """
SELECT ph.authorization_status AS status, NVL(pll.amount_billed, 0) AS amount_billed_ship,
    CASE WHEN NVL(pll.amount_billed, 0)=0 THEN 'Comprometida' ELSE 'Realizado' END AS status_faturado,
    ph.segment1 || ' / ' || pll.shipment_num AS numero_po, ph.creation_date AS data_criacao,
    ph.approved_date AS data_aprovacao, pl.line_num AS po_line_num, pll.shipment_num,
    pl.item_description AS desc_po, pll.need_by_date AS necessario_em,
    NVL(pll.quantity, 0) AS qty_pedida, NVL(pll.quantity_received, 0) AS qty_recebida,
    NVL(pll.quantity_billed, 0) AS qty_faturada, NVL(pll.quantity_cancelled, 0) AS qty_cancelada,
    NVL(pll.price_override, NVL(pl.unit_price, 0)) AS price_override,
    NVL(pll.amount, NVL(pll.quantity, 0) * NVL(pll.price_override, NVL(pl.unit_price, 0))) AS amount_ship
FROM APPS.PA_PROJECTS_ALL p
JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.project_id = p.project_id
JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.line_location_id = pd.line_location_id
JOIN APPS.PO_LINES_ALL pl ON pl.po_line_id = pd.po_line_id
JOIN APPS.PO_HEADERS_ALL ph ON ph.po_header_id = pl.po_header_id
WHERE p.segment1 = :p_project_number ORDER BY ph.segment1, pll.shipment_num
""",
    "rc": """
SELECT p.segment1 AS project_number, prh.segment1 AS rc_numero, prh.authorization_status AS rc_status,
    prh.description AS rc_descricao, prh.creation_date AS rc_data_criacao, prl.line_num AS rc_line_num,
    prl.item_description AS rc_item_desc, prl.quantity AS rc_qty, prl.unit_price AS rc_unit_price
FROM apps.pa_projects_all p
JOIN apps.po_req_distributions_all prd ON prd.project_id = p.project_id
JOIN apps.po_requisition_lines_all prl ON prl.requisition_line_id = prd.requisition_line_id
JOIN apps.po_requisition_headers_all prh ON prh.requisition_header_id = prl.requisition_header_id
WHERE p.segment1 = :p_project_number ORDER BY rc_data_criacao DESC
""",
    "acordos": """
SELECT pha.segment1 AS agreement_num, pv.vendor_name, pha.start_date, pha.end_date,
    ROUND(pha.end_date - SYSDATE) AS days_to_expire, pha.authorization_status,
    hou.name AS operating_unit
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
LEFT JOIN APPS.HR_OPERATING_UNITS hou ON hou.organization_id = pha.org_id
WHERE pha.type_lookup_code = 'BLANKET' AND pha.authorization_status = 'APPROVED'
  AND pha.end_date IS NOT NULL AND TRUNC(pha.end_date) BETWEEN TRUNC(SYSDATE) AND TRUNC(SYSDATE) + :p_days
ORDER BY days_to_expire ASC
""",
    "vendor_lookup": """
SELECT DISTINCT pv.vendor_name
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
WHERE pha.type_lookup_code IN ('BLANKET','CONTRACT') AND pha.authorization_status = 'APPROVED'
  AND TRUNC(SYSDATE) >= TRUNC(NVL(pha.start_date, SYSDATE))
  AND (pha.end_date IS NULL OR TRUNC(SYSDATE) <= TRUNC(pha.end_date))
ORDER BY pv.vendor_name
""",
    "vendor_items": """
SELECT hou.name AS operating_unit, NVL(msib.description, pl.item_description) AS item_description,
    pl.unit_meas_lookup_code AS uom, pl.list_price_per_unit AS unit_price
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
LEFT JOIN APPS.HR_OPERATING_UNITS hou ON hou.organization_id = pha.org_id
JOIN APPS.PO_LINES_ALL pl ON pl.po_header_id = pha.po_header_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = pl.item_id AND msib.organization_id = 0
WHERE pha.type_lookup_code IN ('BLANKET','CONTRACT') AND pha.authorization_status = 'APPROVED'
  AND UPPER(pv.vendor_name) = UPPER(:p_vendor_name)
  AND TRUNC(SYSDATE) >= TRUNC(NVL(pha.start_date, SYSDATE))
  AND (pha.end_date IS NULL OR TRUNC(SYSDATE) <= TRUNC(pha.end_date))
ORDER BY hou.name, pha.segment1, pl.line_num
""",
    # ── Nossa consulta: os itens de uma PO, para o Agendamento ──────────
    # A busca_po acima veio do módulo do outro time e devolve uma linha por
    # DISTRIBUIÇÃO: o mesmo item repete quando há várias entregas ou vários
    # projetos rateando. Para a tela de Agendamento isso é ruído — ela
    # precisa de "10 desktops, 20 leitores", uma linha por item.
    #
    # Então aqui agrega por linha do pedido e devolve o que a tela usa:
    # descrição, unidade, quantidade pedida, já recebida e o que falta.
    # Linha cancelada fica de fora; quantidade_pendente é o que se espera
    # receber de verdade.
    "po_itens": """
SELECT ph.segment1                                   AS po_numero,
       pr.release_num                                AS liberacao,
       s.vendor_name                                 AS fornecedor,
       ph.authorization_status                       AS status_po,
       ph.currency_code                              AS moeda,
       pl.line_num                                   AS linha,
       LTRIM(msib.segment1, '0')                     AS item_ebs,
       NVL(pl.item_description, msib.description)    AS descricao,
       NVL(pl.unit_meas_lookup_code, msib.primary_uom_code) AS unidade,
       SUM(NVL(pll.quantity, 0))                     AS quantidade_pedida,
       SUM(NVL(pll.quantity_received, 0))            AS quantidade_recebida,
       SUM(NVL(pll.quantity, 0) - NVL(pll.quantity_received, 0)) AS quantidade_pendente,
       MAX(NVL(pll.price_override, pl.unit_price))   AS preco_unitario
FROM APPS.PO_HEADERS_ALL ph
JOIN APPS.PO_LINES_ALL          pl  ON pl.po_header_id = ph.po_header_id
JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.po_line_id  = pl.po_line_id
LEFT JOIN APPS.PO_RELEASES_ALL  pr  ON pr.po_release_id = pll.po_release_id
LEFT JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = ph.vendor_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib
       ON msib.inventory_item_id = pl.item_id
      AND msib.organization_id   = pll.ship_to_organization_id
WHERE ph.segment1 = :numero_po
  -- Acordo de compras tem UM número de PO e várias liberações: o que muda de
  -- um pedido para outro é o número depois do hífen (2570313-25 → liberação
  -- 25). Sem este filtro a consulta somaria as quantidades de TODAS as
  -- liberações do acordo, e o agendamento nasceria pedindo o total do ano.
  -- `:liberacao` nulo traz a PO inteira, que é o caso da compra avulsa.
  AND (:liberacao IS NULL OR pr.release_num = :liberacao)
  AND NVL(pl.cancel_flag, 'N') = 'N'
  AND NVL(pll.cancel_flag, 'N') = 'N'
GROUP BY ph.segment1, pr.release_num, s.vendor_name, ph.authorization_status,
         ph.currency_code, pl.line_num, msib.segment1, pl.item_description,
         msib.description, pl.unit_meas_lookup_code, msib.primary_uom_code
ORDER BY pl.line_num
""",
    "busca_po": """
SELECT h.segment1 AS po_numero, ppa.segment1 AS projeto_numero, ppa.name AS projeto_nome,
    s.vendor_name AS fornecedor, h.authorization_status AS status_po, l.line_num AS linha,
    NVL(l.item_description, msib.description) AS descricao_item,
    NVL(l.unit_meas_lookup_code, msib.primary_uom_code) AS uom,
    l.unit_price AS preco_unitario, l.closed_code AS status_linha,
    ll.shipment_num AS entrega, ll.quantity AS quantidade_pedida,
    ll.promised_date AS data_prometida, ll.need_by_date AS data_necessidade,
    ll.closed_code AS status_entrega, pd.distribution_num AS distribuicao,
    pat.task_number AS tarefa_numero, pat.task_name AS tarefa_nome,
    pd.expenditure_type AS tipo_despesa, pd.destination_type_code AS destino,
    COALESCE(pah_ll.note, pah_hdr.note) AS motivo_rejeicao,
    COALESCE(pah_ll.action_date, pah_hdr.action_date) AS data_rejeicao,
    COALESCE(fu_ll.user_name, fu_hdr.user_name) AS rejeitado_por
FROM APPS.PO_HEADERS_ALL h
JOIN APPS.PO_LINES_ALL l ON l.po_header_id = h.po_header_id
JOIN APPS.PO_LINE_LOCATIONS_ALL ll ON ll.po_line_id = l.po_line_id
JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = h.vendor_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = l.item_id AND msib.organization_id = ll.ship_to_organization_id
JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.line_location_id = ll.line_location_id
LEFT JOIN APPS.PA_PROJECTS_ALL ppa ON ppa.project_id = pd.project_id
LEFT JOIN APPS.PA_TASKS pat ON pat.task_id = pd.task_id
LEFT JOIN (SELECT pah1.* FROM APPS.PO_ACTION_HISTORY pah1 WHERE pah1.object_type_code='PO' AND pah1.action_code='REJECT'
  AND pah1.sequence_num=(SELECT MAX(pah2.sequence_num) FROM APPS.PO_ACTION_HISTORY pah2
  WHERE pah2.object_type_code=pah1.object_type_code AND pah2.object_id=pah1.object_id AND pah2.action_code='REJECT')
) pah_hdr ON pah_hdr.object_id = h.po_header_id
LEFT JOIN APPS.FND_USER fu_hdr ON fu_hdr.user_id = pah_hdr.last_updated_by
LEFT JOIN (SELECT pah1.* FROM APPS.PO_ACTION_HISTORY pah1 WHERE pah1.object_type_code='PO_LINE_LOCATION' AND pah1.action_code='REJECT'
  AND pah1.sequence_num=(SELECT MAX(pah2.sequence_num) FROM APPS.PO_ACTION_HISTORY pah2
  WHERE pah2.object_type_code=pah1.object_type_code AND pah2.object_id=pah1.object_id AND pah2.action_code='REJECT')
) pah_ll ON pah_ll.object_id = ll.line_location_id
LEFT JOIN APPS.FND_USER fu_ll ON fu_ll.user_id = pah_ll.last_updated_by
WHERE h.segment1 = :numero_po AND (:p_line_num IS NULL OR l.line_num = :p_line_num)
ORDER BY l.line_num, ll.shipment_num, pd.distribution_num
""",
    "catalogo": """
SELECT * FROM (
    SELECT LTRIM(msib.segment1, '0') AS item_ebs, msib.description AS descricao,
        CASE msib.item_type WHEN 'ATIVO FIXO' THEN 'HARDWARE' WHEN 'SERVICO' THEN 'SERVICOS'
            WHEN 'SERVICO ATIVO FIXO' THEN 'SERVICOS' WHEN 'USO CONSUMO' THEN 'HARDWARE' ELSE 'OUTROS' END AS tipo_item,
        s.vendor_name AS fornecedor, pl.unit_price AS valor_unitario, pd.expenditure_type,
        pat.task_number AS tarefa, ph.creation_date AS po_date,
        ROW_NUMBER() OVER (PARTITION BY msib.inventory_item_id ORDER BY ph.creation_date DESC) AS rn
    FROM APPS.PO_HEADERS_ALL ph
    JOIN APPS.PO_LINES_ALL pl ON pl.po_header_id = ph.po_header_id
    JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.po_line_id = pl.po_line_id
    JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = ph.vendor_id
    LEFT JOIN APPS.PA_TASKS pat ON pat.task_id = pd.task_id
    LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = pl.item_id AND msib.organization_id = 101
    WHERE ph.authorization_status IN ('APPROVED','CLOSED')
      AND pd.expenditure_type IN ('Computadores e Perifericos','Sistemas de Informatica')
      AND msib.segment1 IS NOT NULL AND ph.creation_date >= ADD_MONTHS(SYSDATE, -36)
) WHERE rn = 1 ORDER BY descricao
""",
}

# Binds de cada consulta, para a tela montar o formulário e validar antes de
# ir ao banco. Derivado do SQL: um bind fora daqui é erro de digitação.
BINDS: dict[str, tuple[str, ...]] = {
    nome: tuple(dict.fromkeys(re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", sql)))
    for nome, sql in QUERIES.items()
}


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
