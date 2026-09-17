"""Banco ISOLADO da Venda de Ativos (A11).

O que sai do Spare por venda não sai peça a peça: a área negocia em
**ciclos trimestrais**. Entre a decisão de vender e a venda em si o ativo
fica parado esperando o ciclo — e é justamente esse tempo que a área quer
enxergar, por isso ele mora na Trilha, como fila (`AG_VENDA`).

Este banco guarda só o que é da venda:

- `Ciclo`: o trimestre (2026-T1), aberto, negociado ou concluído.
- `Item`: o ativo dentro do ciclo, de onde veio e por quanto saiu.

O estado do ativo continua na Trilha. Aqui não se duplica estado: o item
existe porque um ativo entrou num ciclo, e a baixa é uma movimentação de
encerramento no núcleo.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, ForeignKey, Index,
    create_engine, event, select,
)
from db._esquema import UtcDateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("venda.db")

DATABASE_URL: str = getattr(
    _cfg, "VENDA_DATABASE_URL", _config_mod._sqlite("venda"),
)

_engine = None
_factory = None


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ── Estados do ciclo ───────────────────────────────────────────────
ABERTO = "ABERTO"            # recebendo ativos
NEGOCIACAO = "NEGOCIACAO"    # fechado para inclusão, em negociação
CONCLUIDO = "CONCLUIDO"      # vendido: os ativos saíram e foram baixados
CANCELADO = "CANCELADO"
ESTADOS_CICLO = (ABERTO, NEGOCIACAO, CONCLUIDO, CANCELADO)

ROTULO_CICLO = {
    ABERTO: "Aberto",
    NEGOCIACAO: "Em negociação",
    CONCLUIDO: "Concluído",
    CANCELADO: "Cancelado",
}

# De onde o ativo veio parar na venda.
DA_ENTRADA = "RECEBIMENTO"   # venda direta, decidida na porta
DO_REPARO = "REPARO"         # a bancada concluiu que não vale reparar


def trimestre_de(quando: date) -> str:
    """Rótulo do trimestre: 2026-T3."""
    return f"{quando.year:04d}-T{(quando.month - 1) // 3 + 1}"


class Ciclo(Base):
    """Um trimestre de venda."""
    __tablename__ = "vnd_ciclo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trimestre: Mapped[str] = mapped_column(String(10), unique=True)   # 2026-T3
    estado: Mapped[str] = mapped_column(String(16), default=ABERTO, index=True)
    observacao: Mapped[str] = mapped_column(Text, default="")

    # Preenchidos na conclusão: é o que sustenta a baixa.
    comprador: Mapped[str] = mapped_column(String(160), default="")
    documento: Mapped[str] = mapped_column(String(120), default="")
    valor_total: Mapped[float] = mapped_column(Float, default=0.0)

    aberto_por: Mapped[str] = mapped_column(String(80), default="")
    aberto_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    concluido_por: Mapped[str] = mapped_column(String(80), default="")
    concluido_em: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), default=None)


class Item(Base):
    """Um ativo dentro de um ciclo de venda."""
    __tablename__ = "vnd_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ciclo_id: Mapped[int] = mapped_column(
        ForeignKey("vnd_ciclo.id", ondelete="CASCADE"), index=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)
    modelo: Mapped[str] = mapped_column(String(160), default="")
    origem: Mapped[str] = mapped_column(String(20), default=DO_REPARO)
    valor: Mapped[float] = mapped_column(Float, default=0.0)
    # Quando o ativo entrou na fila de venda: é daqui que sai o tempo
    # parado esperando o ciclo.
    na_fila_desde: Mapped[datetime | None] = mapped_column(
        UtcDateTime(), default=None)
    incluido_por: Mapped[str] = mapped_column(String(80), default="")
    incluido_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    baixado: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __table_args__ = (
        Index("ix_vnd_item_ciclo_serial", "ciclo_id", "serial", unique=True),
    )


class Config(Base):
    __tablename__ = "vnd_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Ativo parado na fila de venda além disto vira alerta na tela: é o
    # sinal de que passou um ciclo sem ser incluído.
    "alerta_fila_dias": "120",
    # Exigir documento (nota, contrato, ata) para concluir o ciclo.
    "exigir_documento": "1",
}


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "venda")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v) for k, v in PADROES.items()
                 if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()


def ler_config() -> dict[str, str]:
    with SessionLocal() as s:
        atual = {c.chave: c.valor for c in s.execute(select(Config)).scalars()}
    return {**PADROES, **atual}


def gravar_config(pares: dict) -> dict:
    with SessionLocal() as s:
        for chave, valor in (pares or {}).items():
            if chave not in PADROES:
                continue
            linha = s.get(Config, chave)
            if linha is None:
                s.add(Config(chave=chave, valor=str(valor)))
            else:
                linha.valor = str(valor)
        s.commit()
    return ler_config()
