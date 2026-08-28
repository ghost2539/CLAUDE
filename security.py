from __future__ import annotations
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import Request, HTTPException, Response
from fastapi.responses import JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_settings

_cfg = get_settings()

# ── Session management ──────────────────────────────────────────

_serializer = URLSafeTimedSerializer(_cfg.SESSION_SECRET, salt="portal-spare-v2")
SESSIONS: dict[str, dict] = {}


def create_session(data: dict) -> tuple[str, str]:
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = data
    cookie_value = _serializer.dumps(sid)
    return sid, cookie_value


def set_session_cookie(resp: Response, cookie_value: str) -> None:
    resp.set_cookie(
        "spare_session",
        cookie_value,
        httponly=True,
        samesite="lax",
        secure=bool(_cfg.SSL_CERTFILE),
        max_age=_cfg.SESSION_TTL,
        path="/",
    )


def delete_session(cookie_raw: str | None) -> None:
    if not cookie_raw:
        return
    try:
        sid = _serializer.loads(cookie_raw)
        SESSIONS.pop(sid, None)
    except Exception:
        pass


def get_session(req: Request, required: bool = True) -> dict | None:
    cookie = req.cookies.get("spare_session")
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


def require_permission(req: Request, module: str, action: str = "view") -> dict:
    sd = get_session(req)
    if sd.get("is_admin"):
        return sd
    pmap = sd.get("permission_map") or {}
    perms = pmap.get(module, {})
    key = {
        "view": "can_view",
        "create": "can_create",
        "edit": "can_edit",
        "export": "can_export",
        "admin": "can_admin",
    }[action]
    if not perms.get(key):
        raise HTTPException(403, "Permissão insuficiente.")
    return sd


def client_ip(req: Request) -> str:
    forwarded = req.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:80]
    return (req.client.host if req.client else "")[:80]


# ── Rate limiting (in-memory, per-IP) ───────────────────────────

class RateLimiter:
    def __init__(self):
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str, limit: int, window: int) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        self._hits[key] = [t for t in hits if now - t < window]
        if len(self._hits[key]) >= limit:
            return False
        self._hits[key].append(now)
        return True

    def cleanup(self) -> None:
        now = time.monotonic()
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


def check_rate_limit(req: Request, kind: str = "api") -> None:
    ip = client_ip(req)
    if kind == "login":
        limit, window = _parse_rate(_cfg.RATE_LIMIT_LOGIN)
    else:
        limit, window = _parse_rate(_cfg.RATE_LIMIT_API)
    key = f"{kind}:{ip}"
    if not _limiter.check(key, limit, window):
        raise HTTPException(429, "Muitas requisições. Tente novamente em breve.")


# ── Security headers middleware ─────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        if _cfg.SSL_CERTFILE:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


# ── Bot protection middleware ───────────────────────────────────

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


# ── Request size limiter ────────────────────────────────────────

class MaxBodyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > _cfg.UPLOAD_MAX_MB * 1024 * 1024:
            return JSONResponse(
                {"detail": f"Payload excede {_cfg.UPLOAD_MAX_MB}MB."},
                status_code=413,
            )
        return await call_next(request)
