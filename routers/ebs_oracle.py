"""Leitura da base do EBS, pela tela.

A camada de acesso é `integracoes/ebs_oracle.py`: conexão só-leitura, com
timeout, teto de linhas e bind variables. Aqui ficam as rotas que a tela de
Parâmetros usa para **provar o acesso** e rodar as consultas de negócio.

Nenhuma senha passa por aqui, e nenhum endereço de banco tampouco. A
credencial é resolvida pelo cofre (`ORACLE_EBS_DSN`, `ORACLE_EBS_USER`,
`ORACLE_EBS_PASS`), então a tela mostra de ONDE cada valor veio — nunca o
valor em si. O DSN carrega host e instância: é dado de acesso e não sai
daqui nem para o administrador.

O que a tela executa são as consultas de `QUERIES`, pelo NOME, com bind
variables. SQL digitado na tela não existe mais: o filtro "começa com
SELECT" não impede subconsulta cara nem leitura de tabela que não é do
assunto, e foi isso que tirou esta camada do ar na revisão de segurança.

Tudo é `admin`: é base de produção de outra área.
"""
from __future__ import annotations

import logging
import re as _re
import time

from fastapi import APIRouter, HTTPException, Request

from core.security import check_rate_limit, require_permission

MODULO = "parametros"
_log = logging.getLogger("ebs_oracle")

router = APIRouter(prefix="/api/ebs-oracle", tags=["Base EBS (leitura)"])

_LIMITE_PADRAO = 200
_LIMITE_TETO = 5000

# Os mesmos nomes que `integracoes/ebs_oracle.py` procura no cofre. Ficam
# repetidos aqui de propósito: aquele módulo importa o driver Oracle no
# topo, e esta tela precisa dizer o que falta MESMO num servidor onde o
# driver ainda não foi instalado — que é justamente quando se pergunta.
CHAVES_DSN = ("ORACLE_EBS_DSN", "EBS_ORACLE_DSN")
CHAVES_USUARIO = ("ORACLE_EBS_USER", "EBS_ORACLE_USER")
CHAVES_SENHA = ("ORACLE_EBS_PASS", "EBS_ORACLE_PASS")
CHAVES_LIB = ("ORACLE_CLIENT_LIB_DIR", "EBS_ORACLE_CLIENT_LIB_DIR")
LIB_DIR_PADRAO = "/usr/lib/oracle/21/client64/lib"


def _exigir(req: Request) -> dict:
    return require_permission(req, MODULO, "admin")


def _credenciais() -> dict:
    """Situação das chaves, sem importar o driver Oracle.

    A camada de acesso importa `oracledb` no topo; num servidor onde o
    driver ainda não foi instalado, o import estoura e a tela ficaria sem
    dizer o que falta — que é justamente a informação de que se precisa
    nesse momento. Por isso a consulta ao cofre é feita aqui.
    """
    from core import cofre

    def resolver(nomes: tuple[str, ...]) -> tuple[str, str]:
        for nome in nomes:
            try:
                valor = cofre.obter(nome, "")
            except Exception as exc:  # noqa: BLE001
                _log.debug("cofre falhou em %s: %s", nome, exc)
                continue
            if valor:
                return valor, nome
        return "", ""

    def onde(chave: str) -> dict:
        """Em quais fontes a chave existe. Sombreamento é falha silenciosa:
        um valor velho no cofre local derruba o corporativo sem avisar."""
        if not chave:
            return {"no_corporativo": False, "no_local": False}
        try:
            return {"no_corporativo": bool(cofre._somente_cofre(chave)),
                    "no_local": bool(cofre._local(chave))}
        except Exception:  # noqa: BLE001
            return {"no_corporativo": False, "no_local": False}

    saida = []
    for rotulo, nomes, sigiloso, padrao in (
        # O DSN é sigiloso: host e instância são dado de acesso.
        ("Endereço do banco (DSN)", CHAVES_DSN, True, ""),
        ("Usuário", CHAVES_USUARIO, True, ""),
        ("Senha", CHAVES_SENHA, True, ""),
        # Caminho de instalação da máquina, não segredo.
        ("Instant Client", CHAVES_LIB, False, LIB_DIR_PADRAO),
    ):
        valor, chave = resolver(nomes)
        efetivo = valor or padrao
        item = {
            "rotulo": rotulo,
            "chave": chave or nomes[0],
            "chaves_aceitas": list(nomes),
            "resolvida": bool(efetivo),
            "fonte": (cofre.fonte(chave) if chave else
                      ("padrão do código" if padrao else "não definido")),
            **onde(chave),
        }
        if efetivo and not sigiloso:
            item["valor"] = efetivo
        saida.append(item)
    return {"chaves": saida,
            "completo": all(c["resolvida"] for c in saida)}


def _driver() -> dict:
    """O driver Oracle está instalado, e em qual versão?"""
    try:
        import oracledb
    except Exception as exc:  # noqa: BLE001
        return {"instalado": False,
                "detalhe": f"oracledb não importa: {exc}. Instale com "
                           f"'pip install oracledb' (consta em requirements.txt)."}
    return {"instalado": True, "versao": getattr(oracledb, "__version__", "?")}


@router.get("/situacao")
def situacao(req: Request):
    """O que o portal consegue resolver hoje, sem tocar no banco."""
    _exigir(req)
    from core import cofre
    ok_corp, detalhe = cofre.diagnostico_corporativo()
    cred = _credenciais()
    return {
        "cofre_corporativo": ok_corp,
        "cofre_detalhe": detalhe,
        "chaves": cred["chaves"],
        "completo": cred["completo"],
        "driver": _driver(),
    }


@router.post("/testar")
def testar(req: Request):
    """Conecta e responde quem sou, em qual instância e a hora do banco.

    É o `check_access()` da camada de acesso: não toca em tabela de
    negócio. Falha vira mensagem legível em vez de 500 sem explicação.
    """
    _exigir(req)
    check_rate_limit(req, "api")
    try:
        from integracoes import ebs_oracle
        dados = ebs_oracle.check_access()
    except ImportError as exc:
        raise HTTPException(
            503, "O driver Oracle não está instalado neste servidor "
                 f"(pip install oracledb). Detalhe: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Teste de acesso à base do EBS falhou: %s", exc)
        raise _erro(exc) from exc
    return {"ok": True, "acesso": dados}


def _erro(exc: Exception) -> HTTPException:
    """Traduz a falha para a tela, separando o que cada uma significa.

    Falta de credencial é 503 (configure o cofre), não 502: ninguém
    recusou nada — a conexão nem foi tentada. Trocar os dois manda quem
    lê a mensagem procurar problema no banco de outra área.
    """
    if type(exc).__name__ == "EbsOracleErro":
        return HTTPException(503, str(exc))
    return HTTPException(502, _limpo(exc))


def _limpo(exc: Exception) -> str:
    """Mensagem do banco sem o DSN dentro.

    O oracledb põe o endereço de conexão no texto de vários erros
    (ORA-12154, ORA-12541). Repassar cru publicaria na tela exatamente o
    que não pode ficar nem no repositório.
    """
    texto = str(exc)
    try:
        from core.cofre import obter
        for nome in CHAVES_DSN:
            dsn = obter(nome, "")
            if dsn:
                texto = texto.replace(dsn, "<endereço do banco>")
                # Host e instância também aparecem soltos, sem o DSN inteiro.
                for pedaco in _re.split(r"[/:@]", dsn):
                    if len(pedaco) > 3:
                        texto = texto.replace(pedaco, "<omitido>")
                break
    except Exception:  # noqa: BLE001
        pass
    return f"A base do EBS recusou: {texto}"


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
    if not _re.fullmatch(r"[A-Z0-9_%]{3,60}", prefixo):
        raise HTTPException(422, "Prefixo aceita letras, números e '_'.")
    if not _re.fullmatch(r"[A-Z0-9_]{1,30}", (owner or "").strip().upper()):
        raise HTTPException(422, "Owner inválido.")
    try:
        from integracoes import ebs_oracle
        itens = ebs_oracle.list_objects(prefixo, owner.strip().upper(),
                                        max(1, min(int(limite), 1000)))
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Catálogo do EBS falhou: %s", exc)
        raise _erro(exc) from exc
    return {"total": len(itens), "itens": itens}


@router.get("/descrever")
def descrever(req: Request, objeto: str, owner: str = "APPS"):
    """As colunas de uma tabela ou view. Só catálogo, nenhum dado."""
    _exigir(req)
    check_rate_limit(req, "api")
    objeto = (objeto or "").strip().upper()
    if not _re.fullmatch(r"[A-Z0-9_$#]{1,60}", objeto):
        raise HTTPException(422, "Nome de objeto inválido.")
    if not _re.fullmatch(r"[A-Z0-9_]{1,30}", (owner or "").strip().upper()):
        raise HTTPException(422, "Owner inválido.")
    try:
        from integracoes import ebs_oracle
        colunas = ebs_oracle.describe(objeto, owner.strip().upper())
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise _erro(exc) from exc
    return {"objeto": objeto, "owner": owner.strip().upper(),
            "total": len(colunas), "colunas": colunas}


def _consultas_nomeadas() -> tuple[dict, dict]:
    """QUERIES e BINDS do módulo de acesso, mesmo sem o driver Oracle.

    O módulo importa `oracledb` no topo; sem o driver, o import estoura e a
    tela ficaria sem a lista. Ler o dicionário do fonte é feio, mas mantém
    a tela útil num servidor onde o driver ainda não foi instalado.
    """
    try:
        from integracoes import ebs_oracle
        return dict(ebs_oracle.QUERIES), dict(ebs_oracle.BINDS)
    except ImportError:
        from pathlib import Path as _P
        fonte = (_P(__file__).resolve().parent.parent / "integracoes" / "ebs_oracle.py"
                 ).read_text(encoding="utf-8")
        escopo: dict = {"re": _re}
        exec(_re.search(r"QUERIES: dict\[str, str\] = \{.*?\n\}\n", fonte, _re.S).group(0), escopo)  # noqa: S102
        exec(_re.search(r"BINDS: dict.*?\n\}\n", fonte, _re.S).group(0), escopo)  # noqa: S102
        return escopo["QUERIES"], escopo["BINDS"]


@router.get("/consultas")
def consultas(req: Request):
    """As consultas nomeadas — as mesmas que o módulo Gestão de Compras usa."""
    _exigir(req)
    queries, binds = _consultas_nomeadas()
    return {"consultas": [{"nome": n, "binds": list(binds.get(n, ())), "sql": queries[n]}
                          for n in sorted(queries)]}


@router.post("/consultar")
def consultar(body: dict, req: Request):
    """Roda uma consulta NOMEADA e devolve as linhas.

    Só nome: o SQL fica no código, versionado e revisável. A credencial vem
    pelo cofre, a sessão é só-leitura e tem teto de linhas e de tempo.
    """
    _exigir(req)
    check_rate_limit(req, "api")
    nome = str((body or {}).get("nome", "") or "").strip().lower()
    if not nome:
        raise HTTPException(422, "Escolha uma consulta. Veja /api/ebs-oracle/consultas.")
    queries, esperados = _consultas_nomeadas()
    if nome not in queries:
        raise HTTPException(422, f"Consulta '{nome}' não existe. "
                                 "Veja /api/ebs-oracle/consultas.")

    binds = (body or {}).get("binds") or {}
    if not isinstance(binds, dict):
        raise HTTPException(422, "Os parâmetros devem vir como objeto {nome: valor}.")
    # Nome de bind é identificador; valor vai como bind variable, nunca
    # concatenado — é o que separa parâmetro de injeção.
    for chave in binds:
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(chave)):
            raise HTTPException(422, f"Nome de parâmetro inválido: {chave}")
    # Bind que a consulta não usa é erro de quem chamou, não algo a ignorar
    # em silêncio: o oracledb recusaria com uma mensagem bem pior.
    sobrando = sorted(set(binds) - set(esperados.get(nome, ())))
    if sobrando:
        raise HTTPException(422, f"A consulta '{nome}' não usa: {', '.join(sobrando)}. "
                                 f"Ela espera: {', '.join(esperados.get(nome, ())) or '—'}.")

    try:
        limite = int((body or {}).get("limite") or _LIMITE_PADRAO)
    except (TypeError, ValueError):
        limite = _LIMITE_PADRAO
    limite = max(1, min(limite, _LIMITE_TETO))

    inicio = time.monotonic()
    try:
        from integracoes import ebs_oracle
        linhas = ebs_oracle.run_named(nome, binds, max_rows=limite)
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.warning("Consulta '%s' à base do EBS falhou: %s", nome, exc)
        raise _erro(exc) from exc
    ms = int((time.monotonic() - inicio) * 1000)
    return {
        "nome": nome,
        "total": len(linhas),
        # Quem pediu 200 e recebeu 200 precisa saber que pode haver mais.
        "truncado": len(linhas) >= limite,
        "limite": limite,
        "ms": ms,
        "colunas": list(linhas[0].keys()) if linhas else [],
        "linhas": linhas,
    }
