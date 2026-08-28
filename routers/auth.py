"""Authentication router — login, logout, session, password change."""
from __future__ import annotations

import logging
import os
from datetime import timedelta

log = logging.getLogger(__name__)

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func

import ebs_service
from config import get_settings
from database import (
    SessionLocal, User, Permission, AccessLog, Setting,
    hash_password, verify_password, utcnow,
)
from security import (
    get_session, client_ip, check_rate_limit,
    create_session, set_session_cookie, delete_session, SESSIONS,
)

_cfg = get_settings()
router = APIRouter(prefix="/api/auth", tags=["Autenticação"])

MODULES = _cfg.MODULES


# ── Pydantic models ───────────────────────────────────────────────

class LoginIn(BaseModel):
    username: str
    password: str
    auth_type: str = "local"

    @field_validator("username")
    @classmethod
    def strip_username(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Usuário obrigatório.")
        return v

    @field_validator("auth_type")
    @classmethod
    def normalize_auth_type(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ("LOCAL", "AD", "SN"):
            raise ValueError("auth_type deve ser LOCAL, AD ou SN.")
        return v


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("A nova senha deve possuir ao menos 8 caracteres.")
        return v


# ── Helpers ───────────────────────────────────────────────────────

def _get_perms(s, u: User) -> dict:
    """Read REAL permissions from the database.

    Admins get full access to all modules.  Non-admin users get only the
    permissions explicitly recorded in the ``permissions`` table.
    """
    if u.is_admin:
        return {
            m: {
                "can_view": True,
                "can_create": True,
                "can_edit": True,
                "can_export": True,
                "can_admin": True,
            }
            for m in MODULES
        }
    rows = s.scalars(select(Permission).where(Permission.user_id == u.id)).all()
    return {
        r.module: {
            "can_view": r.can_view,
            "can_create": r.can_create,
            "can_edit": r.can_edit,
            "can_export": r.can_export,
            "can_admin": r.can_admin,
        }
        for r in rows
    }


def _user_payload(u: User, perms: dict) -> dict:
    return {
        "username": u.login,
        "display_name": u.display_name,
        "role": "ADMIN" if u.is_admin else "USUÁRIO",
        "is_admin": u.is_admin,
        "auth_source": u.auth_source,
        "must_change_password": u.must_change_password,
        "permissions": (
            ["admin"] if u.is_admin
            else [k for k, v in perms.items() if v.get("can_view")]
        ),
        "permission_map": perms,
    }


# ── Endpoints ─────────────────────────────────────────────────────

def _sn_login(username: str, password: str):
    """Authenticate via ServiceNow SSO. Returns a requests.Session with
    authenticated cookies, or raises ValueError on failure."""
    try:
        import requests as _req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except ImportError:
        raise ValueError("Pacote 'requests' não instalado no servidor.")
    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        raise ValueError("Pacote 'beautifulsoup4' não instalado no servidor.")

    from routers.servicenow import SERVICENOW_BASE, SN_PROXY, _login_sso, _follow_form_redirects

    http_session = _req.Session()
    http_session.verify = False
    if SN_PROXY:
        http_session.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    http_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    try:
        ok = _login_sso(http_session, username, password, _req, _BS)
    except Exception as exc:
        log.error("SSO login error for %s: %s", username, exc)
        raise ValueError(f"Erro SSO: {exc}")
    if not ok:
        log.warning("SSO login returned False for %s", username)
        raise ValueError("Credenciais ServiceNow inválidas ou SSO indisponível.")
    cookies = {c.name: c.value for c in http_session.cookies}
    log.info("SSO login OK for %s, %d cookies", username, len(cookies))
    return cookies


@router.post("/login")
def auth_login(body: LoginIn, req: Request):
    check_rate_limit(req, "login")

    login = body.username
    source = body.auth_type
    detail = ""

    with SessionLocal.begin() as s:
        u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
        try:
            if source == "SN":
                sn_cookies = _sn_login(login, body.password)
                ebs_auth = None
            elif source == "AD":
                ebs_auth = ebs_service.login(login, body.password)
                sn_cookies = None
            else:
                # Local authentication
                if not u or not u.active:
                    raise ValueError("Usuário local inválido ou inativo.")
                if u.locked_until and u.locked_until > utcnow():
                    raise ValueError("Usuário temporariamente bloqueado.")
                if not verify_password(body.password, u.password_hash):
                    u.failed_attempts += 1
                    if u.failed_attempts >= 5:
                        u.locked_until = utcnow() + timedelta(minutes=15)
                        u.failed_attempts = 0
                    raise ValueError("Usuário ou senha inválidos.")
                ebs_auth = None
                sn_cookies = None

            # Auto-create user on first SN/AD login
            if not u:
                u = User(
                    login=login,
                    display_name=login,
                    auth_source=source,
                    active=True,
                    is_admin=(login == _cfg.INITIAL_ADMIN_LOGIN),
                )
                s.add(u)
                s.flush()

            if not u.active:
                raise ValueError("Usuário inativo.")

            # Check external access control (SN/AD users)
            if source in ("SN", "AD"):
                ac_row = s.get(Setting, "access_control")
                block_external = (ac_row.value if ac_row else {}).get("block_external", False)
                if block_external and not u.allowed:
                    raise ValueError("Acesso negado. Usuário não autorizado.")

            # Ensure initial admin keeps admin flag
            if login == _cfg.INITIAL_ADMIN_LOGIN:
                u.is_admin = True

            u.last_access = utcnow()
            u.failed_attempts = 0
            u.locked_until = None
            u.auth_source = source

            perms = _get_perms(s, u)
            data = _user_payload(u, perms)

            session_data = {
                **data,
                "user_id": u.id,
                "ebs_auth": ebs_auth,
            }
            if sn_cookies:
                session_data["sn_cookies"] = sn_cookies

            sid, cookie_value = create_session(session_data)

            s.add(AccessLog(
                login=login,
                auth_source=source,
                success=True,
                ip=client_ip(req),
            ))

            visual_row = s.get(Setting, "visual")
            visual = visual_row.value if visual_row else {}

            resp = JSONResponse({**data, "visual_config": visual})
            set_session_cookie(resp, cookie_value)
            return resp

        except Exception as e:
            detail = str(e)
            s.add(AccessLog(
                login=login,
                auth_source=source,
                success=False,
                ip=client_ip(req),
                detail=detail[:500],
            ))
            raise HTTPException(401, detail)


@router.get("/me")
def auth_me(req: Request):
    sd = get_session(req)
    with SessionLocal() as s:
        visual_row = s.get(Setting, "visual")
        visual = visual_row.value if visual_row else {}
    return {
        **{k: v for k, v in sd.items() if k not in ("ebs_auth", "permission_map", "sn_cookies")},
        "permission_map": sd.get("permission_map", {}),
        "visual_config": visual,
        "sn_active": bool(sd.get("sn_cookies")),
    }


@router.post("/logout")
def logout(req: Request):
    cookie = req.cookies.get("spare_session")
    delete_session(cookie)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("spare_session")
    return resp


class SNReloginIn(BaseModel):
    password: str


@router.get("/sn-session")
def sn_session_status(req: Request):
    """Check if the ServiceNow session is still valid."""
    sd = get_session(req)
    sn_cookies = sd.get("sn_cookies")
    if not sn_cookies:
        return {"active": False, "reason": "no_session"}
    try:
        import requests as _req
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        from routers.servicenow import SERVICENOW_BASE, SN_PROXY
        proxies = {"https": SN_PROXY, "http": SN_PROXY} if SN_PROXY else None
        r = _req.get(
            f"{SERVICENOW_BASE}/api/now/table/sys_user?sysparm_limit=1",
            cookies=sn_cookies,
            timeout=15,
            verify=False,
            allow_redirects=False,
            proxies=proxies,
        )
        if r.status_code == 200:
            return {"active": True}
        return {"active": False, "reason": "expired"}
    except Exception:
        return {"active": False, "reason": "error"}


@router.post("/sn-relogin")
def sn_relogin(body: SNReloginIn, req: Request):
    """Re-authenticate ServiceNow without full portal re-login.
    Uses the username from the current session."""
    sd = get_session(req)
    check_rate_limit(req, "login")
    username = sd["username"]
    try:
        sn_cookies = _sn_login(username, body.password)
    except ValueError as e:
        raise HTTPException(401, str(e))
    sd["sn_cookies"] = sn_cookies
    return {"ok": True, "sn_active": True}


@router.post("/change-password")
def change_password(body: PasswordChangeIn, req: Request):
    sd = get_session(req)
    with SessionLocal.begin() as s:
        u = s.get(User, sd["user_id"])
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")
        if u.auth_source != "LOCAL":
            raise HTTPException(400, "Disponível somente para Logon Local.")
        if not verify_password(body.current_password, u.password_hash):
            raise HTTPException(400, "Senha atual incorreta.")
        u.password_hash = hash_password(body.new_password)
        u.must_change_password = False
    return {"ok": True}
