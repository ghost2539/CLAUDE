#!/usr/bin/env python3
"""Verificação da leitura da base do EBS.

    python3 scripts/verificar_ebs_oracle.py

Não há banco Oracle aqui, e não é disso que se trata. O que se prova é o
contorno da conexão, que é onde mora o risco:

  • a credencial vem do ambiente do serviço e, depois, do COFRE — nessa
    ordem, e os dois caminhos são o mesmo para a tela e para a conexão;
  • endereço, instância, usuário e senha NÃO aparecem em resposta nenhuma —
    nem quando o próprio erro do driver os traz dentro;
  • o TEXTO do SQL também não sai: ele é o mapa da base de outra área;
  • a tela executa consulta NOMEADA, com bind variables, e o SQL digitado
    passa pela contenção da seção [4];
  • a sessão é só-leitura, com teto de linhas e de tempo;
  • sem credencial, o portal diz o que falta e não tenta conectar.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

# Valores de mentira, mas tratados como se fossem reais: se qualquer um
# aparecer numa resposta, a verificação falha.
DSN = "servidor-de-mentira:1521/INSTANCIA_DE_MENTIRA"
USUARIO = "usuario-de-mentira"
SENHA = "senha-que-nao-pode-sair"

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def sem_segredo(texto: str, onde: str) -> None:
    """Nenhum dado de acesso pode estar no texto — nem em pedaços."""
    for valor, rotulo in ((SENHA, "senha"), (USUARIO, "usuário"),
                          (DSN, "DSN"), ("servidor-de-mentira", "host"),
                          ("INSTANCIA_DE_MENTIRA", "instância")):
        checar(valor not in texto, f"{onde}: não vaza o {rotulo}")


# Marcas que só existem dentro do SQL das consultas nomeadas. O SQL descreve
# a base de PRODUÇÃO de outra área — esquema, tabela, coluna — e devolvê-lo
# para a tela é publicar esse mapa em algo que qualquer um lê na aba de rede
# do navegador, salva em HAR e cola em chamado.
MARCAS_DE_SQL = (
    ("SELECT", "o verbo SELECT"),
    ("FROM", "a cláusula FROM"),
    ("APPS.", "o esquema APPS"),
    ("PO_HEADERS_ALL", "a tabela PO_HEADERS_ALL"),
    ("PA_PROJECTS_ALL", "a tabela PA_PROJECTS_ALL"),
    ("MTL_SYSTEM_ITEMS_B", "a tabela MTL_SYSTEM_ITEMS_B"),
    ("AP_SUPPLIERS", "a tabela AP_SUPPLIERS"),
)


def sem_sql(texto: str, onde: str) -> None:
    """Nenhum texto de SQL no corpo da resposta — nem o verbo, nem as tabelas.

    Para executar, a tela precisa do NOME da consulta e dos parâmetros que
    ela pede. Quem precisa ler o SQL o lê em integracoes/ebs_oracle.py, que
    é versionado e revisável; a API não é lugar de publicá-lo.
    """
    alto = texto.upper()
    for agulha, rotulo in MARCAS_DE_SQL:
        checar(agulha not in alto, f"{onde}: não traz {rotulo}")


from core import cofre, security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

cliente = TestClient(main.app)
_, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


OBRIGATORIAS = ("ORACLE_EBS_DSN", "ORACLE_EBS_USER", "ORACLE_EBS_PASS")

print("\n[1] Sem credencial: diz o que falta e NÃO tenta conectar")
d = cliente.get("/api/ebs-oracle/situacao").json()
nomes = {c["chave"]: c for c in d["chaves"]}
checar(set(OBRIGATORIAS) <= set(nomes), "as três chaves obrigatórias aparecem na tela")
checar(all(not nomes[k]["resolvida"] for k in OBRIGATORIAS),
       "nenhuma resolvida, porque não há valor em lugar nenhum")
checar(all(nomes[k]["situacao"] == "ausente" for k in OBRIGATORIAS),
       "a situação é 'ausente', não 'padrão do código'")
# A regressão que motivou esta verificação: o expurgo do histórico deixou
# marcadores ("BANCO_REMOVIDO:1521/...") como PADRÃO de usuário e endereço.
# A tela os exibia como valor e o portal tentava conectar num host inventado.
checar(all("valor" not in nomes[k] for k in OBRIGATORIAS),
       "nenhum valor de usuário ou endereço é exibido")
checar(not any("REMOVID" in json.dumps(c) for c in d["chaves"]),
       "nenhum marcador de expurgo sobrou fazendo as vezes de valor")
r = cliente.post("/api/ebs-oracle/testar")
checar(r.status_code == 503, f"testar sem credencial: 503, não 502 ({r.status_code})")
detalhe = r.json().get("detail", "")
checar(all(k in detalhe for k in OBRIGATORIAS), "e a mensagem diz QUAIS chaves faltam")
checar("Parâmetros" in detalhe or "environment" in detalhe,
       "e onde gravá-las")


print("\n[2] A credencial vem do cofre, e o cofre é a única porta")
fonte_mod = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
# Aqui o ambiente do serviço vem primeiro (é como a conexão que está no ar
# lê, igual aos Correios) e o cofre é a segunda parada. O que não pode é a
# tela procurar num lugar e a conexão em outro: chave gravada pela tela
# apareceria "resolvida" e a conexão falharia assim mesmo.
checar("from core.cofre import obter" in fonte_mod,
       "a camada de acesso também procura no cofre")
checar(fonte_mod.index("os.environ.get(nome)") < fonte_mod.index("from core.cofre import obter"),
       "e o ambiente do serviço continua sendo consultado primeiro")
# O original trazia DSN e usuário escritos como padrão. Isso é dado de
# acesso: some do código, e no repositório fica só o NOME da chave.
checar(not re.search(r"(dsn|user)\"?\s*[:=]\s*\"[^\"]*:1521", fonte_mod, re.I),
       "nenhum endereço de banco escrito como padrão no código")
checar("EbsOracleSemCredencial" in fonte_mod,
       "faltar chave é erro nomeado, não tentativa de conectar às cegas")
for arquivo in ("integracoes/ebs_oracle.py", "routers/ebs_oracle.py",
                "docs/EBS_ORACLE_BASE.md", "main.py"):
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    checar("BANCO_REMOVIDO" not in texto and "USUARIO_REMOVIDO" not in texto
           and "BASE_REMOVIDA" not in texto,
           f"{arquivo} não tem sobra do expurgo de histórico")
for arquivo in ("integracoes/ebs_oracle.py", "routers/ebs_oracle.py",
                "modulos/parametros.js", ".env.example"):
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    checar(not re.search(r"\b\w+[-.\w]*:1521/\w+", texto),
           f"{arquivo} não tem endereço de banco escrito")


print("\n[3] Só-leitura, com teto de linhas e de tempo")
checar("SET TRANSACTION READ ONLY" in fonte_mod, "cada consulta abre transação só-leitura")
checar("conn.rollback()" in fonte_mod, "e a conexão nunca comita — rollback no finally")
checar("autocommit = False" in fonte_mod, "autocommit desligado")
checar("call_timeout" in fonte_mod, "timeout por chamada, para não travar sessão no banco")
checar("fetchmany(max_rows)" in fonte_mod, "teto de linhas por consulta")


print("\n[4] SQL digitado na tela é aceito, mas contido")
# Esta tela é o campo de consulta que a área pediu: digitar um SELECT e
# testar ali mesmo. Quem pode chegar nela já é admin de Parâmetros, e a
# conexão é só-leitura (seção [3]). O que se prova aqui é a contenção: só
# leitura, uma consulta por vez, tamanho limitado e parâmetro por bind.
fonte_router = (RAIZ / "routers" / "ebs_oracle.py").read_text(encoding="utf-8")
for sql, motivo in (
    ("UPDATE po_headers_all SET segment1 = 'x'", "UPDATE"),
    ("DELETE FROM po_headers_all", "DELETE"),
    ("INSERT INTO po_headers_all VALUES (1)", "INSERT"),
    ("BEGIN NULL; END;", "bloco PL/SQL"),
    ("MERGE INTO po_headers_all USING dual ON (1=1)", "MERGE"),
    ("GRANT DBA TO scott", "GRANT"),
):
    r = cliente.post("/api/ebs-oracle/consultar", json={"sql": sql})
    checar(r.status_code == 422, f"{motivo} é recusado antes de sair ({r.status_code})")
r = cliente.post("/api/ebs-oracle/consultar",
                 json={"sql": "SELECT 1 FROM dual; DROP TABLE x"})
checar(r.status_code == 422, "duas consultas no mesmo texto são recusadas")
r = cliente.post("/api/ebs-oracle/consultar", json={"sql": "SELECT " + "'a'," * 9000 + "1 FROM dual"})
checar(r.status_code == 422, "consulta grande demais é recusada")
r = cliente.post("/api/ebs-oracle/consultar", json={"sql": "   "})
checar(r.status_code == 422, "consulta vazia é recusada")
checar("_INICIO_PERMITIDO" in fonte_router and "READ ONLY" in fonte_mod,
       "a permissão é por início do texto E por transação só-leitura no banco")
r = cliente.post("/api/ebs-oracle/consultar", json={})
checar(r.status_code == 422, "sem nome e sem SQL, 422")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "nao_existe"})
checar(r.status_code == 422, "nome fora da lista, 422")


print("\n[5] As consultas nomeadas, com os binds derivados do próprio SQL")
r = cliente.get("/api/ebs-oracle/consultas")
d = r.json()
nomes = {q["nome"]: q for q in d["consultas"]}
checar(set(nomes) == {"acordos", "ativo_por_serial", "busca_po", "catalogo",
                      "po", "po_itens", "rc", "saldo", "vendor_items",
                      "vendor_lookup"},
       "as dez consultas de negócio estão lá")
# É ela que a tela Internalização → Patrimônio usa para saber se o
# equipamento já virou ativo fixo no EBS. A busca é pelo SERIAL porque é o
# que o portal tem na mão: a plaqueta é o próprio EBS quem atribui.
checar(nomes["ativo_por_serial"]["binds"] == ["numero_serie"],
       "ativo_por_serial procura pelo número de série")
# BINDS é derivado de QUERIES por regex sobre o próprio SQL. Uma lista escrita
# à parte seria uma segunda verdade: mexeriam no SQL e o formulário da tela
# continuaria pedindo o parâmetro velho — ou deixaria de pedir o novo.
checar(nomes["busca_po"]["binds"] == ["numero_po", "p_line_num"],
       "binds de busca_po saem do SQL, não de uma lista à parte")
checar(nomes["po"]["binds"] == ["p_project_number"], "binds de po")
# A PO de acordo de compras tem UM número e várias liberações: o que muda de
# um pedido para outro é o número depois do hífen (2570313-25 → liberação 25).
# Sem o bind da liberação a consulta somaria as quantidades de todas elas, e o
# agendamento nasceria pedindo o total do ano.
checar(sorted(nomes["po_itens"]["binds"]) == ["liberacao", "numero_po"],
       "po_itens recebe o número da PO E a liberação")
fonte_agf = (RAIZ / "routers" / "agendamentos_forn.py").read_text(encoding="utf-8")
checar('"liberacao": liberacao' in fonte_agf,
       "e o endereço dos Agendamentos passa a liberação que separou do hífen")
sql_itens = [q for q in d["consultas"] if q["nome"] == "po_itens"]
checar(bool(sql_itens), "a consulta po_itens continua registrada")
checar(all(isinstance(q.get("binds"), list) for q in d["consultas"]),
       "toda consulta diz quais parâmetros espera, mesmo quando não espera nenhum")

# O TEXTO do SQL não vem junto, e é o ponto desta rota: ele traz esquema,
# tabelas e colunas do EBS — o mapa da base de outra área. A tela precisa do
# NOME da consulta e dos parâmetros; nada além disso.
checar(all("sql" not in q for q in d["consultas"]),
       "nenhuma consulta devolve o campo sql")
sem_sql(r.text, "/consultas")

# Bind que a consulta não usa é erro de quem chamou, e precisa morrer aqui:
# chegando ao banco vira ORA-01036 ("illegal variable name/number"), que não
# diz qual parâmetro sobrou nem quais a consulta espera. Repare que ainda não
# há credencial nenhuma — se a recusa for 503 ou 502, a validação está depois
# da conexão, e não antes dela.
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "po", "binds": {"inventado": 1}})
detalhe = str(r.json().get("detail", ""))
checar(r.status_code == 422 and "inventado" in detalhe and "p_project_number" in detalhe,
       f"bind que a consulta não usa é recusado, dizendo quais ela espera ({r.status_code})")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "po", "binds": {"1; DROP": 1}})
checar(r.status_code == 422, "nome de bind que não é identificador é recusado")


print("\n[6] Com credencial gravada: nada do que está no cofre aparece")
cofre.definir("ORACLE_EBS_DSN", DSN)
cofre.definir("ORACLE_EBS_USER", USUARIO)
cofre.definir("ORACLE_EBS_PASS", SENHA)
r = cliente.get("/api/ebs-oracle/situacao")
d = r.json()
nomes = {c["chave"]: c for c in d["chaves"]}
checar(all(nomes[k]["resolvida"] for k in OBRIGATORIAS),
       "as três chaves passam a aparecer como resolvidas")
checar(all(nomes[k]["situacao"] == "cofre" for k in OBRIGATORIAS),
       "e a situação de cada uma é 'cofre', não 'padrão do código'")
checar(nomes["ORACLE_EBS_DSN"]["fonte"].startswith("cofre"),
       "a tela diz de qual cofre o valor veio")
checar(all(nomes[k]["no_local"] for k in OBRIGATORIAS),
       "e diz que estão no cofre local, e não no corporativo")
# Gravar pela tela TEM de alimentar a conexão também: `_secret_multi` lê o
# ambiente e, não achando, o cofre. Sem essa segunda parada a tela dizia
# "resolvida" e a conexão falhava assim mesmo — a tela procurando num lugar
# e o portal em outro.
from integracoes import ebs_oracle as _acesso  # noqa: E402
checar(_acesso._config()["dsn"] == DSN,
       "o que a tela grava no cofre é o que a conexão vai usar")
# E nada do valor sai na resposta. O usuário e o endereço não são senha, mas
# são dado de acesso: com eles, quem lê a tela sabe onde bater e com que
# conta — só falta a senha.
checar(all("valor" not in nomes[k] for k in OBRIGATORIAS),
       "DSN, usuário e senha não trazem o valor — nem o usuário, que não é "
       "senha mas é dado de acesso")
sem_segredo(r.text, "/situacao")
sem_sql(r.text, "/situacao")


print("\n[7] O erro do driver traz o endereço dentro; a tela, não")
# O host não existe, então a conexão falha de verdade. O texto do driver muda
# com a versão e com o modo (neste ambiente, modo thin, a falha é de resolução
# de nome e vem sem o endereço); o que não pode mudar é a resposta da API.
r = cliente.post("/api/ebs-oracle/testar")
checar(r.status_code == 502, f"falha de conexão vira 502 ({r.status_code})")
sem_segredo(r.text, "erro de conexão")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "vendor_lookup"})
checar(r.status_code == 502, f"consulta sem banco também vira 502 ({r.status_code})")
sem_segredo(r.text, "erro de consulta")
# O 502 da consulta nomeada não pode devolver o SQL que tentou rodar.
sem_sql(r.text, "erro de consulta")

# O caso de produção: ORA-12154, ORA-12541 e DPY-6003 citam host, porta e
# instância dentro da própria mensagem. Aqui o driver falhou antes disso, na
# resolução do nome, então o erro é forçado a ser o de lá — é o único jeito de
# provar a promessa do topo deste arquivo: nem quando o erro do driver traz o
# endereço dentro ele sai na resposta.
_original_check = _acesso.check_access


def _erro_como_em_producao():
    host, servico = DSN.split("/")[0].split(":")[0], DSN.split("/")[-1]
    raise RuntimeError(f'DPY-6003: SID "{servico}" is not registered with the '
                       f'listener at host "{host}" port 1521')


_acesso.check_access = _erro_como_em_producao
try:
    r = cliente.post("/api/ebs-oracle/testar")
finally:
    _acesso.check_access = _original_check
checar(r.status_code == 502,
       f"erro do driver com o endereço dentro também vira 502 ({r.status_code})")
sem_segredo(r.text, "erro do driver que cita o endereço")


print("\n[8] Catálogo: só com prefixo, e só catálogo")
r = cliente.get("/api/ebs-oracle/objetos?prefixo=PO")
checar(r.status_code == 422, f"prefixo curto demais é recusado antes de sair ({r.status_code})")
checar("3 letras" in str(r.json().get("detail", "")),
       "e a recusa diz quanto falta, em vez de varrer o catálogo inteiro")
# Owner e nome de objeto não são validados por formato, e aqui não precisam
# ser: os dois viajam como bind variable (:owner, :name, :pat), nunca
# concatenados no texto. Um ';' no meio vira busca que não acha nada, e não
# um segundo comando.
catalogo = fonte_mod[fonte_mod.index("def list_objects"):fonte_mod.index("def sql_livre")]
checar(":owner" in catalogo and ":pat" in catalogo,
       "owner e prefixo vão como bind variable, não concatenados no SQL")
checar(":name" in catalogo, "o nome do objeto também vai como bind variable")
checar('f"""' not in catalogo and ".format(" not in catalogo,
       "o SQL do catálogo é texto fixo, sem interpolação")
checar("all_objects" in fonte_mod and "all_tab_columns" in fonte_mod,
       "o catálogo lê all_objects/all_tab_columns — dicionário, não tabela de negócio")
checar(not re.search(r"PO_HEADERS_ALL|PA_PROJECTS_ALL|MTL_SYSTEM_ITEMS_B",
                     catalogo, re.I),
       "e nenhuma tabela de negócio entra no caminho do catálogo")
# Sem banco alcançável as duas chamadas morrem; o que se prova é que morrem
# limpas, sem 500 e sem levar endereço junto.
for rota, desc in (("/api/ebs-oracle/objetos?prefixo=PO_HEADERS&owner=A;DROP", "objetos"),
                   ("/api/ebs-oracle/descrever?objeto=x;drop", "descrever")):
    r = cliente.get(rota)
    checar(r.status_code in (422, 502),
           f"{desc}: entrada estranha é recusada ou morre no banco, sem 500 ({r.status_code})")
    sem_segredo(r.text, f"catálogo ({desc})")


print("\n[9] Permissão")
anon = TestClient(main.app)
for metodo, rota in (("get", "/api/ebs-oracle/situacao"),
                     ("get", "/api/ebs-oracle/consultas"),
                     ("get", "/api/ebs-oracle/objetos?prefixo=PO_HEADERS"),
                     ("get", "/api/ebs-oracle/descrever?objeto=PO_HEADERS_ALL"),
                     ("post", "/api/ebs-oracle/testar"),
                     ("post", "/api/ebs-oracle/consultar")):
    r = getattr(anon, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
    checar(r.status_code in (401, 403), f"{rota} exige sessão ({r.status_code})")
comum = TestClient(main.app)
_, ck = security.create_session(
    {"username": "sem-admin", "is_admin": False, "permission_map": {}})
comum.cookies.set("spare_session", ck)
# Sessão válida não basta: é base de produção de outra área, e a permissão é
# a de admin de Parâmetros. Vale para TODA rota, não só para a primeira tela.
for metodo, rota in (("get", "/api/ebs-oracle/situacao"),
                     ("get", "/api/ebs-oracle/consultas"),
                     ("get", "/api/ebs-oracle/objetos?prefixo=PO_HEADERS"),
                     ("post", "/api/ebs-oracle/testar"),
                     ("post", "/api/ebs-oracle/consultar")):
    r = getattr(comum, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
    checar(r.status_code == 403, f"{rota} sem admin: 403 ({r.status_code})")


print("\n[10] Registro e tela")
checar('MODULO = "parametros"' in fonte_router,
       "usa a permissão de Parâmetros, e não uma chave nova sem tela")
principal = (RAIZ / "main.py").read_text(encoding="utf-8")
checar("from routers.ebs_oracle import router" in principal,
       "main.py importa o router da base do EBS")
checar("include_router(ebs_oracle_router)" in principal,
       "e o registra na aplicação — rota escrita e não pendurada não existe")
# A Base EBS é aba só de admin, e as abas de admin saíram do parametros.js
# para um arquivo próprio, entregue apenas a quem tem a permissão. A tela é
# procurada onde ela está hoje, mas as verificações de vazamento valem para
# os DOIS arquivos — o que não pode aparecer, não pode em nenhum deles.
js_admin = (RAIZ / "modulos/parametros_admin.js").read_text(encoding="utf-8")
js_base = (RAIZ / "modulos/parametros.js").read_text(encoding="utf-8")
js = js_admin + "\n" + js_base
checar("renderBaseEbs" in js_admin and "renderBaseEbs" not in js_base,
       "a tela da Base EBS vive no arquivo de admin, não no que todos recebem")
checar("/ebs-oracle/" not in js_base,
       "quem não é admin não recebe nem o endereço das rotas da base do EBS")
for trecho, desc in (("/ebs-oracle/situacao", "mostra a situação das chaves"),
                     ("/ebs-oracle/testar", "tem o botão de testar conexão"),
                     ("/ebs-oracle/consultas", "lista as consultas nomeadas"),
                     ("/ebs-oracle/consultar", "executa pelo nome"),
                     ("/ebs-oracle/objetos", "procura no catálogo")):
    checar(trecho in js, f"parametros.js {desc}")
# O corpo da tela, da definição de renderBaseEbs até a próxima função.
corpo_js = js_admin.split("renderBaseEbs")[-1].split("\nasync function")[0]
# A tela NÃO exibe o SQL das consultas nomeadas. O bloco "Ver o SQL desta
# consulta" saiu junto com o campo `sql` da resposta de /consultas: o que a
# API não manda, a tela não tem como mostrar — e o que a tela não pede,
# ninguém volta a mandar sem perceber. Quem precisa do SQL o lê no
# repositório, onde ele é versionado e revisável.
#
# O campo de consulta livre é outra coisa: ali o SQL é DIGITADO por quem
# está na tela (seção [4]), e o exemplo no placeholder foi escrito à mão
# aqui. Por isso a verificação não é "não existe a palavra SELECT na tela",
# e sim "nada do SQL que está no servidor foi parar nela".
checar('id="eo-sql"' not in js and "Ver o SQL" not in js,
       "a tela não tem mais o elemento que exibia o SQL da consulta")
checar("<details>" not in corpo_js,
       "nem o bloco recolhível que o abrigava")
checar(not re.search(r"\.sql\b", corpo_js),
       "e não lê campo de SQL de resposta nenhuma")
achatado = " ".join(js.split())
for nome_consulta, sql_da_consulta in sorted(_acesso.QUERIES.items()):
    trecho = " ".join(sql_da_consulta.split())[:60]
    checar(trecho not in achatado,
           f"o SQL da consulta {nome_consulta} não está escrito na tela")
checar(not re.search(r"#[0-9a-fA-F]{3,6}\b", corpo_js),
       "a tela não tem cor fixa — só tokens do padrão")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Leitura da base do EBS íntegra: credencial pelo cofre, nada de acesso na tela.")
