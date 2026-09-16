"""Autenticação — login pelo SSO corporativo, sessão e logout.

Não existe senha no portal: a credencial é sempre a da rede (AD via OAM).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func

from config import get_settings
from db.portal import (
    SessionLocal, User, Permission, AccessLog, Setting, utcnow,
)
from core.security import (
    get_session, client_ip, check_rate_limit,
    create_session, set_session_cookie, delete_session,
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
        if v not in ("SN", "SSO"):
            raise ValueError("auth_type deve ser SN ou SSO.")
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


def perms_efetivas(s, u: User) -> dict:
    """Permissões da sessão: as da base mais as que a liberação da Consulta
    Times confere (a própria, a Consulta e o ServiceNow). É o que o login
    grava na sessão e o que Parâmetros reaplica quando algo muda."""
    perms = _get_perms(s, u)
    if u.is_admin:
        return perms
    try:
        import db.consulta_times as _dbct
        nivel = _dbct.nivel_do_login(u.login)
        if nivel:
            edit = nivel in ("edit", "admin")
            base = {"can_view": True, "can_create": edit, "can_edit": edit,
                    "can_export": True, "can_admin": nivel == "admin"}
            for m in ("consulta_times", "consulta", "servicenow"):
                if not perms.get(m, {}).get("can_view"):
                    perms[m] = dict(base)
    except Exception as exc:  # noqa: BLE001 — módulo fora do ar não trava o login
        log.warning("consulta_times: liberação não lida: %s", exc)
    return perms


def _user_payload(u: User, perms: dict) -> dict:
    return {
        "username": u.login,
        "display_name": u.display_name,
        "role": "ADMIN" if u.is_admin else "USUÁRIO",
        "is_admin": u.is_admin,
        "auth_source": u.auth_source,
        "permissions": (
            ["admin"] if u.is_admin
            else [k for k, v in perms.items() if v.get("can_view")]
        ),
        "permission_map": perms,
    }


TEMAS = ("claro", "escuro")


def _chave_preferencias(login: str) -> str:
    return f"pref:{(login or '').strip().lower()}"


def tema_preferido(s, login: str) -> str:
    """Tema claro/escuro salvo no perfil do usuário; sem registro, claro."""
    row = s.get(Setting, _chave_preferencias(login))
    tema = (row.value or {}).get("tema") if row else None
    return tema if tema in TEMAS else "claro"


# ── Endpoints ─────────────────────────────────────────────────────

def _sn_login(username: str, password: str):
    """Authenticate via ServiceNow SSO. Returns a requests.Session with
    authenticated cookies, or raises ValueError on failure."""
    try:
        import requests as _req
    except ImportError:
        raise ValueError("Pacote 'requests' não instalado no servidor.")
    try:
        from bs4 import BeautifulSoup as _BS
    except ImportError:
        raise ValueError("Pacote 'beautifulsoup4' não instalado no servidor.")

    from routers.servicenow import _login_sso, _sn_sessao

    # TLS verificado: a senha de rede só sai para um OAM com certificado válido.
    http_session = _sn_sessao()
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


class _LoginFailed(Exception):
    """Falha controlada de login. `pendente=True` marca casos em que o usuário
    autenticou na rede mas não está liberado — registramos como pendente para
    o admin poder liberar depois."""
    def __init__(self, detail: str, pendente: bool = False):
        super().__init__(detail)
        self.detail = detail
        self.pendente = pendente


def _registrar_falha(login: str, source: str, detail: str, ip: str, pendente: bool) -> None:
    """Registra a tentativa falha em transação PRÓPRIA (não é revertida junto
    com o login). Se `pendente`, garante o usuário na base como allowed=False,
    para aparecer em Parâmetros e poder ser liberado pelo admin."""
    try:
        with SessionLocal.begin() as s:
            s.add(AccessLog(
                login=login, auth_source=source, success=False,
                ip=ip, detail=detail[:500],
            ))
            if pendente:
                u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
                if not u:
                    s.add(User(
                        login=login, display_name=login, auth_source=source,
                        active=True, is_admin=False, allowed=False,
                    ))
    except Exception:  # noqa: BLE001 — registro de falha nunca derruba o login
        log.exception("Falha ao registrar tentativa de acesso de %s", login)

    # Espelha no Monitoramento e, quando a credencial era válida mas o usuário
    # não tem liberação, dispara o alerta por e-mail. Nada aqui pode interferir
    # no fluxo de login.
    try:
        import db.monitoramento as _dbmon
        _dbmon.registrar(
            severidade="alerta" if pendente else "erro",
            origem="acesso",
            alvo="acesso não autorizado" if pendente else "login recusado",
            usuario=login,
            detalhe=f"{detail} · autenticação {source} · IP {ip or '—'}",
        )
    except Exception:  # noqa: BLE001
        pass

    if pendente:
        try:
            from core.notificador import alertar_acesso_negado
            alertar_acesso_negado(login, ip, detail, source)
        except Exception:  # noqa: BLE001
            pass


@router.post("/login")
def auth_login(body: LoginIn, req: Request):
    # Dois baldes: por IP e por login tentado — trocar de IP não zera o
    # limite contra uma conta de rede específica.
    check_rate_limit(req, "login", chave_extra=f"user:{body.username.lower()}")

    login = body.username
    source = body.auth_type

    try:
        with SessionLocal.begin() as s:
            u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))

            # Login corporativo via loginsso (Oracle Access Manager).
            # Valida a credencial de rede; a senha permanece no AD.
            # Só entra quem foi previamente liberado por um administrador.
            # (SN é apenas um alias interno da mesma autenticação ServiceNow.)
            sn_cookies = _sn_login(login, body.password)
            ebs_auth = None

            # Auto-create user on first SN/SSO login.
            # Para SSO, o usuário nasce SEM liberação (allowed=False) e só
            # entra depois que um admin o liberar — exceto o admin inicial.
            if not u:
                is_initial_admin = (login.lower() == _cfg.INITIAL_ADMIN_LOGIN.lower())
                u = User(
                    login=login,
                    display_name=login,
                    auth_source=source,
                    active=True,
                    is_admin=is_initial_admin,
                    allowed=(False if source == "SSO" and not is_initial_admin else True),
                )
                s.add(u)
                s.flush()

            if not u.active:
                raise _LoginFailed("Usuário inativo.")

            # Check external access control
            if source == "SSO":
                # SSO sempre exige liberação prévia (só usuários permitidos entram).
                if not u.allowed and login.lower() != _cfg.INITIAL_ADMIN_LOGIN.lower():
                    raise _LoginFailed(
                        "Acesso ainda não liberado. Solicite a um administrador "
                        "a liberação do seu usuário de rede.", pendente=True)
            elif source == "SN":
                ac_row = s.get(Setting, "access_control")
                block_external = (ac_row.value if ac_row else {}).get("block_external", False)
                if block_external and not u.allowed:
                    raise _LoginFailed("Acesso negado. Usuário não autorizado.", pendente=True)

            # Ensure initial admin keeps admin flag
            if login == _cfg.INITIAL_ADMIN_LOGIN:
                u.is_admin = True

            u.last_access = utcnow()
            u.failed_attempts = 0
            u.locked_until = None
            u.auth_source = source

            perms = perms_efetivas(s, u)
            data = _user_payload(u, perms)
            data["tema"] = tema_preferido(s, u.login)

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
            set_session_cookie(resp, cookie_value, req)
            return resp

    except _LoginFailed as e:
        _registrar_falha(login, source, e.detail, client_ip(req), e.pendente)
        raise HTTPException(401, e.detail)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 — falha de credencial externa etc.
        _registrar_falha(login, source, str(e), client_ip(req), pendente=False)
        log.error("login: falha interna na autenticação: %s", e, exc_info=True)
        raise HTTPException(401, "Não foi possível autenticar agora. Tente de novo em instantes.")


@router.get("/me")
def auth_me(req: Request):
    sd = get_session(req)
    with SessionLocal() as s:
        visual_row = s.get(Setting, "visual")
        visual = visual_row.value if visual_row else {}
        tema = tema_preferido(s, sd.get("username", ""))
    return {
        **{k: v for k, v in sd.items() if k not in ("ebs_auth", "permission_map", "sn_cookies")},
        "permission_map": sd.get("permission_map", {}),
        "visual_config": visual,
        "sn_active": bool(sd.get("sn_cookies")),
        "tema": tema,
    }


class PreferenciasIn(BaseModel):
    tema: str

    @field_validator("tema")
    @classmethod
    def _tema(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v not in TEMAS:
            raise ValueError("tema deve ser 'claro' ou 'escuro'")
        return v


@router.put("/preferencias")
def preferencias_put(body: PreferenciasIn, req: Request):
    """Preferências do próprio usuário (hoje só o tema das telas)."""
    sd = get_session(req)
    login = (sd.get("username") or "").strip().lower()
    with SessionLocal() as s:
        chave = _chave_preferencias(login)
        row = s.get(Setting, chave)
        if row is None:
            row = Setting(key=chave, value={}, updated_by=login)
            s.add(row)
        row.value = {**(row.value or {}), "tema": body.tema}
        row.updated_by = login
        s.commit()
    return {"ok": True, "tema": body.tema}


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
        from routers.servicenow import SERVICENOW_BASE, _sn_sessao
        r = _sn_sessao(json=True).get(
            f"{SERVICENOW_BASE}/api/now/table/sys_user?sysparm_limit=1",
            cookies=sn_cookies,
            timeout=15,
            allow_redirects=False,
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


class MeuNomeIn(BaseModel):
    display_name: str


@router.post("/meu-nome")
def alterar_meu_nome(body: MeuNomeIn, req: Request):
    """Cada um escreve o próprio nome de exibição. Como o acesso é por SSO,
    o nome vindo do AD nem sempre é o que a pessoa usa no dia a dia."""
    sd = get_session(req)
    nome = " ".join(body.display_name.split())[:120]
    if len(nome) < 2:
        raise HTTPException(400, "Informe um nome com pelo menos 2 caracteres.")
    with SessionLocal.begin() as s:
        u = s.get(User, sd["user_id"])
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")
        u.display_name = nome
    sd["display_name"] = nome          # a sessão em memória acompanha
    return {"ok": True, "display_name": nome}
