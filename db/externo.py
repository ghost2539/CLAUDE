"""Banco ISOLADO do que sai da área e pode voltar (A05, A14).

Dois processos com a mesma forma e finalidades opostas:

- **A05 Assistência externa** — o equipamento sai para conserto e volta
  (reparado, substituído ou sem reparo).
- **A14 Devolução a terceiros** — operadora, Lexmark, comodato. Sai e
  não volta; o que volta é a confirmação de quem recebeu.

O relógio de quem está do lado de fora é EXTERNO na trilha: conta
contra o fluxo, nunca contra alguém do SPARE. Serve para negociar prazo
com o fornecedor, não para avaliar o time.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, Numeric, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("externo.db")

DATABASE_URL: str = getattr(
    _cfg, "EXTERNO_DATABASE_URL", _config_mod._sqlite("externo"),
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


ASSISTENCIA = "ASSISTENCIA"   # A05
DEVOLUCAO = "DEVOLUCAO"       # A14
ESPECIES = (ASSISTENCIA, DEVOLUCAO)

ROTULO_ESPECIE = {ASSISTENCIA: "Assistência externa",
                  DEVOLUCAO: "Devolução a terceiros"}
PROCESSO = {ASSISTENCIA: "A05", DEVOLUCAO: "A14"}

FILA = {ASSISTENCIA: "AG_ASSISTENCIA", DEVOLUCAO: "AG_DEVOLUCAO"}
FORA = {ASSISTENCIA: "EM_ASSISTENCIA", DEVOLUCAO: "AG_CONFIRMACAO"}
CONFERENCIA = "EX_CONFERENCIA_RETORNO"

# Resultado do retorno da assistência.
REPARADO = "REPARADO"
SUBSTITUIDO = "SUBSTITUIDO"
SEM_REPARO = "SEM_REPARO"
RESULTADOS = (REPARADO, SUBSTITUIDO, SEM_REPARO)

ROTULO_RESULTADO = {
    REPARADO: "Reparado",
    SUBSTITUIDO: "Substituído por outro equipamento",
    SEM_REPARO: "Voltou sem reparo",
}


class Envio(Base):
    """Uma saída de equipamento para fora da área."""
    __tablename__ = "ext_envio"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    especie: Mapped[str] = mapped_column(String(16), default=ASSISTENCIA, index=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    fornecedor: Mapped[str] = mapped_column(String(160), default="", index=True)
    contrato: Mapped[str] = mapped_column(String(80), default="")
    numero_rma: Mapped[str] = mapped_column(String(80), default="", index=True)
    justificativa: Mapped[str] = mapped_column(Text, default="")

    enviado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)
    previsao: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)
    retornado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    resultado: Mapped[str] = mapped_column(String(20), default="")
    # Substituição gera equipamento novo: a trilha do antigo encerra e a
    # do novo começa, com referência cruzada nos dois sentidos.
    serial_substituto: Mapped[str] = mapped_column(String(120), default="")
    custo: Mapped[float | None] = mapped_column(Numeric(14, 2), default=None)
    observacao: Mapped[str] = mapped_column(Text, default="")

    aberto_por: Mapped[str] = mapped_column(String(80), default="")
    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    encerrado: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    __table_args__ = (
        Index("ix_ext_envio_serial_data", "serial", "aberto_em"),
    )


class Config(Base):
    __tablename__ = "ext_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Dias além da previsão que disparam alerta. Equipamento em
    # assistência sem prazo vira equipamento perdido.
    "alerta_atraso_dias": "5",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "externo")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
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
            linha = s.get(Config, chave)
            if linha is None:
                s.add(Config(chave=chave, valor=str(valor)))
            else:
                linha.valor = str(valor)
        s.commit()
