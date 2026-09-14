#!/usr/bin/env python3
"""Verificação do diagnóstico do cofre visto de dentro do serviço.

    python3 scripts/verificar_cofre_diagnostico.py

Sobe o portal em memória, entra como admin e chama `/api/cofre/*`. O que
mais importa aqui não é o cofre responder — no servidor de teste ele nem
existe — e sim que a tela diga a verdade e que **nenhum valor de segredo
saia na resposta**.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
# Cofre local próprio: a verificação nunca toca no cofre de ninguém.
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import cofre, security  # noqa: E402

# Segredo de mentira no cofre local, para provar que o valor não vaza.
SEGREDO = "valor-secreto-que-nao-pode-aparecer"
cofre.definir("CORREIOS_CHAVE", SEGREDO)
cofre.definir("CORREIOS_USUARIO", "usuario-de-teste")

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

app = main.app
cliente = TestClient(app)

# Sessão de admin direto na memória: o login de verdade depende do AD.
sid, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)

print("\n[1] O diagnóstico responde e diz quem o serviço é")
r = cliente.get("/api/cofre/diagnostico")
checar(r.status_code == 200, f"HTTP 200 ({r.status_code})")
d = r.json() if r.status_code == 200 else {}
checar(bool(d.get("usuario_do_servico")), "diz o usuário do processo")
checar("corporativo_ok" in d, "diz se o cofre corporativo responde")
checar(isinstance(d.get("corporativo_detalhe"), str) and d["corporativo_detalhe"],
       "explica por que responde ou não")
checar(isinstance(d.get("grupos"), list) and len(d["grupos"]) >= 4,
       "traz os grupos de chaves (Correios, EBS, ServiceNow, MDM)")
checar(d.get("grupos", [{}])[0].get("nome") == "Correios",
       "os Correios vêm primeiro — é a referência já validada")

print("\n[2] Nenhum valor de segredo sai na resposta")
bruto = r.text
checar(SEGREDO not in bruto, "a chave dos Correios não aparece no JSON")
correios = next((g for g in d.get("grupos", []) if g["nome"] == "Correios"), {})
por_chave = {c["chave"]: c for c in correios.get("chaves", [])}
checar(por_chave.get("CORREIOS_CHAVE", {}).get("resolvida") is True,
       "mesmo assim, a chave consta como resolvida")
checar("valor" not in por_chave.get("CORREIOS_CHAVE", {}),
       "e sem o campo valor")
checar(por_chave.get("CORREIOS_CHAVE", {}).get("tamanho") == len(SEGREDO),
       "o tamanho basta para separar 'não existe' de 'existe e veio vazio'")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("valor") == "usuario-de-teste",
       "o que não é segredo aparece, para conferir se é o valor esperado")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("no_local") is True,
       "aponta o cofre local como origem")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("no_corporativo") is False,
       "e que o corporativo não tem — é assim que se enxerga o sombreamento")
checar(por_chave.get("CORREIOS_CARTOES", {}).get("resolvida") is False,
       "chave ausente aparece como não resolvida, sem inventar valor")

print("\n[3] Permissão do arquivo do cofre corporativo")
for campo in ("modulo_corporativo", "arquivo_corporativo"):
    a = d.get(campo) or {}
    checar(set(("caminho", "existe", "legivel", "pasta_acessivel")) <= set(a),
           f"{campo}: diz caminho, existência, leitura e acesso à pasta")
checar(isinstance(d.get("permissoes_corporativo"), dict),
       "traz dono/grupo/modo do arquivo — é onde a falha de permissão aparece")

print("\n[3b] De onde o cofre lê, e se o nome pode ser outro")
inv = d.get("inventario") or {}
checar(isinstance(inv, dict) and "modulo_carregado" in inv,
       "diz se o módulo do cofre carregou")
checar("sabe_listar" in inv and isinstance(inv.get("nomes"), list),
       "diz se o cofre sabe listar as próprias chaves")
checar(isinstance(inv.get("arquivos_do_modulo"), list),
       "lista os arquivos que o próprio módulo aponta")
pastas = {p["caminho"]: p for p in inv.get("pastas", [])}
checar("/usr/local/lib/vcreports" in pastas and "/etc/vcreports" in pastas,
       "olha as duas pastas do cofre corporativo")
checar(all(set(("existe", "listavel", "itens")) <= set(v) for v in pastas.values()),
       "de cada pasta diz se existe, se lista e o que há dentro")
alt = d.get("alternativas") or []
checar(len(alt) == 3, "sonda os apelidos de usuário, senha e endereço do EBS")
nomes_alt = [c["chave"] for g in alt for c in g["chaves"]]
checar("ORACLE_EBS_PASS" in nomes_alt and "ORACLE_EBS_PASSWORD" in nomes_alt,
       "inclui o nome atual e as variantes")
checar(all(not c.get("resolvida") for g in alt for c in g["chaves"]),
       "sem cofre, nenhum apelido resolve — e nenhum valor é inventado")

print("\n[3c] Com um cofre corporativo de mentira, o inventário aparece")
# O caminho que importa no servidor: o módulo IMPORTA. Sem simular isso, o
# inventário nunca é exercitado — e é justamente ele que separa "não alcança
# o cofre" de "a chave tem outro nome".
import types  # noqa: E402

FALSO = _TMP / "backing.env"
FALSO.write_text("nada aqui\n", encoding="utf-8")
mod = types.ModuleType("vcreports_secrets")
mod.SECRETS = {"CORREIOS_USUARIO": "conta-de-servico",
               "ORACLE_EBS_SENHA": "senha-simulada-que-nao-pode-sair"}
mod.CAMINHO_DOS_SEGREDOS = str(FALSO)
mod.s = lambda nome: mod.SECRETS.get(nome, "")
cofre._modulo, cofre._modulo_via = mod, "import direto (simulado)"

r6 = cliente.get("/api/cofre/diagnostico")
d6 = r6.json()
inv6 = d6.get("inventario") or {}
checar(inv6.get("modulo_carregado") is True, "o módulo aparece como carregado")
checar(inv6.get("funcao") == "<lambda>" or inv6.get("funcao"),
       "diz qual função do módulo é usada")
checar(inv6.get("sabe_listar") is True and "ORACLE_EBS_SENHA" in inv6.get("nomes", []),
       "lista os nomes que o cofre expõe")
checar("senha-simulada-que-nao-pode-sair" not in r6.text,
       "e a senha do cofre corporativo não sai na resposta")
apontados = {a["caminho"]: a for a in inv6.get("arquivos_do_modulo", [])}
checar(str(FALSO) in apontados and apontados[str(FALSO)]["legivel"] is True,
       "aponta o arquivo que o próprio módulo lê, e se dá para ler")
alt6 = {c["chave"]: c for g in d6.get("alternativas", []) for c in g["chaves"]}
checar(alt6["ORACLE_EBS_PASS"]["resolvida"] is False,
       "o nome de hoje continua sem resolver")
checar(alt6["ORACLE_EBS_SENHA"]["resolvida"] is True
       and alt6["ORACLE_EBS_SENHA"]["no_corporativo"] is True,
       "e o apelido certo aparece resolvido, no cofre corporativo")
cofre._modulo, cofre._modulo_via = None, ""

print("\n[3d] Os nomes que estão DENTRO do arquivo do cofre")
# É a pergunta direta: o serviço enxerga o conteúdo? Lista cheia = enxerga;
# erro de permissão = não enxerga. E em nenhum dos casos o valor sai.
PHP = _TMP / "secrets.php"
PHP.write_text(
    "<?php\n"
    "define('ORACLE_EBS_USUARIO', 'inframon');\n"
    "define('ORACLE_EBS_SENHA', 'senha-do-arquivo-que-nao-pode-sair');\n"
    "$mapa = ['CORREIOS_CHAVE' => 'chave-do-arquivo-que-nao-pode-sair'];\n",
    encoding="utf-8")
ENV = _TMP / "outro.env"
ENV.write_text("# comentário\nexport SN_API_USER=conta\nMDM_SENHA='nao-pode-sair'\n",
               encoding="utf-8")
JSON = _TMP / "cofre_ext.json"
JSON.write_text('{"EBS_PASS": "tambem-nao-pode-sair"}', encoding="utf-8")

mod2 = types.ModuleType("vcreports_secrets")
mod2.ARQUIVO_PHP = str(PHP)
mod2.ARQUIVO_ENV = str(ENV)
mod2.ARQUIVO_JSON = str(JSON)
mod2.s = lambda nome: ""
cofre._modulo, cofre._modulo_via = mod2, "import direto (simulado 2)"

r7 = cliente.get("/api/cofre/diagnostico")
d7 = r7.json()
conteudo = {c["caminho"]: c for c in (d7.get("inventario") or {}).get("conteudo", [])}
php = conteudo.get(str(PHP), {})
checar(php.get("legivel") is True, "lê o arquivo em formato PHP")
checar("ORACLE_EBS_USUARIO" in php.get("nomes", [])
       and "ORACLE_EBS_SENHA" in php.get("nomes", []),
       "pega os nomes do define()")
checar("CORREIOS_CHAVE" in php.get("nomes", []), "e os nomes do array =>")
env = conteudo.get(str(ENV), {})
checar("SN_API_USER" in env.get("nomes", []) and "MDM_SENHA" in env.get("nomes", []),
       "pega os nomes do formato env, com ou sem export")
checar("comentário" not in str(env.get("nomes")), "e ignora comentário")
js_ = conteudo.get(str(JSON), {})
checar("EBS_PASS" in js_.get("nomes", []), "pega os nomes do formato JSON")
for proibido in ("senha-do-arquivo-que-nao-pode-sair", "chave-do-arquivo-que-nao-pode-sair",
                 "nao-pode-sair", "tambem-nao-pode-sair", "inframon"):
    checar(proibido not in r7.text, f"nenhum valor do arquivo sai na resposta ({proibido})")
inv7 = d7.get("inventario") or {}
checar("ORACLE_EBS_USUARIO" in inv7.get("nomes", []),
       "o inventário junta o que veio dos arquivos")

# Arquivo sem permissão: a tela precisa dizer isso, não uma lista vazia.
FECHADO = _TMP / "fechado.env"
FECHADO.write_text("X=1\n", encoding="utf-8")
os.chmod(FECHADO, 0o000)
from routers import cofre as rc  # noqa: E402
# Como root o chmod não impede nada, e é justamente o caso do servidor que
# precisa estar certo — então a recusa é forçada na abertura do arquivo.
from unittest.mock import patch  # noqa: E402
with patch("builtins.open", side_effect=PermissionError("negado")):
    sem = rc._nomes_no_arquivo(str(FECHADO))
checar(sem["legivel"] is False and "permissão" in sem["erro"],
       "arquivo sem permissão vira erro claro, não lista vazia")
os.chmod(FECHADO, 0o600)
checar(rc._nomes_no_arquivo(str(_TMP / "nao-existe.env"))["erro"] != "",
       "arquivo inexistente também explica o porquê")

print("\n[3e] Chave nos dois cofres com valores diferentes")
cofre.definir("SN_API_USER", "valor-do-cofre-local")
mod3 = types.ModuleType("vcreports_secrets")
mod3.s = lambda n: "valor-do-corporativo" if n == "SN_API_USER" else ""
cofre._modulo, cofre._modulo_via = mod3, "import direto (simulado 3)"
d8 = cliente.get("/api/cofre/diagnostico").json()
sn = {c["chave"]: c for g in d8["grupos"] if g["nome"] == "ServiceNow"
      for c in g["chaves"]}["SN_API_USER"]
checar(sn.get("divergente") is True,
       "aponta que os dois cofres discordam — sem comparar nada na tela")
checar(sn.get("fonte") == "cofre corporativo",
       "e diz qual dos dois o portal usa de fato")
cofre._modulo, cofre._modulo_via = None, ""

print("\n[3g] Correios pelo ambiente, e o cofre local sombreando")
# O jeito novo de entregar o segredo: variável no processo do serviço. O
# cofre local vem ANTES do ambiente, então um valor velho esquecido ali
# derruba a variável nova sem avisar — é isso que a tela precisa mostrar.
os.environ["CORREIOS_CARTOES"] = "cartao-do-ambiente"
os.environ["CORREIOS_USUARIO"] = "usuario-do-ambiente"
d9 = cliente.get("/api/cofre/diagnostico").json()
co9 = {c["chave"]: c for g in d9["grupos"] if g["nome"] == "Correios" for c in g["chaves"]}

checar(co9["CORREIOS_CARTOES"]["fonte"] == "ambiente",
       "chave só no ambiente é resolvida pelo ambiente")
checar(co9["CORREIOS_CARTOES"]["sombreado"] is False,
       "e sem aviso de sombreamento, porque só há uma fonte")

# CORREIOS_USUARIO está no cofre local (posto no início) E no ambiente.
checar(co9["CORREIOS_USUARIO"]["fonte"] == "cofre local",
       "com as duas fontes, o cofre local ganha do ambiente")
checar(co9["CORREIOS_USUARIO"]["sombreado"] is True,
       "e a tela avisa que a chave está em mais de uma fonte")
checar(co9["CORREIOS_USUARIO"]["divergente"] is True,
       "apontando que os valores discordam")
checar(co9["CORREIOS_USUARIO"]["fontes_com_valor"] == ["cofre local", "ambiente"],
       "nomeando quais fontes têm a chave")
checar("usuario-do-ambiente" not in str(co9["CORREIOS_USUARIO"]),
       "sem comparar valores na tela — só o fato de discordarem")

os.environ.pop("CORREIOS_USUARIO", None)
d10 = cliente.get("/api/cofre/diagnostico").json()
co10 = {c["chave"]: c for g in d10["grupos"] if g["nome"] == "Correios" for c in g["chaves"]}
checar(co10["CORREIOS_USUARIO"]["sombreado"] is False,
       "tirada a duplicidade, o aviso some")
os.environ.pop("CORREIOS_CARTOES", None)

print("\n[3f] Sem permissão, a tela entrega o pedido pronto")
# O caso real do servidor: o módulo importa, mas o arquivo do cofre não é
# legível pelo usuário do serviço. A tela precisa dizer o que pedir.
from routers import cofre as rc2  # noqa: E402

ARQ = _TMP / "secrets.env"
ARQ.write_text("X=1\n", encoding="utf-8")
info = rc2._arquivo(str(ARQ))
checar(info["existe"] and info["dono"] and info["modo"],
       "diz dono e modo do arquivo do cofre")
checar(info["pasta"]["caminho"] == str(_TMP) and info["pasta"]["grupo"],
       "e o dono/grupo da pasta — serve quando o arquivo não se deixa consultar")

sem_leitura = dict(info, legivel=False, grupo="vcreports", dono="root")
rem = rc2._remedio(sem_leitura, "portal")
checar(rem["necessario"] is True, "reconhece que falta permissão")
checar("vcreports" in rem["motivo"] and "portal" in rem["motivo"],
       "explica com o grupo do arquivo e o usuário do serviço")
checar(any("usermod -aG vcreports portal" in c for c in rem["comandos"]),
       "e monta o comando com os dois nomes certos")
checar(any("chmod g+r" in c for c in rem["comandos"]), "mais a leitura do arquivo")

# O caso que engana: arquivo 644 e mesmo assim "Permission denied", porque
# a permissão da PASTA é verificada antes do modo do arquivo.
fora = rc2._remedio({"caminho": "/etc/vcreports/.secrets.env", "existe": False,
                     "pasta_acessivel": False, "modo": "644",
                     "pasta": {"caminho": "/etc/vcreports"}}, "portal")
checar(fora["necessario"] is True, "pasta inacessível vira pedido, não silêncio")
checar("644" in fora["motivo"] and "/etc/vcreports" in fora["motivo"],
       "e explica por que o 644 do arquivo não resolve sozinho")
checar(any(c == "chmod o+x /etc/vcreports" for c in fora["comandos"]),
       "com o comando que abre a pasta")
checar(any("setfacl" in c for c in fora["comandos"]),
       "e a alternativa que libera só o serviço")

sumido = rc2._remedio({"caminho": "/etc/vcreports/.secrets.env", "existe": False,
                       "pasta_acessivel": True,
                       "pasta": {"caminho": "/etc/vcreports"}}, "portal")
checar("não está lá" in sumido["motivo"],
       "pasta acessível e arquivo ausente é outro problema, e é dito como tal")
checar(rc2._remedio(info, "portal")["necessario"] is False,
       "arquivo legível, em pasta acessível, não gera pedido nenhum")

print("\n[3h] Ver tudo: a lista completa, sem publicar segredo")
os.environ["CORREIOS_CARTOES"] = "cartao-do-ambiente"
r11 = cliente.get("/api/cofre/tudo")
checar(r11.status_code == 200, f"HTTP 200 ({r11.status_code})")
d11 = r11.json()
chaves11 = {i["chave"]: i for i in d11.get("itens", [])}
checar(d11["total"] == len(d11["itens"]) and d11["total"] > 10,
       "traz a lista inteira das três fontes")
checar("CORREIOS_CHAVE" in chaves11 and chaves11["CORREIOS_CHAVE"]["fonte"] == "cofre local",
       "inclui o que está no cofre local")
checar("CORREIOS_CARTOES" in chaves11
       and chaves11["CORREIOS_CARTOES"]["fonte"] == "ambiente",
       "e o que está só no ambiente")
checar(d11["por_fonte"]["ambiente"] >= 1 and d11["por_fonte"]["cofre local"] >= 1,
       "conta quantas vieram de cada fonte")

# O ambiente do processo carrega o que assina a sessão e a URL do banco.
# Publicar qualquer um dos dois numa tela seria pior que o problema.
checar(SEGREDO not in r11.text, "a chave dos Correios não aparece")
checar(os.environ["PORTAL_SESSION_SECRET"] not in r11.text,
       "o segredo que assina a sessão não aparece")
checar("valor" not in chaves11.get("PORTAL_SESSION_SECRET", {"valor": 1}),
       "e ele é tratado como segredo pelo nome")
checar("valor" not in chaves11.get("DATABASE_URL", {"valor": 1}),
       "a URL do banco também — senha vem embutida nela com frequência")
for nome in ("MINHA_SENHA", "API_TOKEN", "X_PWD", "SERVICO_URL", "BASE_DSN",
             "CRED_AUTH", "COOKIE_X", "CHAVE_Y"):
    checar(rc2._e_segredo(nome), f"{nome} é tratado como segredo")
checar(not rc2._e_segredo("CORREIOS_USUARIO") and not rc2._e_segredo("ORACLE_EBS_USER"),
       "e nome de usuário continua visível, que é o que se precisa conferir")
os.environ.pop("CORREIOS_CARTOES", None)

anon2 = TestClient(app)
checar(anon2.get("/api/cofre/tudo").status_code in (401, 403), "sem sessão, não responde")

print("\n[3i] Uma parte quebrada não derruba a tela inteira")
# A tela existe para explicar falhas. Se ela mesma devolver 500, não sobra
# nada para diagnosticar — então cada parte responde por si.
from unittest.mock import patch as _patch  # noqa: E402

with _patch.object(rc2, "_inventario_corporativo",
                   side_effect=RuntimeError("cofre explodiu")):
    r12 = cliente.get("/api/cofre/diagnostico")
checar(r12.status_code == 200, f"segue respondendo 200 ({r12.status_code})")
d12 = r12.json()
checar(d12.get("usuario_do_servico"), "o resto do diagnóstico continua inteiro")
checar("inventário do cofre" in (d12.get("erros") or {}),
       "e a parte que falhou aparece nomeada")
checar("cofre explodiu" in d12["erros"]["inventário do cofre"],
       "com o erro de verdade, não um texto genérico")

with _patch.object(rc2, "_sondar", side_effect=RuntimeError("sonda explodiu")):
    r13 = cliente.get("/api/cofre/diagnostico")
checar(r13.status_code == 200, "idem quando a sondagem de chaves falha")
checar("chaves por assunto" in (r13.json().get("erros") or {}),
       "nomeando a seção certa")

with _patch.object(rc2, "_inventario_corporativo",
                   side_effect=RuntimeError("cofre explodiu")):
    r14 = cliente.get("/api/cofre/tudo")
checar(r14.status_code == 200 and "erros" in r14.json(),
       "a lista completa também não morre por causa de uma parte")

print("\n[4] Sondagem avulsa")
r2 = cliente.get("/api/cofre/sondar/CORREIOS_CHAVE")
checar(r2.status_code == 200, f"HTTP 200 ({r2.status_code})")
checar(SEGREDO not in r2.text, "também não vaza o valor")
r3 = cliente.get("/api/cofre/sondar/nao;existe")
checar(r3.status_code == 422, f"nome inválido é recusado ({r3.status_code})")
r4 = cliente.get("/api/cofre/sondar/correios_usuario")
checar(r4.status_code == 200 and r4.json().get("chave") == "CORREIOS_USUARIO",
       "o nome é normalizado para maiúsculas")

print("\n[5] Teste dos Correios: falta de chave não vira 500")
r5 = cliente.post("/api/cofre/testar-correios")
checar(r5.status_code == 200, f"responde 200 com o diagnóstico ({r5.status_code})")
d5 = r5.json() if r5.status_code == 200 else {}
checar(d5.get("ok") is False, "sem CORREIOS_CARTOES, o teste falha")
checar(d5.get("etapa") == "cofre", "e diz que parou no cofre, não na API")
checar("CORREIOS_CARTOES" in (d5.get("detalhe") or ""), "nomeando a chave que falta")
checar(SEGREDO not in r5.text, "sem vazar segredo nem aqui")

print("\n[6] Sem sessão, nada responde")
anonimo = TestClient(app)
for caminho in ("/api/cofre/diagnostico", "/api/cofre/sondar/CORREIOS_CHAVE"):
    checar(anonimo.get(caminho).status_code in (401, 403), f"{caminho} exige sessão")
checar(anonimo.post("/api/cofre/testar-correios").status_code in (401, 403),
       "/api/cofre/testar-correios exige sessão")

# Usuário comum, sem admin: a tela é de administração.
sid2, cookie2 = security.create_session(
    {"username": "comum", "is_admin": False, "permission_map": {}})
comum = TestClient(app)
comum.cookies.set("spare_session", cookie2)
checar(comum.get("/api/cofre/diagnostico").status_code == 403,
       "usuário sem admin recebe 403")
checar(comum.get("/api/cofre/tudo").status_code == 403,
       "e a lista completa também é só de admin")

print("\n[7] A aba existe na tela de Parâmetros")
js = (RAIZ / "static/modules/parametros.js").read_text(encoding="utf-8")
checar("['cofre',           'Cofre de segredos']" in js, "a aba está na lista")
checar("cofre:          renderCofre" in js, "e ligada ao renderizador")
checar("'cofre', 'ebs-oracle'" in js, "é aba de admin")
checar("/cofre/testar-correios" in js, "a tela chama o teste dos Correios")
checar("em ' + (k.fontes_com_valor || []).length + ' fontes" in js,
       "a tela mostra em quantas fontes a chave está")
checar("errosHtml" in js, "a tela mostra qual parte do diagnóstico falhou")
checar("/cofre/tudo" in js and "cf-filtro" in js,
       "a tela tem o botão Ver tudo, com filtro por nome")
checar("remedioHtml" in js, "a tela mostra o pedido de permissão pronto")
checar("valores diferentes" in js, "a tela avisa quando os cofres discordam")
checar("inventarioHtml" in js and "alternativasHtml" in js,
       "a tela mostra de onde o cofre lê e os apelidos sondados")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Diagnóstico do cofre íntegro.")
