"""Destinação (A09 a A13) — o fim da vida do ativo na área.

Fecha o caminho que a bancada abre com "reparo inviável" e o que o
recebimento classifica direto como destinação.

Duas travas que o código não negocia, porque são de conformidade e não
de processo:

- **Remoção de mídia exige evidência anexada.** Sem o arquivo, a
  descaracterização não conclui. É o documento que a empresa apresenta
  se for questionada, e 99% aqui não é bom resultado, é exposição.
- **Descarte exige certificado e doação exige termo.** Mesma lógica: o
  lote não fecha sem o papel que prova para onde o equipamento foi.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select, func

import db.destinacao as db
import db.trilha as dbt
from db.destinacao import (
    Descaracterizacao, Lote, LoteItem, Anexo, SessionLocal,
    VENDA, DESCARTE, DOACAO, DESTINOS, ROTULO_DESTINO, PROCESSO_DO_DESTINO,
    ABERTO, AG_TRATATIVA, EM_TRATATIVA, CONCLUIDO, ROTULO_ESTADO,
    REMOCAO_FISICA, LAUDO_ERS, SEM_MIDIA, METODOS, ROTULO_METODO,
)
from config import get_settings
from core.security import require_permission, check_rate_limit
from routers.trilha import Calendario, duracao_util, mover, encerrar, TrilhaInvalida, _utc

_log = logging.getLogger("destinacao")
_cfg = get_settings()

router = APIRouter(prefix="/api/destinacao", tags=["Destinação"])

AG_DESCARACTERIZACAO = "AG_DESCARACTERIZACAO"
EX_DESCARACTERIZACAO = "EX_DESCARACTERIZACAO"
AG_DEFINICAO_DESTINO = "AG_DEFINICAO_DESTINO"

# Documento exigido por destino. É a regra de conformidade em uma linha.
DOCUMENTO_EXIGIDO = {
    DESCARTE: ("certificado_destinacao", "certificado de destinação"),
    DOACAO: ("termo_doacao", "termo de doação"),
    VENDA: ("nf_venda", "nota fiscal de venda"),
}


# Tipos de anexo aceitos: os documentos exigidos por destino, mais um
# genérico. Texto livre entrava no nome do arquivo e qualquer string
# passava por "certificado de destinação" na conferência do lote.
TIPOS_ANEXO = {t for t, _ in DOCUMENTO_EXIGIDO.values()} | {"outro"}


def _pasta_anexos() -> Path:
    # data/uploads é o único lugar de gravação previsto pelas normas do
    # projeto; nada é escrito fora dali.
    destino = Path(_cfg.UPLOAD) / "destinacao"
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _calendario() -> Calendario:
    return Calendario(dbt.ler_config())


# ══════════════════════════════════════════════════════════════════
#  A09 — Descaracterização
# ══════════════════════════════════════════════════════════════════

@router.get("/fila")
def api_fila(req: Request):
    """Ativos aguardando descaracterização e os que já estão na bancada."""
    require_permission(req, "destinacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with dbt.SessionLocal() as s:
        linhas = s.execute(
            select(dbt.Intervalo, dbt.Ativo)
            .join(dbt.Ativo, dbt.Ativo.id == dbt.Intervalo.ativo_id)
            .where(dbt.Intervalo.fim.is_(None),
                   dbt.Intervalo.estado.in_(
                       (AG_DESCARACTERIZACAO, EX_DESCARACTERIZACAO,
                        AG_DEFINICAO_DESTINO)))
            .order_by(dbt.Intervalo.inicio)
        ).all()

    # Sem destino há tempo demais vira alerta: é decisão de gestor, não
    # de bancada, e é onde equipamento descaracterizado vira volume morto.
    try:
        dias_alerta = float(db.ler_config().get("alerta_sem_destino_dias") or 0)
    except ValueError:
        dias_alerta = 0

    fila, curso, sem_destino = [], [], []
    for i, a in linhas:
        item = {
            "serial": a.serial, "modelo": a.modelo, "origem": a.origem,
            "usuario": i.usuario,
            "segundos": duracao_util(_utc(i.inicio), None, cal, agora),
        }
        if i.estado == AG_DEFINICAO_DESTINO and dias_alerta > 0:
            item["atrasado"] = prazo_util(_utc(i.inicio), dias_alerta, cal) < agora
        if i.estado == AG_DESCARACTERIZACAO:
            fila.append(item)
        elif i.estado == EX_DESCARACTERIZACAO:
            curso.append(item)
        else:
            sem_destino.append(item)

    return {"aguardando": fila, "em_curso": curso,
            "aguardando_destino": sem_destino}


class BipeIn(BaseModel):
    serial: str


@router.post("/bipar")
def api_bipar(body: BipeIn, req: Request):
    sd = require_permission(req, "destinacao", "edit")
    check_rate_limit(req)
    serial = (body.serial or "").strip().upper()
    usuario = sd.get("username", "")
    if not serial:
        raise HTTPException(400, "Bipe a série do equipamento.")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != AG_DESCARACTERIZACAO:
            raise HTTPException(
                409, f"A série {serial} não está aguardando descaracterização "
                     f"— está em {ativo.estado_fisico or 'estado desconhecido'}.")
        try:
            mover(s, ativo, estado=EX_DESCARACTERIZACAO, tipo=dbt.TRATATIVA,
                  processo="A09", usuario=usuario)
        except TrilhaInvalida as exc:
            raise HTTPException(409, str(exc))
        s.commit()
        dados = {"serial": ativo.serial, "modelo": ativo.modelo,
                 "origem": ativo.origem}
    return dados


@router.post("/descaracterizar")
async def api_descaracterizar(
    req: Request,
    serial: str = Form(...),
    possui_midia: bool = Form(False),
    serie_midia: str = Form(""),
    metodo: str = Form(SEM_MIDIA),
    condicao_fisica: str = Form(""),
    observacao: str = Form(""),
    local_destino: str = Form(""),
    evidencia: UploadFile | None = File(None),
):
    """Conclui a descaracterização. Com mídia, a evidência é obrigatória."""
    sd = require_permission(req, "destinacao", "edit")
    serial = (serial or "").strip().upper()
    usuario = sd.get("username", "")

    if metodo not in METODOS:
        raise HTTPException(400, "Método inválido.")
    if condicao_fisica not in ("boa", "regular", "ruim"):
        raise HTTPException(
            400, "Informe a condição física: é ela que define se o "
                 "equipamento é candidato a doação.")
    if possui_midia:
        if not (serie_midia or "").strip():
            raise HTTPException(
                400, "Informe o número de série da mídia — a rastreabilidade "
                     "é por série, não por equipamento.")
        if metodo == SEM_MIDIA:
            raise HTTPException(400, "Escolha o método de remoção da mídia.")
        if evidencia is None or not evidencia.filename:
            raise HTTPException(
                400, "Anexe a evidência da remoção de mídia. Sem ela a "
                     "descaracterização não conclui.")

    with dbt.SessionLocal() as s:
        ativo = s.execute(
            select(dbt.Ativo).where(dbt.Ativo.serial == serial)
        ).scalar_one_or_none()
        if ativo is None:
            raise HTTPException(404, f"A série {serial} não está na trilha.")
        if ativo.estado_fisico != EX_DESCARACTERIZACAO:
            raise HTTPException(409, "Bipe o equipamento antes de concluir.")
        ativo_id = ativo.id

    with SessionLocal() as s:
        registro = Descaracterizacao(
            serial=serial, trilha_ativo_id=ativo_id,
            possui_midia=possui_midia, serie_midia=(serie_midia or "").strip().upper(),
            metodo=metodo, condicao_fisica=condicao_fisica,
            observacao=(observacao or "").strip(),
            local_destino=(local_destino or "").strip(), usuario=usuario,
        )
        s.add(registro)
        s.flush()
        registro_id = registro.id
        if evidencia is not None and evidencia.filename:
            await _guardar_anexo(s, evidencia, "descaracterizacao",
                                 registro_id, "evidencia_midia", usuario)
        s.commit()

    # O ativo só anda depois que a evidência está guardada: mover antes
    # deixaria um ativo "descaracterizado" sem o documento que prova.
    with dbt.SessionLocal() as s:
        ativo = s.get(dbt.Ativo, ativo_id)
        mover(s, ativo, estado=AG_DEFINICAO_DESTINO, tipo=dbt.FILA,
              processo="A09", usuario=usuario)
        s.commit()

    _log.info("destinacao: %s descaracterizou %s (mídia: %s)",
              usuario, serial, "sim" if possui_midia else "não")
    return {"ok": True, "serial": serial}


async def _guardar_anexo(s, arquivo: UploadFile, especie: str,
                         referencia_id: int, tipo: str, usuario: str) -> Anexo:
    cfg = db.ler_config()
    permitidas = [e.strip().lower()
                  for e in (cfg.get("anexo_extensoes") or "").split(",") if e.strip()]
    try:
        limite = int(float(cfg.get("anexo_tamanho_mb") or 20) * 1024 * 1024)
    except ValueError:
        limite = 20 * 1024 * 1024

    nome = arquivo.filename or "anexo"
    extensao = Path(nome).suffix.lower()
    if permitidas and extensao not in permitidas:
        raise HTTPException(
            400, f"Formato {extensao or 'desconhecido'} não aceito. "
                 f"Use: {', '.join(permitidas)}.")

    conteudo = await arquivo.read()
    if len(conteudo) > limite:
        raise HTTPException(
            400, f"Arquivo maior que o limite de {limite // (1024*1024)} MB.")
    if not conteudo:
        raise HTTPException(400, "O arquivo chegou vazio.")

    seguro = _nome_seguro(nome)
    destino = _pasta_anexos() / f"{especie}-{referencia_id}-{tipo}-{seguro}"
    destino.write_bytes(conteudo)

    anexo = Anexo(especie=especie, referencia_id=referencia_id, tipo=tipo,
                  nome_original=nome, caminho=str(destino),
                  tamanho=len(conteudo), enviado_por=usuario)
    s.add(anexo)
    return anexo


_RE_INSEGURO = re.compile(r"[^A-Za-z0-9._-]+")


def _nome_seguro(nome: str) -> str:
    """Nome de arquivo sem acento, espaço nem caminho.

    O nome vem do navegador do usuário: tratar como texto confiável
    seria deixar alguém escolher onde o arquivo é gravado.
    """
    base = Path(nome).name
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = _RE_INSEGURO.sub("_", base).strip("._-")
    return (base or "anexo")[:120]


# ══════════════════════════════════════════════════════════════════
#  A10 — Lotes
# ══════════════════════════════════════════════════════════════════

@router.get("/lotes")
def api_lotes(req: Request, estado: str = ""):
    require_permission(req, "destinacao", "view")
    cal, agora = _calendario(), dbt.utcnow()
    with SessionLocal() as s:
        q = select(Lote).order_by(Lote.aberto_em.desc())
        if estado:
            q = q.where(Lote.estado == estado)
        lotes = s.execute(q.limit(200)).scalars().all()
        contagem = {
            l.id: s.execute(
                select(func.count(LoteItem.id)).where(LoteItem.lote_id == l.id)
            ).scalar_one() for l in lotes
        }
    return {"lotes": [{
        "numero": l.numero, "destino": l.destino,
        "destino_rotulo": ROTULO_DESTINO.get(l.destino, l.destino),
        "estado": l.estado, "estado_rotulo": ROTULO_ESTADO.get(l.estado, l.estado),
        "itens": contagem.get(l.id, 0),
        "aberto_em": _utc(l.aberto_em).isoformat(),
        "aberto_por": l.aberto_por,
        "segundos": duracao_util(_utc(l.aberto_em),
                                 _utc(l.concluido_em), cal, agora),
    } for l in lotes]}


class LoteIn(BaseModel):
    destino: str
    justificativa: str = ""
    seriais: list[str] = []


@router.post("/lotes")
def api_lote_criar(body: LoteIn, req: Request):
    """Forma o lote e move os ativos para a tratativa do destino."""
    sd = require_permission(req, "destinacao", "edit")
    if body.destino not in DESTINOS:
        raise HTTPException(400, "Destino inválido.")
    seriais = [x.strip().upper() for x in body.seriais if x.strip()]
    if not seriais:
        raise HTTPException(400, "Inclua ao menos um equipamento no lote.")
    if not (body.justificativa or "").strip():
        raise HTTPException(
            400, "Justifique o destino: é o que responde 'por que vendemos "
                 "em vez de doar' seis meses depois.")

    usuario = sd.get("username", "")
    estado_alvo = f"AG_{body.destino}"

    # Todos os ativos precisam estar prontos antes de o lote existir. Um
    # lote com metade dos itens no estado errado é pior do que nenhum.
    with dbt.SessionLocal() as s:
        ativos = {}
        for serial in seriais:
            a = s.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one_or_none()
            if a is None:
                raise HTTPException(404, f"A série {serial} não está na trilha.")
            if a.estado_fisico != AG_DEFINICAO_DESTINO:
                raise HTTPException(
                    409, f"A série {serial} não está aguardando destino — "
                         "descaracterize antes.")
            ativos[serial] = a

    with SessionLocal() as s:
        lote = Lote(numero=db.proximo_numero(s), destino=body.destino,
                    estado=AG_TRATATIVA,
                    justificativa=body.justificativa.strip(),
                    aberto_por=usuario)
        s.add(lote)
        s.flush()
        condicoes = {
            d.serial: d.condicao_fisica for d in s.execute(
                select(Descaracterizacao).where(Descaracterizacao.serial.in_(seriais))
            ).scalars()
        }
        for serial in seriais:
            s.add(LoteItem(lote_id=lote.id, serial=serial,
                           modelo=ativos[serial].modelo,
                           condicao_fisica=condicoes.get(serial, "")))
        s.commit()
        numero = lote.numero

    with dbt.SessionLocal() as s:
        for serial in seriais:
            ativo = s.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one()
            mover(s, ativo, estado=estado_alvo, tipo=dbt.FILA,
                  processo=PROCESSO_DO_DESTINO[body.destino], usuario=usuario,
                  detalhe=f'{{"lote": "{numero}"}}')
        s.commit()

    _log.info("destinacao: %s formou o lote %s (%s, %d itens)",
              usuario, numero, body.destino, len(seriais))
    return api_lote_detalhe(numero, req)


@router.get("/lotes/{numero}")
def api_lote_detalhe(numero: str, req: Request):
    require_permission(req, "destinacao", "view")
    with SessionLocal() as s:
        lote = _buscar_lote(s, numero)
        itens = s.execute(
            select(LoteItem).where(LoteItem.lote_id == lote.id)
        ).scalars().all()
        anexos = s.execute(
            select(Anexo).where(Anexo.especie == "lote",
                                Anexo.referencia_id == lote.id)
        ).scalars().all()
    return {
        "numero": lote.numero, "destino": lote.destino,
        "destino_rotulo": ROTULO_DESTINO.get(lote.destino, lote.destino),
        "estado": lote.estado,
        "estado_rotulo": ROTULO_ESTADO.get(lote.estado, lote.estado),
        "justificativa": lote.justificativa,
        "comprador": lote.comprador, "nf_venda": lote.nf_venda,
        "valor_estimado": float(lote.valor_estimado or 0) or None,
        "valor_realizado": float(lote.valor_realizado or 0) or None,
        "fornecedor": lote.fornecedor, "tipo_residuo": lote.tipo_residuo,
        "peso_kg": float(lote.peso_kg or 0) or None,
        "entidade": lote.entidade, "recebedor": lote.recebedor,
        "aberto_por": lote.aberto_por,
        "aberto_em": _utc(lote.aberto_em).isoformat(),
        "concluido_em": _utc(lote.concluido_em).isoformat() if lote.concluido_em else None,
        "documento_exigido": DOCUMENTO_EXIGIDO.get(lote.destino, ("", ""))[1],
        "itens": [{"serial": i.serial, "modelo": i.modelo,
                   "condicao_fisica": i.condicao_fisica} for i in itens],
        "anexos": [{"id": a.id, "tipo": a.tipo, "nome": a.nome_original,
                    "enviado_por": a.enviado_por} for a in anexos],
    }


def _buscar_lote(s, numero: str) -> Lote:
    lote = s.execute(
        select(Lote).where(Lote.numero == (numero or "").strip().upper())
    ).scalar_one_or_none()
    if lote is None:
        raise HTTPException(404, f"Lote {numero} não encontrado.")
    return lote


@router.post("/lotes/{numero}/anexos")
async def api_lote_anexo(numero: str, req: Request,
                         tipo: str = Form(...),
                         arquivo: UploadFile = File(...)):
    sd = require_permission(req, "destinacao", "edit")
    tipo = (tipo or "").strip().lower()
    if tipo not in TIPOS_ANEXO:
        raise HTTPException(400, "Tipo de documento inválido.")
    with SessionLocal() as s:
        lote = _buscar_lote(s, numero)
        await _guardar_anexo(s, arquivo, "lote", lote.id, tipo,
                             sd.get("username", ""))
        s.commit()
    return api_lote_detalhe(numero, req)


@router.get("/anexos/{anexo_id}")
def api_anexo_baixar(anexo_id: int, req: Request):
    require_permission(req, "destinacao", "view")
    with SessionLocal() as s:
        anexo = s.get(Anexo, anexo_id)
    if anexo is None:
        raise HTTPException(404, "Anexo não encontrado.")
    caminho = Path(anexo.caminho)
    # O caminho vem do banco, mas confirmar que ele está dentro da pasta
    # de anexos custa uma linha e fecha a porta para um registro adulterado.
    if not caminho.is_file() or _pasta_anexos() not in caminho.parents:
        raise HTTPException(404, "Arquivo não está mais disponível.")
    return FileResponse(caminho, filename=anexo.nome_original)


class ConclusaoLoteIn(BaseModel):
    comprador: str = ""
    valor_estimado: float | None = None
    valor_realizado: float | None = None
    nf_venda: str = ""
    fornecedor: str = ""
    tipo_residuo: str = ""
    peso_kg: float | None = None
    entidade: str = ""
    recebedor: str = ""


@router.post("/lotes/{numero}/concluir")
def api_lote_concluir(numero: str, body: ConclusaoLoteIn, req: Request):
    """Fecha o lote. O documento comprobatório é condição, não formalidade."""
    sd = require_permission(req, "destinacao", "edit")
    usuario = sd.get("username", "")

    with SessionLocal() as s:
        lote = _buscar_lote(s, numero)
        if lote.estado == CONCLUIDO:
            raise HTTPException(409, "O lote já está concluído.")

        tipo_doc, nome_doc = DOCUMENTO_EXIGIDO.get(lote.destino, ("", ""))
        if tipo_doc:
            tem = s.execute(
                select(Anexo).where(Anexo.especie == "lote",
                                    Anexo.referencia_id == lote.id,
                                    Anexo.tipo == tipo_doc)
            ).scalars().first()
            if tem is None:
                raise HTTPException(
                    400, f"Anexe o {nome_doc} antes de concluir o lote.")

        _exigencias_do_destino(lote.destino, body)

        lote.comprador = (body.comprador or "").strip()
        lote.valor_estimado = body.valor_estimado
        lote.valor_realizado = body.valor_realizado
        lote.nf_venda = (body.nf_venda or "").strip()
        lote.fornecedor = (body.fornecedor or "").strip()
        lote.tipo_residuo = (body.tipo_residuo or "").strip()
        lote.peso_kg = body.peso_kg
        lote.entidade = (body.entidade or "").strip()
        lote.recebedor = (body.recebedor or "").strip()
        lote.estado = CONCLUIDO
        lote.concluido_por = usuario
        lote.concluido_em = db.utcnow()
        itens = s.execute(
            select(LoteItem).where(LoteItem.lote_id == lote.id)
        ).scalars().all()
        destino, seriais = lote.destino, [i.serial for i in itens]
        s.commit()

    # A trilha de cada ativo termina aqui: saiu da área para sempre.
    estado_final = {VENDA: "VENDIDO", DESCARTE: "DESCARTADO",
                    DOACAO: "DOADO"}[destino]
    with dbt.SessionLocal() as s:
        for serial in seriais:
            ativo = s.execute(
                select(dbt.Ativo).where(dbt.Ativo.serial == serial)
            ).scalar_one_or_none()
            if ativo is None or ativo.encerrado:
                continue
            encerrar(s, ativo, estado=estado_final,
                     processo=PROCESSO_DO_DESTINO[destino], usuario=usuario,
                     justificativa=f"Lote {numero.upper()}")
        s.commit()

    _log.info("destinacao: %s concluiu o lote %s (%s, %d itens)",
              usuario, numero.upper(), destino, len(seriais))
    return api_lote_detalhe(numero, req)


def _exigencias_do_destino(destino: str, body: ConclusaoLoteIn) -> None:
    if destino == VENDA:
        if not (body.comprador or "").strip():
            raise HTTPException(400, "Informe o comprador.")
        if body.valor_realizado is None:
            raise HTTPException(
                400, "Informe o valor realizado: sem ele não dá para comparar "
                     "com o potencial do lote.")
    elif destino == DESCARTE:
        if not (body.fornecedor or "").strip():
            raise HTTPException(400, "Informe o fornecedor do descarte.")
    elif destino == DOACAO:
        if not (body.entidade or "").strip():
            raise HTTPException(400, "Informe a entidade destinatária.")
        if not (body.recebedor or "").strip():
            raise HTTPException(
                400, "Informe quem recebeu: o termo precisa de um nome.")


@router.get("/config")
def api_config(req: Request):
    require_permission(req, "destinacao", "admin")
    return db.ler_config()


@router.put("/config")
def api_config_gravar(body: dict, req: Request):
    require_permission(req, "destinacao", "admin")
    pares = {k: str(v) for k, v in (body or {}).items() if k in db.PADROES}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    db.gravar_config(pares)
    return db.ler_config()
