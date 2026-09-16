"""PO e projetos do EBS pela API do módulo Gestão de Compras.

O serviço do portal não lê o cofre corporativo; o módulo `/gestao_compras`
(sob o Apache) lê, e já expõe as consultas por HTTP. Estas rotas só
repassam: a credencial é a do portal no módulo, resolvida pelo cofre, e o
que volta é o que o módulo devolve. Nenhuma senha passa por aqui.

Tudo é `admin` enquanto se valida o caminho; quando virar dado de tela, a
permissão desce para o módulo de orçamento.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from core.security import check_rate_limit, require_permission

MODULO = "parametros"
_log = logging.getLogger("gestao_compras")

router = APIRouter(prefix="/api/gestao-compras", tags=["Gestão de Compras (API)"])


def _exigir(req: Request) -> dict:
    return require_permission(req, MODULO, "admin")


def _erro(exc: Exception) -> HTTPException:
    from integracoes.gestao_compras import GestaoComprasErro
    if isinstance(exc, GestaoComprasErro):
        # 4xx do lado de lá é problema de pedido ou credencial; o resto é 502,
        # porque para quem usa o portal o módulo é um serviço externo.
        codigo = exc.status if 400 <= exc.status < 500 and exc.status != 401 else 502
        if exc.status == 503:
            codigo = 503
        return HTTPException(codigo, str(exc))
    _log.warning("Gestão de Compras: %s", exc)
    return HTTPException(502, f"Falha ao falar com o Gestão de Compras: {exc}")


@router.get("/situacao")
def situacao(req: Request):
    """O que está configurado, sem tocar na rede."""
    _exigir(req)
    from config import get_settings
    from integracoes import gestao_compras as gc
    cfg = get_settings()
    return {
        "url": cfg.GESTAO_COMPRAS_URL,
        "verify_ssl": cfg.GESTAO_COMPRAS_VERIFY,
        "proxy": cfg.GESTAO_COMPRAS_PROXY or "",
        "timeout": cfg.GESTAO_COMPRAS_TIMEOUT,
        "credenciais": gc.credenciais_configuradas(),
        "acoes": {nome: list(p) for nome, p in gc.ACOES.items()},
        "chaves_cofre": {"usuario": list(gc.CHAVES_USUARIO), "senha": list(gc.CHAVES_SENHA)},
    }


@router.post("/testar")
def testar(req: Request):
    """Login no módulo e conferência da sessão — prova que a credencial vale."""
    _exigir(req)
    check_rate_limit(req, "api")
    from integracoes import gestao_compras as gc
    try:
        return {"ok": True, **gc.verificar()}
    except Exception as exc:  # noqa: BLE001
        raise _erro(exc) from exc


@router.get("/consultar")
def consultar(req: Request, acao: str, project: str = "", days: int | None = None,
              org: str = "", vendor: str = "", po: str = "", line: int | None = None,
              projects: str = ""):
    """Uma ação do api/oracle.php do módulo, com os parâmetros dela."""
    _exigir(req)
    check_rate_limit(req, "api")
    from integracoes import gestao_compras as gc
    import time
    inicio = time.monotonic()
    try:
        linhas = gc.consultar(acao.strip().lower(), project=project.strip(), days=days,
                              org=org.strip(), vendor=vendor.strip(), po=po.strip(),
                              line=line, projects=projects.strip())
    except Exception as exc:  # noqa: BLE001
        raise _erro(exc) from exc
    colunas: list[str] = []
    for linha in linhas:
        for k in linha:
            if k not in colunas:
                colunas.append(k)
    return {"acao": acao, "total": len(linhas), "ms": int((time.monotonic() - inicio) * 1000),
            "colunas": colunas, "linhas": linhas}
