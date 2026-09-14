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
    # Quem tem a chave, e todos concordam? Comparar não revela nada, e é o
    # que faltava enxergar quando o cofre local sombreou a credencial certa.
    # A ordem é corporativo → local → ambiente: o local ganha do ambiente,
    # então um valor velho esquecido ali derruba a variável nova em silêncio.
    tem = [(rotulo, v) for rotulo, v in
           (("cofre corporativo", corp), ("cofre local", local), ("ambiente", ambiente))
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


def _dono(caminho) -> dict:
    """Dono, grupo e modo. É o que o admin do cofre precisa saber para liberar."""
    import grp
    import os
    import pwd
    import stat as _st
    saida = {"dono": "", "grupo": "", "modo": ""}
    try:
        st = os.stat(caminho)
    except OSError:
        return saida
    saida["modo"] = oct(_st.S_IMODE(st.st_mode))[2:]
    try:
        saida["dono"] = pwd.getpwuid(st.st_uid).pw_name
    except Exception:  # noqa: BLE001
        saida["dono"] = str(st.st_uid)
    try:
        saida["grupo"] = grp.getgrgid(st.st_gid).gr_name
    except Exception:  # noqa: BLE001
        saida["grupo"] = str(st.st_gid)
    return saida


def _arquivo(caminho: str) -> dict:
    """Existe? O usuário do serviço consegue ler? Sem abrir o conteúdo."""
    import os
    from pathlib import Path as _P
    p = _P(caminho)
    existe = p.exists()
    item = {
        "caminho": str(caminho),
        "existe": existe,
        "legivel": bool(existe and os.access(p, os.R_OK)),
        # Sem permissão de entrar na pasta, nem se sabe que o arquivo existe.
        "pasta_acessivel": os.access(p.parent, os.X_OK) if p.parent.exists() else False,
    }
    item.update(_dono(p))
    # Quando nem o arquivo se deixa consultar, o dono da PASTA já diz em qual
    # grupo o usuário do serviço precisa entrar — é o dado que falta na hora
    # de pedir a liberação.
    item["pasta"] = dict(_dono(p.parent), caminho=str(p.parent))
    return item


# Formatos em que um cofre guarda par nome=valor. Só o NOME é capturado —
# o grupo de captura nunca alcança o valor, por construção.
_PADROES_NOME = (
    re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]{0,63})\s*=", re.M),
    re.compile(r"""define\(\s*['"]([A-Za-z_][A-Za-z0-9_.-]{0,63})['"]"""),
    re.compile(r"""['"]([A-Za-z_][A-Za-z0-9_.-]{0,63})['"]\s*=>"""),
    re.compile(r'"([A-Za-z_][A-Za-z0-9_.-]{0,63})"\s*:'),
)
# Um cofre é pequeno. Se o arquivo for enorme, não é o cofre — e ler tudo
# na memória do portal por engano seria o pior jeito de descobrir isso.
_TETO_LEITURA = 2 * 1024 * 1024


def _nomes_no_arquivo(caminho: str) -> dict:
    """Todos os nomes de chave que o arquivo contém. Nenhum valor.

    É o que responde de uma vez "o serviço enxerga o cofre?": se a lista
    vem cheia, enxerga; se vem o erro de permissão, não enxerga; se vem
    vazia, o arquivo não é o cofre.
    """
    import os
    info = {"caminho": caminho, "legivel": False, "total": 0,
            "nomes": [], "erro": "", "bytes": 0}
    try:
        tamanho = os.path.getsize(caminho)
    except OSError as exc:
        info["erro"] = str(exc)
        return info
    info["bytes"] = tamanho
    if tamanho > _TETO_LEITURA:
        info["erro"] = f"arquivo grande demais para ser um cofre ({tamanho} bytes)"
        return info
    try:
        with open(caminho, "r", encoding="utf-8", errors="replace") as f:
            texto = f.read()
    except PermissionError:
        info["erro"] = "sem permissão de leitura para o usuário do serviço"
        return info
    except OSError as exc:
        info["erro"] = str(exc)
        return info
    info["legivel"] = True
    nomes: set[str] = set()
    for padrao in _PADROES_NOME:
        nomes.update(padrao.findall(texto))
    info["nomes"] = sorted(nomes)[:400]
    info["total"] = len(nomes)
    return info


def _inventario_corporativo() -> dict:
    """Quais nomes o cofre corporativo expõe — só os nomes, nunca os valores.

    É o que separa "o serviço não alcança o cofre" de "a chave se chama
    outra coisa". Nem todo cofre sabe se listar; quando não sabe, dizemos
    isso em vez de fingir que a lista vazia é resposta.
    """
    from core import cofre
    nomes: set[str] = set()
    fontes: list[str] = []
    try:
        do_arquivo = cofre.chaves_corporativas()
    except Exception:  # noqa: BLE001
        do_arquivo = []
    if do_arquivo:
        nomes.update(do_arquivo)
        fontes.append(f"arquivo ({cofre.CAMINHO_ARQUIVO})")

    mod = None
    try:
        mod = cofre._resolver_modulo()
    except Exception as exc:  # noqa: BLE001
        _log.debug("módulo do cofre não resolveu: %s", exc)
    if mod is not None:
        for atributo in ("SECRETS", "_SECRETS", "SEGREDOS", "DATA", "_DATA"):
            alvo = getattr(mod, atributo, None)
            if isinstance(alvo, dict) and alvo:
                nomes.update(str(k) for k in alvo)
                fontes.append(f"módulo, atributo {atributo}")
                break
        for nome_fn in ("keys", "chaves", "listar", "list_secrets", "all_secrets"):
            fn = getattr(mod, nome_fn, None)
            if not callable(fn):
                continue
            try:
                lista = fn()
            except Exception:  # noqa: BLE001
                continue
            if lista:
                nomes.update(str(k) for k in lista)
                fontes.append(f"módulo, função {nome_fn}()")
                break

    # De onde o módulo tira os segredos: quando ele importa mas nada resolve,
    # o culpado costuma ser o arquivo que ELE lê — não o módulo. Aqui só se
    # olha caminho e permissão; conteúdo, nunca.
    arquivos: list[dict] = []
    if mod is not None:
        vistos: set[str] = set()
        for atributo in dir(mod):
            if atributo.startswith("__"):
                continue
            valor = getattr(mod, atributo, None)
            if not isinstance(valor, str) or not valor.startswith("/"):
                continue
            if valor in vistos or len(valor) > 300:
                continue
            vistos.add(valor)
            arquivos.append(dict(_arquivo(valor), atributo=atributo))

    # Os nomes que estão DENTRO dos arquivos do cofre. Vale mesmo quando o
    # módulo não sabe se listar: é a resposta direta para "ele enxerga?".
    conteudo = [_nomes_no_arquivo(cofre.CAMINHO_ARQUIVO)]
    for a in arquivos:
        if a.get("existe"):
            conteudo.append(_nomes_no_arquivo(a["caminho"]))
    for c in conteudo:
        if c["nomes"]:
            nomes.update(c["nomes"])
            if f"conteúdo de {c['caminho']}" not in fontes:
                fontes.append(f"conteúdo de {c['caminho']}")

    funcao = ""
    if mod is not None:
        fn = cofre._funcao_do_modulo(mod)
        funcao = getattr(fn, "__name__", "") if fn else ""
    return {
        "modulo_carregado": mod is not None,
        "modulo_via": getattr(cofre, "_modulo_via", ""),
        "funcao": funcao,
        "sabe_listar": bool(nomes),
        "fontes": fontes,
        "nomes": sorted(nomes),
        "arquivos_do_modulo": arquivos,
        "conteudo": conteudo,
        "pastas": [_pasta(d) for d in ("/usr/local/lib/vcreports", "/etc/vcreports")],
    }


def _pasta(caminho: str) -> dict:
    """O serviço enxerga a pasta do cofre, e o que há dentro é legível?"""
    import os
    from pathlib import Path as _P
    p = _P(caminho)
    info = {"caminho": caminho, "existe": p.exists(),
            "listavel": False, "itens": [], "erro": ""}
    try:
        info["itens"] = [
            {"nome": f.name, "legivel": os.access(f, os.R_OK)}
            for f in sorted(p.iterdir())[:40]
        ]
        info["listavel"] = True
    except PermissionError:
        info["erro"] = "sem permissão para listar"
    except OSError as exc:
        info["erro"] = str(exc)
    return info


def _remedio(arq: dict, usuario: str) -> dict:
    """O pedido pronto para quem administra o cofre.

    Quando o arquivo existe e não é legível, a conversa com a equipe do
    cofre é sempre a mesma. Deixar o comando escrito aqui evita o vaivém
    de "qual grupo?" — a tela já sabe, porque acabou de olhar.
    """
    if not arq.get("existe") and not arq.get("pasta_acessivel"):
        return {"necessario": True, "motivo":
                "O serviço não consegue nem entrar na pasta do cofre, então "
                "não dá para saber se o arquivo está lá.",
                "comandos": [f"setfacl -m u:{usuario}:x {arq.get('pasta', {}).get('caminho', '')}",
                             f"setfacl -m u:{usuario}:r {arq.get('caminho', '')}"]}
    if arq.get("existe") and not arq.get("legivel"):
        grupo = arq.get("grupo") or arq.get("pasta", {}).get("grupo") or "<grupo do cofre>"
        return {"necessario": True, "motivo":
                f"O arquivo existe, mas só o dono ({arq.get('dono') or '?'}) e o "
                f"grupo {grupo} leem — e o serviço roda como {usuario}.",
                "comandos": [f"usermod -aG {grupo} {usuario}",
                             f"chmod g+r {arq.get('caminho', '')}",
                             f"chmod g+x {arq.get('pasta', {}).get('caminho', '')}",
                             "systemctl restart portal-spare"]}
    return {"necessario": False, "motivo": "", "comandos": []}


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
        # Nomes que o cofre expõe e apelidos plausíveis da credencial do
        # EBS: responde "a chave tem outro nome?" sem chutar um por vez.
        "inventario": _inventario_corporativo(),
        "remedio": _remedio(_arquivo(cofre.CAMINHO_ARQUIVO), quem),
        "alternativas": [{"nome": rotulo, "chaves": [_sondar(k) for k in chaves]}
                         for rotulo, chaves in ALTERNATIVAS],
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
