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


# Linha de item do DANFE costuma terminar com QUANT VUNIT VTOTAL. Pegamos a
# descrição (texto) e a primeira quantidade plausível da cauda numérica.
_LINHA_ITEM = re.compile(
    r"^\s*(?:\d{1,6}\s+)?"                # código do produto (opcional)
    r"(?P<desc>.+?)\s+"                    # descrição
    r"\d{8}\s+"                           # NCM (8 dígitos) — âncora do DANFE
    r"(?:\d{2,3}\s+)"                      # CST/CSOSN
    r"\d{4}\s+"                           # CFOP (4 dígitos)
    r"[A-Za-z]{1,6}\s+"                    # unidade (UN, PC, CX...)
    r"(?P<qtd>\d{1,6}(?:[.,]\d+)?)\s+"    # quantidade
    r"[\d.,]+",                           # valor unitário
    re.IGNORECASE,
)


def achar_itens(texto: str) -> list[dict]:
    """Itens (descrição + quantidade). Heurístico: revisar sempre."""
    itens: list[dict] = []
    for linha in texto.splitlines():
        m = _LINHA_ITEM.match(linha)
        if not m:
            continue
        desc = re.sub(r"\s+", " ", m.group("desc")).strip()
        if len(desc) < 3:
            continue
        try:
            qtd = int(float(m.group("qtd").replace(".", "").replace(",", ".")))
        except ValueError:
            continue
        if qtd <= 0:
            continue
        itens.append({"descricao": desc[:200], "quantidade": qtd})
    return itens


def extrair_campos(dados: bytes) -> dict:
    """Tudo o que dá para inferir do PDF, para pré-preencher o formulário."""
    texto = extrair_texto(dados)
    chave = achar_chave(texto)
    campos = {
        "chave": chave,
        "nf": "", "serie": "", "emitente_cnpj": "",
        "po": achar_po(texto),
        "itens": achar_itens(texto),
    }
    campos.update(_campos_da_chave(chave))
    # Aviso honesto: número/série vêm da chave (exatos); o resto é palpite.
    campos["confiavel"] = bool(chave)
    return campos
