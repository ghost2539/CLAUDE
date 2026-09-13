"""Trilha do Ativo — motor de rastreabilidade e relógios.

Este módulo é a única porta de entrada para mexer no núcleo. Nenhum
processo escreve em `db.trilha` direto: todos chamam `mover()`, e é por
isso que a trilha fica consistente sem depender de disciplina de quem
escreve o processo seguinte.

O que o motor garante, em uma transação:

1. fecha o intervalo aberto daquela trilha, se houver;
2. grava a movimentação (que nunca mais é alterada);
3. abre o intervalo novo, numerando a sessão;
4. atualiza o estado corrente do ativo.

Regra de ouro: um ativo tem, em cada trilha, no máximo um intervalo
aberto. Duas trilhas podem correr juntas — é o caso da entrada de ativo
novo, em que o equipamento segue para o estoque sem esperar o EBS.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, time, date, timezone

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func

import db.trilha as db
from db.trilha import (
    Ativo, Movimentacao, Intervalo, SessionLocal,
    FISICA, ADMINISTRATIVA, TRILHAS,
    FILA, TRATATIVA, EXTERNO, TIPOS,
    PORTAL, ADMIN, AUTOMACAO, ORIGENS,
)
from core.security import require_permission, check_rate_limit

_log = logging.getLogger("trilha")

router = APIRouter(prefix="/api/trilha", tags=["Trilha do Ativo"])


# ══════════════════════════════════════════════════════════════════
#  Calendário de expediente
# ══════════════════════════════════════════════════════════════════

class Calendario:
    """Expediente já interpretado, para não reprocessar texto por linha."""

    def __init__(self, cfg: dict[str, str]):
        self.dias = {int(d) for d in _lista(cfg.get("expediente_dias", ""))
                     if d.isdigit() and 0 <= int(d) <= 6}
        self.inicio = _hora(cfg.get("expediente_inicio", ""), time(0, 0))
        self.fim = _hora(cfg.get("expediente_fim", ""), time(23, 59, 59))
        self.feriados = _datas(cfg.get("feriados", ""))
        try:
            self.fuso = timedelta(hours=float(cfg.get("fuso_horas", "0") or 0))
        except ValueError:
            self.fuso = timedelta(0)
        # Sem dia útil configurado, o relógio corre direto. É o que faz
        # sentido enquanto o calendário da área não estiver definido:
        # medir corrido é impreciso, medir zero é errado.
        self.corrido = not self.dias or self.fim <= self.inicio


def _lista(texto: str) -> list[str]:
    return [p.strip() for p in (texto or "").split(",") if p.strip()]


def _hora(texto: str, padrao: time) -> time:
    try:
        h, m = (texto or "").split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return padrao


def _datas(texto: str) -> set[date]:
    fora = set()
    for p in _lista(texto):
        try:
            fora.add(date.fromisoformat(p))
        except ValueError:
            _log.warning("trilha: feriado ignorado, data inválida: %r", p)
    return fora


def _utc(dt: datetime | None) -> datetime | None:
    """Devolve a data ciente de fuso, assumindo UTC quando vier sem.

    O SQLite não guarda o fuso: tudo que é gravado como UTC volta ingênuo.
    Sem esta normalização, comparar uma data lida do banco com `utcnow()`
    levanta TypeError — e o erro só apareceria na segunda movimentação de
    um ativo, já em produção.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# Um intervalo aberto há mais de dois anos é defeito, não espera. O laço
# para aí para que um registro corrompido não trave uma listagem inteira.
_TETO_DIAS = 760


def duracao_util(inicio: datetime, fim: datetime | None,
                 cal: Calendario, agora: datetime | None = None) -> int:
    """Segundos úteis entre `inicio` e `fim` (ou até agora, se aberto).

    O desconto de expediente acontece aqui, na leitura — nunca na
    gravação. Mudar o calendário recalcula todo o histórico.
    """
    inicio = _utc(inicio)
    fim = _utc(fim) or agora or db.utcnow()
    if fim <= inicio:
        return 0
    if cal.corrido:
        return int((fim - inicio).total_seconds())

    ini = (inicio + cal.fuso).replace(tzinfo=None)
    fin = (fim + cal.fuso).replace(tzinfo=None)

    total = 0.0
    dia = ini.date()
    ultimo = fin.date()
    if (ultimo - dia).days > _TETO_DIAS:
        ultimo = dia + timedelta(days=_TETO_DIAS)
    while dia <= ultimo:
        if dia.weekday() in cal.dias and dia not in cal.feriados:
            abre = datetime.combine(dia, cal.inicio)
            fecha = datetime.combine(dia, cal.fim)
            de = max(ini, abre)
            ate = min(fin, fecha)
            if ate > de:
                total += (ate - de).total_seconds()
        dia += timedelta(days=1)
    return int(total)


def prazo_util(inicio: datetime, dias_uteis: float, cal: Calendario) -> datetime:
    """Instante que fica `dias_uteis` dias úteis depois de `inicio`.

    Um dia útil é uma jornada inteira de expediente, não 24 horas: com
    expediente das 8 às 18, três dias úteis são 30 horas de trabalho, e
    o prazo cai na tarde do terceiro dia, não na madrugada do quarto.
    """
    inicio = _utc(inicio)
    if dias_uteis <= 0:
        return inicio
    if cal.corrido:
        return inicio + timedelta(days=dias_uteis)

    jornada = (datetime.combine(date(2000, 1, 1), cal.fim)
               - datetime.combine(date(2000, 1, 1), cal.inicio)).total_seconds()
    if jornada <= 0:
        return inicio + timedelta(days=dias_uteis)

    faltam = jornada * dias_uteis
    # Avança de hora em hora e desconta só o que for útil. Passo grosso
    # o bastante para ser barato e fino o bastante para o prazo cair na
    # hora certa do dia — que é o que a fila mostra.
    quando = inicio
    passo = timedelta(minutes=30)
    for _ in range(int(_TETO_DIAS * 48)):
        proximo = quando + passo
        faltam -= duracao_util(quando, proximo, cal)
        quando = proximo
        if faltam <= 0:
            return quando
    return quando


# ══════════════════════════════════════════════════════════════════
#  Motor — a única forma de mover um ativo
# ══════════════════════════════════════════════════════════════════

class TrilhaInvalida(Exception):
    """Chamada que violaria a consistência do núcleo."""


def abrir_ativo(s, *, serial: str, usuario: str, modelo: str = "",
                numero_ativo: str = "", tipo_equipamento: str = "",
                origem: str = "", bu: str = "") -> Ativo:
    """Cria o token do ativo. Serial repetido é erro, não atualização.

    Um serial que já existe significa reentrada (o mesmo equipamento
    voltando) ou erro de digitação. Quem chama decide qual é — o núcleo
    não adivinha, porque as duas coisas pedem tratamento oposto.
    """
    serial = (serial or "").strip().upper()
    if not serial:
        raise TrilhaInvalida("Serial obrigatório para abrir o ativo.")
    ja = s.execute(select(Ativo).where(Ativo.serial == serial)).scalar_one_or_none()
    if ja is not None:
        raise TrilhaInvalida(f"Serial {serial} já existe na trilha (ativo {ja.id}).")

    ativo = Ativo(
        serial=serial,
        numero_ativo=(numero_ativo or "").strip(),
        modelo=(modelo or "").strip(),
        tipo_equipamento=(tipo_equipamento or "").strip(),
        origem=(origem or "").strip(),
        bu=(bu or "").strip(),
        criado_por=usuario,
    )
    s.add(ativo)
    s.flush()
    return ativo


def mover(s, ativo: Ativo, *, estado: str, tipo: str, processo: str = "",
          trilha: str = FISICA, usuario: str = "", justificativa: str = "",
          origem: str = PORTAL, detalhe: str = "",
          quando: datetime | None = None) -> Movimentacao:
    """Transição de estado: fecha o relógio anterior e abre o próximo.

    Recebe a sessão do processo que chama, e não abre transação própria:
    assim a movimentação e o que o processo grava no banco dele entram
    ou falham juntos.
    """
    estado = (estado or "").strip().upper()
    if not estado:
        raise TrilhaInvalida("Estado de destino obrigatório.")
    if trilha not in TRILHAS:
        raise TrilhaInvalida(f"Trilha desconhecida: {trilha!r}")
    if tipo not in TIPOS:
        raise TrilhaInvalida(f"Tipo de intervalo desconhecido: {tipo!r}")
    if origem not in ORIGENS:
        raise TrilhaInvalida(f"Origem desconhecida: {origem!r}")
    if origem == ADMIN and not (justificativa or "").strip():
        raise TrilhaInvalida(
            "Correção manual exige justificativa — é o que a auditoria lê.")
    if tipo == TRATATIVA and not (usuario or "").strip():
        raise TrilhaInvalida(
            "Tratativa exige usuário: é o relógio de alguém que começa a correr.")
    if ativo.encerrado:
        raise TrilhaInvalida(
            f"Ativo {ativo.serial} está encerrado; reabra antes de movimentar.")

    quando = quando or db.utcnow()
    atual = _estado_atual(ativo, trilha)

    aberto = _intervalo_aberto(s, ativo.id, trilha)
    if aberto is not None and _utc(aberto.inicio) > quando:
        raise TrilhaInvalida(
            "Movimentação anterior ao intervalo aberto — relógio não anda para trás.")

    mov = Movimentacao(
        ativo_id=ativo.id, trilha=trilha,
        estado_de=atual, estado_para=estado, processo=processo,
        usuario=usuario, origem=origem,
        justificativa=justificativa, detalhe=detalhe, quando=quando,
    )
    s.add(mov)
    s.flush()

    if aberto is not None:
        aberto.fim = quando
        aberto.mov_fechou_id = mov.id

    s.add(Intervalo(
        ativo_id=ativo.id, trilha=trilha, estado=estado, processo=processo,
        tipo=tipo, sessao=_proxima_sessao(s, ativo.id, trilha, estado),
        inicio=quando, usuario=(usuario if tipo == TRATATIVA else ""),
        mov_abriu_id=mov.id,
    ))
    # A sessão é autoflush=False de propósito (o processo controla quando
    # grava). Sem este flush, uma segunda movimentação na MESMA sessão não
    # enxergaria o intervalo recém-aberto e deixaria dois relógios correndo.
    s.flush()
    _gravar_estado(ativo, trilha, estado)
    return mov


def encerrar(s, ativo: Ativo, *, estado: str, processo: str = "",
             trilha: str = FISICA, usuario: str = "",
             justificativa: str = "", origem: str = PORTAL,
             quando: datetime | None = None) -> Movimentacao:
    """Estado final: fecha o relógio e não abre outro.

    Usado nas pontas de saída (entregue, doado, descartado, vendido). O
    ativo continua na base inteiro — encerrar é parar o relógio, não
    apagar a trilha.
    """
    mov = mover(s, ativo, estado=estado, tipo=FILA, processo=processo,
                trilha=trilha, usuario=usuario, justificativa=justificativa,
                origem=origem, quando=quando)
    ultimo = _intervalo_aberto(s, ativo.id, trilha)
    if ultimo is not None:
        ultimo.fim = mov.quando
        ultimo.mov_fechou_id = mov.id
    ativo.encerrado = True
    return mov


def _estado_atual(ativo: Ativo, trilha: str) -> str:
    return (ativo.estado_fisico if trilha == FISICA
            else ativo.estado_administrativo)


def _gravar_estado(ativo: Ativo, trilha: str, estado: str) -> None:
    if trilha == FISICA:
        ativo.estado_fisico = estado
    else:
        ativo.estado_administrativo = estado


def _intervalo_aberto(s, ativo_id: int, trilha: str) -> Intervalo | None:
    return s.execute(
        select(Intervalo)
        .where(Intervalo.ativo_id == ativo_id,
               Intervalo.trilha == trilha,
               Intervalo.fim.is_(None))
        .order_by(Intervalo.inicio.desc())
    ).scalars().first()


def _proxima_sessao(s, ativo_id: int, trilha: str, estado: str) -> int:
    quantas = s.execute(
        select(func.count(Intervalo.id))
        .where(Intervalo.ativo_id == ativo_id,
               Intervalo.trilha == trilha,
               Intervalo.estado == estado)
    ).scalar_one()
    return int(quantas) + 1


# ══════════════════════════════════════════════════════════════════
#  Leitura — a trilha e os tempos
# ══════════════════════════════════════════════════════════════════

def trilha_do_ativo(serial: str) -> dict:
    """Linha do tempo completa de um ativo, com os tempos já calculados."""
    cal = Calendario(db.ler_config())
    agora = db.utcnow()
    with SessionLocal() as s:
        ativo = s.execute(
            select(Ativo).where(Ativo.serial == (serial or "").strip().upper())
        ).scalar_one_or_none()
        if ativo is None:
            raise TrilhaInvalida(f"Ativo {serial} não está na trilha.")

        movs = s.execute(
            select(Movimentacao)
            .where(Movimentacao.ativo_id == ativo.id)
            .order_by(Movimentacao.quando, Movimentacao.id)
        ).scalars().all()
        intervalos = s.execute(
            select(Intervalo)
            .where(Intervalo.ativo_id == ativo.id)
            .order_by(Intervalo.inicio, Intervalo.id)
        ).scalars().all()

    linhas = []
    por_estado: dict[str, int] = {}
    total_area = 0
    for i in intervalos:
        inicio, fim = _utc(i.inicio), _utc(i.fim)
        seg = duracao_util(inicio, fim, cal, agora)
        total_area += seg
        por_estado[i.estado] = por_estado.get(i.estado, 0) + seg
        linhas.append({
            "estado": i.estado, "processo": i.processo, "trilha": i.trilha,
            "tipo": i.tipo, "sessao": i.sessao,
            "inicio": inicio.isoformat(),
            "fim": fim.isoformat() if fim else None,
            "aberto": i.fim is None,
            "usuario": i.usuario,
            "segundos_uteis": seg,
        })

    return {
        "ativo": {
            "serial": ativo.serial, "numero_ativo": ativo.numero_ativo,
            "modelo": ativo.modelo, "tipo_equipamento": ativo.tipo_equipamento,
            "origem": ativo.origem, "bu": ativo.bu,
            "estado_fisico": ativo.estado_fisico,
            "estado_administrativo": ativo.estado_administrativo,
            "encerrado": ativo.encerrado,
            "criado_em": _utc(ativo.criado_em).isoformat(),
            "criado_por": ativo.criado_por,
        },
        "intervalos": linhas,
        "movimentacoes": [{
            "quando": _utc(m.quando).isoformat(), "trilha": m.trilha,
            "de": m.estado_de, "para": m.estado_para, "processo": m.processo,
            "usuario": m.usuario, "origem": m.origem,
            "justificativa": m.justificativa,
        } for m in movs],
        "por_estado": [{"estado": e, "segundos_uteis": v}
                       for e, v in sorted(por_estado.items(), key=lambda x: -x[1])],
        # O indicador que a área não tem hoje: quanto tempo o equipamento
        # ficou na mão do SPARE, somando fila e tratativa.
        "tempo_total_uteis": total_area,
        "calendario_corrido": cal.corrido,
    }


def filas() -> list[dict]:
    """Uma linha por estado com relógio correndo: quantos e o mais antigo."""
    cal = Calendario(db.ler_config())
    agora = db.utcnow()
    with SessionLocal() as s:
        abertos = s.execute(
            select(Intervalo).where(Intervalo.fim.is_(None))
        ).scalars().all()

    grupos: dict[tuple[str, str, str], list[Intervalo]] = {}
    for i in abertos:
        grupos.setdefault((i.estado, i.processo, i.tipo), []).append(i)

    saida = []
    for (estado, processo, tipo), itens in grupos.items():
        tempos = [duracao_util(_utc(i.inicio), None, cal, agora) for i in itens]
        saida.append({
            "estado": estado, "processo": processo, "tipo": tipo,
            "quantidade": len(itens),
            "mais_antigo_segundos": max(tempos) if tempos else 0,
            "media_segundos": int(sum(tempos) / len(tempos)) if tempos else 0,
        })
    saida.sort(key=lambda x: -x["mais_antigo_segundos"])
    return saida


# ══════════════════════════════════════════════════════════════════
#  API
# ══════════════════════════════════════════════════════════════════

@router.get("/ativos/{serial}")
def api_trilha_ativo(serial: str, req: Request):
    require_permission(req, "trilha", "view")
    check_rate_limit(req, "api")
    try:
        return trilha_do_ativo(serial)
    except TrilhaInvalida as exc:
        raise HTTPException(404, str(exc))


@router.get("/filas")
def api_filas(req: Request):
    require_permission(req, "trilha", "view")
    check_rate_limit(req, "api")
    return {"filas": filas(), "atualizado_em": db.utcnow().isoformat()}


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "trilha", "admin")
    return db.ler_config()


class ConfigIn(BaseModel):
    expediente_dias: str | None = None
    expediente_inicio: str | None = None
    expediente_fim: str | None = None
    feriados: str | None = None
    fuso_horas: str | None = None
    sla_horas: str | None = None


@router.put("/config")
def api_config_gravar(body: ConfigIn, req: Request):
    sd = require_permission(req, "trilha", "admin")
    pares = {k: v for k, v in body.model_dump().items() if v is not None}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")

    # Validar antes de gravar: calendário quebrado não derruba nada na
    # hora, só devolve tempo errado depois — o pior tipo de defeito.
    teste = Calendario({**db.ler_config(), **pares})
    if "expediente_inicio" in pares or "expediente_fim" in pares:
        if teste.fim <= teste.inicio:
            raise HTTPException(400, "O fim do expediente precisa ser depois do início.")
    if "feriados" in pares:
        for p in _lista(pares["feriados"]):
            try:
                date.fromisoformat(p)
            except ValueError:
                raise HTTPException(400, f"Feriado inválido: {p} (use AAAA-MM-DD).")

    db.gravar_config(pares)
    _log.info("trilha: calendário alterado por %s: %s",
              sd.get("username", "?"), ", ".join(pares))
    return db.ler_config()
