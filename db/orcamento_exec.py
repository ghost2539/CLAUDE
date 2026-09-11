"""Banco de dados EXCLUSIVO do módulo "Controle de Orçamento — Execução CAPEX".

Banco do Controle de Orçamento (execução CAPEX). Usa um
engine/sessão próprios e um arquivo/banco SEPARADO, para que este dashboard e o
o portal NUNCA compartilhem dados.

Por padrão os dados ficam em ``data/controle_orcamento_exec.db`` (SQLite). Para
outro banco (ex.: MySQL no servidor novo), defina ``ORCAMENTO_EXEC_DATABASE_URL``.

O modelo tem a coluna ``a_realizar`` (recebe o
``saldo_dia`` vindo da API de CAPEX do EBS).

Regra de isolamento: nada é criado em tempo de import — engine, conexão e
criação de tabela só acontecem na primeira requisição ao módulo.
"""
from __future__ import annotations

import importlib
import logging
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, create_engine,
    event, func, select,
)
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from config import get_settings

_cfg = get_settings()
_log = logging.getLogger("controle_orcamento_exec")

DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_EXEC_DATABASE_URL",
    "sqlite:///" + str(_cfg.ROOT / "data" / "controle_orcamento_exec.db"),
)

_engine = None
_factory = None
_engine_lock = threading.Lock()


def url_efetiva():
    """Usa o MESMO driver do portal quando o backend coincide (evita depender
    de um pacote que o portal não tenha)."""
    url = make_url(DATABASE_URL)
    try:
        portal = make_url(_cfg.DATABASE_URL)
    except Exception:  # noqa: BLE001
        return url
    if (url.get_backend_name() == portal.get_backend_name()
            and url.drivername != portal.drivername):
        url = url.set(drivername=portal.drivername)
    return _driver_instalado(url)


_DRIVERS_PG = ("psycopg2", "psycopg", "pg8000")


def _driver_instalado(url):
    if url.get_backend_name() != "postgresql":
        return url
    atual = url.get_driver_name()
    try:
        importlib.import_module(atual)
        return url
    except ImportError:
        pass
    for nome in _DRIVERS_PG:
        try:
            importlib.import_module(nome)
        except ImportError:
            continue
        return url.set(drivername=f"postgresql+{nome}")
    return url


def get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = url_efetiva()
                is_sqlite = url.get_backend_name() == "sqlite"
                if is_sqlite:
                    caminho = url.database or ""
                    if caminho and caminho != ":memory:":
                        import os
                        os.makedirs(os.path.dirname(os.path.abspath(caminho)), exist_ok=True)
                eng = create_engine(
                    url,
                    pool_pre_ping=True,
                    connect_args={"check_same_thread": False, "timeout": 15} if is_sqlite else {},
                )
                if is_sqlite:
                    @event.listens_for(eng, "connect")
                    def _sqlite_pragmas(dbapi_conn, _record):
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
    """Projeto/demanda acompanhado (uma linha da tabela do dashboard)."""
    __tablename__ = "budget_projects"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    code: Mapped[str] = mapped_column(String(40), default="", index=True)   # nº do projeto (ID que puxa do EBS)
    name: Mapped[str] = mapped_column(String(300), default="")              # Projeto/Demanda (manual)
    kind: Mapped[str] = mapped_column(String(10), default="CAPEX")
    category: Mapped[str] = mapped_column(String(40), default="Outros")
    area: Mapped[str] = mapped_column(String(120), default="")
    stage: Mapped[str] = mapped_column(String(30), default="Planejamento")
    priority: Mapped[str] = mapped_column(String(10), default="Média")
    approved_budget: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)   # saldo_inicial
    committed: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)         # comprometido + reservados
    realized: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)          # realizado
    a_realizar: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)        # saldo_dia (pode ser negativo)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)                  # se True, "Atualizar (EBS)" NÃO altera
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    updated_by: Mapped[str] = mapped_column(String(80), default="")


class BudgetCategory(Base):
    """Categoria de projeto (editável pelo usuário)."""
    __tablename__ = "budget_categories"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(60), unique=True)
    color: Mapped[str] = mapped_column(String(9), default="#9ca3af")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Acesso(Base):
    """Trilha de acesso da tela: quem abriu e quem alterou o quê.

    Fica no banco do próprio módulo — a tela tem permissão exclusiva e o
    registro de quem viu a informação faz parte dela, não do portal.
    """
    __tablename__ = "budget_acessos"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    quando: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    usuario: Mapped[str] = mapped_column(String(120), default="", index=True)
    ip: Mapped[str] = mapped_column(String(80), default="")
    acao: Mapped[str] = mapped_column(String(40), default="")      # abrir | consultar | incluir | alterar | excluir | sincronizar | negado
    detalhe: Mapped[str] = mapped_column(String(400), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "quando": self.quando.isoformat() if self.quando else None,
            "usuario": self.usuario, "ip": self.ip,
            "acao": self.acao, "detalhe": self.detalhe,
        }


class BudgetPermissao(Base):
    """Liberação de acesso PRÓPRIA do módulo (não é a permissão do portal).

    Quem é ADMIN do módulo (marcado em Parâmetros) libera aqui, por login,
    quem pode ver ou editar este módulo — sem depender da grade de permissões
    do portal. O login continua sendo o do SSO (ServiceNow).
    """
    __tablename__ = "budget_permissoes"
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    login: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(160), default="")
    nivel: Mapped[str] = mapped_column(String(10), default="view")   # view | edit | admin
    criado_por: Mapped[str] = mapped_column(String(120), default="")
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    def to_dict(self) -> dict:
        return {
            "login": self.login, "nome": self.nome, "nivel": self.nivel,
            "criado_por": self.criado_por,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
        }


NIVEIS_MODULO = ("view", "edit", "admin")


def _norm_login(login: str) -> str:
    return (login or "").strip().lower()[:120]


def nivel_do_login(login: str) -> str:
    """Nível liberado para o login na lista do módulo, ou "" se não houver."""
    login = _norm_login(login)
    if not login:
        return ""
    try:
        with SessionLocal() as s:
            row = s.scalar(select(BudgetPermissao).where(BudgetPermissao.login == login))
            return row.nivel if row else ""
    except Exception:  # noqa: BLE001
        return ""


def listar_permissoes_modulo() -> list[dict]:
    try:
        with SessionLocal() as s:
            stmt = select(BudgetPermissao).order_by(BudgetPermissao.login)
            return [r.to_dict() for r in s.scalars(stmt).all()]
    except Exception:  # noqa: BLE001
        return []


def definir_permissao_modulo(login: str, nivel: str, nome: str, criado_por: str) -> dict:
    login = _norm_login(login)
    if not login:
        raise ValueError("Informe o login (usuário de rede).")
    if nivel not in NIVEIS_MODULO:
        raise ValueError("Nível inválido.")
    with SessionLocal.begin() as s:
        row = s.scalar(select(BudgetPermissao).where(BudgetPermissao.login == login))
        if row is None:
            row = BudgetPermissao(login=login, criado_por=(criado_por or "")[:120])
            s.add(row)
        row.nivel = nivel
        if nome is not None:
            row.nome = (nome or "")[:160]
        s.flush()
        return row.to_dict()


def remover_permissao_modulo(login: str) -> bool:
    login = _norm_login(login)
    with SessionLocal.begin() as s:
        row = s.scalar(select(BudgetPermissao).where(BudgetPermissao.login == login))
        if row is None:
            return False
        s.delete(row)
        return True


def registrar_acesso(usuario: str, ip: str, acao: str, detalhe: str = "") -> None:
    """Grava uma linha na trilha. Nunca levanta: auditoria não bloqueia a tela."""
    try:
        with SessionLocal.begin() as s:
            s.add(Acesso(usuario=(usuario or "")[:120], ip=(ip or "")[:80],
                         acao=(acao or "")[:40], detalhe=(detalhe or "")[:400]))
    except Exception as exc:  # noqa: BLE001
        _log.warning("Falha ao registrar acesso do Controle de Orçamento: %s", exc)


def listar_acessos(limit: int = 300, usuario: str = "", acao: str = "") -> list[dict]:
    try:
        with SessionLocal() as s:
            stmt = select(Acesso).order_by(Acesso.id.desc())
            if usuario:
                stmt = stmt.where(Acesso.usuario == usuario)
            if acao:
                stmt = stmt.where(Acesso.acao == acao)
            return [r.to_dict() for r in s.scalars(stmt.limit(max(1, min(limit, 2000)))).all()]
    except Exception:  # noqa: BLE001
        return []


# Só categorias iniciais (para o seletor não nascer vazio). SEM projetos de
# exemplo: os projetos são incluídos pelo usuário e puxados do EBS.
CATEGORY_SEED: list[tuple[str, str]] = [
    ("Coletores", "#2563eb"),
    ("Equipamentos TI", "#f97316"),
    ("Infraestrutura", "#8b5cf6"),
    ("Software", "#22c55e"),
    ("Outros", "#9ca3af"),
]

_init_lock = threading.Lock()
_ready = False


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        _ensure_coluna_a_realizar()
        with SessionLocal.begin() as s:
            if not s.scalar(select(func.count()).select_from(BudgetCategory)):
                for i, (name, color) in enumerate(CATEGORY_SEED):
                    s.add(BudgetCategory(name=name, color=color, sort_order=i + 1))
        _ready = True


def _ensure_coluna_a_realizar() -> None:
    """Migração leve: adiciona colunas novas se um banco antigo já existir."""
    from sqlalchemy import inspect as sa_inspect, text as sa_text
    eng = get_engine()
    insp = sa_inspect(eng)
    if "budget_projects" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("budget_projects")}
    with eng.begin() as conn:
        if "a_realizar" not in cols:
            conn.execute(sa_text("ALTER TABLE budget_projects ADD COLUMN a_realizar NUMERIC(18,2) DEFAULT 0"))
        if "synced_at" not in cols:
            conn.execute(sa_text("ALTER TABLE budget_projects ADD COLUMN synced_at TIMESTAMP NULL"))
        if "locked" not in cols:
            conn.execute(sa_text("ALTER TABLE budget_projects ADD COLUMN locked BOOLEAN DEFAULT 0"))


def criar_tabelas() -> None:
    Base.metadata.create_all(get_engine())
    _ensure_coluna_a_realizar()


def ensure_db() -> None:
    if not _ready:
        init_db()


def try_init_db() -> None:
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Banco do Controle de Orçamento (Execução) indisponível no startup: %s", exc)
