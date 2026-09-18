#!/usr/bin/env python3
"""O que a chave da Omnissa alcança: Intelligence, UEM, ou os dois.

    python3 scripts/testar_omnissa_mdm.py --credencial /caminho/credenciais.json
    python3 scripts/testar_omnissa_mdm.py --credencial cred.json --uem cn123.awmdm.com

Responde, com evidência em vez de opinião, três perguntas:

1.  A chave autentica?
2.  Ela lê a base de coletores? (Intelligence → relatórios)
3.  Ela FAZ coisas no device — as TAGS? (isso é a API do Workspace ONE UEM,
    que é outra API, outro host e outro escopo)

Exclusão de device NÃO é testada aqui, de propósito: é irreversível e não
tem desfazer. Uma sonda de diagnóstico não é lugar para chegar perto disso,
nem para deixar o caminho escrito à mão de quem estiver conferindo.

POR QUE ESTE SCRIPT EXISTE
--------------------------
A documentação entregue é a do **Omnissa Intelligence V2**, que é o produto
de análise. Ela descreve relatórios: criar, agendar, baixar. Três fatos do
próprio documento, e os três apontam para o mesmo lugar:

*   a seção "HTTP Methods" lista **apenas GET e POST** — não há PUT nem
    DELETE em API nenhuma dali;
*   a palavra `delete` não aparece uma vez em 52 páginas;
*   a entidade `airwatch.device` expõe 15 atributos (nome, modelo, SO,
    último contato, usuário...) e **nenhum deles é tag**.

Ou seja: *aquele documento* não descreve tag nem exclusão. Isso NÃO quer
dizer que a chave não sirva — quem disse que dá pode estar certo, se a mesma
credencial também autorizar a **API do Workspace ONE UEM**, que é onde essas
operações moram. O campo `resourceIds` do arquivo de credencial é o que
decide, e este script confirma na prática.

NADA AQUI ESCREVE NO MDM. Só leitura, e as chamadas de escrita são apenas
descritas, nunca disparadas.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

TEMPO = 45


def _sessao():
    """Sessão HTTP com TLS verificado — a política do portal, não a do script."""
    try:
        import os
        os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
        os.environ.setdefault("PORTAL_SESSION_SECRET", "sonda-sem-sessao")
        from integracoes import http as http_saida
        return http_saida.sessao("omnissa")
    except Exception:  # noqa: BLE001 — fora do servidor, requests puro
        import requests
        return requests.Session()


def token(cred: dict) -> str:
    """O JWT, pelo client_credentials do arquivo de credencial."""
    endpoint = cred.get("tokenEndpoint") or ""
    if not endpoint:
        raise SystemExit("O arquivo de credencial não tem 'tokenEndpoint'.")
    usuario, senha = cred.get("clientId", ""), cred.get("clientSecret", "")
    if not usuario or not senha:
        raise SystemExit("O arquivo precisa de 'clientId' e 'clientSecret'.")
    basico = base64.b64encode(f"{usuario}:{senha}".encode()).decode()
    s = _sessao()
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


def _tentar(s, metodo: str, url: str, cabecalhos: dict, **kw) -> tuple[int, str]:
    try:
        r = s.request(metodo, url, headers=cabecalhos, timeout=TEMPO, **kw)
        return r.status_code, (r.text or "")[:400]
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"


def _diz(rotulo: str, codigo: int, corpo: str) -> None:
    leitura = {
        200: "FUNCIONA", 201: "FUNCIONA", 204: "FUNCIONA",
        400: "chegou lá (pedido malformado — o caminho existe)",
        401: "NÃO autorizado (o token não vale para esta API)",
        403: "autenticou, mas SEM PERMISSÃO para isto",
        404: "caminho não existe neste host",
        405: "caminho existe, método errado",
        429: "limite de chamadas",
        0: "nem conectou",
    }.get(codigo, "resposta inesperada")
    print(f"  [{codigo or '---'}] {rotulo}: {leitura}")
    if codigo not in (200, 201, 204) and corpo:
        print(f"        {corpo[:200].strip()}")


def descobrir(s, base_uem: str, cab: dict, saida: Path) -> None:
    """Puxa a documentação viva da UEM e lista o que ela expõe.

    O console da UEM publica a própria referência em `/api/help/`. De fora da
    rede de vocês esse host não é alcançável, então quem tem de buscá-la é
    esta máquina — e o arquivo gravado é o que permite conferir os caminhos
    reais em vez de trabalhar com o que "costuma ser".

    Tenta a página e os lugares usuais do descritor legível por máquina. O
    que responder é gravado; o que não, é dito.
    """
    import re
    caminhos = [
        "/api/help/",
        "/api/help/resources",
        "/api/mdm/swagger.json",
        "/api/swagger.json",
        "/api/v1/swagger.json",
        "/api/help/swagger/docs/v1",
    ]
    achados = []
    for caminho in caminhos:
        cod, corpo = _tentar(s, "GET", base_uem + caminho, cab)
        marca = "ok " if cod == 200 else "-- "
        print(f"  {marca}[{cod or '---'}] {caminho}"
              + ("" if cod == 200 else f"  {corpo[:90].strip()}"))
        if cod == 200 and corpo:
            achados.append((caminho, corpo))

    if not achados:
        print("\n  Nada respondeu. Confira se o host está certo e se a credencial")
        print("  tem acesso — a página de ajuda costuma exigir sessão.")
        return

    # A página inteira, não só o pedaço que coube no _tentar.
    with saida.open("w", encoding="utf-8") as f:
        for caminho, _ in achados:
            r = s.get(base_uem + caminho, headers=cab, timeout=TEMPO)
            f.write(f"\n===== {caminho} ({r.status_code}) =====\n")
            f.write(r.text)
    print(f"\n  gravado: {saida}")

    # Um resumo aqui mesmo: os caminhos citados na documentação.
    texto = saida.read_text(encoding="utf-8", errors="ignore")
    rotas = sorted(set(re.findall(r"/(?:API|api)/[A-Za-z0-9/_{}.-]{3,80}", texto)))
    print(f"  {len(rotas)} caminho(s) citados. Os de device e tag:")
    for r_ in rotas:
        if any(x in r_.lower() for x in ("device", "tag")):
            print("   ", r_)
    print("\n  Mande este arquivo que eu leio a superfície inteira.")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--credencial", required=True,
                   help="o JSON que o console baixou ao criar a service account")
    p.add_argument("--uem", default="",
                   help="host da API do Workspace ONE UEM (ex.: cnXXX.awmdm.com). "
                        "Sem ele, só o Intelligence é testado.")
    p.add_argument("--tenant-code", default="",
                   help="aw-tenant-code da UEM, se você tiver")
    p.add_argument("--descobrir", action="store_true",
                   help="baixa a referência da API da UEM (/api/help/) e lista "
                        "os caminhos que ela expõe, num arquivo para análise")
    p.add_argument("--saida", default="uem_api_help.txt",
                   help="arquivo onde gravar a referência baixada")
    a = p.parse_args()

    cred = json.loads(Path(a.credencial).read_text(encoding="utf-8"))
    print("Credencial")
    print(f"  conta: {cred.get('name', '?')}")
    print(f"  clientId: {cred.get('clientId', '?')}")
    # O campo que decide o alcance: um token de client_credentials é emitido
    # PARA um recurso. Se aqui só houver o da Intelligence, a chave não vai
    # ser aceita pela API da UEM por mais correta que seja.
    recursos = cred.get("resourceIds") or []
    print(f"  resourceIds: {recursos or '(não informado)'}")
    if recursos and not any("uem" in str(x).lower() or "awmdm" in str(x).lower()
                            for x in recursos):
        print("  ⚠ Nenhum recurso de UEM listado. Se a UEM recusar abaixo, é por isto:"
              "\n    a chave foi emitida para o Intelligence, não para a gestão de"
              "\n    dispositivos. Peça uma credencial de API da UEM.")
    print()

    jwt = token(cred)
    cab = {"Authorization": f"Bearer {jwt}", "Accept": "application/json"}
    s = _sessao()

    # ── 1. Intelligence: ler a base ────────────────────────────────────
    base_intel = ""
    for chave in ("tokenEndpoint",):
        u = urlparse(cred.get(chave, ""))
        if u.netloc:
            base_intel = f"{u.scheme}://{u.netloc}"
    print("\n[1] Intelligence — LER a base de coletores")
    if base_intel:
        cod, corpo = _tentar(s, "GET", f"{base_intel}/v2/meta/integration/airwatch"
                                       "/entity/device/attributes", cab)
        _diz("atributos da entidade device", cod, corpo)
        cod, corpo = _tentar(s, "POST", f"{base_intel}/v2/reports/search", cab,
                             json={"offset": 0, "page_size": 1})
        _diz("busca de relatórios", cod, corpo)
    else:
        print("  (não deu para deduzir o host do Intelligence)")
    print("  → Leitura da base sai daqui, por RELATÓRIO: cria, roda, baixa o CSV.")
    print("    Não é consulta ao vivo por device; é extração assíncrona.")

    # ── 2. UEM: agir no device ─────────────────────────────────────────
    print("\n[2] Workspace ONE UEM — TAGS")
    if not a.uem:
        print("  (pulado: informe --uem <host> para testar)")
        print("  O host é o do console da UEM, no formato cnXXX.awmdm.com — a mesma")
        print("  máquina onde hoje o portal raspa a grade HTML dos coletores.")
    else:
        base_uem = a.uem if a.uem.startswith("http") else f"https://{a.uem}"
        cab_uem = dict(cab)
        cab_uem["Accept"] = "application/json;version=1"
        if a.tenant_code:
            cab_uem["aw-tenant-code"] = a.tenant_code
        # Caminhos da API da UEM. NÃO saem do PDF entregue — ele é do
        # Intelligence. São os caminhos usuais dessa API, e é justamente a
        # resposta abaixo que confirma se existem nesta instalação.
        provas = [
            ("GET", "/API/mdm/devices/search?pagesize=1", None,
             "buscar dispositivos"),
            ("GET", "/API/mdm/tags/search?pagesize=1", None,
             "listar tags"),
            ("GET", "/API/system/info", None, "info do sistema"),
        ]
        for metodo, caminho, corpo_req, rotulo in provas:
            cod, corpo = _tentar(s, metodo, base_uem + caminho, cab_uem,
                                 **({"json": corpo_req} if corpo_req else {}))
            _diz(rotulo, cod, corpo)

        if a.descobrir:
            print("\n[3] A referência da própria UEM")
            descobrir(s, base_uem, cab_uem, Path(a.saida))
        print("\n  Se as leituras acima responderem 200, as escritas de TAG existem no")
        print("  mesmo host (NÃO são disparadas por este script):")
        print("    incluir tag : POST /API/mdm/tags/{tagId}/adddevices")
        print("    remover tag : POST /API/mdm/tags/{tagId}/removedevices")
        print("  Confirme as duas na documentação da API da UEM antes de automatizar.")
        print("\n  EXCLUSÃO DE DEVICE ficou de fora deste script, de propósito, a")
        print("  pedido: é irreversível e não tem desfazer. Quando for a hora, ela")
        print("  entra com confirmação explícita e em tela separada — não junto do")
        print("  que se usa todo dia.")

    print("\nResumo")
    print("  Consultar a base .......... o Intelligence faz, por relatório (assíncrono).")
    print("  Incluir/remover tag ....... NÃO está na documentação entregue.")
    print("  Excluir device ............ NÃO está na documentação entregue")
    print("                              (e não é testado aqui: irreversível).")
    print("  As duas últimas são da API do Workspace ONE UEM. Se as provas de [2]")
    print("  voltarem 200, a mesma chave serve. Se voltarem 401/403, peça ao time")
    print("  da Omnissa uma credencial de API da UEM — é outro escopo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
