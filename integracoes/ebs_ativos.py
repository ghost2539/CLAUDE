"""Consulta de ativos direto na base do EBS: por número de série, etiqueta ou imobilizado."""
from __future__ import annotations

import logging
import re
from pathlib import Path

CONSULTA = Path(__file__).resolve().parent.parent / "consultas" / "ebs" / "ativo_consulta.sql"
LIMITE = 100
EMPRESAS = ("RENNER", "YOUCOM", "CAMICADO")
DONO = "APPS"
TABELAS = ("FA_ADDITIONS_B", "FA_ADDITIONS_TL", "FA_BOOKS", "FA_BOOK_CONTROLS",
           "FA_RETIREMENTS", "FA_DISTRIBUTION_HISTORY", "FA_LOCATIONS", "FA_ASSET_INVOICES",
           "AP_INVOICE_DISTRIBUTIONS_ALL", "PO_DISTRIBUTIONS_ALL", "PO_RELEASES_ALL")
# A liberação da PO (o "-32" de 2570313-32) sai da distribuição da fatura.
CADEIA_LIBERACAO = ("FA_ASSET_INVOICES.PO_NUMBER", "FA_ASSET_INVOICES.INVOICE_DISTRIBUTION_ID",
                    "AP_INVOICE_DISTRIBUTIONS_ALL.PO_DISTRIBUTION_ID",
                    "PO_DISTRIBUTIONS_ALL.PO_RELEASE_ID", "PO_RELEASES_ALL.RELEASE_NUM")
BUSCA = (("IMOBILIZADO", "ASSET_NUMBER"), ("ETIQUETA", "TAG_NUMBER"), ("SERIE", "SERIAL_NUMBER"))

_RE_BLOCO = re.compile(r"[ \t]*--<opcional ([A-Z0-9_.+]+)>[ \t]*\n(.*?)[ \t]*--</opcional>[ \t]*\n", re.S)
_RE_APELIDO = re.compile(r"\bAS\s+([a-z_][a-z0-9_]*)\s*(,?)[ \t]*$", re.I | re.M)
_RE_NULO = re.compile(r"^\s*NULL AS ([a-z_][a-z0-9_]*)\s*,?\s*$", re.I)

_log = logging.getLogger("ebs_ativos")
_catalogo: dict[str, set[str]] | None = None


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


def catalogo(recarregar: bool = False) -> dict[str, set[str]]:
    """Colunas que ESTA conta enxerga nas tabelas da consulta.

    Instalação difere: em R12 a descrição do ativo está em FA_ADDITIONS_TL, e
    FA_BOOKS não tem date_retired. Perguntar à base o que existe evita derrubar
    a consulta inteira com ORA-00904/ORA-00942 uma coluna por vez.
    """
    global _catalogo
    if _catalogo is not None and not recarregar:
        return _catalogo
    from integracoes import ebs_oracle
    binds = {f"o{n}": v for n, v in enumerate(TABELAS)}
    lista = ", ".join(f":{k}" for k in binds)
    # Dono restrito aos esquemas que o sinônimo aponta: varrer ALL_TAB_COLUMNS
    # inteiro numa base de EBS é lento.
    alvo = (
        "SELECT c.table_name AS tabela, c.column_name AS coluna "
        "  FROM all_tab_columns c "
        f" WHERE c.table_name IN ({lista}) "
        "   AND c.owner IN (SELECT s.table_owner FROM all_synonyms s "
        f"                   WHERE s.synonym_name IN ({lista}) "
        "                     AND s.owner IN (:dono, 'PUBLIC') "
        "                   UNION ALL SELECT :dono FROM dual)"
    )
    largo = ("SELECT c.table_name AS tabela, c.column_name AS coluna "
             "  FROM all_tab_columns c "
             f" WHERE c.table_name IN ({lista})")
    linhas = ebs_oracle.query(alvo, {**binds, "dono": DONO}, max_rows=20000)
    if not linhas:
        linhas = ebs_oracle.query(largo, binds, max_rows=20000)
    achado: dict[str, set[str]] = {}
    for r in linhas:
        tabela = str(r.get("tabela") or "").strip().upper()
        coluna = str(r.get("coluna") or "").strip().upper()
        if tabela and coluna:
            achado.setdefault(tabela, set()).add(coluna)
    _catalogo = achado
    _log.info("catálogo do EBS: %s", {t: len(c) for t, c in sorted(achado.items())})
    return _catalogo


def _coluna(cat, tabela: str, nomes) -> str:
    existentes = cat.get(tabela) if cat else None
    if existentes is None:
        return nomes[0]
    for nome in nomes:
        if nome in existentes:
            return nome
    return ""


def _parecida(cat, tabela: str, parte: str) -> str:
    existentes = sorted(cat.get(tabela) or []) if cat else []
    for nome in existentes:
        if parte in nome:
            return nome
    return ""


def mapa(cat=None) -> dict:
    """Qual coluna da base alimenta cada campo da tela."""
    if cat is None:
        try:
            cat = catalogo()
        except Exception:  # noqa: BLE001
            cat = None
    cat = cat or None
    if cat is not None and not cat.get("FA_ADDITIONS_B"):
        cat = None
    m: dict[str, str] = {}
    for campo, coluna in BUSCA:
        achou = _coluna(cat, "FA_ADDITIONS_B", (coluna,))
        m[campo] = f"fa.{achou.lower()}" if achou else ""
    desc = _coluna(cat, "FA_ADDITIONS_B", ("DESCRIPTION", "ASSET_DESCRIPTION", "ITEM_DESCRIPTION"))
    if desc:
        m["DESCRICAO"] = f"fa.{desc.lower()}"
    else:
        tl = _coluna(cat, "FA_ADDITIONS_TL", ("DESCRIPTION",)) if cat else ""
        if tl:
            m["DESCRICAO"] = f"tl.{tl.lower()}"
        else:
            perto = _parecida(cat, "FA_ADDITIONS_B", "DESCRI")
            m["DESCRICAO"] = f"fa.{perto.lower()}" if perto else ""
    fab = _coluna(cat, "FA_ADDITIONS_B", ("MANUFACTURER_NAME", "MANUFACTURER"))
    m["FABRICANTE"] = f"fa.{fab.lower()}" if fab else ""
    mod = _coluna(cat, "FA_ADDITIONS_B", ("MODEL_NUMBER", "MODEL"))
    m["MODELO"] = f"fa.{mod.lower()}" if mod else ""
    custo = _coluna(cat, "FA_BOOKS", ("COST",))
    m["CUSTO"] = f"fb.{custo.lower()}" if custo else ""
    dpis = _coluna(cat, "FA_BOOKS", ("DATE_PLACED_IN_SERVICE",))
    m["DPIS"] = f"fb.{dpis.lower()}" if dpis else ""
    baixa = _coluna(cat, "FA_BOOKS", ("PERIOD_COUNTER_FULLY_RETIRED", "DATE_RETIRED"))
    m["BAIXADO"] = (f"CASE WHEN fb.{baixa.lower()} IS NOT NULL THEN 'S' ELSE 'N' END"
                    if baixa else "")
    return m


def _sem_bloco(corpo: str) -> str:
    apelidos = _RE_APELIDO.findall(corpo)
    if not apelidos:
        return ""
    return "\n".join(f"       NULL AS {nome}{virgula}" for nome, virgula in apelidos) + "\n"


def _sem_duplicados(sql: str) -> str:
    """Campo com mais de uma variante no molde entra uma vez só."""
    linhas = sql.split("\n")
    nulos: dict[str, list[int]] = {}
    reais: set[str] = set()
    for n, linha in enumerate(linhas):
        vazio = _RE_NULO.match(linha)
        if vazio:
            nulos.setdefault(vazio.group(1).lower(), []).append(n)
            continue
        apelido = _RE_APELIDO.search(linha)
        if apelido:
            reais.add(apelido.group(1).lower())
    fora: set[int] = set()
    for apelido, posicoes in nulos.items():
        fora.update(posicoes if apelido in reais else posicoes[:-1])
    saida = "\n".join(linha for n, linha in enumerate(linhas) if n not in fora)
    return re.sub(r",\s*\n(FROM APPS\.)", r"\n\1", saida)


def _busca(m: dict, lista_binds: str) -> str:
    partes = [f"  SELECT fa0.asset_id FROM APPS.FA_ADDITIONS_B fa0"
              f"\n   WHERE fa0.{m[campo].split('.', 1)[1]} IN ({lista_binds})"
              for campo, _c in BUSCA if m.get(campo)]
    return "\n  UNION\n".join(partes)


def montar_sql(lista_binds: str, cat=None) -> str:
    """O SQL do molde preenchido com as colunas que a conta enxerga."""
    if cat is None:
        try:
            cat = catalogo()
        except Exception:  # noqa: BLE001
            cat = None
    if cat is not None and not cat.get("FA_ADDITIONS_B"):
        cat = None
    m = mapa(cat)
    if not _busca(m, ":x"):
        cat, m = None, mapa(None)
    presentes = None if cat is None else {f"{t}.{c}" for t, cols in cat.items() for c in cols}
    usa_tl = m.get("DESCRICAO", "").startswith("tl.")
    # Só com o catálogo confirmando a cadeia inteira: no escuro vale a PO sem
    # liberação, que é o que sempre funcionou.
    com_liberacao = presentes is not None and all(x in presentes for x in CADEIA_LIBERACAO)
    tem_po = presentes is None or "FA_ASSET_INVOICES.PO_NUMBER" in presentes
    decisao = {"USA_TL": usa_tl, "PO_LIBERACAO": com_liberacao,
               "PO_SIMPLES": tem_po and not com_liberacao}

    def manter(exigidos: list[str]) -> bool:
        if len(exigidos) == 1 and exigidos[0] in decisao:
            return decisao[exigidos[0]]
        return presentes is None or all(x in presentes for x in exigidos)

    sql = CONSULTA.read_text(encoding="utf-8")
    sql = _RE_BLOCO.sub(
        lambda b: (b.group(2)
                   if manter([x for x in b.group(1).upper().split("+") if x])
                   else _sem_bloco(b.group(2))),
        sql,
    )
    sql = re.sub(r"/\*([A-Z_]+)\*/[ \t]*",
                 lambda c: ((m.get(c.group(1)) or "NULL") + " ").ljust(46) if c.group(1) in m else c.group(0),
                 sql)
    return _sem_duplicados(sql.replace("/*BUSCA*/", _busca(m, lista_binds)))


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
