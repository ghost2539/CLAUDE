"""Recebimento (receiving) router — scan, list, update, dashboard, lots."""
from __future__ import annotations

import logging

import csv
import io
import unicodedata
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func, or_

from config import get_settings
from db.portal import (
    SessionLocal, Asset, ReceiptCycle, Movement, LotSequence, Lot, Setting,
)
from core.security import get_session, require_permission, check_rate_limit
from routers.helpers import (
    classify, apply_class, find_asset, upsert_asset, asset_dict, cycle_dict,
    local_search_one,
)
from routers.consulta import _query_single, _query_assets, QueryIn

_cfg = get_settings()
CLOSED = _cfg.CLOSED_STATUSES
DUPLICATE_PREFIXES = ["CM", "YC"]
router = APIRouter(prefix="/api", tags=["Recebimento"])

# ── Destino de entrada: o Recebimento é a porta do Spare ───────────
# Só há dois caminhos a partir daqui. Venda direta sai do fluxo de
# reparo e vai esperar o ciclo trimestral; triagem entra no backlog de
# uma das bancadas, escolhida pela subcategoria do ativo.
VENDA_DIRETA = "VENDA"
TRIAGEM = "TRIAGEM"
DESTINOS_ENTRADA = (VENDA_DIRETA, TRIAGEM)


# ── Pydantic models ───────────────────────────────────────────────

class ScanIn(BaseModel):
    identificador: str

    @field_validator("identificador")
    @classmethod
    def strip_ident(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Identificador obrigatório.")
        return v


class ReceiptUpdateIn(BaseModel):
    status: str | None = None
    local_id: int | None = None
    lote: str | None = None
    note: str | None = None


class AssetUpdateIn(BaseModel):
    categoria: str | None = None
    modelo: str | None = None
    empresa: str | None = None
    numero_serie: str | None = None
    imobilizado: str | None = None


class BulkSubmitItem(BaseModel):
    empresa: str = ""
    asset_id: str = ""
    ativo: str = ""
    etiqueta: str = ""
    numero_serie: str = ""
    descricao: str = ""
    categoria: str = "NÃO CLASSIFICADA"
    modelo: str = ""
    custo_asset: float | None = None
    dpis: str | None = None
    fonte: str = "EBS"
    # O Recebimento é a porta de entrada e define o próximo destino:
    # venda direta, ou triagem para uma das bancadas. Em triagem, a
    # subcategoria é obrigatória — é ela que diz qual bancada recebe.
    destino_entrada: str = TRIAGEM
    subcategoria: str = ""


class BulkSubmitIn(BaseModel):
    items: list[BulkSubmitItem]
    # Onde o lote foi guardado. Obrigatório quando o espelho no ServiceNow
    # está ligado: "em estoque" sem dizer onde não fecha inventário depois.
    espaco_corredor: str = ""


class LotCreateIn(BaseModel):
    prefixo: str
    ids: list[int] = []

    @field_validator("prefixo")
    @classmethod
    def validate_prefix(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ("VENDA", "TRIAGEM"):
            raise ValueError("Prefixo inválido. Use VENDA ou TRIAGEM.")
        return v


class LotSeqUpdateIn(BaseModel):
    next_number: int


# ── Endpoints ─────────────────────────────────────────────────────

@router.post("/recebimento/scan")
def receipt_scan(body: ScanIn, req: Request):
    sd = require_permission(req, "recebimento", "create")
    check_rate_limit(req)

    result = _query_single(body.identificador, req)

    with SessionLocal.begin() as s:
        a = upsert_asset(s, result)

        current = s.scalar(
            select(ReceiptCycle)
            .where(ReceiptCycle.asset_id == a.id, ReceiptCycle.open == True)  # noqa: E712
            .order_by(ReceiptCycle.id.desc())
        )
        if current:
            return {
                "warning": "Ativo já possui recebimento em aberto.",
                "existing": True,
                **cycle_dict(current),
            }

        n = (
            s.scalar(
                select(func.max(ReceiptCycle.cycle_number))
                .where(ReceiptCycle.asset_id == a.id)
            ) or 0
        ) + 1

        today = date.today()
        iso = today.isocalendar()
        c = ReceiptCycle(
            asset_id=a.id,
            cycle_number=n,
            received_date=today,
            iso_week=f"{iso.year}-S{iso.week:02d}",
            status="RECEBIDO",
            created_by=sd["username"],
            updated_by=sd["username"],
        )
        s.add(c)
        s.flush()

        s.add(Movement(
            asset_id=a.id,
            cycle_id=c.id,
            new_status="RECEBIDO",
            origin="RECEBIMENTO",
            username=sd["username"],
        ))

        return {
            "id": c.id,
            "status": "RECEBIDO",
            "hora": datetime.now().strftime("%H:%M"),
            **asset_dict(a),
        }


def _check_local_duplicate(s, etiqueta: str, numero_serie: str) -> dict | None:
    """Check if an asset with the same tag_number or serial_number already
    exists in the local database. Returns a summary dict or None."""
    ors = []
    if etiqueta.strip():
        ors.append(func.upper(Asset.tag_number) == etiqueta.strip().upper())
    if numero_serie.strip():
        ors.append(func.upper(Asset.serial_number) == numero_serie.strip().upper())
    if not ors:
        return None
    existing = s.scalar(select(Asset).where(or_(*ors)).limit(1))
    if not existing:
        return None
    last_cycle = s.scalar(
        select(ReceiptCycle)
        .where(ReceiptCycle.asset_id == existing.id)
        .order_by(ReceiptCycle.id.desc())
    )
    return {
        "asset_db_id": existing.id,
        "empresa": existing.company,
        "etiqueta": existing.tag_number,
        "numero_serie": existing.serial_number,
        "categoria": existing.category,
        "modelo": existing.model,
        "imobilizado": existing.asset_id or existing.asset_number,
        "ultimo_status": last_cycle.status if last_cycle else None,
        "data_recebimento": str(last_cycle.received_date) if last_cycle else None,
        "ciclo_aberto": last_cycle.open if last_cycle else False,
    }


@router.post("/recebimento/check-duplicate")
def check_duplicate(body: ScanIn, req: Request):
    """Check if an asset already exists in the local database by tag or serial."""
    require_permission(req, "recebimento", "create")
    ident = body.identificador.strip()
    with SessionLocal() as s:
        dup = _check_local_duplicate(s, ident, ident)
        return {"duplicado": dup is not None, "existente": dup}


@router.post("/recebimento/preview")
def receipt_preview(body: ScanIn, req: Request):
    """Query EBS for an asset WITHOUT saving. Returns all matches including
    duplicates across companies (CM/YC prefix variants)."""
    sd = require_permission(req, "recebimento", "create")
    check_rate_limit(req)

    ident = body.identificador.strip()
    search_terms = [ident]
    bare = ident
    detected_prefix = ""
    prefixos = config_familias()["prefixos_duplicidade"]
    for pfx in prefixos:
        if ident.upper().startswith(pfx) and len(ident) > len(pfx):
            bare = ident[len(pfx):]
            detected_prefix = pfx
            break
    if bare != ident:
        search_terms.append(bare)
    for pfx in prefixos:
        variant = pfx + bare
        if variant.upper() != ident.upper() and variant not in search_terms:
            search_terms.append(variant)

    try:
        result = _query_assets(QueryIn(identificadores=search_terms), req)
    except Exception:
        result = {"resultados": []}

    matches: list[dict[str, Any]] = []
    local_dup: dict | None = None
    with SessionLocal() as s:
        for r in result.get("resultados", []):
            if not r.get("encontrado"):
                continue
            r = apply_class(s, r)
            cat = r.get("categoria", "NÃO CLASSIFICADA")
            classified = cat != "NÃO CLASSIFICADA"
            r["situacao"] = "PRONTO PARA ENVIO" if classified else "EDITADO"
            matches.append(r)

        if not matches:
            lr = local_search_one(s, ident)
            if lr.get("encontrado"):
                lr = apply_class(s, lr)
                cat = lr.get("categoria", "NÃO CLASSIFICADA")
                lr["situacao"] = "PRONTO PARA ENVIO" if cat != "NÃO CLASSIFICADA" else "EDITADO"
                matches.append(lr)

        if matches:
            m0 = matches[0]
            local_dup = _check_local_duplicate(
                s,
                m0.get("etiqueta", ""),
                m0.get("numero_serie", ""),
            )

    has_duplicates = len(matches) > 1
    if has_duplicates:
        for m in matches:
            m["situacao"] = "REQUER ATUAÇÃO"

    return {
        "pesquisado": ident,
        "encontrado": len(matches) > 0,
        "duplicatas": has_duplicates,
        "resultados": matches,
        "duplicado_local": local_dup,
    }


@router.post("/recebimento/bulk-submit")
def receipt_bulk_submit(body: BulkSubmitIn, req: Request):
    """Save validated assets from the temporary session to the database."""
    sd = require_permission(req, "recebimento", "create")
    check_rate_limit(req)
    # O espelho no ServiceNow escreve estoque E local. Sem o local, gravaria
    # "em estoque" sem dizer onde, e a conferência não fecharia depois.
    # Recusa-se ANTES de gravar, para não deixar o recebimento pela metade.
    if _config_servicenow().get("ativo", True) and not (body.espaco_corredor or "").strip():
        raise HTTPException(400, "Informe o Espaço e Corredor onde os ativos "
                                 "ficaram guardados antes de confirmar o recebimento.")

    if not body.items:
        raise HTTPException(400, "Nenhum ativo para enviar.")

    # Destino de entrada de cada ativo, decidido aqui e não adivinhado
    # depois. Em triagem, a subcategoria tem de estar na lista: é ela que
    # diz qual bancada recebe o equipamento.
    rota: dict[int, tuple[str, str, str]] = {}   # índice → (destino, sub, família)
    for pos, item in enumerate(body.items):
        destino = (item.destino_entrada or TRIAGEM).strip().upper()
        if destino not in DESTINOS_ENTRADA:
            raise HTTPException(400, "Destino de entrada inválido: informe "
                                     "venda direta ou triagem.")
        ident = item.etiqueta or item.numero_serie or item.ativo or "ativo"
        familia = ""
        if destino == TRIAGEM:
            if not (item.subcategoria or "").strip():
                raise HTTPException(
                    400, f"{ident}: informe a subcategoria para mandar à triagem.")
            familia = familia_da_subcategoria(item.subcategoria)
            if not familia:
                raise HTTPException(
                    400, f"{ident}: subcategoria \"{item.subcategoria}\" não está "
                         "cadastrada. Ajuste em Configuração → Configuração "
                         "Módulos → Recebimento.")
        rota[pos] = (destino, (item.subcategoria or "").strip(), familia)

    created = 0
    skipped = 0
    errors: list[str] = []
    entrando: list[dict] = []

    with SessionLocal.begin() as s:
        for pos, item in enumerate(body.items):
            destino_entrada, subcategoria, familia = rota[pos]
            payload: dict[str, Any] = {
                "empresa": item.empresa,
                "asset_id": item.asset_id,
                "ativo": item.ativo,
                "etiqueta": item.etiqueta,
                "numero_serie": item.numero_serie,
                "descricao": item.descricao,
                "categoria": item.categoria or "NÃO CLASSIFICADA",
                "modelo": item.modelo,
                "fonte": item.fonte or "EBS",
            }
            if item.custo_asset is not None:
                payload["custo_asset"] = item.custo_asset
            if item.dpis:
                payload["dpis"] = item.dpis

            try:
                a = upsert_asset(s, payload)

                current = s.scalar(
                    select(ReceiptCycle)
                    .where(
                        ReceiptCycle.asset_id == a.id,
                        ReceiptCycle.open == True,  # noqa: E712
                    )
                    .order_by(ReceiptCycle.id.desc())
                )
                if current:
                    skipped += 1
                    continue

                n = (
                    s.scalar(
                        select(func.max(ReceiptCycle.cycle_number))
                        .where(ReceiptCycle.asset_id == a.id)
                    ) or 0
                ) + 1

                today = date.today()
                iso = today.isocalendar()
                c = ReceiptCycle(
                    asset_id=a.id,
                    cycle_number=n,
                    received_date=today,
                    iso_week=f"{iso.year}-S{iso.week:02d}",
                    status="RECEBIDO",
                    created_by=sd["username"],
                    updated_by=sd["username"],
                )
                s.add(c)
                s.flush()

                s.add(Movement(
                    asset_id=a.id,
                    cycle_id=c.id,
                    new_status="RECEBIDO",
                    origin="RECEBIMENTO",
                    username=sd["username"],
                ))
                created += 1
                entrando.append({
                    "serial": item.numero_serie or item.etiqueta or item.ativo,
                    "etiqueta": item.etiqueta or "",
                    "modelo": item.modelo,
                    "categoria": item.categoria or "",
                    "numero_ativo": item.ativo or "",
                    "destino_entrada": destino_entrada,
                    "subcategoria": subcategoria,
                    "familia": familia,
                })
            except Exception as e:
                ident = item.etiqueta or item.ativo or item.numero_serie
                errors.append(f"{ident}: {e}")

    # Entrada na Trilha do Ativo (A01). Aditivo e tolerante a falha: o
    # recebimento já foi gravado, e não se desfaz um recebimento porque
    # o núcleo de medição não respondeu.
    na_trilha = _entrar_na_trilha(entrando, sd["username"])

    # Espelho no ServiceNow: o que chegou fisicamente está no CD, e lá
    # precisa aparecer em estoque. Também aditivo — recebimento gravado
    # não se desfaz porque o ServiceNow recusou.
    no_servicenow = _marcar_no_servicenow(entrando, req, body.espaco_corredor)

    # Logística reversa (A17): série que bate com uma coleta em aberto
    # entra como recebida nela — a caixa está no CD, quem confirmou foi
    # quem recebeu. Aditivo e tolerante a falha, como os outros ganchos.
    na_coleta = _casar_com_coleta(entrando, sd["username"])

    # Coletor que chegou ao CD sai do MDM: ele voltou para o estoque e não
    # está mais com a loja. Só o que passou por aqui — a base do MDM não é
    # tocada. Aditivo: recebimento gravado não se desfaz por causa do MDM.
    do_mdm = _remover_do_mdm(entrando, sd["username"])
    # O ciclo guarda o desfecho: o recebimento foi o gatilho da remoção.
    # Movimento de origem MDM — rastro, não item de tela.
    _registrar_mdm_no_ciclo(entrando, do_mdm, sd["username"])

    return {
        "ok": True,
        "criados": created,
        "ignorados": skipped,
        "erros": errors,
        "na_trilha": na_trilha,
        "no_servicenow": no_servicenow,
        "na_coleta": na_coleta,
        "mdm": do_mdm,
    }


# ── Ponte com a Trilha do Ativo (A01) ───────────────────────────────

# Para onde cada família vai depois do recebimento. A bancada da frota
# e a de loja compartilham o estado de fila; quem separa as duas é o
# tipo do equipamento, lido na própria fila.
_FILA_POR_FAMILIA = {
    "frota": "AG_TRIAGEM",
    "loja": "AG_TRIAGEM",
    "conectividade": "AG_TRIAGEM_CONECT",
}

# ── Subcategorias: é a subcategoria que escolhe a bancada ──────────
# Lista fechada de propósito. Se o operador pudesse digitar, "PDV",
# "P.D.V." e "pdv" virariam três subcategorias e o backlog da bancada
# passaria a depender de grafia. A área mantém a lista em Configuração
# → Configuração Módulos → Recebimento.
CONFIG_SUBCATEGORIAS = "recebimento_subcategorias"

_SUBCATEGORIAS_PADRAO = [
    {"nome": "Coletor", "familia": "frota"},
    {"nome": "Sled", "familia": "frota"},
    {"nome": "Impressora portátil", "familia": "frota"},
    {"nome": "PDV", "familia": "loja"},
    {"nome": "Impressora fiscal", "familia": "loja"},
    {"nome": "Impressora de etiqueta", "familia": "loja"},
    {"nome": "Desktop", "familia": "loja"},
    {"nome": "Monitor", "familia": "loja"},
    {"nome": "Leitor", "familia": "loja"},
    {"nome": "Balança", "familia": "loja"},
    {"nome": "Periférico", "familia": "loja"},
    {"nome": "Access point", "familia": "conectividade"},
    {"nome": "Switch", "familia": "conectividade"},
    {"nome": "Roteador", "familia": "conectividade"},
    {"nome": "Firewall", "familia": "conectividade"},
]


def config_subcategorias() -> list[dict]:
    """Subcategorias válidas e a família (bancada) de cada uma."""
    with SessionLocal() as s:
        x = s.get(Setting, CONFIG_SUBCATEGORIAS)
        valor = (x.value or {}) if x else {}
    linhas = valor.get("subcategorias") if isinstance(valor, dict) else None
    if not isinstance(linhas, list) or not linhas:
        return [dict(l) for l in _SUBCATEGORIAS_PADRAO]
    limpas = []
    for l in linhas:
        if not isinstance(l, dict):
            continue
        nome = str(l.get("nome") or "").strip()
        familia = str(l.get("familia") or "").strip().lower()
        if nome and familia in _FILA_POR_FAMILIA:
            limpas.append({"nome": nome, "familia": familia})
    return limpas or [dict(l) for l in _SUBCATEGORIAS_PADRAO]


def _chave_sub(nome: str) -> str:
    """Compara subcategoria sem depender de acento, caixa ou espaço."""
    texto = unicodedata.normalize("NFKD", str(nome or "").strip().lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def familia_da_subcategoria(nome: str) -> str:
    """Família (bancada) da subcategoria, ou "" se não estiver na lista."""
    alvo = _chave_sub(nome)
    if not alvo:
        return ""
    for l in config_subcategorias():
        if _chave_sub(l["nome"]) == alvo:
            return l["familia"]
    return ""


@router.get("/recebimento/subcategorias")
def api_subcategorias(req: Request):
    require_permission(req, "recebimento", "view")
    return {"subcategorias": config_subcategorias(),
            "destinos": [{"valor": VENDA_DIRETA, "rotulo": "Venda direta"},
                         {"valor": TRIAGEM, "rotulo": "Triagem"}]}

_PALAVRAS_FROTA = ("coletor", "sled", "mc33", "tc2", "ef50", "rfr")
_PALAVRAS_CONECT = ("access point", "acess point", " ap ", "switch",
                    "roteador", "router", "wifi", "wi-fi")

# As palavras acima são só o padrão. O que vale é a configuração
# (Configuração → Configuração Módulos → Recebimento), lida a cada
# chamada: modelo novo entra sem release.
CONFIG_FAMILIAS = "recebimento_familias"


def config_familias() -> dict:
    with SessionLocal() as s:
        x = s.get(Setting, CONFIG_FAMILIAS)
        cfg = dict(x.value or {}) if x else {}
    def lista(chave, padrao):
        v = cfg.get(chave)
        return [str(p).strip().lower() for p in v if str(p).strip()] if isinstance(v, list) and v else list(padrao)
    return {"frota": lista("frota", _PALAVRAS_FROTA),
            "conectividade": lista("conectividade", _PALAVRAS_CONECT),
            "prefixos_duplicidade": [p.upper() for p in lista("prefixos_duplicidade", DUPLICATE_PREFIXES)]}


def _familia(modelo: str, categoria: str) -> str:
    """Frota, loja ou conectividade, pelo modelo e pela categoria.

    Heurística de texto porque nem o EBS nem o ServiceNow têm um campo
    que separe as três famílias do jeito que a área trabalha. Na dúvida
    cai em loja, que é a bancada com mais gente — errar para lá é mais
    fácil de perceber e de corrigir.
    """
    texto = f" {modelo} {categoria} ".lower()
    cfg = config_familias()
    if any(p in texto for p in cfg["conectividade"]):
        return "conectividade"
    if any(p in texto for p in cfg["frota"]):
        return "frota"
    return "loja"


def _detalhe_entrada(it: dict, reentrada: bool = False) -> str:
    """O que o Recebimento decidiu, gravado junto da movimentação."""
    import json as _json
    dados = {"destino_entrada": (it.get("destino_entrada") or TRIAGEM).upper()}
    if it.get("subcategoria"):
        dados["subcategoria"] = it["subcategoria"]
    if reentrada:
        dados["reentrada"] = True
    return _json.dumps(dados, ensure_ascii=False)


def _estado_de_entrada(it: dict) -> tuple[str, str]:
    """Onde o ativo entra na trilha, e com que família (bancada).

    Venda direta pula o reparo e vai esperar o ciclo de venda. Triagem
    cai no backlog da bancada que a subcategoria indicou; sem
    subcategoria (recebimento antigo, importação), a família ainda é
    adivinhada pelo modelo — melhor um palpite do que ativo sem fila.
    """
    if (it.get("destino_entrada") or "").upper() == VENDA_DIRETA:
        return "AG_VENDA", ""
    familia = (it.get("familia") or "").strip().lower()
    if familia not in _FILA_POR_FAMILIA:
        familia = _familia(it.get("modelo", ""), it.get("categoria", ""))
    return _FILA_POR_FAMILIA[familia], familia


def _entrar_na_trilha(itens: list[dict], usuario: str) -> dict:
    """Cria o token de cada ativo recebido e o põe onde o destino mandou."""
    if not itens:
        return {"criados": 0, "ja_existiam": 0, "falhas": 0}
    resumo = {"criados": 0, "reentradas": 0, "ja_existiam": 0, "falhas": 0}
    try:
        import db.trilha as dbt
        from routers.trilha import abrir_ativo, mover, reabrir_ativo, TrilhaInvalida
        from sqlalchemy import select as _select
    except Exception as exc:  # noqa: BLE001 — trilha fora do ar
        logging.getLogger("recebimento").error(
            "Trilha indisponível; %d ativo(s) recebidos sem medição: %s",
            len(itens), exc)
        return {"criados": 0, "ja_existiam": 0, "falhas": len(itens)}

    for it in itens:
        serial = (it.get("serial") or "").strip()
        if not serial:
            resumo["falhas"] += 1
            continue
        estado_entrada, familia = _estado_de_entrada(it)
        detalhe = _detalhe_entrada(it)
        try:
            with dbt.SessionLocal() as s:
                ativo = abrir_ativo(
                    s, serial=serial, usuario=usuario,
                    modelo=it.get("modelo", ""),
                    numero_ativo=it.get("numero_ativo", ""),
                    tipo_equipamento=familia, origem="RECEBIMENTO")
                mover(s, ativo, estado=estado_entrada,
                      tipo=dbt.FILA, processo="A01", usuario=usuario,
                      detalhe=detalhe)
                s.commit()
            resumo["criados"] += 1
        except TrilhaInvalida:
            # Serial repetido. Se o ciclo anterior já encerrou (saiu para a
            # loja e voltou), é reentrada: reabre e mede o segundo ciclo.
            # Se ainda está em curso, é leitura duplicada — não mexe.
            try:
                with dbt.SessionLocal() as s:
                    ativo = s.execute(_select(dbt.Ativo).where(
                        dbt.Ativo.serial == serial.upper())).scalar_one_or_none()
                    if ativo is not None and ativo.encerrado:
                        reabrir_ativo(s, ativo, usuario=usuario, origem="RECEBIMENTO")
                        mover(s, ativo, estado=estado_entrada,
                              tipo=dbt.FILA, processo="A01", usuario=usuario,
                              detalhe=_detalhe_entrada(it, reentrada=True))
                        s.commit()
                        resumo["reentradas"] += 1
                    else:
                        resumo["ja_existiam"] += 1
            except Exception as exc:  # noqa: BLE001
                resumo["falhas"] += 1
                logging.getLogger("recebimento").error(
                    "Trilha: reentrada da série %s não registrada: %s", serial, exc)
        except Exception as exc:  # noqa: BLE001
            resumo["falhas"] += 1
            logging.getLogger("recebimento").error(
                "Trilha: série %s recebida mas não entrou na fila: %s",
                serial, exc)
    return resumo


# ── Ponte com o ServiceNow (A01) ────────────────────────────────────

# O recebimento não cria ativo no ServiceNow: quem cria é a Entrada de
# Estoque, que tem os campos fiscais e de modelo. Aqui só se corrige a
# situação de quem já está lá — em estoque, no CD.
CONFIG_SN = "recebimento_servicenow"


def _config_servicenow() -> dict:
    with SessionLocal() as s:
        x = s.get(Setting, CONFIG_SN)
        cfg = dict(x.value or {}) if x else {}
    cfg.setdefault("ativo", True)
    cfg.setdefault("stockroom", "")
    cfg.setdefault("install_status", "")
    # Ativo recebido que o ServiceNow não conhece é criado no ato. Sem isto
    # ele fica invisível até alguém lembrar da Entrada de Estoque.
    cfg.setdefault("criar_ausentes", True)
    return cfg


def _marcar_no_servicenow(itens: list[dict], req: Request, espaco: str = "") -> dict:
    """Espelha o recebimento no ServiceNow: cria o que falta, atualiza o resto."""
    resumo = {"ativo": False, "encontrados": 0, "atualizados": 0, "criados": 0,
              "nao_encontrados": 0, "falhas": []}
    if not itens:
        return resumo
    cfg = _config_servicenow()
    if not cfg.get("ativo", True):
        return resumo
    resumo["ativo"] = True
    try:
        from routers.servicenow import (
            marcar_recebidos_em_estoque, _sn_session_from_portal,
        )
        # A escrita sai no nome de quem recebeu, como manda a norma.
        session = _sn_session_from_portal(req)
        r = marcar_recebidos_em_estoque(
            session, itens,
            stockroom=cfg.get("stockroom", ""),
            install_status=cfg.get("install_status", ""),
            aisle_space=espaco,
            criar=bool(cfg.get("criar_ausentes", True)))
        resumo.update(r)
    except Exception as exc:  # noqa: BLE001 — ServiceNow fora do ar
        resumo["falhas"] = [str(exc)]
        logging.getLogger("recebimento").error(
            "ServiceNow: %d ativo(s) recebidos sem marcação de estoque: %s",
            len(itens), exc)
    return resumo


def _remover_do_mdm(itens: list[dict], usuario: str) -> dict:
    """Tira do MDM os coletores recebidos. Módulo ausente não trava nada."""
    if not itens:
        return {"tentados": 0, "removidos": 0, "pendentes": 0}
    try:
        from routers.obsolescencia import remover_recebidos_do_mdm
        return remover_recebidos_do_mdm(itens, usuario)
    except Exception as exc:  # noqa: BLE001 — módulo fora do ar
        logging.getLogger("recebimento").warning(
            "MDM: %d recebido(s) sem remoção: %s", len(itens), exc)
        return {"tentados": 0, "removidos": 0, "pendentes": 0, "falhas": [str(exc)]}


def _registrar_mdm_no_ciclo(itens: list[dict], resultado: dict, usuario: str) -> int:
    """Guarda o desfecho da remoção no MDM no ciclo do ativo recebido.

    O livro das escritas no MDM continua na Obsolescência — quem fala com o
    console é quem registra lá. Isto aqui é o rastro do lado de cá: o
    recebimento foi o gatilho, então o ciclo guarda o que aconteceu. Fica
    como movimento de origem MDM, fora do que a tela de recebimento lista.
    """
    por_serie = (resultado or {}).get("por_serie") or {}
    if not por_serie:
        return 0
    gravados = 0
    try:
        with SessionLocal.begin() as s:
            for item in itens:
                chave = str(item.get("serial") or "").strip().upper()
                desfecho = por_serie.get(chave)
                if not desfecho:
                    continue
                a = s.scalar(
                    select(Asset).where(
                        or_(Asset.serial_number == item.get("serial"),
                            Asset.tag_number == item.get("etiqueta"))
                    ).order_by(Asset.id.desc())
                )
                if a is None:
                    continue
                ciclo = s.scalar(
                    select(ReceiptCycle)
                    .where(ReceiptCycle.asset_id == a.id)
                    .order_by(ReceiptCycle.id.desc())
                )
                if ciclo is None:
                    continue
                nota = ("Removido do MDM" if desfecho.get("ok")
                        else "Remoção do MDM pendente")
                detalhe = str(desfecho.get("detalhe") or "").strip()
                if detalhe and not desfecho.get("ok"):
                    nota = f"{nota}: {detalhe}"
                s.add(Movement(
                    asset_id=a.id,
                    cycle_id=ciclo.id,
                    origin="MDM",
                    note=nota[:400],
                    username=usuario,
                ))
                gravados += 1
    except Exception as exc:  # noqa: BLE001 — rastro não desfaz recebimento
        logging.getLogger("recebimento").warning(
            "MDM: desfecho não registrado no ciclo: %s", exc)
        return 0
    return gravados


def _casar_com_coleta(itens: list[dict], usuario: str) -> dict:
    """Avisa a Logística Reversa do que chegou. Sem ela no ar, segue."""
    if not itens:
        return {"casados": 0, "coletas": []}
    try:
        from routers.reversa import registrar_recebimento
        return registrar_recebimento(itens, usuario)
    except Exception as exc:  # noqa: BLE001 — módulo ausente ou fora do ar
        logging.getLogger("recebimento").warning(
            "Reversa: %d ativo(s) recebidos sem casar com coleta: %s", len(itens), exc)
        return {"casados": 0, "coletas": [], "falha": str(exc)}


@router.get("/recebimento/familias")
def api_familias(req: Request):
    require_permission(req, "recebimento", "view")
    return config_familias()


@router.delete("/recebimentos/{cycle_id}")
def delete_receipt(cycle_id: int, req: Request):
    """Remove a receipt cycle from the base (soft delete)."""
    sd = require_permission(req, "recebimento", "edit")
    check_rate_limit(req)

    with SessionLocal.begin() as s:
        c = s.get(ReceiptCycle, cycle_id)
        if not c:
            raise HTTPException(404, "Recebimento não encontrado.")

        old_status = c.status
        c.status = "REMOVIDO"
        c.open = False
        c.updated_by = sd["username"]

        s.add(Movement(
            asset_id=c.asset_id,
            cycle_id=c.id,
            old_status=old_status,
            new_status="REMOVIDO",
            origin="EXCLUSÃO",
            username=sd["username"],
        ))

    return {"ok": True}


@router.put("/recebimentos/{cycle_id}/asset")
def update_receipt_asset(cycle_id: int, body: AssetUpdateIn, req: Request):
    """Update the underlying asset fields of a receipt."""
    sd = require_permission(req, "recebimento", "edit")
    check_rate_limit(req)

    with SessionLocal.begin() as s:
        c = s.get(ReceiptCycle, cycle_id)
        if not c:
            raise HTTPException(404, "Recebimento não encontrado.")
        a = c.asset
        if body.categoria is not None:
            a.category = body.categoria
        if body.modelo is not None:
            a.model = body.modelo
        if body.empresa is not None:
            a.company = body.empresa
        if body.numero_serie is not None:
            a.serial_number = body.numero_serie
        if body.imobilizado is not None:
            a.asset_id = body.imobilizado
            a.asset_number = body.imobilizado
        c.updated_by = sd["username"]

    return {"ok": True}


@router.get("/recebimentos")
def list_receipts(
    req: Request,
    status: str = "",
    empresa: str = "",
    categoria: str = "",
    data_inicio: str = "",
    data_fim: str = "",
    q: str = "",
    limit: int = 1000,
):
    require_permission(req, "recebimento", "view")

    with SessionLocal() as s:
        stmt = (
            select(ReceiptCycle)
            .join(Asset)
            .where(ReceiptCycle.status != "REMOVIDO")
            .order_by(ReceiptCycle.id.desc())
            .limit(min(limit, 5000))
        )
        if status:
            stmt = stmt.where(ReceiptCycle.status == status)
        if empresa:
            stmt = stmt.where(func.upper(Asset.company).contains(empresa.upper()))
        if categoria:
            stmt = stmt.where(func.upper(Asset.category).contains(categoria.upper()))
        if data_inicio:
            stmt = stmt.where(ReceiptCycle.received_date >= date.fromisoformat(data_inicio))
        if data_fim:
            stmt = stmt.where(ReceiptCycle.received_date <= date.fromisoformat(data_fim))
        if q:
            term = q.upper()
            stmt = stmt.where(or_(
                func.upper(Asset.asset_id).contains(term),
                func.upper(Asset.asset_number).contains(term),
                func.upper(Asset.serial_number).contains(term),
                func.upper(Asset.tag_number).contains(term),
                func.upper(Asset.description).contains(term),
            ))

        rows = s.scalars(stmt).unique().all()
        return {"registros": [cycle_dict(x) for x in rows]}


@router.put("/recebimentos/{cycle_id}")
def update_receipt(cycle_id: int, body: ReceiptUpdateIn, req: Request):
    sd = require_permission(req, "recebimento", "edit")
    check_rate_limit(req)

    with SessionLocal.begin() as s:
        c = s.get(ReceiptCycle, cycle_id)
        if not c:
            raise HTTPException(404, "Recebimento não encontrado.")

        old_status = c.status
        old_loc = c.location.name if c.location else ""

        if body.status is not None:
            c.status = body.status.upper()
            c.open = c.status not in CLOSED
        if body.local_id is not None:
            c.location_id = body.local_id or None
        if body.lote is not None:
            c.lot_number = body.lote
        if body.note is not None:
            c.note = body.note

        c.updated_by = sd["username"]
        s.flush()

        new_loc = c.location.name if c.location else ""
        s.add(Movement(
            asset_id=c.asset_id,
            cycle_id=c.id,
            old_status=old_status,
            new_status=c.status,
            old_location=old_loc,
            new_location=new_loc,
            lot_number=c.lot_number,
            origin="EDIÇÃO",
            note=c.note,
            username=sd["username"],
        ))

    return {"ok": True}


@router.get("/recebimentos/dashboard")
def receipt_dashboard(
    req: Request,
    ano: int | None = None,
    mes: int | None = None,
    data_inicio: str = "",
    data_fim: str = "",
):
    require_permission(req, "recebimento", "view")
    ano = ano or date.today().year

    with SessionLocal() as s:
        base = select(ReceiptCycle).where(
            func.extract("year", ReceiptCycle.received_date) == ano
        )
        if mes:
            base = base.where(func.extract("month", ReceiptCycle.received_date) == mes)
        if data_inicio:
            base = base.where(ReceiptCycle.received_date >= date.fromisoformat(data_inicio))
        if data_fim:
            base = base.where(ReceiptCycle.received_date <= date.fromisoformat(data_fim))

        cycles = s.scalars(base).all()
        ids = [c.asset_id for c in cycles]

        def counter(vals):
            return [
                {"label": k or "N/D", "total": v}
                for k, v in sorted(Counter(vals).items(), key=lambda x: -x[1])
            ]

        return {
            "total": len(cycles),
            "unicos": len(set(ids)),
            "devolucoes": len(cycles) - len(set(ids)),
            "por_mes": [
                {"mes": m, "total": sum(1 for c in cycles if c.received_date.month == m)}
                for m in range(1, 13)
            ],
            "por_empresa": [
                {"empresa": x["label"], "total": x["total"]}
                for x in counter([c.asset.company for c in cycles])
            ],
            "por_categoria": [
                {"categoria": x["label"], "total": x["total"]}
                for x in counter([c.asset.category for c in cycles])
            ],
            "por_status": [
                {"status": x["label"], "total": x["total"]}
                for x in counter([c.status for c in cycles])
            ],
            "por_local": [
                {"local": x["label"], "total": x["total"]}
                for x in counter([
                    c.location.name if c.location else "N/D" for c in cycles
                ])
            ],
        }


@router.get("/recebimentos/export-servicenow")
def export_servicenow(req: Request):
    require_permission(req, "recebimento", "export")

    with SessionLocal() as db:
        cycles = db.scalars(
            select(ReceiptCycle).order_by(ReceiptCycle.id)
        ).all()

        output = io.StringIO()
        writer = csv.writer(
            output,
            delimiter=";",
            quotechar='"',
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        writer.writerow([
            "Serial Number", "Model", "Asset tag", "Model category",
            "Stockroom", "State", "Substate", "Acquisition method",
            "Aisle and space", "Company", "Cost", "Expenditure type",
            "Purchased", "Quantity", "Depreciation",
            "Depreciation effective date",
        ])
        for c in cycles:
            a = c.asset
            d = a.dpis.strftime("%d/%m/%Y") if a.dpis else ""
            writer.writerow([
                a.serial_number, a.model, a.tag_number, a.category,
                "SPARE - CD324", "In stock", "Available", "Purchase",
                "", a.company, str(a.cost or ""), "Capex",
                d, 1, "SL 5 Years", d,
            ])

        data = ("﻿" + output.getvalue()).encode("utf-8")
        from fastapi.responses import StreamingResponse
        return StreamingResponse(
            io.BytesIO(data),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="recebimentos_servicenow.csv"'},
        )


@router.post("/recebimentos/import-historico")
def import_historico(req: Request, file: UploadFile = File(...)):
    sd = require_permission(req, "recebimento", "admin")
    check_rate_limit(req)

    raw = file.file.read()
    suffix = Path(file.filename or "upload.csv").suffix.lower()

    try:
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(raw), sep=None, engine="python", dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(raw), dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Arquivo inválido: {e}")

    def norm(x):
        return "".join(
            c for c in unicodedata.normalize("NFD", str(x))
            if unicodedata.category(c) != "Mn"
        ).strip().lower()

    cmap = {norm(c): c for c in df.columns}
    aliases = {
        "empresa": ["empresa", "bu"],
        "categoria": ["categoria"],
        "modelo": ["modelo"],
        "serie": ["numero de serie", "serie", "serial number"],
        "imobilizado": ["imobilizado", "asset id", "ativo"],
        "etiqueta": ["etiqueta", "asset tag"],
        "status": ["status"],
        "data": ["data", "data recebimento"],
        "local": ["local", "armazenado em"],
        "lote": ["lote"],
        "descricao": ["descricao ebs", "descricao"],
    }

    def col(k):
        return next(
            (cmap.get(norm(a)) for a in aliases[k] if norm(a) in cmap),
            None,
        )

    m = {k: col(k) for k in aliases}
    imported = 0
    rejected = 0

    with SessionLocal.begin() as db:
        for _, row in df.iterrows():
            def val(k):
                return str(row[m[k]]).strip() if m[k] and pd.notna(row[m[k]]) else ""

            if not any(val(x) for x in ("serie", "imobilizado", "etiqueta")):
                rejected += 1
                continue

            d = pd.to_datetime(val("data"), dayfirst=True, errors="coerce")
            received = d.date() if pd.notna(d) else date.today()

            payload = {
                "empresa": val("empresa"),
                "asset_id": val("imobilizado"),
                "ativo": val("imobilizado"),
                "etiqueta": val("etiqueta"),
                "numero_serie": val("serie"),
                "descricao": val("descricao"),
                "categoria": val("categoria") or "NÃO CLASSIFICADA",
                "modelo": val("modelo") or val("descricao"),
                "fonte": "HISTÓRICO",
            }
            a = upsert_asset(db, payload)
            n = (
                db.scalar(
                    select(func.max(ReceiptCycle.cycle_number))
                    .where(ReceiptCycle.asset_id == a.id)
                ) or 0
            ) + 1

            status = (val("status") or "RECEBIDO").upper()
            iso = received.isocalendar()
            c = ReceiptCycle(
                asset_id=a.id,
                cycle_number=n,
                received_date=received,
                iso_week=f"{iso.year}-S{iso.week:02d}",
                status=status,
                lot_number=val("lote"),
                open=status not in CLOSED,
                created_by=sd["username"],
                updated_by=sd["username"],
            )
            db.add(c)
            db.flush()

            db.add(Movement(
                asset_id=a.id,
                cycle_id=c.id,
                new_status=status,
                lot_number=val("lote"),
                origin="IMPORTAÇÃO HISTÓRICA",
                username=sd["username"],
            ))
            imported += 1

    return {"ok": True, "importados": imported, "rejeitados": rejected}


# ── Lot endpoints ─────────────────────────────────────────────────

@router.post("/lotes")
def create_lot(body: LotCreateIn, req: Request):
    sd = require_permission(req, "recebimento", "edit")
    check_rate_limit(req)

    with SessionLocal.begin() as s:
        seq = s.execute(
            select(LotSequence)
            .where(LotSequence.prefix == body.prefixo)
            .with_for_update()
        ).scalar_one()

        number = f"{body.prefixo}_{seq.next_number}"
        seq.next_number += 1

        lot = Lot(
            number=number,
            prefix=body.prefixo,
            created_by=sd["username"],
        )
        s.add(lot)

        status = "VENDA" if body.prefixo == "VENDA" else "EM TRIAGEM"
        count = 0

        for i in body.ids:
            c = s.get(ReceiptCycle, i)
            if not c:
                continue
            old = c.status
            c.status = status
            c.lot_number = number
            c.updated_by = sd["username"]
            count += 1
            s.add(Movement(
                asset_id=c.asset_id,
                cycle_id=c.id,
                old_status=old,
                new_status=status,
                lot_number=number,
                origin="LOTE",
                username=sd["username"],
            ))

        return {"numero_lote": number, "quantidade": count}


@router.get("/lotes/sequencias")
def lot_sequences(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal() as s:
        rows = s.scalars(select(LotSequence).order_by(LotSequence.prefix)).all()
        return {
            "sequencias": [
                {
                    "prefixo": x.prefix,
                    "proximo_numero": x.next_number,
                    "ativo": x.active,
                }
                for x in rows
            ]
        }


@router.put("/lotes/sequencias/{prefix}")
def lot_sequence_update(prefix: str, body: LotSeqUpdateIn, req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(LotSequence, prefix.upper())
        if not x:
            x = LotSequence(prefix=prefix.upper())
            s.add(x)
        x.next_number = body.next_number
    return {"ok": True}
