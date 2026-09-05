"""Monitoramento do Portal SPARE — saúde e falhas.

- Saúde do SERVIDOR: memória, disco, carga, uptime, processo.
- Saúde dos SERVIÇOS: bancos (portal + isolados) e integrações
  (ServiceNow, EBS, Correios) — checadas sob demanda.
- FALHAS: 5xx e exceções de API (via middleware), falhas de integração e
  de automações, tudo em banco isolado e consultável.

Módulo isolado e aditivo: nada aqui altera fluxo existente, e toda escrita é
tolerante a erro.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime

from fastapi import APIRouter, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

import config as _config_mod
import database_monitoramento as db
from security import require_permission

_cfg = _config_mod.get_settings()
_log = logging.getLogger("monitoramento")

router = APIRouter(prefix="/api/monitor", tags=["Monitoramento"])

_INICIO = time.time()

# Limiares para o status ficar "alerta"/"critico"
LIM_DISCO_ALERTA = 80.0     # % usado
LIM_DISCO_CRITICO = 90.0
LIM_MEM_ALERTA = 85.0
LIM_MEM_CRITICO = 95.0


# ── Coleta de saúde do servidor (sem dependências extras) ───────────────
def _memoria() -> dict:
    try:
        info = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for linha in f:
                partes = linha.split(":")
                if len(partes) == 2:
                    info[partes[0].strip()] = partes[1].strip()

        def _kb(chave):
            v = info.get(chave, "0 kB").split()[0]
            return int(v) if v.isdigit() else 0

        total = _kb("MemTotal")
        disp = _kb("MemAvailable") or _kb("MemFree")
        usado = max(0, total - disp)
        pct = round(usado / total * 100, 1) if total else 0.0
        return {"total_mb": round(total / 1024), "usado_mb": round(usado / 1024),
                "livre_mb": round(disp / 1024), "pct_usado": pct}
    except Exception:  # noqa: BLE001
        return {"total_mb": 0, "usado_mb": 0, "livre_mb": 0, "pct_usado": 0.0}


def _disco(caminho: str | None = None) -> dict:
    alvo = caminho or str(getattr(_cfg, "ROOT", ".") or ".")
    try:
        t, u, l = shutil.disk_usage(alvo)
        pct = round(u / t * 100, 1) if t else 0.0
        return {"caminho": alvo, "total_gb": round(t / 1024**3, 1),
                "usado_gb": round(u / 1024**3, 1), "livre_gb": round(l / 1024**3, 1),
                "pct_usado": pct}
    except Exception:  # noqa: BLE001
        return {"caminho": alvo, "total_gb": 0, "usado_gb": 0, "livre_gb": 0, "pct_usado": 0.0}


def _carga() -> dict:
    try:
        c1, c5, c15 = os.getloadavg()
        cpus = os.cpu_count() or 1
        return {"load1": round(c1, 2), "load5": round(c5, 2), "load15": round(c15, 2),
                "cpus": cpus, "pct_load1": round(c1 / cpus * 100, 1)}
    except Exception:  # noqa: BLE001
        return {"load1": 0, "load5": 0, "load15": 0, "cpus": os.cpu_count() or 1, "pct_load1": 0}


def _uptime() -> dict:
    seg_srv = 0
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            seg_srv = int(float(f.read().split()[0]))
    except Exception:  # noqa: BLE001
        pass

    def _fmt(s):
        d, r = divmod(int(s), 86400)
        h, r = divmod(r, 3600)
        m = r // 60
        return (f"{d}d " if d else "") + f"{h}h {m}m"

    app_seg = int(time.time() - _INICIO)
    return {"servidor": _fmt(seg_srv), "servidor_seg": seg_srv,
            "aplicacao": _fmt(app_seg), "aplicacao_seg": app_seg}


def _processo() -> dict:
    try:
        rss = 0
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for linha in f:
                if linha.startswith("VmRSS:"):
                    v = linha.split()[1]
                    rss = int(v) if v.isdigit() else 0
                    break
        return {"pid": os.getpid(), "memoria_mb": round(rss / 1024)}
    except Exception:  # noqa: BLE001
        return {"pid": os.getpid(), "memoria_mb": 0}


def _bancos() -> list[dict]:
    """Tamanho e acessibilidade de cada banco (SQLite por arquivo; Postgres por ping)."""
    saida = []
    arquivos = {
        "Orçamento (/tv2)": getattr(_cfg, "ORCAMENTO_DATABASE_URL", ""),
        "Execução CAPEX": getattr(_cfg, "ORCAMENTO_EXEC_DATABASE_URL", ""),
        "Indicadores": getattr(_cfg, "INDICADORES_DATABASE_URL", ""),
        "Automações": getattr(_cfg, "AUTOMACOES_DATABASE_URL", ""),
        "Monitoramento": getattr(_cfg, "MONITORAMENTO_DATABASE_URL", ""),
    }
    for nome, url in arquivos.items():
        if not url or not url.startswith("sqlite"):
            continue
        caminho = url.split("///", 1)[-1] if "///" in url else ""
        try:
            tam = os.path.getsize(caminho) if caminho and os.path.exists(caminho) else 0
            saida.append({"nome": nome, "tipo": "SQLite", "ok": bool(tam >= 0),
                          "tamanho_mb": round(tam / 1024**2, 2),
                          "detalhe": caminho or "—"})
        except Exception as exc:  # noqa: BLE001
            saida.append({"nome": nome, "tipo": "SQLite", "ok": False,
                          "tamanho_mb": 0, "detalhe": str(exc)[:120]})
    # Postgres do portal — ping leve
    try:
        from sqlalchemy import text as _text
        from database import SessionLocal as _PortalSession
        t0 = time.time()
        with _PortalSession() as s:
            s.execute(_text("SELECT 1"))
        saida.insert(0, {"nome": "Portal (principal)", "tipo": "Postgres", "ok": True,
                         "tamanho_mb": 0,
                         "detalhe": f"resposta em {int((time.time()-t0)*1000)} ms"})
    except Exception as exc:  # noqa: BLE001
        saida.insert(0, {"nome": "Portal (principal)", "tipo": "Postgres", "ok": False,
                         "tamanho_mb": 0, "detalhe": str(exc)[:160]})
    return saida


def _status_geral(mem: dict, disco: dict, bancos: list[dict], res: dict) -> str:
    if not all(b["ok"] for b in bancos):
        return "critico"
    if disco["pct_usado"] >= LIM_DISCO_CRITICO or mem["pct_usado"] >= LIM_MEM_CRITICO:
        return "critico"
    if (disco["pct_usado"] >= LIM_DISCO_ALERTA or mem["pct_usado"] >= LIM_MEM_ALERTA
            or res.get("erros", 0) > 0):
        return "alerta"
    return "ok"


# ── Endpoints ───────────────────────────────────────────────────────────
@router.get("/saude")
def saude(req: Request, horas: int = 24):
    """Painel de saúde: servidor, aplicação, bancos e resumo de falhas."""
    require_permission(req, "parametros", "admin")
    mem, disco, bancos = _memoria(), _disco(), _bancos()
    res = db.resumo(horas)
    return {
        "gerado_em": datetime.now().isoformat(),
        "status": _status_geral(mem, disco, bancos, res),
        "servidor": {"memoria": mem, "disco": disco, "carga": _carga(), "uptime": _uptime()},
        "aplicacao": _processo(),
        "bancos": bancos,
        "falhas": res,
        "limiares": {"disco_alerta": LIM_DISCO_ALERTA, "disco_critico": LIM_DISCO_CRITICO,
                     "mem_alerta": LIM_MEM_ALERTA, "mem_critico": LIM_MEM_CRITICO},
    }


@router.get("/falhas")
def falhas(req: Request, limit: int = 300, severidade: str = "", origem: str = "", q: str = ""):
    """Lista os eventos registrados (falhas de API, integração e automação)."""
    require_permission(req, "parametros", "admin")
    return {"eventos": db.listar(limit=limit, severidade=severidade, origem=origem, q=q)}


@router.post("/checar")
def checar(req: Request):
    """Testa as integrações externas AGORA e registra o resultado."""
    sd = require_permission(req, "parametros", "admin")
    usuario = sd.get("username", "")
    resultados = []

    def _add(nome, ok, detalhe, ms):
        resultados.append({"servico": nome, "ok": ok, "detalhe": detalhe, "ms": ms})
        db.registrar(severidade="ok" if ok else "erro", origem="integracao",
                     alvo=nome, duracao_ms=ms, usuario=usuario,
                     detalhe=detalhe if not ok else "checagem ok")

    # ServiceNow — conta de serviço (leitura)
    t0 = time.time()
    try:
        from routers.indicadores import _sn_stats
        _sn_stats("incident", "sys_created_on>=javascript:gs.daysAgoStart(1)")
        _add("ServiceNow (conta de serviço)", True, "consulta respondeu",
             int((time.time() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        _add("ServiceNow (conta de serviço)", False, str(exc)[:300], int((time.time() - t0) * 1000))

    # Sessão ServiceNow do usuário logado
    t0 = time.time()
    try:
        from routers.servicenow import _sn_session_from_portal, _sn_session_valida
        s = _sn_session_from_portal(req)
        ok = _sn_session_valida(s)
        _add("ServiceNow (sessão do usuário)", ok,
             "sessão válida" if ok else "sessão expirada", int((time.time() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        _add("ServiceNow (sessão do usuário)", False, str(exc)[:300], int((time.time() - t0) * 1000))

    # EBS
    t0 = time.time()
    try:
        from routers.public_assets import _auth as _ebs_auth
        ok = bool(_ebs_auth())
        _add("EBS (autenticação)", ok, "autenticou" if ok else "sem autenticação",
             int((time.time() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        _add("EBS (autenticação)", False, str(exc)[:300], int((time.time() - t0) * 1000))

    # Correios
    t0 = time.time()
    try:
        from routers.correios import _correios_creds
        usuario_c, chave, _cartoes, _dr, _contrato = _correios_creds()
        ok = bool(usuario_c and chave)
        _add("Correios (credenciais)", ok,
             "credenciais presentes" if ok else "credenciais ausentes no cofre/ambiente",
             int((time.time() - t0) * 1000))
    except Exception as exc:  # noqa: BLE001
        _add("Correios (credenciais)", False, str(exc)[:300], int((time.time() - t0) * 1000))

    return {"ok": True, "resultados": resultados}


@router.post("/limpar")
def limpar(req: Request, dias: int = 90):
    """Remove eventos mais antigos que N dias."""
    require_permission(req, "parametros", "admin")
    return {"ok": True, "removidos": db.limpar_antigos(dias)}


# ── Middleware de captura de falhas ─────────────────────────────────────
class MonitorFalhasMiddleware(BaseHTTPMiddleware):
    """Registra 5xx e exceções não tratadas. Nunca altera a resposta em caso
    de sucesso, e nunca deixa o próprio monitoramento quebrar a requisição."""

    IGNORAR = ("/static", "/favicon", "/api/monitor/")

    async def dispatch(self, request: Request, call_next):
        caminho = request.url.path
        if caminho.startswith(self.IGNORAR):
            return await call_next(request)

        t0 = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — captura e re-levanta
            try:
                db.registrar(severidade="erro", origem="api", alvo=caminho,
                             status_code=500, duracao_ms=int((time.time() - t0) * 1000),
                             usuario=self._usuario(request),
                             detalhe=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001
                pass
            raise

        try:
            if response.status_code >= 500:
                db.registrar(severidade="erro", origem="api", alvo=caminho,
                             status_code=response.status_code,
                             duracao_ms=int((time.time() - t0) * 1000),
                             usuario=self._usuario(request),
                             detalhe=f"HTTP {response.status_code} em {request.method} {caminho}")
        except Exception:  # noqa: BLE001
            pass
        return response

    @staticmethod
    def _usuario(request: Request) -> str:
        try:
            from security import get_session
            sd = get_session(request, required=False)
            return (sd or {}).get("username", "") if sd else ""
        except Exception:  # noqa: BLE001
            return ""


def registrar_falha(origem: str, alvo: str, detalhe: str, usuario: str = "",
                    severidade: str = "erro") -> None:
    """Atalho para outros módulos registrarem uma falha no monitoramento."""
    db.registrar(severidade=severidade, origem=origem, alvo=alvo,
                 usuario=usuario, detalhe=detalhe)
