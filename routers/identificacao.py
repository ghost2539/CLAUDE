"""Identificação router — Gerar Lote, Zebra Livre, A4, Impressoras."""
from __future__ import annotations

import io
import socket
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text, func, or_

from database import SessionLocal, Asset, ReceiptCycle, Movement, Base, engine
from security import require_permission, check_rate_limit

router = APIRouter(prefix="/api/identificacao", tags=["Identificação"])


# ── Ensure tables ─────────────────────────────────────────────────

def _ensure_tables():
    from sqlalchemy import (
        Table, Column, BigInteger, String, Boolean, DateTime, Integer,
    )
    from database import utcnow
    meta = Base.metadata
    if "printers" not in meta.tables:
        Table(
            "printers", meta,
            Column("id", BigInteger, primary_key=True),
            Column("name", String(120)),
            Column("ip", String(60)),
            Column("port", Integer, default=9100),
            Column("type", String(30), default="zebra"),
            Column("active", Boolean, default=True),
            Column("created_at", DateTime(timezone=True), default=utcnow),
            extend_existing=True,
        )
    if "box_sequences" not in meta.tables:
        Table(
            "box_sequences", meta,
            Column("prefix", String(10), primary_key=True),
            Column("next_number", BigInteger, default=1000),
            extend_existing=True,
        )
    if "print_log" not in meta.tables:
        Table(
            "print_log", meta,
            Column("id", BigInteger, primary_key=True),
            Column("box_number", String(60), default=""),
            Column("action", String(30)),
            Column("username", String(80)),
            Column("detail", String(500), default=""),
            Column("created_at", DateTime(timezone=True), default=utcnow),
            extend_existing=True,
        )
    meta.create_all(engine, checkfirst=True)

    with SessionLocal.begin() as s:
        year_key = str(datetime.now().year)
        row = s.execute(
            text("SELECT prefix FROM box_sequences WHERE prefix = :p"), {"p": year_key}
        ).fetchone()
        if not row:
            s.execute(
                text("INSERT INTO box_sequences (prefix, next_number) VALUES (:p, :n)"),
                {"p": year_key, "n": 1},
            )

_ensure_tables()


# ── Pydantic models ───────────────────────────────────────────────

class PrinterIn(BaseModel):
    name: str
    ip: str
    port: int = 9100
    type: str = "zebra"

class LookupIn(BaseModel):
    identificador: str

class GerarLoteIn(BaseModel):
    tipo: str
    equipamento: str = ""
    quantidade: int = 1
    lote_venda: str = ""
    asset_ids: list[int] = []
    printer_id: int | None = None
    copies: int = 1

class ZebraLivreIn(BaseModel):
    titulo: str = ""
    identificador: str = ""
    quantidade: int = 1
    equipamento: str = ""
    printer_id: int | None = None
    copies: int = 1

class A4In(BaseModel):
    texto1: str = ""
    texto2: str = ""
    texto3: str = ""
    copies: int = 1

class A4PrintIn(BaseModel):
    texto1: str = ""
    texto2: str = ""
    texto3: str = ""
    printer_id: int
    copies: int = 1


# ── TCP printing ──────────────────────────────────────────────────

def _send_to_printer(ip: str, port: int, data: bytes):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect((ip, port))
            s.sendall(data)
    except socket.timeout:
        raise HTTPException(504, f"Timeout ao conectar em {ip}:{port}.")
    except ConnectionRefusedError:
        raise HTTPException(502, f"Conexão recusada por {ip}:{port}.")
    except Exception as e:
        raise HTTPException(502, f"Falha ao enviar para {ip}:{port} — {e}")


def _get_printer(s, printer_id: int | None):
    if not printer_id:
        return None
    row = s.execute(
        text("SELECT ip, port, type FROM printers WHERE id = :id"),
        {"id": printer_id},
    ).fetchone()
    if not row:
        raise HTTPException(404, "Impressora não encontrada.")
    return row


# ── ZPL builders ──────────────────────────────────────────────────

def _build_box_zpl(
    box_number: str,
    tipo: str,
    equipamento: str,
    quantidade: int,
    lote_venda: str = "",
) -> str:
    """Etiqueta de caixa — 799×1439 dots (100×180mm @ 203 DPI).

    Layout MAXIMIZADO para leitura à distância em porta-pallets.
    Ocupa toda a área da etiqueta com fontes grandes e barcode alto.
    """
    w, h = 799, 1439
    mx = 15
    inner = w - 2 * mx

    zpl = f"^XA^PW{w}^LL{h}^CI28^"

    # ── Borda externa espessa ──
    zpl += f"^FO{mx},{mx}^GB{inner},{h - 2*mx},4,B^FS"

    # ── Header bar — barra escura no topo ──
    hdr_h = 130
    zpl += f"^FO{mx},{mx}^GB{inner},{hdr_h},{hdr_h},B^FS"
    zpl += f"^FO{mx + 15},{mx + 25}^FR^A0N,80,80^FDSPARE^FS"
    tipo_upper = tipo.upper()
    zpl += f"^FO{mx},{mx + 25}^FR^A0N,80,80^FB{inner - 15},1,0,R^FD{tipo_upper}^FS"

    y = mx + hdr_h + 25

    # ── Box number — ENORME, centralizado ──
    zpl += f"^FO{mx},{y}^A0N,140,140^FB{inner},1,0,C^FD{box_number}^FS"
    y += 160

    # ── Separador ──
    zpl += f"^FO{mx + 10},{y}^GB{inner - 20},3,3^FS"
    y += 25

    # ── Barcode Code 128 — GRANDE, centralizado, module 4 ──
    bc_module = 4
    bc_height = 320
    bc_data_len = len(box_number)
    bc_w = ((bc_data_len + 3) * 11 + 2) * bc_module
    bc_x = max(mx + 10, (w - bc_w) // 2)
    zpl += f"^FO{bc_x},{y}^BY{bc_module}^BCN,{bc_height},N,N,N^FD{box_number}^FS"
    y += bc_height + 15

    # ── Texto do barcode abaixo ──
    zpl += f"^FO{mx},{y}^A0N,50,50^FB{inner},1,0,C^FD{box_number}^FS"
    y += 65

    # ── Separador ──
    zpl += f"^FO{mx + 10},{y}^GB{inner - 20},3,3^FS"
    y += 30

    label_x = mx + 20

    # ── Equipamento — fonte GRANDE para identificação à distância ──
    if equipamento:
        zpl += f"^FO{label_x},{y}^A0N,35,35^FDEQUIPAMENTO^FS"
        y += 45
        fs = 120 if len(equipamento) <= 8 else 100 if len(equipamento) <= 12 else 70 if len(equipamento) <= 20 else 50
        zpl += f"^FO{mx},{y}^A0N,{fs},{fs}^FB{inner},1,0,C^FD{equipamento}^FS"
        y += fs + 30
    else:
        y += 15

    # ── Quantidade — ENORME ──
    zpl += f"^FO{label_x},{y}^A0N,35,35^FDQUANTIDADE^FS"
    y += 45
    zpl += f"^FO{mx},{y}^A0N,140,140^FB{inner},1,0,C^FD{quantidade}^FS"
    y += 160

    # ── Lote da venda (só para venda) ──
    if lote_venda:
        zpl += f"^FO{mx + 10},{y}^GB{inner - 20},3,3^FS"
        y += 30
        zpl += f"^FO{label_x},{y}^A0N,35,35^FDLOTE DA VENDA^FS"
        y += 45
        fs = 70 if len(lote_venda) <= 15 else 50
        zpl += f"^FO{mx},{y}^A0N,{fs},{fs}^FB{inner},1,0,C^FD{lote_venda}^FS"
        y += fs + 20

    # ── Footer bar — barra escura no rodapé ──
    ftr_h = 80
    ftr_y = h - mx - ftr_h
    zpl += f"^FO{mx},{ftr_y}^GB{inner},{ftr_h},{ftr_h},B^FS"
    now = datetime.now().strftime("%d/%m/%Y  %H:%M")
    zpl += f"^FO{mx},{ftr_y + 22}^FR^A0N,36,36^FB{inner},1,0,C^FD{now}^FS"

    zpl += "^XZ"
    return zpl


def _build_livre_zpl(
    titulo: str,
    identificador: str,
    quantidade: int,
    equipamento: str,
) -> str:
    """Etiqueta livre — 799×1439 dots (100×180mm @ 203 DPI).

    Layout MAXIMIZADO para leitura à distância em porta-pallets.
    """
    w, h = 799, 1439
    mx = 15
    inner = w - 2 * mx

    zpl = f"^XA^PW{w}^LL{h}^CI28^"

    # ── Borda externa espessa ──
    zpl += f"^FO{mx},{mx}^GB{inner},{h - 2*mx},4,B^FS"

    # ── Header bar — barra escura ──
    hdr_h = 130
    zpl += f"^FO{mx},{mx}^GB{inner},{hdr_h},{hdr_h},B^FS"
    zpl += f"^FO{mx + 15},{mx + 25}^FR^A0N,80,80^FDSPARE^FS"
    zpl += f"^FO{mx},{mx + 25}^FR^A0N,50,50^FB{inner - 15},1,0,R^FDETIQUETA LIVRE^FS"

    y = mx + hdr_h + 30
    label_x = mx + 20

    # ── Título — ENORME centralizado ──
    if titulo:
        fs = 120 if len(titulo) <= 8 else 90 if len(titulo) <= 12 else 70 if len(titulo) <= 18 else 50
        zpl += f"^FO{mx},{y}^A0N,{fs},{fs}^FB{inner},1,0,C^FD{titulo.upper()}^FS"
        y += fs + 30

    # ── Separador ──
    zpl += f"^FO{mx + 10},{y}^GB{inner - 20},3,3^FS"
    y += 25

    # ── Barcode Code 128 — GRANDE, centralizado, module 4 ──
    if identificador:
        bc_module = 4
        bc_height = 320
        bc_data_len = len(identificador)
        bc_w = ((bc_data_len + 3) * 11 + 2) * bc_module
        bc_x = max(mx + 10, (w - bc_w) // 2)
        zpl += f"^FO{bc_x},{y}^BY{bc_module}^BCN,{bc_height},N,N,N^FD{identificador}^FS"
        y += bc_height + 15
        zpl += f"^FO{mx},{y}^A0N,50,50^FB{inner},1,0,C^FD{identificador}^FS"
        y += 65

    # ── Separador ──
    zpl += f"^FO{mx + 10},{y}^GB{inner - 20},3,3^FS"
    y += 30

    # ── Equipamento — fonte GRANDE ──
    if equipamento:
        zpl += f"^FO{label_x},{y}^A0N,35,35^FDEQUIPAMENTO^FS"
        y += 45
        fs = 120 if len(equipamento) <= 8 else 100 if len(equipamento) <= 12 else 70 if len(equipamento) <= 20 else 50
        zpl += f"^FO{mx},{y}^A0N,{fs},{fs}^FB{inner},1,0,C^FD{equipamento}^FS"
        y += fs + 30

    # ── Quantidade — ENORME ──
    zpl += f"^FO{label_x},{y}^A0N,35,35^FDQUANTIDADE^FS"
    y += 45
    zpl += f"^FO{mx},{y}^A0N,140,140^FB{inner},1,0,C^FD{quantidade}^FS"

    # ── Footer bar ──
    ftr_h = 80
    ftr_y = h - mx - ftr_h
    zpl += f"^FO{mx},{ftr_y}^GB{inner},{ftr_h},{ftr_h},B^FS"
    now = datetime.now().strftime("%d/%m/%Y  %H:%M")
    zpl += f"^FO{mx},{ftr_y + 22}^FR^A0N,36,36^FB{inner},1,0,C^FD{now}^FS"

    zpl += "^XZ"
    return zpl


# ── A4 PDF builder ────────────────────────────────────────────────

def _build_a4_pdf(texto1: str, texto2: str, texto3: str) -> bytes:
    """PDF A4 replicando o layout do template PPTX:
    - Borda preta espessa ao redor da página
    - Logo RENNER (image2.png) no canto superior esquerdo
    - 3 campos de texto grandes centralizados verticalmente
    - Logo Programa Qualidade (image3.png) no canto inferior esquerdo
    - Logo 5S (image1.png) no canto inferior direito
    """
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.pdfgen import canvas as canv
    except ImportError:
        raise HTTPException(501, "reportlab não está instalado no servidor.")

    import os

    # A4 PAISAGEM (o modelo é 4:3 landscape). Replica proporcionalmente o
    # template PPTX (10 x 7,5 pol): borda, logo RENNER (topo-esq.), 3 textos
    # centralizados, Qualidade (rodapé-esq.) e 5S (rodapé-dir.).
    buf = io.BytesIO()
    W, H = landscape(A4)  # ~841.89 x 595.27 points
    c = canv.Canvas(buf, pagesize=(W, H))

    # Escala do template (pontos): 10 x 7,5 pol → página A4 paisagem
    TPL_W, TPL_H = 10.0 * 72, 7.5 * 72
    sx, sy = W / TPL_W, H / TPL_H

    def _box(x_in, y_in, w_in, h_in):
        """(x,y,w,h) em pontos, origem inferior-esquerda, a partir de coords
        do template em polegadas com origem superior-esquerda."""
        w = w_in * 72 * sx
        h = h_in * 72 * sy
        x = x_in * 72 * sx
        y = H - (y_in + h_in) * 72 * sy
        return x, y, w, h

    def _img(path, x_in, y_in, w_in, h_in, anchor):
        if os.path.isfile(path):
            x, y, w, h = _box(x_in, y_in, w_in, h_in)
            try:
                c.drawImage(path, x, y, width=w, height=h,
                            preserveAspectRatio=True, anchor=anchor, mask="auto")
            except Exception:
                pass

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assets = os.path.join(base, "static", "identificacao", "assets")
    img_logo = os.path.join(assets, "logo_renner.png")
    img_qual = os.path.join(assets, "programa_qualidade.png")
    img_5s = os.path.join(assets, "programa_5s.png")

    # ── Borda (Retângulo 5 do template: 0.06,0.29 · 9.88x6.93) ──
    bx, by, bw, bh = _box(0.06, 0.29, 9.88, 6.93)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(3)
    c.rect(bx, by, bw, bh, stroke=1, fill=0)

    # ── Logos (posições/tamanhos exatos do template) ──
    _img(img_logo, 0.27, 0.42, 3.76, 1.11, "nw")   # RENNER — topo-esq.
    _img(img_qual, 0.27, 5.94, 2.00, 1.18, "sw")   # Qualidade — rodapé-esq.
    _img(img_5s,   7.97, 5.26, 1.97, 1.85, "se")   # 5S — rodapé-dir.

    # ── 3 textos centralizados (caixas full-width, fonte 80 com auto-ajuste) ──
    font_name = "Helvetica-Bold"
    box_h = 1.57
    campos = [(texto1, 2.03), (texto2, 3.28), (texto3, 4.59)]
    max_w = (9.21 * 72 * sx) - 10
    cx = W / 2
    for txt, y_in in campos:
        if not txt:
            continue
        t = txt.upper()
        fs = 80 * sy
        while fs > 14 and c.stringWidth(t, font_name, fs) > max_w:
            fs -= 2
        top = H - (y_in * 72 * sy)
        bot = H - ((y_in + box_h) * 72 * sy)
        baseline = (top + bot) / 2 - fs * 0.35
        c.setFont(font_name, fs)
        c.setFillColorRGB(0, 0, 0)
        c.drawCentredString(cx, baseline, t)

    c.save()
    buf.seek(0)
    return buf.read()


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS — Printers
# ═══════════════════════════════════════════════════════════════════

@router.get("/printers")
def list_printers(req: Request):
    require_permission(req, "identificacao", "view")
    with SessionLocal() as s:
        rows = s.execute(
            text("SELECT id, name, ip, port, type, active FROM printers ORDER BY type, name")
        ).fetchall()
        return {
            "impressoras": [
                {"id": r[0], "name": r[1], "ip": r[2], "port": r[3],
                 "type": r[4], "active": r[5]}
                for r in rows
            ]
        }


@router.post("/printers")
def add_printer(body: PrinterIn, req: Request):
    require_permission(req, "identificacao", "admin")
    check_rate_limit(req)
    t = body.type.upper()
    if t not in ("ZEBRA", "LEXMARK"):
        raise HTTPException(400, "Tipo deve ser ZEBRA ou LEXMARK.")
    with SessionLocal.begin() as s:
        s.execute(
            text(
                "INSERT INTO printers (name, ip, port, type, active) "
                "VALUES (:n, :i, :p, :t, true)"
            ),
            {"n": body.name.strip(), "i": body.ip.strip(), "p": body.port, "t": t},
        )
    return {"ok": True}


@router.delete("/printers/{printer_id}")
def delete_printer(printer_id: int, req: Request):
    require_permission(req, "identificacao", "admin")
    with SessionLocal.begin() as s:
        s.execute(text("DELETE FROM printers WHERE id = :id"), {"id": printer_id})
    return {"ok": True}


@router.post("/test-printer")
def test_printer(body: dict, req: Request):
    require_permission(req, "identificacao", "view")
    pid = body.get("printer_id")
    with SessionLocal() as s:
        pr = _get_printer(s, pid)
    if not pr:
        raise HTTPException(400, "Impressora não informada.")
    ip, port, ptype = pr[0], pr[1], pr[2]
    if ptype == "ZEBRA":
        data = b"^XA^CF0,30^FO50,50^FDTeste SPARE OK^FS^XZ"
    else:
        data = b"\x1b%-12345X@PJL\r\n@PJL ECHO SPARE TEST OK\r\n\x1b%-12345X"
    _send_to_printer(ip, port, data)
    return {"ok": True, "message": f"Teste enviado para {ip}:{port}"}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS — Gerar Lote
# ═══════════════════════════════════════════════════════════════════

@router.get("/next-sequence/{tipo}")
def next_sequence(tipo: str, req: Request):
    require_permission(req, "identificacao", "view")
    year_key = str(datetime.now().year)
    with SessionLocal() as s:
        row = s.execute(
            text("SELECT next_number FROM box_sequences WHERE prefix = :p"),
            {"p": year_key},
        ).fetchone()
        num = row[0] if row else 1
    display = f"{year_key}-{num:04d}"
    return {"prefix": year_key, "next": num, "display": display}


@router.post("/lookup-asset")
def lookup_asset(body: LookupIn, req: Request):
    """Busca ativo por qualquer identificador e retorna com ciclo aberto."""
    require_permission(req, "identificacao", "view")
    ident = body.identificador.strip().upper()
    if not ident:
        raise HTTPException(400, "Identificador vazio.")

    with SessionLocal() as s:
        asset = s.scalar(
            select(Asset).where(
                or_(
                    func.upper(Asset.serial_number) == ident,
                    func.upper(Asset.tag_number) == ident,
                    func.upper(Asset.asset_number) == ident,
                    func.upper(Asset.asset_id) == ident,
                )
            )
        )
        if not asset:
            raise HTTPException(404, f"Ativo '{body.identificador}' não encontrado.")

        cycle = s.scalar(
            select(ReceiptCycle)
            .where(ReceiptCycle.asset_id == asset.id, ReceiptCycle.open == True)  # noqa: E712
            .order_by(ReceiptCycle.id.desc())
        )

        return {
            "asset_id": asset.id,
            "empresa": asset.company or "",
            "serie": asset.serial_number or "",
            "etiqueta": asset.tag_number or "",
            "ativo": asset.asset_number or asset.asset_id or "",
            "modelo": asset.model or "",
            "categoria": asset.category or "",
            "descricao": asset.description or "",
            "cycle_id": cycle.id if cycle else None,
            "cycle_status": cycle.status if cycle else None,
            "cycle_open": bool(cycle),
        }


@router.post("/preview-lote")
def preview_lote(body: GerarLoteIn, req: Request):
    """Gera preview da ZPL sem consumir sequência."""
    require_permission(req, "identificacao", "view")
    year_key = str(datetime.now().year)

    with SessionLocal() as s:
        row = s.execute(
            text("SELECT next_number FROM box_sequences WHERE prefix = :p"),
            {"p": year_key},
        ).fetchone()
        num = row[0] if row else 1

    box_display = f"{year_key}-{num:04d}"
    zpl = _build_box_zpl(
        box_number=box_display,
        tipo=body.tipo,
        equipamento=body.equipamento,
        quantidade=body.quantidade,
        lote_venda=body.lote_venda,
    )
    return {"zpl": zpl, "caixa_preview": box_display}


@router.post("/gerar-lote")
def gerar_lote(body: GerarLoteIn, req: Request):
    """Gera caixa, consome sequência, atualiza status dos recebimentos, imprime."""
    sd = require_permission(req, "identificacao", "create")
    check_rate_limit(req)

    tipo_upper = body.tipo.upper()
    if tipo_upper not in ("TRIAGEM", "VENDA"):
        raise HTTPException(400, "Tipo deve ser TRIAGEM ou VENDA.")
    if tipo_upper == "VENDA" and not body.lote_venda.strip():
        raise HTTPException(400, "Lote da Venda é obrigatório para tipo Venda.")

    new_status = "AG TRIAGEM" if tipo_upper == "TRIAGEM" else "VENDA"
    year_key = str(datetime.now().year)

    # Consume sequence atomically (YYYY-NNNN, reset anual)
    with SessionLocal.begin() as s:
        row = s.execute(
            text("SELECT next_number FROM box_sequences WHERE prefix = :p FOR UPDATE"),
            {"p": year_key},
        ).fetchone()
        if row:
            seq = row[0]
            s.execute(
                text("UPDATE box_sequences SET next_number = :n WHERE prefix = :p"),
                {"n": seq + 1, "p": year_key},
            )
        else:
            seq = 1
            s.execute(
                text("INSERT INTO box_sequences (prefix, next_number) VALUES (:p, :n)"),
                {"p": year_key, "n": 2},
            )

    box_number = f"{year_key}-{seq:04d}"

    # Update receipt cycles for included assets
    updated = 0
    with SessionLocal.begin() as s:
        for aid in body.asset_ids:
            cycle = s.scalar(
                select(ReceiptCycle)
                .where(ReceiptCycle.asset_id == aid, ReceiptCycle.open == True)  # noqa: E712
                .order_by(ReceiptCycle.id.desc())
            )
            if not cycle:
                continue
            old_status = cycle.status
            cycle.status = new_status
            cycle.lot_number = box_number
            cycle.updated_by = sd["username"]
            s.add(Movement(
                asset_id=cycle.asset_id,
                cycle_id=cycle.id,
                old_status=old_status,
                new_status=new_status,
                lot_number=box_number,
                origin="GERAR LOTE",
                username=sd["username"],
            ))
            updated += 1

        # Log the print
        s.execute(
            text(
                "INSERT INTO print_log (box_number, action, username, detail) "
                "VALUES (:b, :a, :u, :d)"
            ),
            {
                "b": box_number,
                "a": "GERAR_LOTE",
                "u": sd["username"],
                "d": f"tipo={tipo_upper}, qty={body.quantidade}, ativos={updated}, lote_venda={body.lote_venda}",
            },
        )

    # Print if printer selected
    zpl = _build_box_zpl(
        box_number=box_number,
        tipo=tipo_upper,
        equipamento=body.equipamento,
        quantidade=body.quantidade,
        lote_venda=body.lote_venda,
    )
    printed = False
    if body.printer_id:
        with SessionLocal() as s:
            pr = _get_printer(s, body.printer_id)
        if pr:
            payload = (zpl * body.copies).encode("utf-8")
            _send_to_printer(pr[0], pr[1], payload)
            printed = True

    return {
        "ok": True,
        "caixa": box_number,
        "tipo": tipo_upper,
        "ativos_atualizados": updated,
        "status_aplicado": new_status,
        "impresso": printed,
        "zpl": zpl,
    }


@router.post("/reimprimir-caixa")
def reimprimir_caixa(body: dict, req: Request):
    """Reimprime etiqueta de caixa já gerada, com rastreabilidade."""
    sd = require_permission(req, "identificacao", "create")
    box_number = body.get("box_number", "").strip()
    printer_id = body.get("printer_id")
    copies = body.get("copies", 1)
    if not box_number or not printer_id:
        raise HTTPException(400, "Informe box_number e printer_id.")

    with SessionLocal() as s:
        pr = _get_printer(s, printer_id)

    # Minimal box label for reprint
    zpl = _build_box_zpl(
        box_number=box_number,
        tipo="TRIAGEM" if box_number.startswith("TR") else "VENDA",
        equipamento=body.get("equipamento", ""),
        quantidade=body.get("quantidade", 0),
        lote_venda=body.get("lote_venda", ""),
    )

    with SessionLocal.begin() as s:
        s.execute(
            text(
                "INSERT INTO print_log (box_number, action, username, detail) "
                "VALUES (:b, :a, :u, :d)"
            ),
            {
                "b": box_number,
                "a": "REIMPRESSAO",
                "u": sd["username"],
                "d": f"copies={copies}",
            },
        )

    payload = (zpl * copies).encode("utf-8")
    _send_to_printer(pr[0], pr[1], payload)
    return {"ok": True, "message": f"Reimpressão de {box_number} enviada."}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS — Zebra Livre
# ═══════════════════════════════════════════════════════════════════

@router.post("/zebra-livre")
def zebra_livre(body: ZebraLivreIn, req: Request):
    """Impressão livre — não consome sequência, não cria lote, não altera status."""
    require_permission(req, "identificacao", "create")
    check_rate_limit(req)

    zpl = _build_livre_zpl(
        titulo=body.titulo,
        identificador=body.identificador,
        quantidade=body.quantidade,
        equipamento=body.equipamento,
    )

    printed = False
    if body.printer_id:
        with SessionLocal() as s:
            pr = _get_printer(s, body.printer_id)
        if pr:
            payload = (zpl * body.copies).encode("utf-8")
            _send_to_printer(pr[0], pr[1], payload)
            printed = True

    return {"ok": True, "impresso": printed, "zpl": zpl}


@router.post("/preview-livre")
def preview_livre(body: ZebraLivreIn, req: Request):
    require_permission(req, "identificacao", "view")
    zpl = _build_livre_zpl(
        titulo=body.titulo,
        identificador=body.identificador,
        quantidade=body.quantidade,
        equipamento=body.equipamento,
    )
    return {"zpl": zpl}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS — Identificação A4
# ═══════════════════════════════════════════════════════════════════

@router.post("/a4.pdf")
def generate_a4(body: A4In, req: Request):
    require_permission(req, "identificacao", "view")
    if not any([body.texto1, body.texto2, body.texto3]):
        raise HTTPException(400, "Preencha ao menos um campo de texto.")
    pdf = _build_a4_pdf(body.texto1, body.texto2, body.texto3)
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="identificacao_a4.pdf"'},
    )


@router.post("/a4.print")
def print_a4(body: A4PrintIn, req: Request):
    require_permission(req, "identificacao", "create")
    check_rate_limit(req)
    if not any([body.texto1, body.texto2, body.texto3]):
        raise HTTPException(400, "Preencha ao menos um campo de texto.")

    with SessionLocal() as s:
        pr = _get_printer(s, body.printer_id)
    if not pr:
        raise HTTPException(404, "Impressora não encontrada.")

    pdf = _build_a4_pdf(body.texto1, body.texto2, body.texto3)
    payload = pdf * body.copies
    _send_to_printer(pr[0], pr[1], payload)
    return {"ok": True, "message": f"A4 enviado para {pr[0]}:{pr[1]}"}


# ═══════════════════════════════════════════════════════════════════
# ENDPOINTS — Options (for selects)
# ═══════════════════════════════════════════════════════════════════

@router.get("/options")
def print_options(req: Request):
    require_permission(req, "identificacao", "view")
    with SessionLocal() as s:
        printers = s.execute(
            text("SELECT id, name, type FROM printers WHERE active = true ORDER BY type, name")
        ).fetchall()
        return {
            "impressoras": [
                {"id": r[0], "name": r[1], "type": r[2]} for r in printers
            ],
        }
