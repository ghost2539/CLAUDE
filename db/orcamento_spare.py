"""Banco ISOLADO do Controle de Orçamento do SPARE (CAPEX).

Separado do `/controle-orcamento` (execução CAPEX da vertical) e do portal:
este é o orçamento da área SPARE, alimentado manualmente.

Os campos ainda estão sendo definidos. Por isso o modelo tem duas partes:

- um NÚCLEO fixo, com o que qualquer controle de orçamento precisa
  (identificação, classificação, os quatro valores e as datas);
- um mapa `dados` (JSON) alimentado pelas definições da tabela
  `spare_campo`, que o administrador cadastra na tela. Assim dá para
  acrescentar campo sem migração de banco.

Quando o desenho estiver fechado, os campos que provarem valor podem virar
coluna de verdade — a migração lê `dados` e distribui.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text,
    create_engine, event, func, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("orcamento_spare.db")

DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_SPARE_DATABASE_URL", _config_mod._sqlite("orcamento_spare"),
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
                cur.close()
    return _engine


def _session_factory():
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _factory


class SessionLocal:
    def __new__(cls):
        return _session_factory()()

    @staticmethod
    def begin():
        return _session_factory().begin()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def localnow() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


TIPOS = ("CAPEX", "OPEX")
SITUACOES = ("Planejado", "Aprovado", "Em execução", "Concluído", "Cancelado")

# Tipos aceitos numa definição de campo (o front escolhe o input por aqui).
TIPOS_CAMPO = ("texto", "numero", "moeda", "data", "lista", "booleano")


class Projeto(Base):
    """Uma linha do orçamento do SPARE."""
    __tablename__ = "spare_projeto"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    # ── Núcleo: identificação e classificação ───────────────────────
    numero: Mapped[str] = mapped_column(String(40), default="", index=True)
    nome: Mapped[str] = mapped_column(String(200), default="")
    tipo: Mapped[str] = mapped_column(String(10), default="CAPEX")
    categoria: Mapped[str] = mapped_column(String(60), default="")
    situacao: Mapped[str] = mapped_column(String(20), default="Planejado", index=True)
    responsavel: Mapped[str] = mapped_column(String(120), default="")

    # ── Núcleo: valores (sempre em BRL) ─────────────────────────────
    aprovado: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    comprometido: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    realizado: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    a_realizar: Mapped[float] = mapped_column(Numeric(15, 2), default=0)

    # ── Núcleo: datas e texto livre ─────────────────────────────────
    inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    observacao: Mapped[str] = mapped_column(Text, default="")

    # ── Campos ainda a definir, guardados como JSON ─────────────────
    dados: Mapped[str] = mapped_column(Text, default="{}")

    ordem: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")

    def to_dict(self) -> dict:
        try:
            extras = json.loads(self.dados or "{}")
        except ValueError:
            extras = {}
        return {
            "id": self.id, "numero": self.numero, "nome": self.nome,
            "tipo": self.tipo, "categoria": self.categoria,
            "situacao": self.situacao, "responsavel": self.responsavel,
            "aprovado": float(self.aprovado or 0),
            "comprometido": float(self.comprometido or 0),
            "realizado": float(self.realizado or 0),
            "a_realizar": float(self.a_realizar or 0),
            "inicio": self.inicio.isoformat() if self.inicio else None,
            "fim": self.fim.isoformat() if self.fim else None,
            "observacao": self.observacao, "ordem": self.ordem,
            "dados": extras,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
            "atualizado_por": self.atualizado_por,
        }


class Campo(Base):
    """Definição de um campo adicional, cadastrada pelo administrador.

    Guardar a definição no banco (em vez de fixar no código) é o que permite
    fechar o desenho da tela depois, sem alterar o modelo.
    """
    __tablename__ = "spare_campo"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    chave: Mapped[str] = mapped_column(String(40), unique=True)   # nome técnico em `dados`
    rotulo: Mapped[str] = mapped_column(String(80), default="")   # o que aparece na tela
    tipo: Mapped[str] = mapped_column(String(12), default="texto")
    opcoes: Mapped[str] = mapped_column(Text, default="")         # lista: valores separados por ;
    obrigatorio: Mapped[bool] = mapped_column(Boolean, default=False)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "chave": self.chave, "rotulo": self.rotulo,
            "tipo": self.tipo,
            "opcoes": [o.strip() for o in (self.opcoes or "").split(";") if o.strip()],
            "obrigatorio": bool(self.obrigatorio), "ativo": bool(self.ativo),
            "ordem": self.ordem,
        }


_init_lock = threading.Lock()
_ready = False


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        _ready = True


def ensure_db() -> None:
    if not _ready:
        init_db()


# ── Consultas ───────────────────────────────────────────────────────────
def listar_projetos() -> list[dict]:
    with SessionLocal() as s:
        rows = s.scalars(
            select(Projeto).order_by(Projeto.ordem, Projeto.id)
        ).all()
        return [r.to_dict() for r in rows]


def listar_campos(somente_ativos: bool = False) -> list[dict]:
    with SessionLocal() as s:
        stmt = select(Campo).order_by(Campo.ordem, Campo.id)
        if somente_ativos:
            stmt = stmt.where(Campo.ativo.is_(True))
        return [r.to_dict() for r in s.scalars(stmt).all()]


def totais() -> dict:
    """Somatório dos quatro valores — base do resumo da tela."""
    with SessionLocal() as s:
        row = s.execute(select(
            func.coalesce(func.sum(Projeto.aprovado), 0),
            func.coalesce(func.sum(Projeto.comprometido), 0),
            func.coalesce(func.sum(Projeto.realizado), 0),
            func.coalesce(func.sum(Projeto.a_realizar), 0),
            func.count(Projeto.id),
        )).one()
    return {
        "aprovado": float(row[0]), "comprometido": float(row[1]),
        "realizado": float(row[2]), "a_realizar": float(row[3]),
        "projetos": int(row[4]),
    }
