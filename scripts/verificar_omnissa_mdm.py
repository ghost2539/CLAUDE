#!/usr/bin/env python3
"""Verificação da sonda da Omnissa — sem tocar no MDM de ninguém.

    python3 scripts/verificar_omnissa_mdm.py

A sonda existe para descobrir o que a credencial alcança na API da UEM.
Como ela roda contra o ambiente de produção do MDM, o que precisa ser
provado aqui não é que ela "funciona", e sim que ela **não faz estrago**:

*   nenhuma chamada de escrita, em nenhum caminho;
*   nenhum caminho de exclusão, nem por engano de digitação;
*   a senha nunca passa pela linha de comando;
*   a verificação de TLS não é desligada em lugar nenhum.

Três provas são contraprovas: refazem o erro que se quer impedir e exigem
que ele seja recusado. Sem isso, um "ok" só diria que hoje ninguém tentou.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


ARQ = RAIZ / "scripts" / "testar_omnissa_mdm.py"
FONTE = ARQ.read_text(encoding="utf-8")
ARVORE = ast.parse(FONTE)

import scripts.testar_omnissa_mdm as sonda  # noqa: E402


# ── 1. A sonda é só de leitura ────────────────────────────────────────
print("\n[1] Só leitura")


class SessaoFalsa:
    """Anota o que foi chamado e devolve algo com cara de resposta."""

    def __init__(self):
        self.chamadas: list[tuple[str, str]] = []

    def request(self, metodo, url, **kw):
        self.chamadas.append((metodo.upper(), url))

        class R:
            status_code = 200
            text = "{}"
        return R()


s = SessaoFalsa()
sonda.provas_uem(s, "https://as258.awmdm.com", "renner\\fulano", "x", "TENANT",
                 serie="ABC123")
checar(len(s.chamadas) >= 5, f"as provas chamaram {len(s.chamadas)} caminhos")
checar(all(m == "GET" for m, _ in s.chamadas),
       "todas as chamadas são GET: " + ", ".join(sorted({m for m, _ in s.chamadas})))

urls = " ".join(u for _, u in s.chamadas)
checar("/devices/bulk" not in urls, "nenhuma chamada em /devices/bulk (que APAGA)")
checar("/api/mdm/devices/search" in urls, "a base de coletores é lida por devices/search")
checar("/api/mdm/tags/search" in urls, "as tags são lidas por tags/search")
checar("searchby=Serialnumber" in urls, "a busca por série usa searchby=Serialnumber")

# Contraprova: um POST tem de ser recusado pela própria sonda.
recusou = False
try:
    sonda._tentar(SessaoFalsa(), "POST", "https://as258.awmdm.com/api/mdm/devices/bulk", {})
except ValueError:
    recusou = True
checar(recusou, "contraprova: _tentar recusa POST (escrita não passa por ela)")

# E a recusa não pode depender de `assert`, que `python -O` remove.
checar("raise ValueError" in FONTE and "assert metodo" not in FONTE,
       "a recusa é `raise`, não `assert` (sobrevive a python -O)")


# ── 2. Nenhum caminho de exclusão no código de chamada ────────────────
print("\n[2] Exclusão de device fica de fora")

chamadas_ast = [n for n in ast.walk(ARVORE) if isinstance(n, ast.Call)]


def _nome_do_metodo(n: ast.Call) -> str:
    return n.func.attr if isinstance(n.func, ast.Attribute) else ""


metodos_http = {_nome_do_metodo(n) for n in chamadas_ast} & {
    "post", "put", "delete", "patch"}
# `post` sobra por causa do token OAuth do Intelligence — que é outro host,
# outro produto, e não é uma escrita no device.
checar(metodos_http <= {"post"},
       f"nenhum put/delete/patch no arquivo (sobrou: {sorted(metodos_http) or 'nada'})")

fora_do_intelligence = []
for n in ast.walk(ARVORE):
    if isinstance(n, ast.FunctionDef) and n.name != "intelligence":
        for c in ast.walk(n):
            if isinstance(c, ast.Call) and _nome_do_metodo(c) in (
                    "post", "put", "delete", "patch"):
                fora_do_intelligence.append(n.name)
checar(not fora_do_intelligence,
       "só a função do Intelligence faz POST (o token OAuth); "
       f"as outras, não {sorted(set(fora_do_intelligence)) or ''}")

checar(len(sonda.CAMINHOS_PROIBIDOS) >= 4,
       f"a lista do que não se chama está escrita ({len(sonda.CAMINHOS_PROIBIDOS)} itens)")
for proibido in ("POST /api/mdm/devices/bulk", "DELETE /api/mdm/devices/{id}"):
    checar(proibido in sonda.CAMINHOS_PROIBIDOS, f"listado como proibido: {proibido}")


# ── 3. A senha não passa pela linha de comando ────────────────────────
print("\n[3] A senha não vai para o `ps` nem para o histórico")

opcoes = [c.value for n in ast.walk(ARVORE) if isinstance(n, ast.Call)
          and _nome_do_metodo(n) == "add_argument"
          for c in n.args if isinstance(c, ast.Constant) and isinstance(c.value, str)]
checar(opcoes, f"{len(opcoes)} opções de linha de comando encontradas")
ruins = [o for o in opcoes if any(x in o.lower() for x in ("senha", "password", "--pass"))]
checar(not ruins, f"nenhuma opção aceita senha {ruins or ''}")

os.environ["OMNISSA_UEM_USUARIO"] = "renner\\teste"
os.environ["OMNISSA_UEM_SENHA"] = "senha-de-mentira"
u, sn = sonda.credencial_basica("")
checar((u, sn) == ("renner\\teste", "senha-de-mentira"),
       "o ambiente é lido quando está definido (sem perguntar nada)")

# Contraprova: sem o ambiente, a sonda NÃO pode inventar uma senha vazia e
# seguir em frente — tem de procurar no cofre ou perguntar.
del os.environ["OMNISSA_UEM_SENHA"]
perguntou = {"sim": False}
_getpass = sonda.getpass.getpass
_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input


def _falso_getpass(_p=""):
    perguntou["sim"] = True
    return "digitada"


sonda.getpass.getpass = _falso_getpass
try:
    u2, s2 = sonda.credencial_basica("")
finally:
    sonda.getpass.getpass = _getpass
checar(perguntou["sim"] or s2 not in ("", None),
       "contraprova: sem senha no ambiente, ela é pedida — nunca fica vazia")
del os.environ["OMNISSA_UEM_USUARIO"]


# ── 4. Cabeçalhos ─────────────────────────────────────────────────────
print("\n[4] Basic auth e cabeçalhos da UEM")

import base64  # noqa: E402

cab = sonda.cabecalhos_basicos("renner\\fulano", "segredo", "TENANT123", 2)
cru = base64.b64decode(cab["Authorization"].split(" ", 1)[1]).decode()
checar(cab["Authorization"].startswith("Basic "), "o cabeçalho é Basic")
checar(cru == "renner\\fulano:segredo", "usuário e senha entram como usuario:senha")
checar(cab.get("aw-tenant-code") == "TENANT123", "aw-tenant-code acompanha o Basic")
checar(cab["Accept"] == "application/json;version=2",
       f"a versão da API vai no Accept ({cab['Accept']})")

sem_tenant = sonda.cabecalhos_basicos("a", "b", "", 1)
checar("aw-tenant-code" not in sem_tenant,
       "sem tenant informado, o cabeçalho não é inventado")

# Contraprova: a senha não pode aparecer em claro em cabeçalho nenhum.
checar(all("segredo" not in str(v) for k, v in cab.items() if k != "Authorization"),
       "contraprova: a senha não vaza em nenhum outro cabeçalho")


# ── 5. TLS ────────────────────────────────────────────────────────────
print("\n[5] TLS")
checar("verify=False" not in FONTE and "verify = False" not in FONTE,
       "a sonda não desliga a verificação de certificado")
checar("integracoes import http" in FONTE or "from integracoes import http" in FONTE,
       "a sessão sai de integracoes/http.py (a política de TLS do portal)")


# ── 6. O host certo ───────────────────────────────────────────────────
print("\n[6] Host da API, que não é o do console")
checar(sonda.UEM_PADRAO == "as258.awmdm.com",
       f"o padrão é o host de API das especificações ({sonda.UEM_PADRAO})")
try:
    from config import get_settings
    console = get_settings().MDM_BASE_URL
    checar(sonda.UEM_PADRAO not in console,
           f"o console segue sendo outro host ({console}) — as258 é só da API")
except Exception as exc:  # noqa: BLE001
    checar(False, f"não deu para ler MDM_BASE_URL do config: {exc}")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram")
if falhas:
    print("\nFalhou:")
    for f in falhas:
        print("  -", f)
sys.exit(1 if falhas else 0)
