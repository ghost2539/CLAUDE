"""Banco de dados ISOLADO do módulo de Indicadores (RMR).

Totalmente separado do banco do portal: usa sua própria engine e sua própria
URL (``INDICADORES_DATABASE_URL`` em config/ambiente). Default: SQLite local
em ``data/indicadores.db``. Guarda snapshots mensais dos indicadores em JSON,
para o RMR ter histórico e a página abrir rápido sem bater no ServiceNow toda
vez.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("indicadores.db")

DATABASE_URL: str = getattr(
    _cfg, "INDICADORES_DATABASE_URL",
    _config_mod._sqlite("indicadores"),
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
    """Mesma interface do sessionmaker (SessionLocal() e SessionLocal.begin())."""
    def __new__(cls):
        return _session_factory()()

    @staticmethod
    def begin():
        return _session_factory().begin()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def localnow() -> datetime:
    """Hora LOCAL do servidor (sem tz). Usada nos carimbos exibidos na tela —
    o SQLite não preserva fuso e o navegador interpretaria o UTC como local,
    mostrando o horário adiantado. Gravando local, a tela exibe a hora certa."""
    return datetime.now()


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    """Um cálculo dos indicadores para um mês de referência (YYYY-MM)."""
    __tablename__ = "indicador_snapshot"

    referencia: Mapped[str] = mapped_column(String(7), primary_key=True)  # "YYYY-MM"
    payload: Mapped[str] = mapped_column(Text, nullable=False)            # JSON
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    criado_por: Mapped[str] = mapped_column(String(120), default="")

    def to_dict(self) -> dict:
        try:
            dados = json.loads(self.payload)
        except Exception:  # noqa: BLE001
            dados = {}
        return {
            "referencia": self.referencia,
            "criado_em": self.criado_em.isoformat() if self.criado_em else None,
            "criado_por": self.criado_por,
            "dados": dados,
        }


class Config(Base):
    """Configuração dos indicadores editável pela UI (sobrescreve os defaults
    do config.py). Guardada como um único registro JSON."""
    __tablename__ = "indicador_config"

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)  # "indicadores"
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")


def init_db() -> None:
    """Cria as tabelas se não existirem (chamado no startup, tolerante a erro)."""
    Base.metadata.create_all(get_engine())


_CFG_KEY = "indicadores"


def obter_config() -> dict:
    """Retorna o dict de overrides salvos (vazio se nunca configurado)."""
    with SessionLocal() as s:
        row = s.get(Config, _CFG_KEY)
        if not row:
            return {}
        try:
            return json.loads(row.payload) or {}
        except Exception:  # noqa: BLE001
            return {}


def salvar_config(dados: dict, atualizado_por: str = "") -> None:
    with SessionLocal.begin() as s:
        row = s.get(Config, _CFG_KEY)
        if row is None:
            row = Config(chave=_CFG_KEY)
            s.add(row)
        row.payload = json.dumps(dados, ensure_ascii=False)
        row.atualizado_em = localnow()
        row.atualizado_por = atualizado_por or ""


def salvar_snapshot(referencia: str, dados: dict, criado_por: str = "") -> None:
    """Grava (ou substitui) o snapshot de um mês de referência."""
    with SessionLocal.begin() as s:
        row = s.get(Snapshot, referencia)
        if row is None:
            row = Snapshot(referencia=referencia)
            s.add(row)
        row.payload = json.dumps(dados, ensure_ascii=False)
        row.criado_em = localnow()
        row.criado_por = criado_por or ""


def ultimo_snapshot() -> dict | None:
    with SessionLocal() as s:
        row = (
            s.query(Snapshot)
            .order_by(Snapshot.referencia.desc())
            .first()
        )
        return row.to_dict() if row else None


def obter_snapshot(referencia: str) -> dict | None:
    with SessionLocal() as s:
        row = s.get(Snapshot, referencia)
        return row.to_dict() if row else None


def listar_referencias() -> list[str]:
    with SessionLocal() as s:
        rows = s.query(Snapshot.referencia).order_by(Snapshot.referencia.desc()).all()
        return [r[0] for r in rows]
