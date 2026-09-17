"""Sessões HTTP de saída — TLS verificado por padrão.

Toda chamada a sistema externo (OAM/ServiceNow, EBS, MDM, Correios, câmbio)
passa por aqui. A verificação do certificado é a regra; desligá-la é decisão
explícita (`VERIFY_SSL=false`), registrada em log na primeira chamada de cada
alvo — nunca um `verify=False` escondido no código.

Com proxy corporativo que intercepta o TLS, a cadeia apresentada é a do
proxy: aponte `PORTAL_CA_BUNDLE` para um PEM com a CA corporativa (e a do
proxy) e a verificação continua ligada.
"""
from __future__ import annotations

import logging
import os
import warnings

import requests

from config import get_settings

_cfg = get_settings()
_log = logging.getLogger("http")
_avisados: set[str] = set()

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _avisar_uma_vez(alvo: str, msg: str) -> None:
    if alvo not in _avisados:
        _avisados.add(alvo)
        _log.warning(msg, alvo)


def verificacao_tls(alvo: str = "", explicito: str | None = None) -> bool | str:
    """O que vai em `verify`: caminho da CA corporativa, `True`, ou `False`.

    `explicito` ("true"/"false") é a política própria de uma integração
    (ex.: EBS_CAPEX_VERIFY); vazio ou None segue a política geral.
    """
    valor = (explicito or "").strip().lower()
    if valor in ("false", "0", "nao", "não", "off"):
        ligado = False
    elif valor in ("true", "1", "sim", "on"):
        ligado = True
    else:
        ligado = _cfg.VERIFY_SSL
    if not ligado:
        _avisar_uma_vez(alvo or "-", "TLS SEM verificação de certificado para %s (VERIFY_SSL=false).")
        try:
            import urllib3
            warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
        except Exception:  # noqa: BLE001 — sem urllib3 não há aviso a silenciar
            pass
        return False
    ca = _cfg.CA_BUNDLE
    if ca:
        if os.path.isfile(ca):
            return ca
        _avisar_uma_vez(f"ca:{ca}", "PORTAL_CA_BUNDLE aponta para arquivo inexistente (%s); usando a CA padrão.")
    return True


def sessao(alvo: str = "", proxy: str | None = None, *, verify: bool | str | None = None,
           explicito: str | None = None, trust_env: bool = True,
           user_agent: str = USER_AGENT) -> requests.Session:
    """`requests.Session` já com TLS, proxy e User-Agent definidos."""
    s = requests.Session()
    s.verify = verificacao_tls(alvo, explicito) if verify is None else verify
    s.trust_env = trust_env
    if proxy:
        s.proxies = {"https": proxy, "http": proxy}
    if user_agent:
        s.headers["User-Agent"] = user_agent
    return s
