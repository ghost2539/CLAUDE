"""Banco ISOLADO da Destinação (A09 a A13).

O fim da vida do ativo na área, em duas etapas com donos diferentes:

- **A09 Descaracterização** (Sergio) — remoção de mídia com evidência,
  análise de condição física, e o ativo sai pronto para ter destino.
- **A10 Definição de destino** (Lucas) — forma lotes e decide entre
  venda, descarte e doação.
- **A11 Venda** e **A12 Descarte** (Leonardo), **A13 Doação** (Lucas) —
  cada um com o seu documento obrigatório.

## O token muda de mãos aqui

Até a descaracterização o token é o ativo. A partir do lote é o lote:
ninguém negocia um notebook, negocia trinta. `LoteItem` guarda a
ligação, e a trilha de cada ativo continua registrando o que aconteceu
com ele.

## Anexo é bloqueante e não é enfeite

Evidência de remoção de mídia e certificado de destinação são os
documentos que a empresa apresenta se for questionada. O código recusa
concluir sem eles — 99% de conformidade aqui não é bom resultado, é
exposição.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, Numeric, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("destinacao.db")

DATABASE_URL: str = getattr(
    _cfg, "DESTINACAO_DATABASE_URL", _config_mod._sqlite("destinacao"),
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


# ── Destinos possíveis do lote ─────────────────────────────────────
VENDA = "VENDA"
DESCARTE = "DESCARTE"
DOACAO = "DOACAO"
DESTINOS = (VENDA, DESCARTE, DOACAO)

ROTULO_DESTINO = {VENDA: "Venda", DESCARTE: "Descarte", DOACAO: "Doação"}
PROCESSO_DO_DESTINO = {VENDA: "A11", DESCARTE: "A12", DOACAO: "A13"}

# Estados do lote.
ABERTO = "ABERTO"              # ainda recebendo ativos
AG_TRATATIVA = "AG_TRATATIVA"  # destino definido, esperando quem executa
EM_TRATATIVA = "EM_TRATATIVA"
CONCLUIDO = "CONCLUIDO"
ESTADOS_LOTE = (ABERTO, AG_TRATATIVA, EM_TRATATIVA, CONCLUIDO)

ROTULO_ESTADO = {
    ABERTO: "Em formação",
    AG_TRATATIVA: "Aguardando tratativa",
    EM_TRATATIVA: "Em tratativa",
    CONCLUIDO: "Concluído",
}

# Métodos de remoção de mídia.
REMOCAO_FISICA = "REMOCAO_FISICA"
LAUDO_ERS = "LAUDO_ERS"
SEM_MIDIA = "SEM_MIDIA"
METODOS = (REMOCAO_FISICA, LAUDO_ERS, SEM_MIDIA)

ROTULO_METODO = {
    REMOCAO_FISICA: "Remoção física da mídia",
    LAUDO_ERS: "Laudo da ERS",
    SEM_MIDIA: "Equipamento sem mídia",
}


class Descaracterizacao(Base):
    """A09 — o que foi feito com o ativo antes de ele poder sair."""
    __tablename__ = "dst_descaracterizacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    possui_midia: Mapped[bool] = mapped_column(Boolean, default=False)
    serie_midia: Mapped[str] = mapped_column(String(120), default="", index=True)
    metodo: Mapped[str] = mapped_column(String(20), default=SEM_MIDIA)

    # Análise prévia: é ela que decide se o equipamento é candidato a
    # doação. Feita aqui porque é aqui que alguém tem o aparelho na mão.
    condicao_fisica: Mapped[str] = mapped_column(String(20), default="")  # boa|regular|ruim
    observacao: Mapped[str] = mapped_column(Text, default="")
    local_destino: Mapped[str] = mapped_column(String(120), default="")

    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    quando: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        Index("ix_dst_desc_serial_data", "serial", "quando"),
    )


class Lote(Base):
    """A10 — o agrupamento que vira a unidade de negociação."""
    __tablename__ = "dst_lote"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    destino: Mapped[str] = mapped_column(String(16), default=VENDA, index=True)
    estado: Mapped[str] = mapped_column(String(20), default=ABERTO, index=True)
    justificativa: Mapped[str] = mapped_column(Text, default="")

    # A11 venda
    comprador: Mapped[str] = mapped_column(String(160), default="")
    valor_estimado: Mapped[float | None] = mapped_column(Numeric(14, 2), default=None)
    valor_realizado: Mapped[float | None] = mapped_column(Numeric(14, 2), default=None)
    nf_venda: Mapped[str] = mapped_column(String(60), default="")

    # A12 descarte
    fornecedor: Mapped[str] = mapped_column(String(160), default="")
    tipo_residuo: Mapped[str] = mapped_column(String(80), default="")
    peso_kg: Mapped[float | None] = mapped_column(Numeric(12, 2), default=None)

    # A13 doação
    entidade: Mapped[str] = mapped_column(String(200), default="")
    recebedor: Mapped[str] = mapped_column(String(160), default="")

    aberto_por: Mapped[str] = mapped_column(String(80), default="")
    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    concluido_por: Mapped[str] = mapped_column(String(80), default="")
    concluido_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)


class LoteItem(Base):
    """Um ativo dentro de um lote."""
    __tablename__ = "dst_lote_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lote_id: Mapped[int] = mapped_column(
        ForeignKey("dst_lote.id", ondelete="CASCADE"), index=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    modelo: Mapped[str] = mapped_column(String(160), default="")
    condicao_fisica: Mapped[str] = mapped_column(String(20), default="")

    __table_args__ = (
        Index("ix_dst_item_unico", "lote_id", "serial", unique=True),
    )


class Anexo(Base):
    """Documento comprobatório.

    O arquivo fica em `data/uploads/destinacao/`; aqui ficam o caminho,
    quem anexou e a que se refere. Guardar o binário no banco tornaria
    o backup do banco impraticável.
    """
    __tablename__ = "dst_anexo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # A que pertence: uma descaracterização ou um lote.
    especie: Mapped[str] = mapped_column(String(20), index=True)  # descaracterizacao|lote
    referencia_id: Mapped[int] = mapped_column(Integer, index=True)
    tipo: Mapped[str] = mapped_column(String(30), default="")
    # evidencia_midia | certificado_destinacao | termo_doacao | nf_venda

    nome_original: Mapped[str] = mapped_column(String(255), default="")
    caminho: Mapped[str] = mapped_column(String(500), default="")
    tamanho: Mapped[int] = mapped_column(Integer, default=0)
    enviado_por: Mapped[str] = mapped_column(String(80), default="")
    enviado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)


class Config(Base):
    __tablename__ = "dst_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Extensões aceitas no anexo. Documento comprobatório é PDF ou foto;
    # aceitar executável seria abrir uma porta sem necessidade nenhuma.
    "anexo_extensoes": ".pdf,.jpg,.jpeg,.png",
    "anexo_tamanho_mb": "20",
    # Quantos dias o lote pode ficar sem destino definido antes de virar
    # alerta. É o tempo entre a baixa e a decisão do Lucas.
    "alerta_sem_destino_dias": "15",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "destinacao")
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


def proximo_numero(s) -> str:
    ano = utcnow().year
    prefixo = f"LOTE-{ano}-"
    ultimo = s.execute(
        select(Lote.numero).where(Lote.numero.like(f"{prefixo}%"))
        .order_by(Lote.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
