"""Consulta de ativos direto na base do EBS: por número de série, etiqueta ou imobilizado."""
from __future__ import annotations

from pathlib import Path

CONSULTA = Path(__file__).resolve().parent.parent / "consultas" / "ebs" / "ativo_consulta.sql"
LIMITE = 100
EMPRESAS = ("RENNER", "YOUCOM", "CAMICADO")


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
        "baixado": "Sim" if str(r.get("baixado") or "N").upper() == "S" else "Não",
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


def _lote(ids: list[str]) -> list[dict]:
    from integracoes import ebs_oracle
    valores: list[str] = []
    for i in ids:
        for v in (i, i.upper()):
            if v not in valores:
                valores.append(v)
    binds = {f"t{n}": v for n, v in enumerate(valores)}
    lista = ", ".join(f":{k}" for k in binds)
    sql = CONSULTA.read_text(encoding="utf-8").replace("/*TERMOS*/", lista)
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
