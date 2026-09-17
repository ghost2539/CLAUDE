"""Orçamento Spare — a tela do Controle de Orçamento (Infra CSC) para a área.

Mesmo desenho, mesma tela, mesmo código: CAPEX e OPEX do SPARE, com banco
próprio (`ORCAMENTO_SPARE_EXEC_DATABASE_URL`) e permissão própria
(`orcamento_spare`, liberada por login na própria tela como no Infra CSC).

O módulo original não é uma fábrica — router, banco e caminhos são globais
de módulo. Em vez de copiar 1.500 linhas (e corrigir bug duas vezes), este
arquivo carrega o MESMO fonte uma segunda vez, sob outro nome, trocando só
o que identifica a instância: o atributo da URL do banco, o nome do módulo
de permissão e os caminhos de página e API. Qualquer correção no original
vale para os dois no próximo boot.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

_log = logging.getLogger("orcamento_spare_exec")
_RAIZ = Path(__file__).resolve().parent.parent

ROTA = "/orcamento-spare"
API = "/api/orcamento-spare-exec"
MODULO = "orcamento_spare"
TITULO = "Orçamento Spare"


def _carregar(nome: str, arquivo: Path, trocas: list[tuple[str, str]]) -> types.ModuleType:
    fonte = arquivo.read_text(encoding="utf-8")
    for velho, novo in trocas:
        if velho not in fonte:
            raise RuntimeError(f"{arquivo.name}: âncora não encontrada: {velho!r}")
        fonte = fonte.replace(velho, novo)
    spec = importlib.util.spec_from_loader(nome, loader=None, origin=str(arquivo))
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(arquivo)
    sys.modules[nome] = mod
    exec(compile(fonte, str(arquivo), "exec"), mod.__dict__)
    return mod


# 1) O banco: mesma estrutura, outro arquivo.
db = _carregar("db.orcamento_spare_exec", _RAIZ / "db" / "orcamento_exec.py", [
    ('"ORCAMENTO_EXEC_DATABASE_URL"', '"ORCAMENTO_SPARE_EXEC_DATABASE_URL"'),
    ('"data" / "controle_orcamento_exec.db"', '"data" / "orcamento_spare_exec.db"'),
    ('getLogger("controle_orcamento_exec")', 'getLogger("orcamento_spare_exec")'),
])

# 2) O router: mesmo código apontando para o banco, a permissão e os
#    caminhos desta instância.
_rt = _carregar("routers._orcamento_spare_exec_base", _RAIZ / "routers" / "controle_orcamento_exec.py", [
    ("from db.orcamento_exec import", "from db.orcamento_spare_exec import"),
    ('MODULO = "orcamento"', f'MODULO = "{MODULO}"'),
    ('"/controle-orcamento"', f'"{ROTA}"'),
    ('"/controle-orçamento"', f'"{ROTA}/"'),
    ('"/api/controle-orcamento-exec', f'"{API}'),
    ('getLogger("controle_orcamento_exec")', 'getLogger("orcamento_spare_exec")'),
])

router: APIRouter = _rt.router

# 3) A página e o bundle: o front é o mesmo arquivo, mas precisa falar com
#    esta API e mostrar este título. O bundle referencia a API por um único
#    literal, trocado ao servir.
_DIR = _rt._DIR


def _pagina() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    html = (html.replace("{{v}}", _rt._asset_version())
                .replace("Controle de Orçamento - Infra CSC", TITULO)
                .replace('/static/controle-orcamento-exec/app.js', f'{ROTA}/app.js'))
    return HTMLResponse(html)


# O clone registrou "/orcamento-spare" com a página original; sobrescreve-se
# a função de página que ele chama, para não duplicar rota.
_rt._page = _pagina


@router.get(f"{ROTA}/app.js", include_in_schema=False)
def bundle(req: Request):
    js = (_DIR / "app.js").read_text(encoding="utf-8")
    js = (js.replace("/api/controle-orcamento-exec", API)
            .replace("controle-orcamento-exec", "orcamento-spare-exec")
            .replace("instancia:csc", "instancia:spare")
            # O esbuild grava o "ç" como \xE7 no bundle.
            .replace('"Controle de Orçamento"', f'"{TITULO}"')
            .replace('"Controle de Or\\xE7amento"', f'"{TITULO}"'))
    return Response(js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})


def init_db() -> None:
    db.ensure_db() if hasattr(db, "ensure_db") else None
