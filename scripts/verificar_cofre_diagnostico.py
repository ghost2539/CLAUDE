#!/usr/bin/env python3
"""Verificação da tela Cofre de segredos.

    python3 scripts/verificar_cofre_diagnostico.py

Sobe o portal em memória, entra como admin e chama `/api/cofre/*`. O que
mais importa aqui não é o cofre responder — no servidor de teste ele nem
existe — e sim que a tela **diga a verdade sobre de onde veio cada chave** e
que **nenhum valor de segredo saia na resposta**.

O cofre corporativo é simulado: `VCREPORTS_SECRETS_FILE` aponta para um
arquivo desta verificação, e o cofre local mora numa pasta temporária. Nada
aqui toca no cofre de ninguém.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")
# Cofre corporativo simulado: um arquivo nosso, no formato do oficial.
CORPORATIVO = _TMP / "secrets.env"
os.environ["VCREPORTS_SECRETS_FILE"] = str(CORPORATIVO)
os.environ["VCREPORTS_SECRETS_MODULE"] = str(_TMP / "modulo_que_nao_existe.py")

# Valores que NÃO podem sair em resposta nenhuma.
SENHA_CORP = "senha-do-corporativo-que-nao-pode-sair"
SENHA_LOCAL = "senha-do-local-que-nao-pode-sair"
SENHA_AMB = "senha-do-ambiente-que-nao-pode-sair"

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def item(resposta: dict, chave: str) -> dict:
    """A linha de uma chave dentro de /diagnostico ou /sondar-varios."""
    for i in resposta.get("itens", []):
        if i["chave"] == chave:
            return i
    for g in resposta.get("grupos", []):
        for i in g.get("chaves", []):
            if i["chave"] == chave:
                return i
    return {}


from core import cofre, security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

cliente = TestClient(main.app)
_, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


print("\n[1] Sem cofre corporativo: a tela diz por quê, e não quebra")
d = cliente.get("/api/cofre/diagnostico").json()
checar(d["corporativo_ok"] is False, "sem arquivo nem módulo, o cofre corporativo não alcança")
checar("não encontrados" in d["corporativo_detalhe"] or "não" in d["corporativo_detalhe"],
       "e o motivo vem escrito, não só um 'falhou'")
checar(d["erros"] == {}, "nenhuma parte do diagnóstico estourou")
checar(d["inventario"]["acesso"]["caminho"] == str(CORPORATIVO),
       "o inventário aponta o arquivo do cofre que este processo procura")
checar(d["inventario"]["ligado"] is True and d["inventario"]["sabe_listar"] is False,
       "cofre ligado, mas sem lista de nomes — é o que o arquivo ausente significa")
checar("prefixo" in d and "ambiente_do_servico" in d,
       "prefixo em uso e arquivo de ambiente entram no mesmo retrato")


print("\n[2] Chave só no ambiente não é dada como do cofre")
os.environ["VERIFICACAO_USUARIO"] = "usuario-de-teste"
os.environ["VERIFICACAO_SENHA"] = SENHA_AMB
d = cliente.post("/api/cofre/sondar-varios",
                 json={"nomes": "VERIFICACAO_USUARIO VERIFICACAO_SENHA"}).json()
u = item(d, "VERIFICACAO_USUARIO")
checar(u["fonte"] == "ambiente" and u["no_corporativo"] is False,
       "variável de ambiente aparece como 'ambiente'")
checar(u.get("valor") == "usuario-de-teste",
       "nome que não denuncia segredo mostra o valor — é o que deixa conferir")
sen = item(d, "VERIFICACAO_SENHA")
checar("valor" not in sen and sen["tamanho"] == len(SENHA_AMB),
       "nome com SENHA mostra só o tamanho")
checar(SENHA_AMB not in json.dumps(d), "e o valor não aparece em lugar nenhum da resposta")


print("\n[3] Cofre local: entra como 'cofre local', e sombreia o ambiente")
cofre.definir("VERIFICACAO_SENHA", SENHA_LOCAL)
d = cliente.post("/api/cofre/sondar-varios", json={"nomes": "VERIFICACAO_SENHA"}).json()
sen = item(d, "VERIFICACAO_SENHA")
checar(sen["fonte"] == "cofre local" and sen["no_local"] is True,
       "com valor no cofre local, é ele que vence")
checar(sen["sombreado"] is True and sen["fontes_com_valor"] == ["cofre local", "ambiente"],
       "a tela avisa que a chave existe em mais de uma fonte")
checar(sen["divergente"] is True, "e que os valores divergem — a falha silenciosa clássica")
checar(SENHA_LOCAL not in json.dumps(d) and SENHA_AMB not in json.dumps(d),
       "nenhum dos dois valores sai na resposta")


print("\n[4] Cofre corporativo legível: a lista de nomes é a verdade")
CORPORATIVO.write_text(
    "CORREIOS_USUARIO=usuario-correios\n"
    f'CORREIOS_CHAVE="{SENHA_CORP}"\n'
    "# comentário e linha vazia são ignorados\n"
    "\n"
    f"VERIFICACAO_SENHA={SENHA_CORP}\n", encoding="utf-8")
cofre._arquivo_cache = None   # o cache é do processo; a verificação recarrega
d = cliente.get("/api/cofre/diagnostico").json()
checar(d["corporativo_ok"] is True and "arquivo lido direto" in d["corporativo_detalhe"],
       "com o arquivo legível, o cofre corporativo responde — e diz como")
inv = d["inventario"]
checar(inv["sabe_listar"] is True and inv["nomes"] ==
       ["CORREIOS_CHAVE", "CORREIOS_USUARIO", "VERIFICACAO_SENHA"],
       "o inventário lista os nomes do cofre, em ordem")
checar(inv["acesso"]["existe"] and inv["acesso"]["legivel"],
       "e mostra que este processo consegue ler o arquivo")
cu = item(d, "CORREIOS_USUARIO")
checar(cu["fonte"] == "cofre corporativo" and cu["no_corporativo"] is True,
       "chave do cofre aparece como do cofre")
cc = item(d, "CORREIOS_CHAVE")
checar(cc["resolvida"] is True and "valor" not in cc,
       "a chave dos Correios resolve, e o valor não sai")
checar(SENHA_CORP not in json.dumps(d), "nenhum segredo do cofre aparece no diagnóstico inteiro")


print("\n[5] Precedência: corporativo ganha do local, que ganha do ambiente")
d = cliente.post("/api/cofre/sondar-varios", json={"nomes": "VERIFICACAO_SENHA"}).json()
sen = item(d, "VERIFICACAO_SENHA")
checar(sen["fonte"] == "cofre corporativo", "com as três fontes, vence o corporativo")
checar(sen["fontes_com_valor"] == ["cofre corporativo", "cofre local", "ambiente"],
       "e a tela mostra as três, para ninguém conferir a fonte errada")
checar(sen["indistinguivel"] is False,
       "com a lista de nomes em mãos, não há dúvida sobre a origem")


print("\n[6] Sem a lista de nomes, a tela admite a dúvida em vez de escolher")
# O caso real: o serviço não lê o arquivo do cofre, mas o módulo do time
# responde — e o `s()` dele cai para os.environ quando a chave não está lá.
# Com o mesmo valor nos dois, não existe como saber de qual veio, e a tela
# tem de dizer isso em vez de creditar o cofre.
os.environ["INDISTINTA_SENHA"] = "mesmo-valor-nos-dois"
CORPORATIVO.write_text("INDISTINTA_SENHA=mesmo-valor-nos-dois\n", encoding="utf-8")
cofre._arquivo_cache = None
d = cliente.post("/api/cofre/sondar-varios", json={"nomes": "INDISTINTA_SENHA"}).json()
checar(item(d, "INDISTINTA_SENHA")["fonte"] == "cofre corporativo",
       "com o arquivo legível, a lista de nomes resolve a dúvida")

LOADER = Path(os.environ["VCREPORTS_SECRETS_MODULE"])
LOADER.write_text("import os\n\n\ndef s(chave, padrao=''):\n"
                  "    return os.environ.get(chave, padrao)\n", encoding="utf-8")
CORPORATIVO.unlink()
cofre._arquivo_cache = None
cofre._modulo = None
d = cliente.post("/api/cofre/sondar-varios", json={"nomes": "INDISTINTA_SENHA"}).json()
i = item(d, "INDISTINTA_SENHA")
checar(i["indistinguivel"] is True and i["fonte"] == "ambiente (pelo cofre)",
       "sem a lista, valor igual nos dois é marcado como indistinguível")
checar(i["no_corporativo"] is False,
       "e a chave NÃO é creditada ao cofre — creditar seria esconder o problema")
d = cliente.get("/api/cofre/diagnostico").json()
checar(d["corporativo_ok"] is True and "função s()" in d["corporativo_detalhe"],
       "o diagnóstico reconhece o módulo do time e a função que ele expõe")
LOADER.unlink()
cofre._modulo = None
cofre._arquivo_cache = None


print("\n[7] /tudo: nomes das três fontes, sem valor de segredo")
CORPORATIVO.write_text(f'CORREIOS_CHAVE="{SENHA_CORP}"\n', encoding="utf-8")
cofre._arquivo_cache = None
d = cliente.get("/api/cofre/tudo").json()
nomes = {i["chave"] for i in d["itens"]}
checar({"CORREIOS_CHAVE", "VERIFICACAO_SENHA", "VERIFICACAO_USUARIO"} <= nomes,
       "lista chaves do cofre corporativo, do local e do ambiente")
checar(d["por_fonte"]["cofre corporativo"] >= 1 and d["por_fonte"]["cofre local"] >= 1,
       "e conta quantas vêm de cada fonte — o número que diz se o cofre respondeu")
bruto = json.dumps(d)
for valor, onde in ((SENHA_CORP, "corporativo"), (SENHA_LOCAL, "local"), (SENHA_AMB, "ambiente")):
    checar(valor not in bruto, f"nenhum segredo do {onde} vaza em /tudo")


print("\n[8] Uma chave avulsa")
r = cliente.get("/api/cofre/sondar/CORREIOS_CHAVE")
checar(r.status_code == 200 and r.json()["chave"] == "CORREIOS_CHAVE"
       and "valor" not in r.json(), "sondar uma chave responde sem o valor")
r = cliente.get("/api/cofre/sondar/nome invalido!")
checar(r.status_code in (404, 422), f"nome fora do formato é recusado ({r.status_code})")


print("\n[9] Correios: o teste diz em que etapa parou")
CORPORATIVO.unlink()
cofre._arquivo_cache = None
for k in ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES"):
    cofre.remover(k)
    os.environ.pop(k, None)
d = cliente.post("/api/cofre/testar-correios").json()
checar(d["ok"] is False and d["etapa"] == "cofre",
       "sem credencial, para na etapa 'cofre' — não sai para a rede")
checar("CORREIOS_USUARIO" in d["detalhe"], "e diz qual chave falta")


print("\n[10] Permissão e sigilo")
anon = TestClient(main.app)
for metodo, rota in (("get", "/api/cofre/diagnostico"), ("get", "/api/cofre/tudo"),
                     ("get", "/api/cofre/sondar/CORREIOS_USUARIO"),
                     ("post", "/api/cofre/testar-correios"),
                     ("post", "/api/cofre/sondar-varios")):
    r = getattr(anon, metodo)(rota, **({"json": {"nomes": "X"}} if metodo == "post" else {}))
    checar(r.status_code in (401, 403), f"{rota} exige sessão ({r.status_code})")

comum = TestClient(main.app)
_, ck = security.create_session(
    {"username": "sem-admin", "is_admin": False, "permission_map": {}})
comum.cookies.set("spare_session", ck)
checar(comum.get("/api/cofre/diagnostico").status_code == 403, "sem admin, 403")

fonte = (RAIZ / "routers" / "cofre.py").read_text(encoding="utf-8")
checar("ORACLE" not in fonte.upper().replace("ORACLE.PHP", ""),
       "o router não carrega nome de credencial de banco Oracle")
checar("testar-php" not in fonte and "subprocess" not in fonte,
       "e não executa arquivo nenhum a pedido da tela")

js = (RAIZ / "static/modules/parametros.js").read_text(encoding="utf-8")
for trecho, desc in (("/cofre/diagnostico", "carrega o diagnóstico"),
                     ("/cofre/sondar-varios", "sonda a lista de nomes"),
                     ("/cofre/testar-correios", "tem o botão de testar os Correios")):
    checar(trecho in js, f"parametros.js {desc}")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Tela do cofre íntegra: diz de onde veio cada chave, e nenhum valor sai.")
