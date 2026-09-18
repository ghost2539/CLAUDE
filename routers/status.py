"""Status router — system health check and dashboard summary."""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Request
from sqlalchemy import select, func, text

from db.portal import SessionLocal, ReceiptCycle, Repair, LocalAsset, engine
from core.mascara import sem_dado_de_acesso
from core.security import get_session

router = APIRouter(prefix="/api", tags=["Status"])

_INICIO = datetime.now(timezone.utc).isoformat()


# ── Qual código está no ar ────────────────────────────────────────
# Sem isto, "corrigi e não funcionou" e "o serviço não foi reiniciado"
# são indistinguíveis da tela — e já custaram rodadas inteiras.
_versao_cache: dict | None = None


def versao_do_codigo() -> dict:
    """Commit, data e ramo do código que este processo está executando."""
    global _versao_cache
    if _versao_cache is not None:
        return _versao_cache
    import os
    import subprocess
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    dados = {"commit": "", "commit_curto": "", "data": "", "ramo": "",
             "ambiente": os.getenv("AMBIENTE", "producao"),
             "iniciado_em": _INICIO}

    def _git(*args) -> str:
        try:
            saida = subprocess.run(["git", *args], cwd=str(raiz), timeout=5,
                                   capture_output=True, text=True)
            return saida.stdout.strip() if saida.returncode == 0 else ""
        except Exception:  # noqa: BLE001 — sem git, seguimos sem a informação
            return ""

    dados["commit"] = _git("rev-parse", "HEAD")
    dados["commit_curto"] = dados["commit"][:8]
    dados["data"] = _git("log", "-1", "--format=%cI")
    dados["ramo"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    dados["assunto"] = _git("log", "-1", "--format=%s")
    _versao_cache = dados
    return dados


@router.get("/versao")
def api_versao(req: Request):
    """Commit em execução. Responde a pergunta "a correção subiu?".

    Aberto de propósito, e só com o essencial: quem precisa conferir se o
    serviço foi reiniciado costuma estar no terminal do servidor, sem
    cookie de navegador nenhum. Exigir login aqui transforma a pergunta
    mais simples do deploy num problema à parte. Com sessão, vem o
    detalhe completo.
    """
    dados = versao_do_codigo()
    try:
        get_session(req)
    except Exception:  # noqa: BLE001 — sem sessão devolve o mínimo
        return {"commit_curto": dados["commit_curto"], "ramo": dados["ramo"],
                "ambiente": dados["ambiente"], "iniciado_em": dados["iniciado_em"]}
    return dados


@router.get("/status")
def system_status(req: Request):
    sd = get_session(req)

    # Banco do portal. Na tela ele aparece como "SQL", não pelo nome do
    # produto: é Postgres num servidor e SQLite no outro.
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        pg = {"connected": True}
    except Exception as e:
        # A mensagem do driver traz a string de conexão inteira quando a
        # conexão falha — host, porta, base e usuário. Isso é dado de acesso
        # e não vai para a tela.
        pg = {"connected": False, "error": sem_dado_de_acesso(str(e))}

    # Local asset base
    try:
        with SessionLocal() as s:
            count = s.scalar(
                select(func.count())
                .select_from(LocalAsset)
                .where(LocalAsset.active == True)  # noqa: E712
            ) or 0
        local = {"connected": True, "count": count}
    except Exception as e:
        local = {"connected": False, "error": str(e)}

    return {"ebs": _ebs(sd), "postgres": pg, "local": local,
            "servidor": _saude_servidor()}


@router.get("/dashboard/summary")
def dashboard_summary(req: Request):
    get_session(req)

    today = date.today()
    first = today.replace(day=1)

    with SessionLocal() as s:
        return {
            "recebidos_mes": s.scalar(
                select(func.count())
                .select_from(ReceiptCycle)
                .where(ReceiptCycle.received_date >= first)
            ) or 0,
            "recebidos_ano": s.scalar(
                select(func.count())
                .select_from(ReceiptCycle)
                .where(func.extract("year", ReceiptCycle.received_date) == today.year)
            ) or 0,
            "reparos_mes": s.scalar(
                select(func.count())
                .select_from(Repair)
                .where(Repair.repair_date >= first)
            ) or 0,
            "saving": float(
                s.scalar(
                    select(func.coalesce(func.sum(Repair.saving), 0))
                    .where(Repair.repair_date >= first)
                ) or 0
            ),
        }


def _ebs(sd: dict) -> dict:
    """Situação real do EBS.

    As consultas do portal rodam com a conta de serviço (mesma que o módulo de
    Monitoramento checa); a sessão EBS do próprio usuário só existe em login
    por AD. Apurar só pela sessão do usuário fazia a tela dizer "N/A" mesmo com
    as consultas funcionando.
    """
    if sd.get("auth_source") == "AD" and sd.get("ebs_auth"):
        return {"connected": True, "not_applicable": False,
                "modo": "sessão do usuário"}
    try:
        from routers.public_assets import _auth as _ebs_auth
        ok = bool(_ebs_auth())
        saida = {"connected": ok, "not_applicable": False,
                 "modo": "conta de serviço"}
        if not ok:
            saida["error"] = "conta de serviço não autenticou"
        return saida
    except Exception as exc:  # noqa: BLE001
        return {"connected": False, "not_applicable": False,
                "modo": "conta de serviço", "error": str(exc)[:300]}


def _saude_servidor() -> dict | None:
    """Recorte de saúde vindo do Monitoramento (memória, disco, carga, uptime
    e falhas críticas). Módulo ausente ou com defeito => bloco simplesmente não
    aparece, sem quebrar a tela de Status."""
    try:
        from routers.monitoramento import saude_servidor
        return saude_servidor(limite_falhas=5)
    except Exception:  # noqa: BLE001
        return None
