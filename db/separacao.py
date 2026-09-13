"""Banco ISOLADO da Separação e Expedição (A15).

Guarda a solicitação de separação e os itens dela. O relógio NÃO mora
aqui: quem mede tempo é a Trilha do Ativo (`db.trilha`). Este módulo
guarda o pedido; o núcleo guarda quanto tempo cada etapa levou.

- `Solicitacao`: o pedido. Nasce sempre de um chamado do ServiceNow —
  sem chamado não existe separação.
- `Item`: uma linha por modelo pedido, com a quantidade. A série de cada
  unidade é definida por quem separa, não por quem pede.
- `Unidade`: uma linha por equipamento efetivamente separado, com a série
  bipada na bancada. É ela que liga o pedido ao ativo na trilha.
- `Config`: como ler o estoque no ServiceNow e quanto tempo cada
  prioridade tem de prazo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("separacao.db")

DATABASE_URL: str = getattr(
    _cfg, "SEPARACAO_DATABASE_URL", _config_mod._sqlite("separacao"),
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


# ── Vocabulário ────────────────────────────────────────────────────
# Tipos de atendimento. Cada um consome um estoque e tem um dono.
FRENTE_RETAGUARDA = "FRENTE_RETAGUARDA"
MOBILIDADE = "MOBILIDADE"
INAUGURACAO_REFORMA = "INAUGURACAO_REFORMA"
TIPOS_ATENDIMENTO = (FRENTE_RETAGUARDA, MOBILIDADE, INAUGURACAO_REFORMA)

ROTULO_TIPO = {
    FRENTE_RETAGUARDA: "Frente e Retaguarda",
    MOBILIDADE: "Mobilidade",
    INAUGURACAO_REFORMA: "Inauguração e Reforma",
}

# Estados da solicitação. São os mesmos que a trilha registra por ativo,
# mas aqui valem para o pedido inteiro.
AG_SEPARACAO = "AG_SEPARACAO"
EX_SEPARACAO = "EX_SEPARACAO"
SEPARADA = "SEPARADA"
ENVIADA = "ENVIADA"
CANCELADA = "CANCELADA"
ESTADOS = (AG_SEPARACAO, EX_SEPARACAO, SEPARADA, ENVIADA, CANCELADA)

ROTULO_ESTADO = {
    AG_SEPARACAO: "Aguardando separação",
    EX_SEPARACAO: "Em separação",
    SEPARADA: "Separada",
    ENVIADA: "Enviada",
    CANCELADA: "Cancelada",
}


class Solicitacao(Base):
    """Um pedido de separação, sempre atrelado a um chamado."""
    __tablename__ = "sep_solicitacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    # A origem. O documento é explícito: toda solicitação nasce de um
    # incidente ou requisição, e o destino vem do chamado — não se digita.
    chamado: Mapped[str] = mapped_column(String(40), index=True)
    chamado_tipo: Mapped[str] = mapped_column(String(20), default="")
    chamado_resumo: Mapped[str] = mapped_column(Text, default="")

    tipo_atendimento: Mapped[str] = mapped_column(
        String(30), default=FRENTE_RETAGUARDA, index=True)
    destino: Mapped[str] = mapped_column(String(200), default="")
    bu: Mapped[str] = mapped_column(String(20), default="")
    prioridade: Mapped[str] = mapped_column(String(20), default="normal")
    motivo: Mapped[str] = mapped_column(Text, default="")

    estado: Mapped[str] = mapped_column(String(20), default=AG_SEPARACAO, index=True)
    prazo: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)

    aberta_por: Mapped[str] = mapped_column(String(80), index=True)
    aberta_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    separada_por: Mapped[str] = mapped_column(String(80), default="")
    separada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    enviada_por: Mapped[str] = mapped_column(String(80), default="")
    enviada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_sep_fila", "estado", "tipo_atendimento"),
    )


class Item(Base):
    """Um modelo pedido, com quantidade. Sem série: quem separa define."""
    __tablename__ = "sep_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solicitacao_id: Mapped[int] = mapped_column(
        ForeignKey("sep_solicitacao.id", ondelete="CASCADE"), index=True)
    modelo: Mapped[str] = mapped_column(String(160))
    modelo_sys_id: Mapped[str] = mapped_column(String(40), default="")
    quantidade: Mapped[int] = mapped_column(Integer, default=1)


class Unidade(Base):
    """Um equipamento separado de verdade, com a série lida na bancada."""
    __tablename__ = "sep_unidade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    solicitacao_id: Mapped[int] = mapped_column(
        ForeignKey("sep_solicitacao.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("sep_item.id", ondelete="CASCADE"), index=True)

    serial: Mapped[str] = mapped_column(String(120), index=True)
    sys_id: Mapped[str] = mapped_column(String(40), default="")
    # Ligação com o núcleo: só existe quando o ativo está na trilha.
    # Ativo antigo, anterior ao módulo, entra sem trilha e sem relógio.
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    separada_por: Mapped[str] = mapped_column(String(80), default="")
    separada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Nome próprio: `serial` já tem índice automático chamado
        # ix_sep_unidade_serial, e dois índices com o mesmo nome fazem o
        # create_all falhar — o módulo inteiro não sobe.
        Index("ix_sep_unidade_pedido_serial", "solicitacao_id", "serial",
              unique=True),
    )


class Config(Base):
    """Parâmetros do módulo, em chave/valor para não migrar a cada ajuste."""
    __tablename__ = "sep_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Como achar o estoque no alm_hardware. O campo é o mesmo que a tela
    # de Saída de Ativos já escreve — no ServiceNow ele aparece como
    # "Aisle and Space", e é onde a área grava espaço e corredor juntos.
    "campo_local": "aisle_space_location",
    "comparacao": "STARTSWITH",          # STARTSWITH | = | LIKE
    "status_estoque": "6",               # In stock

    # O que separa um estoque do outro: o começo do espaço/corredor.
    "prefixo_reposicao": "REP",
    "prefixo_inauguracao": "IN",

    # Reserva. Ao bipar, o equipamento sai do saldo disponível na hora —
    # é o que impede duas pessoas separarem a mesma unidade. O campo é
    # configurável porque "reservado" pode ser substatus ou um valor
    # próprio de install_status, dependendo de como a área modelou.
    "reserva_campo": "substatus",
    "reserva_valor": "reserved",
    "reserva_valor_livre": "available",   # ao cancelar, volta para cá

    # Envio: o equipamento passa a estar em uso, na loja de destino.
    "envio_status": "1",                  # In use
    "envio_campo_local": "location",

    # Qual estoque cada tipo de atendimento consome. Mobilidade é
    # reposição: o mesmo modelo existe nos dois, e é a prateleira que
    # decide, não o equipamento.
    "estoque_FRENTE_RETAGUARDA": "reposicao",
    "estoque_MOBILIDADE": "reposicao",
    "estoque_INAUGURACAO_REFORMA": "inauguracao",

    # Escape: encoded query inteira, com $campo, $comparacao e $prefixo.
    # Em branco, o filtro é montado a partir dos parâmetros acima.
    "filtro_manual": "",

    # Chamado de origem.
    "chamado_prefixos": "INC,RITM",
    "chamado_estados_bloqueados": "7,8",   # encerrado, cancelado

    # Prazo por prioridade, em dias úteis.
    "prazo_normal": "3",
    "prazo_loja_parada": "1",
    "prazo_inauguracao": "10",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("separacao: %d parâmetro(s) padrão criados", len(novas))


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


def proximo_numero(s) -> str:
    """SEP-AAAA-NNNN, sequencial por ano."""
    ano = utcnow().year
    prefixo = f"SEP-{ano}-"
    ultimo = s.execute(
        select(Solicitacao.numero)
        .where(Solicitacao.numero.like(f"{prefixo}%"))
        .order_by(Solicitacao.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
