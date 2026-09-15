"""Consulta à TIPI (Tabela de Incidência do IPI) por NCM.

A tabela vem do arquivo `core/tipi_ncm.json` (gerado do XLSX oficial do
governo): { "84713011": 15.0, "01012100": "NT", ... } — chave é o NCM com 8
dígitos (sem pontos); valor é a alíquota em % ou "NT" (não tributado).

Uso: `consultar("84713011")` → {"ncm": "8471.30.11", "encontrado": True,
"nt": False, "aliquota": 15.0}. Formata o NCM para XXXX.XX.XX.
"""
from __future__ import annotations

import json
import os
import re

_PATH = os.path.join(os.path.dirname(__file__), "tipi_ncm.json")
_tab: dict | None = None


def _load() -> dict:
    global _tab
    if _tab is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                _tab = json.load(f)
        except Exception:  # noqa: BLE001 — sem tabela, consulta devolve não encontrado
            _tab = {}
    return _tab


def normalizar(txt) -> str:
    """Só os dígitos do NCM, no máximo 8 (XXXXXXXX)."""
    return re.sub(r"\D", "", str(txt or ""))[:8]


def formatar(txt) -> str:
    """XXXXXXXX → XXXX.XX.XX. Com menos de 8 dígitos, devolve como veio (limpo)."""
    d = normalizar(txt)
    if len(d) == 8:
        return d[0:4] + "." + d[4:6] + "." + d[6:8]
    return str(txt or "").strip()


def consultar(ncm) -> dict:
    """Alíquota do NCM na TIPI. nt=True quando 'NT' (não tributado, alíquota 0)."""
    d = normalizar(ncm)
    base = {"ncm": formatar(ncm), "encontrado": False, "nt": False, "aliquota": None}
    if len(d) != 8:
        return base
    v = _load().get(d)
    if v is None:
        base["ncm"] = formatar(d)
        return base
    if isinstance(v, str) and v.strip().upper() == "NT":
        return {"ncm": formatar(d), "encontrado": True, "nt": True, "aliquota": 0.0}
    try:
        al = float(v)
    except (TypeError, ValueError):
        al = None
    return {"ncm": formatar(d), "encontrado": True, "nt": False, "aliquota": al}
