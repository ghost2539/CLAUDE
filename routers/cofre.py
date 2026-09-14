"""O SERVIÇO enxerga o cofre? — verificação feita de dentro do processo.

Por que existe: no servidor, só o root lê o cofre corporativo, e o usuário
do serviço tem sudo apenas para mexer no próprio serviço. Rodar o CLI no
shell responde sobre o SHELL, não sobre o portal — são usuários e ambientes
diferentes. Só o processo do portal pode responder se ele alcança o cofre.

Nenhum valor de segredo sai daqui. De cada chave se diz apenas se foi
resolvida, de qual fonte, e o tamanho — o suficiente para distinguir "não
existe" de "existe e veio vazio", sem revelar nada.

Tudo é `admin`.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request

from core.security import check_rate_limit, require_permission

MODULO = "parametros"
_log = logging.getLogger("cofre_diag")

router = APIRouter(prefix="/api/cofre", tags=["Cofre"])

# As chaves que o portal precisa, por assunto. Os Correios vêm primeiro
# porque são a referência que o time de segurança já validou — se elas
# resolvem, o caminho até o cofre está de pé.
GRUPOS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Correios", ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES")),
    ("Base EBS (Oracle)", ("ORACLE_EBS_USER", "ORACLE_EBS_PASS",
                           "ORACLE_EBS_DSN", "ORACLE_CLIENT_LIB_DIR")),
    ("ServiceNow", ("SN_API_USER", "SN_API_PASS")),
    ("MDM", ("MDM_USUARIO", "MDM_SENHA")),
)

# Nomes cujo valor nunca aparece, nem parcialmente.
def _e_segredo(nome: str) -> bool:
    return any(p in nome.upper() for p in
               ("PASS", "SENHA", "CHAVE", "SECRET", "TOKEN"))


def _exigir(req: Request) -> dict:
    return require_permission(req, MODULO, "admin")


def _sondar(nome: str) -> dict:
    """Uma chave: onde está, se resolveu, e o tamanho. Sem o valor."""
    from core import cofre
    try:
        corp = cofre._corporativo(nome)
    except Exception as exc:  # noqa: BLE001
        corp = ""
        _log.debug("cofre corporativo falhou em %s: %s", nome, exc)
    try:
        local = cofre._local(nome)
    except Exception:  # noqa: BLE001
        local = ""
    import os
    ambiente = os.environ.get(nome, "")
    valor = corp or local or ambiente
    item = {
        "chave": nome,
        "resolvida": bool(valor),
        "fonte": ("cofre corporativo" if corp else
                  "cofre local" if local else
                  "ambiente" if ambiente else "não definido"),
        "no_corporativo": bool(corp),
        "no_local": bool(local),
        "no_ambiente": bool(ambiente),
        "tamanho": len(valor),
    }
    # Só o que não é segredo aparece — usuário e DSN ajudam a conferir se o
    # valor é o esperado; senha e chave, nunca.
    if valor and not _e_segredo(nome):
        item["valor"] = valor
    return item


def _arquivo(caminho: str) -> dict:
    """Existe? O usuário do serviço consegue ler? Sem abrir o conteúdo."""
    import os
    from pathlib import Path as _P
    p = _P(caminho)
    existe = p.exists()
    return {
        "caminho": str(caminho),
        "existe": existe,
        "legivel": bool(existe and os.access(p, os.R_OK)),
        # Sem permissão de entrar na pasta, nem se sabe que o arquivo existe.
        "pasta_acessivel": os.access(p.parent, os.X_OK) if p.parent.exists() else False,
    }


@router.get("/diagnostico")
def diagnostico(req: Request):
    """O retrato completo, do ponto de vista do processo do portal."""
    _exigir(req)
    from core import cofre
    import getpass
    import os
    # Prova o cofre com uma chave dos Correios: é a que o time de segurança
    # já usa, então serve de referência quando as outras falham.
    ok, detalhe = cofre.diagnostico_corporativo("CORREIOS_USUARIO")
    grupos = [{"nome": nome, "chaves": [_sondar(k) for k in chaves]}
              for nome, chaves in GRUPOS]
    try:
        quem = getpass.getuser()
    except Exception:  # noqa: BLE001
        quem = str(os.getuid())
    return {
        # Quem o portal É no servidor: é isso que decide o que ele lê.
        "usuario_do_servico": quem,
        "corporativo_ok": ok,
        "corporativo_detalhe": detalhe,
        # O que mais importa quando falha: o serviço CONSEGUE LER o arquivo
        # do cofre corporativo? Permissão é a causa número um.
        "modulo_corporativo": _arquivo(cofre.CAMINHO_MODULO),
        "arquivo_corporativo": _arquivo(cofre.CAMINHO_ARQUIVO),
        # Dono, grupo e modo do arquivo do cofre: quando falta permissão, é
        # aqui que se vê em qual grupo o usuário do serviço precisa entrar.
        "permissoes_corporativo": cofre.acesso_ao_arquivo(),
        "onde_procura": cofre.onde_procura(),
        "cofre_local": str(cofre.ARQ_COFRE),
        "cofre_local_existe": cofre.ARQ_COFRE.exists(),
        "algoritmo": cofre.algoritmo(),
        "grupos": grupos,
    }


@router.get("/sondar/{nome}")
def sondar_uma(nome: str, req: Request):
    """Uma chave avulsa, para quando o nome não está na lista conhecida."""
    _exigir(req)
    limpo = (nome or "").strip().upper()
    if not limpo or not limpo.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(422, "Nome de chave inválido.")
    return _sondar(limpo)


@router.post("/testar-correios")
def testar_correios(req: Request):
    """A prova de ponta a ponta: autenticar nos Correios com o que o cofre deu.

    Resolver a chave só mostra que o cofre respondeu. Autenticar mostra que o
    valor é o certo — é o teste que o time de segurança já usa, e o único que
    separa "o serviço lê o cofre" de "o serviço lê a coisa certa".
    """
    _exigir(req)
    check_rate_limit(req, "api")
    chaves = [_sondar(k) for k in ("CORREIOS_USUARIO", "CORREIOS_CHAVE",
                                   "CORREIOS_CARTOES")]
    faltando = [c["chave"] for c in chaves if not c["resolvida"]]
    if faltando:
        return {"ok": False, "etapa": "cofre", "chaves": chaves,
                "detalhe": "O cofre não devolveu: " + ", ".join(faltando)}
    try:
        from routers import correios
        token = correios._correios_authenticate()
    except Exception as exc:  # noqa: BLE001
        detalhe = getattr(exc, "detail", None) or str(exc)
        _log.warning("Teste dos Correios falhou: %s", detalhe)
        return {"ok": False, "etapa": "api", "chaves": chaves,
                "detalhe": str(detalhe)}
    # O token é credencial: só o tamanho sai daqui.
    return {"ok": True, "etapa": "api", "chaves": chaves,
            "detalhe": f"Autenticado nos Correios — token de {len(token)} caracteres."}
