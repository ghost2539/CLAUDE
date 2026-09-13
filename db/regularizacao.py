"""Banco ISOLADO da Regularização de Ativo (A19).

Todo processo da área produz divergência: a coleta que chegou com um a
menos, o inventário que achou um coletor que o ServiceNow diz estar em
loja, a série que ninguém sabe de onde veio. Sem um lugar para isso, a
divergência vira linha de relatório — e relatório ninguém trata.

O token é a **divergência**. O mecanismo inteiro são dois campos:
**responsável** e **prazo**. É o que transforma "faltou um" em "fulano
tem até sexta para dizer onde está". A divergência não sai da fila sem
os dois, e não fecha sem dizer como foi resolvida.

O relógio mora no núcleo (serial sintético `REG-AAAA-NNNN`), na frente
"Regularização" da Torre: a divergência parada há um mês é uma fila tão
real quanto um coletor parado na bancada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, Index, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("regularizacao.db")

DATABASE_URL: str = getattr(
    _cfg, "REGULARIZACAO_DATABASE_URL", _config_mod._sqlite("regularizacao"),
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

# De onde a divergência veio. É o que permite o indicador por processo:
# quantas a reversa gera, quantas o inventário gera.
ORIGENS = {
    "A17": "Logística reversa",
    "A18": "Inventário",
    "MANUAL": "Aberta à mão",
}

# O que a divergência é.
FALTANTE = "FALTANTE"        # deveria estar, não está
INESPERADO = "INESPERADO"    # está, não deveria (ou não se sabe de onde veio)
LOCAL_ERRADO = "LOCAL_ERRADO"  # está, mas o sistema diz que está em outro lugar
TIPOS = (FALTANTE, INESPERADO, LOCAL_ERRADO)
ROTULO_TIPO = {
    FALTANTE: "Faltante",
    INESPERADO: "Inesperado",
    LOCAL_ERRADO: "Local errado",
}

# Estados — também os do token no núcleo.
AG_TRATATIVA = "AG_TRATATIVA_REG"   # ninguém assumiu
EX_TRATATIVA = "EX_TRATATIVA_REG"   # tem responsável e prazo
RESOLVIDA = "RESOLVIDA_REG"
CANCELADA = "CANCELADA_REG"
ESTADOS = (AG_TRATATIVA, EX_TRATATIVA, RESOLVIDA, CANCELADA)
ESTADOS_FINAIS = (RESOLVIDA, CANCELADA)

ROTULO_ESTADO = {
    AG_TRATATIVA: "Aguardando tratativa",
    EX_TRATATIVA: "Em tratativa",
    RESOLVIDA: "Resolvida",
    CANCELADA: "Cancelada",
}

# Como terminou. Obrigatório ao resolver: "resolvido" sem dizer como é
# o mesmo que não ter registrado.
RESOLUCOES = {
    "ENCONTRADO": "Equipamento encontrado",
    "AJUSTE_SN": "Cadastro corrigido no ServiceNow",
    "BAIXA_SN": "Baixado no ServiceNow",
    "DEVOLVIDO_LOJA": "Devolvido pela loja",
    "PERDA": "Perda reconhecida",
    "DUPLICIDADE": "Era duplicidade de registro",
}


class Divergencia(Base):
    __tablename__ = "reg_divergencia"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    origem: Mapped[str] = mapped_column(String(10), default="MANUAL", index=True)
    # O número do processo que abriu (COL-..., INV-...). É o caminho de
    # volta para quem for atrás.
    referencia: Mapped[str] = mapped_column(String(40), default="", index=True)
    tipo: Mapped[str] = mapped_column(String(20), default=FALTANTE, index=True)

    serial: Mapped[str] = mapped_column(String(120), default="", index=True)
    etiqueta: Mapped[str] = mapped_column(String(60), default="")
    modelo: Mapped[str] = mapped_column(String(160), default="")
    loja: Mapped[str] = mapped_column(String(200), default="", index=True)
    # Onde o sistema dizia que estava / onde foi achado — quando houver.
    local_sistema: Mapped[str] = mapped_column(String(200), default="")
    local_fisico: Mapped[str] = mapped_column(String(200), default="")
    descricao: Mapped[str] = mapped_column(Text, default="")

    estado: Mapped[str] = mapped_column(String(30), default=AG_TRATATIVA, index=True)
    responsavel: Mapped[str] = mapped_column(String(80), default="", index=True)
    prazo: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)

    resolucao: Mapped[str] = mapped_column(String(30), default="")
    resolucao_detalhe: Mapped[str] = mapped_column(Text, default="")
    historico: Mapped[str] = mapped_column(Text, default="")

    aberta_por: Mapped[str] = mapped_column(String(80), index=True)
    aberta_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    assumida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    encerrada_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    encerrada_por: Mapped[str] = mapped_column(String(80), default="")

    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_reg_fila", "estado", "prazo"),
        # A mesma série do mesmo processo não abre duas vezes.
        Index("ix_reg_origem_ref_serial", "origem", "referencia", "serial", "tipo"),
    )


class Config(Base):
    __tablename__ = "reg_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Prazo sugerido ao assumir, em dias úteis. Quem assume pode mudar;
    # o que não pode é ficar sem.
    "prazo_padrao_dias": "5",
    # Quanto tempo uma divergência pode ficar sem dono antes de virar
    # alerta na fila, em dias úteis.
    "alerta_sem_dono_dias": "2",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "regularizacao")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("regularizacao: %d parâmetro(s) padrão criados", len(novas))


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
    """REG-AAAA-NNNN, sequencial por ano."""
    ano = utcnow().year
    prefixo = f"REG-{ano}-"
    ultimo = s.execute(
        select(Divergencia.numero)
        .where(Divergencia.numero.like(f"{prefixo}%"))
        .order_by(Divergencia.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
