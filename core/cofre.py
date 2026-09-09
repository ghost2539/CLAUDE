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

CAMINHO_MODULO = os.environ.get(
    "VCREPORTS_SECRETS_MODULE", "/usr/local/lib/vcreports/vcreports_secrets.py")
CAMINHO_ARQUIVO = os.environ.get(
    "VCREPORTS_SECRETS_FILE", "/etc/vcreports/.secrets.env")

# Diretório extra no sys.path (alternativa a recriar o venv).
_EXTRA = os.environ.get("VCREPORTS_SECRETS_PATH", "")
if _EXTRA and _EXTRA not in sys.path:
    sys.path.append(_EXTRA)

_modulo = None         
_modulo_via = ""        
_arquivo_cache: dict | None = None


def _funcao_do_modulo(mod):
    for nome in ("s", "secret", "vcreports_secret"):
        fn = getattr(mod, nome, None)
        if callable(fn):
            return fn
    return None


def _resolver_modulo():
    global _modulo, _modulo_via
    if _modulo is not None:
        return _modulo
    try:
        import vcreports_secrets as mod  # type: ignore
        _modulo, _modulo_via = mod, f"import direto ({getattr(mod, '__file__', '?')})"
        return _modulo
    except Exception:  # noqa: BLE001
        pass
    if os.path.isfile(CAMINHO_MODULO):
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("vcreports_secrets", CAMINHO_MODULO)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                _modulo, _modulo_via = mod, f"carregado por caminho ({CAMINHO_MODULO})"
                return _modulo
        except Exception as exc:  # noqa: BLE001
            _log.warning("Falha ao carregar o cofre por caminho (%s): %s", CAMINHO_MODULO, exc)
    return None


def _ler_arquivo_cofre() -> dict:
    global _arquivo_cache
    if _arquivo_cache is not None:
        return _arquivo_cache
    dados: dict[str, str] = {}
    try:
        with open(CAMINHO_ARQUIVO, "r", encoding="utf-8") as f:
            for bruto in f:
                linha = bruto.strip()
                if not linha or linha.startswith("#") or "=" not in linha:
                    continue
                k, _, v = linha.partition("=")
                k, v = k.strip(), v.strip()
                if len(v) >= 2 and ((v[0] == v[-1] == '"') or (v[0] == v[-1] == "'")):
                    v = v[1:-1]
                dados[k] = v
    except OSError:
        pass   # sem permissão ou inexistente: os outros caminhos assumem
    _arquivo_cache = dados
    return dados

COMANDO = os.environ.get("VCREPORTS_SECRETS_CMD", "")

USAR_CORPORATIVO = os.environ.get("COFRE_CORPORATIVO", "sim").strip().lower() \
    not in ("nao", "não", "0", "false", "off", "desligado")


def _por_comando(nome: str) -> str:
    if not COMANDO:
        return ""
    import shlex
    import subprocess
    try:
        argv = [p.replace("{chave}", nome) for p in shlex.split(COMANDO)]
        if not any("{chave}" in p for p in shlex.split(COMANDO)):
            argv.append(nome)
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as exc:  # noqa: BLE001
        _log.warning("Comando do cofre falhou para '%s': %s", nome, exc)
        return ""


def _corporativo(nome: str) -> str:
    if not USAR_CORPORATIVO:
        return ""
    v = _por_comando(nome)
    if v:
        return v
    mod = _resolver_modulo()
    if mod is not None:
        fn = _funcao_do_modulo(mod)
        if fn:
            try:
                v = fn(nome)
                if v:
                    return str(v)
            except Exception:  # noqa: BLE001
                pass
    return _ler_arquivo_cofre().get(nome, "")


def _somente_cofre(nome: str) -> str:  # noqa: D401

    if not USAR_CORPORATIVO:
        return ""
    arq = _ler_arquivo_cofre()
    if arq:
        return arq.get(nome, "")
    return _corporativo(nome)


def arquivo_legivel() -> bool:
    return bool(_ler_arquivo_cofre())


def chaves_corporativas() -> list[str]:  # noqa: D401
   
    if not USAR_CORPORATIVO:
        return []
    return sorted(_ler_arquivo_cofre().keys())


def corporativo_disponivel() -> bool:
    return diagnostico_corporativo()[0]


def diagnostico_corporativo() -> tuple[bool, str]:
    if not USAR_CORPORATIVO:
        return False, "desligado por configuração (COFRE_CORPORATIVO=nao)"
    if COMANDO:
        return True, f"comando configurado (não verificado): {COMANDO}"
    mod = _resolver_modulo()
    if mod is not None:
        fn = _funcao_do_modulo(mod)
        if not fn:
            return False, f"módulo encontrado ({_modulo_via}), mas sem função s()/secret()"
        chave = os.environ.get("COFRE_CHAVE_TESTE", "CORREIOS_USUARIO")
        try:
            valor = fn(chave)
        except Exception as exc:  # noqa: BLE001
            return False, f"{_modulo_via}, mas {fn.__name__}('{chave}') falhou: {exc}"
        if valor:
            return True, (f"{_modulo_via}, função {fn.__name__}() — leitura "
                          f"confirmada com a chave '{chave}'")
        return False, (
            f"{_modulo_via}, função {fn.__name__}(), mas não devolveu valor para "
            f"'{chave}'. Ou a chave tem outro nome (python3 scripts/cofre.py sondar), "
            f"ou este usuário não alcança o cofre. Para testar com outra chave: "
            f"COFRE_CHAVE_TESTE=NOME python3 scripts/cofre.py acesso"
        )
    if _ler_arquivo_cofre():
        return True, f"arquivo lido direto ({CAMINHO_ARQUIVO})"
    if os.path.exists(CAMINHO_ARQUIVO):
        return False, f"{CAMINHO_ARQUIVO} existe mas não é legível por este usuário"
    return False, (f"módulo não importável e {CAMINHO_MODULO} / {CAMINHO_ARQUIVO} "
                   f"não encontrados")


def acesso_ao_arquivo() -> dict:
    import grp
    import pwd

    info: dict = {
        "caminho": CAMINHO_ARQUIVO,
        "existe": False, "legivel": False,
        "dono": "", "grupo": "", "modo": "",
        "usuario_atual": "", "grupos_atuais": [],
        "erro": "",
    }
    try:
        info["usuario_atual"] = pwd.getpwuid(os.getuid()).pw_name
        info["grupos_atuais"] = sorted(
            g.gr_name for g in grp.getgrall() if info["usuario_atual"] in g.gr_mem
        ) + [grp.getgrgid(os.getgid()).gr_name]
    except Exception:  # noqa: BLE001
        pass

    try:
        st = os.stat(CAMINHO_ARQUIVO)
        info["existe"] = True
        info["modo"] = oct(stat.S_IMODE(st.st_mode))[2:]
        try:
            info["dono"] = pwd.getpwuid(st.st_uid).pw_name
        except Exception:  # noqa: BLE001
            info["dono"] = str(st.st_uid)
        try:
            info["grupo"] = grp.getgrgid(st.st_gid).gr_name
        except Exception:  # noqa: BLE001
            info["grupo"] = str(st.st_gid)
        info["legivel"] = os.access(CAMINHO_ARQUIVO, os.R_OK)
    except PermissionError as exc:
        info["erro"] = f"sem permissão nem para consultar o arquivo: {exc}"
    except FileNotFoundError:
        info["erro"] = "arquivo não encontrado neste caminho"
    except OSError as exc:
        info["erro"] = str(exc)
    return info


def onde_procura() -> list[str]:
    """Diretórios em que o Python procura o módulo."""
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
    for origem in (_corporativo, _local):
        v = origem(nome)
        if v:
            return v

    v = os.environ.get(nome, "")
    if v and MARCADOR not in v:
        return v
    return default


def fonte(nome: str) -> str:
    if USAR_CORPORATIVO and _corporativo(nome):
        return "cofre corporativo"
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
