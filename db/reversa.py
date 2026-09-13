"""Banco ISOLADO da Logística Reversa (A17).

O caminho de volta: a loja devolve equipamento ao CD. O token é a
**Coleta** — o que se combinou que a loja mandaria, com prazo acordado,
código de rastreio quando postar, e a lista do que era esperado.

O indicador que justifica o módulo é **esperado × recebido**. Sem ele,
equipamento que "foi mandado" e nunca chegou some em silêncio, e é assim
que ativo se perde: não no roubo, na falta de conferência. Por isso a
coleta só fecha depois de conferida, e fecha como CONFERIDA ou como
DIVERGENTE — nunca como "recebida" a granel.

O relógio mora no núcleo: a coleta abre um token com serial sintético
(`COL-AAAA-NNNN`) e aparece na Torre na frente "Reversa". Enquanto a bola
está com a loja ou com os Correios o intervalo é EXTERNO; quando o pacote
chega ao CD, vira fila da área.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, Date, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("reversa.db")

DATABASE_URL: str = getattr(
    _cfg, "REVERSA_DATABASE_URL", _config_mod._sqlite("reversa"),
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

# Estados da coleta. Também são os estados do token no núcleo.
AG_POSTAGEM = "AG_POSTAGEM_REV"        # combinado com a loja; ela ainda não postou
EM_TRANSITO = "EM_TRANSITO_REV"        # postado; nas mãos dos Correios
AG_CONFERENCIA = "AG_CONFERENCIA_REV"  # chegou ao CD; ninguém abriu ainda
EX_CONFERENCIA = "EX_CONFERENCIA_REV"  # alguém está conferindo
CONFERIDA = "CONFERIDA_REV"            # tudo que era esperado chegou, e nada além
DIVERGENTE = "DIVERGENTE_REV"          # faltou ou sobrou algo
CANCELADA = "CANCELADA_REV"

ESTADOS = (AG_POSTAGEM, EM_TRANSITO, AG_CONFERENCIA, EX_CONFERENCIA,
           CONFERIDA, DIVERGENTE, CANCELADA)

ROTULO_ESTADO = {
    AG_POSTAGEM: "Aguardando postagem",
    EM_TRANSITO: "Em trânsito",
    AG_CONFERENCIA: "Aguardando conferência",
    EX_CONFERENCIA: "Em conferência",
    CONFERIDA: "Conferida",
    DIVERGENTE: "Divergente",
    CANCELADA: "Cancelada",
}

# A bola está fora da área: o tempo é real, entra no total, mas não
# conta contra ninguém do CD.
ESTADOS_FORA = (AG_POSTAGEM, EM_TRANSITO)
ESTADOS_FINAIS = (CONFERIDA, DIVERGENTE, CANCELADA)

# De onde veio a leitura do recebido.
ORIGEM_CONFERENCIA = "CONFERENCIA"     # bipado na tela da coleta
ORIGEM_RECEBIMENTO = "RECEBIMENTO"     # casou sozinho com um recebimento (A01)


class Coleta(Base):
    """O que se combinou que a loja devolveria."""
    __tablename__ = "rev_coleta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    # Mesma regra da área: nada nasce sem chamado; a loja vem dele.
    chamado: Mapped[str] = mapped_column(String(40), index=True)
    chamado_tipo: Mapped[str] = mapped_column(String(20), default="")
    chamado_resumo: Mapped[str] = mapped_column(Text, default="")
    loja: Mapped[str] = mapped_column(String(200), default="", index=True)
    bu: Mapped[str] = mapped_column(String(20), default="")

    motivo: Mapped[str] = mapped_column(Text, default="")
    # A data que a loja se comprometeu a postar. É o que dá autoridade
    # para cobrar: sem prazo acordado, "a loja não mandou" é conversa.
    prazo_acordado: Mapped[datetime | None] = mapped_column(
        Date, default=None, index=True)

    # Rastreio. A etiqueta reversa é emitida fora do portal; aqui fica o
    # código, e é por ele que o portal acompanha nos Correios.
    codigo_rastreio: Mapped[str] = mapped_column(String(40), default="", index=True)
    postada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    ultimo_evento: Mapped[str] = mapped_column(Text, default="")
    ultimo_evento_em: Mapped[str] = mapped_column(String(40), default="")
    chegou_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    estado: Mapped[str] = mapped_column(String(30), default=AG_POSTAGEM, index=True)
    prazo_conferencia: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    aberta_por: Mapped[str] = mapped_column(String(80), index=True)
    aberta_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    conferida_por: Mapped[str] = mapped_column(String(80), default="")
    encerrada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    observacao: Mapped[str] = mapped_column(Text, default="")

    # Ligação com o núcleo. Vazio se a trilha estava fora do ar.
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_rev_coleta_fila", "estado", "prazo_acordado"),
    )


class Esperado(Base):
    """Uma linha do que a loja disse que mandaria."""
    __tablename__ = "rev_esperado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coleta_id: Mapped[int] = mapped_column(
        ForeignKey("rev_coleta.id", ondelete="CASCADE"), index=True)

    # Série e etiqueta em maiúsculas, para o casamento com o recebimento
    # não depender de como cada um digitou.
    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    etiqueta: Mapped[str] = mapped_column(String(60), default="", index=True)
    modelo: Mapped[str] = mapped_column(String(160), default="")
    descricao: Mapped[str] = mapped_column(String(200), default="")


class Recebido(Base):
    """Uma unidade que de fato chegou, casada ou não com o esperado."""
    __tablename__ = "rev_recebido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    coleta_id: Mapped[int] = mapped_column(
        ForeignKey("rev_coleta.id", ondelete="CASCADE"), index=True)
    # Nulo quando chegou algo que ninguém esperava — a sobra também é
    # divergência, e das que mais precisam de explicação.
    esperado_id: Mapped[int | None] = mapped_column(
        ForeignKey("rev_esperado.id", ondelete="SET NULL"), default=None, index=True)

    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    etiqueta: Mapped[str] = mapped_column(String(60), default="")
    origem: Mapped[str] = mapped_column(String(20), default=ORIGEM_CONFERENCIA)
    recebido_por: Mapped[str] = mapped_column(String(80), default="")
    recebido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Nome próprio: `serial` já tem ix_rev_recebido_serial do index=True.
        Index("ix_rev_recebido_coleta_serial", "coleta_id", "serial", unique=True),
    )


class Config(Base):
    __tablename__ = "rev_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Chamado de origem.
    "chamado_prefixos": "INC,RITM",
    "chamado_estados_bloqueados": "7,8",

    # Quantos dias corridos a loja tem para postar, quando quem abre não
    # informa uma data. Corrido de propósito: a loja não segue o
    # expediente do CD.
    "prazo_postagem_dias": "5",

    # Depois que o pacote chega, quanto tempo útil até conferir.
    "prazo_conferencia_dias": "2",

    # Divergência abre regularização (A19) automaticamente.
    "abrir_regularizacao": "1",
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
            _log.info("reversa: %d parâmetro(s) padrão criados", len(novas))


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
    """COL-AAAA-NNNN, sequencial por ano."""
    ano = utcnow().year
    prefixo = f"COL-{ano}-"
    ultimo = s.execute(
        select(Coleta.numero)
        .where(Coleta.numero.like(f"{prefixo}%"))
        .order_by(Coleta.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
