"""Parâmetros (admin/settings) router — locations, classifications,
hourly rate, visual/tv config, permissions, users, base-local upload.
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func, text

from config import get_settings
from db.portal import (
    SessionLocal, Setting, Classification, StorageLocation,
    User, Permission, LotSequence, LocalAsset, LoadHistory,
    AccessLog, AccessProfile, hash_password,
)
from core.security import (get_session, require_permission, client_ip,
                          check_rate_limit, is_admin_geral, require_admin_geral)
import core.permissoes as _perm
from routers.helpers import reapply_classification, reclassify_all

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

def favicon_atual() -> Path | None:
    """O arquivo enviado pelo admin geral, se houver."""
    if not FAVICON_DIR.exists():
        return None
    for ext in (".svg", ".png", ".ico"):
        p = FAVICON_DIR / f"favicon{ext}"
        if p.exists():
            return p
    return None


# ── Ícone do portal (favicon) — só o admin geral altera ──────────────
FAVICON_DIR = _cfg.DATA / "branding"


FAVICON_MAX = 256 * 1024


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


# Nomes de coluna aceitos na planilha. Cada um com os apelidos que
# aparecem nas planilhas que a área já usa — exigir o cabeçalho exato
# transformaria a importação num jogo de adivinhação.
_COLUNAS_CLASSIFICACAO = {
    "padrao_descricao": ["padrao da descricao", "padrao descricao", "padrao",
                         "descricao", "descricao do bem", "item"],
    "empresa": ["empresa", "company", "bu"],
    "categoria": ["categoria", "category"],
    "modelo": ["modelo", "model"],
    "ativo": ["ativo", "ativa", "active"],
}


def _sem_acento(x) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", str(x))
        if unicodedata.category(c) != "Mn"
    ).strip().lower()


@router.post("/classificacoes/importar")
def classification_import(
    req: Request,
    file: UploadFile = File(...),
    modo: str = Form("ACRESCENTAR"),
):
    """Importa regras de classificação de uma planilha (CSV ou Excel).

    A tela só tinha "Nova regra", uma de cada vez. Quem chega com duzentas
    regras vindas do servidor antigo, ou monta a tabela no Excel, não tem
    como cadastrar — e cadastrar à mão duzentas vezes não é alternativa, é
    convite a erro de digitação.

    `modo=SUBSTITUIR` apaga as regras existentes antes; `ACRESCENTAR`
    (padrão) mantém e atualiza pelo padrão da descrição. O padrão é o mais
    conservador de propósito: apagar a configuração inteira não pode ser o
    que acontece quando alguém erra o clique.

    Não reaplica sobre a base de recebimento: isso é caro e já tem botão
    próprio ("Aplicar em toda a base"). A resposta lembra disso.
    """
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)

    modo = (modo or "ACRESCENTAR").strip().upper()
    if modo not in ("ACRESCENTAR", "SUBSTITUIR"):
        raise HTTPException(422, "Modo deve ser ACRESCENTAR ou SUBSTITUIR.")

    sufixo = Path(file.filename or "planilha.csv").suffix.lower()
    dados = file.file.read()
    if not dados:
        raise HTTPException(400, "Arquivo vazio.")
    try:
        if sufixo == ".csv":
            df = pd.read_csv(io.BytesIO(dados), sep=None, engine="python", dtype=str)
        else:
            df = pd.read_excel(io.BytesIO(dados), dtype=str)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Não consegui ler o arquivo: {e}")

    achadas = {_sem_acento(c): c for c in df.columns}

    def coluna(chave):
        for apelido in _COLUNAS_CLASSIFICACAO[chave]:
            if _sem_acento(apelido) in achadas:
                return achadas[_sem_acento(apelido)]
        return None

    mapa = {k: coluna(k) for k in _COLUNAS_CLASSIFICACAO}
    faltando = [k for k in ("padrao_descricao", "categoria") if not mapa[k]]
    if faltando:
        raise HTTPException(
            400, "Faltam colunas obrigatórias: " + ", ".join(faltando) +
                 ". O cabeçalho aceita, por exemplo: Padrão da descrição, "
                 "Empresa, Categoria, Modelo, Ativo.")

    def texto(linha, chave):
        col = mapa[chave]
        if not col:
            return ""
        v = linha[col]
        return "" if pd.isna(v) else str(v).strip()

    novas, atualizadas, ignoradas = 0, 0, 0
    with SessionLocal.begin() as s:
        if modo == "SUBSTITUIR":
            for x in s.scalars(select(Classification)).all():
                s.delete(x)
            s.flush()
        # Índice do que já existe, para não duplicar regra igual: a chave é
        # o par padrão+empresa, que é o que a classificação usa para casar.
        existentes = {
            (_sem_acento(x.description_pattern), _sem_acento(x.company)): x
            for x in s.scalars(select(Classification)).all()
        }
        for _, linha in df.iterrows():
            padrao = texto(linha, "padrao_descricao")
            categoria = texto(linha, "categoria")
            if not padrao or not categoria:
                ignoradas += 1
                continue
            empresa = texto(linha, "empresa")
            modelo = texto(linha, "modelo")
            bruto = _sem_acento(texto(linha, "ativo"))
            ativo = bruto not in ("nao", "n", "false", "0", "inativo", "inativa")

            chave = (_sem_acento(padrao), _sem_acento(empresa))
            atual = existentes.get(chave)
            if atual:
                atual.category, atual.model, atual.active = categoria, modelo, ativo
                atualizadas += 1
            else:
                nova = Classification(
                    description_pattern=padrao, company=empresa,
                    category=categoria, model=modelo, active=ativo)
                s.add(nova)
                existentes[chave] = nova
                novas += 1

    return {
        "ok": True, "novas": novas, "atualizadas": atualizadas,
        "ignoradas": ignoradas, "modo": modo,
        "aviso": ("As regras foram gravadas. Para aplicá-las aos "
                  "recebimentos que já existem, use 'Aplicar em toda a base'."),
    }


@router.post("/classificacoes/aplicar-base")
def classification_apply_all(req: Request):
    """Reaplica todas as regras sobre a base de recebimento inteira."""
    require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    with SessionLocal.begin() as s:
        return {"ok": True, **reclassify_all(s)}


@router.delete("/classificacoes/{id}")
def classification_delete(id: int, req: Request):
    """Apaga a regra do Cadastro de modelos.

    Os ativos já classificados por ela NÃO mudam: a regra só deixa de valer
    para as próximas entradas. Fica no log de acesso porque não dá para
    desfazer — e agora a tela tem o botão, então passa a acontecer."""
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(Classification, id)
        if not x:
            raise HTTPException(404, "Classificação não encontrada.")
        descricao = (x.description_pattern or "")[:120]
        s.delete(x)
        s.add(AccessLog(
            login=sd["username"],
            auth_source=sd.get("auth_source", "LOCAL"),
            success=True,
            ip=client_ip(req),
            detail=f"Regra de classificação {id} excluída ({descricao})"[:500],
        ))
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
    if key in ("visual", "tv", "correios", "dashboards") and not sd.get("is_admin"):
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
                "niveis": _perm.niveis_do_mapa(pmap),
                "perfil": u.perfil or "",
            })
        ac_row = s.get(Setting, "access_control")
        block_external = (ac_row.value if ac_row else {}).get("block_external", False)
        perfis = [
            {"id": x.id, "nome": x.nome, "descricao": x.descricao,
             "niveis": x.niveis or {}}
            for x in s.scalars(select(AccessProfile).order_by(AccessProfile.nome)).all()
        ]
        return {
            "usuarios": out, "modules": MODULES, "block_external": block_external,
            # O vocabulário vem do servidor: a tela não deve ter a própria
            # cópia da regra, senão as duas divergem e o que está escrito na
            # tela deixa de ser o que foi gravado.
            "niveis": [{"chave": n, "rotulo": _perm.ROTULOS[n]} for n in _perm.NIVEIS],
            "perfis": perfis,
        }


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
        # A tela nova manda nível por módulo; a tradução é do servidor, para
        # não existirem duas versões da regra. `permission_map` continua
        # aceito: chamada antiga não pode parar de funcionar por causa disto.
        if payload.get("niveis"):
            requested = _perm.mapa_de_niveis(payload["niveis"])
        if "perfil" in payload:
            u.perfil = str(payload.get("perfil") or "")[:60]

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


# ── Perfis de acesso ──────────────────────────────────────────────
#
# Liberar alguém exigia percorrer trinta módulos. Com o perfil, escolhe-se
# um e os trinta são preenchidos de uma vez; depois se ajusta a exceção.
#
# O perfil é MODELO, não vínculo: aplicar copia os níveis para as linhas de
# permissão e acaba ali. Mudar um perfil depois NÃO altera quem já foi
# liberado — de propósito, porque o contrário mudaria em silêncio o acesso
# de gente que ninguém tocou, e a tela de permissões deixaria de dizer a
# verdade sobre o usuário que está aberto.


def _perfil_dict(x: AccessProfile) -> dict:
    return {"id": x.id, "nome": x.nome, "descricao": x.descricao,
            "niveis": x.niveis or {},
            "atualizado_em": x.atualizado_em.isoformat() if x.atualizado_em else "",
            "atualizado_por": x.atualizado_por or ""}


def _niveis_validos(bruto: dict) -> dict:
    """Só módulo que existe e nível que existe. O resto é descartado.

    Vale a pena descartar em silêncio: perfil gravado com módulo que foi
    removido do portal não é erro de quem está usando a tela, e recusar o
    salvamento inteiro por causa disso travaria a edição sem motivo.
    """
    saida = {}
    for modulo, nivel in (bruto or {}).items():
        m, n = str(modulo), str(nivel).strip().lower()
        if m in MODULES and n in _perm.NIVEIS and n != "nenhum":
            saida[m] = n
    return saida


@router.get("/perfis")
def perfis_listar(req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal() as s:
        return {"perfis": [_perfil_dict(x) for x in
                           s.scalars(select(AccessProfile).order_by(AccessProfile.nome)).all()],
                "modules": MODULES,
                "niveis": [{"chave": n, "rotulo": _perm.ROTULOS[n]} for n in _perm.NIVEIS]}


@router.post("/perfis")
def perfil_criar(body: dict, req: Request):
    sd = require_permission(req, "parametros", "admin")
    nome = str((body or {}).get("nome") or "").strip()[:60]
    if len(nome) < 2:
        raise HTTPException(422, "Informe um nome com pelo menos 2 caracteres.")
    with SessionLocal.begin() as s:
        if s.scalar(select(AccessProfile).where(func.lower(AccessProfile.nome) == nome.lower())):
            raise HTTPException(409, f"Já existe um perfil chamado '{nome}'.")
        x = AccessProfile(
            nome=nome, descricao=str((body or {}).get("descricao") or "")[:240],
            niveis=_niveis_validos((body or {}).get("niveis")),
            atualizado_por=sd["username"])
        s.add(x)
        s.flush()
        return {"ok": True, "perfil": _perfil_dict(x)}


@router.put("/perfis/{perfil_id}")
def perfil_editar(perfil_id: int, body: dict, req: Request):
    sd = require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(AccessProfile, perfil_id)
        if not x:
            raise HTTPException(404, "Perfil não encontrado.")
        if "nome" in (body or {}):
            nome = str(body.get("nome") or "").strip()[:60]
            if len(nome) < 2:
                raise HTTPException(422, "Informe um nome com pelo menos 2 caracteres.")
            outro = s.scalar(select(AccessProfile).where(
                func.lower(AccessProfile.nome) == nome.lower(), AccessProfile.id != x.id))
            if outro:
                raise HTTPException(409, f"Já existe um perfil chamado '{nome}'.")
            x.nome = nome
        if "descricao" in (body or {}):
            x.descricao = str(body.get("descricao") or "")[:240]
        if "niveis" in (body or {}):
            x.niveis = _niveis_validos(body.get("niveis"))
        x.atualizado_por = sd["username"]
        s.flush()
        return {"ok": True, "perfil": _perfil_dict(x)}


@router.delete("/perfis/{perfil_id}")
def perfil_remover(perfil_id: int, req: Request):
    require_permission(req, "parametros", "admin")
    with SessionLocal.begin() as s:
        x = s.get(AccessProfile, perfil_id)
        if not x:
            raise HTTPException(404, "Perfil não encontrado.")
        s.delete(x)
    # Ninguém perde acesso: o perfil é modelo, e o que vale são as linhas de
    # permissão que já foram gravadas em cada usuário.
    return {"ok": True}


@router.post("/perfis/{perfil_id}/aplicar")
def perfil_aplicar(perfil_id: int, body: dict, req: Request):
    """Grava os níveis do perfil nas permissões dos usuários informados."""
    sd = require_permission(req, "parametros", "admin")
    check_rate_limit(req)
    logins = [str(x).strip() for x in ((body or {}).get("logins") or []) if str(x).strip()]
    if not logins:
        raise HTTPException(422, "Informe ao menos um usuário.")
    aplicados, ignorados = [], []
    with SessionLocal.begin() as s:
        perfil = s.get(AccessProfile, perfil_id)
        if not perfil:
            raise HTTPException(404, "Perfil não encontrado.")
        mapa = _perm.mapa_de_niveis(perfil.niveis or {})
        for login in logins:
            u = s.scalar(select(User).where(func.lower(User.login) == login.lower()))
            if not u:
                ignorados.append(login)
                continue
            # Admin geral não recebe perfil: ele já pode tudo, e gravar
            # linhas de permissão para ele daria a impressão de que o perfil
            # é o que manda no acesso dele.
            if u.is_admin:
                ignorados.append(login)
                continue
            for antiga in s.scalars(select(Permission).where(Permission.user_id == u.id)).all():
                s.delete(antiga)
            s.flush()
            for m, flags in mapa.items():
                s.add(Permission(user_id=u.id, module=m, **flags))
            u.perfil = perfil.nome
            aplicados.append(u.login)
        s.add(AccessLog(
            login=sd["username"], auth_source=sd.get("auth_source", "LOCAL"),
            success=True, ip=client_ip(req),
            detail=f"Perfil '{perfil.nome}' aplicado a: {', '.join(aplicados) or 'ninguém'}"[:500]))
    return {"ok": True, "aplicados": aplicados, "ignorados": ignorados,
            "modulos": len(mapa)}


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
