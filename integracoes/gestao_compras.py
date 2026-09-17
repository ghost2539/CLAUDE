"""Cliente da API do módulo Gestão de Compras (suporte.lojasrenner.com.br).

Por que existe: o serviço do portal não alcança o cofre corporativo
(`/etc/vcreports/.secrets.env`), e nenhuma linguagem muda isso — é permissão
de usuário. Mas o módulo `/gestao_compras` roda sob o Apache, que alcança, e
já expõe por HTTP exatamente as consultas de PO e projetos do EBS que o
portal precisa. Então o portal chama ESSE módulo, como cliente, e o cofre
fica onde está, lido por quem pode lê-lo. É o mesmo desenho do `/ebs/api`.

Contrato do lado de lá (api/auth.php e api/oracle.php do módulo):

  POST api/auth.php?action=login   {"username","password"} → sessão (cookie)
  GET  api/auth.php?action=check   → {"authenticated": true, "user": {...}}
  GET  api/oracle.php?action=<ação>&<parâmetros> → {"data": [...]} | {"error"}

Sem sessão, `require_login()` devolve 401 em JSON SÓ quando a chamada se
apresenta como XMLHttpRequest; senão redireciona para a página de login. Por
isso todo pedido daqui leva `X-Requested-With: XMLHttpRequest`.

Credencial: `GESTAO_COMPRAS_USER` / `GESTAO_COMPRAS_PASS` no cofre (pelo
loader, como tudo). Sem elas, cai na conta de serviço do EBS público
(`EBS_PUBLIC_USER` / `EBS_PUBLIC_PASS`): o módulo autentica por LDAP, então
a mesma conta de domínio costuma servir. A senha nunca é logada.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

import requests

from config import get_settings

_log = logging.getLogger("gestao_compras")

# Ações que o api/oracle.php aceita e os parâmetros de cada uma, com o nome
# que ELE espera na query string. Fora desta lista nada é enviado: a tela
# não vira um encaminhador de URL para o servidor de lá.
ACOES: dict[str, tuple[str, ...]] = {
    "saldo": ("project",),
    "po": ("project",),
    "rc": ("project",),
    "resumo": ("project",),
    "acordos": ("days", "org"),
    "vendors": (),
    "vendor_items": ("vendor",),
    "busca_po": ("po", "line"),
    "catalogo": (),
    "forecast": ("projects",),
}
# Parâmetros sem os quais o módulo responde 400 — melhor barrar aqui, com
# mensagem em português, do que devolver o erro em inglês dele.
OBRIGATORIOS: dict[str, tuple[str, ...]] = {
    "saldo": ("project",), "po": ("project",), "rc": ("project",),
    "resumo": ("project",), "vendor_items": ("vendor",), "busca_po": ("po",),
    "forecast": ("projects",),
}

# Chaves de cofre, na ordem de preferência. Específica primeiro; a conta de
# serviço do EBS público é o padrão razoável porque o módulo autentica por
# LDAP e é a mesma conta de domínio.
CHAVES_USUARIO = ("GESTAO_COMPRAS_USER", "EBS_PUBLIC_USER", "EBS_PUBLIC_USERNAME")
CHAVES_SENHA = ("GESTAO_COMPRAS_PASS", "EBS_PUBLIC_PASS", "EBS_PUBLIC_PASSWORD")

# Sessão do lado de lá dura o que o PHP quiser; renovar a cada 10 minutos
# evita a maioria dos 401, e o 401 que sobrar é tratado com um novo login.
_TTL_SESSAO = 600


class GestaoComprasErro(RuntimeError):
    """Falha legível, sem segredo dentro. `status` é o HTTP do lado de lá."""

    def __init__(self, mensagem: str, status: int = 502):
        super().__init__(mensagem)
        self.status = status


def _credencial(chaves: tuple[str, ...]) -> tuple[str, str]:
    """Primeiro valor resolvido e de qual chave veio (nunca o valor da senha
    para fora daqui — quem chama decide o que mostrar)."""
    from core.cofre import obter
    for chave in chaves:
        valor = obter(chave, "")
        if valor:
            return valor, chave
    return "", ""


def credenciais_configuradas() -> dict:
    """Só o retrato: usuário (valor) e senha (sim/não), e de qual chave."""
    from core.cofre import fonte
    usuario, chave_u = _credencial(CHAVES_USUARIO)
    senha, chave_s = _credencial(CHAVES_SENHA)
    return {
        "usuario": usuario,
        "usuario_chave": chave_u,
        "usuario_fonte": fonte(chave_u) if chave_u else "não definido",
        "senha_definida": bool(senha),
        "senha_chave": chave_s,
        "senha_fonte": fonte(chave_s) if chave_s else "não definido",
    }


def _nova_sessao() -> requests.Session:
    cfg = get_settings()
    s = requests.Session()
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
        "User-Agent": "portal-spare/gestao-compras",
    })
    if cfg.GESTAO_COMPRAS_PROXY:
        s.proxies.update({"http": cfg.GESTAO_COMPRAS_PROXY, "https": cfg.GESTAO_COMPRAS_PROXY})
    else:
        # Proxy declarado vazio é decisão: não herdar o do perfil do servidor.
        s.trust_env = False
    if not cfg.GESTAO_COMPRAS_VERIFY:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return s


def _url(caminho: str) -> str:
    return get_settings().GESTAO_COMPRAS_URL + caminho.lstrip("/")


def _json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def login(usuario: str, senha: str) -> tuple[requests.Session, dict]:
    """Abre sessão no módulo. Devolve a sessão (com o cookie) e o usuário."""
    cfg = get_settings()
    s = _nova_sessao()
    try:
        r = s.post(_url("api/auth.php?action=login"),
                   json={"username": usuario, "password": senha},
                   timeout=cfg.GESTAO_COMPRAS_TIMEOUT, verify=cfg.GESTAO_COMPRAS_VERIFY,
                   allow_redirects=False)
    except requests.RequestException as exc:
        raise GestaoComprasErro(f"Sem resposta de {cfg.GESTAO_COMPRAS_URL}: "
                                f"{type(exc).__name__}") from exc
    corpo = _json(r) or {}
    if r.status_code == 401:
        raise GestaoComprasErro(
            "O módulo Gestão de Compras recusou a credencial "
            f"(usuário '{usuario}').", 401)
    if r.status_code >= 400 or not corpo.get("success"):
        raise GestaoComprasErro(
            f"Login no Gestão de Compras falhou: HTTP {r.status_code} "
            f"{corpo.get('error') or ''}".strip(), r.status_code or 502)
    return s, corpo.get("user") or {}


# ── Sessão compartilhada do processo ─────────────────────────────────────
_lock = threading.Lock()
_cache: dict[str, Any] = {"sessao": None, "usuario": {}, "expira": 0.0}


def _sessao(forcar: bool = False) -> requests.Session:
    agora = time.time()
    with _lock:
        if not forcar and _cache["sessao"] is not None and _cache["expira"] > agora:
            return _cache["sessao"]
        usuario, chave_u = _credencial(CHAVES_USUARIO)
        senha, _ = _credencial(CHAVES_SENHA)
        if not usuario or not senha:
            faltam = [n for n, v in (("usuário", usuario), ("senha", senha)) if not v]
            raise GestaoComprasErro(
                "Credencial do Gestão de Compras ausente (" + " e ".join(faltam) +
                "). Grave no cofre: python3 scripts/cofre.py definir "
                "GESTAO_COMPRAS_USER e GESTAO_COMPRAS_PASS.", 503)
        s, quem = login(usuario, senha)
        _cache.update(sessao=s, usuario=quem, expira=agora + _TTL_SESSAO)
        _log.info("Sessão no Gestão de Compras aberta como %s (chave %s)",
                  quem.get("username") or usuario, chave_u)
        return s


def esquecer_sessao() -> None:
    with _lock:
        _cache.update(sessao=None, usuario={}, expira=0.0)


def verificar() -> dict:
    """Login + `auth.php?action=check`: prova que a credencial vale lá."""
    cfg = get_settings()
    s = _sessao(forcar=True)
    r = s.get(_url("api/auth.php?action=check"),
              timeout=cfg.GESTAO_COMPRAS_TIMEOUT, verify=cfg.GESTAO_COMPRAS_VERIFY,
              allow_redirects=False)
    corpo = _json(r) or {}
    if r.status_code != 200 or not corpo.get("authenticated"):
        raise GestaoComprasErro(
            f"Login aceito, mas a sessão não se sustentou (HTTP {r.status_code}).",
            r.status_code or 502)
    return {"url": cfg.GESTAO_COMPRAS_URL, "usuario": corpo.get("user") or {}}


def consultar(acao: str, **parametros: Any) -> list[dict]:
    """Uma ação do api/oracle.php. Devolve as linhas; erro vira exceção legível."""
    if acao not in ACOES:
        raise GestaoComprasErro(
            f"Ação desconhecida: {acao}. Disponíveis: " + ", ".join(ACOES), 422)
    for obrig in OBRIGATORIOS.get(acao, ()):
        if parametros.get(obrig) in (None, ""):
            raise GestaoComprasErro(f"A ação '{acao}' exige o parâmetro '{obrig}'.", 422)
    # Só o que a ação conhece, e nada vazio: o PHP trata "line=" como zero.
    query = {"action": acao}
    for nome in ACOES[acao]:
        valor = parametros.get(nome)
        if valor not in (None, ""):
            query[nome] = valor

    cfg = get_settings()

    def _tentar(s: requests.Session) -> requests.Response:
        try:
            return s.get(_url("api/oracle.php"), params=query,
                         timeout=cfg.GESTAO_COMPRAS_TIMEOUT,
                         verify=cfg.GESTAO_COMPRAS_VERIFY, allow_redirects=False)
        except requests.Timeout as exc:
            raise GestaoComprasErro(
                f"O Gestão de Compras não respondeu em {cfg.GESTAO_COMPRAS_TIMEOUT} s "
                f"(ação {acao}).", 504) from exc
        except requests.RequestException as exc:
            raise GestaoComprasErro(
                f"Sem resposta de {cfg.GESTAO_COMPRAS_URL}: {type(exc).__name__}") from exc

    r = _tentar(_sessao())
    # Sessão caiu do lado de lá (401 em JSON, ou o redirecionamento para o
    # login quando o cabeçalho XHR não convenceu): um novo login e mais uma.
    if r.status_code in (401, 302, 303):
        esquecer_sessao()
        r = _tentar(_sessao(forcar=True))

    corpo = _json(r)
    if corpo is None:
        raise GestaoComprasErro(
            f"Resposta do Gestão de Compras não é JSON (HTTP {r.status_code}).",
            r.status_code or 502)
    if r.status_code >= 400 or "error" in corpo:
        raise GestaoComprasErro(str(corpo.get("error") or f"HTTP {r.status_code}"),
                                r.status_code if r.status_code >= 400 else 502)
    dados = corpo.get("data")
    if isinstance(dados, list):
        return dados
    # `resumo` devolve UM objeto, não uma lista; para quem consome, é uma
    # linha. Qualquer outra coisa (null, string) é "nada veio".
    return [dados] if isinstance(dados, dict) else []
