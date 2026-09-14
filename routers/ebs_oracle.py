"""Teste de leitura da base Oracle do EBS (BASE_REMOVIDA), pela tela.

A camada de acesso é `integracoes/ebs_oracle.py`: conexão só-leitura, com
timeout, teto de linhas e bind variables. Aqui ficam só as rotas que a tela
de Parâmetros usa para **provar o acesso** antes de qualquer consulta de
negócio.

Nenhuma senha passa por aqui. A credencial é resolvida por referência no
cofre (`ORACLE_EBS_USER`, `ORACLE_EBS_PASS`, `ORACLE_EBS_DSN`), então a
tela mostra de onde cada valor veio — nunca o valor em si.

Tudo é `admin`: é base de produção de outra área.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from core.security import check_rate_limit, require_permission

MODULO = "parametros"
_log = logging.getLogger("ebs_oracle")

router = APIRouter(prefix="/api/ebs-oracle", tags=["EBS Oracle (leitura)"])

# Chaves que a camada de acesso procura no cofre. A senha entra na lista
# para a tela poder dizer se está resolvida — o valor nunca sai daqui.
CHAVES = ("ORACLE_EBS_USER", "ORACLE_EBS_PASS", "ORACLE_EBS_DSN",
          "ORACLE_CLIENT_LIB_DIR")
SIGILOSAS = {"ORACLE_EBS_PASS"}


def _exigir(req: Request) -> dict:
    return require_permission(req, MODULO, "admin")


def _situacao_das_chaves() -> list[dict]:
    """Por chave: se foi resolvida e de qual fonte. Sem revelar o valor."""
    from core import cofre
    saida = []
    for nome in CHAVES:
        try:
            valor = cofre.obter(nome, "")
            origem = cofre.fonte(nome) if valor else ""
        except Exception as exc:  # noqa: BLE001
            valor, origem = "", f"erro ao consultar o cofre: {exc}"
        item = {"chave": nome, "resolvida": bool(valor), "fonte": origem}
        # Só o que não é segredo aparece; a senha fica no sim/não.
        if valor and nome not in SIGILOSAS:
            item["valor"] = valor
        saida.append(item)
    return saida


@router.get("/situacao")
def situacao(req: Request):
    """O que o portal consegue resolver hoje, sem tocar no banco."""
    _exigir(req)
    from core import cofre
    ok_corp, detalhe = cofre.diagnostico_corporativo()
    return {
        "cofre_corporativo": ok_corp,
        "cofre_detalhe": detalhe,
        "chaves": _situacao_das_chaves(),
        "driver": _driver(),
    }


def _driver() -> dict:
    """O driver Oracle está instalado, e em qual modo?"""
    try:
        import oracledb
    except Exception as exc:  # noqa: BLE001
        return {"instalado": False, "detalhe": f"oracledb não importa: {exc}"}
    return {"instalado": True, "versao": getattr(oracledb, "__version__", "?")}


@router.post("/testar")
def testar(req: Request):
    """Conecta e responde quem sou, em qual instância e a hora do banco.

    É o `check_access()` da camada de acesso: não toca em tabela de
    negócio. Falha vira mensagem legível em vez de 500 sem explicação.
    """
    _exigir(req)
    check_rate_limit(req, "api")
    try:
        # O import entra no try: sem o driver Oracle instalado ele estoura
        # aqui, e um 500 sem texto não diz a ninguém o que instalar.
        from integracoes import ebs_oracle
        dados = ebs_oracle.check_access()
    except ImportError as exc:
        raise HTTPException(
            503, "O driver Oracle não está instalado neste servidor "
                 f"(pip install oracledb). Detalhe: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Teste de acesso ao BASE_REMOVIDA falhou: %s", exc)
        raise HTTPException(502, f"Não foi possível ler o BASE_REMOVIDA: {exc}") from exc
    return {"ok": True, "acesso": dados}


@router.get("/objetos")
def objetos(req: Request, prefixo: str = "", owner: str = "APPS",
            limite: int = 200):
    """Tabelas e views que a conta enxerga, por prefixo. Só catálogo."""
    _exigir(req)
    check_rate_limit(req, "api")
    prefixo = (prefixo or "").strip().upper()
    if len(prefixo) < 3:
        raise HTTPException(422, "Informe ao menos 3 letras do prefixo — "
                                 "listar o catálogo inteiro trava a sessão.")
    try:
        from integracoes import ebs_oracle
        linhas = ebs_oracle.list_objects(prefixo, owner=owner.strip().upper() or "APPS",
                                         limit=max(1, min(int(limite or 200), 1000)))
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Falha ao listar objetos: {exc}") from exc
    return {"total": len(linhas), "itens": linhas}


@router.get("/descrever")
def descrever(req: Request, objeto: str, owner: str = "APPS"):
    """Colunas de um objeto: nome, tipo, tamanho, aceita nulo."""
    _exigir(req)
    check_rate_limit(req, "api")
    alvo = (objeto or "").strip().upper()
    if not alvo:
        raise HTTPException(422, "Informe o nome do objeto.")
    try:
        from integracoes import ebs_oracle
        linhas = ebs_oracle.describe(alvo, owner=owner.strip().upper() or "APPS")
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Falha ao descrever {alvo}: {exc}") from exc
    if not linhas:
        raise HTTPException(404, f"{owner}.{alvo} não foi encontrado ou a conta "
                                 f"não enxerga esse objeto.")
    return {"objeto": alvo, "owner": owner.strip().upper() or "APPS",
            "total": len(linhas), "colunas": linhas}
