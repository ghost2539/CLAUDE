"""Controle de Orçamento do SPARE (CAPEX da área).

Banco PRÓPRIO (`db/orcamento_spare.py`), separado do `/controle-orcamento` e
do portal. Alimentado manualmente — não puxa do EBS.

ACESSO CONTROLADO: exige sessão do portal e o módulo `orcamento_spare`
liberado ao usuário. `view` para ler, `create` para incluir, `edit` para
alterar/excluir e `admin` para definir os campos.

Os campos adicionais ainda estão em definição. Enquanto isso, o núcleo fixo
(identificação, classificação, os quatro valores e as datas) já funciona, e
o administrador cadastra os campos extras em `/campos` — cada um vira um
input na tela e uma chave no mapa `dados` do projeto.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

import db.orcamento_spare as db
from core.security import check_rate_limit, require_permission

_log = logging.getLogger("orcamento_spare")

router = APIRouter(prefix="/api/orcamento-spare", tags=["Orçamento SPARE"],
                   include_in_schema=False)

MODULO = "orcamento_spare"


def _exigir(req: Request, acao: str) -> dict:
    db.ensure_db()
    return require_permission(req, MODULO, acao)


# ── Entrada ─────────────────────────────────────────────────────────────
class ProjetoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    numero: str = ""
    nome: str = ""
    tipo: str = "CAPEX"
    categoria: str = ""
    situacao: str = "Planejado"
    responsavel: str = ""
    aprovado: float = 0
    comprometido: float = 0
    realizado: float = 0
    a_realizar: float = 0
    inicio: Optional[str] = None
    fim: Optional[str] = None
    observacao: str = ""
    ordem: int = 0
    dados: dict[str, Any] = {}


class CampoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chave: str
    rotulo: str = ""
    tipo: str = "texto"
    opcoes: str = ""
    obrigatorio: bool = False
    ativo: bool = True
    ordem: int = 0


def _data(v: Optional[str]) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(422, f"Data inválida: {v}")


def _valida_extras(dados: dict) -> str:
    """Aceita só as chaves de campos ativos e cobra os obrigatórios."""
    definidos = {c["chave"]: c for c in db.listar_campos(somente_ativos=True)}
    limpo = {k: v for k, v in (dados or {}).items() if k in definidos}
    faltando = [c["rotulo"] or c["chave"] for c in definidos.values()
                if c["obrigatorio"] and not str(limpo.get(c["chave"], "")).strip()]
    if faltando:
        raise HTTPException(422, "Campo obrigatório em branco: " + ", ".join(faltando))
    return json.dumps(limpo, ensure_ascii=False)


def _aplica(p, body: ProjetoIn, usuario: str) -> None:
    if body.tipo not in db.TIPOS:
        raise HTTPException(422, f"Tipo inválido. Use um de: {', '.join(db.TIPOS)}.")
    if body.situacao not in db.SITUACOES:
        raise HTTPException(422, f"Situação inválida. Use uma de: {', '.join(db.SITUACOES)}.")
    p.numero = body.numero.strip()[:40]
    p.nome = body.nome.strip()[:200]
    p.tipo = body.tipo
    p.categoria = body.categoria.strip()[:60]
    p.situacao = body.situacao
    p.responsavel = body.responsavel.strip()[:120]
    p.aprovado = body.aprovado
    p.comprometido = body.comprometido
    p.realizado = body.realizado
    p.a_realizar = body.a_realizar
    p.inicio = _data(body.inicio)
    p.fim = _data(body.fim)
    p.observacao = body.observacao.strip()
    p.ordem = body.ordem
    p.dados = _valida_extras(body.dados)
    p.atualizado_por = usuario


# ── Projetos ────────────────────────────────────────────────────────────
@router.get("/projetos")
def listar(req: Request):
    _exigir(req, "view")
    return {
        "projetos": db.listar_projetos(),
        "totais": db.totais(),
        "campos": db.listar_campos(somente_ativos=True),
        "opcoes": {"tipos": list(db.TIPOS), "situacoes": list(db.SITUACOES)},
    }


@router.post("/projetos", status_code=201)
def criar(body: ProjetoIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        p = db.Projeto()
        _aplica(p, body, sd.get("username", ""))
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
        _aplica(p, body, sd.get("username", ""))
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


# ── Definição de campos (admin) ─────────────────────────────────────────
@router.get("/campos")
def campos_listar(req: Request):
    _exigir(req, "admin")
    return {"campos": db.listar_campos(), "tipos": list(db.TIPOS_CAMPO)}


@router.post("/campos", status_code=201)
def campos_criar(body: CampoIn, req: Request):
    _exigir(req, "admin")
    chave = body.chave.strip().lower().replace(" ", "_")[:40]
    if not chave:
        raise HTTPException(422, "Informe a chave do campo.")
    if body.tipo not in db.TIPOS_CAMPO:
        raise HTTPException(422, f"Tipo inválido. Use um de: {', '.join(db.TIPOS_CAMPO)}.")
    with db.SessionLocal.begin() as s:
        if s.scalar(db.select(db.Campo).where(db.Campo.chave == chave)):
            raise HTTPException(409, f"Já existe um campo com a chave '{chave}'.")
        c = db.Campo(chave=chave, rotulo=body.rotulo.strip()[:80] or chave,
                     tipo=body.tipo, opcoes=body.opcoes.strip(),
                     obrigatorio=body.obrigatorio, ativo=body.ativo, ordem=body.ordem)
        s.add(c)
        s.flush()
        return c.to_dict()


@router.put("/campos/{campo_id}")
def campos_atualizar(campo_id: int, body: CampoIn, req: Request):
    _exigir(req, "admin")
    if body.tipo not in db.TIPOS_CAMPO:
        raise HTTPException(422, f"Tipo inválido. Use um de: {', '.join(db.TIPOS_CAMPO)}.")
    with db.SessionLocal.begin() as s:
        c = s.get(db.Campo, campo_id)
        if not c:
            raise HTTPException(404, "Campo não encontrado.")
        # A chave não muda: ela é o nome usado dentro do mapa `dados` dos
        # projetos já gravados.
        c.rotulo = body.rotulo.strip()[:80] or c.chave
        c.tipo = body.tipo
        c.opcoes = body.opcoes.strip()
        c.obrigatorio = body.obrigatorio
        c.ativo = body.ativo
        c.ordem = body.ordem
        return c.to_dict()


@router.delete("/campos/{campo_id}")
def campos_excluir(campo_id: int, req: Request):
    """Remove a definição. O valor já gravado nos projetos permanece em
    `dados` (fica apenas invisível) — nada é perdido por engano."""
    _exigir(req, "admin")
    with db.SessionLocal.begin() as s:
        c = s.get(db.Campo, campo_id)
        if not c:
            raise HTTPException(404, "Campo não encontrado.")
        s.delete(c)
    return {"ok": True}
