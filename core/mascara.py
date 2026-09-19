from __future__ import annotations

import re

# O que se procura no texto, na ordem. São nomes de chave, não valores.
_CHAVES_ENDERECO = ("ORACLE_EBS_DSN", "EBS_ORACLE_DSN")
_CHAVES_USUARIO = ("ORACLE_EBS_USER", "EBS_ORACLE_USER")


_ESQUEMAS_BANCO = ("postgresql", "postgres", "sqlite", "mysql", "mariadb",
                   "oracle", "mssql", "db2", "cockroachdb")
_RE_URL_BANCO = re.compile(
    r"\b(?:" + "|".join(_ESQUEMAS_BANCO) + r")(?:\+[a-z0-9_]+)?://[^\s'\"<>,;)]*",
    re.IGNORECASE)


_MINIMO = 4


def _valores(chaves: tuple[str, ...]) -> list[str]:
    try:
        from core.cofre import obter
    except Exception:  # noqa: BLE001
        return []
    achados = []
    for nome in chaves:
        v = (obter(nome, "") or "").strip()
        if v:
            achados.append(v)
    return achados


def _url_do_portal() -> str:
    """DATABASE_URL como o processo a enxerga. Não vem do cofre: é config."""
    try:
        from config import get_settings
        return (get_settings().DATABASE_URL or "").strip()
    except Exception:  # noqa: BLE001 — sem config, não há o que mascarar
        return ""


def sem_dado_de_acesso(texto: str) -> str:
    """Devolve o texto com endereço, partes do endereço e usuário trocados."""
    saida = str(texto)
    
    saida = _RE_URL_BANCO.sub("<endereço do banco>", saida)
    
    url = _url_do_portal()
    if url:
        saida = saida.replace(url, "<endereço do banco>")
        for pedaco in re.split(r"[/:@,()= ?&]+", url):
            if len(pedaco) < _MINIMO or pedaco.lower() in _ESQUEMAS_BANCO:
                continue
            
            saida = re.sub(rf"\b{re.escape(pedaco)}\b", "<omitido>", saida)
    for dsn in _valores(_CHAVES_ENDERECO):
        saida = saida.replace(dsn, "<endereço do banco>")
        
        for pedaco in re.split(r"[/:@,()= ]+", dsn):
            if len(pedaco) >= _MINIMO:
                saida = saida.replace(pedaco, "<omitido>")
    for user in _valores(_CHAVES_USUARIO):
        if len(user) >= _MINIMO:
            saida = saida.replace(user, "<usuário>")
            saida = saida.replace(user.upper(), "<usuário>")
            saida = saida.replace(user.lower(), "<usuário>")
    return saida
