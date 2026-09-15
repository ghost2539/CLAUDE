"""CAPEX Spare — Controle de Orçamento do SPARE. API /api/orcamento-spare.

Mestre-detalhe: projetos com linhas de item (Item EBS, descrição, quantidade,
valor unitário; o total da linha e o custo do projeto são calculados). Cada
projeto tem o total APROVADO puxado do EBS pelo número e a PARCELA desse
aprovado destinada ao Spare (informada à mão).

Banco próprio (`db/orcamento_spare.py`). Permissão pelo módulo
`orcamento_spare`: view lê, create inclui, edit altera/exclui, admin idem.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

import db.orcamento_spare as db
from core.security import check_rate_limit, client_ip, require_permission

_log = logging.getLogger("orcamento_spare")
MODULO = "orcamento_spare"

router = APIRouter(prefix="/api/orcamento-spare", tags=["CAPEX Spare"],
                   include_in_schema=False)


def _exigir(req: Request, acao: str) -> dict:
    return require_permission(req, MODULO, acao)


# ── Entrada validada ──────────────────────────────────────────────────────
class ItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    item_ebs: str = ""
    descricao_item: str = ""
    quantidade: float = 0
    valor_unitario: float = 0
    imposto_percent: float = 0

    @field_validator("item_ebs", "descricao_item")
    @classmethod
    def _txt(cls, v: str) -> str:
        return (v or "").strip()[:200]

    @field_validator("quantidade", "valor_unitario", "imposto_percent")
    @classmethod
    def _naoneg(cls, v):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            raise ValueError("Valor numérico inválido.")
        if v < 0:
            raise ValueError("Valores não podem ser negativos.")
        return v


class ProjetoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    numero: str = ""
    descricao: str = ""
    servico: str = ""
    categoria: str = ""
    aprovado_spare: float = 0
    observacao: str = ""
    itens: list[ItemIn] = []

    @field_validator("numero", "descricao", "servico", "categoria")
    @classmethod
    def _txt(cls, v: str) -> str:
        return (v or "").strip()[:200]

    @field_validator("aprovado_spare")
    @classmethod
    def _valor(cls, v):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            raise ValueError("Valor destinado ao Spare inválido.")
        if v < 0:
            raise ValueError("O valor destinado ao Spare não pode ser negativo.")
        return v


def _aplica(p: "db.Projeto", body: ProjetoIn) -> None:
    p.numero = body.numero
    p.descricao = body.descricao
    p.servico = body.servico
    p.categoria = body.categoria
    p.aprovado_spare = body.aprovado_spare
    p.observacao = (body.observacao or "").strip()
    p.itens.clear()
    for i, it in enumerate(body.itens):
        if not (it.item_ebs or it.descricao_item or it.quantidade
                or it.valor_unitario or it.imposto_percent):
            continue  # linha em branco: ignora
        p.itens.append(db.Item(
            item_ebs=it.item_ebs, descricao_item=it.descricao_item,
            quantidade=it.quantidade, valor_unitario=it.valor_unitario,
            imposto_percent=it.imposto_percent, ordem=i))


# ── Rotas ─────────────────────────────────────────────────────────────────
@router.get("/projetos")
def listar(req: Request):
    _exigir(req, "view")
    return {"projetos": db.listar_projetos(), "totais": db.totais()}


@router.post("/projetos", status_code=201)
def criar(body: ProjetoIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        p = db.Projeto(atualizado_por=sd.get("username", ""))
        _aplica(p, body)
        s.add(p)
        s.flush()
        return p.to_dict()


@router.put("/projetos/{projeto_id}")
def atualizar(projeto_id: int, body: ProjetoIn, req: Request):
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        p = s.get(db.Projeto, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        _aplica(p, body)
        p.atualizado_por = sd.get("username", "")
        return p.to_dict()


@router.delete("/projetos/{projeto_id}")
def excluir(projeto_id: int, req: Request):
    _exigir(req, "edit")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        p = s.get(db.Projeto, projeto_id)
        if not p:
            raise HTTPException(404, "Projeto não encontrado.")
        s.delete(p)
    return {"ok": True}


@router.post("/sincronizar")
def sincronizar(req: Request):
    """Puxa o total aprovado do EBS (por número do projeto) e grava em
    aprovado_ebs. Reaproveita a MESMA integração do controle-orçamento.
    Não zera nada: projeto sem retorno no EBS fica como está."""
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    with db.SessionLocal() as s:
        numeros = [n for n in (p.numero.strip() for p in s.scalars(
            db.select(db.Projeto)).all()) if n]
    if not numeros:
        return {"atualizados": 0, "aviso": "Nenhum projeto com número para sincronizar."}
    try:
        from routers.controle_orcamento_exec import _ebs_capex, _valor
        ebs = _ebs_capex(numeros)
    except ValueError as exc:
        raise HTTPException(502, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Falha ao consultar o EBS: {exc}")
    if not ebs:
        return {"atualizados": 0, "aviso": "O EBS não retornou dados; nada foi alterado."}
    atualizados = 0
    nao_encontrados: list[str] = []
    with db.SessionLocal.begin() as s:
        for p in s.scalars(db.select(db.Projeto)).all():
            linha = ebs.get((p.numero or "").strip())
            if not linha:
                if p.numero:
                    nao_encontrados.append(p.numero)
                continue
            if "saldo_inicial" in linha and linha.get("saldo_inicial") not in (None, ""):
                p.aprovado_ebs = float(_valor(linha.get("saldo_inicial")))
                p.ebs_sincronizado_em = db.utcnow()
                atualizados += 1
    _log.info("orcamento_spare sincronizar: %s atualizados por=%s ip=%s",
              atualizados, sd.get("username", ""), client_ip(req))
    aviso = ("Não encontrado(s) no EBS: " + ", ".join(nao_encontrados)) if nao_encontrados else ""
    return {"atualizados": atualizados, "aviso": aviso}
