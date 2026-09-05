"""Banco ISOLADO do módulo de Automações (encerramento/encaminhamento).

Separado do banco do portal. Guarda:
- Regra: subcategoria(s) → ação (encerrar/encaminhar) + fila destino + mensagem.
- LogAutomacao: histórico do que foi encerrado/encaminhado (auto, botão ou manual).
- Config: horários da rotina, se está ligada e a sessão do ServiceNow do
  usuário que ativou (para a rotina rodar com o usuário dele).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("automacoes.db")

DATABASE_URL: str = getattr(
    _cfg, "AUTOMACOES_DATABASE_URL", _config_mod._sqlite("automacoes"),
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


class Regra(Base):
    """Regra de automação: uma ou mais subcategorias → ação."""
    __tablename__ = "automacao_regra"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(160), default="")
    # subcategorias aceitas (uma por linha ou separadas por ;), casadas de
    # forma tolerante (minúsculas, sem _).
    subcategorias: Mapped[str] = mapped_column(Text, default="")
    acao: Mapped[str] = mapped_column(String(20), default="encerrar")  # encerrar|encaminhar
    fila_destino: Mapped[str] = mapped_column(String(160), default="")  # p/ encaminhar
    mensagem: Mapped[str] = mapped_column(Text, default="")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    ordem: Mapped[int] = mapped_column(Integer, default=100)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "nome": self.nome, "subcategorias": self.subcategorias,
            "acao": self.acao, "fila_destino": self.fila_destino,
            "mensagem": self.mensagem, "ativo": self.ativo, "ordem": self.ordem,
        }


class LogAutomacao(Base):
    """Histórico das ações da automação."""
    __tablename__ = "automacao_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    executado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)
    origem: Mapped[str] = mapped_column(String(20), default="")   # agendador|botao|manual
    usuario: Mapped[str] = mapped_column(String(120), default="")
    number: Mapped[str] = mapped_column(String(40), default="")
    sys_id: Mapped[str] = mapped_column(String(40), default="")
    subcategoria: Mapped[str] = mapped_column(String(160), default="")
    acao: Mapped[str] = mapped_column(String(20), default="")     # encerrar|encaminhar|ignorado
    fila_origem: Mapped[str] = mapped_column(String(160), default="")
    fila_destino: Mapped[str] = mapped_column(String(160), default="")
    resultado: Mapped[str] = mapped_column(String(20), default="")  # ok|erro|ignorado
    detalhe: Mapped[str] = mapped_column(Text, default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "executado_em": self.executado_em.isoformat() if self.executado_em else None,
            "origem": self.origem, "usuario": self.usuario, "number": self.number,
            "sys_id": self.sys_id, "subcategoria": self.subcategoria, "acao": self.acao,
            "fila_origem": self.fila_origem, "fila_destino": self.fila_destino,
            "resultado": self.resultado, "detalhe": self.detalhe,
        }


class Config(Base):
    __tablename__ = "automacao_config"
    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)


_CFG_KEY = "automacoes"

_MSG_ENCERRAR = (
    "Olá, tudo bem?\n\n"
    "Conforme código de rastreio, o equipamento novo foi entregue.\n"
    "Estamos encerrando este chamado.\n\n"
    "Dúvidas de processos de TI? Acesse a nossa FAQ! 🤩\n"
    "Acesse a nossa FAQ através do caminho abaixo:\n"
    "https://renner.sharepoint.com/sites/bibliotecasa?OR=Teams-HL&CT=1756993890685\n"
    "08. 1 Processos e Qualidade Informa | Renner > CSC TI > FAQ TI\n\n"
    "Para esclarecimento de dúvidas, contate o Núcleo de Atendimento ao Cliente "
    "Interno, de segunda à sexta-feira, das 9h às 19h, em nossos canais:\n"
    "💬 WhatsApp Conecta: (51) 99837.3485\n"
    "📱 App Conecta (disponível em Bluebirds, computadores e telefones corporativos)"
)

_MSG_ENCAMINHAR_AP = (
    "Prezados, equipamento entregue em loja.\n\n"
    "Gentileza, dar andamento na instalação no local."
)


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    _seed_regras()


def _seed_regras() -> None:
    """Cria as regras padrão na primeira execução (se não houver nenhuma)."""
    with SessionLocal.begin() as s:
        existe = s.scalar(select(Regra.id).limit(1))
        if existe:
            return
        s.add(Regra(
            nome="Access Point → REMOTO DISPATCH",
            subcategorias="access_point\nAccess Point",
            acao="encaminhar",
            fila_destino="TI_N2_FLD_RNR_LOJAS_REMOTO_DISPATCH",
            mensagem=_MSG_ENCAMINHAR_AP,
            ativo=True, ordem=10,
        ))
        s.add(Regra(
            nome="Equipamentos → Encerrar (entregue)",
            subcategorias=(
                "Card printer\nImpressora de Cartão\ncard_printer\n"
                "Coletor\ncoletor\n"
                "RFID Sled\nSled RFID\nSLED\nrfid_sled\nRFID\n"
                "Teclado fiscal\ntax keyboard\ntax_keyboard"
            ),
            acao="encerrar",
            fila_destino="",
            mensagem=_MSG_ENCERRAR,
            ativo=True, ordem=20,
        ))


# ── Regras ─────────────────────────────────────────────────────────────
def listar_regras() -> list[dict]:
    with SessionLocal() as s:
        rows = s.scalars(select(Regra).order_by(Regra.ordem, Regra.id)).all()
        return [r.to_dict() for r in rows]


def obter_regra(rid: int) -> Regra | None:
    with SessionLocal() as s:
        return s.get(Regra, rid)


def salvar_regra(dados: dict, rid: int | None = None) -> int:
    with SessionLocal.begin() as s:
        r = s.get(Regra, rid) if rid else None
        if r is None:
            r = Regra()
            s.add(r)
        r.nome = dados.get("nome", "") or ""
        r.subcategorias = dados.get("subcategorias", "") or ""
        r.acao = dados.get("acao", "encerrar") or "encerrar"
        r.fila_destino = dados.get("fila_destino", "") or ""
        r.mensagem = dados.get("mensagem", "") or ""
        r.ativo = bool(dados.get("ativo", True))
        try:
            r.ordem = int(dados.get("ordem", 100))
        except (ValueError, TypeError):
            r.ordem = 100
        r.atualizado_em = localnow()
        s.flush()
        return r.id


def excluir_regra(rid: int) -> bool:
    with SessionLocal.begin() as s:
        r = s.get(Regra, rid)
        if not r:
            return False
        s.delete(r)
        return True


# ── Logs ───────────────────────────────────────────────────────────────
def add_log(**kw) -> None:
    with SessionLocal.begin() as s:
        s.add(LogAutomacao(**kw))


def listar_logs(limit: int = 500, origem: str = "", q: str = "") -> list[dict]:
    with SessionLocal() as s:
        stmt = select(LogAutomacao).order_by(LogAutomacao.id.desc())
        if origem:
            stmt = stmt.where(LogAutomacao.origem == origem)
        rows = s.scalars(stmt.limit(2000)).all()
    out = [r.to_dict() for r in rows]
    if q:
        ql = q.lower()
        out = [x for x in out if ql in (x["number"] or "").lower()
               or ql in (x["subcategoria"] or "").lower()
               or ql in (x["detalhe"] or "").lower()]
    return out[:limit]


# ── Config ─────────────────────────────────────────────────────────────
def obter_config() -> dict:
    with SessionLocal() as s:
        row = s.get(Config, _CFG_KEY)
        if not row:
            return {}
        try:
            return json.loads(row.payload) or {}
        except Exception:  # noqa: BLE001
            return {}


def salvar_config(dados: dict) -> None:
    with SessionLocal.begin() as s:
        row = s.get(Config, _CFG_KEY)
        if row is None:
            row = Config(chave=_CFG_KEY)
            s.add(row)
        atual = {}
        try:
            atual = json.loads(row.payload) or {}
        except Exception:  # noqa: BLE001
            atual = {}
        atual.update(dados)
        row.payload = json.dumps(atual, ensure_ascii=False)
        row.atualizado_em = localnow()
