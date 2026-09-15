"""Banco ISOLADO do CAPEX Spare (Controle de Orçamento do SPARE).

Separado do `/controle-orcamento` e do portal. Modelo mestre-detalhe:

- `orc_spare_projeto`: um projeto (ID/nº do EBS, descrição, serviço, categoria),
  com o total APROVADO puxado do EBS pelo número e a PARCELA desse aprovado que
  é destinada ao Spare (informada à mão — o EBS não separa Spare do resto);
- `orc_spare_item`: as linhas de item de cada projeto (Item EBS, descrição do
  item, quantidade, valor unitário). O valor total da linha e o custo total do
  projeto são calculados (quantidade × valor unitário).

Tabelas novas de propósito (nomes `orc_spare_*`), para não colidir com o
desenho anterior do módulo (`spare_projeto`/`spare_campo`).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text,
    create_engine, event, func, select,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("orcamento_spare.db")

DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_SPARE_DATABASE_URL", _config_mod._sqlite("orcamento_spare"),
)

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
        _factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
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


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class Projeto(Base):
    __tablename__ = "orc_spare_projeto"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    numero: Mapped[str] = mapped_column(String(40), default="", index=True)   # ID do projeto (EBS)
    descricao: Mapped[str] = mapped_column(String(200), default="")
    servico: Mapped[str] = mapped_column(String(160), default="")
    categoria: Mapped[str] = mapped_column(String(80), default="")
    # Total aprovado do projeto no EBS (puxado pelo número) e a parcela que é do Spare.
    aprovado_ebs: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    aprovado_spare: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    observacao: Mapped[str] = mapped_column(Text, default="")
    ebs_sincronizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")

    itens: Mapped[list["Item"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan", order_by="Item.id")

    def to_dict(self) -> dict:
        itens = [i.to_dict() for i in self.itens]
        custo = round(sum(i["valor_total"] for i in itens), 2)
        aprovado_spare = _f(self.aprovado_spare)
        return {
            "id": self.id,
            "numero": self.numero or "",
            "descricao": self.descricao or "",
            "servico": self.servico or "",
            "categoria": self.categoria or "",
            "aprovado_ebs": _f(self.aprovado_ebs),
            "aprovado_spare": aprovado_spare,
            "observacao": self.observacao or "",
            "ebs_sincronizado_em": self.ebs_sincronizado_em.isoformat() if self.ebs_sincronizado_em else None,
            "custo_total": custo,
            "saldo_spare": round(aprovado_spare - custo, 2),
            "itens": itens,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
            "atualizado_por": self.atualizado_por or "",
        }


class Item(Base):
    __tablename__ = "orc_spare_item"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("orc_spare_projeto.id", ondelete="CASCADE"), index=True)
    item_ebs: Mapped[str] = mapped_column(String(60), default="")
    descricao_item: Mapped[str] = mapped_column(String(200), default="")
    quantidade: Mapped[float] = mapped_column(Numeric(15, 3), default=0)
    valor_unitario: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    ordem: Mapped[int] = mapped_column(Integer, default=0)

    projeto: Mapped["Projeto"] = relationship(back_populates="itens")

    def to_dict(self) -> dict:
        q = _f(self.quantidade)
        vu = _f(self.valor_unitario)
        return {
            "id": self.id,
            "item_ebs": self.item_ebs or "",
            "descricao_item": self.descricao_item or "",
            "quantidade": q,
            "valor_unitario": vu,
            "valor_total": round(q * vu, 2),
        }


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


# ── Consultas ───────────────────────────────────────────────────────────
def listar_projetos() -> list[dict]:
    with SessionLocal() as s:
        rows = s.scalars(select(Projeto).order_by(Projeto.ordem, Projeto.id)).all()
        return [p.to_dict() for p in rows]


def totais() -> dict:
    projetos = listar_projetos()
    return {
        "projetos": len(projetos),
        "aprovado_ebs": round(sum(p["aprovado_ebs"] for p in projetos), 2),
        "aprovado_spare": round(sum(p["aprovado_spare"] for p in projetos), 2),
        "custo_total": round(sum(p["custo_total"] for p in projetos), 2),
    }
