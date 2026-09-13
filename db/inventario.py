"""Banco ISOLADO do Inventário e Contagem (A18).

O token é o **ciclo de contagem**: um recorte do estoque (um corredor,
uma prateleira, o depósito inteiro), o retrato do que o ServiceNow dizia
estar lá na hora em que o ciclo abriu, e o que de fato foi bipado.

O retrato é tirado **na abertura** e congelado. Contar contra o
ServiceNow ao vivo faria o alvo se mover enquanto alguém conta — um
envio no meio da contagem viraria "faltante" sem ninguém ter errado.

Cada diferença entre o retrato e a contagem vira um token de
regularização (A19), com dono e prazo. O ciclo em si fecha como
CONFERIDO ou DIVERGENTE e não fica aberto "até resolver": quem resolve
é a regularização, e o indicador do inventário é a divergência
encontrada, não a divergência resolvida.
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
_log = logging.getLogger("inventario.db")

DATABASE_URL: str = getattr(
    _cfg, "INVENTARIO_DATABASE_URL", _config_mod._sqlite("inventario"),
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

AG_CONTAGEM = "AG_CONTAGEM_INV"    # ciclo aberto, retrato tirado, ninguém contando
EX_CONTAGEM = "EX_CONTAGEM_INV"    # alguém contando
CONFERIDO = "CONFERIDO_INV"        # contagem bateu com o retrato
DIVERGENTE = "DIVERGENTE_INV"      # faltou ou sobrou
CANCELADO = "CANCELADO_INV"
ESTADOS = (AG_CONTAGEM, EX_CONTAGEM, CONFERIDO, DIVERGENTE, CANCELADO)
ESTADOS_FINAIS = (CONFERIDO, DIVERGENTE, CANCELADO)

ROTULO_ESTADO = {
    AG_CONTAGEM: "Aguardando contagem",
    EX_CONTAGEM: "Em contagem",
    CONFERIDO: "Conferido",
    DIVERGENTE: "Divergente",
    CANCELADO: "Cancelado",
}


class Ciclo(Base):
    """Um recorte do estoque, contado de uma vez."""
    __tablename__ = "inv_ciclo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    # O recorte. `prefixo` é o começo do espaço/corredor (REP, IN, REP-A03…);
    # vazio conta o depósito inteiro. `filtro` é a consulta que de fato
    # foi ao ServiceNow, guardada para auditoria.
    descricao: Mapped[str] = mapped_column(String(200), default="")
    prefixo: Mapped[str] = mapped_column(String(60), default="", index=True)
    filtro: Mapped[str] = mapped_column(Text, default="")

    estado: Mapped[str] = mapped_column(String(30), default=AG_CONTAGEM, index=True)
    esperados: Mapped[int] = mapped_column(Integer, default=0)

    aberto_por: Mapped[str] = mapped_column(String(80), index=True)
    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    contado_por: Mapped[str] = mapped_column(String(80), default="")
    iniciado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    encerrado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    observacao: Mapped[str] = mapped_column(Text, default="")

    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)


class Esperado(Base):
    """Uma linha do retrato: o que o ServiceNow dizia estar no recorte."""
    __tablename__ = "inv_esperado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ciclo_id: Mapped[int] = mapped_column(
        ForeignKey("inv_ciclo.id", ondelete="CASCADE"), index=True)
    sys_id: Mapped[str] = mapped_column(String(40), default="")
    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    etiqueta: Mapped[str] = mapped_column(String(60), default="", index=True)
    modelo: Mapped[str] = mapped_column(String(160), default="")
    local: Mapped[str] = mapped_column(String(120), default="")
    substatus: Mapped[str] = mapped_column(String(40), default="")


class Contado(Base):
    """Uma leitura da contagem, casada ou não com o retrato."""
    __tablename__ = "inv_contado"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ciclo_id: Mapped[int] = mapped_column(
        ForeignKey("inv_ciclo.id", ondelete="CASCADE"), index=True)
    esperado_id: Mapped[int | None] = mapped_column(
        ForeignKey("inv_esperado.id", ondelete="SET NULL"), default=None, index=True)

    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    # Onde a leitura foi feita — o corredor que a pessoa está contando.
    # É o que permite dizer "está aqui, mas o sistema diz ali".
    local: Mapped[str] = mapped_column(String(120), default="")
    # O que o ServiceNow diz dessa série quando ela não estava no retrato
    # (em uso na loja X, em outro corredor, inexistente…).
    situacao_sistema: Mapped[str] = mapped_column(String(200), default="")
    contado_por: Mapped[str] = mapped_column(String(80), default="")
    contado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Nome próprio: `serial` já tem ix_inv_contado_serial do index=True.
        Index("ix_inv_contado_ciclo_serial", "ciclo_id", "serial", unique=True),
    )


class Config(Base):
    __tablename__ = "inv_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # O retrato: o que conta como "em estoque" e onde está o corredor.
    # Os dois primeiros seguem a Separação por padrão; ficam aqui para
    # poder divergir sem mexer lá.
    "status_estoque": "6",
    "campo_local": "aisle_space_location",
    # Sobra que o sistema diz estar em outro corredor do mesmo depósito é
    # LOCAL_ERRADO; em qualquer outra situação é INESPERADO.
    "abrir_regularizacao": "1",
    # Prazo da contagem, em dias úteis a partir da abertura.
    "prazo_contagem_dias": "2",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "inventario")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("inventario: %d parâmetro(s) padrão criados", len(novas))


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
    """INV-AAAA-NNNN, sequencial por ano."""
    ano = utcnow().year
    prefixo = f"INV-{ano}-"
    ultimo = s.execute(
        select(Ciclo.numero)
        .where(Ciclo.numero.like(f"{prefixo}%"))
        .order_by(Ciclo.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
