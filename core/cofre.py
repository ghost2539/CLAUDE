from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import stat
import sys
from pathlib import Path

_log = logging.getLogger("cofre")

DIR = Path(os.environ.get(
    "PORTAL_COFRE_DIR",
    Path.home() / ".config" / "portal-spare",
))
ARQ_CHAVE = DIR / "cofre.key"
ARQ_COFRE = DIR / "cofre.json"

_REF = re.compile(r"@cofre:([A-Za-z0-9_.-]{1,64})@")
MARCADOR = "@cofre:"

USAR_CORPORATIVO = False
_MSG_REMOVIDO = ("cofre corporativo removido do código — os segredos vêm do "
                 "os.environ e do cofre local")


def _corporativo(nome: str) -> str:
    return ""


def _somente_cofre(nome: str) -> str:
    return ""


def _resolver_modulo():
    return None


def _cache_do_loader(mod=None) -> dict | None:
    return None


def _funcao_do_modulo(mod=None):
    return None


def caminho_do_loader() -> str:
    return ""


def reler_loader() -> int:
    return 0


def arquivo_legivel() -> bool:
    return False


def chaves_corporativas() -> list[str]:
    return []


def corporativo_disponivel() -> bool:
    return False


def diagnostico_corporativo(chave_teste: str = "") -> tuple[bool, str]:
    return False, _MSG_REMOVIDO


def acesso_ao_arquivo() -> dict:
    return {
        "caminho": "", "existe": False, "legivel": False,
        "dono": "", "grupo": "", "modo": "",
        "usuario_atual": "", "grupos_atuais": [],
        "erro": _MSG_REMOVIDO,
    }


def onde_procura() -> list[str]:
    """Diretórios em que o Python procura módulos (informativo)."""
    return [p for p in sys.path if p]


# ── Arquivos e permissões ───────────────────────────────────────────────
def _garantir_dir() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    try:
        DIR.chmod(0o700)
    except OSError:
        pass


def _restringir(caminho: Path) -> None:
    try:
        caminho.chmod(0o600)
    except OSError:
        pass


def permissoes_ok() -> tuple[bool, list[str]]:
    problemas: list[str] = []
    for caminho in (DIR, ARQ_CHAVE, ARQ_COFRE):
        if not caminho.exists():
            continue
        modo = stat.S_IMODE(caminho.stat().st_mode)
        limite = 0o700 if caminho.is_dir() else 0o600
        if modo & ~limite:
            problemas.append(f"{caminho} está {oct(modo)[2:]} (esperado {oct(limite)[2:]})")
    return (not problemas), problemas


# ── Criptografia ────────────────────────────────────────────────────────
def _fernet():
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001
        return None

    _garantir_dir()
    if ARQ_CHAVE.exists():
        bruto = ARQ_CHAVE.read_bytes().strip()
    else:
        bruto = Fernet.generate_key()
        ARQ_CHAVE.write_bytes(bruto)
        _restringir(ARQ_CHAVE)
        _log.info("Chave do cofre criada em %s", ARQ_CHAVE)
    try:
        return Fernet(bruto)
    except Exception:  # noqa: BLE001 — chave corrompida
        _log.error("Chave do cofre inválida em %s", ARQ_CHAVE)
        return None


def _derivada() -> bytes:
    _garantir_dir()
    if not ARQ_CHAVE.exists():
        ARQ_CHAVE.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
        _restringir(ARQ_CHAVE)
    return hashlib.sha256(ARQ_CHAVE.read_bytes()).digest()


def _cifra_simples(texto: str) -> str:
    k = _derivada()
    b = texto.encode("utf-8")
    return "x:" + base64.b64encode(
        bytes(c ^ k[i % len(k)] for i, c in enumerate(b))).decode()


def _decifra_simples(blob: str) -> str:
    k = _derivada()
    b = base64.b64decode(blob[2:].encode())
    return bytes(c ^ k[i % len(k)] for i, c in enumerate(b)).decode("utf-8", "ignore")


# ── Leitura e escrita do cofre local ────────────────────────────────────
def _carregar() -> dict[str, str]:
    if not ARQ_COFRE.exists():
        return {}
    try:
        return json.loads(ARQ_COFRE.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        _log.error("Cofre ilegível (%s): %s", ARQ_COFRE, exc)
        return {}


def _gravar(dados: dict[str, str]) -> None:
    _garantir_dir()
    tmp = ARQ_COFRE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    _restringir(tmp)
    tmp.replace(ARQ_COFRE)
    _restringir(ARQ_COFRE)


def _local(nome: str) -> str:
    blob = _carregar().get(nome, "")
    if not blob:
        return ""
    if blob.startswith("x:"):
        try:
            return _decifra_simples(blob)
        except Exception:  # noqa: BLE001
            return ""
    f = _fernet()
    if not f:
        return ""
    try:
        return f.decrypt(blob.encode()).decode()
    except Exception:  # noqa: BLE001
        _log.error("Não consegui decifrar '%s' — a chave do cofre mudou?", nome)
        return ""


# ── API pública ─────────────────────────────────────────────────────────
def obter(nome: str, default: str = "") -> str:
    """Segredo: cofre local → os.environ → default. Sem cofre corporativo."""
    v = _local(nome)
    if v:
        return v
    v = os.environ.get(nome, "")
    if v and MARCADOR not in v:
        return v
    return default


def fonte(nome: str) -> str:
    if _local(nome):
        return "cofre local"
    if os.environ.get(nome):
        return "ambiente"
    return "não definido"


def definir(nome: str, valor: str) -> None:
    dados = _carregar()
    f = _fernet()
    dados[nome] = f.encrypt(valor.encode()).decode() if f else _cifra_simples(valor)
    _gravar(dados)


def remover(nome: str) -> bool:
    dados = _carregar()
    if nome not in dados:
        return False
    del dados[nome]
    _gravar(dados)
    return True


def listar() -> list[str]:
    return sorted(_carregar().keys())


def algoritmo() -> str:
    return "fernet" if _fernet() else "derivada"


def expandir(texto: str) -> str:
    if not texto or MARCADOR not in texto:
        return texto
    return _REF.sub(lambda m: obter(m.group(1)), texto)


def referencias(texto: str) -> list[str]:
    """Nomes citados como `@cofre:...@` — para conferir o que falta."""
    return _REF.findall(texto or "")
