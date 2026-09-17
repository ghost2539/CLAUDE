"""Parâmetros (admin/settings) router — locations, classifications,
hourly rate, visual/tv config, permissions, users, base-local upload.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func

from config import get_settings
from db.portal import (
    SessionLocal, Setting, Classification, StorageLocation,
    User, Permission, LotSequence, LocalAsset, LoadHistory,
    AccessLog,
)
from core.security import (get_session, require_permission, client_ip, check_rate_limit,
                           require_admin_geral, is_admin_geral, require_admin,
                           atualizar_sessoes, encerrar_sessoes, acoes_do_modulo)
from routers.helpers import reapply_classification, reclassify_all
from routers.auth import perms_efetivas, _user_payload

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
    auth_source: str = "SSO"
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
        if v not in ("AD", "SN", "SSO"):
            raise ValueError("auth_source deve ser AD, SN ou SSO (a senha é sempre a da rede).")
        return v


# ── Helpers ───────────────────────────────────────────────────────

def _get_perms(s, u: User) -> dict:
    """Permissões efetivas (as da base mais a liberação da Consulta Times)."""
    return perms_efetivas(s, u)


def _refletir_na_sessao(s, u: User) -> None:
    """O que foi gravado passa a valer para quem já está logado: sem isto a
    sessão seguiria com a cópia antiga por até 8 horas. Desativado ou sem
    liberação: as sessões caem."""
    if not u.active or not u.allowed:
        encerrar_sessoes(u.login)
        return
    dados = _user_payload(u, perms_efetivas(s, u))
    atualizar_sessoes(u.login, **{k: dados[k] for k in
                                  ("is_admin", "role", "permissions", "permission_map")})


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
        rule = Classification(
            description_pattern=body.padrao_descricao,
            company=body.empresa.strip(),
            category=body.categoria,
            model=body.modelo,
            active=True,
        )
        s.add(rule)
        s.flush()
        atualizados = reapply_classification(s, rule)
    return {"ok": True, "atualizados": atualizados}


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
        s.flush()
        atualizados = reapply_classification(s, x) if x.active else 0
    return {"ok": True, "atualizados": atualizados}


@router.post("/classificacoes/aplicar-base")
def classification_apply_all(req: Request):
    """Reaplica todas as regras sobre a base de recebimento inteira."""
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        return {"ok": True, **reclassify_all(s)}


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
    # Toda chave é configuração do portal (visual, dashboards, controle de
    # acesso, integrações). Nenhum fluxo de operador grava por aqui, então
    # a regra é uma só: administrar.
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Setting, key)
        if not x:
            x = Setting(key=key)
            s.add(x)
        x.value = payload
        x.updated_by = sd["username"]
    return {"ok": True}


# ── Ícone do portal (favicon) — só o admin geral altera ──────────────
FAVICON_DIR = _cfg.DATA / "branding"
FAVICON_MAX = 256 * 1024


def favicon_atual() -> Path | None:
    """O arquivo enviado pelo admin geral, se houver."""
    if not FAVICON_DIR.exists():
        return None
    for ext in (".svg", ".png", ".ico"):
        p = FAVICON_DIR / f"favicon{ext}"
        if p.exists():
            return p
    return None


def _sniff_favicon(conteudo: bytes, tipo: str) -> str:
    """Confere o conteúdo, não só o Content-Type. Devolve a extensão."""
    if conteudo.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if conteudo.startswith(b"\x00\x00\x01\x00"):
        return ".ico"
    cabeca = conteudo[:4096].lstrip().lower()
    if cabeca.startswith(b"<?xml") or cabeca.startswith(b"<svg"):
        baixo = conteudo.lower()
        # SVG é documento: sem script, sem handlers, sem carregar de fora.
        if b"<script" in baixo or b"javascript:" in baixo or b"<foreignobject" in baixo \
                or re.search(rb"\son[a-z]+\s*=", baixo) or b"xlink:href=\"http" in baixo \
                or b"href=\"http" in baixo:
            raise HTTPException(400, "SVG com script ou conteúdo externo não é aceito.")
        return ".svg"
    raise HTTPException(400, "Envie um arquivo SVG, PNG ou ICO.")


@router.get("/favicon")
def favicon_info(req: Request):
    sd = get_session(req)
    atual = favicon_atual()
    with SessionLocal() as s:
        x = s.get(Setting, "portal_favicon")
        meta = x.value if x else {}
    return {"personalizado": atual is not None, "pode_alterar": is_admin_geral(sd),
            "atualizado_por": meta.get("atualizado_por", ""), "atualizado_em": meta.get("atualizado_em", ""),
            "versao": meta.get("versao", 0)}


@router.post("/favicon")
async def favicon_enviar(req: Request, arquivo: UploadFile = File(...)):
    sd = require_admin_geral(req)
    check_rate_limit(req)
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "Arquivo vazio.")
    if len(conteudo) > FAVICON_MAX:
        raise HTTPException(400, "Ícone acima de 256 KB.")
    ext = _sniff_favicon(conteudo, arquivo.content_type or "")
    FAVICON_DIR.mkdir(parents=True, exist_ok=True)
    for velho in FAVICON_DIR.glob("favicon.*"):
        velho.unlink()
    (FAVICON_DIR / f"favicon{ext}").write_bytes(conteudo)
    with SessionLocal.begin() as s:
        x = s.get(Setting, "portal_favicon")
        if not x:
            x = Setting(key="portal_favicon")
            s.add(x)
        versao = int((x.value or {}).get("versao", 0)) + 1
        x.value = {"extensao": ext, "atualizado_por": sd["username"],
                   "atualizado_em": date.today().isoformat(), "versao": versao}
        x.updated_by = sd["username"]
    return {"ok": True, "versao": versao}


@router.delete("/favicon")
def favicon_restaurar(req: Request):
    sd = require_admin_geral(req)
    if FAVICON_DIR.exists():
        for velho in FAVICON_DIR.glob("favicon.*"):
            velho.unlink()
    with SessionLocal.begin() as s:
        x = s.get(Setting, "portal_favicon")
        if x:
            x.value = {**(x.value or {}), "extensao": "", "atualizado_por": sd["username"],
                       "atualizado_em": date.today().isoformat(),
                       "versao": int((x.value or {}).get("versao", 0)) + 1}
    return {"ok": True}


# ── EBS: API de consulta e banco Oracle, configuráveis sem reiniciar ──
@router.get("/ebs")
def ebs_ler(req: Request):
    require_permission(req, "parametros", "admin")
    import db.monitoramento as _mon
    api = _mon.obter_config("ebs_api") or {}
    return {"api": {"login_url": api.get("login_url") or _cfg.EBS_LOGIN_URL,
                    "search_url": api.get("search_url") or _cfg.EBS_SEARCH_URL}}


class EbsIn(BaseModel):
    login_url: str = ""
    search_url: str = ""

    @field_validator("login_url", "search_url")
    @classmethod
    def _so_https_ou_vazio(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.lower().startswith("https://"):
            raise ValueError("A URL da API do EBS precisa começar com https://.")
        return v


@router.put("/ebs")
def ebs_gravar(body: EbsIn, req: Request):
    sd = require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    import db.monitoramento as _mon
    _mon.salvar_config({"login_url": body.login_url, "search_url": body.search_url}, "ebs_api")
    import logging as _lg
    _lg.getLogger("parametros").info("URLs da API do EBS reconfiguradas por %s", sd.get("username", "?"))
    return ebs_ler(req)


@router.post("/visual/reset")
def visual_reset(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Setting, "visual")
        if x:
            x.value = {
                "nome_app": "Portal de Operações - SPARE",
                "footer": "SPARE - Portal de Operações",
            }
    return {"ok": True}


# ── Permissions ───────────────────────────────────────────────────

@router.get("/permissoes")
def permissions_list(req: Request):
    require_admin(req)
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
        return {"usuarios": out, "modules": MODULES,
                "module_actions": {m: list(acoes_do_modulo(m)) for m in MODULES},
                "block_external": block_external}


class PermissoesIn(BaseModel):
    active: bool | None = None
    allowed: bool | None = None
    is_admin: bool | None = None
    permission_map: dict[str, dict[str, bool]] = {}


@router.put("/permissoes/{login}")
def permissions_set(login: str, body: PermissoesIn, req: Request):
    """Liberação de acesso. Só administrador do portal chega aqui, e nem ele
    tira o próprio `is_admin` nem mexe no administrador inicial — evita
    ficar sem ninguém que possa liberar acesso."""
    sd = require_admin(req)
    with SessionLocal.begin() as s:
        u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
        if not u:
            raise HTTPException(404, "Usuário não encontrado.")

        inicial = u.login.lower() == (_cfg.INITIAL_ADMIN_LOGIN or "").lower()
        proprio = u.login.lower() == (sd.get("username") or "").lower()
        if inicial and (body.active is False or body.allowed is False or body.is_admin is False):
            raise HTTPException(400, "O administrador inicial não pode ser desativado nem rebaixado.")
        if proprio and (body.active is False or body.is_admin is False):
            raise HTTPException(400, "Você não pode desativar nem rebaixar o próprio usuário.")

        if body.active is not None:
            u.active = body.active
        if body.allowed is not None:
            u.allowed = body.allowed
        if body.is_admin is not None:
            u.is_admin = body.is_admin

        for p in s.scalars(select(Permission).where(Permission.user_id == u.id)).all():
            s.delete(p)
        s.flush()

        if not u.is_admin:
            for m in MODULES:
                cfg = body.permission_map.get(m)
                if not cfg or not cfg.get("can_view"):
                    continue
                # Só o que existe no módulo entra; o resto é descartado.
                acoes = acoes_do_modulo(m)
                s.add(Permission(
                    user_id=u.id,
                    module=m,
                    can_view=True,
                    can_create=bool(cfg.get("can_create")) and "create" in acoes,
                    can_edit=bool(cfg.get("can_edit")) and "edit" in acoes,
                    can_export=bool(cfg.get("can_export")) and "export" in acoes,
                    can_admin=bool(cfg.get("can_admin")) and "admin" in acoes,
                ))
        s.flush()

        s.add(AccessLog(
            login=sd["username"],
            auth_source=sd.get("auth_source", "SSO"),
            success=True,
            ip=client_ip(req),
            detail=(f"Permissões atualizadas para {u.login}"
                    f" (admin={u.is_admin}, ativo={u.active}, liberado={u.allowed})")[:500],
        ))
        _refletir_na_sessao(s, u)

    return {"ok": True}


# ── Access control ────────────────────────────────────────────────

@router.get("/controle-acesso")
def access_control_get(req: Request):
    require_admin(req)
    with SessionLocal() as s:
        row = s.get(Setting, "access_control")
        return row.value if row else {"block_external": False}


@router.put("/controle-acesso")
def access_control_set(payload: dict, req: Request):
    sd = require_admin(req)
    with SessionLocal.begin() as s:
        row = s.get(Setting, "access_control")
        if not row:
            row = Setting(key="access_control")
            s.add(row)
        row.value = {"block_external": bool(payload.get("block_external", False))}
        row.updated_by = sd["username"]
    return {"ok": True}


# ── User creation ─────────────────────────────────────────────────

@router.post("/usuarios")
def user_create(body: UserCreateIn, req: Request):
    """Libera um login de rede antes do primeiro acesso. Sem senha no portal:
    quem autentica é o SSO corporativo."""
    require_admin(req)
    check_rate_limit(req)

    with SessionLocal.begin() as s:
        existing = s.scalar(
            select(User).where(func.lower(User.login) == body.login.lower())
        )
        if existing:
            raise HTTPException(409, "Usuário já existe.")
        u = User(
            login=body.login,
            display_name=body.display_name.strip() or body.login,
            auth_source=body.auth_source,
            is_admin=body.is_admin,
            active=True,
            must_change_password=False,
            allowed=True,
        )
        s.add(u)
    return {"ok": True, "login": body.login}


@router.delete("/usuarios/{login}")
def user_delete(login: str, req: Request):
    """Exclui um usuário. Só admin do portal; protege o admin inicial e o próprio usuário."""
    sd = require_admin(req)

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
            auth_source=sd.get("auth_source", "SSO"),
            success=True,
            ip=client_ip(req),
            detail=f"Usuário excluído: {login}"[:500],
        ))
    encerrar_sessoes(login)
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
