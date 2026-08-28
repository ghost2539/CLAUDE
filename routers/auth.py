"""Authentication router — login, logout, session, password change."""
from __future__ import annotations

import os
from datetime import timedelta

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
        if v not in ("LOCAL", "AD"):
            raise ValueError("auth_type deve ser LOCAL ou AD.")
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

@router.post("/login")
def auth_login(body: LoginIn, req: Request):
    check_rate_limit(req, "login")

    login = body.username
    source = body.auth_type
    detail = ""

    with SessionLocal.begin() as s:
        u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
        try:
            if source == "AD":
                ebs_auth = ebs_service.login(login, body.password)
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

            # Auto-create user on first AD login
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

            # Ensure initial admin keeps admin flag
            if login == _cfg.INITIAL_ADMIN_LOGIN:
                u.is_admin = True

            u.last_access = utcnow()
            u.failed_attempts = 0
            u.locked_until = None
            u.auth_source = source

            perms = _get_perms(s, u)
            data = _user_payload(u, perms)

            sid, cookie_value = create_session({
                **data,
                "user_id": u.id,
                "ebs_auth": ebs_auth,
            })

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
        **{k: v for k, v in sd.items() if k not in ("ebs_auth", "permission_map")},
        "permission_map": sd.get("permission_map", {}),
        "visual_config": visual,
    }


@router.post("/logout")
def logout(req: Request):
    cookie = req.cookies.get("spare_session")
    delete_session(cookie)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("spare_session")
    return resp


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
