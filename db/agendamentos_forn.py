"""Banco ISOLADO dos Agendamentos de Fornecedores (menu Entrada).

Registra o agendamento da entrega de um fornecedor antes de ela chegar:
BU, NF, PO, fornecedor, estoque de destino, data agendada e os equipamentos
(descrição + quantidade). Quando a carga chega, um botão confirma o
recebimento (grava a data e passa o agendamento para a etapa de
internalização). Nada aqui escreve no banco de outro módulo.

Tabelas:
- `agf_agendamento` — um agendamento por NF/PO;
- `agf_equipamento` — uma linha por equipamento do agendamento (n itens).
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import (
    Date, DateTime, ForeignKey, Integer, String, create_engine, event,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("agendamentos_forn.db")

DATABASE_URL: str = getattr(
    _cfg, "AGENDAMENTOS_FORN_DATABASE_URL",
    _config_mod._sqlite("agendamentos_forn"),
)

# ── Vocabulário canônico ────────────────────────────────────────────────
# BU: só estas três bandeiras.
BUS = ("Renner", "Camicado", "Youcom")
# Estoque de destino: dois grupos, guardados por código e exibidos por rótulo.
DESTINOS = {
    "INAUGURACAO_REFORMAS": "Inauguração/Reformas",
    "REPOSICAO": "Reposição",
}
# Ciclo: agendado → (confirma recebimento) → recebido (segue p/ internalização).
STATUS = {"AGENDADO": "Agendado", "RECEBIDO": "Recebido"}

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


class Agendamento(Base):
    __tablename__ = "agf_agendamento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bu: Mapped[str] = mapped_column(String(20), default="")
    nf: Mapped[str] = mapped_column(String(40), index=True)
    po: Mapped[str] = mapped_column(String(40), index=True)
    volumes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fornecedor: Mapped[str] = mapped_column(String(160))
    estoque_destino: Mapped[str] = mapped_column(String(40))
    data_agendada: Mapped[date] = mapped_column(Date)
    # Não é preenchida à mão: entra só quando alguém confirma o recebimento.
    data_recebimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="AGENDADO", index=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    criado_por: Mapped[str] = mapped_column(String(80), default="")
    recebido_por: Mapped[str] = mapped_column(String(80), default="")

    equipamentos: Mapped[list["Equipamento"]] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan",
        order_by="Equipamento.id",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "bu": self.bu or "",
            "nf": self.nf or "",
            "po": self.po or "",
            "volumes": self.volumes,
            "fornecedor": self.fornecedor or "",
            "estoque_destino": self.estoque_destino or "",
            "estoque_destino_rotulo": DESTINOS.get(self.estoque_destino, self.estoque_destino or ""),
            "data_agendada": self.data_agendada.isoformat() if self.data_agendada else "",
            "data_recebimento": self.data_recebimento.isoformat() if self.data_recebimento else "",
            "status": self.status or "AGENDADO",
            "status_rotulo": STATUS.get(self.status, self.status or ""),
            "criado_em": self.criado_em.isoformat() if self.criado_em else "",
            "criado_por": self.criado_por or "",
            "recebido_por": self.recebido_por or "",
            "equipamentos": [e.to_dict() for e in self.equipamentos],
        }


class Equipamento(Base):
    __tablename__ = "agf_equipamento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), index=True)
    descricao: Mapped[str] = mapped_column(String(200))
    quantidade: Mapped[int] = mapped_column(Integer, default=1)

    agendamento: Mapped["Agendamento"] = relationship(back_populates="equipamentos")

    def to_dict(self) -> dict:
        return {"id": self.id, "descricao": self.descricao or "",
                "quantidade": self.quantidade or 0}


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        _ready = True


def ensure_db() -> None:
    if not _ready:
        init_db()
