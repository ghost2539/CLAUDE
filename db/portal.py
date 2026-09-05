from __future__ import annotations
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    create_engine, String, Text, Boolean, DateTime, Date,
    Integer, BigInteger, Numeric, ForeignKey, UniqueConstraint,
    Index, JSON, func, select, text,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)
import bcrypt

from config import get_settings

_cfg = get_settings()
engine = create_engine(
    _cfg.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── ORM Models ──────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(180), default="")
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    auth_source: Mapped[str] = mapped_column(String(12), default="LOCAL")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_access: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    module: Mapped[str] = mapped_column(String(50))
    can_view: Mapped[bool] = mapped_column(Boolean, default=True)
    can_create: Mapped[bool] = mapped_column(Boolean, default=False)
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False)
    can_export: Mapped[bool] = mapped_column(Boolean, default=False)
    can_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint("user_id", "module"),)


class AccessLog(Base):
    __tablename__ = "access_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    login: Mapped[str] = mapped_column(String(80))
    auth_source: Mapped[str] = mapped_column(String(12))
    success: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str] = mapped_column(String(80), default="")
    detail: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(80), default="system")


class Classification(Base):
    __tablename__ = "classifications"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    description_pattern: Mapped[str] = mapped_column(String(500), index=True)
    company: Mapped[str] = mapped_column(String(40), default="")
    category: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(180))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class StorageLocation(Base):
    __tablename__ = "storage_locations"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company: Mapped[str] = mapped_column(String(40), default="")
    book_type_code: Mapped[str] = mapped_column(String(80), default="")
    asset_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    asset_number: Mapped[str] = mapped_column(String(120), default="", index=True)
    tag_number: Mapped[str] = mapped_column(String(180), default="", index=True)
    serial_number: Mapped[str] = mapped_column(String(180), default="", index=True)
    description: Mapped[str] = mapped_column(String(600), default="")
    category: Mapped[str] = mapped_column(
        String(120), default="NÃO CLASSIFICADA"
    )
    model: Mapped[str] = mapped_column(String(180), default="")
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(18, 2), nullable=True)
    dpis: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    acquisition_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="EBS")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReceiptCycle(Base):
    __tablename__ = "receipt_cycles"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    cycle_number: Mapped[int] = mapped_column(Integer)
    received_date: Mapped[date] = mapped_column(
        Date, default=date.today, index=True
    )
    iso_week: Mapped[str] = mapped_column(String(12), default="")
    status: Mapped[str] = mapped_column(String(40), default="RECEBIDO", index=True)
    location_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("storage_locations.id"), nullable=True
    )
    lot_number: Mapped[str] = mapped_column(String(120), default="", index=True)
    open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_by: Mapped[str] = mapped_column(String(80), default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    asset: Mapped[Asset] = relationship()
    location: Mapped[Optional[StorageLocation]] = relationship()
    __table_args__ = (
        UniqueConstraint("asset_id", "cycle_number"),
        Index("ix_cycle_period", "received_date", "status"),
    )


class Movement(Base):
    __tablename__ = "movements"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("receipt_cycles.id"), index=True
    )
    old_status: Mapped[str] = mapped_column(String(40), default="")
    new_status: Mapped[str] = mapped_column(String(40), default="")
    old_location: Mapped[str] = mapped_column(String(120), default="")
    new_location: Mapped[str] = mapped_column(String(120), default="")
    lot_number: Mapped[str] = mapped_column(String(120), default="")
    origin: Mapped[str] = mapped_column(String(60), default="PORTAL")
    note: Mapped[str] = mapped_column(Text, default="")
    username: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class LotSequence(Base):
    __tablename__ = "lot_sequences"
    prefix: Mapped[str] = mapped_column(String(30), primary_key=True)
    next_number: Mapped[int] = mapped_column(BigInteger, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Lot(Base):
    __tablename__ = "lots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    number: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    prefix: Mapped[str] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Repair(Base):
    __tablename__ = "repairs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    cycle_id: Mapped[int] = mapped_column(
        ForeignKey("receipt_cycles.id"), index=True
    )
    triage_min: Mapped[int] = mapped_column(Integer, default=0)
    repair_min: Mapped[int] = mapped_column(Integer, default=0)
    research_min: Mapped[int] = mapped_column(Integer, default=0)
    hygiene_min: Mapped[int] = mapped_column(Integer, default=0)
    total_min: Mapped[int] = mapped_column(Integer, default=0)
    hourly_rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    saving: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    result: Mapped[str] = mapped_column(String(50))
    technician: Mapped[str] = mapped_column(String(180))
    note: Mapped[str] = mapped_column(Text, default="")
    repair_date: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LocalAsset(Base):
    __tablename__ = "local_assets"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    branch: Mapped[str] = mapped_column(String(80), default="")
    asset_number: Mapped[str] = mapped_column(String(120), default="", index=True)
    tag_number: Mapped[str] = mapped_column(String(180), default="", index=True)
    serial_number: Mapped[str] = mapped_column(String(180), default="", index=True)
    description: Mapped[str] = mapped_column(String(600), default="")
    acquisition_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    load_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class LoadHistory(Base):
    __tablename__ = "load_history"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company: Mapped[str] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    mode: Mapped[str] = mapped_column(String(30))
    total_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    valid_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    rejected_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ── Database init ───────────────────────────────────────────────

def init_db() -> None:
    Base.metadata.create_all(engine)

    from sqlalchemy import inspect as sa_inspect, text as sa_text
    insp = sa_inspect(engine)
    if "users" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("users")}
        if "allowed" not in cols:
            with engine.begin() as conn:
                conn.execute(sa_text(
                    "ALTER TABLE users ADD COLUMN allowed BOOLEAN NOT NULL DEFAULT TRUE"
                ))

    with SessionLocal.begin() as s:
        defaults = {
            "visual": {
                "nome_app": "Portal de Operações - SPARE",
                "subtitulo": "Operações de ativos",
                "login_title": "Portal de Operações - SPARE",
                "footer": "SPARE - Portal de Operações",
                "fonte": "Inter",
                "cor_primaria": "#AB4807",
                "cor_fundo": "#090B0D",
                "cor_painel": "#111419",
                "cor_texto": "#E8E8E8",
                "cor_destaque": "#C79105",
            },
            "tv": {
                "title": "Painel de Operações",
                "interval": 60,
                "widgets": [
                    "stats", "chart_categoria", "chart_status", "last_received"
                ],
                "last_rows": 8,
            },
            "hourly_rate": {"value": _cfg.DEFAULT_HOURLY_RATE},
            "access_control": {"block_external": False},
        }
        for k, v in defaults.items():
            if not s.get(Setting, k):
                s.add(Setting(key=k, value=v))

        for p in ("VENDA", "TRIAGEM"):
            if not s.get(LotSequence, p):
                s.add(LotSequence(prefix=p, next_number=1))

        admin_login = _cfg.INITIAL_ADMIN_LOGIN
        if not admin_login:
            return
        u = s.scalar(select(User).where(User.login == admin_login))
        if not u:
            password = _cfg.INITIAL_ADMIN_PASSWORD.strip()
            u = User(
                login=admin_login,
                display_name="Administrador Principal",
                auth_source="LOCAL" if password else "AD",
                is_admin=True,
                active=True,
                password_hash=hash_password(password) if password else None,
                must_change_password=False,
            )
            s.add(u)
        else:
            u.is_admin = True
            u.active = True
