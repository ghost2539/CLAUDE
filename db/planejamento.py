"""Banco ISOLADO do Planejamento de compras (Orçamento Spare).

Três tabelas e nenhum consumo real gravado aqui: o consumo real vem da
Separação, lido na hora. O que este banco guarda é o que só existe no
planejamento — o item (agrupamento de modelos com parâmetros de compra),
o histórico **imputado** dos meses anteriores ao sistema, e a configuração.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("planejamento.db")

DATABASE_URL: str = getattr(
    _cfg, "PLANEJAMENTO_DATABASE_URL", _config_mod._sqlite("planejamento_spare"),
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


class Item(Base):
    """Um item de compra: um ou mais modelos equivalentes da Separação."""
    __tablename__ = "pln_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(160), unique=True)
    # Modelos da Separação que este item agrupa, separados por "|".
    modelos: Mapped[str] = mapped_column(Text, default="")
    categoria: Mapped[str] = mapped_column(String(80), default="")

    estoque_atual: Mapped[int] = mapped_column(Integer, default=0)
    estoque_atualizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    estoque_origem: Mapped[str] = mapped_column(String(20), default="")   # manual | servicenow
    pedidos_abertos: Mapped[int] = mapped_column(Integer, default=0)
    lead_time_dias: Mapped[int] = mapped_column(Integer, default=30)
    seguranca_dias: Mapped[int] = mapped_column(Integer, default=30)
    custo_unitario: Mapped[float] = mapped_column(Float, default=0.0)
    observacao: Mapped[str] = mapped_column(Text, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    # Item do EBS: liga o item a um acordo de compra (fornecedor, valor,
    # vencimento). Em branco, o custo é só o digitado.
    item_ebs: Mapped[str] = mapped_column(String(60), default="", index=True)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")


class Historico(Base):
    """Consumo imputado de um mês anterior ao sistema. Um por item e mês."""
    __tablename__ = "pln_historico"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("pln_item.id", ondelete="CASCADE"), index=True)
    mes: Mapped[str] = mapped_column(String(7), index=True)   # AAAA-MM
    quantidade: Mapped[int] = mapped_column(Integer, default=0)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_pln_historico_item_mes", "item_id", "mes", unique=True),
    )


class Acordo(Base):
    """Acordo de compra vigente com um fornecedor para um item do EBS."""
    __tablename__ = "pln_acordo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_ebs: Mapped[str] = mapped_column(String(60), index=True)
    descricao: Mapped[str] = mapped_column(String(240), default="")
    valor: Mapped[float] = mapped_column(Float, default=0.0)
    fornecedor_codigo: Mapped[str] = mapped_column(String(30), default="", index=True)
    fornecedor_nome: Mapped[str] = mapped_column(String(160), default="")
    vencimento: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    numero: Mapped[str] = mapped_column(String(60), default="")      # nº do acordo/contrato, se houver
    observacao: Mapped[str] = mapped_column(Text, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_pln_acordo_item_fornecedor", "item_ebs", "fornecedor_codigo", unique=True),
    )


class Substituicao(Base):
    """Plano de substituição de um modelo obsoleto: com o quê, por quanto e quando."""
    __tablename__ = "pln_substituicao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    modelo_obsoleto: Mapped[str] = mapped_column(String(160), unique=True)
    # Item de planejamento que substitui (dá o custo e o acordo). Opcional.
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("pln_item.id", ondelete="SET NULL"), default=None, index=True)
    custo_unitario: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = usar o do item
    mes_alvo: Mapped[str] = mapped_column(String(7), default="")       # AAAA-MM da compra
    percentual: Mapped[int] = mapped_column(Integer, default=100)       # quanto do parque trocar
    observacao: Mapped[str] = mapped_column(Text, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Config(Base):
    __tablename__ = "pln_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # A partir deste mês o consumo vem da Separação; antes, é imputado.
    # Em branco: o primeiro mês em que a Separação registrou envio.
    "data_inicio_sistema": "",
    # Quantos meses de previsão a tela mostra.
    "horizonte_meses": "12",
    # Quantos meses de histórico a tela mostra na grade.
    "meses_historico": "24",
    # Acordo "vence em breve" a partir de quantos dias antes do vencimento.
    "acordo_alerta_dias": "60",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "planejamento")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v) for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()


def ler_config() -> dict[str, str]:
    with SessionLocal() as s:
        atual = {c.chave: c.valor for c in s.execute(select(Config)).scalars()}
    return {**PADROES, **atual}


def gravar_config(pares: dict[str, str]) -> None:
    with SessionLocal() as s:
        for chave, valor in pares.items():
            if chave not in PADROES:
                continue
            linha = s.get(Config, chave)
            if linha is None:
                s.add(Config(chave=chave, valor=str(valor)))
            else:
                linha.valor = str(valor)
        s.commit()
