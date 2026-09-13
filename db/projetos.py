"""Banco ISOLADO de Projetos de Loja (A16) — inauguração e reforma.

O que separa este módulo da Separação (A15) não é o estoque: os dois
consomem o mesmo depósito, e o corredor é que decide qual prateleira.
O que separa é o **token**.

Na separação o token é o chamado: alguém pede, alguém separa, acabou.
Num projeto de loja o token é o **item do projeto** — uma linha de
"tantos equipamentos deste modelo, para esta área da loja" que nasce
meses antes de existir série, atravessa a definição de escopo, a
separação, a configuração, e só então vira equipamento numa caixa. Cada
item anda no seu ritmo: o projeto inteiro não espera o item que travou.

Três estados de exceção pausam o relógio do item, porque o tempo neles
não é de ninguém da área:

- `AG_DEFINICAO`   — o escopo ainda não fechou;
- `AG_ESTOQUE`     — não há saldo, e comprar não depende de quem separa;
- `AG_REPARO_PROJ` — o equipamento voltou para a bancada.

O relógio propriamente dito não mora aqui: mora na Trilha do Ativo. Cada
item do projeto abre um token no núcleo com serial próprio
(`PRJ-AAAA-NNNN/12`), e é por isso que ele aparece na Torre de Controle
junto com o resto — um item de projeto parado há duas semanas é uma fila
tão real quanto um coletor parado na bancada.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, Date, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("projetos.db")

DATABASE_URL: str = getattr(
    _cfg, "PROJETOS_DATABASE_URL", _config_mod._sqlite("projetos"),
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ── Vocabulário ────────────────────────────────────────────────────

INAUGURACAO = "INAUGURACAO"
REFORMA = "REFORMA"
TIPOS_PROJETO = (INAUGURACAO, REFORMA)

ROTULO_TIPO = {
    INAUGURACAO: "Inauguração",
    REFORMA: "Reforma",
}

# Estados do PROJETO. O projeto é uma pasta: ele não tem relógio, quem
# tem é o item. O estado aqui só diz se ainda entra item novo.
PLANEJAMENTO = "PLANEJAMENTO"
EM_ANDAMENTO = "EM_ANDAMENTO"
CONCLUIDO = "CONCLUIDO"
CANCELADO = "CANCELADO"
ESTADOS_PROJETO = (PLANEJAMENTO, EM_ANDAMENTO, CONCLUIDO, CANCELADO)

ROTULO_PROJETO = {
    PLANEJAMENTO: "Em planejamento",
    EM_ANDAMENTO: "Em andamento",
    CONCLUIDO: "Concluído",
    CANCELADO: "Cancelado",
}

# Estados do ITEM — estes sim viram intervalo no núcleo.
AG_DEFINICAO = "AG_DEFINICAO"
AG_SEPARACAO_PROJ = "AG_SEPARACAO_PROJ"
EX_SEPARACAO_PROJ = "EX_SEPARACAO_PROJ"
AG_ESTOQUE = "AG_ESTOQUE"
AG_CONFIGURACAO_PROJ = "AG_CONFIGURACAO_PROJ"
EX_CONFIGURACAO_PROJ = "EX_CONFIGURACAO_PROJ"
AG_REPARO_PROJ = "AG_REPARO_PROJ"
PRONTO_PROJ = "PRONTO_PROJ"
ENVIADO_PROJ = "ENVIADO_PROJ"
CANCELADO_PROJ = "CANCELADO_PROJ"

ESTADOS_ITEM = (
    AG_DEFINICAO, AG_SEPARACAO_PROJ, EX_SEPARACAO_PROJ, AG_ESTOQUE,
    AG_CONFIGURACAO_PROJ, EX_CONFIGURACAO_PROJ, AG_REPARO_PROJ,
    PRONTO_PROJ, ENVIADO_PROJ, CANCELADO_PROJ,
)

ROTULO_ITEM = {
    AG_DEFINICAO: "Aguardando definição",
    AG_SEPARACAO_PROJ: "Aguardando separação",
    EX_SEPARACAO_PROJ: "Em separação",
    AG_ESTOQUE: "Aguardando estoque",
    AG_CONFIGURACAO_PROJ: "Aguardando configuração",
    EX_CONFIGURACAO_PROJ: "Em configuração",
    AG_REPARO_PROJ: "Aguardando reparo",
    PRONTO_PROJ: "Pronto para envio",
    ENVIADO_PROJ: "Enviado",
    CANCELADO_PROJ: "Cancelado",
}

# Os três que pausam o relógio. Não é que o tempo não passe — ele passa
# e aparece no total do projeto. É que ele não conta contra ninguém da
# área, e por isso entra no núcleo como intervalo EXTERNO.
ESTADOS_PAUSA = (AG_DEFINICAO, AG_ESTOQUE, AG_REPARO_PROJ)

# Encerrados: não têm próximo passo nem entram em fila nenhuma.
ESTADOS_FINAIS = (ENVIADO_PROJ, CANCELADO_PROJ)


class Projeto(Base):
    """Uma loja abrindo ou reformando. Pasta de itens, sem relógio próprio."""
    __tablename__ = "prj_projeto"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    # Mesma regra do resto da área: nada nasce sem chamado. Aqui o
    # chamado é a demanda do projeto, e é dele que vem a loja de destino.
    chamado: Mapped[str] = mapped_column(String(40), index=True)
    chamado_tipo: Mapped[str] = mapped_column(String(20), default="")
    chamado_resumo: Mapped[str] = mapped_column(Text, default="")

    tipo: Mapped[str] = mapped_column(String(20), default=INAUGURACAO, index=True)
    loja: Mapped[str] = mapped_column(String(200), default="")
    bu: Mapped[str] = mapped_column(String(20), default="")
    # A data que manda no projeto inteiro: a loja abre nela, e o prazo de
    # cada item é contado para trás a partir daqui.
    data_prevista: Mapped[datetime | None] = mapped_column(
        Date, default=None, index=True)
    responsavel: Mapped[str] = mapped_column(String(80), default="")
    observacao: Mapped[str] = mapped_column(Text, default="")

    estado: Mapped[str] = mapped_column(String(20), default=PLANEJAMENTO, index=True)
    aberto_por: Mapped[str] = mapped_column(String(80), index=True)
    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    encerrado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)


class ItemProjeto(Base):
    """O token do A16: um modelo, uma quantidade, uma área da loja."""
    __tablename__ = "prj_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("prj_projeto.id", ondelete="CASCADE"), index=True)

    # Serial sintético do token no núcleo: PRJ-2026-0001/12. É o que
    # deixa o item aparecer na Torre com o mesmo relógio de todo mundo.
    token: Mapped[str] = mapped_column(String(60), unique=True, index=True)

    modelo: Mapped[str] = mapped_column(String(160))
    quantidade: Mapped[int] = mapped_column(Integer, default=1)
    area: Mapped[str] = mapped_column(String(80), default="")

    estado: Mapped[str] = mapped_column(String(30), default=AG_DEFINICAO, index=True)
    prazo: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)
    responsavel: Mapped[str] = mapped_column(String(80), default="")
    observacao: Mapped[str] = mapped_column(Text, default="")

    # Ligação com o núcleo. Vazio quando a trilha estava fora do ar na
    # criação — o item funciona, só não é medido.
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)
    encerrado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)

    __table_args__ = (
        Index("ix_prj_item_fila", "estado", "projeto_id"),
    )


class UnidadeProjeto(Base):
    """Um equipamento de verdade, separado para um item do projeto."""
    __tablename__ = "prj_unidade"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("prj_projeto.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(
        ForeignKey("prj_item.id", ondelete="CASCADE"), index=True)

    serial: Mapped[str] = mapped_column(String(120), index=True)
    sys_id: Mapped[str] = mapped_column(String(40), default="")
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)

    # Uma unidade sai do projeto quando volta para a bancada; a linha
    # fica, com o motivo, para a conta de retrabalho do projeto.
    devolvida_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None)
    devolvida_motivo: Mapped[str] = mapped_column(Text, default="")

    separada_por: Mapped[str] = mapped_column(String(80), default="")
    separada_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        # Nome próprio: `serial` já ganhou ix_prj_unidade_serial do
        # index=True, e dois índices homônimos fazem o create_all falhar.
        # Não é único de propósito: a mesma série pode voltar ao mesmo
        # item depois de reprovada e consertada, e a linha devolvida
        # fica. Quem impede duplicata entre as unidades vivas é o bipe.
        Index("ix_prj_unidade_item_serial", "item_id", "serial"),
    )


class Config(Base):
    __tablename__ = "prj_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Projeto consome o estoque de inauguração. O filtro em si é da
    # Separação: um só lugar decide o que é prateleira de inauguração.
    "tipo_estoque": "INAUGURACAO_REFORMA",

    # Prazos em dias úteis, contados da criação do item.
    "prazo_definicao": "5",
    "prazo_separacao": "5",
    "prazo_configuracao": "3",

    # Quantos dias úteis antes da abertura da loja tudo tem que estar
    # pronto. Serve para o alerta de projeto em risco.
    "folga_antes_abertura": "5",

    # Chamado de origem. Projeto costuma vir de requisição, mas
    # incidente também é aceito — a regra da área é "tem que ter".
    "chamado_prefixos": "RITM,INC",
    "chamado_estados_bloqueados": "7,8",

    # Para onde volta a unidade reprovada na configuração.
    "estado_reparo": "AG_TRIAGEM",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    from db._esquema import migrar_colunas
    migrar_colunas(Base, get_engine(), "projetos")
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("projetos: %d parâmetro(s) padrão criados", len(novas))


def ler_config() -> dict[str, str]:
    with SessionLocal() as s:
        atual = {c.chave: c.valor for c in s.execute(select(Config)).scalars()}
    return {**PADROES, **atual}


def gravar_config(pares: dict[str, str]) -> None:
    with SessionLocal() as s:
        for chave, valor in pares.items():
            linha = s.get(Config, chave)
            if linha is None:
                s.add(Config(chave=chave, valor=str(valor)))
            else:
                linha.valor = str(valor)
        s.commit()


def proximo_numero(s) -> str:
    """PRJ-AAAA-NNNN, sequencial por ano."""
    ano = utcnow().year
    prefixo = f"PRJ-{ano}-"
    ultimo = s.execute(
        select(Projeto.numero)
        .where(Projeto.numero.like(f"{prefixo}%"))
        .order_by(Projeto.numero.desc())
    ).scalars().first()
    seq = int(ultimo.rsplit("-", 1)[1]) + 1 if ultimo else 1
    return f"{prefixo}{seq:04d}"
