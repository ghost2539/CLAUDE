#!/usr/bin/env python3
"""Indicadores: a tela abre atrás do proxy e os números saem em planilha.

    python3 scripts/verificar_indicadores.py

Roda contra um SQLite temporário, sem rede e sem ServiceNow.

O que se confere
----------------
1. `/indicadores/` (com barra) responde. A função da barra chamava a outra
   sem o argumento `req` e estourava TypeError -> 500. A barra vem de graça
   quando o portal é publicado atrás de prefixo, então isto aparecia lá e
   não aqui.
2. O JavaScript da tela usa o prefixo (`<meta name="app-base">`). Com
   `/api/...` absoluto, a tela não carregava nada num portal publicado em
   /portal-spare — e era assim que estava.
3. A exportação exige sessão, recusa com recado quando não há snapshot, e
   devolve uma planilha com as abas e os números do snapshot.
"""
from __future__ import annotations

import asyncio
import io as _io
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["INDICADORES_DATABASE_URL"] = f"sqlite:///{_TMP/'ind.db'}"
os.environ["SN_INDIC_REFRESH_MIN"] = "0"   # sem agendador na verificação

from fastapi import HTTPException  # noqa: E402

import db.indicadores as dbi  # noqa: E402
import routers.indicadores as ri  # noqa: E402

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


class Req:
    headers: dict = {}
    client = None
    scope: dict = {"root_path": ""}


print("[1] A página abre com e sem barra final")
import inspect  # noqa: E402

for nome in ("indicadores_page", "indicadores_page_slash"):
    fn = getattr(ri, nome)
    args = list(inspect.signature(fn).parameters)
    checar("req" in args, f"{nome} recebe req ({args})")

print("\n[2] O JavaScript da tela respeita o prefixo do proxy")
app_js = (RAIZ / "static" / "indicadores" / "app.js").read_text(encoding="utf-8")
checar('meta[name="app-base"]' in app_js, "lê <meta name=\"app-base\">")
import re  # noqa: E402

absolutas = re.findall(r'fetch\(\s*"(/api/[^"]*)"', app_js)
checar(not absolutas, f"nenhum fetch com /api absoluto ({absolutas or 'nenhum'})")
checar(app_js.count('fetch(url(') >= 2, "as chamadas passam por url()")

print("\n[3] Exportar sem snapshot: recado, não erro cru")
dbi.init_db()
ri.get_session = lambda req: {"username": "verificacao"}
try:
    ri.indicadores_exportar(Req())
    checar(False, "deveria recusar quando não há snapshot")
except HTTPException as exc:
    checar(exc.status_code == 404 and "Atualizar" in exc.detail,
           f"404 dizendo o que fazer ({exc.detail[:44]}…)")

print("\n[4] Exportar com snapshot: planilha com os números da tela")
MESES = ["2026-08", "2026-09"]
DADOS = {
    "meses": MESES,
    "kpis": {"backlog": 143, "ritms": 52, "ag_atendimento": 97, "priorizados": 8},
    "tratado_por_mes": [{"mes": "2026-08", "total": 451}, {"mes": "2026-09", "total": 398}],
    "backlog_por_mes": [{"mes": "2026-08", "total": 139}, {"mes": "2026-09", "total": 143}],
    "sla": {"compliance_pct": 94.2,
            "por_mes": [{"mes": "2026-08", "pct": 93.7, "dentro": 422, "total": 451},
                        {"mes": "2026-09", "pct": 94.2, "dentro": 375, "total": 398}]},
    "abertos_por_status": [{"nome": "Novo", "total": 41}],
    "por_localidade": [{"nome": "CD Palhoça", "total": 88}],
    "por_bu": [{"nome": "Renner", "total": 101}, {"nome": "Camicado", "total": 27}],
    "por_subcategoria": [{"nome": "Coletor", "total": 77}],
    "sled_por_mes": [{"mes": "2026-08", "total": 35}, {"mes": "2026-09", "total": 30}],
    "coletor_por_mes": [{"mes": "2026-08", "total": 80}, {"mes": "2026-09", "total": 74}],
    "erros": {},
}
dbi.salvar_snapshot("2026-09", DADOS, criado_por="verificacao")


async def _corpo(resp):
    partes = []
    async for pedaco in resp.body_iterator:
        partes.append(pedaco if isinstance(pedaco, bytes) else pedaco.encode())
    return b"".join(partes)


resp = ri.indicadores_exportar(Req())
bruto = asyncio.run(_corpo(resp))
checar(len(bruto) > 4000, f"a planilha tem conteúdo ({len(bruto)} bytes)")
checar("indicadores_202609.xlsx" in resp.headers.get("content-disposition", ""),
       f"o arquivo leva o mês no nome ({resp.headers.get('content-disposition', '')[-28:]})")

from openpyxl import load_workbook  # noqa: E402

wb = load_workbook(_io.BytesIO(bruto))
checar(wb.sheetnames == ["Resumo", "Mensal", "Por status", "Por localidade",
                         "Por BU", "Por subcategoria"],
       f"as abas da reunião ({wb.sheetnames})")

resumo = {r[0]: r[1] for r in wb["Resumo"].iter_rows(values_only=True) if r and r[0]}
checar(resumo.get("Mês de referência") == "2026-09", "o Resumo diz o mês")
checar(resumo.get("Snapshot gerado em") not in (None, "—"),
       f"e de quando é o snapshot ({resumo.get('Snapshot gerado em')})")
checar(resumo.get("Backlog (abertos)") == 143, "KPI backlog bate com o snapshot")
checar(resumo.get("SLA — cumprimento (%)") == 94.2, "SLA bate com o snapshot")
checar(resumo.get("SLED — total no período") == 65,
       f"SLED soma os meses (35+30={resumo.get('SLED — total no período')})")

mensal = list(wb["Mensal"].iter_rows(values_only=True))
checar(mensal[0] == ("Mês", "Tratados", "Backlog", "SLA dentro", "SLA total",
                     "SLA %", "SLED", "Coletores"),
       "a aba Mensal tem as colunas da reunião")
checar(mensal[1][0] == "ago/26" and mensal[2][0] == "set/26",
       f"mês legível e em ordem ({[l[0] for l in mensal[1:]]})")
checar(mensal[2][1:] == (398, 143, 375, 398, 94.2, 30, 74),
       f"a linha de set/26 cruza todas as séries ({mensal[2][1:]})")

bu = list(wb["Por BU"].iter_rows(values_only=True))
checar(bu[0] == ("BU", "Total"), f"o cabeçalho é BU, não 'Bu' ({bu[0]})")
checar(bu[1] == ("Renner", 101), f"e os dados vêm do snapshot ({bu[1]})")

print("\n[5] Exportar exige sessão")
def _sem_sessao(req):
    raise HTTPException(401, "Sessão expirada.")


ri.get_session = _sem_sessao
try:
    ri.indicadores_exportar(Req())
    checar(False, "sem sessão, a exportação deveria ser recusada")
except HTTPException as exc:
    checar(exc.status_code == 401, f"sem sessão não baixa ({exc.status_code})")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Indicadores com problema:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("Indicadores íntegros: tela abre atrás do proxy e exporta o snapshot.")
