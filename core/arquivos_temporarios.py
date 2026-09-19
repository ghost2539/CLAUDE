"""Arquivos do fluxo de recebimento e lançamento que têm prazo de vida."""
from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("arquivos_temporarios")

RETENCAO_DIAS = 5

MODO_PASTA = 0o750
MODO_ARQUIVO = 0o640

_SEGMENTO = re.compile(r"[A-Za-z0-9_-]+")
_FORA_DO_NOME = re.compile(r"[^A-Za-z0-9._-]")
_PREFIXO_PARCIAL = ".parcial-"
_TAMANHO_MAXIMO_NOME = 200

def _raiz() -> Path:
    from config import get_settings
    return get_settings().DATA / "tmp"

def _segmento(valor, rotulo: str) -> str:
    texto = str(valor)
    if not _SEGMENTO.fullmatch(texto):
        raise ValueError(
            f"{rotulo} inválido: {texto!r} (só letras, dígitos, '_' e '-')")
    return texto

def sanear_nome(nome: str) -> str:
    limpo = _FORA_DO_NOME.sub("_", str(nome or "").strip()).lstrip(".")
    if not limpo:
        raise ValueError(f"nome de arquivo inválido: {nome!r}")
    return limpo[:_TAMANHO_MAXIMO_NOME]

def _criar_pasta(p: Path) -> None:
    if p.is_dir():
        return
    p.mkdir(mode=MODO_PASTA, parents=True, exist_ok=True)
    os.chmod(p, MODO_PASTA)

def _pasta_sem_criar(area, chave) -> Path:
    return _raiz() / _segmento(area, "area") / _segmento(chave, "chave")

def pasta(area: str, chave: str | int) -> Path:
    destino = _pasta_sem_criar(area, chave)
    for p in (_raiz(), destino.parent, destino):
        _criar_pasta(p)
    return destino

def gravar(area: str, chave: str | int, nome: str, conteudo: bytes) -> Path:
    destino_dir = pasta(area, chave)
    destino = destino_dir / sanear_nome(nome)
    fd, parcial = tempfile.mkstemp(prefix=_PREFIXO_PARCIAL, dir=destino_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(parcial, MODO_ARQUIVO)
        os.replace(parcial, destino)
    except BaseException:
        try:
            os.unlink(parcial)
        except OSError:
            pass
        raise
    return destino

def caminho(area: str, chave: str | int, nome: str) -> Path | None:
    try:
        base = _pasta_sem_criar(area, chave)
        limpo = sanear_nome(nome)
    except ValueError:
        return None
    if limpo != nome:
        return None
    try:
        base_real = base.resolve(strict=True)
        alvo = (base / limpo).resolve(strict=True)
    except OSError:
        return None
    if alvo.parent != base_real or not alvo.is_file():
        return None
    return alvo

def listar(area: str, chave: str | int) -> list[dict]:
    base = _pasta_sem_criar(area, chave)
    if not base.is_dir():
        return []
    itens = []
    for p in sorted(base.iterdir()):
        if p.name.startswith(".") or p.is_symlink() or not p.is_file():
            continue
        st = p.stat()
        itens.append({
            "nome": p.name,
            "tamanho": st.st_size,
            "modificado_em": datetime.fromtimestamp(st.st_mtime)
            .isoformat(timespec="seconds"),
        })
    return itens

def limpar_expirados(dias: int = RETENCAO_DIAS, area: str | None = None) -> int:
    raiz = _raiz()
    inicio = raiz / _segmento(area, "area") if area else raiz
    if not inicio.is_dir():
        return 0
    limite = time.time() - max(0, int(dias)) * 86400
    apagados = 0
    for atual, _subpastas, arquivos in os.walk(inicio, topdown=False):
        pasta_atual = Path(atual)
        for nome in arquivos:
            p = pasta_atual / nome
            try:
                if p.lstat().st_mtime < limite:
                    p.unlink()
                    apagados += 1
            except OSError as exc:
                _log.warning("Limpeza de temporários: %s não apagado: %s",
                             p, exc)
        if pasta_atual == raiz or raiz not in pasta_atual.parents:
            continue
        try:
            if not os.listdir(pasta_atual):
                os.rmdir(pasta_atual)
        except OSError as exc:
            _log.warning("Limpeza de temporários: pasta %s não removida: %s",
                         pasta_atual, exc)
    return apagados
