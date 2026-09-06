"""Cofre local de segredos.

Por que existe: o arquivo de ambiente é lido por todo mundo que abrir o
`environment`, entra em backup e é fácil de copiar por engano. O cofre tira
usuário e senha de lá e deixa no ambiente só a configuração que não é
segredo (host, porta, URL, flags).

Ordem de busca de um segredo — o primeiro que responder vence:

1. **Cofre corporativo** (`vcreports_secrets`), quando existir no servidor;
2. **Cofre local**, cifrado, nesta máquina;
3. **Variável de ambiente** de mesmo nome (compatibilidade e emergência).

O que o cofre local protege — e o que NÃO protege
-------------------------------------------------
Protege contra o que acontece de verdade no dia a dia: alguém que abre o
`environment`, um backup que vaza, um `git` que leva o arquivo junto, outro
usuário do servidor lendo a pasta.

NÃO protege contra quem consegue executar código **como o mesmo usuário**
que roda o portal. A aplicação precisa abrir o cofre sozinha, sem ninguém
digitar senha na subida; então a chave tem de estar ao alcance dela — e,
portanto, ao alcance de quem for esse usuário. Root também lê tudo.

Ou seja: o cofre local vale enquanto as permissões de arquivo separarem as
pessoas de verdade. Se todos entram com o mesmo usuário, ele é cosmético.
Segredo com valor alto deve morar no cofre corporativo.
"""
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

# Fica FORA do diretório da aplicação: assim um `git`, um pacote de deploy
# ou uma cópia da pasta do sistema não levam o cofre junto.
DIR = Path(os.environ.get(
    "PORTAL_COFRE_DIR",
    Path.home() / ".config" / "portal-spare",
))
ARQ_CHAVE = DIR / "cofre.key"
ARQ_COFRE = DIR / "cofre.json"

# Sintaxe do marcador: @cofre:NOME@
#
# NÃO usar ${cofre:NOME}: o arquivo de ambiente é carregado com `. arquivo`
# pelos scripts de operação, e o bash trata ${...} como expansão dele — a
# variável chegaria VAZIA, sem erro nenhum. O `@` o shell não toca.
_REF = re.compile(r"@cofre:([A-Za-z0-9_.-]{1,64})@")
MARCADOR = "@cofre:"


# ── Cofre corporativo ───────────────────────────────────────────────────
# O loader oficial fica em /usr/local/lib/vcreports/vcreports_secrets.py e é
# importável no Python do SISTEMA por um .pth. Um venv isolado não o alcança.
# A API pública é `s(chave, default)`, com alias `secret` — NÃO existe
# `vcreports_secret`.
#
# Por baixo, ele apenas lê o arquivo /etc/vcreports/.secrets.env (KEY=VALOR).
# Por isso tentamos, nesta ordem, até algo responder:
#   1. o módulo já importável;
#   2. o módulo carregado pelo caminho absoluto (resolve o venv isolado);
#   3. o arquivo do cofre lido direto (resolve até sem o módulo).

CAMINHO_MODULO = os.environ.get(
    "VCREPORTS_SECRETS_MODULE", "/usr/local/lib/vcreports/vcreports_secrets.py")
CAMINHO_ARQUIVO = os.environ.get(
    "VCREPORTS_SECRETS_FILE", "/etc/vcreports/.secrets.env")

# Diretório extra no sys.path (alternativa a recriar o venv).
_EXTRA = os.environ.get("VCREPORTS_SECRETS_PATH", "")
if _EXTRA and _EXTRA not in sys.path:
    sys.path.append(_EXTRA)

_modulo = None          # módulo resolvido
_modulo_via = ""        # como foi resolvido, para diagnóstico
_arquivo_cache: dict | None = None


def _funcao_do_modulo(mod):
    """O loader expõe `s`; `secret` é alias. Aceitamos também o nome antigo,
    caso alguma versão o tenha."""
    for nome in ("s", "secret", "vcreports_secret"):
        fn = getattr(mod, nome, None)
        if callable(fn):
            return fn
    return None


def _resolver_modulo():
    """Módulo do cofre, importado normalmente ou pelo caminho absoluto."""
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
    """Lê /etc/vcreports/.secrets.env no mesmo formato do loader oficial.

    Último recurso — e o único que diz com certeza o que está NO COFRE, sem
    a mistura com variáveis de ambiente que o `s()` faz.
    """
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


def _corporativo(nome: str) -> str:
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


def _somente_cofre(nome: str) -> str:
    """Valor que está DE FATO no arquivo do cofre.

    O `s()` oficial cai para `os.environ` quando a chave não existe; para
    sondar, isso daria falso positivo. Quando o arquivo não é legível,
    voltamos ao caminho normal e avisamos no diagnóstico.
    """
    arq = _ler_arquivo_cofre()
    if arq:
        return arq.get(nome, "")
    return _corporativo(nome)


def arquivo_legivel() -> bool:
    return bool(_ler_arquivo_cofre())


def chaves_corporativas() -> list[str]:
    """Nomes das chaves do cofre corporativo, quando o arquivo é legível.

    Resolve o problema de "existe mas não sei o nome": em vez de adivinhar,
    lê a lista. Valores nunca saem daqui.
    """
    return sorted(_ler_arquivo_cofre().keys())


def corporativo_disponivel() -> bool:
    return diagnostico_corporativo()[0]


def diagnostico_corporativo() -> tuple[bool, str]:
    """(disponível, motivo). O motivo é o que permite consertar sem chutar."""
    mod = _resolver_modulo()
    if mod is not None:
        fn = _funcao_do_modulo(mod)
        if fn:
            return True, f"{_modulo_via}, função {fn.__name__}()"
        return False, f"módulo encontrado ({_modulo_via}), mas sem função s()/secret()"
    if _ler_arquivo_cofre():
        return True, f"arquivo lido direto ({CAMINHO_ARQUIVO})"
    if os.path.exists(CAMINHO_ARQUIVO):
        return False, f"{CAMINHO_ARQUIVO} existe mas não é legível por este usuário"
    return False, (f"módulo não importável e {CAMINHO_MODULO} / {CAMINHO_ARQUIVO} "
                   f"não encontrados")


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
    """Cofre e chave são só do dono. Permissão frouxa aqui anula o resto."""
    try:
        caminho.chmod(0o600)
    except OSError:
        pass


def permissoes_ok() -> tuple[bool, list[str]]:
    """Confere se ninguém além do dono lê o cofre. Usado pelo CLI e pelo
    monitoramento."""
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
    """Fernet com a chave do arquivo. None quando a lib falta ou quebra.

    O import de `cryptography` pode falhar com PanicException (binding Rust),
    que não é `Exception` — daí a captura ampla.
    """
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
    """Alternativa quando não há `cryptography`: chave derivada do arquivo.

    Mais fraca que Fernet (sem autenticação da mensagem), mas ainda impede a
    leitura direta. Em produção a lib está no requirements.txt.
    """
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
    # Grava num temporário e troca: um desligamento no meio da escrita não
    # deixa o cofre truncado.
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
    """O segredo `nome`: cofre corporativo, cofre local, ambiente, default."""
    for fonte in (_corporativo, _local):
        v = fonte(nome)
        if v:
            return v
    return os.environ.get(nome, default)


def fonte(nome: str) -> str:
    """De onde `nome` viria agora — para diagnóstico, sem revelar o valor."""
    if _corporativo(nome):
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
    """Só os NOMES. Valor de cofre não se lista."""
    return sorted(_carregar().keys())


def algoritmo() -> str:
    """Qual cifra o cofre está usando agora.

    'fernet' é o esperado (a lib está no requirements.txt). 'derivada' é o
    plano B, mais fraco — sem autenticação da mensagem —, e serve só para o
    cofre não quebrar num ambiente onde a `cryptography` não carrega. Quando
    aparecer 'derivada' em produção, é problema a resolver, não detalhe.
    """
    return "fernet" if _fernet() else "derivada"


def expandir(texto: str) -> str:
    """Troca `@cofre:NOME@` pelo segredo.

    É o que permite manter no `environment` uma linha completa e legível,
    sem a senha:

        DATABASE_URL=postgresql+psycopg2://portal:@cofre:DB_SENHA@@host:5432/base
    """
    if not texto or MARCADOR not in texto:
        return texto
    return _REF.sub(lambda m: obter(m.group(1)), texto)


def referencias(texto: str) -> list[str]:
    """Nomes citados como `@cofre:...@` — para conferir o que falta."""
    return _REF.findall(texto or "")
