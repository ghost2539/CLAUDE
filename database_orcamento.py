"""Banco de dados EXCLUSIVO do módulo Controle de Orçamento de Portfólio.

Este módulo usa um engine/sessão próprios, totalmente separados de
``database.py`` (banco do portal), para que a tela de orçamento não impacte
as demais telas: nenhuma tabela é criada no banco principal e uma falha
aqui não afeta o restante do portal.

Por padrão os dados ficam em um arquivo SQLite em ``data/controle_orcamento.db``
(diretório já gravável pelo serviço). Para usar outro banco (por exemplo um
Postgres dedicado), defina ``CONTROLE_ORCAMENTO_DATABASE_URL``.

Regra de isolamento: este arquivo NÃO faz nada em tempo de import além de
definir classes e funções. Engine, conexão e criação de tabela acontecem
apenas na primeira requisição ao módulo.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Date, DateTime, Integer, Numeric, String, create_engine, event,
    func, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from config import get_settings

_cfg = get_settings()
_log = logging.getLogger("controle_orcamento")

# URL do banco separado. getattr com padrão: funciona mesmo se o config.py
# do servidor for uma versão antiga sem ORCAMENTO_DATABASE_URL.
DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_DATABASE_URL",
    "sqlite:///" + str(_cfg.ROOT / "data" / "controle_orcamento.db"),
)

# Nada é criado em tempo de import: engine e sessão nascem no primeiro
# uso, dentro de um endpoint deste módulo. Assim um problema de driver,
# permissão ou URL nunca afeta o startup do portal.
_engine = None
_factory = None
_engine_lock = threading.Lock()


def get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                is_sqlite = DATABASE_URL.startswith("sqlite")
                if is_sqlite:
                    # garante a pasta do arquivo (ex.: data/)
                    caminho = DATABASE_URL.replace("sqlite:///", "", 1)
                    if caminho and caminho != ":memory:":
                        import os
                        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
                eng = create_engine(
                    DATABASE_URL,
                    pool_pre_ping=True,
                    connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {},
                )
                if is_sqlite:
                    @event.listens_for(eng, "connect")
                    def _sqlite_pragmas(dbapi_conn, _record):
                        # WAL permite leituras concorrentes durante gravações;
                        # busy_timeout evita "database is locked" com vários usuários.
                        cur = dbapi_conn.cursor()
                        cur.execute("PRAGMA journal_mode=WAL")
                        cur.execute("PRAGMA busy_timeout=15000")
                        cur.close()
                _engine = eng
    return _engine


def _session_factory():
    global _factory
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
    return _factory


class _LazySessionLocal:
    """Mesma interface de ``sessionmaker`` (``SessionLocal()`` e
    ``SessionLocal.begin()``), mas só cria o engine quando usado."""

    def __call__(self):
        return _session_factory()()

    def begin(self):
        return _session_factory().begin()


SessionLocal = _LazySessionLocal()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class BudgetProject(Base):
    """Projeto do portfólio (linha da tabela editável do dashboard)."""
    __tablename__ = "budget_projects"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(40), default="", index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    kind: Mapped[str] = mapped_column(String(10), default="CAPEX")
    category: Mapped[str] = mapped_column(String(40), default="Outros")
    area: Mapped[str] = mapped_column(String(120), default="")
    stage: Mapped[str] = mapped_column(String(30), default="Planejamento")
    priority: Mapped[str] = mapped_column(String(10), default="Média")
    approved_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    committed: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    realized: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(80), default="")


# Projetos de exemplo, carregados apenas quando a tabela está vazia
SEED: list[dict] = [
    dict(code="PRJ-26001", name="Substituição de Coletor Principal", kind="CAPEX", category="Manutenção",       area="Operações",  stage="Em Execução",  priority="Alta",  approved_budget=18750000, committed=8250000,  realized=6125000, due_date=date(2026, 11, 30)),
    dict(code="PRJ-26002", name="Expansão Linha de Produção 2",      kind="CAPEX", category="Expansão",         area="Engenharia", stage="Em Execução",  priority="Alta",  approved_budget=32600000, committed=14800000, realized=9420000, due_date=date(2027, 3, 31)),
    dict(code="PRJ-26003", name="Automação Sistema de Envase",       kind="CAPEX", category="Estratégico",      area="Industrial", stage="Em Execução",  priority="Média", approved_budget=15200000, committed=6100000,  realized=4180000, due_date=date(2026, 9, 20)),
    dict(code="PRJ-26004", name="Adequação NR-12",                   kind="CAPEX", category="Legal/Compliance", area="EHS",        stage="Planejamento", priority="Alta",  approved_budget=6850000,  committed=1250000,  realized=350000,  due_date=date(2027, 6, 30)),
    dict(code="PRJ-26005", name="Novo Sistema ERP",                  kind="OPEX",  category="Estratégico",      area="TI",         stage="Aprovação",    priority="Alta",  approved_budget=9950000,  committed=0,        realized=0,       due_date=date(2027, 2, 28)),
    dict(code="PRJ-26006", name="Ampliação Armazém Central",         kind="CAPEX", category="Expansão",         area="Logística",  stage="Em Execução",  priority="Média", approved_budget=12400000, committed=5300000,  realized=2870000, due_date=date(2026, 8, 15)),
    dict(code="PRJ-26007", name="Eficiência Energética - Planta 1",  kind="CAPEX", category="Manutenção",       area="Utilidades", stage="Concluído",    priority="Baixa", approved_budget=2450000,  committed=2420000,  realized=2450000, due_date=date(2026, 4, 10)),
    dict(code="PRJ-26008", name="Pintura Externa Tanques",           kind="OPEX",  category="Manutenção",       area="Operações",  stage="Planejamento", priority="Baixa", approved_budget=1850000,  committed=120000,   realized=0,       due_date=date(2027, 5, 31)),
]

_init_lock = threading.Lock()
_ready = False


def init_db() -> None:
    """Cria a tabela (se não existir) e carrega os exemplos na primeira vez."""
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        with SessionLocal.begin() as s:
            if not s.scalar(select(func.count()).select_from(BudgetProject)):
                for i, row in enumerate(SEED):
                    s.add(BudgetProject(**row, sort_order=i + 1, updated_by="seed"))
        _ready = True


def ensure_db() -> None:
    """Inicializa sob demanda; erros são propagados ao chamador (endpoint)."""
    if not _ready:
        init_db()


def try_init_db() -> None:
    """Inicialização no startup que nunca derruba o portal em caso de falha."""
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001 — isolar falhas deste módulo
        _log.warning("Banco do Controle de Orçamento indisponível no startup: %s", exc)
