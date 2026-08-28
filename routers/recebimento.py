"""Recebimento (receiving) router — scan, list, update, dashboard, lots."""
from __future__ import annotations

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
from database import (
    SessionLocal, Asset, ReceiptCycle, Movement, LotSequence, Lot,
)
from security import get_session, require_permission, check_rate_limit
from routers.helpers import (
    classify, apply_class, find_asset, upsert_asset, asset_dict, cycle_dict,
    local_search_one,
)
from routers.consulta import _query_single, _query_assets, QueryIn

_cfg = get_settings()
CLOSED = _cfg.CLOSED_STATUSES
DUPLICATE_PREFIXES = ["CM", "YC"]
router = APIRouter(prefix="/api", tags=["Recebimento"])


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


class BulkSubmitIn(BaseModel):
    items: list[BulkSubmitItem]


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
    for pfx in DUPLICATE_PREFIXES:
        if ident.upper().startswith(pfx) and len(ident) > len(pfx):
            bare = ident[len(pfx):]
            detected_prefix = pfx
            break
    if bare != ident:
        search_terms.append(bare)
    for pfx in DUPLICATE_PREFIXES:
        variant = pfx + bare
        if variant.upper() != ident.upper() and variant not in search_terms:
            search_terms.append(variant)

    try:
        result = _query_assets(QueryIn(identificadores=search_terms), req)
    except Exception:
        result = {"resultados": []}

    matches: list[dict[str, Any]] = []
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

    has_duplicates = len(matches) > 1
    if has_duplicates:
        for m in matches:
            m["situacao"] = "REQUER ATUAÇÃO"

    return {
        "pesquisado": ident,
        "encontrado": len(matches) > 0,
        "duplicatas": has_duplicates,
        "resultados": matches,
    }


@router.post("/recebimento/bulk-submit")
def receipt_bulk_submit(body: BulkSubmitIn, req: Request):
    """Save validated assets from the temporary session to the database."""
    sd = require_permission(req, "recebimento", "create")
    check_rate_limit(req)

    if not body.items:
        raise HTTPException(400, "Nenhum ativo para enviar.")

    created = 0
    skipped = 0
    errors: list[str] = []

    with SessionLocal.begin() as s:
        for item in body.items:
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
            except Exception as e:
                ident = item.etiqueta or item.ativo or item.numero_serie
                errors.append(f"{ident}: {e}")

    return {
        "ok": True,
        "criados": created,
        "ignorados": skipped,
        "erros": errors,
    }


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
