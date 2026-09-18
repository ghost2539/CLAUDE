"""Orçamento Spare — Controle de Orçamento do SPARE. API /api/orcamento-spare.

Mestre-detalhe: projetos com linhas de item (Item EBS, descrição, quantidade,
valor unitário; o total da linha e o custo do projeto são calculados). Cada
projeto tem o total APROVADO puxado do EBS pelo número e a PARCELA desse
aprovado destinada ao Spare (informada à mão).

Banco próprio (`db/orcamento_spare.py`). Permissão pelo módulo
`orcamento_spare`: view lê, create inclui, edit altera/exclui, admin idem.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator

import db.orcamento_spare as db
from core import acordos as acordos_mod
from core import tipi
from core.security import check_rate_limit, client_ip, require_permission

_log = logging.getLogger("orcamento_spare")
MODULO = "orcamento_spare"

router = APIRouter(prefix="/api/orcamento-spare", tags=["Orçamento Spare"],
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
    acordo: bool = False
    acordo_numero: str = ""
    ncm: str = ""
    fase: str = ""
    solicitacao_compra: str = ""
    pedido_compra: str = ""
    entrega_status: str = "pendente"
    entregas: list[dict] = []

    @field_validator("item_ebs", "descricao_item", "acordo_numero", "ncm",
                     "fase", "solicitacao_compra", "pedido_compra")
    @classmethod
    def _txt(cls, v: str) -> str:
        return (v or "").strip()[:200]

    @field_validator("entrega_status")
    @classmethod
    def _entrega(cls, v: str) -> str:
        v = (v or "pendente").strip().lower()
        # 'parcial'/'entregue' são derivados da quantidade; entrada só pendente/agendado.
        return v if v in ("pendente", "agendado") else "pendente"

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
    bu: str = ""
    servico: str = ""
    categoria: str = ""
    aprovado_spare: float = 0
    observacao: str = ""
    itens: list[ItemIn] = []

    @field_validator("numero", "descricao", "bu", "servico", "categoria")
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
    p.bu = body.bu
    p.servico = body.servico
    p.categoria = body.categoria
    p.aprovado_spare = body.aprovado_spare
    p.observacao = (body.observacao or "").strip()
    p.itens.clear()
    for i, it in enumerate(body.itens):
        if not (it.item_ebs or it.descricao_item or it.quantidade
                or it.valor_unitario or it.imposto_percent or it.ncm):
            continue  # linha em branco: ignora
        # Imposto vem do NCM (TIPI). Sem NCM válido, usa o informado.
        info = tipi.consultar(it.ncm) if it.ncm else None
        imp = info["aliquota"] if (info and info["encontrado"] and info["aliquota"] is not None) else it.imposto_percent
        ncm_fmt = info["ncm"] if info else (it.ncm or "")
        # Sem acordo, o preço é obrigatório.
        if not it.acordo and float(it.valor_unitario or 0) <= 0:
            nome = it.item_ebs or it.descricao_item or ("item " + str(i + 1))
            raise HTTPException(422, f"Informe o preço do item '{nome}' (não é de acordo de compras).")
        pedido = (it.pedido_compra or "").strip()
        # Entregas (parciais, cada uma com sua NF). Só valem com pedido de compra.
        entregas = []
        qent = 0.0
        if pedido:
            for ent in (it.entregas or []):
                try:
                    qe = float(ent.get("quantidade") or 0)
                except (TypeError, ValueError):
                    qe = 0.0
                nfe = str(ent.get("nf") or "").strip()[:60]
                data = str(ent.get("data") or "").strip()[:10]
                if qe <= 0 and not nfe:
                    continue
                entregas.append({"quantidade": qe, "nf": nfe, "data": data})
                qent += qe
        qtd = float(it.quantidade or 0)
        if qtd > 0:
            qent = min(qent, qtd)
        # Status derivado da quantidade entregue; senão, o informado (pendente/agendado).
        if qtd > 0 and qent >= qtd:
            entrega = "entregue"
        elif qent > 0:
            entrega = "parcial"
        else:
            entrega = it.entrega_status if pedido else "pendente"
        p.itens.append(db.Item(
            item_ebs=it.item_ebs, descricao_item=it.descricao_item,
            quantidade=it.quantidade, valor_unitario=it.valor_unitario,
            imposto_percent=(imp or 0),
            acordo=bool(it.acordo), acordo_numero=(it.acordo_numero if it.acordo else ""),
            ncm=ncm_fmt, fase=it.fase,
            solicitacao_compra=it.solicitacao_compra, pedido_compra=pedido,
            entrega_status=entrega, quantidade_entregue=qent,
            entregas=json.dumps(entregas, ensure_ascii=False),
            recebido=(entrega == "entregue"),
            ordem=i))


# ── Catálogo de itens (cadastro) ───────────────────────────────────────────
class CatalogoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    item_ebs: str = ""
    descricao_item: str = ""
    ncm: str = ""
    acordo: bool = False
    acordo_numero: str = ""
    acordo_bu: str = ""
    preco_acordo: float = 0

    @field_validator("item_ebs", "descricao_item", "acordo_numero", "acordo_bu")
    @classmethod
    def _txt(cls, v: str) -> str:
        return (v or "").strip()[:200]

    @field_validator("preco_acordo")
    @classmethod
    def _preco(cls, v):
        try:
            v = float(v or 0)
        except (TypeError, ValueError):
            raise ValueError("Preço de acordo inválido.")
        if v < 0:
            raise ValueError("O preço de acordo não pode ser negativo.")
        return v


def _aplica_catalogo(c: "db.Catalogo", body: CatalogoIn) -> None:
    info = tipi.consultar(body.ncm)
    c.item_ebs = body.item_ebs
    c.descricao_item = body.descricao_item
    c.ncm = info["ncm"]
    c.aliquota = (None if info["aliquota"] is None else info["aliquota"])
    c.nt = bool(info["nt"])
    c.acordo = bool(body.acordo)
    c.acordo_numero = body.acordo_numero if body.acordo else ""
    c.acordo_bu = body.acordo_bu if body.acordo else ""
    c.preco_acordo = body.preco_acordo if body.acordo else 0


# ── Rotas ─────────────────────────────────────────────────────────────────
@router.get("/projetos")
def listar(req: Request):
    _exigir(req, "view")
    return {"projetos": db.listar_projetos(), "totais": db.totais()}


@router.get("/ncm")
def consultar_ncm(req: Request, codigo: str = ""):
    """Formata o NCM (XXXX.XX.XX) e devolve a alíquota da TIPI (nt = não tributado)."""
    _exigir(req, "view")
    return tipi.consultar(codigo)


@router.get("/catalogo")
def catalogo_listar(req: Request):
    _exigir(req, "view")
    return {"itens": db.listar_catalogo()}


@router.get("/catalogo/item")
def catalogo_item(req: Request, item_ebs: str = "", bu: str = ""):
    """Item do catálogo pelo Item EBS — para preencher a linha do projeto.
    Com a BU do projeto, prefere o acordo daquela BU."""
    _exigir(req, "view")
    return db.buscar_catalogo(item_ebs, bu) or {}


def _bu_canonica(bu: str) -> str:
    """Normaliza a BU da planilha (ex.: UO_CAMICADO) para o nome padrão."""
    t = (bu or "").strip()
    limpo = t.upper().replace("UO_", "").replace("_", " ").strip()
    for b in db.BUS:
        if b.upper() == limpo or b.upper() == t.upper():
            return b
    return t.title() if t else ""


@router.post("/catalogo/importar")
async def catalogo_importar(req: Request, arquivo: UploadFile = File(...)):
    """Importa a planilha de Acordos: cria/atualiza itens de acordo por
    Item EBS + BU, sem duplicar. NCM traz a alíquota da TIPI."""
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    try:
        dados = await arquivo.read()
        linhas = acordos_mod.ler_acordos(dados)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Não consegui ler a planilha: {exc}")
    if not linhas:
        return {"criados": 0, "atualizados": 0, "total": 0,
                "aviso": "Nenhum acordo encontrado na planilha."}
    criados = atualizados = 0
    usuario = sd.get("username", "")
    with db.SessionLocal.begin() as s:
        for r in linhas:
            bu = _bu_canonica(r.get("bu", ""))
            item_ebs = (r.get("item_ebs") or "").strip()
            info = tipi.consultar(r.get("ncm", ""))
            existente = None
            if item_ebs:
                existente = s.scalars(db.select(db.Catalogo).where(
                    db.func.lower(db.Catalogo.item_ebs) == item_ebs.lower(),
                    db.func.lower(db.Catalogo.acordo_bu) == bu.lower())).first()
            c = existente or db.Catalogo()
            c.item_ebs = item_ebs
            c.descricao_item = r.get("descricao_item", "")
            c.ncm = info["ncm"] if info["ncm"] else (r.get("ncm", "") or "")
            c.aliquota = (None if info["aliquota"] is None else info["aliquota"])
            c.nt = bool(info["nt"])
            c.acordo = True
            c.acordo_numero = r.get("acordo_numero", "")
            c.acordo_bu = bu
            c.preco_acordo = r.get("preco_acordo", 0) or 0
            c.fornecedor = r.get("fornecedor", "")
            c.vencimento = r.get("vencimento", "")
            c.atualizado_por = usuario
            if existente:
                atualizados += 1
            else:
                s.add(c)
                criados += 1
    _log.info("orcamento_spare importar acordos: %s criados, %s atualizados por=%s",
              criados, atualizados, usuario)
    return {"criados": criados, "atualizados": atualizados, "total": len(linhas)}


@router.post("/catalogo", status_code=201)
def catalogo_criar(body: CatalogoIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    if not body.item_ebs:
        raise HTTPException(422, "Informe o Item EBS.")
    with db.SessionLocal.begin() as s:
        c = db.Catalogo(atualizado_por=sd.get("username", ""))
        _aplica_catalogo(c, body)
        s.add(c)
        s.flush()
        return c.to_dict()


@router.put("/catalogo/{cid}")
def catalogo_atualizar(cid: int, body: CatalogoIn, req: Request):
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    if not body.item_ebs:
        raise HTTPException(422, "Informe o Item EBS.")
    with db.SessionLocal.begin() as s:
        c = s.get(db.Catalogo, cid)
        if not c:
            raise HTTPException(404, "Item não encontrado.")
        _aplica_catalogo(c, body)
        c.atualizado_por = sd.get("username", "")
        return c.to_dict()


@router.delete("/catalogo/{cid}")
def catalogo_excluir(cid: int, req: Request):
    _exigir(req, "edit")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        c = s.get(db.Catalogo, cid)
        if not c:
            raise HTTPException(404, "Item não encontrado.")
        s.delete(c)
    return {"ok": True}


@router.get("/item-acordo")
def item_acordo(req: Request, item_ebs: str = "", acordo: str = ""):
    """Dados de um item DE ACORDO já cadastrado (descrição, valor unit., %
    imposto), para reaproveitar ao lançar o mesmo item em outro projeto."""
    _exigir(req, "view")
    return db.buscar_item_acordo(item_ebs, acordo) or {}


@router.get("/itens")
def listar_itens(req: Request):
    """Itens de todos os projetos (achatados) + totais por situação — a visão
    de quais itens consomem cada projeto e do previsto/andamento/executado."""
    _exigir(req, "view")
    return {
        "itens": db.listar_itens(),
        "totais_status": db.totais_por_status(),
        "status": [{"valor": s, "rotulo": db.STATUS_ROTULO.get(s, s)} for s in db.STATUS_ITEM],
    }


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
