"""Banco ISOLADO do módulo de Monitoramento.

Guarda eventos de falha e checagens de saúde, separado do resto do sistema:
- falhas de API (5xx e exceções não tratadas), capturadas por middleware;
- falhas de integração (EBS, ServiceNow, Correios) e de automações;
- resultado das checagens de saúde.

Escrita é sempre tolerante a erro: monitoramento nunca pode derrubar o portal.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import (
    String, Text, Integer, DateTime, create_engine, event, select, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("monitoramento.db")

DATABASE_URL: str = getattr(
    _cfg, "MONITORAMENTO_DATABASE_URL", "sqlite:///data/monitoramento.db",
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


def localnow() -> datetime:
    return datetime.now()


class Base(DeclarativeBase):
    pass


class Evento(Base):
    """Um evento de monitoramento (falha, alerta ou checagem)."""
    __tablename__ = "monitor_evento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quando: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow, index=True)
    severidade: Mapped[str] = mapped_column(String(12), default="erro", index=True)   # erro|alerta|ok
    origem: Mapped[str] = mapped_column(String(24), default="", index=True)           # api|integracao|automacao|servidor
    alvo: Mapped[str] = mapped_column(String(200), default="")                        # rota / serviço
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    duracao_ms: Mapped[int] = mapped_column(Integer, default=0)
    usuario: Mapped[str] = mapped_column(String(120), default="")
    detalhe: Mapped[str] = mapped_column(Text, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "quando": self.quando.isoformat() if self.quando else None,
            "severidade": self.severidade, "origem": self.origem, "alvo": self.alvo,
            "status_code": self.status_code, "duracao_ms": self.duracao_ms,
            "usuario": self.usuario, "detalhe": self.detalhe,
        }


class Config(Base):
    """Configuração do módulo (chave/valor JSON) — alertas por e-mail etc."""
    __tablename__ = "monitor_config"

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)


CFG_ALERTAS = "alertas"

# Padrões do canal de alertas. O destinatário nasce fixo no e-mail do
# responsável pela área e pode ser ajustado na tela de Parâmetros.
ALERTAS_PADRAO: dict = {
    "ativo": False,
    "host": "",
    "porta": 25,
    "seguranca": "none",          # none | starttls | ssl
    "usuario": "",
    "senha_algo": "",             # fernet | xor  (a senha nunca é devolvida à tela)
    "senha_cifrada": "",
    "remetente": "portal-spare@lojasrenner.com.br",
    "destinatarios": "raphael.steilein@lojasrenner.com.br",
    "alerta_acesso_negado": True,  # tentativa com credencial válida e sem liberação
    "alerta_falhas": False,        # erros de API/integração/automação
    "intervalo_min": 5,            # janela mínima entre e-mails do mesmo assunto
    "max_por_hora": 20,
}


def obter_config(chave: str = CFG_ALERTAS) -> dict:
    base = dict(ALERTAS_PADRAO) if chave == CFG_ALERTAS else {}
    try:
        with SessionLocal() as s:
            row = s.get(Config, chave)
            if row and row.payload:
                base.update(json.loads(row.payload))
    except Exception as exc:  # noqa: BLE001
        _log.warning("Falha ao ler config de monitoramento (%s): %s", chave, exc)
    return base


def salvar_config(dados: dict, chave: str = CFG_ALERTAS) -> dict:
    atual = obter_config(chave)
    atual.update(dados or {})
    try:
        with SessionLocal.begin() as s:
            row = s.get(Config, chave)
            if not row:
                row = Config(chave=chave)
                s.add(row)
            row.payload = json.dumps(atual, ensure_ascii=False)
            row.atualizado_em = localnow()
    except Exception as exc:  # noqa: BLE001
        _log.warning("Falha ao gravar config de monitoramento (%s): %s", chave, exc)
    return atual


def init_db() -> None:
    Base.metadata.create_all(get_engine())


def registrar(severidade: str = "erro", origem: str = "", alvo: str = "",
              status_code: int = 0, duracao_ms: int = 0, usuario: str = "",
              detalhe: str = "") -> None:
    """Grava um evento. NUNCA levanta exceção (monitoramento não derruba nada)."""
    try:
        with SessionLocal.begin() as s:
            s.add(Evento(
                severidade=severidade or "erro", origem=origem or "", alvo=(alvo or "")[:200],
                status_code=int(status_code or 0), duracao_ms=int(duracao_ms or 0),
                usuario=(usuario or "")[:120], detalhe=(detalhe or "")[:4000],
            ))
    except Exception as exc:  # noqa: BLE001
        _log.warning("Falha ao registrar evento de monitoramento: %s", exc)


def listar(limit: int = 300, severidade: str = "", origem: str = "", q: str = "") -> list[dict]:
    try:
        with SessionLocal() as s:
            stmt = select(Evento).order_by(Evento.id.desc())
            if severidade:
                stmt = stmt.where(Evento.severidade == severidade)
            if origem:
                stmt = stmt.where(Evento.origem == origem)
            rows = s.scalars(stmt.limit(2000)).all()
        out = [r.to_dict() for r in rows]
        if q:
            ql = q.lower()
            out = [x for x in out
                   if ql in (x["alvo"] or "").lower() or ql in (x["detalhe"] or "").lower()]
        return out[:limit]
    except Exception:  # noqa: BLE001
        return []


def resumo(horas: int = 24) -> dict:
    """Contagem por severidade e por origem nas últimas N horas."""
    corte = localnow() - timedelta(hours=horas)
    try:
        with SessionLocal() as s:
            por_sev = dict(s.execute(
                select(Evento.severidade, func.count()).where(Evento.quando >= corte)
                .group_by(Evento.severidade)).all())
            por_org = dict(s.execute(
                select(Evento.origem, func.count()).where(Evento.quando >= corte)
                .group_by(Evento.origem)).all())
            ultima = s.scalar(select(func.max(Evento.quando)).where(Evento.severidade == "erro"))
        return {
            "horas": horas,
            "erros": int(por_sev.get("erro", 0)),
            "alertas": int(por_sev.get("alerta", 0)),
            "por_origem": {k or "?": int(v) for k, v in por_org.items()},
            "ultimo_erro": ultima.isoformat() if ultima else None,
        }
    except Exception:  # noqa: BLE001
        return {"horas": horas, "erros": 0, "alertas": 0, "por_origem": {}, "ultimo_erro": None}


def limpar_antigos(dias: int = 90) -> int:
    corte = localnow() - timedelta(days=dias)
    try:
        with SessionLocal.begin() as s:
            rows = s.scalars(select(Evento).where(Evento.quando < corte)).all()
            for r in rows:
                s.delete(r)
            return len(rows)
    except Exception:  # noqa: BLE001
        return 0
