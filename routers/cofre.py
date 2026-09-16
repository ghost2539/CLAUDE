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
import re

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
    ("ServiceNow", ("SN_API_USER", "SN_API_PASS")),
    ("MDM", ("MDM_USUARIO", "MDM_SENHA")),
)

# Nomes cujo valor nunca aparece, nem parcialmente. A lista é deliberadamente
# larga: aqui o erro de sobrar um nome custa uma coluna com "—", e o erro de
# faltar um custa um segredo publicado numa tela.
_MARCAS_DE_SEGREDO = (
    "PASS", "PWD", "SENHA", "CHAVE", "KEY", "SECRET", "SEGREDO", "TOKEN",
    "CREDENCIAL", "CREDENTIAL", "AUTH", "COOKIE", "SESSION",
    # URL e DSN carregam senha embutida com frequência (user:senha@host).
    "URL", "URI", "DSN", "CONN",
)


def _e_segredo(nome: str) -> bool:
    return any(p in nome.upper() for p in _MARCAS_DE_SEGREDO)


def _exigir(req: Request) -> dict:
    return require_permission(req, MODULO, "admin")


def _sondar(nome: str) -> dict:
    """Uma chave: onde está, se resolveu, e o tamanho. Sem o valor."""
    import os

    from core import cofre

    # `_somente_cofre` é o que está DE FATO no arquivo do cofre. O `s()`
    # oficial cai para os.environ quando a chave não existe; contar isso
    # como "veio do cofre" esconderia exatamente o que se quer enxergar.
    try:
        corp = cofre._somente_cofre(nome)
    except Exception as exc:  # noqa: BLE001
        corp = ""
        _log.debug("cofre corporativo falhou em %s: %s", nome, exc)
    try:
        local = cofre._local(nome)
    except Exception:  # noqa: BLE001
        local = ""
    ambiente = os.environ.get(nome, "")
    valor = corp or local or ambiente

    # Com o arquivo do cofre legível, a lista de nomes é a verdade e não há
    # dúvida. Sem ela, só resta o `s()` — que mistura cofre e ambiente: se
    # os dois valores batem, não dá para dizer de qual vieram, e a tela
    # precisa admitir isso em vez de escolher.
    try:
        nomes_do_cofre = cofre.chaves_corporativas()
    except Exception:  # noqa: BLE001
        nomes_do_cofre = []
    if nomes_do_cofre:
        do_cofre = nome in nomes_do_cofre and bool(corp)
        indistinguivel = False
    else:
        indistinguivel = bool(corp and ambiente and corp == ambiente)
        do_cofre = bool(corp) and not indistinguivel

    item = {
        "chave": nome,
        "resolvida": bool(valor),
        "fonte": ("cofre corporativo" if do_cofre else
                  "ambiente (pelo cofre)" if indistinguivel else
                  "cofre local" if local else
                  "ambiente" if ambiente else "não definido"),
        "no_corporativo": do_cofre,
        "no_local": bool(local),
        "no_ambiente": bool(ambiente),
        "indistinguivel": indistinguivel,
        "tamanho": len(valor),
    }
    # Quem tem a chave, e todos concordam? Comparar não revela nada, e é o
    # que faltava enxergar quando o cofre local sombreou a credencial certa.
    # A ordem é corporativo → local → ambiente: o local ganha do ambiente,
    # então um valor velho esquecido ali derruba a variável nova em silêncio.
    tem = [(rotulo, v) for rotulo, v in
           (("cofre corporativo", corp if do_cofre else ""),
            ("cofre local", local),
            ("ambiente", ambiente))
           if v]
    item["fontes_com_valor"] = [rotulo for rotulo, _ in tem]
    item["divergente"] = len({v for _, v in tem}) > 1
    # Sombreamento: mais de uma fonte tem a chave. Vale avisar mesmo quando
    # os valores batem — no dia em que uma mudar, a outra continua mandando.
    item["sombreado"] = len(tem) > 1
    # Só o que não é segredo aparece — usuário e host ajudam a conferir se o
    # valor é o esperado; senha e chave, nunca.
    if valor and not _e_segredo(nome):
        item["valor"] = valor
    return item


def _inventario_corporativo() -> dict:
    """Como o portal chega ao cofre corporativo, e que nomes existem lá.

    Responde de uma vez às duas perguntas que travam o diagnóstico: "o
    serviço alcança o cofre?" e "a chave existe com outro nome?". Nomes,
    nunca valores — e o dono/permissão do arquivo, que é o que se pede ao
    time quando a resposta é "não alcança".
    """
    from core import cofre

    mod = None
    try:
        mod = cofre._resolver_modulo()
    except Exception as exc:  # noqa: BLE001
        _log.debug("módulo do cofre não resolveu: %s", exc)
    funcao = ""
    if mod is not None:
        fn = cofre._funcao_do_modulo(mod)
        funcao = getattr(fn, "__name__", "") if fn else ""

    try:
        nomes = cofre.chaves_corporativas()
    except Exception:  # noqa: BLE001
        nomes = []

    return {
        "ligado": cofre.USAR_CORPORATIVO,
        "comando_externo": cofre.COMANDO,
        "modulo_carregado": mod is not None,
        "modulo_via": getattr(cofre, "_modulo_via", ""),
        "funcao": funcao,
        "arquivo_do_cofre": cofre.CAMINHO_ARQUIVO,
        # Dono, grupo, modo e se ESTE processo lê. É o pedido exato a fazer
        # ao time quando o arquivo existe e o serviço não alcança.
        "acesso": cofre.acesso_ao_arquivo(),
        "sabe_listar": bool(nomes),
        "nomes": nomes,
    }


# O arquivo de ambiente que a unit do systemd carrega com EnvironmentFile.
# Este é NOSSO arquivo, não o cofre do time: ler os nomes daqui é legítimo, e
# é o que explica de onde vêm as variáveis que o processo tem.
ENVS_DO_SERVICO = (
    "/var/www/vcreports/portal-spare/data/environment",
    "/etc/portal_operacoes_spare_testes/environment",
    "/etc/portal_operacoes_spare/environment",
)


def _ambiente_do_servico() -> dict:
    """Qual arquivo de ambiente a unit carrega, e que nomes ele define.

    Responde a pergunta que ficou no ar: se uma credencial funciona sem
    estar no cofre nem na unit, ela veio daqui. E é aqui que se acrescenta
    a próxima, sem precisar mexer na unit — basta reiniciar o serviço.

    Nomes e marcadores, nunca valores.
    """
    import os
    import re as _re
    from pathlib import Path as _P

    escolhido = os.environ.get("PORTAL_ENV_FILE", "")
    candidatos = [escolhido] if escolhido else list(ENVS_DO_SERVICO)
    for caminho in candidatos:
        if not caminho:
            continue
        p = _P(caminho)
        info = {"caminho": caminho, "existe": p.is_file(), "legivel": False,
                "chaves": [], "erro": ""}
        if not info["existe"]:
            continue
        try:
            texto = p.read_text(encoding="utf-8", errors="replace")
        except PermissionError:
            info["erro"] = "existe, mas o serviço não consegue ler"
            return info
        except OSError as exc:
            info["erro"] = str(exc)
            return info
        info["legivel"] = True
        for linha in texto.splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            nome, _, valor = linha.partition("=")
            nome = nome.strip().removeprefix("export ").strip()
            if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", nome):
                continue
            valor = valor.strip().strip('"').strip("'")
            # O marcador @cofre:NOME@ delega ao cofre — e com o cofre fora do
            # ar ele não resolve. Distinguir isso de um valor direto é o que
            # explica por que uma chave funciona e a outra não.
            ref = _re.fullmatch(r"@cofre:([A-Za-z0-9_.-]{1,64})@", valor)
            info["chaves"].append({
                "chave": nome,
                "marcador": bool(ref),
                "aponta_para": ref.group(1) if ref else "",
                "vazio": valor == "",
                # Está no arquivo mas não chegou ao processo? Então o systemd
                # não conseguiu ler a linha — valor com espaço, aspas abertas
                # ou cifrão sem escape derrubam a variável (às vezes as
                # seguintes junto). É a explicação mais comum para "mexi no
                # environment e o portal mudou de comportamento".
                "chegou_ao_processo": nome in os.environ,
            })
        return info
    return {"caminho": "", "existe": False, "legivel": False, "chaves": [],
            "erro": "nenhum arquivo de ambiente encontrado nos caminhos conhecidos"}


def _prefixo_em_uso(req) -> dict:
    """Qual subcaminho a página está usando, e de onde ele saiu.

    Quando o portal perde o prefixo, o navegador busca CSS e JS no lugar
    errado e a tela aparece sem estilo nenhum. Como o prefixo vem do
    ambiente, uma linha quebrada no arquivo derruba a tela inteira — e o
    sintoma não parece ter nada a ver com a causa.
    """
    from config import get_settings
    from core.prefixo import prefixo as _prefixo
    cfg = get_settings()
    root = (req.scope.get("root_path") or "") if req is not None else ""
    return {
        "em_uso": _prefixo(req),
        "root_path_do_uvicorn": root,
        "app_base_path": getattr(cfg, "APP_BASE_PATH", ""),
        "origem": ("--root-path do uvicorn" if root
                   else "APP_BASE_PATH" if getattr(cfg, "APP_BASE_PATH", "")
                   else "nenhuma — a página vai sem prefixo"),
    }


def _seguro(rotulo: str, fn, padrao):
    """Roda um pedaço do diagnóstico sem deixar que ele derrube o resto.

    Esta tela existe para explicar falhas. Se ela própria devolver
    "internal server error", não sobra nada para diagnosticar — então cada
    parte responde por si, e a que quebrar aparece como erro no lugar dela.
    """
    try:
        return fn(), ""
    except Exception as exc:  # noqa: BLE001
        _log.warning("Diagnóstico do cofre: '%s' falhou: %s", rotulo, exc, exc_info=True)
        return padrao, f"{type(exc).__name__}: {exc}"


@router.get("/diagnostico")
def diagnostico(req: Request):
    """O retrato completo, do ponto de vista do processo do portal."""
    _exigir(req)
    from core import cofre
    import getpass
    import os

    erros: dict[str, str] = {}

    def parte(rotulo, fn, padrao):
        valor, erro = _seguro(rotulo, fn, padrao)
        if erro:
            erros[rotulo] = erro
        return valor

    try:
        quem = getpass.getuser()
    except Exception:  # noqa: BLE001
        quem = str(os.getuid())

    # O core sabe dizer por que o cofre responde ou não — módulo importado,
    # arquivo lido direto, arquivo sem permissão, desligado por configuração.
    ok, detalhe = parte("prova do cofre corporativo",
                        cofre.diagnostico_corporativo, (False, ""))
    if not detalhe and "prova do cofre corporativo" in erros:
        detalhe = erros["prova do cofre corporativo"]

    resposta = {
        # Quem o portal É no servidor: é isso que decide o que ele lê.
        "usuario_do_servico": quem,
        "corporativo_ok": ok,
        "corporativo_detalhe": detalhe,
        "cofre_local": str(cofre.ARQ_COFRE),
        "cofre_local_existe": parte("cofre local", cofre.ARQ_COFRE.exists, False),
        "algoritmo": parte("algoritmo do cofre local", cofre.algoritmo, ""),
        "grupos": parte("chaves por assunto",
                        lambda: [{"nome": nome, "chaves": [_sondar(k) for k in chaves]}
                                 for nome, chaves in GRUPOS], []),
        # Os nomes que o cofre expõe. Responde "a chave existe com outro
        # nome?" sem chutar um por vez — só nomes, nunca valores.
        "inventario": parte("inventário do cofre", _inventario_corporativo, {}),
        # De onde vêm as variáveis que o processo tem sem estar no cofre.
        "ambiente_do_servico": parte("arquivo de ambiente do serviço",
                                     _ambiente_do_servico, {}),
        # Sem prefixo, o navegador busca CSS e JS no lugar errado e a tela
        # aparece crua. Como ele vem do ambiente, entra no mesmo diagnóstico.
        "prefixo": parte("prefixo em uso", lambda: _prefixo_em_uso(req), {}),
    }
    # Nada some em silêncio: o que falhou vai nomeado para a tela.
    resposta["erros"] = erros
    return resposta


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


@router.get("/tudo")
def tudo(req: Request):
    """Tudo o que o serviço enxerga, das três fontes, em uma lista só.

    Serve para conferir de uma vez o que está disponível — e, principalmente,
    para achar a chave que existe com um nome que ninguém adivinharia.
    Nomes sempre; valor só quando o nome não denuncia um segredo.
    """
    _exigir(req)
    import os
    from core import cofre

    erros: dict[str, str] = {}
    nomes: set[str] = set()
    inv, erro = _seguro("inventário do cofre", _inventario_corporativo, {})
    if erro:
        erros["inventário do cofre"] = erro
    nomes.update(inv.get("nomes", []))  # vazio: o arquivo do cofre não é legível
    locais, erro = _seguro("cofre local", cofre.listar, [])
    if erro:
        erros["cofre local"] = erro
    nomes.update(locais)
    nomes.update(os.environ)

    itens = []
    for n in sorted(nomes):
        item, erro = _seguro(f"chave {n}", lambda n=n: _sondar(n),
                             {"chave": n, "resolvida": False, "fonte": "erro",
                              "tamanho": 0})
        if erro:
            erros[f"chave {n}"] = erro
        itens.append(item)
    return {
        "total": len(itens),
        # Quantas vêm de cada fonte: é o número que diz se o cofre respondeu.
        "por_fonte": {
            rotulo: sum(1 for i in itens if i["fonte"] == rotulo)
            for rotulo in ("cofre corporativo", "cofre local", "ambiente")
        },
        "itens": itens,
        "erros": erros,
    }


@router.post("/sondar-varios")
def sondar_varios(body: dict, req: Request):
    """Sonda uma lista de nomes de uma vez.

    Quando o arquivo do cofre não é legível por este processo, ele só
    responde por nome — e esta é a única forma de "procurar": dizer os
    nomes candidatos e ver quais respondem.
    Aceita o texto colado de qualquer jeito — vírgula, espaço ou uma por
    linha —, porque quem tem a lista costuma tê-la em algum desses formatos.
    """
    _exigir(req)
    import re as _re
    bruto = str((body or {}).get("nomes", ""))[:20000]
    candidatos: list[str] = []
    for pedaco in _re.split(r"[\s,;]+", bruto):
        nome = pedaco.strip().upper()
        if nome and nome.replace("_", "").replace("-", "").isalnum():
            if nome not in candidatos:
                candidatos.append(nome)
        if len(candidatos) >= 300:
            break
    if not candidatos:
        raise HTTPException(422, "Informe ao menos um nome de chave.")
    itens = [_sondar(n) for n in candidatos]
    return {"total": len(itens),
            "resolvidas": sum(1 for i in itens if i["resolvida"]),
            "itens": itens}
