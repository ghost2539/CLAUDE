"""Banco ISOLADO do Orçamento de Manutenção (reparo de coletores e SLEDs).

Substitui a planilha `Manutenção.xlsx` do time SPARE. Contrato completo em
`docs/ORCAMENTO_MANUTENCAO.md`. Nada aqui escreve no banco de outro módulo.

Tabelas:
- `manut_reparo`      — uma linha por RMA (chamado de reparo do fornecedor);
- `manut_config`      — chave/valor JSON (cota mensal, limiar, valores padrão);
- `manut_importacao`  — log de cada planilha importada.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Integer, Numeric, String, Text,
    create_engine, event, select, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("orcamento_manutencao.db")

DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_MANUTENCAO_DATABASE_URL",
    _config_mod._sqlite("orcamento_manutencao"),
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


class Base(DeclarativeBase):
    pass


# ── Vocabulário canônico (seção 3 do contrato) ──────────────────────────
STATUS_ROTULOS = {
    "APROVADO": "Aprovado",
    "REPROVADO": "Reprovado",
    "AGUARDANDO_APROVACAO": "Aguardando Aprovação",
    "AGUARDANDO_ORCAMENTO": "Aguardando Orçamento",
    "VALIDANDO_ORCAMENTO": "Validando Orçamento",
}
STATUS_PENDENTES = ("AGUARDANDO_APROVACAO", "AGUARDANDO_ORCAMENTO", "VALIDANDO_ORCAMENTO")
TIPOS_ROTULOS = {"CONTRATO": "Contrato", "AVULSA": "Avulsa"}
RETORNO_ROTULOS = {"DEVOLVIDO": "Devolvido", "EM_MANUTENCAO": "Em Manutenção"}
FAMILIAS = ("COLETOR", "SLED")
EMPRESAS = ("RENNER", "CAMICADO", "YOUCOM")
FONTES_VALOR = ("EBS", "PLANILHA", "PADRAO", "MANUAL")

# Padrões da configuração (seção 2.2). Os valores de compra são os que a
# planilha histórica usa.
CONFIG_PADRAO: dict = {
    "cota_mensal": {},
    "limiar_percentual": 0.60,
    "valor_compra_padrao": {
        "Coletor": 4978.29,
        "Coletor HF550X": 4589.79,
        "Sled RFID": 3731.51,
        "Sled RFR901": 3735.95,
    },
}


def _f(v):
    return None if v is None else float(v)


def _dt(v):
    return v.isoformat() if v else None


class Reparo(Base):
    """Uma linha por RMA."""
    __tablename__ = "manut_reparo"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    rma: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    serie: Mapped[str] = mapped_column(String(60), default="", index=True)
    loja: Mapped[int | None] = mapped_column(Integer, nullable=True)
    categoria: Mapped[str] = mapped_column(String(40), default="", index=True)
    familia: Mapped[str] = mapped_column(String(10), default="", index=True)
    empresa: Mapped[str] = mapped_column(String(20), default="")
    # 8.1 — vêm da planilha do fornecedor (lote de reparo).
    origem_equipamento: Mapped[str] = mapped_column(String(10), default="")
    disponibilizacao: Mapped[date | None] = mapped_column(Date, nullable=True)

    orcamento: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), default=0)
    garantia: Mapped[bool] = mapped_column(Boolean, default=False)
    valor_compra: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    valor_compra_fonte: Mapped[str] = mapped_column(String(12), default="")
    percentual: Mapped[float | None] = mapped_column(Numeric(8, 4, asdecimal=False), nullable=True)
    avaliacao: Mapped[str] = mapped_column(String(10), default="")

    status: Mapped[str] = mapped_column(String(24), default="AGUARDANDO_ORCAMENTO", index=True)
    status_original: Mapped[str] = mapped_column(String(60), default="")
    tipo_manutencao: Mapped[str] = mapped_column(String(10), default="CONTRATO")
    tipo_original: Mapped[str] = mapped_column(String(60), default="")
    status_retorno: Mapped[str] = mapped_column(String(14), default="EM_MANUTENCAO", index=True)

    ano: Mapped[int] = mapped_column(Integer, index=True)
    mes_referencia: Mapped[str | None] = mapped_column(String(7), nullable=True, index=True)
    ano_devolucao: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 8.3 — confirmação do retorno pelo portal.
    devolvido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    devolvido_por: Mapped[str] = mapped_column(String(80), default="")
    lote_prime: Mapped[str] = mapped_column(String(200), default="")
    po: Mapped[str] = mapped_column(String(40), default="")
    qtde: Mapped[int] = mapped_column(Integer, default=1)
    observacao: Mapped[str] = mapped_column(Text, default="")

    ebs_consultado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ebs_erro: Mapped[str] = mapped_column(String(200), default="")

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    criado_por: Mapped[str] = mapped_column(String(80), default="")
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")
    origem: Mapped[str] = mapped_column(String(12), default="PORTAL")

    def to_dict(self) -> dict:
        return {
            "id": self.id, "rma": self.rma, "serie": self.serie, "loja": self.loja,
            "categoria": self.categoria, "familia": self.familia, "empresa": self.empresa,
            "origem_equipamento": self.origem_equipamento or "",
            "disponibilizacao": _dt(self.disponibilizacao),
            "orcamento": float(self.orcamento or 0), "garantia": bool(self.garantia),
            "valor_compra": _f(self.valor_compra), "valor_compra_fonte": self.valor_compra_fonte or "",
            "percentual": _f(self.percentual), "avaliacao": self.avaliacao or "",
            "status": self.status, "status_rotulo": STATUS_ROTULOS.get(self.status, self.status),
            "status_original": self.status_original or "",
            "tipo_manutencao": self.tipo_manutencao, "tipo_original": self.tipo_original or "",
            "status_retorno": self.status_retorno,
            "ano": self.ano, "mes_referencia": self.mes_referencia,
            "ano_devolucao": self.ano_devolucao, "devolvido_em": _dt(self.devolvido_em),
            "devolvido_por": self.devolvido_por or "",
            "lote_prime": self.lote_prime or "", "po": self.po or "",
            "qtde": int(self.qtde or 1), "observacao": self.observacao or "",
            "ebs_consultado_em": _dt(self.ebs_consultado_em), "ebs_erro": self.ebs_erro or "",
            "criado_em": _dt(self.criado_em), "atualizado_em": _dt(self.atualizado_em),
            "criado_por": self.criado_por or "", "atualizado_por": self.atualizado_por or "",
            "origem": self.origem or "",
        }


class Configuracao(Base):
    """Chave/valor com JSON em texto."""
    __tablename__ = "manut_config"

    chave: Mapped[str] = mapped_column(String(40), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="{}")
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")


class Importacao(Base):
    """Log de cada planilha importada."""
    __tablename__ = "manut_importacao"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True
    )
    arquivo: Mapped[str] = mapped_column(String(200), default="")
    usuario: Mapped[str] = mapped_column(String(80), default="")
    quando: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lidas: Mapped[int] = mapped_column(Integer, default=0)
    incluidas: Mapped[int] = mapped_column(Integer, default=0)
    atualizadas: Mapped[int] = mapped_column(Integer, default=0)
    rejeitadas: Mapped[int] = mapped_column(Integer, default=0)
    detalhes: Mapped[str] = mapped_column(Text, default="[]")

    def to_dict(self) -> dict:
        try:
            detalhes = json.loads(self.detalhes or "[]")
        except ValueError:
            detalhes = []
        return {
            "id": self.id, "arquivo": self.arquivo, "usuario": self.usuario,
            "quando": _dt(self.quando), "lidas": self.lidas, "incluidas": self.incluidas,
            "atualizadas": self.atualizadas, "rejeitadas": self.rejeitadas,
            "detalhes": detalhes,
        }


_init_lock = threading.Lock()
_ready = False

# Colunas acrescentadas depois que o banco de produção já existia (8.1).
# `create_all` só cria tabela nova: em banco antigo elas entram por ALTER.
COLUNAS_MIGRAVEIS: dict[str, tuple[str, ...]] = {
    "manut_reparo": ("origem_equipamento", "disponibilizacao", "devolvido_em",
                     "devolvido_por", "po"),
}


def _colunas_existentes(conn, tabela: str) -> set[str]:
    """Colunas que a tabela tem hoje, pelo catálogo do banco em uso.
    Conjunto vazio significa "tabela ausente" — aí não há o que migrar."""
    if conn.dialect.name == "sqlite":
        # PRAGMA não aceita parâmetro; o nome vem só de COLUNAS_MIGRAVEIS.
        linhas = conn.exec_driver_sql(f"PRAGMA table_info({tabela})").all()
        return {str(l[1]) for l in linhas}
    linhas = conn.execute(
        text("SELECT column_name FROM information_schema.columns "
             "WHERE table_name = :t AND table_schema = current_schema()"),
        {"t": tabela},
    ).all()
    return {str(l[0]) for l in linhas}


def migrar_colunas(engine) -> list[str]:
    """Acrescenta as colunas que faltarem, uma por vez e cada uma na sua
    transação. NUNCA recria nem apaga tabela: falha vira log e a próxima
    coluna segue. Devolve as colunas efetivamente acrescentadas."""
    acrescentadas: list[str] = []
    for tabela, nomes in COLUNAS_MIGRAVEIS.items():
        try:
            with engine.connect() as conn:
                existentes = _colunas_existentes(conn, tabela)
        except Exception as exc:  # noqa: BLE001
            _log.warning("migração: não consegui ler as colunas de %s: %s", tabela, exc)
            continue
        if not existentes:
            continue
        modelo = Base.metadata.tables[tabela]
        for nome in nomes:
            if nome in existentes:
                continue
            coluna = modelo.c[nome]
            tipo = coluna.type.compile(engine.dialect)
            # O default do modelo é do Python; repeti-lo no ALTER preenche as
            # linhas que já existem em vez de deixá-las nulas.
            padrao = getattr(coluna.default, "arg", None)
            sufixo = f" DEFAULT '{padrao}'" if isinstance(padrao, str) else ""
            try:
                with engine.begin() as conn:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}{sufixo}")
                acrescentadas.append(f"{tabela}.{nome}")
                _log.info("migração: coluna %s.%s acrescentada", tabela, nome)
            except Exception as exc:  # noqa: BLE001
                _log.warning("migração: falha ao acrescentar %s.%s: %s", tabela, nome, exc)
    return acrescentadas


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        engine = get_engine()
        Base.metadata.create_all(engine)
        migrar_colunas(engine)
        _ready = True


def ensure_db() -> None:
    if not _ready:
        init_db()


# ── Configuração ────────────────────────────────────────────────────────
def ler_config(s) -> dict:
    """Padrões da seção 2.2 mesclados com o que está gravado (dicts por chave)."""
    cfg = copy.deepcopy(CONFIG_PADRAO)
    for row in s.scalars(select(Configuracao)).all():
        try:
            valor = json.loads(row.valor or "null")
        except ValueError:
            continue
        if isinstance(cfg.get(row.chave), dict) and isinstance(valor, dict):
            cfg[row.chave] = {**cfg[row.chave], **valor}
        elif valor is not None:
            cfg[row.chave] = valor
    try:
        cfg["limiar_percentual"] = float(cfg["limiar_percentual"])
    except (TypeError, ValueError):
        cfg["limiar_percentual"] = CONFIG_PADRAO["limiar_percentual"]
    return cfg


def gravar_config(s, dados: dict, usuario: str) -> None:
    for chave, valor in dados.items():
        row = s.get(Configuracao, chave)
        if row is None:
            row = Configuracao(chave=chave)
            s.add(row)
        row.valor = json.dumps(valor, ensure_ascii=False)
        row.atualizado_em = utcnow()
        row.atualizado_por = usuario
    s.flush()  # a sessão não faz autoflush: garante que `ler_config` já veja o gravado
