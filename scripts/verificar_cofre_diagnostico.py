#!/usr/bin/env python3
"""Verificação do diagnóstico do cofre visto de dentro do serviço.

    python3 scripts/verificar_cofre_diagnostico.py

Sobe o portal em memória, entra como admin e chama `/api/cofre/*`. O que
mais importa aqui não é o cofre responder — no servidor de teste ele nem
existe — e sim que a tela diga a verdade e que **nenhum valor de segredo
saia na resposta**.

O que saiu deste script, e por quê:

*   O **cofre corporativo** (loader `vcreports_secrets`, comando externo,
    ponte PHP). `core/cofre.py` não implementa mais nada disso: as funções
    viraram stubs inertes (`USAR_CORPORATIVO = False`), porque o loader era
    inalcançável neste servidor e só enchia o log. Sete seções deste script
    montavam cofres de mentira para exercitar esse caminho; elas não
    testavam mais nada — só os stubs.
*   O **rastreio de origem** por chave (de qual fonte veio, em quantas
    fontes está, se uma sombreia a outra) e o **tamanho** do valor. A aba
    passou a informar apenas se a chave foi LOCALIZADA (pedido de 18/09).
    O contrato novo é conferido inteiro em
    `scripts/verificar_cofre_sem_valor.py`, inclusive com contraprova.

Nenhuma afirmação foi afrouxada para passar: o que saiu, saiu porque o
assunto deixou de existir no código.
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
import routers.cofre as rc  # noqa: E402

app = main.app
cliente = TestClient(app)
sid, cookie = security.create_session(
    {"username": "verificador", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)

print("[1] O diagnóstico responde e diz quem o serviço é")
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

print("\n[2] A resposta diz se achou, e mais nada")
# O JSON vai inteiro para o navegador: quem abre as ferramentas de
# desenvolvedor lê o que a tela escolheu não desenhar. Por isso o que some,
# some AQUI — e some para TODA chave, segredo ou não: `CORREIOS_USUARIO` e
# `ORACLE_EBS_USER` saíam por extenso porque "USUARIO"/"USER" não constam
# da lista de marcas de segredo, e usuário de banco é dado de acesso.
bruto = r.text
checar(SEGREDO not in bruto, "a chave dos Correios não aparece no JSON")
correios = next((g for g in d.get("grupos", []) if g["nome"] == "Correios"), {})
por_chave = {c["chave"]: c for c in correios.get("chaves", [])}
checar(por_chave.get("CORREIOS_CHAVE", {}).get("resolvida") is True,
       "mesmo assim, a chave consta como resolvida")
checar(set(por_chave.get("CORREIOS_CHAVE", {})) == {"chave", "resolvida"},
       "e o item traz só o nome e se foi localizada")
checar("tamanho" not in por_chave.get("CORREIOS_CHAVE", {}),
       "sem o tamanho — ele estreita o segredo para quem for adivinhar")
checar("valor" not in por_chave.get("CORREIOS_USUARIO", {}),
       "o que não é segredo pelo nome também não traz valor")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("resolvida") is True,
       "apenas que foi localizada")
checar("usuario-de-teste" not in bruto,
       "e o usuário do cofre não sai na resposta — é dado de acesso")
checar(por_chave.get("CORREIOS_CARTOES", {}).get("resolvida") is False,
       "chave ausente aparece como não localizada, sem inventar valor")

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
checar(all(set(c) == {"chave", "resolvida"} for g in alt for c in g["chaves"]),
       "e os apelidos seguem a mesma regra: nome e se achou, só")
checar(all(not c.get("resolvida") for g in alt for c in g["chaves"]),
       "sem cofre, nenhum apelido resolve — e nenhum valor é inventado")

print("\n[3c] O cofre corporativo saiu do código, e a tela diz isso")
# Não é uma falha a diagnosticar: foi decisão. A tela precisa dizer o motivo,
# senão vira caça a um "Permission denied" que não existe mais.
checar(cofre.USAR_CORPORATIVO is False, "o core não procura mais o loader do time")
checar(cofre._corporativo("QUALQUER_COISA") == "" and cofre._resolver_modulo() is None,
       "e os stubs não importam nada nem leem arquivo")
ok_corp, det_corp = cofre.diagnostico_corporativo("CORREIOS_CHAVE")
checar(ok_corp is False and "removido" in det_corp,
       "o diagnóstico explica que foi removido, em vez de dizer só 'indisponível'")

print("\n[3h] Ver tudo: a lista completa, sem publicar segredo")
os.environ["CORREIOS_CARTOES"] = "cartao-do-ambiente"
r11 = cliente.get("/api/cofre/tudo")
checar(r11.status_code == 200, f"HTTP 200 ({r11.status_code})")
d11 = r11.json()
chaves11 = {i["chave"]: i for i in d11.get("itens", [])}
checar(d11["total"] == len(d11["itens"]) and d11["total"] > 10,
       "traz a lista das chaves que o portal procura")
checar(chaves11.get("CORREIOS_CHAVE", {}).get("resolvida") is True,
       "acha o que está no cofre local")
checar(chaves11.get("CORREIOS_CARTOES", {}).get("resolvida") is True,
       "e o que está só no ambiente")
checar(d11["resolvidas"] == sum(1 for i in d11["itens"] if i["resolvida"]),
       "e conta quantas foram localizadas")

# O ambiente do processo carrega o que assina a sessão e a URL do banco.
# Antes a lista era montada a partir de `os.environ`, então tudo que o
# systemd injeta entrava nela e o filtro por nome era a única defesa. Agora a
# lista é só o que o portal procura, e um nome que ele não procura não entra.
checar(SEGREDO not in r11.text, "a chave dos Correios não aparece")
checar(os.environ["PORTAL_SESSION_SECRET"] not in r11.text,
       "o segredo que assina a sessão não aparece")
checar("PORTAL_SESSION_SECRET" not in chaves11 and "DATABASE_URL" not in chaves11,
       "e a lista não é mais um despejo do ambiente do processo")
checar(all(set(i) == {"chave", "resolvida"} for i in d11["itens"]),
       "cada item da lista traz nome e se foi localizada, só")
for nome in ("MINHA_SENHA", "API_TOKEN", "X_PWD", "SERVICO_URL", "BASE_DSN",
             "CRED_AUTH", "COOKIE_X", "CHAVE_Y"):
    checar(rc._e_segredo(nome), f"{nome} continua sendo tratado como segredo")
os.environ.pop("CORREIOS_CARTOES", None)

anon2 = TestClient(app)
checar(anon2.get("/api/cofre/tudo").status_code in (401, 403), "sem sessão, não responde")

print("\n[3i] Uma parte quebrada não derruba a tela inteira")
# A tela existe para explicar falhas. Se ela mesma devolver 500, não sobra
# nada para diagnosticar — então cada parte responde por si.
from unittest.mock import patch as _patch  # noqa: E402

with _patch.object(rc, "_inventario_corporativo",
                   side_effect=RuntimeError("cofre explodiu")):
    r12 = cliente.get("/api/cofre/diagnostico")
checar(r12.status_code == 200, f"segue respondendo 200 ({r12.status_code})")
d12 = r12.json()
checar(d12.get("usuario_do_servico"), "o resto do diagnóstico continua inteiro")
checar("inventário do cofre" in (d12.get("erros") or {}),
       "e a parte que falhou aparece nomeada")
checar("cofre explodiu" in d12["erros"]["inventário do cofre"],
       "com o erro de verdade, não um texto genérico")

with _patch.object(rc, "_sondar", side_effect=RuntimeError("sonda explodiu")):
    r13 = cliente.get("/api/cofre/diagnostico")
checar(r13.status_code == 200, "idem quando a sondagem de chaves falha")
checar("chaves por assunto" in (r13.json().get("erros") or {}),
       "nomeando a seção certa")

with _patch.object(rc, "_inventario_corporativo",
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
checar("cartao-do-ambiente" not in r15.text,
       "nem o valor que veio do ambiente — a sondagem é por nome livre")
checar(all(set(i) == {"chave", "resolvida"} for i in d15["itens"]),
       "e a sondagem livre responde no mesmo formato enxuto")
checar(cliente.post("/api/cofre/sondar-varios", json={"nomes": "   "}).status_code == 422,
       "texto vazio é recusado")
checar(cliente.post("/api/cofre/sondar-varios",
                    json={"nomes": "a;b" * 500}).status_code == 200,
       "lista enorme não derruba nada")
checar(anon2.post("/api/cofre/sondar-varios", json={"nomes": "X"}).status_code in (401, 403),
       "e exige sessão")
os.environ.pop("CORREIOS_CARTOES", None)

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
checar("conta-de-servico" not in r21.text,
       "nem o usuário, que também é dado de acesso")

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

print("\n[4] Sondagem avulsa")
r2 = cliente.get("/api/cofre/sondar/CORREIOS_CHAVE")
checar(r2.status_code == 200, f"HTTP 200 ({r2.status_code})")
checar(SEGREDO not in r2.text, "também não vaza o valor")
r3 = cliente.get("/api/cofre/sondar/nao;existe")
checar(r3.status_code == 422, f"nome inválido é recusado ({r3.status_code})")
r4 = cliente.get("/api/cofre/sondar/correios_usuario")
checar(r4.status_code == 200 and r4.json().get("chave") == "CORREIOS_USUARIO",
       "o nome é normalizado para maiúsculas")
checar("usuario-de-teste" not in r4.text,
       "e nem pedindo a chave pelo nome se obtém o valor")

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
js_abas = (RAIZ / "modulos/parametros.js").read_text(encoding="utf-8")
checar("['cofre',           'Cofre de segredos']" in js_abas, "a aba está na lista")
checar("'cofre', 'base-ebs'" in js_abas, "é aba de admin")

js = (RAIZ / "modulos/parametros_admin.js").read_text(encoding="utf-8")
checar("'cofre':          renderCofre" in js, "e ligada ao renderizador")
checar("/cofre/diagnostico" in js, "a tela chama o diagnóstico")
checar("/cofre/testar-correios" in js, "e o teste dos Correios")
checar("/cofre/sondar-varios" in js and "cf-nomes" in js,
       "a tela tem a caixa de sondagem por nome")
checar("Não consegui checar" in js,
       "a tela mostra qual parte do diagnóstico falhou")
checar("remedioHtml" not in js and "pasta_acessivel" not in js,
       "e não fala mais em permissão de arquivo do cofre local")
checar("inv.sabe_listar" in js and "inv.nomes" in js,
       "a tela mostra de onde o cofre lê")

# O desenho não pode reintroduzir o que a API parou de mandar: se a tela
# ainda pedisse esses campos, a leitura seria `undefined` e a coluna viria
# vazia — parecendo defeito, e não decisão. Isto vale para as DUAS abas que
# listam chaves: Cofre de segredos e Base EBS.
for morto in ("fontes_com_valor", "sombreado", "no_corporativo", "divergente",
              "no_local", "k.valor", "k.tamanho", "'fonte'", "linha.valor"):
    checar(morto not in js, f"e não lê mais {morto}, que a API não manda")
checar(js.count("label: 'Localizada'") == 2,
       "as duas abas dizem 'Localizada' — é a única coluna que sobrou")

# Painéis que a API entrega e esta tela ainda não desenha. Não é defeito do
# que se mexeu agora: `parametros_admin.js` nunca os teve (vieram de
# `parametros.js`, na tela antiga). Fica anotado para não passar por
# esquecimento na próxima leitura — e para ninguém achar que a API os perdeu.
for campo, painel in (("prefixo", "prefixo em uso"),
                      ("ambiente_do_servico", "arquivo de ambiente do serviço"),
                      ("alternativas", "apelidos sondados do EBS")):
    checar(campo in (d or {}),
           f"a API ainda entrega {painel} — falta a tela desenhar (pendência aberta)")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Diagnóstico do cofre íntegro.")
