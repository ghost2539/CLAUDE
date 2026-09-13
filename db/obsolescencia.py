"""Banco ISOLADO da Obsolescência do parque de coletores.

Separado do banco do portal. Guarda:

- `Coletor`: a foto mais recente de cada aparelho do MDM, já com a loja/BU
  resolvida e a idade cruzada com o EBS.
- `Coleta`: uma linha por varredura, para saber quando rodou, quanto trouxe
  e se falhou.
- `Escrita`: trilha de TODA alteração feita no MDM pelo portal (tag ou
  deleção) — quem, quando, o quê e o que o MDM respondeu. Deleção não tem
  volta, então o registro é local e independente do log do próprio MDM.
- `Config`: parâmetros do módulo (modo da regra, limites, modelos EOL).

Contrato do MDM em docs/MDM_OBSOLESCENCIA.md.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("obsolescencia.db")

DATABASE_URL: str = getattr(
    _cfg, "OBSOLESCENCIA_DATABASE_URL", _config_mod._sqlite("obsolescencia"),
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


# O painel cobre dois parques, de origens diferentes:
COLETOR = "coletor"   # MDM (Workspace ONE), raspado da grade
PDV = "pdv"           # ServiceNow, tabela cmdb_ci_computer

# Situação do ativo na base, entre uma coleta e outra.
ATIVO = "ativo"              # veio na última varredura
SUMIU = "sumiu"              # estava na base e não veio mais — vai para tratativa
TRATADO = "tratado"          # alguém resolveu o sumiço


class Coletor(Base):
    """Foto do aparelho na última varredura, mais o que o portal deduziu."""
    __tablename__ = "obs_coletor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Chave estável entre coletas: id do MDM para coletor, sys_id para PDV.
    mdm_id: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    tipo: Mapped[str] = mapped_column(String(10), default=COLETOR, index=True)
    serie: Mapped[str] = mapped_column(String(80), default="", index=True)
    local: Mapped[str] = mapped_column(String(160), default="")

    nome: Mapped[str] = mapped_column(String(200), default="")
    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    caminho_og: Mapped[str] = mapped_column(String(300), default="")

    # Resolvidos a partir do usuário (ljr234_coletor -> Renner / 234).
    bu: Mapped[str] = mapped_column(String(10), default="", index=True)
    bu_nome: Mapped[str] = mapped_column(String(60), default="")
    pais: Mapped[str] = mapped_column(String(4), default="")
    loja: Mapped[str] = mapped_column(String(20), default="", index=True)
    e_loja: Mapped[bool] = mapped_column(Boolean, default=True)

    plataforma: Mapped[str] = mapped_column(String(30), default="")
    modelo: Mapped[str] = mapped_column(String(80), default="", index=True)
    versao_os: Mapped[str] = mapped_column(String(30), default="")
    propriedade: Mapped[str] = mapped_column(String(60), default="")
    gerenciamento: Mapped[str] = mapped_column(String(60), default="")
    conformidade: Mapped[str] = mapped_column(String(60), default="")

    visto_relativo: Mapped[str] = mapped_column(String(30), default="")
    visto_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    dias_sem_ver: Mapped[int | None] = mapped_column(Integer, default=None, index=True)

    tags: Mapped[str] = mapped_column(Text, default="")   # nomes reais, separados por |

    # Idade do ativo: só o EBS tem a data de compra. Quando não achar, fica
    # nulo e `idade_desconhecida` avisa — estimar pela inscrição mentiria,
    # porque coletor reinscrito após reparo "rejuvenesce".
    data_aquisicao: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    idade_anos: Mapped[float | None] = mapped_column(default=None)
    idade_desconhecida: Mapped[bool] = mapped_column(Boolean, default=True)

    obsoleto: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    criterios: Mapped[str] = mapped_column(Text, default="")   # JSON dos três

    situacao: Mapped[str] = mapped_column(String(12), default=ATIVO, index=True)
    visto_na_coleta: Mapped[int | None] = mapped_column(Integer, default=None)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)


class Coleta(Base):
    """Uma varredura do parque."""
    __tablename__ = "obs_coleta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    inicio: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow)
    fim: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    usuario: Mapped[str] = mapped_column(String(80), default="")
    # aberta | concluida | falhou
    situacao: Mapped[str] = mapped_column(String(12), default="aberta")
    paginas: Mapped[int] = mapped_column(Integer, default=0)
    total_mdm: Mapped[int] = mapped_column(Integer, default=0)
    lidos: Mapped[int] = mapped_column(Integer, default=0)
    novos: Mapped[int] = mapped_column(Integer, default=0)
    atualizados: Mapped[int] = mapped_column(Integer, default=0)
    sumiram: Mapped[int] = mapped_column(Integer, default=0)
    erro: Mapped[str] = mapped_column(Text, default="")


class Escrita(Base):
    """Trilha de alteração feita no MDM pelo portal. Nunca é apagada."""
    __tablename__ = "obs_escrita"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    quando: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=localnow, index=True)
    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    ip: Mapped[str] = mapped_column(String(80), default="")
    acao: Mapped[str] = mapped_column(String(30), default="")     # tag_incluir|tag_remover|deletar
    mdm_id: Mapped[str] = mapped_column(String(30), default="", index=True)
    serie: Mapped[str] = mapped_column(String(60), default="")
    detalhe: Mapped[str] = mapped_column(Text, default="")
    sucesso: Mapped[bool] = mapped_column(Boolean, default=False)
    resposta: Mapped[str] = mapped_column(Text, default="")
    origem: Mapped[str] = mapped_column(String(30), default="")   # tela|recebimento|chamado


class Config(Base):
    """Parâmetros do módulo, em chave/valor para não migrar a cada ajuste."""
    __tablename__ = "obs_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    "modo_regra": "todos",          # todos (E) | qualquer (OU)
    "limite_anos": "5",
    "limite_sem_ver": "30",
    "modelos_eol": "EF500,EF500R",
    # Android travado: versão abaixo da mínima suportada OU modelo que o
    # fabricante não atualiza mais. É o critério mais barato e o que mais
    # fala com Segurança.
    "versao_os_minima": "11",
    "modelos_sem_update": "",
    # PDVs no ServiceNow. Deixado configurável porque o rótulo da tela
    # ("Origem da descoberta") pode não bater com o nome interno do campo.
    "pdv_tabela": "cmdb_ci_computer",
    "pdv_query": "discovery_source=ACC_VISIBILITY^install_status=1",
}


def init_db() -> None:
    Base.metadata.create_all(get_engine())
    with SessionLocal.begin() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave)).all()}
        for chave, valor in PADROES.items():
            if chave not in existentes:
                s.add(Config(chave=chave, valor=valor))


def ler_config() -> dict:
    with SessionLocal() as s:
        atual = {c.chave: c.valor for c in s.execute(select(Config)).scalars()}
    return {**PADROES, **atual}


def gravar_config(mudancas: dict) -> dict:
    with SessionLocal.begin() as s:
        for chave, valor in (mudancas or {}).items():
            if chave not in PADROES:
                continue          # só o que o módulo conhece
            linha = s.get(Config, chave)
            if linha:
                linha.valor = str(valor)
            else:
                s.add(Config(chave=chave, valor=str(valor)))
    return ler_config()
