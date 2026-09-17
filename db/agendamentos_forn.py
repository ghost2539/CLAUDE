"""Banco ISOLADO dos Agendamentos de Fornecedores (menu Entrada).

Registra o agendamento da entrega de um fornecedor antes de ela chegar:
BU, NF, PO, fornecedor, estoque de destino, data agendada e os equipamentos
(descrição + quantidade). Quando a carga chega, um botão confirma o
recebimento (grava a data e passa o agendamento para a etapa de
internalização). Nada aqui escreve no banco de outro módulo.

Tabelas:
- `agf_agendamento` — um agendamento (a entrega);
- `agf_pedido` — uma linha por PO do agendamento, com a NF que a cobre
  (a mesma NF pode aparecer em várias POs);
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

    pedidos: Mapped[list["Pedido"]] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan",
        order_by="Pedido.id",
    )

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
            # A tela reabre o agendamento por aqui: sem `pedidos` ela só veria
            # a primeira PO (a que ficou nas colunas soltas) e as demais
            # sumiriam na edição.
            "pedidos": [p.to_dict() for p in self.pedidos],
            "equipamentos": [e.to_dict() for e in self.equipamentos],
        }


class Pedido(Base):
    """Uma PO do agendamento, com a NF que a cobre.

    Antes o agendamento tinha uma PO e uma NF, em duas colunas. Na prática
    uma entrega traz várias POs, e UMA NF pode cobrir mais de uma — por isso
    a NF fica aqui, repetida em cada PO que ela atende, em vez de numa
    tabela à parte. Assim a pergunta que se faz no dia a dia ("o que veio
    nesta NF?" e "esta PO já foi agendada?") se responde sem join extra.

    As colunas `po` e `nf` do agendamento continuam existindo e guardam a
    PRIMEIRA delas: é o que a listagem e a busca já usavam, e mexer nisso
    apagaria da tela os agendamentos que já estão gravados.
    """

    __tablename__ = "agf_pedido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), index=True)
    po: Mapped[str] = mapped_column(String(40), index=True)
    nf: Mapped[str] = mapped_column(String(40), default="", index=True)
    # O que a consulta ao EBS respondeu quando a PO foi digitada. Guardado
    # para a conferência na chegada não depender do banco do EBS estar de pé.
    fornecedor_ebs: Mapped[str] = mapped_column(String(160), default="")
    status_ebs: Mapped[str] = mapped_column(String(40), default="")

    agendamento: Mapped["Agendamento"] = relationship(back_populates="pedidos")

    def to_dict(self) -> dict:
        return {"id": self.id, "po": self.po or "", "nf": self.nf or "",
                "fornecedor_ebs": self.fornecedor_ebs or "",
                "status_ebs": self.status_ebs or ""}


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
        # Coluna nova em tabela que já existe: o create_all não acrescenta.
        from db._esquema import migrar_colunas
        migrar_colunas(Base, get_engine(), "agendamentos_forn")
        # Agendamento gravado antes da tabela de pedidos fica sem linha nela
        # e apareceria sem PO nenhuma na tela. Aqui a PO e a NF que estão nas
        # colunas antigas viram a primeira linha — uma vez só, sem duplicar.
        _semear_pedidos()
        _ready = True


def _semear_pedidos() -> None:
    """Passa a PO/NF antigas do agendamento para a tabela de pedidos.

    Idempotente: só semeia agendamento que ainda não tem nenhuma linha.
    Falha aqui não pode derrubar a subida do módulo — no pior caso a tela
    mostra o agendamento sem PO, e o log diz por quê.
    """
    from sqlalchemy import select
    try:
        with SessionLocal.begin() as s:
            ja = {p.agendamento_id for p in s.scalars(select(Pedido)).all()}
            for ag in s.scalars(select(Agendamento)).all():
                if ag.id in ja or not (ag.po or ag.nf):
                    continue
                s.add(Pedido(agendamento_id=ag.id, po=ag.po or "", nf=ag.nf or ""))
    except Exception as exc:  # noqa: BLE001
        _log.warning("agendamentos: não semeei os pedidos antigos: %s", exc)


def ensure_db() -> None:
    if not _ready:
        init_db()
