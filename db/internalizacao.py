"""Banco ISOLADO da Internalização (menu Entrada).

Quando um agendamento de fornecedor tem o recebimento confirmado, ele passa
para a internalização: para CADA equipamento fisicamente recebido registra-se
o item do EBS, a descrição, a plaqueta (nº do bem), o número de série e a NF.
Esses dados alimentam a exportação da planilha "Placa Patrimonial".

Para não depender do banco de outro módulo na hora de exportar, o processo
guarda um retrato (snapshot) dos dados do agendamento (BU, fornecedor, NF,
destino, data do recebimento) no momento em que é aberto. Nada aqui escreve
no banco dos Agendamentos.

Tabelas:
- `int_processo` — um processo por agendamento recebido (com o snapshot);
- `int_ativo`    — uma linha por ativo internalizado (plaqueta/série únicos).
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import (
    Date, DateTime, ForeignKey, Integer, String, create_engine, event,
)
from db._esquema import UtcDateTime
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("internalizacao.db")

DATABASE_URL: str = getattr(
    _cfg, "INTERNALIZACAO_DATABASE_URL",
    _config_mod._sqlite("internalizacao"),
)

# Ciclo do processo: pendente → (dados lançados) → concluída.
STATUS = {"PENDENTE": "Pendente", "CONCLUIDA": "Concluída"}

# Etapas de CADA equipamento depois do lançamento. Por equipamento, e não
# por remessa: numa nota com dez desktops, se sete aparecerem no EBS e três
# não, os sete seguem. Segurar todos pelo atraso de um é o que faz fila
# parar sem motivo.
ETAPA_PATRIMONIO = "PATRIMONIO"   # esperando o serial aparecer no EBS
ETAPA_ENTRADA = "ENTRADA"         # liberado, esperando o técnico
ETAPA_CONCLUIDO = "CONCLUIDO"     # subiu no ServiceNow e virou estoque
ETAPAS = {
    ETAPA_PATRIMONIO: "Aguardando patrimônio",
    ETAPA_ENTRADA: "Pronto para entrada",
    ETAPA_CONCLUIDO: "Concluído",
}

# BUs cujo patrimônio nasce no EBS. Youcom compra por fora: lá não há o que
# consultar, e quem confirma é uma pessoa.
BUS_COM_EBS = ("Renner", "Camicado")

_engine = None
_factory = None
_ready = False
_init_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is None:
        kwargs = {"pool_pre_ping": True, "future": True}
        if DATABASE_URL.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(DATABASE_URL, **kwargs)
        if DATABASE_URL.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
    return _engine


def _session_factory():
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), autoflush=False,
                                expire_on_commit=False)
    return _factory


class SessionLocal:
    def __new__(cls):
        return _session_factory()()

    @staticmethod
    def begin():
        return _session_factory().begin()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Processo(Base):
    __tablename__ = "int_processo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Referência ao agendamento de origem (outro banco): guardada como número,
    # sem FK — os bancos são isolados.
    agendamento_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    # Snapshot do agendamento, tirado ao abrir o processo.
    bu: Mapped[str] = mapped_column(String(20), default="")
    fornecedor: Mapped[str] = mapped_column(String(160), default="")
    nf: Mapped[str] = mapped_column(String(40), default="", index=True)
    estoque_destino: Mapped[str] = mapped_column(String(40), default="")
    estoque_destino_rotulo: Mapped[str] = mapped_column(String(60), default="")
    data_recebimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDENTE", index=True)
    criado_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    atualizado_em: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), nullable=True)

    ativos: Mapped[list["Ativo"]] = relationship(
        back_populates="processo", cascade="all, delete-orphan",
        order_by="Ativo.id",
    )

    def to_dict(self, com_ativos: bool = True) -> dict:
        d = {
            "id": self.id,
            "agendamento_id": self.agendamento_id,
            "bu": self.bu or "",
            "fornecedor": self.fornecedor or "",
            "nf": self.nf or "",
            "estoque_destino": self.estoque_destino or "",
            "estoque_destino_rotulo": self.estoque_destino_rotulo or "",
            "data_recebimento": self.data_recebimento.isoformat() if self.data_recebimento else "",
            "status": self.status or "PENDENTE",
            "status_rotulo": STATUS.get(self.status, self.status or ""),
            "criado_em": self.criado_em.isoformat() if self.criado_em else "",
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else "",
            "total_ativos": len(self.ativos),
        }
        if com_ativos:
            d["ativos"] = [a.to_dict() for a in self.ativos]
        return d


class Ativo(Base):
    __tablename__ = "int_ativo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processo_id: Mapped[int] = mapped_column(
        ForeignKey("int_processo.id", ondelete="CASCADE"), index=True)
    ebs_item: Mapped[str] = mapped_column(String(60), default="")
    descricao: Mapped[str] = mapped_column(String(200), default="")
    plaqueta: Mapped[str] = mapped_column(String(60), default="")
    numero_serie: Mapped[str] = mapped_column(String(80), default="")
    criado_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    criado_por: Mapped[str] = mapped_column(String(80), default="")

    # ── Depois do lançamento ────────────────────────────────────────────
    etapa: Mapped[str] = mapped_column(
        String(20), default=ETAPA_PATRIMONIO, server_default=ETAPA_PATRIMONIO,
        index=True)
    # O que a consulta ao EBS achou, e quando. Guardado porque a conferência
    # depois não pode depender do banco do EBS estar de pé — e porque saber
    # QUANDO apareceu é o que explica a demora de uma remessa.
    ebs_encontrado_em: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    ebs_ativo: Mapped[str] = mapped_column(String(60), default="", server_default="")
    ebs_descricao: Mapped[str] = mapped_column(String(200), default="", server_default="")
    # Youcom: quem confirmou à mão, já que não há EBS para consultar.
    confirmado_por: Mapped[str] = mapped_column(String(80), default="", server_default="")
    confirmado_em: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # ── Entrada de equipamento (técnico de gestão de ativos) ────────────
    espaco_corredor: Mapped[str] = mapped_column(String(60), default="", server_default="")
    sn_sys_id: Mapped[str] = mapped_column(String(64), default="", server_default="")
    entrada_por: Mapped[str] = mapped_column(String(80), default="", server_default="")
    entrada_em: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    # O ativo criado no estoque do portal ao concluir. É o elo entre este
    # fluxo e a Consulta, a Separação e o resto — sem ele, "entrou em
    # estoque" seria só uma palavra na tela.
    asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    processo: Mapped["Processo"] = relationship(back_populates="ativos")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "ebs_item": self.ebs_item or "",
            "descricao": self.descricao or "",
            "plaqueta": self.plaqueta or "",
            "numero_serie": self.numero_serie or "",
            "etapa": self.etapa or ETAPA_PATRIMONIO,
            "etapa_rotulo": ETAPAS.get(self.etapa or ETAPA_PATRIMONIO, ""),
            "ebs_encontrado_em": (self.ebs_encontrado_em.isoformat()
                                  if self.ebs_encontrado_em else ""),
            "ebs_ativo": self.ebs_ativo or "",
            "ebs_descricao": self.ebs_descricao or "",
            "confirmado_por": self.confirmado_por or "",
            "confirmado_em": self.confirmado_em.isoformat() if self.confirmado_em else "",
            "espaco_corredor": self.espaco_corredor or "",
            "sn_sys_id": self.sn_sys_id or "",
            "entrada_por": self.entrada_por or "",
            "entrada_em": self.entrada_em.isoformat() if self.entrada_em else "",
            "asset_id": self.asset_id,
        }


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        # Colunas novas em tabela que já existe: create_all não acrescenta.
        from db._esquema import migrar_colunas
        migrar_colunas(Base, get_engine(), "internalizacao")
        _ready = True


def ensure_db() -> None:
    if not _ready:
        init_db()
