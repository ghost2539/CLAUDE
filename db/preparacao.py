"""Banco ISOLADO da Preparação (A06, A07, A08.2).

Três estações que ficam entre a bancada e o estoque:

- **A06 Configuração** — coletor apto recebe a baseline da frota
- **A07 Montagem de sled** — componentes entram, um ativo novo sai
- **A08.2 Internalização** — conferência e endereçamento no estoque

O estado do ativo mora na Trilha. Aqui fica o conteúdo de cada estação:
qual baseline foi aplicada, que componentes foram consumidos, em que
endereço o equipamento foi guardado.

`Baseline` é versionada porque é o que permite correlacionar falha com
versão de configuração mais tarde (G12). Sem a versão registrada, a
pergunta "essa falha começou depois de qual baseline?" não tem resposta.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("preparacao.db")

DATABASE_URL: str = getattr(
    _cfg, "PREPARACAO_DATABASE_URL", _config_mod._sqlite("preparacao"),
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


# ── As três estações ───────────────────────────────────────────────
CONFIGURACAO = "CONFIGURACAO"      # A06
MONTAGEM = "MONTAGEM"              # A07
INTERNALIZACAO = "INTERNALIZACAO"  # A08.2
ESTACOES = (CONFIGURACAO, MONTAGEM, INTERNALIZACAO)

ROTULO_ESTACAO = {
    CONFIGURACAO: "Configuração de coletor",
    MONTAGEM: "Montagem de sled",
    INTERNALIZACAO: "Internalização",
}

PROCESSO = {CONFIGURACAO: "A06", MONTAGEM: "A07", INTERNALIZACAO: "A08.2"}

FILA_DA_ESTACAO = {
    CONFIGURACAO: "AG_CONFIGURACAO",
    MONTAGEM: "AG_MONTAGEM",
    INTERNALIZACAO: "AG_INTERNALIZACAO",
}
TRATATIVA_DA_ESTACAO = {
    CONFIGURACAO: "EX_CONFIGURACAO",
    MONTAGEM: "EX_MONTAGEM",
    INTERNALIZACAO: "EX_INTERNALIZACAO",
}

# Onde o ativo vai ao sair de cada estação.
SAIDA_DA_ESTACAO = {
    CONFIGURACAO: "AG_INTERNALIZACAO",
    MONTAGEM: "AG_INTERNALIZACAO",
    INTERNALIZACAO: "DISPONIVEL",
}


class Passagem(Base):
    """Uma passagem de um ativo por uma estação."""
    __tablename__ = "prp_passagem"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)
    estacao: Mapped[str] = mapped_column(String(20), default=CONFIGURACAO, index=True)

    # A06
    baseline_id: Mapped[int | None] = mapped_column(
        ForeignKey("prp_baseline.id", ondelete="RESTRICT"), default=None, index=True)
    firmware: Mapped[str] = mapped_column(String(60), default="")
    teste_funcional: Mapped[bool | None] = mapped_column(Boolean, default=None)

    # A07 — os componentes consumidos ficam em texto porque nem todos
    # têm série própria; os que têm entram na tabela de composição.
    carcaca: Mapped[str] = mapped_column(String(120), default="")
    componentes: Mapped[str] = mapped_column(Text, default="")

    # A08.2
    conferencia_ok: Mapped[bool | None] = mapped_column(Boolean, default=None)
    endereco: Mapped[str] = mapped_column(String(120), default="")
    origem_anterior: Mapped[str] = mapped_column(String(40), default="")
    observacao: Mapped[str] = mapped_column(Text, default="")

    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    quando: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        # Nome distinto do índice automático de `serial`, que já se chama
        # ix_prp_passagem_serial. Dois índices homônimos fazem o
        # create_all falhar e o módulo inteiro não sobe.
        Index("ix_prp_passagem_serial_data", "serial", "quando"),
    )


class Composicao(Base):
    """Componente com série consumido numa montagem.

    A montagem é o único processo que transforma ativos: o que entra
    deixa de existir como unidade e passa a fazer parte do que saiu. A
    referência cruzada é o que mantém a trilha do componente legível
    depois que ele some do estoque.
    """
    __tablename__ = "prp_composicao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    passagem_id: Mapped[int] = mapped_column(
        ForeignKey("prp_passagem.id", ondelete="CASCADE"), index=True)
    serial_resultante: Mapped[str] = mapped_column(String(120), index=True)
    serial_componente: Mapped[str] = mapped_column(String(120), index=True)
    papel: Mapped[str] = mapped_column(String(60), default="")


class Baseline(Base):
    """Padrão de configuração da frota, versionado."""
    __tablename__ = "prp_baseline"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    versao: Mapped[str] = mapped_column(String(40), unique=True)
    descricao: Mapped[str] = mapped_column(Text, default="")
    vigente: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    criada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)
    criada_por: Mapped[str] = mapped_column(String(80), default="")


class Config(Base):
    __tablename__ = "prp_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Escrever o endereçamento no ServiceNow ao internalizar. Deixado
    # como chave porque o ambiente de teste não deve escrever lá.
    "escrever_no_servicenow": "sim",
    "status_disponivel": "6",     # In stock
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "preparacao")
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
