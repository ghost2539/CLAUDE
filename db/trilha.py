"""Banco ISOLADO da Trilha do Ativo — o núcleo de rastreabilidade.

É a espinha dos processos da área: todo módulo que toca um equipamento
registra aqui, e todo indicador de tempo sai daqui. Guarda:

- `Ativo`: o token que atravessa os processos. Um por equipamento, criado
  no momento em que ele entra na área — nunca retroativo.
- `Movimentacao`: uma linha por transição de estado. Nunca é alterada nem
  apagada; correção se faz por nova movimentação com justificativa.
- `Intervalo`: o relógio. Um por período que o ativo passou num estado,
  com início e fim CRUS (hora real, sem desconto).
- `Config`: calendário de expediente e metas de SLA por etapa.

## Por que o intervalo é gravado cru

O tempo que interessa desconta fim de semana, fora de expediente e
feriado. Esse desconto NÃO é gravado: é calculado na leitura, a partir do
calendário em `Config`. Assim, quando o calendário mudar — e ele vai
mudar, feriado é anual — todo o histórico se recalcula sozinho. Gravar o
número já descontado congelaria uma conta feita com a regra errada.

## Por que sessões, e não um par início/fim

Um reparo que para para esperar peça e volta depois é o mesmo trabalho,
não dois. Cada retomada abre uma sessão nova do mesmo par (ativo, etapa),
e o total é a soma. Guardar só o primeiro início e o último fim contaria
a espera como se fosse bancada.

## Exclusividade é POR TRILHA

Um ativo tem no máximo um intervalo aberto em cada trilha. São duas:
a física (onde o equipamento está, quem o detém) e a administrativa (onde
está o registro fiscal e patrimonial). Elas correm em paralelo de
propósito na entrada de ativo novo: o equipamento vai para o estoque sem
esperar o EBS.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, Boolean, DateTime, ForeignKey, Index,
    UniqueConstraint, create_engine, event, select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

import config as _config_mod

_cfg = _config_mod.get_settings()
_log = logging.getLogger("trilha.db")

DATABASE_URL: str = getattr(
    _cfg, "TRILHA_DATABASE_URL", _config_mod._sqlite("trilha"),
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
                # O núcleo tem chave estrangeira de verdade: intervalo órfão
                # é indicador errado, e o SQLite não checa isso por padrão.
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


# ── Vocabulário do núcleo ──────────────────────────────────────────
# Trilhas. A física é a única obrigatória; a administrativa só existe em
# processos que têm ponta fiscal ou patrimonial.
FISICA = "FISICA"
ADMINISTRATIVA = "ADMINISTRATIVA"
TRILHAS = (FISICA, ADMINISTRATIVA)

# Natureza do intervalo — é o que separa o que se cobra de quem.
FILA = "FILA"              # o ativo aguarda; mede o fluxo, não a pessoa
TRATATIVA = "TRATATIVA"    # alguém assumiu a custódia; mede o colaborador
EXTERNO = "EXTERNO"        # espera de terceiro; nunca entra em avaliação
TIPOS = (FILA, TRATATIVA, EXTERNO)

# Como a movimentação foi feita. ADMIN exige justificativa.
PORTAL = "PORTAL"          # o fluxo normal, pelo bipe
ADMIN = "ADMIN"            # correção manual por perfil administrador
AUTOMACAO = "AUTOMACAO"    # rotina do próprio portal
ORIGENS = (PORTAL, ADMIN, AUTOMACAO)


class Ativo(Base):
    """O token que atravessa os processos.

    Criado quando o equipamento entra na área. Ativos que já estavam em
    circulação antes do módulo entrar no ar NÃO são criados aqui: a
    contagem começa nos novos, sem retroativo.
    """
    __tablename__ = "trl_ativo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    serial: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    numero_ativo: Mapped[str] = mapped_column(String(120), default="", index=True)
    modelo: Mapped[str] = mapped_column(String(120), default="")
    tipo_equipamento: Mapped[str] = mapped_column(String(60), default="", index=True)
    bu: Mapped[str] = mapped_column(String(20), default="")

    # De onde veio (loja, fornecedor, assistência, transferência, reversa,
    # projeto cancelado). Texto livre controlado por quem chama, porque a
    # lista de origens é do processo de entrada, não do núcleo.
    origem: Mapped[str] = mapped_column(String(40), default="", index=True)

    # Estado corrente em cada trilha — cache do que a última movimentação
    # daquela trilha registrou. A verdade é a movimentação; isto é atalho
    # de consulta, para não varrer o histórico a cada listagem de fila.
    estado_fisico: Mapped[str] = mapped_column(String(50), default="", index=True)
    estado_administrativo: Mapped[str] = mapped_column(String(50), default="", index=True)

    encerrado: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    criado_por: Mapped[str] = mapped_column(String(80), default="")

    __table_args__ = (
        Index("ix_trl_ativo_fila", "estado_fisico", "encerrado"),
    )


class Movimentacao(Base):
    """Uma transição de estado. Imutável: nunca alterada, nunca apagada.

    Correção se faz por nova movimentação com justificativa, para que a
    trilha continue servindo de prova em auditoria.
    """
    __tablename__ = "trl_movimentacao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ativo_id: Mapped[int] = mapped_column(
        ForeignKey("trl_ativo.id", ondelete="RESTRICT"), index=True)
    trilha: Mapped[str] = mapped_column(String(16), default=FISICA, index=True)

    estado_de: Mapped[str] = mapped_column(String(50), default="")
    estado_para: Mapped[str] = mapped_column(String(50), default="", index=True)

    # Processo que produziu a transição (A01, A02, A15...). Guardado para
    # que o painel some por etapa sem ter que deduzir pelo nome do estado.
    processo: Mapped[str] = mapped_column(String(12), default="", index=True)

    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)
    origem: Mapped[str] = mapped_column(String(16), default=PORTAL, index=True)
    justificativa: Mapped[str] = mapped_column(Text, default="")
    detalhe: Mapped[str] = mapped_column(Text, default="")   # JSON livre do processo

    quando: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)

    __table_args__ = (
        Index("ix_trl_mov_ativo_quando", "ativo_id", "quando"),
    )


class Intervalo(Base):
    """O relógio: um período que o ativo passou num estado.

    `inicio` e `fim` são hora real, sem desconto de expediente. O tempo
    útil é calculado na leitura — ver o cabeçalho do módulo.
    """
    __tablename__ = "trl_intervalo"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ativo_id: Mapped[int] = mapped_column(
        ForeignKey("trl_ativo.id", ondelete="RESTRICT"), index=True)
    trilha: Mapped[str] = mapped_column(String(16), default=FISICA, index=True)

    estado: Mapped[str] = mapped_column(String(50), default="", index=True)
    processo: Mapped[str] = mapped_column(String(12), default="", index=True)
    tipo: Mapped[str] = mapped_column(String(12), default=FILA, index=True)

    # Ordem da sessão para o mesmo par (ativo, estado): 1, 2, 3... Uma
    # etapa retomada depois de uma pausa continua na mesma contagem.
    sessao: Mapped[int] = mapped_column(Integer, default=1)

    inicio: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True)
    fim: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=True)

    # Quem detinha a custódia. Vazio em FILA: fila não é de ninguém.
    usuario: Mapped[str] = mapped_column(String(80), default="", index=True)

    mov_abriu_id: Mapped[int | None] = mapped_column(Integer, default=None)
    mov_fechou_id: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_trl_int_aberto", "ativo_id", "trilha", "fim"),
        Index("ix_trl_int_estado_fim", "estado", "fim"),
    )


class Config(Base):
    """Parâmetros do núcleo, em chave/valor para não migrar a cada ajuste."""
    __tablename__ = "trl_config"

    chave: Mapped[str] = mapped_column(String(60), primary_key=True)
    valor: Mapped[str] = mapped_column(Text, default="")


PADROES = {
    # Calendário de expediente. PROVISÓRIO: estes valores são um palpite
    # razoável, não a regra da área — ainda não foi definida. Como o
    # intervalo é gravado cru, trocar isto recalcula todo o histórico.
    "expediente_dias": "0,1,2,3,4",     # 0 = segunda ... 6 = domingo
    "expediente_inicio": "08:00",
    "expediente_fim": "18:00",
    "feriados": "",                     # ISO separadas por vírgula: 2026-12-25,...
    "fuso_horas": "-3",                 # deslocamento de Brasília em relação ao UTC

    # Metas de SLA por etapa, em horas úteis: "ESTADO=horas" por vírgula.
    # Vazio de propósito: o documento recomenda o primeiro mês só medindo.
    "sla_horas": "",
}


def init_db() -> None:
    Base.metadata.create_all(bind=get_engine())
    with SessionLocal() as s:
        existentes = {c for (c,) in s.execute(select(Config.chave))}
        novas = [Config(chave=k, valor=v)
                 for k, v in PADROES.items() if k not in existentes]
        if novas:
            s.add_all(novas)
            s.commit()
            _log.info("trilha: %d parâmetro(s) padrão criados", len(novas))


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
