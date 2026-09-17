"""Banco ISOLADO do acesso Consulta Times.

Outros times (loja, suporte, fornecedores internos) entram por um link
próprio (`/consulta-times`), com o login da rede pelo ServiceNow, e só
quem foi liberado enxerga. A liberação é por login, feita na própria
tela por quem administra o módulo — mesmo desenho do Controle de
Orçamento, que a área já conhece.

Cada abertura e cada gravação ficam na trilha de acesso.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, create_engine, event, select
from db._esquema import UtcDateTime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("consulta_times.db")

DATABASE_URL: str = getattr(
    _cfg, "CONSULTA_TIMES_DATABASE_URL", _config_mod._sqlite("consulta_times"),
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
            def _pragmas(dbapi_conn, _record):  # noqa: ANN001
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


NIVEIS = ("view", "edit", "admin")


class Liberacao(Base):
    __tablename__ = "ct_liberacao"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    login: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(160), default="")
    nivel: Mapped[str] = mapped_column(String(10), default="view")
    criado_por: Mapped[str] = mapped_column(String(120), default="")
    criado_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)

    def to_dict(self) -> dict:
        return {"login": self.login, "nome": self.nome, "nivel": self.nivel,
                "criado_por": self.criado_por,
                "criado_em": self.criado_em.isoformat() if self.criado_em else None}


class Acesso(Base):
    __tablename__ = "ct_acesso"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    usuario: Mapped[str] = mapped_column(String(120), index=True)
    ip: Mapped[str] = mapped_column(String(80), default="")
    acao: Mapped[str] = mapped_column(String(40), index=True)
    detalhe: Mapped[str] = mapped_column(String(400), default="")
    quando: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow, index=True)


class Config(Base):
    """Configuração do espaço Times, no banco DELE.

    Não compartilha a chave `gestao_ativos` do portal de propósito: mexer
    aqui não pode mudar as telas do portal, e o contrário também não.
    """
    __tablename__ = "ct_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


# Listas do espaço Times. Começam vazias: os estoques dos outros times não
# são os do SPARE, e quem administra o espaço escolhe os dele.
PADROES = {"estoques": [], "corredores": []}


def ler_listas() -> dict:
    import json
    with SessionLocal() as s:
        atual = {c.chave: c.valor for c in s.execute(select(Config)).scalars()}
    saida = {}
    for chave, padrao in PADROES.items():
        bruto = atual.get(chave)
        if not bruto:
            saida[chave] = list(padrao)
            continue
        try:
            valor = json.loads(bruto)
        except (TypeError, ValueError):
            valor = []
        saida[chave] = [str(x).strip() for x in valor if str(x).strip()] if isinstance(valor, list) else list(padrao)
    return saida


def gravar_listas(pares: dict) -> None:
    import json
    with SessionLocal() as s:
        for chave, valor in pares.items():
            if chave not in PADROES:
                continue
            texto = json.dumps([str(x).strip() for x in (valor or []) if str(x).strip()])
            linha = s.get(Config, chave)
            if linha is None:
                s.add(Config(chave=chave, valor=texto))
            else:
                linha.valor = texto
        s.commit()


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "consulta_times")


def _norm(login: str) -> str:
    return (login or "").strip().lower()[:120]


def nivel_do_login(login: str) -> str:
    login = _norm(login)
    if not login:
        return ""
    try:
        with SessionLocal() as s:
            row = s.scalar(select(Liberacao).where(Liberacao.login == login))
            return row.nivel if row else ""
    except Exception:  # noqa: BLE001 — banco fora: ninguém liberado, ninguém derrubado
        return ""


def listar() -> list[dict]:
    with SessionLocal() as s:
        return [r.to_dict() for r in s.scalars(select(Liberacao).order_by(Liberacao.login)).all()]


def liberar(login: str, nivel: str, nome: str, por: str) -> dict:
    login = _norm(login)
    if not login:
        raise ValueError("Informe o login de rede.")
    if nivel not in NIVEIS:
        raise ValueError("Nível inválido.")
    with SessionLocal() as s:
        row = s.scalar(select(Liberacao).where(Liberacao.login == login))
        if row is None:
            row = Liberacao(login=login, criado_por=(por or "")[:120])
            s.add(row)
        row.nivel = nivel
        row.nome = (nome or "")[:160]
        s.commit()
        return row.to_dict()


def revogar(login: str) -> bool:
    with SessionLocal() as s:
        row = s.scalar(select(Liberacao).where(Liberacao.login == _norm(login)))
        if row is None:
            return False
        s.delete(row)
        s.commit()
        return True


def registrar_acesso(usuario: str, ip: str, acao: str, detalhe: str = "") -> None:
    try:
        with SessionLocal() as s:
            s.add(Acesso(usuario=(usuario or "")[:120], ip=(ip or "")[:80],
                         acao=(acao or "")[:40], detalhe=(detalhe or "")[:400]))
            s.commit()
    except Exception as exc:  # noqa: BLE001 — auditoria não bloqueia a tela
        _log.warning("consulta_times: acesso não registrado: %s", exc)


def listar_acessos(limit: int = 300) -> list[dict]:
    with SessionLocal() as s:
        return [{"usuario": a.usuario, "ip": a.ip, "acao": a.acao, "detalhe": a.detalhe,
                 "quando": a.quando.isoformat() if a.quando else None}
                for a in s.scalars(select(Acesso).order_by(Acesso.id.desc()).limit(limit)).all()]
