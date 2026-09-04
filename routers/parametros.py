"""Parâmetros (admin/settings) router — locations, classifications,
hourly rate, visual/tv config, permissions, users, base-local upload.
"""
from __future__ import annotations

import io
import os
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func, text

from config import get_settings
from database import (
    SessionLocal, Setting, Classification, StorageLocation,
    User, Permission, LotSequence, LocalAsset, LoadHistory,
    AccessLog, hash_password,
)
from security import get_session, require_permission, client_ip, check_rate_limit

_cfg = get_settings()
MODULES = _cfg.MODULES
router = APIRouter(prefix="/api/parametros", tags=["Parâmetros"])


# ── Pydantic models ───────────────────────────────────────────────

class LocationIn(BaseModel):
    nome: str
    descricao: str = ""
    ativo: bool = True

    @field_validator("nome")
    @classmethod
    def strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nome obrigatório.")
        if len(v) > 120:
            raise ValueError("Nome muito longo (máx. 120 caracteres).")
        return v


class ClassificationIn(BaseModel):
    padrao_descricao: str
    empresa: str = ""
    categoria: str
    modelo: str

    @field_validator("padrao_descricao", "categoria", "modelo")
    @classmethod
    def strip_required(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Campo obrigatório.")
        return v


class ClassificationEditIn(ClassificationIn):
    ativo: bool = True


class HourlyRateIn(BaseModel):
    valor: float

    @field_validator("valor")
    @classmethod
    def positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Valor deve ser positivo.")
        return v


class UserCreateIn(BaseModel):
    login: str
    display_name: str = ""
    password: str = ""
    auth_source: str = "LOCAL"
    is_admin: bool = False

    @field_validator("login")
    @classmethod
    def strip_login(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Login obrigatório.")
        if len(v) > 80:
            raise ValueError("Login muito longo (máx. 80 caracteres).")
        return v

    @field_validator("auth_source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in ("LOCAL", "AD", "SN", "SSO"):
            raise ValueError("auth_source deve ser LOCAL, AD, SN ou SSO.")
        return v


# ── Helpers ───────────────────────────────────────────────────────

def _get_perms(s, u: User) -> dict:
    """Read REAL permissions from the database (mirrors auth.py)."""
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


# ── Storage Locations ─────────────────────────────────────────────

@router.get("/locais")
def locations_list(req: Request):
    get_session(req)
    with SessionLocal() as s:
        rows = s.scalars(select(StorageLocation).order_by(StorageLocation.name)).all()
        return {
            "locais": [
                {
                    "id": x.id,
                    "nome": x.name,
                    "descricao": x.description,
                    "ativo": x.active,
                }
                for x in rows
            ]
        }


@router.post("/locais")
def location_add(body: LocationIn, req: Request):
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        s.add(StorageLocation(
            name=body.nome,
            description=body.descricao,
            active=body.ativo,
        ))
    return {"ok": True}


@router.put("/locais/{id}")
def location_edit(id: int, body: LocationIn, req: Request):
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        x = s.get(StorageLocation, id)
        if not x:
            raise HTTPException(404, "Local não encontrado.")
        x.name = body.nome
        x.description = body.descricao
        x.active = body.ativo
    return {"ok": True}


# ── Classifications ───────────────────────────────────────────────

@router.get("/classificacoes")
def classification_list(req: Request):
    get_session(req)
    with SessionLocal() as s:
        rows = s.scalars(select(Classification).order_by(Classification.id.desc())).all()
        return {
            "regras": [
                {
                    "id": x.id,
                    "padrao_descricao": x.description_pattern,
                    "empresa": x.company,
                    "categoria": x.category,
                    "modelo": x.model,
                    "ativo": x.active,
                }
                for x in rows
            ]
        }


@router.post("/classificacoes")
def classification_add(body: ClassificationIn, req: Request):
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        s.add(Classification(
            description_pattern=body.padrao_descricao,
            company=body.empresa.strip(),
            category=body.categoria,
            model=body.modelo,
            active=True,
        ))
    return {"ok": True}


@router.put("/classificacoes/{id}")
def classification_edit(id: int, body: ClassificationEditIn, req: Request):
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        x = s.get(Classification, id)
        if not x:
            raise HTTPException(404, "Classificação não encontrada.")
        x.description_pattern = body.padrao_descricao
        x.company = body.empresa.strip()
        x.category = body.categoria
        x.model = body.modelo
        x.active = body.ativo
    return {"ok": True}


@router.delete("/classificacoes/{id}")
def classification_delete(id: int, req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Classification, id)
        if not x:
            raise HTTPException(404, "Classificação não encontrada.")
        s.delete(x)
    return {"ok": True}


# ── Hourly Rate ───────────────────────────────────────────────────

@router.get("/valor-hora")
def hourly_get(req: Request):
    get_session(req)
    with SessionLocal() as s:
        row = s.get(Setting, "hourly_rate")
        return {"valor": (row.value if row else {}).get("value", 150)}


@router.put("/valor-hora")
def hourly_set(body: HourlyRateIn, req: Request):
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Setting, "hourly_rate")
        if not x:
            x = Setting(key="hourly_rate")
            s.add(x)
        x.value = {"value": body.valor}
        x.updated_by = sd["username"]
    return {"ok": True}


# ── Generic config (visual / tv) ─────────────────────────────────

@router.get("/config/{key}")
def get_setting(key: str, req: Request):
    get_session(req)
    with SessionLocal() as s:
        x = s.get(Setting, key)
        return x.value if x else {}


@router.put("/config/{key}")
def put_setting(key: str, payload: dict, req: Request):
    sd = get_session(req)
    if key in ("visual", "tv", "correios") and not sd.get("is_admin"):
        raise HTTPException(403, "Permissão insuficiente.")
    with SessionLocal.begin() as s:
        x = s.get(Setting, key)
        if not x:
            x = Setting(key=key)
            s.add(x)
        x.value = payload
        x.updated_by = sd["username"]
    return {"ok": True}


@router.post("/visual/reset")
def visual_reset(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Setting, "visual")
        if x:
            x.value = {
                "nome_app": "Portal de Operações - SPARE",
                "subtitulo": "Operações de ativos",
                "login_title": "Portal de Operações - SPARE",
                "footer": "SPARE - Portal de Operações",
                "fonte": "Inter",
                "cor_primaria": "#AB4807",
                "cor_fundo": "#090B0D",
                "cor_painel": "#111419",
                "cor_texto": "#E8E8E8",
                "cor_destaque": "#C79105",
            }
    return {"ok": True}


@router.post("/visual/logo")
def logo_upload(req: Request, logo: UploadFile = File(...)):
    require_permission(req, "parametros", "admin")
    if not (logo.filename or "").lower().endswith(".png"):
        raise HTTPException(400, "Envie arquivo PNG.")
    p = _cfg.STATIC / "logo_custom.png"
    p.write_bytes(logo.file.read())
    return {"logo_url": "/static/logo_custom.png"}


# ── Permissions ───────────────────────────────────────────────────

@router.get("/permissoes")
def permissions_list(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal() as s:
        out = []
        for u in s.scalars(select(User).order_by(User.login)).all():
            pmap = _get_perms(s, u)
            out.append({
                "username": u.login,
                "display_name": u.display_name,
                "auth_source": u.auth_source,
                "active": u.active,
                "is_admin": u.is_admin,
                "allowed": u.allowed,
                "last_access": u.last_access.isoformat() if u.last_access else None,
                "permissions": (
                    ["admin"] if u.is_admin
                    else [m for m, v in pmap.items() if v.get("can_view")]
                ),
                "permission_map": pmap,
            })
        ac_row = s.get(Setting, "access_control")
        block_external = (ac_row.value if ac_row else {}).get("block_external", False)
        return {"usuarios": out, "modules": MODULES, "block_external": block_external}


@router.put("/permissoes/{login}")
def permissions_set(login: str, payload: dict, req: Request):
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")

        if u.login == _cfg.INITIAL_ADMIN_LOGIN and payload.get("active") is False:
            raise HTTPException(400, "O administrador inicial não pode ser desativado.")

        u.active = bool(payload.get("active", u.active))
        u.is_admin = bool(payload.get("is_admin", False))
        if "allowed" in payload:
            u.allowed = bool(payload["allowed"])

        requested = payload.get("permission_map") or {}
        legacy = payload.get("permissions") or []

        # Clear existing permissions for this user
        existing = s.scalars(
            select(Permission).where(Permission.user_id == u.id)
        ).all()
        for p in existing:
            s.delete(p)
        s.flush()

        if not u.is_admin:
            for m in MODULES:
                cfg = requested.get(m)
                if cfg is None and m in legacy:
                    cfg = {
                        "can_view": True,
                        "can_create": True,
                        "can_edit": True,
                        "can_export": True,
                        "can_admin": False,
                    }
                if cfg and cfg.get("can_view"):
                    s.add(Permission(
                        user_id=u.id,
                        module=m,
                        can_view=True,
                        can_create=bool(cfg.get("can_create")),
                        can_edit=bool(cfg.get("can_edit")),
                        can_export=bool(cfg.get("can_export")),
                        can_admin=bool(cfg.get("can_admin")),
                    ))

        s.add(AccessLog(
            login=sd["username"],
            auth_source=sd.get("auth_source", "LOCAL"),
            success=True,
            ip=client_ip(req),
            detail=f"Permissões atualizadas para {u.login}"[:500],
        ))

    return {"ok": True}


# ── Access control ────────────────────────────────────────────────

@router.get("/controle-acesso")
def access_control_get(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal() as s:
        row = s.get(Setting, "access_control")
        return row.value if row else {"block_external": False}


@router.put("/controle-acesso")
def access_control_set(payload: dict, req: Request):
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        row = s.get(Setting, "access_control")
        if not row:
            row = Setting(key="access_control")
            s.add(row)
        row.value = {"block_external": bool(payload.get("block_external", False))}
        row.updated_by = sd["username"]
    return {"ok": True}


# ── User creation ─────────────────────────────────────────────────

def _gerar_senha_temporaria() -> str:
    """Senha temporária legível e forte para primeiro acesso."""
    import secrets
    import string
    alfabeto = string.ascii_letters + string.digits
    return "".join(secrets.choice(alfabeto) for _ in range(12))


@router.post("/usuarios")
def user_create(body: UserCreateIn, req: Request):
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)

    senha_temporaria = None
    with SessionLocal.begin() as s:
        existing = s.scalar(
            select(User).where(func.lower(User.login) == body.login.lower())
        )
        if existing:
            raise HTTPException(409, "Usuário já existe.")

        if body.auth_source == "LOCAL":
            # Usuário local: gera uma senha temporária para o admin repassar.
            # A troca obrigatória no primeiro acesso foi desativada.
            senha_temporaria = _gerar_senha_temporaria()
            pwd_hash = hash_password(senha_temporaria)
            must_change = False
        else:
            # AD/SN: autenticação externa, sem senha local.
            pwd_hash = None
            must_change = False

        u = User(
            login=body.login,
            display_name=body.display_name.strip() or body.login,
            password_hash=pwd_hash,
            auth_source=body.auth_source,
            is_admin=body.is_admin,
            active=True,
            must_change_password=must_change,
            # Usuário externo (AD/SN/SSO) criado pelo admin já entra liberado.
            allowed=(body.auth_source != "LOCAL"),
        )
        s.add(u)
    return {"ok": True, "login": body.login, "senha_temporaria": senha_temporaria}


@router.delete("/usuarios/{login}")
def user_delete(login: str, req: Request):
    """Exclui um usuário. Só admin; protege o admin inicial e o próprio usuário."""
    sd = require_permission(req, "parametros", "admin")

    if login.lower() == _cfg.INITIAL_ADMIN_LOGIN.lower():
        raise HTTPException(400, "O administrador inicial não pode ser excluído.")
    if login.lower() == sd["username"].lower():
        raise HTTPException(400, "Você não pode excluir o próprio usuário.")

    with SessionLocal.begin() as s:
        u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")
        # Remove permissões vinculadas antes de excluir o usuário.
        for p in s.scalars(select(Permission).where(Permission.user_id == u.id)).all():
            s.delete(p)
        s.delete(u)
        s.add(AccessLog(
            login=sd["username"],
            auth_source=sd.get("auth_source", "LOCAL"),
            success=True,
            ip=client_ip(req),
            detail=f"Usuário excluído: {login}"[:500],
        ))
    return {"ok": True}


# ── Lot sequences ────────────────────────────────────────────────

@router.get("/sequencias")
def sequences_list(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal() as s:
        rows = s.scalars(select(LotSequence).order_by(LotSequence.prefix)).all()
        return {
            "sequencias": [
                {
                    "prefixo": x.prefix,
                    "proximo_numero": x.next_number,
                    "ativo": x.active,
                }
                for x in rows
            ]
        }


# ── Base local upload ─────────────────────────────────────────────

@router.post("/base-local/upload")
def base_local_upload(
    req: Request,
    company: str = Form(...),
    mode: str = Form("SUBSTITUIR"),
    file: UploadFile = File(...),
):
    sd = require_permission(req, "parametros", "admin")
    check_rate_limit(req)

    suffix = Path(file.filename or "upload.csv").suffix.lower()
    data = file.file.read()

    try:
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(data), sep=None, engine="python", dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(data), dtype=str)
    except Exception as e:
        raise HTTPException(400, f"Arquivo inválido: {e}")

    def norm(x):
        return "".join(
            c for c in unicodedata.normalize("NFD", str(x))
            if unicodedata.category(c) != "Mn"
        ).strip().lower()

    aliases = {
        "filial": ["filial"],
        "ativo": ["ativo", "imobilizado"],
        "etiqueta": ["etiqueta", "tag"],
        "descricao": ["descricao do bem", "descricao", "item"],
        "serie": ["numero de serie", "serie", "serial"],
        "data": ["dt. aquisicao", "data de aquisicao", "data aquisicao"],
    }
    cols = {norm(c): c for c in df.columns}

    def pick(k):
        return next(
            (cols.get(norm(a)) for a in aliases[k] if norm(a) in cols),
            None,
        )

    m = {k: pick(k) for k in aliases}
    valid = []

    for _, r in df.iterrows():
        row = {
            k: (str(r[v]).strip() if v and pd.notna(r[v]) else "")
            for k, v in m.items()
        }
        if not any(row.get(k) for k in ("ativo", "etiqueta", "serie")):
            continue

        dt = None
        if row["data"]:
            try:
                x = pd.to_datetime(row["data"], dayfirst=True, errors="coerce")
                dt = x.date() if pd.notna(x) and 1900 <= x.year <= 2100 else None
            except (ValueError, TypeError):
                pass

        valid.append(LocalAsset(
            company=company.upper(),
            branch=row["filial"],
            asset_number=row["ativo"],
            tag_number=row["etiqueta"],
            serial_number=row["serie"],
            description=row["descricao"],
            acquisition_date=dt,
            active=True,
        ))

    with SessionLocal.begin() as s:
        h = LoadHistory(
            company=company.upper(),
            filename=file.filename or "upload",
            mode=mode,
            total_rows=len(df),
            valid_rows=len(valid),
            rejected_rows=len(df) - len(valid),
            status="CONCLUIDO",
            created_by=sd["username"],
        )
        s.add(h)
        s.flush()

        if mode.upper() == "SUBSTITUIR":
            # Deactivate existing entries for this company
            existing = s.scalars(
                select(LocalAsset).where(
                    LocalAsset.company == company.upper(),
                    LocalAsset.active == True,  # noqa: E712
                )
            ).all()
            for la in existing:
                la.active = False

        for x in valid:
            x.load_id = h.id
            s.add(x)

    return {
        "ok": True,
        "total": len(df),
        "validos": len(valid),
        "rejeitados": len(df) - len(valid),
    }
