"""Consulta de ativos direto na base do EBS: por número de série, etiqueta ou imobilizado."""
from __future__ import annotations

import re
from pathlib import Path

CONSULTA = Path(__file__).resolve().parent.parent / "consultas" / "ebs" / "ativo_consulta.sql"
LIMITE = 100
EMPRESAS = ("RENNER", "YOUCOM", "CAMICADO")
OBJETOS = ("FA_ADDITIONS_B", "FA_BOOKS", "FA_BOOK_CONTROLS", "FA_RETIREMENTS",
           "FA_DISTRIBUTION_HISTORY", "FA_LOCATIONS", "FA_ASSET_INVOICES")

_RE_BLOCO = re.compile(r"[ \t]*--<opcional ([A-Z_.+]+)>[ \t]*\n(.*?)[ \t]*--</opcional>[ \t]*\n", re.S)
_RE_APELIDO = re.compile(r"\bAS\s+([a-z_][a-z0-9_]*)\s*(,?)[ \t]*$", re.I | re.M)

_disponiveis: set[str] | None = None


def empresa_do_livro(livro: str) -> str:
    v = (livro or "").strip().upper()
    for nome in EMPRESAS:
        if nome in v:
            return nome
    return v.replace("FA_", "")


def _data(valor) -> str:
    if not valor:
        return ""
    return str(valor)[:10]


def _numero(valor):
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _baixado(valor) -> str:
    if valor is None or str(valor).strip() == "":
        return ""
    return "Sim" if str(valor).strip().upper() == "S" else "Não"


def nao_encontrado(pesquisado: str, erro: str = "Ativo não encontrado no EBS.") -> dict:
    return {"pesquisado": pesquisado, "encontrado": False, "erro": erro, "fonte": "EBS"}


def _formatar(pesquisado: str, r: dict) -> dict:
    imobilizado = str(r.get("imobilizado") or "").strip()
    return {
        "pesquisado": pesquisado,
        "encontrado": True,
        "fonte": "EBS",
        "empresa": empresa_do_livro(r.get("livro") or ""),
        "livro": r.get("livro") or "",
        "asset_id": str(r.get("asset_id") or ""),
        "ativo": imobilizado,
        "imobilizado": imobilizado,
        "etiqueta": str(r.get("etiqueta") or "").strip(),
        "numero_serie": str(r.get("numero_serie") or "").strip(),
        "descricao": str(r.get("descricao") or "").strip(),
        "fabricante": str(r.get("fabricante") or "").strip(),
        "modelo": str(r.get("modelo_ebs") or "").strip(),
        "custo_asset": _numero(r.get("custo")),
        "dpis": _data(r.get("dpis")),
        "baixado": _baixado(r.get("baixado")),
        "data_baixa": _data(r.get("data_baixa")),
        "local_atribuido": str(r.get("local_atribuido") or "").strip(),
        "po": str(r.get("po") or "").strip(),
        "nf": str(r.get("nf") or "").strip(),
        "erro": "",
    }


def limpar(identificadores) -> list[str]:
    vistos: set[str] = set()
    saida: list[str] = []
    for bruto in identificadores or []:
        item = str(bruto or "").strip()
        if item and item not in vistos:
            vistos.add(item)
            saida.append(item)
    return saida


def disponiveis(recarregar: bool = False) -> set[str]:
    """Tabelas e colunas que ESTA conta enxerga, entre as que a consulta usa.

    Instalação que não concede FA_RETIREMENTS, ou versão em que uma coluna não
    existe, derrubava a consulta inteira com ORA-00904/00942. Aqui o que falta
    é descoberto uma vez por processo e o pedaço correspondente sai do SQL.
    """
    global _disponiveis
    if _disponiveis is not None and not recarregar:
        return _disponiveis
    from integracoes import ebs_oracle
    binds = {f"o{n}": v for n, v in enumerate(OBJETOS)}
    lista = ", ".join(f":{k}" for k in binds)
    # Sem filtro de dono de propósito: no EBS a tabela é do esquema do produto
    # e o APPS entra por sinônimo. Filtrar por APPS traria zero linha e faria o
    # portal descartar coluna que existe.
    sql = (
        "SELECT object_name AS nome FROM all_objects "
        f" WHERE object_type IN ('TABLE','VIEW','SYNONYM') AND object_name IN ({lista}) "
        "UNION ALL "
        "SELECT table_name || '.' || column_name AS nome FROM all_tab_columns "
        f" WHERE table_name IN ({lista})"
    )
    linhas = ebs_oracle.query(sql, binds, max_rows=20000)
    _disponiveis = {str(r.get("nome") or "").strip().upper() for r in linhas if r.get("nome")}
    return _disponiveis


def _sem_bloco(corpo: str) -> str:
    apelidos = _RE_APELIDO.findall(corpo)
    if not apelidos:
        return ""
    linhas = [f"       NULL AS {nome}{virgula}" for nome, virgula in apelidos]
    return "\n".join(linhas) + "\n"


def montar_sql(lista_binds: str, presentes: set[str] | None = None) -> str:
    """O SQL do arquivo, sem os blocos que esta conta não consegue ler."""
    sql = CONSULTA.read_text(encoding="utf-8")
    if presentes is None:
        try:
            presentes = disponiveis()
        except Exception:  # noqa: BLE001 — sem o catálogo, tenta a consulta inteira
            presentes = None
    # Catálogo que não enxerga nem a tabela principal não está dizendo que as
    # outras faltam: está dizendo que não serve. Nesse caso vale o SQL inteiro.
    if presentes is not None and "FA_ADDITIONS_B" not in presentes:
        presentes = None
    if presentes is not None:
        # Catálogo que não devolveu coluna nenhuma não está dizendo que as
        # colunas faltam: nesse caso só os blocos de TABELA são decididos.
        colunas_vistas = any("." in nome for nome in presentes)

        def manter(nome: str) -> bool:
            if "." in nome:
                return not colunas_vistas or nome in presentes
            return nome in presentes

        def resolver(m):
            # O bloco pode depender de mais de um objeto (`A+B`): falta um, sai.
            exigidos = [x for x in m.group(1).upper().split("+") if x]
            return m.group(2) if all(manter(x) for x in exigidos) else _sem_bloco(m.group(2))

        sql = _RE_BLOCO.sub(resolver, sql)
    return sql.replace("/*TERMOS*/", lista_binds)


def _lote(ids: list[str]) -> list[dict]:
    from integracoes import ebs_oracle
    valores: list[str] = []
    for i in ids:
        for v in (i, i.upper()):
            if v not in valores:
                valores.append(v)
    binds = {f"t{n}": v for n, v in enumerate(valores)}
    sql = montar_sql(", ".join(f":{k}" for k in binds))
    linhas = ebs_oracle.query(sql, binds, max_rows=len(ids) * 20)
    indice: dict[str, dict] = {}
    for r in linhas:
        for campo in ("imobilizado", "etiqueta", "numero_serie"):
            chave = str(r.get(campo) or "").strip().upper()
            if chave:
                indice.setdefault(chave, r)
    return [_formatar(i, indice[i.upper()]) if i.upper() in indice else nao_encontrado(i) for i in ids]


def consultar(identificadores) -> list[dict]:
    ids = limpar(identificadores)
    saida: list[dict] = []
    for n in range(0, len(ids), LIMITE):
        saida.extend(_lote(ids[n:n + LIMITE]))
    return saida


def consultar_um(identificador: str) -> dict:
    res = consultar([identificador])
    return res[0] if res else nao_encontrado(str(identificador or ""), "Identificador vazio.")
