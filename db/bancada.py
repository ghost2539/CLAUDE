"""Banco ISOLADO das Bancadas de Triagem e Reparo (A02, A03, A04).

Três bancadas com a mesma mecânica e escopos diferentes:

- **A02 Frota** — coletores e sleds
- **A03 Loja** — PDV, impressora, desktop, periférico
- **A04 Conectividade** — AP, switch, roteador

O estado do ativo NÃO mora aqui: mora na Trilha (`db.trilha`). Este
banco guarda o que aconteceu na bancada — causa, peças, destino — e é
isso que alimenta a ponte com mobilidade (G12) mais tarde.

`Causa` é tabela e não lista fixa no código porque a lista muda com o
parque, e um campo de texto livre no lugar dela produziria "não liga",
"nao liga" e "Não Liga" como três causas diferentes — que é o mesmo que
não ter o campo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, Index,
    create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("bancada.db")

DATABASE_URL: str = getattr(
    _cfg, "BANCADA_DATABASE_URL", _config_mod._sqlite("bancada"),
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


# ── As três bancadas ───────────────────────────────────────────────
FROTA = "FROTA"                  # A02 — coletor e sled
LOJA = "LOJA"                    # A03 — frente e retaguarda
CONECTIVIDADE = "CONECTIVIDADE"  # A04 — AP, switch, roteador
BANCADAS = (FROTA, LOJA, CONECTIVIDADE)

ROTULO_BANCADA = {
    FROTA: "Frota móvel",
    LOJA: "Equipamento de loja",
    CONECTIVIDADE: "Conectividade",
}

PROCESSO = {FROTA: "A02", LOJA: "A03", CONECTIVIDADE: "A04"}

# Estado de fila de cada bancada, na Trilha. Conectividade tem estado
# próprio porque a fila dela é de outra pessoa e outro indicador.
FILA_DA_BANCADA = {
    FROTA: "AG_TRIAGEM",
    LOJA: "AG_TRIAGEM",
    CONECTIVIDADE: "AG_TRIAGEM_CONECT",
}
TRATATIVA_DA_BANCADA = {
    FROTA: "EX_REPARO",
    LOJA: "EX_REPARO",
    CONECTIVIDADE: "EX_TRIAGEM_CONECT",
}

# ── Destinos possíveis e para onde cada um manda o ativo ───────────
APTO = "APTO"
AGUARDANDO_PECAS = "AGUARDANDO_PECAS"
ASSISTENCIA = "ASSISTENCIA"
INVIAVEL = "INVIAVEL"
DEVOLVER = "DEVOLVER"          # devolução a terceiro (A14): comodato, locação, garantia
DESTINOS = (APTO, AGUARDANDO_PECAS, ASSISTENCIA, INVIAVEL, DEVOLVER)

ROTULO_DESTINO = {
    APTO: "Apto / reparado",
    AGUARDANDO_PECAS: "Aguardando peças",
    ASSISTENCIA: "Assistência externa",
    INVIAVEL: "Reparo inviável",
    DEVOLVER: "Devolver ao terceiro",
}

# Estado seguinte na Trilha. Coletor apto vai configurar antes de voltar
# ao estoque; o resto vai direto para a internalização.
PROXIMO_ESTADO = {
    (FROTA, APTO): "AG_CONFIGURACAO",
    (LOJA, APTO): "AG_INTERNALIZACAO",
    (CONECTIVIDADE, APTO): "AG_INTERNALIZACAO",
    (FROTA, AGUARDANDO_PECAS): "AG_PECAS",
    (LOJA, AGUARDANDO_PECAS): "AG_PECAS",
    (CONECTIVIDADE, AGUARDANDO_PECAS): "AG_PECAS",
    (FROTA, ASSISTENCIA): "AG_ASSISTENCIA",
    (LOJA, ASSISTENCIA): "AG_ASSISTENCIA",
    (CONECTIVIDADE, ASSISTENCIA): "AG_ASSISTENCIA",
    (FROTA, INVIAVEL): "AG_DESCARACTERIZACAO",
    (LOJA, INVIAVEL): "AG_DESCARACTERIZACAO",
    (CONECTIVIDADE, INVIAVEL): "AG_DESCARACTERIZACAO",
    # A14 nasce aqui: sem este destino a fila de devolução do módulo
    # Assistência não tinha quem a alimentasse.
    (FROTA, DEVOLVER): "AG_DEVOLUCAO",
    (LOJA, DEVOLVER): "AG_DEVOLUCAO",
    (CONECTIVIDADE, DEVOLVER): "AG_DEVOLUCAO",
}


class Reparo(Base):
    """O que aconteceu com um ativo numa passagem pela bancada.

    Uma passagem, uma linha. O mesmo ativo que volta meses depois gera
    outra — é a reincidência, que o indicador precisa enxergar.
    """
    __tablename__ = "bnc_reparo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(120), index=True)
    trilha_ativo_id: Mapped[int | None] = mapped_column(Integer, default=None)
    bancada: Mapped[str] = mapped_column(String(16), default=FROTA, index=True)

    modelo: Mapped[str] = mapped_column(String(160), default="")
    fabricante: Mapped[str] = mapped_column(String(120), default="")
    tipo_equipamento: Mapped[str] = mapped_column(String(60), default="")
    garantia_vigente: Mapped[bool | None] = mapped_column(Boolean, default=None)

    acoes: Mapped[str] = mapped_column(Text, default="")
    causa_id: Mapped[int | None] = mapped_column(
        ForeignKey("bnc_causa.id", ondelete="RESTRICT"), default=None, index=True)
    # Texto exigido, com "N/A" explícito: em branco não distingue "não
    # trocou peça" de "esqueceu de preencher".
    pecas: Mapped[str] = mapped_column(Text, default="")

    destino: Mapped[str] = mapped_column(String(20), default=APTO, index=True)
    justificativa: Mapped[str] = mapped_column(Text, default="")
    fornecedor: Mapped[str] = mapped_column(String(120), default="")
    pecas_aguardadas: Mapped[str] = mapped_column(Text, default="")

    tecnico: Mapped[str] = mapped_column(String(80), default="", index=True)
    aberto_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    fechado_em: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)

    __table_args__ = (
        Index("ix_bnc_reparo_serial_data", "serial", "aberto_em"),
    )


class Causa(Base):
    """Causa de falha. Lista fechada, mantida em Parâmetros."""
    __tablename__ = "bnc_causa"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True)
    bancada: Mapped[str] = mapped_column(String(16), default="", index=True)  # vazio = todas
    ativa: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Config(Base):
    __tablename__ = "bnc_config"
    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Janela da reincidência, em dias. O documento fala em 60 e em 90 em
    # pontos diferentes; fica configurável em vez de escolhido no código.
    "janela_reincidencia": "90",
    # Quantos dias um ativo pode ficar em AG_PECAS antes de virar alerta.
    # Sem isso a fila de peças vira cemitério.
    "alerta_pecas_dias": "15",
}

# Ponto de partida da lista de causas. É semente, não regra: a área
# ajusta em Parâmetros conforme o parque ensina.
CAUSAS_INICIAIS = [
    ("Bateria não retém carga", FROTA),
    ("Falha de leitura do scanner", FROTA),
    ("Tela quebrada ou sem imagem", FROTA),
    ("Conector de carga danificado", FROTA),
    ("Não liga", ""),
    ("Dano por queda", ""),
    ("Dano por líquido", ""),
    ("Falha de teclado ou botão", ""),
    ("Superaquecimento", ""),
    ("Falha de fonte de alimentação", LOJA),
    ("Cabeça de impressão gasta", LOJA),
    ("Falha de comunicação com o PDV", LOJA),
    ("Porta de rede sem link", CONECTIVIDADE),
    ("Sem alimentação PoE", CONECTIVIDADE),
    ("Firmware corrompido", ""),
    ("Sem defeito encontrado", ""),
]


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
        if not s.execute(select(Causa.id)).first():
            s.add_all([Causa(nome=n, bancada=b) for n, b in CAUSAS_INICIAIS])
            _log.info("bancada: %d causas iniciais criadas", len(CAUSAS_INICIAIS))
        s.commit()


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
