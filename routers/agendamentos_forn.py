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
import re
from datetime import date

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

import db.agendamentos_forn as db
from core.security import check_rate_limit, client_ip, require_permission

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


# Teto de linhas de pedido por agendamento. Uma entrega real junta algumas
# POs, não centenas: acima disso é engano (ou payload forjado) e recusar sai
# mais barato que gravar.
_MAX_PEDIDOS = 50


class PedidoIn(BaseModel):
    """Uma PO do agendamento, com a NF que a cobre.

    A NF é opcional porque o agendamento nasce antes de a nota existir; a PO
    não, que é ela que identifica o pedido. A mesma NF pode vir repetida em
    várias linhas — é justamente como se representa "uma nota para várias POs".
    """

    model_config = ConfigDict(extra="ignore")
    po: str
    nf: str = ""
    # Vêm da consulta ao EBS, não da digitação: aqui só se apara no tamanho
    # da coluna, sem recusar o salvamento por causa deles.
    fornecedor_ebs: str = ""
    status_ebs: str = ""

    @field_validator("po")
    @classmethod
    def _po(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Linha de pedido sem PO: informe o número ou remova a linha.")
        if len(v) > 40:
            raise ValueError("Número de PO longo demais (máx. 40 caracteres).")
        return v

    @field_validator("nf")
    @classmethod
    def _nf(cls, v: str) -> str:
        v = (v or "").strip()
        if len(v) > 40:
            raise ValueError("Número de NF longo demais (máx. 40 caracteres).")
        return v

    @field_validator("fornecedor_ebs")
    @classmethod
    def _forn_ebs(cls, v: str) -> str:
        return (v or "").strip()[:160]

    @field_validator("status_ebs")
    @classmethod
    def _status_ebs(cls, v: str) -> str:
        return (v or "").strip()[:40]


class AgendamentoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    bu: str = ""
    # `nf` e `po` são as colunas antigas, de uma PO só. Continuam aqui porque
    # a listagem e a busca leem delas, mas quem manda agora é `pedidos`: o
    # validador de baixo copia a PRIMEIRA linha para cá.
    nf: str = ""
    po: str = ""
    volumes: int | None = None
    fornecedor: str
    estoque_destino: str
    data_agendada: str
    pedidos: list[PedidoIn] = []
    equipamentos: list[EquipamentoIn] = []

    @field_validator("fornecedor")
    @classmethod
    def _obrig(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("Campo obrigatório em branco.")
        return v[:160]

    @field_validator("nf", "po")
    @classmethod
    def _coluna_antiga(cls, v: str) -> str:
        return (v or "").strip()[:40]

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

    @model_validator(mode="after")
    def _casar_pedidos(self):
        """Concilia a lista de POs com as colunas antigas `po`/`nf`.

        Dois sentidos, porque os dois existem na prática:
        - veio `pedidos` (tela nova): a PRIMEIRA PO e a PRIMEIRA NF vão para
          as colunas soltas, que é de onde a listagem e a busca leem;
        - veio só `po`/`nf` (chamada antiga): vira uma linha de pedido, senão
          o agendamento nasceria sem nenhuma e sumiria da tela nova.
        """
        if len(self.pedidos) > _MAX_PEDIDOS:
            raise ValueError(f"Máximo de {_MAX_PEDIDOS} POs por agendamento.")
        vistas = set()
        for p in self.pedidos:
            chave = p.po.upper()
            if chave in vistas:
                raise ValueError(f"PO {p.po} repetida no mesmo agendamento.")
            vistas.add(chave)
        if self.pedidos:
            self.po, self.nf = self.pedidos[0].po, self.pedidos[0].nf
        elif self.po:
            self.pedidos = [PedidoIn(po=self.po, nf=self.nf)]
        if not self.po:
            raise ValueError("Informe ao menos uma PO.")
        return self


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


# BUs cujo pedido nasce no EBS. Youcom compra por fora, então digitar a PO
# lá não tem o que consultar — e a tela não deve fingir que tem.
BUS_COM_EBS = ("Renner", "Camicado")


@router.get("/po/{numero}")
def consultar_po(numero: str, req: Request, bu: str = ""):
    """Os itens de uma PO, lidos DIRETO da base do EBS.

    É a consulta `po_itens` de integracoes/ebs_oracle.py — nossa, agregada
    por linha do pedido, não a do módulo de compras (que repete o item por
    distribuição). Só-leitura, com teto de linhas e bind variable.

    A tela usa a resposta para preencher os Equipamentos: cada item vira uma
    linha com a descrição e a quantidade pedida.
    """
    _exigir(req, "view")
    check_rate_limit(req, "api")

    numero = (numero or "").strip()
    # O número da PO entra como bind variable, mas validar antes evita ida
    # ao banco por engano de digitação e dá erro melhor que o do driver.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,39}", numero):
        raise HTTPException(422, "Número de PO inválido.")
    if bu and bu not in BUS_COM_EBS:
        raise HTTPException(
            422, f"A BU {bu} não tem pedido no EBS. Informe os equipamentos à mão.")

    try:
        from integracoes import ebs_oracle
        linhas = ebs_oracle.run_named("po_itens", {"numero_po": numero}, max_rows=500)
    except ImportError as exc:
        raise HTTPException(
            503, "O driver Oracle não está instalado neste servidor "
                 f"(pip install oracledb). Detalhe: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("consulta da PO %s falhou: %s", numero, exc)
        # Falta de credencial é 503 (configure o serviço), não 502: ninguém
        # recusou nada — a conexão nem foi tentada.
        if type(exc).__name__ == "EbsOracleSemCredencial":
            raise HTTPException(503, str(exc)) from exc
        raise HTTPException(502, _erro_limpo(exc)) from exc

    if not linhas:
        raise HTTPException(404, f"PO {numero} não encontrada no EBS, "
                                 "ou sem linha ativa.")

    cab = linhas[0]
    return {
        "po": cab.get("po_numero") or numero,
        "fornecedor": cab.get("fornecedor") or "",
        "status": cab.get("status_po") or "",
        "moeda": cab.get("moeda") or "",
        "itens": [{
            "linha": l.get("linha"),
            "item_ebs": l.get("item_ebs") or "",
            "descricao": l.get("descricao") or "",
            "unidade": l.get("unidade") or "",
            # A tela preenche o Equipamento com o que FALTA receber; se a PO
            # ainda não teve recebimento, pendente é igual a pedida.
            "quantidade_pedida": int(l.get("quantidade_pedida") or 0),
            "quantidade_recebida": int(l.get("quantidade_recebida") or 0),
            "quantidade_pendente": int(l.get("quantidade_pendente") or 0),
        } for l in linhas],
    }


def _erro_limpo(exc: Exception) -> str:
    """Erro do banco sem o endereço de conexão dentro.

    ORA-12154 e ORA-12541 trazem o DSN no texto. Repassar cru publicaria na
    tela o que nem no repositório pode ficar.
    """
    texto = str(exc)
    try:
        from core.cofre import obter
        for nome in ("ORACLE_EBS_DSN", "EBS_ORACLE_DSN"):
            dsn = obter(nome, "")
            if dsn:
                texto = texto.replace(dsn, "<endereço do banco>")
                for pedaco in re.split(r"[/:@]", dsn):
                    if len(pedaco) > 3:
                        texto = texto.replace(pedaco, "<omitido>")
                break
    except Exception:  # noqa: BLE001
        pass
    return f"A base do EBS recusou a consulta: {texto}"


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
        for p in body.pedidos:
            a.pedidos.append(db.Pedido(
                po=p.po, nf=p.nf,
                fornecedor_ebs=p.fornecedor_ebs, status_ebs=p.status_ebs))
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
        # Lista inteira substituída (não há edição linha a linha na tela):
        # o que o formulário mandou é o que fica.
        a.pedidos.clear()
        for p in body.pedidos:
            a.pedidos.append(db.Pedido(
                po=p.po, nf=p.nf,
                fornecedor_ebs=p.fornecedor_ebs, status_ebs=p.status_ebs))
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
