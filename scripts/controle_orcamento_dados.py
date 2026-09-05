#!/usr/bin/env python3
"""Exporta / importa os dados do Controle de Orçamento (/tv2) em JSON.

Funciona com qualquer banco (SQLite ou Postgres) e serve para backup e para
migração entre servidores. Usa a variável CONTROLE_ORCAMENTO_DATABASE_URL
(ou o arquivo SQLite padrão em data/controle_orcamento.db).

Uso (na pasta da aplicação):
    python scripts/controle_orcamento_dados.py exportar backup.json
    python scripts/controle_orcamento_dados.py importar backup.json --substituir

Sem --substituir a importação só é feita se o banco de destino estiver vazio.
Com --substituir, categorias e projetos existentes são apagados antes.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PORTAL_SESSION_SECRET", secrets.token_urlsafe(32))

from sqlalchemy import func, select  # noqa: E402

from db.orcamento import (  # noqa: E402
    BudgetCategory, BudgetProject, SessionLocal, criar_tabelas, url_efetiva,
)

CAMPOS_PROJETO = (
    "code", "name", "kind", "category", "area", "stage", "priority",
    "approved_budget", "committed", "realized", "due_date", "sort_order",
    "updated_by", "created_at", "updated_at",
)


def _json(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def exportar(caminho: str) -> None:
    criar_tabelas()
    with SessionLocal() as s:
        cats = s.scalars(select(BudgetCategory).order_by(BudgetCategory.sort_order, BudgetCategory.id)).all()
        projs = s.scalars(select(BudgetProject).order_by(BudgetProject.sort_order, BudgetProject.id)).all()
        dados = {
            "formato": "controle-orcamento/1",
            "exportado_em": datetime.now().isoformat(timespec="seconds"),
            "origem": url_efetiva().render_as_string(hide_password=True),
            "categorias": [{"nome": c.name, "cor": c.color, "ordem": c.sort_order} for c in cats],
            "projetos": [{k: _json(getattr(p, k)) for k in CAMPOS_PROJETO} for p in projs],
        }
    Path(caminho).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exportado: {len(dados['categorias'])} categoria(s), {len(dados['projetos'])} projeto(s) -> {caminho}")


def importar(caminho: str, substituir: bool) -> None:
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if dados.get("formato") != "controle-orcamento/1":
        sys.exit("Arquivo não reconhecido (esperado formato controle-orcamento/1).")
    criar_tabelas()
    with SessionLocal.begin() as s:
        n_cat = s.scalar(select(func.count()).select_from(BudgetCategory)) or 0
        n_proj = s.scalar(select(func.count()).select_from(BudgetProject)) or 0
        if (n_cat or n_proj) and not substituir:
            sys.exit(f"Destino já contém {n_cat} categoria(s) e {n_proj} projeto(s). "
                     "Use --substituir para apagar e importar por cima.")
        if substituir:
            for p in s.scalars(select(BudgetProject)).all():
                s.delete(p)
            for c in s.scalars(select(BudgetCategory)).all():
                s.delete(c)
            s.flush()
        for i, c in enumerate(dados.get("categorias", [])):
            s.add(BudgetCategory(name=c["nome"], color=c.get("cor") or "#9ca3af", sort_order=c.get("ordem") or i + 1))
        for i, p in enumerate(dados.get("projetos", [])):
            proj = BudgetProject(
                code=p.get("code") or "", name=p.get("name") or "", kind=p.get("kind") or "CAPEX",
                category=p.get("category") or "Outros", area=p.get("area") or "",
                stage=p.get("stage") or "Planejamento", priority=p.get("priority") or "Média",
                approved_budget=Decimal(str(p.get("approved_budget") or 0)),
                committed=Decimal(str(p.get("committed") or 0)),
                realized=Decimal(str(p.get("realized") or 0)),
                due_date=date.fromisoformat(p["due_date"]) if p.get("due_date") else None,
                sort_order=p.get("sort_order") or i + 1,
                updated_by=p.get("updated_by") or "importacao",
            )
            if p.get("created_at"):
                proj.created_at = datetime.fromisoformat(p["created_at"])
            if p.get("updated_at"):
                proj.updated_at = datetime.fromisoformat(p["updated_at"])
            s.add(proj)
    print(f"Importado: {len(dados.get('categorias', []))} categoria(s), {len(dados.get('projetos', []))} projeto(s) "
          f"em {url_efetiva().render_as_string(hide_password=True)}")


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[0] not in ("exportar", "importar"):
        sys.exit(__doc__)
    if argv[0] == "exportar":
        exportar(argv[1])
    else:
        importar(argv[1], "--substituir" in argv[2:])


if __name__ == "__main__":
    main(sys.argv[1:])
