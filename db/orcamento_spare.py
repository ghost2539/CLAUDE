"""Banco ISOLADO do CAPEX Spare (Controle de Orçamento do SPARE).

Separado do `/controle-orcamento` e do portal. Modelo mestre-detalhe:

- `orc_spare_projeto`: um projeto (ID/nº do EBS, descrição, serviço, categoria),
  com o total APROVADO puxado do EBS pelo número e a PARCELA desse aprovado que
  é destinada ao Spare (informada à mão — o EBS não separa Spare do resto);
- `orc_spare_item`: as linhas de item de cada projeto (Item EBS, descrição do
  item, quantidade, valor unitário). O valor total da linha e o custo total do
  projeto são calculados (quantidade × valor unitário).

Tabelas novas de propósito (nomes `orc_spare_*`), para não colidir com o
desenho anterior do módulo (`spare_projeto`/`spare_campo`).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text,
    create_engine, event, func, select,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("orcamento_spare.db")

DATABASE_URL: str = getattr(
    _cfg, "ORCAMENTO_SPARE_DATABASE_URL", _config_mod._sqlite("orcamento_spare"),
)

_engine = None
_factory = None
_ready = False
_init_lock = threading.Lock()

# Situação do item no consumo do orçamento.
STATUS_ITEM = ("orcado", "andamento", "executado")
STATUS_ROTULO = {"orcado": "Orçado/Previsto", "andamento": "Em andamento", "executado": "Executado"}

# Unidades de negócio (BU). Acordos de compra são por BU.
BUS = ("Renner", "Camicado", "Youcom", "Renner Argentina", "Renner Uruguai")


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
                cur.execute("PRAGMA foreign_keys=ON")
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


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


class Projeto(Base):
    __tablename__ = "orc_spare_projeto"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    numero: Mapped[str] = mapped_column(String(40), default="", index=True)   # ID do projeto (EBS)
    descricao: Mapped[str] = mapped_column(String(200), default="")
    bu: Mapped[str] = mapped_column(String(40), default="")   # unidade de negócio
    servico: Mapped[str] = mapped_column(String(160), default="")
    categoria: Mapped[str] = mapped_column(String(80), default="")
    # Total aprovado do projeto no EBS (puxado pelo número) e a parcela que é do Spare.
    aprovado_ebs: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    aprovado_spare: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    observacao: Mapped[str] = mapped_column(Text, default="")
    ebs_sincronizado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")

    itens: Mapped[list["Item"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan", order_by="Item.id")

    def to_dict(self) -> dict:
        itens = [i.to_dict() for i in self.itens]
        custo = round(sum(i["valor_total"] for i in itens), 2)
        aprovado_spare = _f(self.aprovado_spare)
        return {
            "id": self.id,
            "numero": self.numero or "",
            "descricao": self.descricao or "",
            "bu": self.bu or "",
            "servico": self.servico or "",
            "categoria": self.categoria or "",
            "aprovado_ebs": _f(self.aprovado_ebs),
            "aprovado_spare": aprovado_spare,
            "observacao": self.observacao or "",
            "ebs_sincronizado_em": self.ebs_sincronizado_em.isoformat() if self.ebs_sincronizado_em else None,
            "custo_total": custo,
            "saldo_spare": round(aprovado_spare - custo, 2),
            "itens": itens,
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
            "atualizado_por": self.atualizado_por or "",
        }


class Item(Base):
    __tablename__ = "orc_spare_item"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("orc_spare_projeto.id", ondelete="CASCADE"), index=True)
    item_ebs: Mapped[str] = mapped_column(String(60), default="")
    descricao_item: Mapped[str] = mapped_column(String(200), default="")
    quantidade: Mapped[float] = mapped_column(Numeric(15, 3), default=0)
    valor_unitario: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    # Alíquota de imposto do item, em % (ex.: 18 = 18%). O valor total do
    # item e o custo do projeto consideram o valor COM imposto.
    imposto_percent: Mapped[float] = mapped_column(Numeric(7, 4), default=0)
    # Situação: orcado (previsto) | andamento | executado.
    status: Mapped[str] = mapped_column(String(20), default="orcado")
    # Item de acordo de compras (preço/imposto acordados) e o nº do acordo.
    acordo: Mapped[bool] = mapped_column(Boolean, default=False)
    acordo_numero: Mapped[str] = mapped_column(String(60), default="")
    ncm: Mapped[str] = mapped_column(String(12), default="")   # XXXX.XX.XX
    # Execução da compra: solicitação (SC), pedido (PC), recebimento e NF.
    solicitacao_compra: Mapped[str] = mapped_column(String(60), default="")
    pedido_compra: Mapped[str] = mapped_column(String(60), default="")
    recebido: Mapped[bool] = mapped_column(Boolean, default=False)
    nf: Mapped[str] = mapped_column(String(60), default="")
    ordem: Mapped[int] = mapped_column(Integer, default=0)

    projeto: Mapped["Projeto"] = relationship(back_populates="itens")

    def to_dict(self) -> dict:
        q = _f(self.quantidade)
        vu = _f(self.valor_unitario)
        imp = _f(self.imposto_percent)
        base = q * vu
        valor_imposto = base * imp / 100.0
        st = (self.status or "orcado")
        if st not in STATUS_ITEM:
            st = "orcado"
        return {
            "id": self.id,
            "item_ebs": self.item_ebs or "",
            "descricao_item": self.descricao_item or "",
            "quantidade": q,
            "valor_unitario": vu,
            "imposto_percent": imp,
            "status": st,
            "status_rotulo": STATUS_ROTULO.get(st, st),
            "acordo": bool(self.acordo),
            "acordo_numero": self.acordo_numero or "",
            "ncm": self.ncm or "",
            "solicitacao_compra": self.solicitacao_compra or "",
            "pedido_compra": self.pedido_compra or "",
            "recebido": bool(self.recebido),
            "nf": self.nf or "",
            "valor_sem_imposto": round(base, 2),
            "valor_imposto": round(valor_imposto, 2),
            "valor_total": round(base + valor_imposto, 2),
        }


def _migrar_colunas() -> None:
    """Adiciona colunas novas em bancos já criados (SQLite não faz no create_all)."""
    from sqlalchemy import inspect, text
    eng = get_engine()

    def _add(tabela: str, coluna: str, ddl: str) -> None:
        try:
            existentes = {c["name"] for c in inspect(eng).get_columns(tabela)}
        except Exception:  # noqa: BLE001 — tabela ainda não existe: create_all cuida
            return
        if coluna not in existentes:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE {tabela} ADD COLUMN {ddl}"))
            _log.info("%s: coluna %s adicionada", tabela, coluna)

    _add("orc_spare_projeto", "bu", "bu VARCHAR(40) DEFAULT ''")
    _add("orc_spare_catalogo", "acordo_bu", "acordo_bu VARCHAR(40) DEFAULT ''")
    _add("orc_spare_catalogo", "fornecedor", "fornecedor VARCHAR(160) DEFAULT ''")
    _add("orc_spare_catalogo", "vencimento", "vencimento VARCHAR(10) DEFAULT ''")

    try:
        cols = {c["name"] for c in inspect(eng).get_columns("orc_spare_item")}
    except Exception:  # noqa: BLE001 — tabela ainda não existe: create_all cuida
        return
    if "imposto_percent" not in cols:
        with eng.begin() as conn:
            conn.execute(text(
                "ALTER TABLE orc_spare_item ADD COLUMN imposto_percent NUMERIC(7,4) DEFAULT 0"))
        _log.info("orc_spare_item: coluna imposto_percent adicionada")
    if "status" not in cols:
        with eng.begin() as conn:
            conn.execute(text(
                "ALTER TABLE orc_spare_item ADD COLUMN status VARCHAR(20) DEFAULT 'orcado'"))
        _log.info("orc_spare_item: coluna status adicionada")
    if "acordo" not in cols:
        with eng.begin() as conn:
            conn.execute(text(
                "ALTER TABLE orc_spare_item ADD COLUMN acordo BOOLEAN DEFAULT 0"))
        _log.info("orc_spare_item: coluna acordo adicionada")
    if "acordo_numero" not in cols:
        with eng.begin() as conn:
            conn.execute(text(
                "ALTER TABLE orc_spare_item ADD COLUMN acordo_numero VARCHAR(60) DEFAULT ''"))
        _log.info("orc_spare_item: coluna acordo_numero adicionada")
    if "ncm" not in cols:
        with eng.begin() as conn:
            conn.execute(text(
                "ALTER TABLE orc_spare_item ADD COLUMN ncm VARCHAR(12) DEFAULT ''"))
        _log.info("orc_spare_item: coluna ncm adicionada")
    for coluna, ddl in (
        ("solicitacao_compra", "solicitacao_compra VARCHAR(60) DEFAULT ''"),
        ("pedido_compra", "pedido_compra VARCHAR(60) DEFAULT ''"),
        ("recebido", "recebido BOOLEAN DEFAULT 0"),
        ("nf", "nf VARCHAR(60) DEFAULT ''"),
    ):
        if coluna not in cols:
            with eng.begin() as conn:
                conn.execute(text(f"ALTER TABLE orc_spare_item ADD COLUMN {ddl}"))
            _log.info("orc_spare_item: coluna %s adicionada", coluna)


class Catalogo(Base):
    """Cadastro (mestre) de itens: Item EBS, descrição, se é de acordo de
    compras (com preço acordado), e o NCM (com a alíquota da TIPI)."""
    __tablename__ = "orc_spare_catalogo"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    item_ebs: Mapped[str] = mapped_column(String(60), default="", index=True)
    descricao_item: Mapped[str] = mapped_column(String(200), default="")
    ncm: Mapped[str] = mapped_column(String(12), default="")   # XXXX.XX.XX
    aliquota: Mapped[float | None] = mapped_column(Numeric(7, 4), nullable=True)
    nt: Mapped[bool] = mapped_column(Boolean, default=False)   # não tributado
    acordo: Mapped[bool] = mapped_column(Boolean, default=False)
    acordo_numero: Mapped[str] = mapped_column(String(60), default="")
    acordo_bu: Mapped[str] = mapped_column(String(40), default="")   # BU do acordo
    preco_acordo: Mapped[float] = mapped_column(Numeric(15, 2), default=0)
    fornecedor: Mapped[str] = mapped_column(String(160), default="")
    vencimento: Mapped[str] = mapped_column(String(10), default="")   # YYYY-MM-DD
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    atualizado_por: Mapped[str] = mapped_column(String(120), default="")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "item_ebs": self.item_ebs or "",
            "descricao_item": self.descricao_item or "",
            "ncm": self.ncm or "",
            "aliquota": (None if self.aliquota is None else _f(self.aliquota)),
            "nt": bool(self.nt),
            "imposto_percent": (0.0 if self.nt else (_f(self.aliquota) if self.aliquota is not None else 0.0)),
            "acordo": bool(self.acordo),
            "acordo_numero": self.acordo_numero or "",
            "acordo_bu": self.acordo_bu or "",
            "preco_acordo": _f(self.preco_acordo),
            "fornecedor": self.fornecedor or "",
            "vencimento": self.vencimento or "",
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else None,
            "atualizado_por": self.atualizado_por or "",
        }


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        _migrar_colunas()
        _ready = True


def ensure_db() -> None:
    if not _ready:
        init_db()


# ── Consultas ───────────────────────────────────────────────────────────
def listar_projetos() -> list[dict]:
    with SessionLocal() as s:
        rows = s.scalars(select(Projeto).order_by(Projeto.ordem, Projeto.id)).all()
        return [p.to_dict() for p in rows]


def totais() -> dict:
    projetos = listar_projetos()
    return {
        "projetos": len(projetos),
        "aprovado_ebs": round(sum(p["aprovado_ebs"] for p in projetos), 2),
        "aprovado_spare": round(sum(p["aprovado_spare"] for p in projetos), 2),
        "custo_total": round(sum(p["custo_total"] for p in projetos), 2),
    }


def listar_itens() -> list[dict]:
    """Todos os itens (achatados), com o projeto a que pertencem. É a visão de
    'quais itens estão consumindo o projeto' e a base da tela por situação."""
    out: list[dict] = []
    with SessionLocal() as s:
        projs = s.scalars(select(Projeto).order_by(Projeto.numero, Projeto.id)).all()
        for p in projs:
            for it in p.itens:
                d = it.to_dict()
                d["projeto_id"] = p.id
                d["projeto_numero"] = p.numero or ""
                d["projeto_descricao"] = p.descricao or ""
                out.append(d)
    return out


def listar_catalogo() -> list[dict]:
    with SessionLocal() as s:
        rows = s.scalars(select(Catalogo).order_by(Catalogo.item_ebs, Catalogo.id)).all()
        return [c.to_dict() for c in rows]


def buscar_catalogo(item_ebs: str = "", bu: str = "") -> dict | None:
    """Item do catálogo pelo Item EBS (para preencher a linha do projeto).

    Como o acordo é por BU, quando a BU do projeto é informada, prefere o
    cadastro de acordo daquela BU; senão, o mais recente."""
    item_ebs = (item_ebs or "").strip()
    bu = (bu or "").strip()
    if not item_ebs:
        return None
    with SessionLocal() as s:
        rows = s.scalars(
            select(Catalogo).where(func.lower(Catalogo.item_ebs) == item_ebs.lower())
            .order_by(Catalogo.id.desc())).all()
        if not rows:
            return None
        if bu:
            for r in rows:
                if r.acordo and (r.acordo_bu or "").strip().lower() == bu.lower():
                    return r.to_dict()
        return rows[0].to_dict()


def buscar_item_acordo(item_ebs: str = "", acordo_numero: str = "") -> dict | None:
    """Procura um item DE ACORDO já cadastrado (em qualquer projeto) para
    reaproveitar descrição, valor unitário e % de imposto. Casa pelo Item EBS
    (o mesmo item), preferindo o mesmo nº de acordo; devolve o mais recente."""
    item_ebs = (item_ebs or "").strip()
    acordo_numero = (acordo_numero or "").strip()
    if not item_ebs and not acordo_numero:
        return None
    with SessionLocal() as s:
        q = select(Item).where(Item.acordo == True)  # noqa: E712
        if item_ebs:
            q = q.where(func.lower(Item.item_ebs) == item_ebs.lower())
        if acordo_numero and not item_ebs:
            q = q.where(func.lower(Item.acordo_numero) == acordo_numero.lower())
        rows = s.scalars(q.order_by(Item.id.desc())).all()
        if not rows:
            return None
        escolhido = None
        if acordo_numero:
            for r in rows:
                if (r.acordo_numero or "").strip().lower() == acordo_numero.lower():
                    escolhido = r
                    break
        escolhido = escolhido or rows[0]
        d = escolhido.to_dict()
        return {
            "descricao_item": d["descricao_item"],
            "valor_unitario": d["valor_unitario"],
            "imposto_percent": d["imposto_percent"],
            "acordo_numero": escolhido.acordo_numero or "",
        }


def totais_por_status() -> dict:
    """Soma (com imposto) e contagem de itens por situação."""
    r = {st: {"itens": 0, "valor": 0.0} for st in STATUS_ITEM}
    for it in listar_itens():
        st = it.get("status") or "orcado"
        if st not in r:
            st = "orcado"
        r[st]["itens"] += 1
        r[st]["valor"] += it["valor_total"]
    for st in r:
        r[st]["valor"] = round(r[st]["valor"], 2)
    return r
