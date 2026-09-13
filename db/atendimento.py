"""Banco ISOLADO do Atendimento a Chamados (A20).

O chamado vive no ServiceNow. Aqui fica o espelho do que a área precisa
medir e ligar: quem assumiu, quanto tempo levou, e a qual ativo, remessa
ou projeto o atendimento se refere.

- `Chamado`: o token. Espelha número, frente e situação.
- `Movimentacao` e `Intervalo`: a mesma mecânica do núcleo da Trilha,
  aplicada a um token que não é ativo.
- `Vinculo`: a ligação obrigatória com serial, remessa ou projeto.

## Por que não usa as tabelas da Trilha

O núcleo é do ativo: `trl_ativo` tem serial único e representa um
equipamento. Um chamado não é um equipamento — ele *fala* de um ou de
vários, e existe mesmo quando nenhum foi identificado ainda.

O que precisava ser comum entre os dois é o **calendário**: um dia útil
tem que significar a mesma coisa em todos os indicadores da área. Isso é
compartilhado de verdade (`routers.trilha.duracao_util`), e é o único
ponto que precisava ser.

O preço é a mecânica de sessão escrita duas vezes. Vale enquanto forem
dois tokens; no terceiro, compensa generalizar o núcleo em vez de
copiar de novo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, Index,
    create_engine, event, select, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("atendimento.db")

DATABASE_URL: str = getattr(
    _cfg, "ATENDIMENTO_DATABASE_URL", _config_mod._sqlite("atendimento"),
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


# ── Frentes de atendimento (A20.1 e A20.2) ─────────────────────────
LOJA = "LOJA"      # frente e retaguarda de loja, inaugurações e reformas
FROTA = "FROTA"    # coletores e sleds
FRENTES = (LOJA, FROTA)

ROTULO_FRENTE = {
    LOJA: "Loja",
    FROTA: "Frota móvel",
}

# ── Estados. O que pausa o relógio do atendente está marcado. ──────
AG_ATENDIMENTO = "AG_ATENDIMENTO"        # fila: ninguém assumiu
EX_ATENDIMENTO = "EX_ATENDIMENTO"        # tratativa: corre contra o atendente
AG_TERCEIRO = "AG_TERCEIRO"              # externo: espera de fora, pausa
AG_EQUIPAMENTO = "AG_EQUIPAMENTO"        # fila: espera a separação, pausa
RESOLVIDO = "RESOLVIDO"
ESTADOS = (AG_ATENDIMENTO, EX_ATENDIMENTO, AG_TERCEIRO, AG_EQUIPAMENTO, RESOLVIDO)

ROTULO_ESTADO = {
    AG_ATENDIMENTO: "Aguardando atendimento",
    EX_ATENDIMENTO: "Em atendimento",
    AG_TERCEIRO: "Aguardando terceiro",
    AG_EQUIPAMENTO: "Aguardando equipamento",
    RESOLVIDO: "Resolvido",
}

# Natureza do intervalo — a mesma régua do núcleo.
FILA = "FILA"
TRATATIVA = "TRATATIVA"
EXTERNO = "EXTERNO"

TIPO_DO_ESTADO = {
    AG_ATENDIMENTO: FILA,
    EX_ATENDIMENTO: TRATATIVA,
    AG_TERCEIRO: EXTERNO,
    AG_EQUIPAMENTO: FILA,
    RESOLVIDO: FILA,
}


class Chamado(Base):
    """Espelho do chamado do ServiceNow, com o relógio da área."""
    __tablename__ = "atd_chamado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(20), default="")     # INC | RITM
    frente: Mapped[str] = mapped_column(String(12), default=LOJA, index=True)

    resumo: Mapped[str] = mapped_column(Text, default="")
    local: Mapped[str] = mapped_column(String(200), default="")
    solicitante: Mapped[str] = mapped_column(String(160), default="")
    categoria: Mapped[str] = mapped_column(String(120), default="")

    estado: Mapped[str] = mapped_column(String(24), default=AG_ATENDIMENTO, index=True)
    atendente: Mapped[str] = mapped_column(String(80), default="", index=True)

    # Situação do chamado no ServiceNow na última leitura. Guardada para
    # que a fila do portal não afirme "em atendimento" sobre um chamado
    # que já foi encerrado por lá.
    estado_sn: Mapped[str] = mapped_column(String(20), default="")
    lido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    resolvido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_atd_fila", "estado", "frente"),
    )


class Movimentacao(Base):
    """Uma transição de estado do chamado. Nunca alterada nem apagada."""
    __tablename__ = "atd_movimentacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamado_id: Mapped[int] = mapped_column(
        ForeignKey("atd_chamado.id", ondelete="RESTRICT"), index=True)
    estado_de: Mapped[str] = mapped_column(String(24), default="")
    estado_para: Mapped[str] = mapped_column(String(24), default="", index=True)
    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    motivo: Mapped[str] = mapped_column(Text, default="")
    quando: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)


class Intervalo(Base):
    """O relógio do chamado. Início e fim crus, como no núcleo."""
    __tablename__ = "atd_intervalo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamado_id: Mapped[int] = mapped_column(
        ForeignKey("atd_chamado.id", ondelete="RESTRICT"), index=True)
    estado: Mapped[str] = mapped_column(String(24), default="", index=True)
    tipo: Mapped[str] = mapped_column(String(12), default=FILA, index=True)
    sessao: Mapped[int] = mapped_column(Integer, default=1)
    inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    fim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)
    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)

    __table_args__ = (
        Index("ix_atd_int_aberto", "chamado_id", "fim"),
    )


class Vinculo(Base):
    """A ligação do chamado com o que ele move.

    O documento exige três: serial do ativo (alimenta o histórico das
    bancadas), remessa (liga à expedição) e projeto. Sem isso o chamado
    é uma linha solta e o histórico do equipamento nunca fica completo.
    """
    __tablename__ = "atd_vinculo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chamado_id: Mapped[int] = mapped_column(
        ForeignKey("atd_chamado.id", ondelete="CASCADE"), index=True)
    especie: Mapped[str] = mapped_column(String(16), index=True)  # serial|remessa|projeto
    valor: Mapped[str] = mapped_column(String(120), index=True)
    criado_por: Mapped[str] = mapped_column(String(80), default="")
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("ix_atd_vinculo_unico", "chamado_id", "especie", "valor", unique=True),
    )


SERIAL = "serial"
REMESSA = "remessa"
PROJETO = "projeto"
ESPECIES = (SERIAL, REMESSA, PROJETO)


class Config(Base):
    __tablename__ = "atd_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Qual frente atende o quê. A categoria do chamado é o que separa,
    # e é texto livre no ServiceNow — por isso fica configurável.
    "categorias_frota": "coletor,sled,mobilidade",
    # Situações do ServiceNow que fecham o chamado no espelho.
    # Prazo de atendimento por frente, em dias úteis.
    "prazo_loja": "2",
    "prazo_frota": "2",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "atendimento")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("atendimento: %d parâmetro(s) padrão criados", len(novas))


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
