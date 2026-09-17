"""Teste de leitura da base Oracle do EBS, pela tela.

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

from core.mascara import sem_dado_de_acesso
from core.security import check_rate_limit, require_permission

MODULO = "parametros"
_log = logging.getLogger("ebs_oracle")

router = APIRouter(prefix="/api/ebs-oracle", tags=["EBS Oracle (leitura)"])

# Chaves que a camada de acesso procura no cofre. A senha entra na lista
# para a tela poder dizer se está resolvida — o valor nunca sai daqui.
CHAVES = ("ORACLE_EBS_USER", "ORACLE_EBS_PASS", "ORACLE_EBS_DSN",
          "ORACLE_CLIENT_LIB_DIR")
# Nenhum destes tem o valor mostrado. A senha é a óbvia, mas endereço,
# porta, instância e usuário identificam ONDE bater e com QUE conta — quem
# lê essa tela fica a uma senha de entrar no banco de outra área. Sobra o
# diretório do Instant Client, que é caminho de arquivo do servidor.
SIGILOSAS = {"ORACLE_EBS_PASS", "ORACLE_EBS_USER", "ORACLE_EBS_DSN"}

# Padrões que `integracoes/ebs_oracle.py::_config()` usa quando o cofre não
# tem a chave. Repetidos aqui porque aquele módulo importa o driver Oracle no
# topo, e a tela precisa responder mesmo sem o driver instalado.
#
# Usuário e endereço NÃO têm padrão, de propósito: endereço, porta, instância
# e usuário do banco são segredo, e um padrão aqui significa duas coisas
# ruins ao mesmo tempo — o dado de acesso volta para dentro do código, e a
# tela diz "resolvida" para uma chave que ninguém configurou, mandando o
# portal tentar um banco que não existe. Sem a chave no cofre a situação é
# "ausente", e `_config()` recusa a conexão dizendo qual chave falta.
# Só o diretório do Instant Client tem padrão: é caminho de arquivo do
# servidor, não credencial.
PADROES = {
    "ORACLE_CLIENT_LIB_DIR": "/usr/lib/oracle/21/client64/lib",
}


def _sem_credencial(exc: Exception) -> bool:
    """A falha foi 'ninguém configurou' e não 'o banco recusou'?

    São dois problemas com donos diferentes: chave que falta é trabalho de
    quem administra o cofre, e o portal precisa dizer QUAL chave. Antes de
    separá-los, a tela devolvia um erro de rede e mandava caçar firewall.
    O nome da classe é comparado por texto porque o módulo de acesso importa
    o driver Oracle no topo: onde o driver não está instalado, o import falha
    e não haveria classe para comparar.
    """
    return type(exc).__name__ == "EbsOracleSemCredencial"


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
        # Onde cada fonte tem a chave. Sem isto, um valor errado no cofre
        # local sombra o corporativo em silêncio — foi o que aconteceu: a
        # tela dizia "do cofre" e ninguém via que era o cofre errado.
        try:
            tem_corp = bool(cofre._corporativo(nome))
            tem_local = bool(cofre._local(nome))
        except Exception:  # noqa: BLE001
            tem_corp = tem_local = False
        padrao = PADROES.get(nome, "")
        # Três situações diferentes, e a tela precisa separá-las: veio do
        # cofre, vai usar o padrão do código, ou não há valor nenhum.
        if valor:
            situacao = "cofre"
        elif padrao:
            situacao, origem = "padrao", "padrão do código"
        else:
            situacao = "ausente"
        item = {"chave": nome, "situacao": situacao,
                "resolvida": situacao != "ausente", "fonte": origem,
                "no_corporativo": tem_corp, "no_local": tem_local}
        # Só o que não é segredo aparece; a senha fica no sim/não.
        efetivo = valor or padrao
        if efetivo and nome not in SIGILOSAS:
            item["valor"] = efetivo
        saida.append(item)
    return saida


@router.get("/situacao")
def situacao(req: Request):
    """O que o portal consegue resolver hoje, sem tocar no banco."""
    _exigir(req)
    from core import cofre
    # Prova o cofre com a chave de que ESTA tela depende. Com a chave
    # genérica, um cofre bom para o EBS apareceria como quebrado.
    ok_corp, detalhe = cofre.diagnostico_corporativo("ORACLE_EBS_PASS")
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
        if _sem_credencial(exc):
            raise HTTPException(503, str(exc)) from exc
        _log.warning("Teste de acesso à base do EBS falhou: %s", exc)
        raise HTTPException(502, "Não foi possível ler a base do EBS: "
                         + sem_dado_de_acesso(str(exc))) from exc
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
        raise HTTPException(502, "Falha ao listar objetos: "
                         + sem_dado_de_acesso(str(exc))) from exc
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
        raise HTTPException(502, f"Falha ao descrever {alvo}: "
                         + sem_dado_de_acesso(str(exc))) from exc
    if not linhas:
        raise HTTPException(404, f"{owner}.{alvo} não foi encontrado ou a conta "
                                 f"não enxerga esse objeto.")
    return {"objeto": alvo, "owner": owner.strip().upper() or "APPS",
            "total": len(linhas), "colunas": linhas}


# Só leitura, e dito de duas formas: a transação já é READ ONLY na camada de
# acesso, e aqui a consulta é recusada antes de sair se não começar por SELECT
# ou WITH. A primeira barreira protege o banco; esta protege quem digitou.
import re as _re

_INICIO_PERMITIDO = _re.compile(r"^\s*(select|with)\b", _re.IGNORECASE)
_LIMITE_PADRAO = 200
_LIMITE_TETO = 5000


def _sem_comentarios_sql(texto: str) -> str:
    """Tira `--` e `/* */` do SQL, respeitando o que está entre aspas.

    Quem testa uma consulta nesta tela cola o SQL de onde estava escrevendo,
    com comentário no topo. Sem isto, `-- rascunho` na primeira linha fazia a
    checagem de "começa em SELECT" reprovar, e um `;` dentro de um comentário
    reprovava por "duas consultas" — as duas mensagens mandando corrigir algo
    que não era o problema.

    O que sobra daqui é o que vai ser validado E executado: o texto conferido
    e o texto que roda no banco são o mesmo, sem uma segunda leitura no meio
    onde um comentário pudesse esconder alguma coisa.
    """
    saida: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        c = texto[i]
        if c == "'":
            # Em Oracle a aspa dentro da string é dobrada ('') — o laço sai
            # dela naturalmente e volta a entrar, o que dá no mesmo.
            j = texto.find("'", i + 1)
            j = n if j < 0 else j + 1
            saida.append(texto[i:j])
            i = j
            continue
        if texto.startswith("--", i):
            j = texto.find("\n", i)
            i = n if j < 0 else j
            continue
        if texto.startswith("/*", i):
            j = texto.find("*/", i + 2)
            i = n if j < 0 else j + 2
            saida.append(" ")
            continue
        saida.append(c)
        i += 1
    return "".join(saida)


def _validar_sql(sql: str) -> str:
    limpo = _sem_comentarios_sql(sql or "").strip().rstrip(";").strip()
    if not limpo:
        raise HTTPException(422, "Escreva a consulta.")
    if len(limpo) > 20000:
        raise HTTPException(422, "Consulta grande demais.")
    if not _INICIO_PERMITIDO.match(limpo):
        raise HTTPException(422, "Só SELECT ou WITH. Esta tela não escreve no EBS.")
    # Uma consulta só. Com duas, a segunda passaria sem a checagem acima.
    if ";" in limpo:
        raise HTTPException(422, "Uma consulta por vez — tire o ';' do meio.")
    return limpo


def _consultas_nomeadas() -> tuple[dict, dict]:
    """QUERIES e BINDS do módulo de acesso, mesmo sem o driver Oracle.

    O módulo importa `oracledb` no topo; sem o driver, o import estoura e a
    tela ficaria sem a lista. Ler o dicionário do fonte por regex é feio,
    mas mantém a tela útil num servidor onde o driver ainda não foi instalado.
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
    """As consultas nomeadas (as mesmas do módulo Gestão de Compras) e seus binds.

    O TEXTO do SQL não vai junto, de propósito. Ele traz esquema, tabelas e
    colunas do EBS — o mapa da base de outra área — e a resposta desta rota
    fica visível em qualquer aba de rede do navegador, salva em HAR e colada
    em chamado. A tela só precisa saber o NOME da consulta e quais parâmetros
    pedir; quem precisa ler o SQL o lê em integracoes/ebs_oracle.py.
    """
    _exigir(req)
    queries, binds = _consultas_nomeadas()
    return {"consultas": [{"nome": n, "binds": list(binds.get(n, ()))}
                          for n in sorted(queries)]}


@router.post("/consultar")
def consultar(body: dict, req: Request):
    """Roda um SELECT na base do EBS e devolve as linhas.

    A credencial vem por `core.cofre.obter`, ou seja, passa pelo loader do
    cofre antes de qualquer outra fonte — nada é lido de arquivo por conta
    própria. A sessão é só-leitura e tem teto de linhas e de tempo.
    """
    _exigir(req)
    check_rate_limit(req, "api")
    nome = str((body or {}).get("nome", "") or "").strip().lower()
    esperados: set[str] | None = None
    if nome:
        queries, binds_da_consulta = _consultas_nomeadas()
        if nome not in queries:
            raise HTTPException(422, f"Consulta '{nome}' não existe. Veja /api/ebs-oracle/consultas.")
        sql = _validar_sql(queries[nome])
        esperados = set(binds_da_consulta.get(nome, ()))
    else:
        sql = _validar_sql(str((body or {}).get("sql", "")))
    binds = (body or {}).get("binds") or {}
    if not isinstance(binds, dict):
        raise HTTPException(422, "Os parâmetros devem vir como objeto {nome: valor}.")
    # Nome de bind é identificador; valor vai como bind variable, nunca
    # concatenado — é o que separa parâmetro de injeção. A variável do laço
    # não se chama `nome`: essa é a consulta, e reusá-la aqui é armadilha
    # para quem mexer neste trecho depois.
    for chave in binds:
        if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", str(chave)):
            raise HTTPException(422, f"Nome de parâmetro inválido: {chave}")
    # Numa consulta nomeada se sabe exatamente quais parâmetros ela usa.
    # Mandar um que ela não usa abria conexão e voltava ORA-01036 ("illegal
    # variable name/number") — um erro do banco para um engano que dava para
    # ver aqui, de graça, e que diz o que fazer.
    if esperados is not None:
        sobrando = sorted(set(map(str, binds)) - esperados)
        if sobrando:
            raise HTTPException(
                422, f"A consulta '{nome}' não usa: {', '.join(sobrando)}. "
                     f"Ela espera: {', '.join(sorted(esperados)) or 'nenhum parâmetro'}.")
    try:
        limite = int((body or {}).get("limite") or _LIMITE_PADRAO)
    except (TypeError, ValueError):
        limite = _LIMITE_PADRAO
    limite = max(1, min(limite, _LIMITE_TETO))

    import time
    inicio = time.monotonic()
    try:
        from integracoes import ebs_oracle
        linhas = ebs_oracle.query(sql, binds, max_rows=limite)
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        if _sem_credencial(exc):
            raise HTTPException(503, str(exc)) from exc
        _log.warning("Consulta à base do EBS falhou: %s", exc)
        raise HTTPException(502, "A base do EBS recusou a consulta: "
                         + sem_dado_de_acesso(str(exc))) from exc
    ms = int((time.monotonic() - inicio) * 1000)
    colunas = list(linhas[0].keys()) if linhas else []
    return {
        "total": len(linhas),
        # Quem pediu 200 e recebeu 200 precisa saber que pode haver mais.
        "truncado": len(linhas) >= limite,
        "limite": limite,
        "ms": ms,
        "colunas": colunas,
        "linhas": linhas,
    }
