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

print("\n[3] O diagnóstico não abre arquivo nenhum")
# O diagnóstico não pode tocar em arquivo: o cofre é consumido por
# referência, e abrir o arquivo só produz "Permission denied" e a falsa
# impressão de que o cofre está quebrado.
for campo in ("modulo_corporativo", "arquivo_corporativo",
              "permissoes_corporativo", "remedio"):
    checar(campo not in d, f"não expõe {campo} — nada de mexer em arquivo")

print("\n[3b] De onde o cofre lê, e se o nome pode ser outro")
inv = d.get("inventario") or {}
checar(isinstance(inv, dict) and "modulo_carregado" in inv,
       "diz se o módulo do cofre carregou")
checar(inv.get("sabe_listar") is False,
       "sem loader, não há o que listar")
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
       "lê o cache do loader (atributo SECRETS) e lista os NOMES que ele carregou")
checar("senha-simulada-que-nao-pode-sair" not in r6.text,
       "e a senha do cofre corporativo não sai na resposta")
alt6 = {c["chave"]: c for g in d6.get("alternativas", []) for c in g["chaves"]}
checar(alt6["ORACLE_EBS_PASS"]["resolvida"] is False,
       "o nome de hoje continua sem resolver")
checar(alt6["ORACLE_EBS_SENHA"]["resolvida"] is True
       and alt6["ORACLE_EBS_SENHA"]["no_corporativo"] is True,
       "e o apelido certo aparece resolvido, no cofre corporativo")
cofre._modulo, cofre._modulo_via = None, ""

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

from routers import cofre as rc2  # noqa: E402

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

print("\n[3j] Sondar vários nomes: a única busca possível no cofre")
os.environ["CORREIOS_CARTOES"] = "cartao-do-ambiente"
r15 = cliente.post("/api/cofre/sondar-varios",
                   json={"nomes": "correios_cartoes, NAO_EXISTE_ISSO\nCORREIOS_CHAVE"})
checar(r15.status_code == 200, f"HTTP 200 ({r15.status_code})")
d15 = r15.json()
por = {i["chave"]: i for i in d15["itens"]}
checar(d15["total"] == 3, "aceita vírgula, espaço e quebra de linha no mesmo texto")
checar("CORREIOS_CARTOES" in por, "normaliza para maiúsculas")
checar(por["CORREIOS_CARTOES"]["resolvida"] and por["NAO_EXISTE_ISSO"]["resolvida"] is False,
       "separa o que responde do que não responde")
checar(d15["resolvidas"] == 2, "e conta quantas responderam")
checar(SEGREDO not in r15.text, "sem vazar valor de segredo")
checar(cliente.post("/api/cofre/sondar-varios", json={"nomes": "   "}).status_code == 422,
       "texto vazio é recusado")
checar(cliente.post("/api/cofre/sondar-varios",
                    json={"nomes": "a;b" * 500}).status_code == 200,
       "lista enorme não derruba nada")
checar(anon2.post("/api/cofre/sondar-varios", json={"nomes": "X"}).status_code in (401, 403),
       "e exige sessão")
os.environ.pop("CORREIOS_CARTOES", None)

print("\n[3k] Comando externo: a saída quando o loader Python não alcança")
# Se outro programa lê o cofre (o PHP do time, por exemplo), o core resolve
# por comando. O que não pode é o diagnóstico dizer "configurado" sem testar.
import importlib  # noqa: E402
import stat as _stat  # noqa: E402

FALSO_CMD = _TMP / "cofre_falso.sh"
FALSO_CMD.write_text(
    "#!/bin/sh\n"
    "case \"$1\" in\n"
    "  CORREIOS_USUARIO) echo conta-vinda-do-comando ;;\n"
    "  *) exit 0 ;;\n"
    "esac\n", encoding="utf-8")
os.chmod(FALSO_CMD, 0o755)

os.environ["VCREPORTS_SECRETS_CMD"] = f"{FALSO_CMD} {{chave}}"
cofre_cmd = importlib.reload(cofre)
ok_cmd, det_cmd = cofre_cmd.diagnostico_corporativo("CORREIOS_USUARIO")
checar(ok_cmd is True, "com o comando respondendo, o cofre é dado como disponível")
checar("respondeu" in det_cmd, "e o detalhe diz que ele foi executado de verdade")
checar("não verificado" not in det_cmd, "sem mais o 'não verificado' de antes")

ruim, det_ruim = cofre_cmd.diagnostico_corporativo("CHAVE_QUE_O_COMANDO_NAO_TEM")
checar(ruim is False, "com o comando calado para a chave, é indisponível")
checar("não devolveu valor" in det_ruim, "dizendo exatamente isso")

checar(cofre_cmd._corporativo("CORREIOS_USUARIO") == "conta-vinda-do-comando",
       "e a resolução por comando realmente entrega o valor")
os.environ.pop("VCREPORTS_SECRETS_CMD", None)
importlib.reload(cofre)

print("\n[3l] A ponte PHP: o portal resolvendo pelo cofre do time")
# O acesso de referência que o time mantém é em PHP. Se o PHP lê e o Python
# não, o portal resolve pela ponte — sem código novo, só a variável na unit.
import shutil  # noqa: E402

PHP = shutil.which("php")
if not PHP:
    print("  (sem php neste ambiente: a ponte não pode ser exercitada aqui)")
else:
    LOADER = _TMP / "secrets_do_time.php"
    LOADER.write_text(
        "<?php\n"
        "function secret($k) {\n"
        "  $m = ['ORACLE_EBS_USUARIO' => 'usuario-de-teste',\n"
        "        'ORACLE_EBS_SENHA' => 'senha-do-php-que-nao-pode-sair'];\n"
        "  return $m[$k] ?? null;\n"
        "}\n", encoding="utf-8")
    PONTE = RAIZ / "scripts" / "cofre_php.php"
    os.environ["VCREPORTS_SECRETS_PHP"] = str(LOADER)
    os.environ["VCREPORTS_SECRETS_CMD"] = f"{PHP} {PONTE} {{chave}}"
    cofre_php = importlib.reload(cofre)

    checar(cofre_php._corporativo("ORACLE_EBS_USUARIO") == "usuario-de-teste",
           "a ponte entrega o valor que só o PHP enxerga")
    checar(cofre_php.fonte("ORACLE_EBS_SENHA") == "cofre corporativo",
           "e o portal contabiliza como cofre corporativo")
    checar(cofre_php._corporativo("NAO_EXISTE_NO_PHP") == "",
           "chave inexistente volta vazia, sem inventar valor")
    ok_php, det_php = cofre_php.diagnostico_corporativo("ORACLE_EBS_USUARIO")
    checar(ok_php is True and "respondeu" in det_php,
           "e o diagnóstico confirma executando a ponte")

    r16 = cliente.post("/api/cofre/sondar-varios",
                       json={"nomes": "ORACLE_EBS_USUARIO ORACLE_EBS_SENHA"})
    achados = {i["chave"]: i for i in r16.json()["itens"]}
    checar(achados["ORACLE_EBS_USUARIO"]["valor"] == "usuario-de-teste",
           "a sondagem pela tela também acha, pela ponte")
    checar("senha-do-php-que-nao-pode-sair" not in r16.text,
           "e a senha vinda do PHP continua sem sair na resposta")

    os.environ.pop("VCREPORTS_SECRETS_CMD", None)
    os.environ.pop("VCREPORTS_SECRETS_PHP", None)
    importlib.reload(cofre)

print("\n[3m] O teste do PHP feito PELO SERVIÇO, não pelo terminal")
# Só o serviço alcança o cofre. Rodar php no terminal responde sobre o
# terminal — então quem executa a ponte é o processo do portal.
r17 = cliente.post("/api/cofre/testar-php",
                   json={"loader": "/tmp/qualquer.php", "chave": "X"})
checar(r17.status_code == 422, f"loader fora das pastas do cofre é recusado ({r17.status_code})")
checar("/usr/local/lib/vcreports/" in r17.json().get("detail", ""),
       "dizendo onde ele pode estar")
checar(cliente.post("/api/cofre/testar-php",
                    json={"loader": "/etc/vcreports/x.txt"}).status_code == 422,
       "e precisa ser um .php — o caminho vira require, ou seja, código")
checar(cliente.post("/api/cofre/testar-php",
                    json={"chave": "a b; rm -rf /"}).status_code == 422,
       "nome de chave inválido é recusado antes de qualquer execução")

if not PHP:
    print("  (sem php: o resto desta seção não se aplica)")
else:
    PASTA = Path("/usr/local/lib/vcreports")
    criei = not PASTA.exists()
    PASTA.mkdir(parents=True, exist_ok=True)
    ALVO = PASTA / "secrets_de_teste.php"
    ALVO.write_text(
        "<?php\n"
        "function secret($k) {\n"
        "  return $k === 'CORREIOS_USUARIO' ? 'conta-de-servico' : null;\n"
        "}\n", encoding="utf-8")

    d18 = cliente.post("/api/cofre/testar-php",
                       json={"loader": str(ALVO), "chave": "CORREIOS_USUARIO"}).json()
    checar(d18["ok"] is True, "o serviço executa a ponte e o PHP responde")
    checar(d18["retirada_do_ambiente"] is False,
           "chave fora do ambiente: aí sim o valor só pode ter vindo do cofre")
    checar("caracteres" in d18["detalhe"] and "conta-de-servico" not in str(d18),
           "informando só o tamanho — o valor nunca sai")
    checar(str(RAIZ / "scripts" / "cofre_php.php") in d18["comando_para_a_unit"],
           "e devolve a linha pronta para a unit")
    checar(str(ALVO) in d18["variavel_do_loader"],
           "com a variável do loader quando ele não é o padrão")

    # A prova: um loader que só ecoa o ambiente NÃO pode passar no teste,
    # senão qualquer variável do serviço se disfarça de segredo do cofre.
    ECO = PASTA / "secrets_eco.php"
    ECO.write_text("<?php\nfunction secret($k) { return getenv($k) ?: null; }\n",
                   encoding="utf-8")
    os.environ["CHAVE_SO_DO_AMBIENTE"] = "valor-que-veio-do-ambiente"
    d18b = cliente.post("/api/cofre/testar-php",
                        json={"loader": str(ECO), "chave": "CHAVE_SO_DO_AMBIENTE"}).json()
    checar(d18b["ok"] is True,
           "loader que ecoa o ambiente responde — é o comportamento real da ponte")
    checar(d18b["retirada_do_ambiente"] is True,
           "e a tela avisa que a chave também está no ambiente, então não prova o cofre")
    os.environ.pop("CHAVE_SO_DO_AMBIENTE", None)
    ECO.unlink()

    d19 = cliente.post("/api/cofre/testar-php",
                       json={"loader": str(ALVO), "chave": "NAO_EXISTE"}).json()
    checar(d19["ok"] is False and "Sem valor" in d19["detalhe"],
           "chave ausente vira mensagem do PHP, não erro do portal")
    checar(d19["comando_para_a_unit"] == "",
           "e não sugere configurar nada quando não funcionou")

    d20 = cliente.post("/api/cofre/testar-php",
                       json={"loader": "/usr/local/lib/vcreports/nao_existe.php"}).json()
    checar(d20["ok"] is False and "legível" in d20["detalhe"],
           "loader inexistente é dito como tal, sem 500")

    ALVO.unlink()
    if criei:
        PASTA.rmdir()

checar(anon2.post("/api/cofre/testar-php", json={}).status_code in (401, 403),
       "e tudo isso exige sessão de admin")

print("\n[3n] O arquivo de ambiente do serviço: de onde vêm as variáveis")
# Uma credencial que funciona sem estar no cofre nem na unit veio daqui. E é
# aqui que se acrescenta a próxima, sem mexer na unit.
ENV_SERVICO = _TMP / "environment"
ENV_SERVICO.write_text(
    "# comentário que deve ser ignorado\n"
    "CORREIOS_USUARIO=conta-de-servico\n"
    'export CORREIOS_CHAVE="chave-do-environment-que-nao-pode-sair"\n'
    "SN_API_PASS=@cofre:SN_API_PASS@\n"
    "MDM_SENHA=\n"
    "linha sem igual\n",
    encoding="utf-8")
os.environ["PORTAL_ENV_FILE"] = str(ENV_SERVICO)

r21 = cliente.get("/api/cofre/diagnostico")
amb = r21.json().get("ambiente_do_servico") or {}
checar(amb.get("caminho") == str(ENV_SERVICO) and amb.get("legivel") is True,
       "acha e lê o arquivo de ambiente que a unit carrega")
por_nome = {k["chave"]: k for k in amb.get("chaves", [])}
checar(set(por_nome) == {"CORREIOS_USUARIO", "CORREIOS_CHAVE", "SN_API_PASS", "MDM_SENHA"},
       "lista as variáveis, ignorando comentário e linha sem '='")
checar(por_nome["CORREIOS_CHAVE"]["marcador"] is False,
       "reconhece valor direto — é assim que os Correios chegam hoje")
checar(por_nome["SN_API_PASS"]["marcador"] is True
       and por_nome["SN_API_PASS"]["aponta_para"] == "SN_API_PASS",
       "e reconhece o marcador @cofre:NOME@, que só resolve se o cofre responder")
checar(por_nome["MDM_SENHA"]["vazio"] is True, "variável vazia é apontada como tal")
checar("chave-do-environment-que-nao-pode-sair" not in r21.text,
       "e nenhum valor do arquivo sai na resposta")

# O caso que derruba a tela sem parecer: a variável está no arquivo e não
# chegou ao processo, porque o systemd não conseguiu ler a linha.
checar(por_nome["CORREIOS_USUARIO"]["chegou_ao_processo"] is False,
       "variável do arquivo que não está no processo é apontada")
os.environ["CORREIOS_USUARIO"] = "conta-de-servico"
amb1b = cliente.get("/api/cofre/diagnostico").json()["ambiente_do_servico"]
checar({k["chave"]: k for k in amb1b["chaves"]}["CORREIOS_USUARIO"]["chegou_ao_processo"] is True,
       "e quando chega, é dito que chegou")
os.environ.pop("CORREIOS_USUARIO", None)

print("\n[3p] O prefixo do portal, que também vem do ambiente")
# Sem prefixo o navegador busca CSS e JS no lugar errado e a tela abre crua.
from core import prefixo as _pf  # noqa: E402

_pf._cfg.APP_BASE_PATH = "/portal-spare"
pf1 = cliente.get("/api/cofre/diagnostico").json()["prefixo"]
checar(pf1["em_uso"] == "/portal-spare", "diz qual prefixo a página está usando")
checar(pf1["origem"] == "APP_BASE_PATH", "e de onde ele saiu")
_pf._cfg.APP_BASE_PATH = ""
pf2 = cliente.get("/api/cofre/diagnostico").json()["prefixo"]
checar(pf2["em_uso"] == "" and "nenhuma" in pf2["origem"],
       "sem prefixo, diz isso em vez de ficar calado — é o que explica a tela crua")

os.environ["PORTAL_ENV_FILE"] = str(_TMP / "nao-existe-environment")
amb2 = cliente.get("/api/cofre/diagnostico").json().get("ambiente_do_servico") or {}
checar(amb2.get("existe") is False and amb2.get("erro"),
       "sem arquivo, diz isso em vez de ficar mudo")
os.environ.pop("PORTAL_ENV_FILE", None)

print("\n[3q] O loader de verdade: _load() com cache, exportando para o ambiente")
# É assim que o loader do time se descreve: `cache = _load()`, "cache em
# memória por processo", e ordem cofre -> os.environ -> default. Se ele
# exporta o que leu para o os.environ (estilo dotenv), tudo do cofre parece
# "ambiente" para quem olha só o os.environ — foi esse o erro a corrigir.
mod6 = types.ModuleType("vcreports_secrets")
_COFRE_DO_TIME = {"CORREIOS_USUARIO": "conta-do-cofre", "CORREIOS_CHAVE": "chave-do-cofre-que-nao-pode-sair",
                  "ORACLE_EBS_USER": "usuario-de-teste", "ORACLE_EBS_PASS": "senha-do-cofre-que-nao-pode-sair"}
def _load6():
    for k, v in _COFRE_DO_TIME.items():
        os.environ.setdefault(k, v)   # exporta, como o load_dotenv
    return dict(_COFRE_DO_TIME)
mod6._load = _load6
mod6.s = lambda k, default=None: _load6().get(k, os.environ.get(k, default))
cofre._modulo, cofre._modulo_via = mod6, "import direto (loader com _load)"
for k in _COFRE_DO_TIME: os.environ.pop(k, None)

d24 = cliente.get("/api/cofre/diagnostico").json()
inv24 = d24["inventario"]
checar(inv24["sabe_listar"] is True and inv24["listagem_por"] == "função _load()",
       "pergunta ao loader pelo _load() e recebe o cache")
checar(inv24["nomes"] == sorted(_COFRE_DO_TIME), "lista exatamente os nomes que o loader carregou")
ebs24 = {c["chave"]: c for g in d24["grupos"] if g["nome"] == "Base EBS (Oracle)" for c in g["chaves"]}
checar(ebs24["ORACLE_EBS_PASS"]["no_corporativo"] is True and ebs24["ORACLE_EBS_PASS"]["fonte"] == "cofre corporativo (loader)",
       "chave no cache do loader é DO COFRE, mesmo estando também no os.environ")
checar(ebs24["ORACLE_EBS_PASS"]["indistinguivel"] is False, "sem o rótulo 'pode ser só o ambiente' — não há dúvida")
checar(ebs24["ORACLE_EBS_PASS"]["sombreado"] is False and ebs24["ORACLE_EBS_PASS"]["fontes_com_valor"] == ["cofre corporativo"],
       "e o eco no os.environ não conta como segunda fonte — é o loader exportando")
checar(ebs24["ORACLE_EBS_DSN"]["no_corporativo"] is False, "chave fora do cache não é dada como do cofre")
checar("senha-do-cofre-que-nao-pode-sair" not in r_txt if (r_txt := cliente.get("/api/cofre/diagnostico").text) else True,
       "e o valor da senha do cofre não aparece em lugar nenhum")
for k in _COFRE_DO_TIME: os.environ.pop(k, None)
cofre._modulo, cofre._modulo_via = None, ""

print("\n[3r] O LOADER DO TIME, tal e qual, lendo um cofre de verdade")
# Réplica exata de /usr/local/lib/vcreports/vcreports_secrets.py (enviado pelo
# time). Não é imitação: é o código dele, importado por caminho como o portal
# faz num venv. O que se prova: o portal lê o cofre PELO LOADER, considera
# "do cofre" só o que o loader leu, e diz qual arquivo foi lido.
import importlib.util  # noqa: E402
LOADER_REAL = _TMP / "vcreports_secrets.py"
LOADER_REAL.write_text('"""\n/usr/local/lib/vcreports/vcreports_secrets.py\n\nLoader do cofre central de credenciais (/etc/vcreports/.secrets.env).\n\nUso (Python do sistema, com .pth instalado):\n    from vcreports_secrets import s\n    db_pass = s(\'MYSQL_LOCAL_PASS\')\n\nUso em venv isolado (sem .pth):\n    from dotenv import load_dotenv\n    load_dotenv(\'/etc/vcreports/.secrets.env\')\n    # ou: importlib.util para carregar este modulo por path absoluto.\n\nOrdem de resolucao: cofre -> os.environ[key] -> default.\nCache em memoria (por processo).\n"""\nfrom __future__ import annotations\n\nimport os\nfrom typing import Optional\n\n_DEFAULT_PATH = "/etc/vcreports/.secrets.env"\n_cache: Optional[dict] = None\n\n\ndef _load() -> dict:\n    global _cache\n    if _cache is not None:\n        return _cache\n    path = os.environ.get("VCREPORTS_SECRETS_FILE", _DEFAULT_PATH)\n    out: dict = {}\n    try:\n        with open(path, "r", encoding="utf-8") as f:\n            for raw in f:\n                line = raw.strip()\n                if not line or line.startswith("#"):\n                    continue\n                if "=" not in line:\n                    continue\n                k, _, v = line.partition("=")\n                k, v = k.strip(), v.strip()\n                if len(v) >= 2 and ((v[0] == v[-1] == \'"\') or (v[0] == v[-1] == "\'")):\n                    v = v[1:-1]\n                out[k] = v\n    except OSError as e:\n        import sys\n        print(f"[vcreports/secrets] cofre nao legivel ({path}): {e}", file=sys.stderr)\n    _cache = out\n    return _cache\n\n\ndef s(key: str, default: Optional[str] = None) -> Optional[str]:\n    """Retorna o valor de `key` no cofre, ou env var, ou `default`."""\n    cache = _load()\n    if key in cache:\n        return cache[key]\n    return os.environ.get(key, default)\n\n\n# alias\nsecret = s\n', encoding="utf-8")
COFRE_REAL = _TMP / "secrets.env"
COFRE_REAL.write_text(
    "# cofre central\n"
    "CORREIOS_USUARIO=conta-do-cofre\n"
    "CORREIOS_CHAVE='chave-do-cofre-que-nao-pode-sair'\n"
    "ORACLE_EBS_USER=usuario-de-teste\n"
    'ORACLE_EBS_PASS="senha-do-cofre-que-nao-pode-sair"\n', encoding="utf-8")


def carregar_loader_real():
    spec = importlib.util.spec_from_file_location("vcreports_secrets", LOADER_REAL)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    cofre._modulo, cofre._modulo_via = m, f"carregado por caminho ({LOADER_REAL})"
    cofre._arquivo_cache = None
    return m


for k in ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "ORACLE_EBS_USER", "ORACLE_EBS_PASS", "MDM_USUARIO"):
    os.environ.pop(k, None)
os.environ["VCREPORTS_SECRETS_FILE"] = str(COFRE_REAL)
m = carregar_loader_real()

checar(cofre.caminho_do_loader() == str(COFRE_REAL), "o portal calcula o arquivo do loader pela regra dele (VCREPORTS_SECRETS_FILE)")
checar(cofre._cache_do_loader(m) == {"CORREIOS_USUARIO": "conta-do-cofre", "CORREIOS_CHAVE": "chave-do-cofre-que-nao-pode-sair",
                                      "ORACLE_EBS_USER": "usuario-de-teste", "ORACLE_EBS_PASS": "senha-do-cofre-que-nao-pode-sair"},
       "o cache do loader real tem exatamente o que está no arquivo, aspas tratadas")
checar(cofre.obter("ORACLE_EBS_PASS") == "senha-do-cofre-que-nao-pode-sair", "obter() entrega a senha do EBS vinda do cofre — pelo loader")
checar(cofre.fonte("ORACLE_EBS_PASS") == "cofre corporativo", "e diz que veio do cofre corporativo")
ok_r, det_r = cofre.diagnostico_corporativo("ORACLE_EBS_PASS")
checar(ok_r is True and "leu 4 chave(s)" in det_r and "está entre elas" in det_r,
       "o diagnóstico diz quantas chaves o loader leu, de qual arquivo, e que a chave está lá")
ok_r2, det_r2 = cofre.diagnostico_corporativo("MDM_USUARIO")
checar(ok_r2 is True and "NÃO está entre elas" in det_r2,
       "chave ausente: o cofre está de pé, mas ela não está provisionada — dito assim")

# O falso positivo original, morto na raiz: chave SÓ no ambiente não é do cofre.
os.environ["MDM_USUARIO"] = "so-no-ambiente"
checar(cofre._corporativo("MDM_USUARIO") == "", "_corporativo() não devolve o que só está no ambiente")
checar(cofre.obter("MDM_USUARIO") == "so-no-ambiente" and cofre.fonte("MDM_USUARIO") == "ambiente",
       "obter() ainda a entrega — mas pela fonte certa: ambiente")
d30 = cliente.get("/api/cofre/diagnostico").json()
inv30 = d30["inventario"]
checar(inv30["arquivo_do_loader"] == str(COFRE_REAL) and inv30["arquivo_por"] == "VCREPORTS_SECRETS_FILE",
       "a tela mostra o arquivo lido e que veio da variável")
checar(inv30["nomes"] == ["CORREIOS_CHAVE", "CORREIOS_USUARIO", "ORACLE_EBS_PASS", "ORACLE_EBS_USER"],
       "e lista os nomes que o loader real carregou")
ebs30 = {c["chave"]: c for g in d30["grupos"] if g["nome"] == "Base EBS (Oracle)" for c in g["chaves"]}
checar(ebs30["ORACLE_EBS_PASS"]["fonte"] == "cofre corporativo (loader)" and ebs30["ORACLE_EBS_PASS"]["resolvida"],
       "na tela, ORACLE_EBS_PASS aparece como do cofre, resolvida")
mdm30 = {c["chave"]: c for g in d30["grupos"] if g["nome"] == "MDM" for c in g["chaves"]}
checar(mdm30["MDM_USUARIO"]["fonte"] == "ambiente" and mdm30["MDM_USUARIO"]["no_corporativo"] is False,
       "e MDM_USUARIO, que só está no ambiente, aparece como ambiente — o falso positivo acabou")
checar("senha-do-cofre-que-nao-pode-sair" not in cliente.get("/api/cofre/diagnostico").text,
       "a senha lida do cofre não sai na resposta")
r_ebs = cliente.get("/api/ebs-oracle/situacao").json()
ch_ebs = {c["chave"]: c for c in r_ebs["chaves"]}
checar(ch_ebs["ORACLE_EBS_PASS"]["no_corporativo"] is True and r_ebs["cofre_corporativo"] is True,
       "a tela Base EBS também: senha do cofre, cofre disponível")

# Cache fixo por processo: arquivo some -> continua lendo do cache; Reler -> vê a realidade.
COFRE_REAL.unlink()
checar(cofre.obter("ORACLE_EBS_PASS") == "senha-do-cofre-que-nao-pode-sair",
       "com o arquivo removido, o loader ainda responde pelo cache (é assim que ele é)")
r31 = cliente.post("/api/cofre/reler").json()
checar(r31["ok"] is False and "continua sem conseguir ler" in r31["detalhe"], "Reler: sem o arquivo, diz que não leu")
ok_r3, det_r3 = cofre.diagnostico_corporativo("ORACLE_EBS_PASS")
checar(ok_r3 is False and "não leu" in det_r3 and "Reler ou" in det_r3,
       "e o diagnóstico explica o cache fixo e o que fazer")
COFRE_REAL.write_text("ORACLE_EBS_PASS=nova-senha-que-nao-pode-sair\n", encoding="utf-8")
r32 = cliente.post("/api/cofre/reler").json()
checar(r32["ok"] is True and r32["chaves"] == 1, "arquivo de volta + Reler: o loader recarrega sem reiniciar")
checar(cofre.obter("ORACLE_EBS_PASS") == "nova-senha-que-nao-pode-sair", "e o portal já enxerga o valor novo")

os.environ.pop("MDM_USUARIO", None); os.environ.pop("VCREPORTS_SECRETS_FILE", None)
cofre._modulo, cofre._modulo_via, cofre._arquivo_cache = None, "", None

print("\n[3o] Falso positivo: o loader devolvendo a variável de ambiente")
# O loader do time resolve cofre -> os.environ -> default. Com a variável no
# arquivo de ambiente, s() responde mesmo com o cofre inacessível. Dar isso
# como "veio do cofre" apaga justamente o problema que se quer enxergar.
mod4 = types.ModuleType("vcreports_secrets")
# Cofre inacessível: o loader só sabe repetir o que está no ambiente.
mod4.s = lambda n: os.environ.get(n, "")
cofre._modulo, cofre._modulo_via = mod4, "import direto (loader que só ecoa o ambiente)"

# A prova do topo usa CORREIOS_USUARIO, e no servidor ela está no ambiente —
# que é exatamente a condição que produzia o falso positivo.
os.environ["CORREIOS_USUARIO"] = "conta-de-servico"
os.environ["ORACLE_EBS_USER"] = "usuario-de-teste"
d22 = cliente.get("/api/cofre/diagnostico").json()
ebs = {c["chave"]: c for g in d22["grupos"] if g["nome"] == "Base EBS (Oracle)"
       for c in g["chaves"]}["ORACLE_EBS_USER"]
checar(ebs["resolvida"] is True, "a chave resolve, e isso continua sendo verdade")
checar(ebs["no_corporativo"] is False,
       "mas NÃO é contabilizada como vinda do cofre")
checar(ebs["indistinguivel"] is True, "e é marcada como indistinguível")
checar(ebs["fonte"] == "ambiente (pelo loader)", "a fonte diz de onde ela realmente veio")
checar(ebs["sombreado"] is False,
       "e não inventa duas fontes para o que é uma só")

checar(d22["corporativo_ok"] is False,
       "a faixa do topo também deixa de dizer que o cofre está disponível")
checar("idêntico ao da variável de ambiente" in d22["corporativo_detalhe"],
       "explicando por quê")
checar("COFRE_CHAVE_TESTE" in d22["corporativo_detalhe"],
       "e como provar de verdade, com uma chave fora do ambiente")

# Cofre de verdade: valor diferente do ambiente, aí sim é do cofre.
mod5 = types.ModuleType("vcreports_secrets")
mod5.s = lambda n: "valor-que-so-o-cofre-tem" if n == "ORACLE_EBS_USER" else ""
cofre._modulo, cofre._modulo_via = mod5, "import direto (cofre de verdade)"
d23 = cliente.get("/api/cofre/diagnostico").json()
ebs2 = {c["chave"]: c for g in d23["grupos"] if g["nome"] == "Base EBS (Oracle)"
        for c in g["chaves"]}["ORACLE_EBS_USER"]
checar(ebs2["no_corporativo"] is True and ebs2["indistinguivel"] is False,
       "valor diferente do ambiente é reconhecido como do cofre")
checar(ebs2["divergente"] is True,
       "e a divergência entre cofre e ambiente aparece")

os.environ.pop("ORACLE_EBS_USER", None)
os.environ.pop("CORREIOS_USUARIO", None)
cofre._modulo, cofre._modulo_via = None, ""

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
js = (RAIZ / "modulos/parametros.js").read_text(encoding="utf-8")
checar("['cofre',           'Cofre de segredos']" in js, "a aba está na lista")
checar("cofre:          renderCofre" in js, "e ligada ao renderizador")
checar("'cofre', 'ebs-oracle'" in js, "é aba de admin")
checar("/cofre/testar-correios" in js, "a tela chama o teste dos Correios")
checar("em ' + (k.fontes_com_valor || []).length + ' fontes" in js,
       "a tela mostra em quantas fontes a chave está")
checar("errosHtml" in js, "a tela mostra qual parte do diagnóstico falhou")
checar("remedioHtml" not in js and "pasta_acessivel" not in js,
       "e não fala mais em permissão de arquivo — o cofre é por referência")
checar("/cofre/sondar-varios" in js and "cf-nomes" in js,
       "a tela tem a caixa de sondagem por nome")
checar("/cofre/testar-php" in js and "cf-php-loader" in js,
       "e o teste do PHP, feito pelo serviço")
checar("prefixoHtml" in js and "não chegou ao processo" in js,
       "a tela mostra o prefixo em uso e a variável que o systemd não carregou")
checar("pode ser só o ambiente" in js,
       "a tela avisa quando não dá para distinguir cofre de ambiente")
checar("ambienteHtml" in js and "marcador do cofre" in js,
       "a tela mostra o arquivo de ambiente e separa marcador de valor direto")
checar("/cofre/tudo" in js and "cf-filtro" in js,
       "a tela tem o botão Ver tudo, com filtro por nome")
checar("valores diferentes" in js, "a tela avisa quando os cofres discordam")
checar("inventarioHtml" in js and "alternativasHtml" in js,
       "a tela mostra de onde o cofre lê e os apelidos sondados")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Diagnóstico do cofre íntegro.")
