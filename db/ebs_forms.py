"""Banco ISOLADO do RPA de EBS Forms.

Guarda três coisas:

- `forms_execucao`: cada rodada do robô (quem pediu, o que pediu, log passo a
  passo, capturas de tela, resultado ou erro). É o que se olha quando algo
  dá errado numa tela que ninguém vê.
- `forms_roteiro`: as sequências de teclas por tela. Ficam no banco para
  serem ajustadas pela tela de administração sem reiniciar o serviço.
- `forms_ativo`: o que já foi coletado, por critério e livro — evita abrir
  o Forms de novo para o mesmo ativo dentro do prazo de validade.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Integer, String, Text, create_engine, event, func, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("ebs_forms.db")

DATABASE_URL: str = getattr(_cfg, "EBS_FORMS_DATABASE_URL", _config_mod._sqlite("ebs_forms"))

_engine = None
_factory = None
_PK = BigInteger().with_variant(Integer, "sqlite")


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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Execucao(Base):
    __tablename__ = "forms_execucao"
    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    tipo: Mapped[str] = mapped_column(String(40))            # teste_abertura | consulta
    parametros: Mapped[str] = mapped_column(Text, default="{}")
    usuario: Mapped[str] = mapped_column(String(120), default="")
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fim: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    situacao: Mapped[str] = mapped_column(String(20), default="rodando")  # rodando|ok|erro
    erro: Mapped[str] = mapped_column(Text, default="")
    log: Mapped[str] = mapped_column(Text, default="")
    resultado: Mapped[str] = mapped_column(Text, default="{}")
    capturas: Mapped[str] = mapped_column(Text, default="[]")


class Roteiro(Base):
    __tablename__ = "forms_roteiro"
    nome: Mapped[str] = mapped_column(String(60), primary_key=True)
    descricao: Mapped[str] = mapped_column(Text, default="")
    passos: Mapped[str] = mapped_column(Text, default="[]")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")


class Ativo(Base):
    __tablename__ = "forms_ativo"
    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    criterio: Mapped[str] = mapped_column(String(120), index=True)
    livro: Mapped[str] = mapped_column(String(60), default="")
    dados: Mapped[str] = mapped_column(Text, default="{}")
    coletado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    execucao_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)


_init_lock = threading.Lock()
_initialized = False


def init_db() -> None:
    global _initialized
    with _init_lock:
        if _initialized:
            return
        Base.metadata.create_all(get_engine())
        _semear_roteiros()
        _initialized = True


def ensure_db() -> None:
    if not _initialized:
        init_db()


def _semear_roteiros() -> None:
    """Roteiros padrão entram só se não existirem — ajuste feito na tela é preservado."""
    from integracoes.ebs_forms import ROTEIROS_PADRAO
    with SessionLocal() as s:
        existentes = {r.nome for r in s.execute(select(Roteiro)).scalars()}
        for nome, r in ROTEIROS_PADRAO.items():
            if nome not in existentes:
                s.add(Roteiro(nome=nome, descricao=r["descricao"],
                              passos=json.dumps(r["passos"], ensure_ascii=False), atualizado_por="padrão"))
        s.commit()


# ── execuções ───────────────────────────────────────────────────────────
def criar_execucao(tipo: str, parametros: dict, usuario: str) -> int:
    ensure_db()
    with SessionLocal() as s:
        e = Execucao(tipo=tipo, parametros=json.dumps(parametros, ensure_ascii=False), usuario=usuario)
        s.add(e)
        s.commit()
        return int(e.id)


def anexar_log(execucao_id: int, linha: str) -> None:
    with SessionLocal() as s:
        e = s.get(Execucao, execucao_id)
        if e is None:
            return
        marca = datetime.now().strftime("%H:%M:%S")
        e.log = (e.log or "") + f"[{marca}] {linha}\n"
        s.commit()


def concluir_execucao(execucao_id: int, ok: bool, resultado: dict | None = None,
                      erro: str = "", capturas: list[str] | None = None) -> None:
    with SessionLocal() as s:
        e = s.get(Execucao, execucao_id)
        if e is None:
            return
        e.fim = utcnow()
        e.situacao = "ok" if ok else "erro"
        e.erro = erro[:4000]
        if resultado is not None:
            e.resultado = json.dumps(resultado, ensure_ascii=False, default=str)
        if capturas is not None:
            e.capturas = json.dumps(capturas)
        s.commit()


def _exec_dict(e: Execucao, com_log: bool) -> dict[str, Any]:
    d = {
        "id": e.id, "tipo": e.tipo, "usuario": e.usuario, "situacao": e.situacao,
        "inicio": e.inicio.isoformat() if e.inicio else None,
        "fim": e.fim.isoformat() if e.fim else None,
        "erro": e.erro, "parametros": json.loads(e.parametros or "{}"),
        "capturas": json.loads(e.capturas or "[]"),
    }
    if com_log:
        d["log"] = e.log
        d["resultado"] = json.loads(e.resultado or "{}")
    return d


def obter_execucao(execucao_id: int) -> Optional[dict]:
    ensure_db()
    with SessionLocal() as s:
        e = s.get(Execucao, execucao_id)
        return _exec_dict(e, True) if e else None


def listar_execucoes(limite: int = 50) -> list[dict]:
    ensure_db()
    with SessionLocal() as s:
        q = select(Execucao).order_by(Execucao.id.desc()).limit(limite)
        return [_exec_dict(e, False) for e in s.execute(q).scalars()]


# ── roteiros ────────────────────────────────────────────────────────────
def listar_roteiros() -> list[dict]:
    ensure_db()
    with SessionLocal() as s:
        out = []
        for r in s.execute(select(Roteiro).order_by(Roteiro.nome)).scalars():
            out.append({"nome": r.nome, "descricao": r.descricao, "ativo": r.ativo,
                        "passos": json.loads(r.passos or "[]"),
                        "atualizado_em": r.atualizado_em.isoformat() if r.atualizado_em else None,
                        "atualizado_por": r.atualizado_por})
        return out


def roteiros_ativos() -> dict[str, list[dict]]:
    return {r["nome"]: r["passos"] for r in listar_roteiros() if r["ativo"]}


def salvar_roteiro(nome: str, descricao: str, passos: list[dict], ativo: bool, usuario: str) -> None:
    ensure_db()
    with SessionLocal() as s:
        r = s.get(Roteiro, nome)
        if r is None:
            r = Roteiro(nome=nome)
            s.add(r)
        r.descricao = descricao
        r.passos = json.dumps(passos, ensure_ascii=False)
        r.ativo = ativo
        r.atualizado_por = usuario
        s.commit()


def restaurar_roteiro(nome: str, usuario: str) -> bool:
    from integracoes.ebs_forms import ROTEIROS_PADRAO
    padrao = ROTEIROS_PADRAO.get(nome)
    if not padrao:
        return False
    salvar_roteiro(nome, padrao["descricao"], padrao["passos"], True, f"{usuario} (padrão)")
    return True


# ── ativos coletados ────────────────────────────────────────────────────
def salvar_ativo(criterio: str, livro: str, dados: dict, execucao_id: int | None) -> None:
    ensure_db()
    with SessionLocal() as s:
        s.add(Ativo(criterio=criterio.strip().upper(), livro=livro,
                    dados=json.dumps(dados, ensure_ascii=False, default=str), execucao_id=execucao_id))
        s.commit()


def buscar_ativo(criterio: str, validade_horas: int = 24) -> Optional[dict]:
    ensure_db()
    limite = utcnow() - timedelta(hours=validade_horas)
    with SessionLocal() as s:
        q = (select(Ativo).where(Ativo.criterio == criterio.strip().upper(), Ativo.coletado_em >= limite)
             .order_by(Ativo.id.desc()).limit(1))
        a = s.execute(q).scalars().first()
        if a is None:
            return None
        return {"criterio": a.criterio, "livro": a.livro, "dados": json.loads(a.dados or "{}"),
                "coletado_em": a.coletado_em.isoformat(), "execucao_id": a.execucao_id}


def contagens() -> dict[str, int]:
    ensure_db()
    with SessionLocal() as s:
        return {
            "execucoes": int(s.execute(select(func.count(Execucao.id))).scalar() or 0),
            "ativos": int(s.execute(select(func.count(Ativo.id))).scalar() or 0),
        }
