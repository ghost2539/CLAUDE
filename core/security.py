from __future__ import annotations
import ipaddress
import secrets
import time
from collections import defaultdict

from fastapi import Request, HTTPException, Response
from fastapi.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_settings

_cfg = get_settings()

# ── Sessão ───────────────────────────────────────────────────────

COOKIE_SESSAO = "spare_session"
_serializer = URLSafeTimedSerializer(_cfg.SESSION_SECRET, salt="portal-spare-v2")
SESSIONS: dict[str, dict] = {}
# login (minúsculo) → sids vivos. É o que permite que uma mudança de
# permissão, desativação ou exclusão alcance quem já está logado.
_SESSOES_POR_LOGIN: dict[str, set[str]] = defaultdict(set)


def _login_de(data: dict) -> str:
    return (data.get("username") or "").strip().lower()


def create_session(data: dict) -> tuple[str, str]:
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = data
    _SESSOES_POR_LOGIN[_login_de(data)].add(sid)
    cookie_value = _serializer.dumps(sid)
    return sid, cookie_value


def _remover_sessao(sid: str) -> None:
    data = SESSIONS.pop(sid, None)
    if data is not None:
        _SESSOES_POR_LOGIN[_login_de(data)].discard(sid)


def encerrar_sessoes(login: str) -> int:
    """Derruba todas as sessões vivas de um login (desativação, exclusão)."""
    sids = list(_SESSOES_POR_LOGIN.get((login or "").strip().lower(), ()))
    for sid in sids:
        _remover_sessao(sid)
    return len(sids)


def atualizar_sessoes(login: str, **campos) -> int:
    """Aplica campos novos (permissões, flags) às sessões vivas de um login."""
    sids = _SESSOES_POR_LOGIN.get((login or "").strip().lower(), ())
    n = 0
    for sid in list(sids):
        data = SESSIONS.get(sid)
        if data is not None:
            data.update(campos)
            n += 1
    return n


def set_session_cookie(resp: Response, cookie_value: str, req: Request | None = None) -> None:
    resp.set_cookie(
        COOKIE_SESSAO,
        cookie_value,
        httponly=True,
        samesite="lax",
        secure=cookie_seguro(req),
        max_age=_cfg.SESSION_TTL,
        path="/",
    )


def delete_session(cookie_raw: str | None) -> None:
    if not cookie_raw:
        return
    try:
        sid = _serializer.loads(cookie_raw)
    except (BadSignature, SignatureExpired):
        return
    _remover_sessao(sid)


def get_session(req: Request, required: bool = True) -> dict | None:
    cookie = req.cookies.get(COOKIE_SESSAO)
    if not cookie:
        if required:
            raise HTTPException(401, "Sessão não autenticada.")
        return None
    try:
        sid = _serializer.loads(cookie, max_age=_cfg.SESSION_TTL)
    except (BadSignature, SignatureExpired):
        if required:
            raise HTTPException(401, "Sessão expirada.")
        return None
    data = SESSIONS.get(sid)
    if not data and required:
        raise HTTPException(401, "Sessão expirada.")
    return data


# ── Permissões ───────────────────────────────────────────────────

ACOES = ("view", "create", "edit", "export", "admin")
_CHAVE_ACAO = {a: f"can_{a}" for a in ACOES}


def acoes_do_modulo(module: str) -> tuple[str, ...]:
    """Ações que existem de fato no módulo (config.MODULE_ACTIONS); módulo
    fora da tabela só tem `view`."""
    return tuple(_cfg.MODULE_ACTIONS.get(module, ("view",)))


def require_permission(req: Request, module: str, action: str = "view") -> dict:
    sd = get_session(req)
    if sd.get("is_admin"):
        return sd
    pmap = sd.get("permission_map") or {}
    perms = pmap.get(module, {})
    if not perms.get(_CHAVE_ACAO[action]):
        raise HTTPException(403, "Permissão insuficiente.")
    return sd


def require_admin(req: Request) -> dict:
    """Administrador do portal (`is_admin`). Gerir usuários, liberações e o
    próprio conjunto de administradores é só daqui — `can_admin` de um módulo
    nunca concede isto."""
    sd = get_session(req)
    if not sd.get("is_admin"):
        raise HTTPException(403, "Ação restrita a administradores do portal.")
    return sd


def admin_geral_login() -> str:
    return (_cfg.ADMIN_GERAL_LOGIN or _cfg.INITIAL_ADMIN_LOGIN or "").strip().lower()


def is_admin_geral(sd: dict | None) -> bool:
    """Admin geral: o login configurado. Sem login configurado, qualquer admin."""
    if not sd:
        return False
    alvo = admin_geral_login()
    if not alvo:
        return bool(sd.get("is_admin"))
    return (sd.get("username") or "").strip().lower() == alvo


def require_admin_geral(req: Request) -> dict:
    sd = get_session(req)
    if not is_admin_geral(sd):
        raise HTTPException(403, "Somente o administrador geral pode alterar isto.")
    return sd


# ── Origem do pedido: proxies confiáveis, IP real, esquema ──────


def _redes_confiaveis() -> list:
    redes = []
    for item in (_cfg.TRUSTED_PROXIES or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            redes.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return redes


_REDES_CONFIAVEIS = _redes_confiaveis()


def vem_de_proxy_confiavel(req: Request) -> bool:
    host = req.client.host if req.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in rede for rede in _REDES_CONFIAVEIS)


def client_ip(req: Request) -> str:
    """IP de quem pediu. X-Forwarded-For só vale vindo de um proxy listado
    em TRUSTED_PROXIES; de qualquer outro cliente é texto forjável."""
    if vem_de_proxy_confiavel(req):
        forwarded = req.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:80]
    return (req.client.host if req.client else "")[:80]


def esquema_efetivo(req: Request) -> str:
    """http ou https do ponto de vista do navegador (TLS no uvicorn ou
    X-Forwarded-Proto de um proxy confiável)."""
    if vem_de_proxy_confiavel(req):
        proto = req.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if proto in ("http", "https"):
            return proto
    return req.url.scheme


def cookie_seguro(req: Request | None) -> bool:
    modo = _cfg.SESSION_COOKIE_SECURE
    if modo in ("true", "1", "sim", "on"):
        return True
    if modo in ("false", "0", "nao", "não", "off"):
        return False
    if _cfg.SSL_CERTFILE:
        return True
    return req is not None and esquema_efetivo(req) == "https"


# ── Rate limiting (em memória, por chave) ────────────────────────

class RateLimiter:
    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._desde_limpeza = 0

    def check(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        self._hits[key] = [t for t in hits if now - t < window]
        # Sem isto o dicionário só cresce: cada IP forjado vira uma chave.
        self._desde_limpeza += 1
        if self._desde_limpeza >= 500 or len(self._hits) > 20000:
            self.cleanup()
        if len(self._hits[key]) >= limit:
            return False
        self._hits[key].append(now)
        return True

    def cleanup(self) -> None:
        now = time.monotonic()
        self._desde_limpeza = 0
        stale = [k for k, v in self._hits.items() if not v or now - v[-1] > 300]
        for k in stale:
            del self._hits[k]


_limiter = RateLimiter()


def _parse_rate(spec: str) -> tuple[int, int]:
    parts = spec.strip().split("/")
    count = int(parts[0])
    unit = parts[1].lower() if len(parts) > 1 else "minute"
    window = {"second": 1, "minute": 60, "hour": 3600}.get(unit, 60)
    return count, window


def check_rate_limit(req: Request, kind: str = "api", chave_extra: str = "") -> None:
    """Limite por IP; `chave_extra` (ex.: o login tentado) acrescenta um
    segundo balde, para o limite valer mesmo com IPs variados."""
    ip = client_ip(req)
    if kind == "login":
        limit, window = _parse_rate(_cfg.RATE_LIMIT_LOGIN)
    else:
        limit, window = _parse_rate(_cfg.RATE_LIMIT_API)
    chaves = [f"{kind}:{ip}"]
    if chave_extra:
        chaves.append(f"{kind}:{chave_extra}")
    for chave in chaves:
        if not _limiter.check(chave, limit, window):
            raise HTTPException(429, "Muitas requisições. Tente novamente em breve.")


# ── Cabeçalhos de segurança ──────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if _cfg.SSL_CERTFILE or esquema_efetivo(request) == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        return response


# ── Bloqueio de scanners por User-Agent ──────────────────────────

class BotProtectionMiddleware(BaseHTTPMiddleware):
    SUSPICIOUS_AGENTS = {
        "sqlmap", "nikto", "nessus", "dirbuster", "gobuster",
        "wfuzz", "hydra", "burp", "zap",
    }

    async def dispatch(self, request: Request, call_next):
        ua = (request.headers.get("user-agent") or "").lower()
        if any(bot in ua for bot in self.SUSPICIOUS_AGENTS):
            return JSONResponse({"detail": "Acesso negado."}, status_code=403)
        return await call_next(request)


# ── Tamanho máximo do corpo ──────────────────────────────────────

class MaxBodyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _cfg.UPLOAD_MAX_MB * 1024 * 1024:
            return JSONResponse(
                {"detail": f"Payload excede {_cfg.UPLOAD_MAX_MB}MB."},
                status_code=413,
            )
        return await call_next(request)
