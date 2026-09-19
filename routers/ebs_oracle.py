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
# Sem ORACLE_CLIENT_LIB_DIR: é do Instant Client (modo thick), que esta
# instalação não usa. `integracoes/ebs_oracle.py` continua aceitando a
# variável para quem precisar do thick — ela só não figura mais na tela.
CHAVES = ("ORACLE_EBS_USER", "ORACLE_EBS_PASS", "ORACLE_EBS_DSN")
# Nenhuma tem o valor mostrado. A senha é a óbvia, mas endereço, porta,
# instância e usuário identificam ONDE bater e com QUE conta — quem lê essa
# tela fica a uma senha de entrar no banco de outra área.
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
# Vazio de propósito. O único padrão que havia era o caminho do Instant
# Client, e ele saiu junto com a chave: além de não ser usado, era caminho
# de arquivo do servidor indo para a tela sem precisar.
PADROES: dict[str, str] = {}


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
    """Por chave: o nome e se foi LOCALIZADA. Nada além disso.

    A tela trazia também de qual fonte a chave veio e em qual cofre ela
    está, para flagrar o sombreamento (um valor velho no cofre local
    vencendo o corporativo em silêncio). Duas coisas mudaram:

    *   O cofre corporativo saiu do código (`cofre.USAR_CORPORATIVO` é
        False, as funções são stubs). Não há mais duas fontes para uma
        sombrear a outra — `no_corporativo` seria False sempre, e o aviso
        "só no local" acenderia em TODA chave. Aviso que acende sempre é
        aviso que se aprende a ignorar.
    *   Estas três chaves são todas dado de acesso: usuário, senha e
        endereço do banco. Dizer de onde o usuário do EBS veio já é contar
        onde procurar por ele.

    O JSON vai inteiro para o navegador, então o que some some aqui, não no
    desenho da tela.
    """
    from core import cofre
    saida = []
    for nome in CHAVES:
        try:
            achou = bool(cofre.obter(nome, ""))
        except Exception as exc:  # noqa: BLE001
            _log.debug("cofre falhou em %s: %s", nome, exc)
            achou = False
        saida.append({"chave": nome, "resolvida": achou})
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


# Nome de consulta é nome de arquivo: nada de subpasta nem de "..".
_RE_NOME_CONSULTA = _re.compile(r"^[a-z][a-z0-9_]{0,60}$")


def _consultas_nomeadas() -> tuple[dict, dict]:
    """As consultas nomeadas e seus binds, mesmo sem o driver Oracle.

    Lê `consultas/ebs/*.sql` direto. Antes isto importava
    `integracoes.ebs_oracle`, e como aquele módulo faz `import oracledb` no
    topo, num servidor sem o driver a tela ficava sem a lista — o contorno
    era arrancar o dicionário do arquivo-fonte com regex e rodar `exec`
    nele. Com as consultas em arquivo não há mais nem import nem `exec`:
    a pasta é a fonte, e é a MESMA pasta que o módulo de acesso usa, então
    tela e execução não podem divergir.
    """
    from pathlib import Path as _P
    pasta = _P(__file__).resolve().parent.parent / "consultas" / "ebs"
    queries, binds = {}, {}
    if not pasta.is_dir():
        return queries, binds
    for arq in sorted(pasta.glob("*.sql")):
        if not _RE_NOME_CONSULTA.match(arq.stem):
            continue
        sql = arq.read_text(encoding="utf-8")
        queries[arq.stem] = sql
        # Comentário fora antes de procurar bind: um `:coisa` escrito num
        # `--` viraria campo no formulário da tela.
        sem_comentario = _re.sub(r"--[^\n]*", "", sql)
        binds[arq.stem] = tuple(dict.fromkeys(
            _re.findall(r":([A-Za-z_][A-Za-z0-9_]*)", sem_comentario)))
    return queries, binds


@router.get("/consulta-de-ativos")
def consulta_de_ativos(req: Request):
    """O que a tela Consulta encontrou na base: colunas, campos e o SQL montado."""
    _exigir(req)
    check_rate_limit(req, "api")
    from integracoes import ebs_ativos
    try:
        cat = ebs_ativos.catalogo(recarregar=True)
    except ImportError as exc:
        raise HTTPException(503, f"Driver Oracle ausente neste servidor: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Falha ao ler o catálogo da base: "
                         + sem_dado_de_acesso(str(exc))) from exc
    return {"tabelas": {tab: sorted(cols) for tab, cols in sorted(cat.items())},
            "campos": ebs_ativos.mapa(cat),
            "sql": ebs_ativos.montar_sql(":termo", cat)}


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
