"""Extração de campos de uma NF-e a partir do PDF (DANFE), em memória.

O PDF NUNCA é gravado em disco: quem chama passa os bytes, aqui se lê o
texto, extraem-se os campos e pronto. Serve só para PRÉ-PREENCHER o
formulário — o operador confere tudo antes de salvar.

O dado mais confiável é a CHAVE DE ACESSO (44 dígitos): dela saem, por
posição fixa (layout oficial da NF-e), o número da NF, a série e o CNPJ do
emitente — sem depender do layout visual do DANFE. PO e itens são
heurísticos (variam por emissor) e vêm marcados para revisão.
"""
from __future__ import annotations

import re


class SemBibliotecaPDF(RuntimeError):
    """Nenhuma biblioteca de leitura de PDF disponível no servidor."""


def extrair_texto(dados: bytes) -> str:
    """Texto do PDF. Tenta PyMuPDF; se faltar, pypdf. Nada é gravado."""
    # PyMuPDF (fitz): melhor extração, quando existe.
    try:
        import fitz  # type: ignore
        doc = fitz.open(stream=dados, filetype="pdf")
        try:
            return "\n".join(pag.get_text() for pag in doc)
        finally:
            doc.close()
    except ImportError:
        pass
    # pypdf: puro-Python, leve, o fallback do requirements.
    try:
        from pypdf import PdfReader  # type: ignore
        import io
        leitor = PdfReader(io.BytesIO(dados))
        return "\n".join((p.extract_text() or "") for p in leitor.pages)
    except ImportError as exc:
        raise SemBibliotecaPDF(
            "Nenhuma biblioteca de PDF instalada no servidor "
            "(pip install pypdf).") from exc


def _so_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto)


def achar_chave(texto: str) -> str:
    """A chave de acesso: 44 dígitos, tolerando espaços a cada 4."""
    # Primeiro o formato agrupado "1234 5678 ..."; depois 44 dígitos seguidos.
    for padrao in (r"(?:\d[\s.]?){43}\d", ):
        for m in re.finditer(padrao, texto):
            d = _so_digitos(m.group(0))
            if len(d) == 44:
                return d
    m = re.search(r"\d{44}", _so_digitos(texto))
    return m.group(0) if m else ""


def _campos_da_chave(chave: str) -> dict:
    """Número, série e CNPJ do emitente, por posição fixa no layout oficial."""
    if len(chave) != 44:
        return {}
    return {
        "emitente_cnpj": chave[6:20],
        "serie": str(int(chave[22:25])),
        "nf": str(int(chave[25:34])),
    }


def achar_po(texto: str) -> str:
    """PO/pedido, quando vem rotulado nos dados adicionais. Heurístico."""
    for padrao in (
        r"(?:pedido de compra|pedido compra|ordem de compra|\bPO\b|pedido)\D{0,12}?"
        r"([A-Z0-9][A-Z0-9\-/]{3,20})",
    ):
        m = re.search(padrao, texto, re.IGNORECASE)
        if m:
            cand = m.group(1).strip(" .:-/")
            if any(c.isdigit() for c in cand):
                return cand
    return ""


# ── Itens do DANFE ────────────────────────────────────────────────────────
# Cada emissor põe a tabela de itens num layout diferente (uma coluna por
# linha, ou colunas coladas). O que NÃO muda é a ordem oficial das colunas:
# NCM → CST → CFOP → UN → QTD. Então tokenizamos o texto inteiro (colapsando
# quebras de linha) e ancoramos na NCM (8 dígitos) que seja seguida de um
# CFOP — isso descarta CNPJ/CEP de 8 dígitos do rodapé. Antes da NCM vêm o
# código e a descrição; depois do CFOP (pulando a unidade) vem a quantidade.
_NCM8   = re.compile(r"^\d{4}\.?\d{2}\.?\d{2}$")
_CFOP   = re.compile(r"^[1-7]\.?\d{3}$")
_MONEY  = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2,}$|^\d+,\d{2,}$")
_NUMTOK = re.compile(r"^\d{1,4}(?:\.\d{3})*(?:,\d+)?$|^\d+(?:[.,]\d+)?$")
_UNIT   = re.compile(r"^[A-Za-zÇç]{1,6}\.?$")
_SKU    = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-\.\/]{3,}$")
_HEADER = re.compile(
    r"DESCRI|PRODUTO|SERVI|NCM|CST|CSOSN|CFOP|UNID|QUANT|QTDE|QTD|UNIT|"
    r"TOTAL|ICMS|IPI|AL[IÍ]Q|TRIBUT|VLR|VALOR|PRE[ÇC]O", re.I)


def _digitos(t: str) -> str:
    return re.sub(r"\D", "", t)


def _qtd_int(s: str) -> int:
    s = s.strip()
    if "," in s:                       # 1.234,000 -> 1234.000 (decimal BR)
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


def _ncm_de_item(toks: list, p: int, n: int) -> bool:
    """A NCM em p é de um item (e não um código/CNPJ de 8 dígitos) se um
    CFOP aparece logo em seguida."""
    for j in range(p + 1, min(p + 7, n)):
        if _CFOP.match(toks[j]):
            return True
    return False


def achar_itens(texto: str) -> list[dict]:
    """Itens do DANFE (código, descrição, quantidade). Heurístico: sempre
    revisar. Cobre bem os emissores mais comuns; para 100% use o XML."""
    toks = [t for t in re.split(r"\s+", texto) if t]
    n = len(toks)
    itens: list[dict] = []
    vistos = set()
    i = 0
    while i < n:
        if not (_NCM8.match(toks[i]) and len(_digitos(toks[i])) == 8
                and _ncm_de_item(toks, i, n)):
            i += 1
            continue
        p = i
        # quantidade: depois do CFOP, pulando a unidade (UN/PC/...)
        qtd = 0
        cfop_at = -1
        for j in range(p + 1, min(p + 7, n)):
            if _CFOP.match(toks[j]):
                cfop_at = j
        if cfop_at > 0:
            k = cfop_at + 1
            while k < min(cfop_at + 4, n) and _UNIT.match(toks[k]):
                k += 1
            if k < n and _NUMTOK.match(toks[k]):
                qtd = _qtd_int(toks[k])
        # bloco de código + descrição: tokens antes da NCM
        bloco = []
        j = p - 1
        while j >= 0 and len(bloco) < 18:
            t = toks[j]
            if _MONEY.match(t) or _HEADER.match(t) or _CFOP.match(t):
                break
            if _NCM8.match(t) and _ncm_de_item(toks, j, n):
                break
            bloco.append(t)
            j -= 1
        bloco.reverse()
        codigo, desc = "", ""
        idx = None
        for x, t in enumerate(bloco):
            if (_SKU.match(t) and any(c.isdigit() for c in t)
                    and x + 1 < len(bloco) and re.search(r"[A-Za-zÇç]", bloco[x + 1])):
                idx = x
                break
        if idx is not None:
            codigo = bloco[idx].strip(".")
            desc = " ".join(bloco[idx + 1:])
        else:
            desc = " ".join(bloco)
        desc = re.sub(r"\s+", " ", desc).strip(" -.")
        letras = len(re.findall(r"[A-Za-zÇç]", desc))
        ok = (qtd > 0 and letras >= 3 and not desc.startswith("R$")
              and "%" not in desc and letras >= 0.35 * len(desc))
        if ok:
            chave_dedup = (codigo, desc, qtd)   # descarta repetição de página
            if chave_dedup not in vistos:
                vistos.add(chave_dedup)
                itens.append({"codigo": codigo, "descricao": desc[:200],
                              "quantidade": qtd})
        i = p + 1
    return itens


def extrair_campos(dados: bytes) -> dict:
    """Campos inferidos do PDF (DANFE), para pré-preencher o formulário."""
    texto = extrair_texto(dados)
    chave = achar_chave(texto)
    campos = {
        "formato": "pdf",
        "chave": chave,
        "nf": "", "serie": "", "emitente_cnpj": "",
        "po": achar_po(texto),
        "itens": achar_itens(texto),
    }
    campos.update(_campos_da_chave(chave))
    # Aviso honesto: número/série vêm da chave (exatos); o resto é palpite.
    campos["confiavel"] = bool(chave)
    return campos


# ── NF-e XML (fonte exata) ─────────────────────────────────────────────────
def _eh_xml(dados: bytes) -> bool:
    trecho = dados[:2048].lstrip()
    return trecho.startswith(b"<") and (b"<NFe" in dados[:4096]
                                        or b"nfeProc" in dados[:4096]
                                        or b"infNFe" in dados[:4096])


def extrair_de_xml(dados: bytes) -> dict:
    """Lê a NF-e diretamente do XML: número, série, CNPJ, PO e itens exatos.
    Sem heurística — é a fonte oficial. Namespace-agnóstico (usa localname)."""
    import xml.etree.ElementTree as ET

    raiz = ET.fromstring(dados)

    def _ln(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _find(elem, nome):
        for f in elem.iter():
            if _ln(f.tag) == nome:
                return f
        return None

    def _txt(elem, nome, default=""):
        f = _find(elem, nome) if elem is not None else None
        return (f.text or "").strip() if f is not None and f.text else default

    inf = _find(raiz, "infNFe")
    ide = _find(inf, "ide") if inf is not None else None
    emit = _find(inf, "emit") if inf is not None else None

    chave = ""
    if inf is not None:
        chave = _digitos(inf.get("Id", "") or "")[:44]

    campos = {
        "formato": "xml",
        "chave": chave,
        "nf": _txt(ide, "nNF"),
        "serie": _txt(ide, "serie"),
        "emitente_cnpj": _txt(emit, "CNPJ"),
        "po": "",
        "itens": [],
        "confiavel": True,
    }

    itens, po = [], ""
    if inf is not None:
        for det in inf.iter():
            if _ln(det.tag) != "det":
                continue
            prod = _find(det, "prod")
            if prod is None:
                continue
            desc = _txt(prod, "xProd")
            codigo = _txt(prod, "cProd")
            qcom = _txt(prod, "qCom") or _txt(prod, "qTrib") or "0"
            try:
                qtd = int(round(float(qcom.replace(",", "."))))
            except ValueError:
                qtd = 0
            if not po:
                po = _txt(prod, "xPed")            # pedido de compra, quando informado
            if desc and qtd > 0:
                itens.append({"codigo": codigo, "descricao": desc[:200],
                              "quantidade": qtd})
    campos["itens"] = itens
    campos["po"] = po
    return campos


def extrair(dados: bytes) -> dict:
    """Ponto único: detecta XML (exato) ou PDF (heurístico) e extrai."""
    if _eh_xml(dados):
        return extrair_de_xml(dados)
    return extrair_campos(dados)
