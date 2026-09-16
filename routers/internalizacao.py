"""Internalização (menu Entrada) — API /api/internalizacao.

Recebe os agendamentos cujo recebimento foi confirmado (status RECEBIDO) e,
para cada equipamento fisicamente recebido, registra: item do EBS, descrição,
plaqueta (nº do bem), número de série e a NF. Grava em banco próprio
(`db/internalizacao.py`) e exporta a planilha "Placa Patrimonial" no mesmo
layout do modelo oficial.

Lê o banco dos Agendamentos apenas para LISTAR os recebidos e tirar o
snapshot do processo; nunca escreve nele.

Permissão pelo módulo "internalizacao": view lê, create/edit lançam e salvam,
export exporta, admin exclui.
"""
from __future__ import annotations

import io
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

import db.internalizacao as db
import db.agendamentos_forn as agf_db
from core.security import client_ip, require_permission

_log = logging.getLogger("internalizacao")
MODULO = "internalizacao"

router = APIRouter(prefix="/api/internalizacao", tags=["Internalização"])

_XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet")


# ── Entrada validada ──────────────────────────────────────────────────────
class AtivoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ebs_item: str = ""
    descricao: str = ""
    plaqueta: str = ""
    numero_serie: str = ""

    @field_validator("ebs_item", "descricao", "plaqueta", "numero_serie")
    @classmethod
    def _limpa(cls, v: str) -> str:
        return (v or "").strip()[:200]


class SalvarIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ativos: list[AtivoIn] = []
    concluir: bool = False


def _exigir(req: Request, acao: str) -> dict:
    return require_permission(req, MODULO, acao)


def _garantir_processo(s, agendamento_id: int):
    """Retorna o Processo do agendamento, criando-o (com snapshot) se preciso.
    O snapshot vem do banco dos Agendamentos e só é lido para copiar os dados."""
    from sqlalchemy import select
    proc = s.scalar(select(db.Processo).where(
        db.Processo.agendamento_id == agendamento_id))
    if proc:
        return proc
    agf_db.ensure_db()
    with agf_db.SessionLocal() as sa:
        ag = sa.get(agf_db.Agendamento, agendamento_id)
        if not ag:
            raise HTTPException(404, "Agendamento não encontrado.")
        if ag.status != "RECEBIDO":
            raise HTTPException(409, "O recebimento deste agendamento ainda não "
                                     "foi confirmado.")
        dados = ag.to_dict()
    proc = db.Processo(
        agendamento_id=agendamento_id,
        bu=dados["bu"], fornecedor=dados["fornecedor"], nf=dados["nf"],
        estoque_destino=dados["estoque_destino"],
        estoque_destino_rotulo=dados["estoque_destino_rotulo"],
        data_recebimento=(date.fromisoformat(dados["data_recebimento"])
                          if dados["data_recebimento"] else None),
        status="PENDENTE",
    )
    s.add(proc)
    s.flush()
    return proc


# ── Rotas ─────────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def listar(req: Request, status: str = "", busca: str = ""):
    """Lista os agendamentos recebidos e o andamento da internalização de cada."""
    _exigir(req, "view")
    from sqlalchemy import select
    agf_db.ensure_db()
    db.ensure_db()
    # processos já existentes, por agendamento
    with db.SessionLocal() as s:
        procs = {p.agendamento_id: p.to_dict(com_ativos=False)
                 for p in s.scalars(select(db.Processo)).all()}
    # agendamentos recebidos (a origem da fila)
    with agf_db.SessionLocal() as sa:
        recs = sa.scalars(
            select(agf_db.Agendamento)
            .where(agf_db.Agendamento.status == "RECEBIDO")
            .order_by(agf_db.Agendamento.id.desc())
        ).all()
        linhas = []
        for ag in recs:
            d = ag.to_dict()
            p = procs.get(ag.id)
            linhas.append({
                "agendamento_id": ag.id,
                "nf": d["nf"], "po": d["po"], "bu": d["bu"],
                "fornecedor": d["fornecedor"],
                "estoque_destino_rotulo": d["estoque_destino_rotulo"],
                "data_recebimento": d["data_recebimento"],
                "equipamentos": d["equipamentos"],
                "status": (p or {}).get("status", "PENDENTE"),
                "status_rotulo": (p or {}).get("status_rotulo", "Pendente"),
                "total_ativos": (p or {}).get("total_ativos", 0),
            })
    st = (status or "").strip().upper()
    if st in db.STATUS:
        linhas = [l for l in linhas if l["status"] == st]
    termo = (busca or "").strip().lower()
    if termo:
        linhas = [l for l in linhas
                  if any(termo in str(l.get(c, "")).lower()
                         for c in ("nf", "po", "fornecedor", "bu"))]
    return {"total": len(linhas), "itens": linhas}


@router.get("/{agendamento_id}")
def obter(agendamento_id: int, req: Request):
    """Abre a internalização de um agendamento (cria o processo se preciso) e
    devolve os ativos já lançados + os equipamentos esperados (para semear)."""
    _exigir(req, "view")
    db.ensure_db()
    with db.SessionLocal.begin() as s:
        proc = _garantir_processo(s, agendamento_id)
        dado = proc.to_dict()
    # equipamentos esperados (do agendamento) para orientar o lançamento
    agf_db.ensure_db()
    with agf_db.SessionLocal() as sa:
        ag = sa.get(agf_db.Agendamento, agendamento_id)
        dado["equipamentos_esperados"] = (
            [e.to_dict() for e in ag.equipamentos] if ag else [])
    return dado


@router.put("/{agendamento_id}")
def salvar(agendamento_id: int, body: SalvarIn, req: Request):
    """Substitui a lista de ativos internalizados do agendamento."""
    sd = _exigir(req, "edit")
    db.ensure_db()
    usuario = sd.get("username", "")
    with db.SessionLocal.begin() as s:
        proc = _garantir_processo(s, agendamento_id)
        proc.ativos.clear()
        for a in body.ativos:
            if not (a.ebs_item or a.descricao or a.plaqueta or a.numero_serie):
                continue   # linha em branco: ignora
            proc.ativos.append(db.Ativo(
                ebs_item=a.ebs_item, descricao=a.descricao,
                plaqueta=a.plaqueta, numero_serie=a.numero_serie,
                criado_por=usuario,
            ))
        proc.status = "CONCLUIDA" if body.concluir else "PENDENTE"
        proc.atualizado_em = db.utcnow()
        s.flush()
        dado = proc.to_dict()
    _log.info("internalização salva agendamento=%s ativos=%s status=%s por=%s ip=%s",
              agendamento_id, len(dado["ativos"]), dado["status"], usuario,
              client_ip(req))
    return dado


@router.get("/{agendamento_id}/exportar")
def exportar(agendamento_id: int, req: Request):
    """Gera a planilha "Placa Patrimonial" (mesmo layout do modelo oficial)."""
    _exigir(req, "export")
    db.ensure_db()
    from sqlalchemy import select
    with db.SessionLocal() as s:
        proc = s.scalar(select(db.Processo).where(
            db.Processo.agendamento_id == agendamento_id))
        if not proc:
            raise HTTPException(404, "Nada internalizado para este agendamento.")
        nf = proc.nf or ""
        linhas = [a.to_dict() for a in proc.ativos]
    conteudo = _montar_xlsx(nf, linhas)
    nome = f"Placa_Patrimonial_NF_{nf or agendamento_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(conteudo), media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.delete("/{agendamento_id}", status_code=204)
def excluir(agendamento_id: int, req: Request):
    """Remove o processo de internalização (só admin do módulo)."""
    _exigir(req, "admin")
    db.ensure_db()
    from sqlalchemy import select
    with db.SessionLocal.begin() as s:
        proc = s.scalar(select(db.Processo).where(
            db.Processo.agendamento_id == agendamento_id))
        if not proc:
            raise HTTPException(404, "Processo não encontrado.")
        s.delete(proc)
    return None


# ── Exportação Excel (idêntica ao modelo) ──────────────────────────────────
def _montar_xlsx(nf: str, linhas: list[dict]) -> bytes:
    """Monta a planilha no MESMO desenho do modelo: aba 'Placa Patrimonial',
    faixa vermelha (C00000) com texto branco, cabeçalho NF | Descrição do item
    | Plaqueta | Número de série | Item, dados a partir da linha 7."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Placa Patrimonial"
    ws.sheet_view.showGridLines = False

    vermelho = PatternFill("solid", fgColor="C00000")
    branco_bold = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
    corpo = Font(name="Calibri", size=11)
    centro = Alignment(horizontal="center", vertical="center")
    fina = Side(style="thin")
    borda = Border(left=fina, right=fina, top=fina, bottom=fina)

    # larguras (iguais ao modelo)
    for col, larg in {"A": 7.57, "B": 123.29, "C": 14.14,
                      "D": 22.14, "E": 8.14, "F": 13.0}.items():
        ws.column_dimensions[col].width = larg

    # faixa título (A5:D5 mesclada)
    ws.merge_cells("A5:D5")
    c = ws["A5"]
    c.value = "Placa Patrimonial (N° do bem)"
    c.fill = vermelho
    c.font = branco_bold
    c.alignment = centro
    for col in "ABCD":
        cc = ws[f"{col}5"]
        cc.fill = vermelho
        cc.border = borda
    ws.row_dimensions[5].height = 15.75

    # cabeçalho (linha 6)
    cabec = ["NF", "Descrição do item", "Plaqueta", "Número de série", "Item"]
    for i, titulo in enumerate(cabec):
        cell = ws.cell(row=6, column=i + 1, value=titulo)
        cell.fill = vermelho
        cell.font = branco_bold
        cell.alignment = centro
        cell.border = borda
    ws.row_dimensions[6].height = 15.75

    # dados (a partir da linha 7)
    r = 7
    for it in linhas:
        valores = [
            int(nf) if str(nf).isdigit() else nf,
            it.get("descricao", ""),
            it.get("plaqueta", ""),
            it.get("numero_serie", ""),
            int(it["ebs_item"]) if str(it.get("ebs_item", "")).isdigit()
            else it.get("ebs_item", ""),
        ]
        for i, v in enumerate(valores):
            cell = ws.cell(row=r, column=i + 1, value=v)
            cell.font = corpo
            cell.alignment = centro
            cell.border = borda
        ws.row_dimensions[r].height = 12.75
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
