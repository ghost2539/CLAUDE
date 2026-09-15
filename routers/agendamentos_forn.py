"""Agendamentos de Fornecedores (menu Entrada) — API /api/agendamentos-forn.

Cadastra o agendamento da entrega antes de a carga chegar e, quando ela
chega, um botão confirma o recebimento: grava a data e passa o registro
para a etapa de internalização (status RECEBIDO). Banco isolado em
`db/agendamentos_forn.py`.

Permissão pelo módulo "agendamentos_forn": view lê, create cadastra, edit
altera e confirma recebimento, admin exclui.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator

import db.agendamentos_forn as db
from core.security import client_ip, require_permission

_log = logging.getLogger("agendamentos_forn")
MODULO = "agendamentos_forn"

router = APIRouter(prefix="/api/agendamentos-forn", tags=["Agendamentos Forn."])


# ── Entrada validada ──────────────────────────────────────────────────────
class EquipamentoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    descricao: str
    quantidade: int

    @field_validator("descricao")
    @classmethod
    def _desc(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Equipamento sem descrição.")
        return v[:200]

    @field_validator("quantidade")
    @classmethod
    def _qtd(cls, v: int) -> int:
        if v is None or int(v) <= 0:
            raise ValueError("Quantidade do equipamento deve ser maior que zero.")
        return int(v)


class AgendamentoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bu: str = ""
    nf: str
    po: str
    volumes: int | None = None
    fornecedor: str
    estoque_destino: str
    data_agendada: str
    equipamentos: list[EquipamentoIn] = []

    @field_validator("nf", "po", "fornecedor")
    @classmethod
    def _obrig(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Campo obrigatório em branco.")
        return v[:160]

    @field_validator("bu")
    @classmethod
    def _bu(cls, v: str) -> str:
        v = (v or "").strip()
        if v and v not in db.BUS:
            raise ValueError(f"BU inválida. Use uma de: {', '.join(db.BUS)}.")
        return v

    @field_validator("estoque_destino")
    @classmethod
    def _destino(cls, v: str) -> str:
        v = (v or "").strip()
        if v not in db.DESTINOS:
            raise ValueError("Estoque de destino inválido "
                             "(Inauguração/Reformas ou Reposição).")
        return v

    @field_validator("data_agendada")
    @classmethod
    def _data(cls, v: str) -> str:
        v = (v or "").strip()
        try:
            date.fromisoformat(v)
        except ValueError:
            raise ValueError("Data agendada inválida (use AAAA-MM-DD).")
        return v

    @field_validator("volumes")
    @classmethod
    def _vol(cls, v):
        if v in (None, ""):
            return None
        if int(v) < 0:
            raise ValueError("Volumes não pode ser negativo.")
        return int(v)


def _exigir(req: Request, acao: str) -> dict:
    return require_permission(req, MODULO, acao)


# ── Rotas ─────────────────────────────────────────────────────────────────
# Teto de tamanho do PDF lido em memória: NF é pequena; acima disso é engano.
_MAX_PDF = 15 * 1024 * 1024


@router.post("/extrair-nf")
async def extrair_nf(req: Request, arquivo: UploadFile = File(...)):
    """Lê a NF EM MEMÓRIA (PDF/DANFE ou XML da NF-e) e devolve os campos para
    pré-preencher.

    O arquivo NUNCA é gravado: os bytes entram, os campos saem, e o upload é
    descartado. O XML é a fonte exata; o PDF é heurístico — o operador confere.
    """
    _exigir(req, "create")
    nome = (arquivo.filename or "").lower()
    if not (nome.endswith(".pdf") or nome.endswith(".xml")):
        raise HTTPException(422, "Envie o PDF (DANFE) ou o XML da NF-e.")
    dados = await arquivo.read()
    if not dados:
        raise HTTPException(422, "Arquivo vazio.")
    if len(dados) > _MAX_PDF:
        raise HTTPException(413, "Arquivo grande demais (máx. 15 MB).")
    try:
        from core.nf_pdf import extrair, SemBibliotecaPDF
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Leitor de NF indisponível: {exc}") from exc
    try:
        campos = extrair(dados)
    except SemBibliotecaPDF as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Falha ao ler PDF da NF: %s", exc)
        raise HTTPException(422, "Não foi possível ler este arquivo. Envie o "
                                 "DANFE em PDF (texto, não imagem) ou o XML da "
                                 "NF-e.") from exc
    finally:
        # Sem persistência: garante que nada do upload fica pendurado.
        try:
            await arquivo.close()
        except Exception:  # noqa: BLE001
            pass
    return campos


@router.get("/opcoes")
def opcoes(req: Request):
    """Listas fixas para os selects da tela."""
    _exigir(req, "view")
    return {
        "bus": list(db.BUS),
        "destinos": [{"valor": k, "rotulo": r} for k, r in db.DESTINOS.items()],
        "status": db.STATUS,
    }


@router.get("")
@router.get("/")
def listar(req: Request, status: str = "", busca: str = ""):
    """Lista os agendamentos, mais recentes primeiro. Filtra por status/busca."""
    _exigir(req, "view")
    from sqlalchemy import select
    status = (status or "").strip().upper()
    termo = (busca or "").strip().lower()
    with db.SessionLocal() as s:
        q = select(db.Agendamento).order_by(db.Agendamento.id.desc())
        if status in db.STATUS:
            q = q.where(db.Agendamento.status == status)
        linhas = [a.to_dict() for a in s.scalars(q).all()]
    if termo:
        def bate(a):
            return any(termo in str(a.get(c, "")).lower()
                       for c in ("nf", "po", "fornecedor", "bu"))
        linhas = [a for a in linhas if bate(a)]
    return {"total": len(linhas), "itens": linhas}


@router.get("/{item_id}")
def obter(item_id: int, req: Request):
    _exigir(req, "view")
    with db.SessionLocal() as s:
        a = s.get(db.Agendamento, item_id)
        if not a:
            raise HTTPException(404, "Agendamento não encontrado.")
        return a.to_dict()


@router.post("", status_code=201)
@router.post("/", status_code=201)
def criar(body: AgendamentoIn, req: Request):
    sd = _exigir(req, "create")
    with db.SessionLocal.begin() as s:
        a = db.Agendamento(
            bu=body.bu, nf=body.nf, po=body.po, volumes=body.volumes,
            fornecedor=body.fornecedor, estoque_destino=body.estoque_destino,
            data_agendada=date.fromisoformat(body.data_agendada),
            status="AGENDADO", criado_por=sd.get("username", ""),
        )
        for eq in body.equipamentos:
            a.equipamentos.append(
                db.Equipamento(descricao=eq.descricao, quantidade=eq.quantidade))
        s.add(a)
        s.flush()
        dado = a.to_dict()
    _log.info("agendamento criado id=%s nf=%s por=%s ip=%s",
              dado["id"], dado["nf"], sd.get("username", ""), client_ip(req))
    return dado


@router.patch("/{item_id}")
def editar(item_id: int, body: AgendamentoIn, req: Request):
    """Edita um agendamento que ainda não foi recebido."""
    _exigir(req, "edit")
    with db.SessionLocal.begin() as s:
        a = s.get(db.Agendamento, item_id)
        if not a:
            raise HTTPException(404, "Agendamento não encontrado.")
        if a.status == "RECEBIDO":
            raise HTTPException(409, "Agendamento já recebido; não pode ser editado.")
        a.bu = body.bu
        a.nf, a.po, a.fornecedor = body.nf, body.po, body.fornecedor
        a.volumes = body.volumes
        a.estoque_destino = body.estoque_destino
        a.data_agendada = date.fromisoformat(body.data_agendada)
        a.equipamentos.clear()
        for eq in body.equipamentos:
            a.equipamentos.append(
                db.Equipamento(descricao=eq.descricao, quantidade=eq.quantidade))
        s.flush()
        return a.to_dict()


@router.post("/{item_id}/receber")
def confirmar_recebimento(item_id: int, req: Request):
    """Confirma o recebimento: grava a data (hoje) e passa para internalização."""
    sd = _exigir(req, "edit")
    with db.SessionLocal.begin() as s:
        a = s.get(db.Agendamento, item_id)
        if not a:
            raise HTTPException(404, "Agendamento não encontrado.")
        if a.status == "RECEBIDO":
            raise HTTPException(409, "Recebimento já confirmado.")
        a.status = "RECEBIDO"
        a.data_recebimento = date.today()
        a.recebido_por = sd.get("username", "")
        s.flush()
        dado = a.to_dict()
    _log.info("recebimento confirmado id=%s nf=%s por=%s ip=%s",
              item_id, dado["nf"], sd.get("username", ""), client_ip(req))
    return dado


@router.delete("/{item_id}", status_code=204)
def excluir(item_id: int, req: Request):
    _exigir(req, "admin")
    with db.SessionLocal.begin() as s:
        a = s.get(db.Agendamento, item_id)
        if not a:
            raise HTTPException(404, "Agendamento não encontrado.")
        s.delete(a)
    return None
