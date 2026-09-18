#!/usr/bin/env python3
"""O que dá para fazer na API da Omnissa, provado contra o ambiente real.

    # a API da UEM, com a credencial de serviço que já está no cofre
    python3 scripts/testar_omnissa_mdm.py --uem as258.awmdm.com --basic

    # idem, procurando um coletor pela série (o caso de uso de vocês)
    python3 scripts/testar_omnissa_mdm.py --uem as258.awmdm.com --basic --serie ABC123

    # o Intelligence (relatórios), com o JSON de service account
    python3 scripts/testar_omnissa_mdm.py --credencial cred.json

Responde três perguntas com evidência em vez de opinião:

1.  A credencial autentica na API?
2.  Ela lê a base de coletores?
3.  Ela mexe nas TAGS do device?

DOIS PRODUTOS, DOIS HOSTS
-------------------------
*   **Omnissa Intelligence** — `api.<regiao>.data.workspaceone.com`. É
    análise: cria relatório, agenda, baixa CSV. Token OAuth
    (client_credentials). NÃO tem tag nem exclusão.
*   **Workspace ONE UEM** — `as258.awmdm.com/api/mdm`. É a gestão do
    device: busca, tag, exclusão. É esta que interessa, e é **Basic auth**.

Repare na letra: o console é `cn258.awmdm.com` (é o que está em
`MDM_BASE_URL`, de onde hoje se raspa a grade HTML); a API é
`as258.awmdm.com`. Mesmo tenant, hosts diferentes.

NADA AQUI ESCREVE NO MDM
------------------------
Todas as provas abaixo são GET. As escritas são apenas *descritas*, com o
caminho exato tirado da especificação — nunca disparadas. Exclusão de
device não é testada de propósito: é irreversível e não tem desfazer
(ver CAMINHOS_PROIBIDOS).
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TEMPO = 45

# Host da API deste tenant, conforme o campo `servers` das quatro
# especificações (mdmv1..mdmv4.json). As duas constantes existem separadas
# de propósito: `UEM_PADRAO` é o que se usa e pode ser trocado enquanto se
# investiga; `UEM_DA_ESPECIFICACAO` é o que o documento diz, e não muda. Se
# alguém editar a primeira, a diferença aparece na tela em vez de o script
# continuar afirmando que o host veio da especificação.
UEM_DA_ESPECIFICACAO = "as258.awmdm.com"
UEM_PADRAO = UEM_DA_ESPECIFICACAO

# Caminhos que APAGAM. Estão aqui pelo nome para que a lista exista em
# algum lugar do código e possa ser conferida — o script nunca os chama, e
# a verificação prova isso (scripts/verificar_omnissa_mdm.py).
#
# O primeiro é uma armadilha de leitura: `POST /devices/bulk` tem cara de
# criação, e a própria documentação diz "Deletes multiple devices".
CAMINHOS_PROIBIDOS = (
    "POST /api/mdm/devices/bulk",            # apaga vários (por série, UDID, MAC...)
    "DELETE /api/mdm/devices",               # apaga por id alternativo
    "DELETE /api/mdm/devices/{id}",          # apaga por id
    "DELETE_DEVICE", "DEVICE_WIPE", "DEVICE_ENTERPRISE_WIPE",
)


def _sessao():
    """Sessão HTTP com TLS verificado — a política do portal, não a do script."""
    try:
        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
        os.environ.setdefault("PORTAL_SESSION_SECRET", "sonda-sem-sessao")
        from integracoes import http as http_saida
        return http_saida.sessao("omnissa")
    except Exception:  # noqa: BLE001 — fora do servidor, requests puro
        import requests
        return requests.Session()


# ── Credencial ────────────────────────────────────────────────────────
def credencial_basica(usuario_arg: str) -> tuple[str, str]:
    """Usuário e senha para o Basic auth, nesta ordem de procura.

    A senha NUNCA vem da linha de comando: `ps` mostra o comando inteiro
    para qualquer um logado na máquina, e ela ficaria no histórico do
    shell. Vem do ambiente, do cofre, ou digitada na hora.

    1. `--usuario` / `OMNISSA_UEM_USUARIO` + `OMNISSA_UEM_SENHA`
    2. o cofre do portal (MDM_USUARIO / MDM_SENHA) — a MESMA credencial
       de serviço que já entra no console hoje
    3. o que for digitado aqui
    """
    usuario = usuario_arg or os.environ.get("OMNISSA_UEM_USUARIO", "")
    senha = os.environ.get("OMNISSA_UEM_SENHA", "")
    if usuario and senha:
        return usuario, senha

    try:
        from core import cofre
        do_cofre_u = cofre.obter("MDM_USUARIO") or ""
        do_cofre_s = cofre.obter("MDM_SENHA") or ""
        if do_cofre_u and do_cofre_s:
            print("  credencial: a de SERVIÇO do cofre (MDM_USUARIO / MDM_SENHA)")
            return usuario or do_cofre_u, senha or do_cofre_s
    except Exception as exc:  # noqa: BLE001 — fora do servidor não há cofre
        print(f"  (cofre indisponível aqui: {type(exc).__name__})")

    if not usuario:
        usuario = input("  usuário da UEM (formato renner\\login): ").strip()
    if not senha:
        senha = getpass.getpass("  senha (não é ecoada, não vai para o histórico): ")
    return usuario, senha


def avisar_tls() -> bool:
    """Grita se a verificação de certificado estiver desligada no ambiente.

    Aqui se manda senha e segredo de cliente pela rede. Com `VERIFY_SSL=false`
    qualquer coisa no caminho pode se passar pelo destino e ficar com as duas.
    O script não desliga nada — mas também não vai deixar isso passar calado,
    que é como esse tipo de ajuste temporário costuma virar permanente.
    """
    try:
        from integracoes import http as http_saida
        if http_saida.verificacao_tls("omnissa") is False:
            print("\n  ⚠⚠ TLS SEM VERIFICAÇÃO neste ambiente (VERIFY_SSL=false).")
            print("     Credencial mandada assim vai para quem estiver no caminho.")
            print("     Para um teste de leitura, tudo bem; antes de automatizar,")
            print("     ligue de volta — com proxy que intercepta o TLS, o certo é")
            print("     PORTAL_CA_BUNDLE apontando para a CA corporativa.")
            return False
    except Exception:  # noqa: BLE001 — fora do servidor não há config a consultar
        pass
    return True


def carregar_credencial(caminho: str) -> dict:
    """A service account do Intelligence: de um arquivo, ou do ambiente.

    `cred.json` não é um arquivo que exista em lugar nenhum por padrão — é o
    JSON que o console da Omnissa **baixa na hora** em que a service account
    é criada, e que só aparece naquele momento. Ele tem esta cara:

        {"clientId": "...", "clientSecret": "...",
         "tokenEndpoint": "https://<regiao>.uemauth...com/connect/token",
         "resourceIds": ["..."]}

    Quem recebeu a chave em pedaços soltos (num chamado, num chat) não
    precisa remontar arquivo nenhum: basta o ambiente.

        export OMNISSA_CLIENT_ID=...
        export OMNISSA_TOKEN_ENDPOINT=https://.../connect/token
        read -rs OMNISSA_CLIENT_SECRET && export OMNISSA_CLIENT_SECRET

    O segredo não entra por argumento pelo mesmo motivo da senha: `ps`
    mostra o comando inteiro, e ele ficaria no histórico do shell.
    """
    if caminho:
        arq = Path(caminho)
        if not arq.is_file():
            raise SystemExit(
                f"Não existe o arquivo {arq}.\n"
                "  Ele é o JSON que o console da Omnissa baixa quando a service\n"
                "  account é criada (clientId, clientSecret, tokenEndpoint) — e\n"
                "  só é oferecido naquele momento.\n"
                "  Se você tem os valores soltos, não precisa de arquivo:\n"
                "    export OMNISSA_CLIENT_ID=...\n"
                "    export OMNISSA_TOKEN_ENDPOINT=https://.../connect/token\n"
                "    read -rs OMNISSA_CLIENT_SECRET && export OMNISSA_CLIENT_SECRET\n"
                "  e rode de novo sem o --credencial.")
        return json.loads(arq.read_text(encoding="utf-8"))

    cred = {
        "name": "(do ambiente)",
        "clientId": os.environ.get("OMNISSA_CLIENT_ID", ""),
        "clientSecret": os.environ.get("OMNISSA_CLIENT_SECRET", ""),
        "tokenEndpoint": os.environ.get("OMNISSA_TOKEN_ENDPOINT", ""),
    }
    faltando = [nome for nome, chave in
                (("OMNISSA_CLIENT_ID", "clientId"),
                 ("OMNISSA_CLIENT_SECRET", "clientSecret"),
                 ("OMNISSA_TOKEN_ENDPOINT", "tokenEndpoint"))
                if not cred[chave]]
    if faltando:
        raise SystemExit(
            "Faltou a credencial do Intelligence: " + ", ".join(faltando) + ".\n"
            "  Ou aponte o arquivo com --credencial <arquivo>, ou defina as três\n"
            "  variáveis. O tokenEndpoint acompanha a chave quando ela é emitida;\n"
            "  sem ele não há para onde pedir o token.")
    return cred


def cabecalhos_basicos(usuario: str, senha: str, tenant: str, versao: int) -> dict:
    """Basic + aw-tenant-code. A UEM costuma exigir os dois juntos.

    `BasicAuth` e `ApiKeyAuth` aparecem como alternativas na especificação
    (`security: [BasicAuth] OU [CmsAuth] OU [ApiKeyAuth]`), mas na prática a
    instalação quase sempre quer a chave do tenant junto do usuário. Sem
    ela, o 401 não distingue "senha errada" de "faltou a chave".
    """
    basico = base64.b64encode(f"{usuario}:{senha}".encode()).decode()
    cab = {"Authorization": f"Basic {basico}",
           "Accept": f"application/json;version={versao}"}
    if tenant:
        cab["aw-tenant-code"] = tenant
    return cab


def cabecalhos_bearer(jwt: str, versao: int) -> dict:
    """O token do Intelligence apontado para a UEM.

    ATENÇÃO ao que isto é: um teste, não um caminho documentado. As quatro
    especificações declaram `BasicAuth`, `ApiKeyAuth`, `GroupIdAuth` e
    `CmsAuth` — **não** há esquema Bearer entre elas. Então, se funcionar,
    funciona por a instalação aceitar mais do que documenta; se voltar 401,
    não prova defeito na chave, só que este caminho não existe aqui.

    Vale a tentativa porque é uma requisição só e responde uma pergunta
    cara: se a UEM aceitar o token, o `aw-tenant-code` deixa de fazer falta.
    """
    return {"Authorization": f"Bearer {jwt}",
            "Accept": f"application/json;version={versao}"}


# ── Provas ────────────────────────────────────────────────────────────
def _tentar(s, metodo: str, url: str, cabecalhos: dict, **kw) -> tuple[int, str]:
    # `raise`, não `assert`: com `python -O` o assert some do bytecode, e a
    # única trava contra uma escrita acidental sumiria junto.
    if metodo.upper() not in ("GET", "HEAD"):
        raise ValueError(f"sonda só de leitura: {metodo} não passa por aqui")
    try:
        r = s.request(metodo, url, headers=cabecalhos, timeout=TEMPO, **kw)
        return r.status_code, (r.text or "")[:400]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _diz(rotulo: str, codigo: int, corpo: str) -> None:
    leitura = {
        200: "FUNCIONA", 201: "FUNCIONA", 204: "FUNCIONA",
        400: "chegou lá (pedido malformado — o caminho existe)",
        401: "NÃO autorizado (credencial recusada, ou falta aw-tenant-code)",
        403: "autenticou, mas SEM PERMISSÃO para isto",
        404: "caminho não existe neste host",
        405: "caminho existe, método errado",
        406: "versão do Accept não aceita neste caminho",
        429: "limite de chamadas",
        0: "nem conectou",
    }.get(codigo, "resposta inesperada")
    print(f"  [{codigo or '---'}] {rotulo}: {leitura}")
    if codigo not in (200, 201, 204) and corpo:
        print(f"        {corpo[:200].strip()}")
    for marca, explicacao in ERROS_DA_UEM.items():
        if marca in corpo:
            for linha in explicacao:
                print(f"        {linha}")


# O que os códigos de erro da própria UEM querem dizer. Sem isto, um 1005
# parece "senha errada" e o tempo vai embora trocando senha.
ERROS_DA_UEM = {
    '"errorCode":1005': (
        "→ 1005 é a recusa do serviço REST, não do console. Três causas, e a",
        "  ordem importa porque a segunda é a que mais engana:",
        "  1. falta o aw-tenant-code;",
        "  2. a conta é de DIRETÓRIO (AD). O Basic da API da UEM quer conta de",
        "     admin do tipo Basic, criada dentro da UEM. Entrar no console com",
        "     a conta do AD não implica que ela autentique na API;",
        "  3. a conta existe, mas não tem papel com acesso de API.",
        "  Nenhuma delas se resolve trocando a senha.",
    ),
    '"errorCode":1001': (
        "→ 1001 costuma ser o aw-tenant-code ausente ou inválido.",
    ),
}


def provas_uem(s, base: str, cabecalho, serie: str = "") -> int:
    """As leituras que provam cada capacidade. Só GET.

    `cabecalho` é uma função `(versao) -> dict`: a mesma bateria de provas
    serve para Basic e para Bearer, e é justamente comparar as duas que
    responde qual credencial a instalação aceita.

    Os caminhos NÃO são chute: saem de mdmv1..mdmv4.json, que declaram
    `servers: https://as258.awmdm.com/api/mdm`.

    **Para no primeiro 401.** A primeira prova já respondeu se a credencial
    autentica; insistir nas outras quatro só soma tentativas de login
    falhas, e com conta de diretório é assim que se bloqueia a conta de
    alguém. Repetir o mesmo erro quatro vezes não informa mais do que
    uma — informa quatro vezes menos, porque gasta o orçamento de
    tentativas que a pessoa tem até o AD travar.
    """
    autenticou = 0
    provas = [
        (1, "/api/system/info", "info do sistema (a credencial autentica?)"),
        (1, "/api/mdm/devices/search?pagesize=1", "buscar devices (base de coletores) — v1"),
        (2, "/api/mdm/devices/search?pagesize=1", "buscar devices — v2"),
        (1, "/api/mdm/tags/search?pagesize=1", "listar tags — v1"),
    ]
    if serie:
        # O caso de uso de vocês: achar o coletor pela série.
        provas.append((1, f"/api/mdm/devices?searchby=Serialnumber&id={serie}",
                       f"device pela série {serie}"))
    for versao, caminho, rotulo in provas:
        cod, corpo = _tentar(s, "GET", base + caminho, cabecalho(versao))
        _diz(rotulo, cod, corpo)
        if cod in (200, 201, 204, 400, 403):
            autenticou += 1
        if cod == 401:
            print(f"\n  Parei aqui: {len(provas) - 1} prova(s) não foram feitas.")
            print("  A credencial foi recusada, e as outras seriam recusadas igual —")
            print("  cada uma somando mais uma tentativa de login falha. Resolva a")
            print("  credencial primeiro; depois rode de novo e a bateria vai até o fim.")
            break
    return autenticou


def descobrir(s, base_uem: str, cab: dict, saida: Path) -> None:
    """Puxa a documentação viva da UEM e lista o que ela expõe.

    Serve para conferir a superfície desta instalação contra as
    especificações em mãos — versão a versão, que é onde moram as
    diferenças (ver o resumo no fim: o mesmo assunto muda de caminho
    entre v1 e v2).
    """
    import re
    caminhos = [
        "/api/help/", "/api/help/resources",
        "/api/mdm/swagger.json", "/api/swagger.json",
        "/api/v1/swagger.json", "/api/help/swagger/docs/v1",
    ]
    achados = []
    for caminho in caminhos:
        cod, corpo = _tentar(s, "GET", base_uem + caminho, cab)
        marca = "ok " if cod == 200 else "-- "
        print(f"  {marca}[{cod or '---'}] {caminho}"
              + ("" if cod == 200 else f"  {corpo[:90].strip()}"))
        if cod == 200 and corpo:
            achados.append(caminho)

    if not achados:
        print("\n  Nada respondeu. Confira o host e se a credencial tem acesso —")
        print("  a página de ajuda costuma exigir sessão de console.")
        return

    with saida.open("w", encoding="utf-8") as f:
        for caminho in achados:
            r = s.get(base_uem + caminho, headers=cab, timeout=TEMPO)
            f.write(f"\n===== {caminho} ({r.status_code}) =====\n")
            f.write(r.text)
    print(f"\n  gravado: {saida}")
    texto = saida.read_text(encoding="utf-8", errors="ignore")
    rotas = sorted(set(re.findall(r"/(?:API|api)/[A-Za-z0-9/_{}.-]{3,80}", texto)))
    print(f"  {len(rotas)} caminho(s) citados. Os de device e tag:")
    for r_ in rotas:
        if any(x in r_.lower() for x in ("device", "tag")):
            print("   ", r_)


def token_intelligence(s, cred: dict) -> str:
    """O JWT, pelo client_credentials do arquivo de service account.

    Imprime `resourceIds` porque é o campo que decide o alcance: um token de
    client_credentials é emitido PARA um recurso. Se ali só houver o do
    Intelligence, a UEM vai recusar por mais correta que a chave esteja — e
    aí o 401 tem explicação, em vez de virar mistério.
    """
    print(f"  conta: {cred.get('name', '?')}")
    print(f"  clientId: {cred.get('clientId', '?')}")
    recursos = cred.get("resourceIds") or []
    print(f"  resourceIds: {recursos or '(não informado)'}")
    if recursos and not any("uem" in str(x).lower() or "awmdm" in str(x).lower()
                            for x in recursos):
        print("  ⚠ Nenhum recurso de UEM listado. Se a UEM recusar, é por isto:")
        print("    a chave foi emitida para o Intelligence, que é análise, e não")
        print("    para a gestão de dispositivos.")
    endpoint = cred.get("tokenEndpoint") or ""
    if not endpoint:
        raise SystemExit("O arquivo de credencial não tem 'tokenEndpoint'.")
    usuario, senha = cred.get("clientId", ""), cred.get("clientSecret", "")
    if not usuario or not senha:
        raise SystemExit("O arquivo precisa de 'clientId' e 'clientSecret'.")
    basico = base64.b64encode(f"{usuario}:{senha}".encode()).decode()
    r = s.post(endpoint, params={"grant_type": "client_credentials"},
               headers={"Authorization": f"Basic {basico}",
                        "Content-Type": "application/x-www-form-urlencoded"},
               timeout=TEMPO)
    if r.status_code != 200:
        raise SystemExit(f"O token foi recusado ({r.status_code}): {r.text[:300]}")
    dados = r.json()
    jwt = dados.get("access_token") or ""
    if not jwt:
        raise SystemExit(f"Resposta sem access_token: {str(dados)[:300]}")
    print(f"  token obtido · expira em {dados.get('expires_in', '?')}s")
    return jwt


def intelligence(s, cred: dict, jwt: str) -> None:
    """O outro produto: relatórios. Token OAuth, host próprio."""
    endpoint = cred.get("tokenEndpoint") or ""
    cab = {"Authorization": f"Bearer {jwt}", "Accept": "application/json"}
    u = urlparse(endpoint)
    base = f"{u.scheme}://{u.netloc}" if u.netloc else ""
    if not base:
        print("  (não deu para deduzir o host do Intelligence)")
        return
    cod, corpo = _tentar(s, "GET", f"{base}/v2/meta/integration/airwatch"
                                   "/entity/device/attributes", cab)
    _diz("atributos da entidade device", cod, corpo)
    print("  → Aqui a leitura sai por RELATÓRIO: cria, roda, baixa o CSV.")
    print("    Não é consulta ao vivo por device; é extração assíncrona.")


def resumo() -> None:
    print("""
O QUE A ESPECIFICAÇÃO GARANTE (mdmv1..mdmv4.json, servers = as258.awmdm.com)

  Autenticação ......... BasicAuth está declarado em todos os caminhos,
                         como alternativa a CmsAuth e ApiKeyAuth.
                         Na prática: Basic + aw-tenant-code.

  Ler a base ........... GET /api/mdm/devices/search   (v1 e v2)
                         GET /api/mdm/devices/extensivesearch  (v1, + atributos
                            personalizados, enrollment, janela de data)
                         GET /api/mdm/devices?searchby=Serialnumber&id=<serie>
                         Campos do device incluem SerialNumber, AssetNumber,
                         UserName, Model, LastSeen, EnrollmentStatus.

  Tags ................. GET    /api/mdm/tags/search            (achar a tag)
                         GET    /api/mdm/devices/{uuid}/tags    (tags do device)
                         POST   /api/mdm/devices/{device_uuid}/tags/{tag_uuid}
                         DELETE /api/mdm/devices/{device_uuid}/tags/{tag_uuid}
                         POST   /api/mdm/tags/{tagid}/adddevices     (lote, v1)
                         POST   /api/mdm/tags/{tagid}/removedevices  (lote, v1)
                         POST   /api/mdm/tags/{tagUuid}/devices      (lote, v2)
                         DELETE /api/mdm/tags/{tagUuid}/devices      (lote, v2)
                         O corpo do lote é BulkInput: {"BulkValues":{"Value":[...]}}

  Excluir device ....... existe, e NÃO é exercitado aqui (ver CAMINHOS_PROIBIDOS).

DUAS ARMADILHAS, para quem for automatizar

  1. `POST /api/mdm/devices/bulk` tem cara de cadastro e APAGA. A defesa é
     lista de permitidos: o portal chama caminho que ele conhece, nunca um
     caminho montado com o que veio da tela.
  2. Na V4 o campo `action_name` de UMA chamada aceita MANAGE_TAGS e também
     DEVICE_DELETE, DEVICE_WIPE, DEVICE_ENTERPRISE_WIPE. Uma string errada
     é a diferença entre marcar uma tag e apagar o aparelho. Por isso as
     rotas v1/v2 acima, específicas por assunto, são a escolha melhor: o
     caminho não tem como virar outra coisa.
""")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--uem", default="", help=f"host da API da UEM (ex.: {UEM_PADRAO})")
    p.add_argument("--basic", action="store_true",
                   help="autenticar por Basic auth (usuário/senha da UEM)")
    p.add_argument("--usuario", default="",
                   help="usuário da UEM. A SENHA não é aceita aqui: use "
                        "OMNISSA_UEM_SENHA, o cofre, ou digite quando for pedida.")
    p.add_argument("--tenant-code", default=os.environ.get("OMNISSA_UEM_TENANT", ""),
                   help="aw-tenant-code (ou a variável OMNISSA_UEM_TENANT)")
    p.add_argument("--serie", default="", help="série de um coletor para procurar")
    p.add_argument("--credencial", default="",
                   help="JSON de service account do Intelligence, se você tiver o "
                        "arquivo. Sem ele, valem OMNISSA_CLIENT_ID, "
                        "OMNISSA_CLIENT_SECRET e OMNISSA_TOKEN_ENDPOINT.")
    p.add_argument("--intelligence", action="store_true",
                   help="testar o Intelligence (relatórios) com a credencial acima")
    p.add_argument("--bearer", action="store_true",
                   help="tentar o token do --credencial na API da UEM. Não é "
                        "caminho documentado (não há esquema Bearer na "
                        "especificação); é uma requisição para descobrir se a "
                        "instalação aceita, e assim dispensar o aw-tenant-code.")
    p.add_argument("--descobrir", action="store_true",
                   help="baixa a referência viva da UEM (/api/help/)")
    p.add_argument("--saida", default="uem_api_help.txt")
    a = p.parse_args()

    if not (a.basic or a.bearer or a.intelligence or a.credencial):
        p.error("escolha o que testar: --basic (usuário/senha na UEM), "
                "--bearer (o token do Intelligence na UEM) ou --intelligence")

    s = _sessao()
    avisar_tls()
    host = a.uem or UEM_PADRAO
    base = host if host.startswith("http") else f"https://{host}"
    jwt = ""

    if a.bearer or a.intelligence or a.credencial:
        print("\n[credencial do Intelligence]")
        cred = carregar_credencial(a.credencial)
        jwt = token_intelligence(s, cred)
        if a.intelligence or not a.bearer:
            print("\n[Intelligence] relatórios")
            intelligence(s, cred, jwt)

    if a.bearer:
        print(f"\n[UEM com o token do Intelligence] {base}")
        print("  Isto NÃO está na especificação: ela declara Basic, ApiKey,")
        print("  GroupId e Cms, e nenhum Bearer. 401 aqui não condena a chave —")
        print("  só diz que este caminho não existe nesta instalação.")
        ok = provas_uem(s, base, lambda v: cabecalhos_bearer(jwt, v), a.serie)
        print("  → Se alguma prova passou, o aw-tenant-code deixou de fazer falta."
              if ok else
              "  → A UEM não aceitou o token. O aw-tenant-code continua necessário.")

    if a.basic:
        print(f"\n[Workspace ONE UEM] {base}")
        if not a.uem:
            print(f"  (host não informado; usando {UEM_PADRAO})")
        if UEM_DA_ESPECIFICACAO not in base:
            print(f"  ⚠ A especificação diz {UEM_DA_ESPECIFICACAO}; este host é"
                  " outro. Se ambos responderem igual, o host não é o problema.")
        print("\n  ⚠ Se a senha estiver errada, cada tentativa conta como falha de")
        print("    login no AD. Confira a credencial ANTES de repetir, para não")
        print("    bloquear a conta.")
        if not a.tenant_code:
            print("  ⚠ Sem aw-tenant-code. Se vier 401 em tudo, é o primeiro suspeito:")
            print("    a chave sai no console em Groups & Settings > All Settings >")
            print("    System > Advanced > API > REST API. Ela é do organization")
            print("    group, não de uma pessoa: quem tem o papel de admin lê ali.")
        usuario, senha = credencial_basica(a.usuario)
        if not usuario or not senha:
            raise SystemExit("Sem usuário e senha não dá para testar.")
        print(f"  usuário: {usuario}")

        def cabecalho(versao):
            return cabecalhos_basicos(usuario, senha, a.tenant_code, versao)

        ok = provas_uem(s, base, cabecalho, a.serie)
        if not ok:
            print("\n  Nenhuma prova passou da autenticação. Ou a credencial não tem")
            print("  acesso de API (é um perfil à parte do acesso ao console), ou")
            print("  falta o aw-tenant-code, ou o host está errado.")
        if a.descobrir:
            print("\n[referência viva da UEM]")
            descobrir(s, base, cabecalho(1), Path(a.saida))

    resumo()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
