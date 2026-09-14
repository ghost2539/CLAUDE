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
    ("Base EBS (Oracle)", ("ORACLE_EBS_USER", "ORACLE_EBS_PASS",
                           "ORACLE_EBS_DSN", "ORACLE_CLIENT_LIB_DIR")),
    ("ServiceNow", ("SN_API_USER", "SN_API_PASS")),
    ("MDM", ("MDM_USUARIO", "MDM_SENHA")),
)

# Quando a chave "certa" não devolve valor, a pergunta seguinte é sempre a
# mesma: o cofre não responde, ou o nome é outro? Estes são os apelidos
# plausíveis da credencial do EBS — sondar todos de uma vez responde isso
# em um clique, em vez de um chute por vez.
ALTERNATIVAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Usuário do EBS", ("ORACLE_EBS_USER", "ORACLE_EBS_USUARIO",
                        "ORACLE_USER", "ORACLE_USERNAME", "EBS_ORACLE_USER",
                        "EBS_USER", "BASE_REMOVIDA_USER", "DB_ORACLE_USER")),
    ("Senha do EBS", ("ORACLE_EBS_PASS", "ORACLE_EBS_PASSWORD",
                      "ORACLE_EBS_SENHA", "ORACLE_PASS", "ORACLE_PASSWORD",
                      "EBS_ORACLE_PASS", "EBS_PASS", "BASE_REMOVIDA_PASS",
                      "DB_ORACLE_PASS")),
    ("Endereço do EBS", ("ORACLE_EBS_DSN", "ORACLE_DSN", "EBS_ORACLE_DSN",
                         "ORACLE_EBS_TNS", "ORACLE_TNS", "EBS_DSN")),
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
    # O loader do time resolve cofre -> os.environ -> default. Ou seja: com a
    # variável definida no arquivo de ambiente, s() devolve valor mesmo com o
    # cofre inacessível — e contabilizar isso como "veio do cofre" esconde
    # exatamente o que se quer enxergar. Quando os dois valores são iguais,
    # não há como distinguir, e a tela precisa dizer isso em vez de escolher.
    indistinguivel = bool(corp and ambiente and corp == ambiente)
    do_cofre = bool(corp) and not indistinguivel
    item = {
        "chave": nome,
        "resolvida": bool(valor),
        "fonte": ("cofre corporativo" if do_cofre else
                  "ambiente (pelo loader)" if indistinguivel else
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
            ("cofre local", local), ("ambiente", ambiente))
           if v]
    item["fontes_com_valor"] = [rotulo for rotulo, _ in tem]
    item["divergente"] = len({v for _, v in tem}) > 1
    # Sombreamento: mais de uma fonte tem a chave e a que vence não é a
    # última a ser configurada. Vale avisar mesmo quando os valores batem —
    # no dia em que uma mudar, a outra continua mandando.
    item["sombreado"] = len(tem) > 1
    # Só o que não é segredo aparece — usuário e DSN ajudam a conferir se o
    # valor é o esperado; senha e chave, nunca.
    if valor and not _e_segredo(nome):
        item["valor"] = valor
    return item


def _inventario_corporativo() -> dict:
    """O que dá para saber do cofre corporativo sem abrir nada.

    O loader (`/usr/local/lib/vcreports/vcreports_secrets.py`) expõe só
    `s(chave)`: ele responde por NOME, um de cada vez. Não há função de
    listar, e o arquivo do cofre não é para ser aberto por quem consome —
    tentar isso só gera "Permission denied" e a falsa impressão de que o
    cofre está quebrado. Então aqui não se lê arquivo nenhum: diz-se qual
    loader está carregado e por qual função, e o resto é sondagem por nome.
    """
    from core import cofre
    mod = None
    try:
        mod = cofre._resolver_modulo()
    except Exception as exc:  # noqa: BLE001
        _log.debug("loader do cofre não resolveu: %s", exc)
    funcao = ""
    if mod is not None:
        fn = cofre._funcao_do_modulo(mod)
        funcao = getattr(fn, "__name__", "") if fn else ""
    return {
        # Saída de emergência que já existe no core: quando o loader Python
        # não alcança o cofre mas outro programa alcança (o PHP do time, por
        # exemplo), VCREPORTS_SECRETS_CMD resolve chave por chave.
        "comando_externo": getattr(cofre, "COMANDO", ""),
        "modulo_carregado": mod is not None,
        "modulo_via": getattr(cofre, "_modulo_via", ""),
        "funcao": funcao,
        # Falso por construção, e dito de propósito: a tela precisa explicar
        # que a lista vazia não é falha, é o contrato do loader.
        "sabe_listar": False,
        "nomes": [],
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
            })
        return info
    return {"caminho": "", "existe": False, "legivel": False, "chaves": [],
            "erro": "nenhum arquivo de ambiente encontrado nos caminhos conhecidos"}


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

    # Prova o cofre com uma chave dos Correios: é a que o time de segurança
    # já usa, então serve de referência quando as outras falham.
    ok, detalhe = parte("prova do cofre corporativo",
                        lambda: cofre.diagnostico_corporativo("CORREIOS_USUARIO"),
                        (False, ""))
    if not detalhe and "prova do cofre corporativo" in erros:
        detalhe = erros["prova do cofre corporativo"]

    resposta = {
        # Quem o portal É no servidor: é isso que decide o que ele lê.
        "usuario_do_servico": quem,
        "corporativo_ok": ok,
        "corporativo_detalhe": detalhe,
        "onde_procura": parte("caminhos de busca", cofre.onde_procura, []),
        "cofre_local": str(cofre.ARQ_COFRE),
        "cofre_local_existe": parte("cofre local", cofre.ARQ_COFRE.exists, False),
        "algoritmo": parte("algoritmo do cofre local", cofre.algoritmo, ""),
        "grupos": parte("chaves por assunto",
                        lambda: [{"nome": nome, "chaves": [_sondar(k) for k in chaves]}
                                 for nome, chaves in GRUPOS], []),
        # Nomes que o cofre expõe e apelidos plausíveis da credencial do
        # EBS: responde "a chave tem outro nome?" sem chutar um por vez.
        "inventario": parte("inventário do cofre", _inventario_corporativo, {}),
        # De onde vêm as variáveis que o processo tem sem estar no cofre.
        "ambiente_do_servico": parte("arquivo de ambiente do serviço",
                                     _ambiente_do_servico, {}),
        "alternativas": parte("apelidos do EBS",
                              lambda: [{"nome": rotulo,
                                        "chaves": [_sondar(k) for k in chaves]}
                                       for rotulo, chaves in ALTERNATIVAS], []),
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
    nomes.update(inv.get("nomes", []))  # vazio: o loader não sabe listar
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

    Como o loader só responde por nome, esta é a única forma de "procurar"
    no cofre corporativo: dizer os nomes candidatos e ver quais respondem.
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


# Onde um loader de cofre pode morar. O caminho vem da tela, e vira `require`
# dentro do PHP — ou seja, vira código executado. Sem esta cerca, um admin
# distraído (ou um navegador comprometido) mandaria o serviço executar
# qualquer arquivo do disco. Admin já pode muito; não precisa poder isso.
PASTAS_DE_LOADER = ("/usr/local/lib/vcreports/", "/etc/vcreports/")
LOADER_PADRAO = "/usr/local/lib/vcreports/secrets.php"


@router.post("/testar-php")
def testar_php(body: dict, req: Request):
    """Roda a ponte PHP DE DENTRO do serviço e diz se o cofre respondeu.

    Só o serviço alcança o cofre — rodar `php` no terminal responde sobre o
    terminal. Então quem executa é o processo do portal, e o que volta é
    "respondeu / não respondeu / o que o PHP reclamou", nunca o valor.
    """
    _exigir(req)
    check_rate_limit(req, "api")
    import os
    import re as _re
    import shutil
    import subprocess
    from pathlib import Path as _P

    chave = str((body or {}).get("chave", "") or "CORREIOS_USUARIO").strip().upper()
    if not _re.fullmatch(r"[A-Z0-9_.-]{1,64}", chave):
        raise HTTPException(422, "Nome de chave inválido.")

    loader = str((body or {}).get("loader", "") or LOADER_PADRAO).strip()
    if not loader.endswith(".php") or not any(
            loader.startswith(pasta) for pasta in PASTAS_DE_LOADER):
        raise HTTPException(
            422, "O loader precisa ser um .php dentro de "
                 + " ou ".join(PASTAS_DE_LOADER) + ".")

    php = shutil.which("php")
    if not php:
        return {"ok": False, "etapa": "php",
                "detalhe": "O PHP não está instalado neste servidor, "
                           "então a ponte não tem como rodar."}

    ponte = _P(__file__).resolve().parent.parent / "scripts" / "cofre_php.php"
    if not ponte.is_file():
        return {"ok": False, "etapa": "ponte",
                "detalhe": f"A ponte não está no lugar esperado: {ponte}"}

    ambiente = dict(os.environ, VCREPORTS_SECRETS_PHP=loader)
    try:
        r = subprocess.run([php, str(ponte), chave, "--tamanho"],
                           capture_output=True, text=True, timeout=15,
                           env=ambiente)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "etapa": "execucao", "detalhe": str(exc)}

    saida = (r.stdout or "").strip()
    erro = (r.returncode and (r.stderr or "").strip()) or ""
    # A linha pronta para a unit: se funcionou, é só isto que falta.
    comando = f'{php} {ponte} {{chave}}'
    return {
        "ok": r.returncode == 0,
        "etapa": "cofre",
        "chave": chave,
        "loader": loader,
        "codigo": r.returncode,
        # --tamanho garante que só o comprimento sai daqui, nunca o valor.
        "detalhe": saida if r.returncode == 0 else (erro or "sem detalhe"),
        "comando_para_a_unit": comando if r.returncode == 0 else "",
        "variavel_do_loader": (f"VCREPORTS_SECRETS_PHP={loader}"
                               if r.returncode == 0 and loader != LOADER_PADRAO else ""),
    }
