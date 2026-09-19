"""Banco ISOLADO dos Agendamentos de Fornecedores (menu Entrada).

Registra o agendamento da entrega de um fornecedor antes de ela chegar:
BU, NF, PO, fornecedor, estoque de destino, data agendada e os equipamentos
(descrição + quantidade). Quando a carga chega, um botão confirma o
recebimento (grava a data e passa o agendamento para a etapa de
internalização). Nada aqui escreve no banco de outro módulo.

Tabelas:
- `agf_agendamento` — um agendamento (a entrega);
- `agf_pedido` — uma linha por PO do agendamento, com a NF que a cobre
  (a mesma NF pode aparecer em várias POs);
- `agf_equipamento` — uma linha por equipamento do agendamento (n itens);
- `agf_recebimento` — a conferência feita na chegada (Recebimento →
  Fornecedores): quando, quem, se foi entrega parcial;
- `agf_recebimento_item` — cada item conferido, com o pedido, o que a NF
  traz e o que chegou;
- `agf_recebimento_unidade` — uma linha por unidade, com o serial;
- `agf_nota` — cada NF do agendamento: chave de acesso, vencimento, de
  onde veio o XML e os arquivos guardados.

Quem grava o recebimento é a rota de Recebimento → Fornecedores, mas
passando pela função deste módulo (`routers/agendamentos_forn.py::
registrar_recebimento`): a regra de "cada módulo escreve no próprio
banco" continua valendo — o que muda é quem aperta o botão.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, String, Text, create_engine,
    event,
)
from db._esquema import UtcDateTime
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("agendamentos_forn.db")

DATABASE_URL: str = getattr(
    _cfg, "AGENDAMENTOS_FORN_DATABASE_URL",
    _config_mod._sqlite("agendamentos_forn"),
)

# ── Vocabulário canônico ────────────────────────────────────────────────
# BU: só estas três bandeiras.
BUS = ("Renner", "Camicado", "Youcom")
# Estoque de destino: dois grupos, guardados por código e exibidos por rótulo.
DESTINOS = {
    "INAUGURACAO_REFORMAS": "Inauguração/Reformas",
    "REPOSICAO": "Reposição",
}
# Ciclo: agendado → (conferência na chegada) → recebido (segue p/ internalização).
STATUS = {"AGENDADO": "Agendado", "RECEBIDO": "Recebido"}

# De onde veio o XML da nota. SEFAZ é a busca pela chave de acesso com o
# certificado da BU; ARQUIVO é o upload (Youcom, que não tem certificado, ou
# qualquer BU quando a SEFAZ não responde); DIGITADA é só o número, sem XML.
NOTA_ORIGENS = {"SEFAZ": "SEFAZ", "ARQUIVO": "Arquivo enviado", "DIGITADA": "Digitada"}

_engine = None
_factory = None
_ready = False
_init_lock = threading.Lock()


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
        _factory = sessionmaker(bind=get_engine(), autoflush=False,
                                expire_on_commit=False)
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


class Agendamento(Base):
    __tablename__ = "agf_agendamento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bu: Mapped[str] = mapped_column(String(20), default="")
    nf: Mapped[str] = mapped_column(String(40), index=True)
    po: Mapped[str] = mapped_column(String(40), index=True)
    volumes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fornecedor: Mapped[str] = mapped_column(String(160))
    estoque_destino: Mapped[str] = mapped_column(String(40))
    data_agendada: Mapped[date] = mapped_column(Date)
    # Não é preenchida à mão: entra só quando alguém confirma o recebimento.
    data_recebimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="AGENDADO", index=True)
    criado_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    criado_por: Mapped[str] = mapped_column(String(80), default="")
    recebido_por: Mapped[str] = mapped_column(String(80), default="")

    pedidos: Mapped[list["Pedido"]] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan",
        order_by="Pedido.id",
    )

    equipamentos: Mapped[list["Equipamento"]] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan",
        order_by="Equipamento.id",
    )

    # No máximo um: a conferência da chegada. Entrega parcial não reabre o
    # agendamento — o restante, quando vier, é outra NF e outro agendamento.
    recebimento: Mapped["Recebimento | None"] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan", uselist=False,
    )
    notas: Mapped[list["Nota"]] = relationship(
        back_populates="agendamento", cascade="all, delete-orphan",
        order_by="Nota.id",
    )

    def to_dict(self) -> dict:
        rec = self.recebimento
        return {
            "id": self.id,
            "bu": self.bu or "",
            "nf": self.nf or "",
            "po": self.po or "",
            "volumes": self.volumes,
            "fornecedor": self.fornecedor or "",
            "estoque_destino": self.estoque_destino or "",
            "estoque_destino_rotulo": DESTINOS.get(self.estoque_destino, self.estoque_destino or ""),
            "data_agendada": self.data_agendada.isoformat() if self.data_agendada else "",
            "data_recebimento": self.data_recebimento.isoformat() if self.data_recebimento else "",
            "status": self.status or "AGENDADO",
            "status_rotulo": STATUS.get(self.status, self.status or ""),
            "criado_em": self.criado_em.isoformat() if self.criado_em else "",
            "criado_por": self.criado_por or "",
            "recebido_por": self.recebido_por or "",
            # A tela reabre o agendamento por aqui: sem `pedidos` ela só veria
            # a primeira PO (a que ficou nas colunas soltas) e as demais
            # sumiriam na edição.
            "pedidos": [p.to_dict() for p in self.pedidos],
            "equipamentos": [e.to_dict() for e in self.equipamentos],
            # A conferência da chegada, quando houve. A lista do Recebimento
            # mostra "Entrega parcial" por aqui.
            "entrega_parcial": bool(rec.entrega_parcial) if rec else False,
            "recebimento_id": rec.id if rec else None,
        }


class Pedido(Base):
    """Uma PO do agendamento, com a NF que a cobre.

    Antes o agendamento tinha uma PO e uma NF, em duas colunas. Na prática
    uma entrega traz várias POs, e UMA NF pode cobrir mais de uma — por isso
    a NF fica aqui, repetida em cada PO que ela atende, em vez de numa
    tabela à parte. Assim a pergunta que se faz no dia a dia ("o que veio
    nesta NF?" e "esta PO já foi agendada?") se responde sem join extra.

    As colunas `po` e `nf` do agendamento continuam existindo e guardam a
    PRIMEIRA delas: é o que a listagem e a busca já usavam, e mexer nisso
    apagaria da tela os agendamentos que já estão gravados.
    """

    __tablename__ = "agf_pedido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), index=True)
    po: Mapped[str] = mapped_column(String(40), index=True)
    nf: Mapped[str] = mapped_column(String(40), default="", index=True)
    # O que a consulta ao EBS respondeu quando a PO foi digitada. Guardado
    # para a conferência na chegada não depender do banco do EBS estar de pé.
    fornecedor_ebs: Mapped[str] = mapped_column(String(160), default="")
    status_ebs: Mapped[str] = mapped_column(String(40), default="")

    agendamento: Mapped["Agendamento"] = relationship(back_populates="pedidos")

    def to_dict(self) -> dict:
        return {"id": self.id, "po": self.po or "", "nf": self.nf or "",
                "fornecedor_ebs": self.fornecedor_ebs or "",
                "status_ebs": self.status_ebs or ""}


class Equipamento(Base):
    __tablename__ = "agf_equipamento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), index=True)
    descricao: Mapped[str] = mapped_column(String(200))
    quantidade: Mapped[int] = mapped_column(Integer, default=1)

    agendamento: Mapped["Agendamento"] = relationship(back_populates="equipamentos")

    def to_dict(self) -> dict:
        return {"id": self.id, "descricao": self.descricao or "",
                "quantidade": self.quantidade or 0}


class Recebimento(Base):
    """A conferência feita quando a carga chega.

    É o que separa "o fornecedor disse que vinha" de "chegou, e isto":
    por item, quanto o pedido pedia, quanto a nota traz e quanto veio; por
    unidade, o serial. A entrega parcial fica marcada aqui, sem travar o
    fluxo — o financeiro precisa saber, o estoque não precisa esperar.
    """

    __tablename__ = "agf_recebimento"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), unique=True, index=True)
    recebido_em: Mapped[datetime] = mapped_column(UtcDateTime(), default=utcnow)
    recebido_por: Mapped[str] = mapped_column(String(80), default="")
    entrega_parcial: Mapped[bool] = mapped_column(Boolean, default=False)
    # A PO trouxe também o que não é imobilizado (cabo, fonte): fica
    # registrado que houve, para quem confere o pagamento.
    tem_nao_imobilizados: Mapped[bool] = mapped_column(Boolean, default=False)
    observacao: Mapped[str] = mapped_column(String(500), default="")

    agendamento: Mapped["Agendamento"] = relationship(back_populates="recebimento")
    itens: Mapped[list["RecebimentoItem"]] = relationship(
        back_populates="recebimento", cascade="all, delete-orphan",
        order_by="RecebimentoItem.id",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agendamento_id": self.agendamento_id,
            "recebido_em": self.recebido_em.isoformat() if self.recebido_em else "",
            "recebido_por": self.recebido_por or "",
            "entrega_parcial": bool(self.entrega_parcial),
            "tem_nao_imobilizados": bool(self.tem_nao_imobilizados),
            "observacao": self.observacao or "",
            "itens": [i.to_dict() for i in self.itens],
        }


class RecebimentoItem(Base):
    __tablename__ = "agf_recebimento_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recebimento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_recebimento.id", ondelete="CASCADE"), index=True)
    po: Mapped[str] = mapped_column(String(40), default="")
    nf: Mapped[str] = mapped_column(String(40), default="")
    # Linha do pedido no EBS e código do item: é o que a planilha do CSC
    # Lançamentos chama de "Item". Youcom não tem EBS: fica o que foi digitado.
    linha: Mapped[int | None] = mapped_column(Integer, nullable=True)
    item_ebs: Mapped[str] = mapped_column(String(60), default="")
    descricao: Mapped[str] = mapped_column(String(200), default="")
    unidade: Mapped[str] = mapped_column(String(20), default="")
    quantidade_pedida: Mapped[int] = mapped_column(Integer, default=0)
    quantidade_nf: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantidade_recebida: Mapped[int] = mapped_column(Integer, default=0)
    imobilizado: Mapped[bool] = mapped_column(Boolean, default=True)

    recebimento: Mapped["Recebimento"] = relationship(back_populates="itens")
    unidades: Mapped[list["RecebimentoUnidade"]] = relationship(
        back_populates="item", cascade="all, delete-orphan",
        order_by="RecebimentoUnidade.id",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "po": self.po or "", "nf": self.nf or "",
            "linha": self.linha, "item_ebs": self.item_ebs or "",
            "descricao": self.descricao or "", "unidade": self.unidade or "",
            "quantidade_pedida": self.quantidade_pedida or 0,
            "quantidade_nf": self.quantidade_nf,
            "quantidade_recebida": self.quantidade_recebida or 0,
            "imobilizado": bool(self.imobilizado),
            "seriais": [u.serial for u in self.unidades],
        }


class RecebimentoUnidade(Base):
    """Uma unidade física recebida: o serial. É o elo com o ativo do
    lançamento (que nasce com este serial e a etiqueta consumida)."""

    __tablename__ = "agf_recebimento_unidade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("agf_recebimento_item.id", ondelete="CASCADE"), index=True)
    serial: Mapped[str] = mapped_column(String(80), index=True)

    item: Mapped["RecebimentoItem"] = relationship(back_populates="unidades")


class Nota(Base):
    """Uma NF do agendamento e o que se sabe dela.

    Nasce na conferência (o número já veio do agendamento) e ganha a chave
    de acesso quando alguém bipa a nota. Com a chave e o certificado da BU,
    o XML vem da SEFAZ; sem certificado (Youcom), alguém envia o arquivo.
    Os arquivos ficam em `data/tmp/recebimento_forn/<agendamento>/` por
    cinco dias — o caminho gravado aqui é relativo a essa pasta.
    """

    __tablename__ = "agf_nota"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    agendamento_id: Mapped[int] = mapped_column(
        ForeignKey("agf_agendamento.id", ondelete="CASCADE"), index=True)
    nf: Mapped[str] = mapped_column(String(40), index=True)
    chave: Mapped[str] = mapped_column(String(44), default="", index=True)
    vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    origem: Mapped[str] = mapped_column(String(20), default="")
    xml_arquivo: Mapped[str] = mapped_column(String(200), default="")
    pdf_arquivo: Mapped[str] = mapped_column(String(200), default="")
    # O que o XML diz dos itens (código, descrição, quantidade), em JSON:
    # é contra isto que a conferência compara o que chegou.
    itens_json: Mapped[str] = mapped_column(Text, default="")
    emitente: Mapped[str] = mapped_column(String(160), default="")
    cstat: Mapped[str] = mapped_column(String(10), default="")
    erro: Mapped[str] = mapped_column(String(300), default="")
    atualizado_em: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    atualizado_por: Mapped[str] = mapped_column(String(80), default="")

    agendamento: Mapped["Agendamento"] = relationship(back_populates="notas")

    def itens(self) -> list[dict]:
        import json
        try:
            dados = json.loads(self.itens_json or "[]")
        except ValueError:
            return []
        return dados if isinstance(dados, list) else []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "nf": self.nf or "",
            "chave": self.chave or "",
            "vencimento": self.vencimento.isoformat() if self.vencimento else "",
            "origem": self.origem or "",
            "origem_rotulo": NOTA_ORIGENS.get(self.origem or "", ""),
            "tem_xml": bool(self.xml_arquivo),
            "tem_pdf": bool(self.pdf_arquivo),
            "itens": self.itens(),
            "emitente": self.emitente or "",
            "cstat": self.cstat or "",
            "erro": self.erro or "",
            "atualizado_em": self.atualizado_em.isoformat() if self.atualizado_em else "",
        }


def init_db() -> None:
    global _ready
    with _init_lock:
        if _ready:
            return
        Base.metadata.create_all(get_engine())
        # Coluna nova em tabela que já existe: o create_all não acrescenta.
        from db._esquema import migrar_colunas
        migrar_colunas(Base, get_engine(), "agendamentos_forn")
        # Agendamento gravado antes da tabela de pedidos fica sem linha nela
        # e apareceria sem PO nenhuma na tela. Aqui a PO e a NF que estão nas
        # colunas antigas viram a primeira linha — uma vez só, sem duplicar.
        _semear_pedidos()
        _ready = True


def _semear_pedidos() -> None:
    """Passa a PO/NF antigas do agendamento para a tabela de pedidos.

    Idempotente: só semeia agendamento que ainda não tem nenhuma linha.
    Falha aqui não pode derrubar a subida do módulo — no pior caso a tela
    mostra o agendamento sem PO, e o log diz por quê.
    """
    from sqlalchemy import select
    try:
        with SessionLocal.begin() as s:
            ja = {p.agendamento_id for p in s.scalars(select(Pedido)).all()}
            for ag in s.scalars(select(Agendamento)).all():
                if ag.id in ja or not (ag.po or ag.nf):
                    continue
                s.add(Pedido(agendamento_id=ag.id, po=ag.po or "", nf=ag.nf or ""))
    except Exception as exc:  # noqa: BLE001
        _log.warning("agendamentos: não semeei os pedidos antigos: %s", exc)


def ensure_db() -> None:
    if not _ready:
        init_db()
