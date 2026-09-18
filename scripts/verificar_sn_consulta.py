#!/usr/bin/env python3
"""Verificação da consulta de chamados do ServiceNow (incidents e RITMs).

    python3 scripts/verificar_sn_consulta.py

Sobe o portal em memória e troca o ServiceNow por um de mentira, que responde
como o de verdade: devolve o dicionário de campos, esconde o que a ACL nega,
pagina, e conta. Só assim dá para exercitar o que importa — a montagem da
encoded query, a descoberta de campos e a paginação de mais de 5 mil.

O que se conferem aqui são as três coisas que quebram calado:

*   **Injeção na encoded query.** `^` e `=` num valor digitado deixam de ser
    conteúdo e viram estrutura: `X^state=7` muda o sentido da busca sem erro
    nenhum. Um campo inventado idem.
*   **Campo que a conta não lê.** O ServiceNow não erra — ele omite a chave.
    Se a tela oferecer esse campo, a coluna sai vazia no arquivo e parece
    dado faltando no chamado.
*   **Exportação acima de 5 mil.** Com offset, uma linha aparece duas vezes
    ou some quando chamados são criados durante a exportação. Aqui a
    paginação por sys_id é exercitada com um ServiceNow que MUDA no meio.
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
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import routers.sn_consulta as sc  # noqa: E402

sc._cfg.SN_API_USER = "zabbix"
sc._cfg.SN_API_PASS = "senha-da-conta-que-nao-pode-sair"

app = main.app
cliente = TestClient(app)
_, cookie = security.create_session(
    {"username": "verificador", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


# ── ServiceNow de mentira ──────────────────────────────────────────────
# Campos do dicionário. `SEM_ACESSO` existe no dicionário e a conta de
# serviço NÃO lê — é o caso que a sonda tem de derrubar.
DICIONARIO = [
    ("number", "Número", "string", "task"),
    ("short_description", "Descrição", "string", "task"),
    ("state", "Estado", "integer", "task"),
    ("opened_at", "Aberto em", "glide_date_time", "task"),
    ("priority", "Prioridade", "integer", "task"),
    ("category", "Categoria", "string", "incident"),
    ("subcategory", "Subcategoria", "string", "incident"),
    ("assignment_group", "Grupo", "reference", "task"),
    ("assigned_to", "Atribuído a", "reference", "task"),
    ("caller_id", "Solicitante", "reference", "incident"),
    ("sys_created_on", "Criado em", "glide_date_time", "task"),
    ("u_custo_interno", "Custo interno", "currency", "incident"),
]
SEM_ACESSO = "u_custo_interno"

# 12.000 chamados: acima do teto da tela (1.000) e bem acima dos 5 mil que
# o pedido cita, para a paginação ser exercitada de verdade.
TOTAL = 12000
BASE = [{
    # Começa em 1 de propósito: o `sys_id` todo-zero fica livre para o
    # registro que [5] insere no meio da exportação, e assim ele cai ABAIXO
    # de qualquer cursor já percorrido — que é onde o offset se perde.
    "sys_id": f"{i + 1:032x}",
    "number": f"INC{i:07d}",
    "short_description": f"chamado {i}",
    "state": "6" if i % 2 else "7",
    "opened_at": "2026-09-01 10:00:00",
    "priority": "3",
    "category": "Hardware",
    "subcategory": "Coletor",
    "assignment_group": "TI_N2_FLD_RNR_LOJAS_SPARE",
    "assigned_to": "fulano",
    "caller_id": "beltrano",
    "sys_created_on": "2026-09-01 10:00:00",
    SEM_ACESSO: "999,99",
} for i in range(TOTAL)]

chamadas: list[dict] = []
_interferir = {"ligado": False, "feito": False}


# O que um servidor/proxy real faz com URL grande: 414. Sem isto a
# verificação passaria feliz com uma query de 60 KB que nunca funcionaria em
# produção — e o defeito só apareceria com a lista de verdade na mão.
LIMITE_URL = 8000


def _falso_get(caminho, params, timeout=60):
    chamadas.append({"caminho": caminho, "params": dict(params)})
    q = params.get("sysparm_query", "")
    tamanho = sum(len(str(k)) + len(str(v)) + 2 for k, v in params.items())
    if tamanho > LIMITE_URL:
        from fastapi import HTTPException as _HE
        raise _HE(502, f"ServiceNow retornou 414 (URL de {tamanho} bytes).")

    if caminho == "/api/now/table/sys_db_object":
        nome = q.split("name=")[-1]
        # Este é o ponto do defeito. `super_class` é referência a
        # sys_db_object, cuja coluna de EXIBIÇÃO é `label`. Com
        # display_value=true volta "Task"; o `name` é "task". Só o dot-walk
        # `super_class.name` entrega o nome. E aqui o `name=` é comparado
        # com distinção de maiúscula, como num banco case-sensitive — que é
        # o caso em que o código antigo quebrava de vez.
        PAI = {"incident": "task", "sc_req_item": "task", "task": ""}
        if nome not in PAI:
            return {"result": []}
        filho = PAI[nome]
        if params.get("sysparm_fields") == "super_class.name":
            return {"result": [{"super_class.name": filho}]}
        return {"result": [{"super_class": (filho.title() if filho else "")}]}

    if caminho == "/api/now/table/sys_dictionary":
        # Sem .lower(): a tabela pedida tem de bater exatamente. É assim que
        # o pedido "nameINincident,Task" (o bug) perde os campos da task.
        tabelas = [x.strip() for x in q.split("nameIN")[1].split("^")[0].split(",")]
        return {"result": [
            {"element": el, "column_label": rot, "internal_type": tipo, "name": tab}
            for el, rot, tipo, tab in DICIONARIO if tab in tabelas
        ]}

    if caminho.startswith("/api/now/stats/"):
        return {"result": {"stats": {"count": str(len(_filtrar(q)))}}}

    if caminho.startswith("/api/now/table/"):
        pedidos = [c for c in (params.get("sysparm_fields") or "").split(",") if c]
        # A ACL: o campo simplesmente não vem. Sem erro, sem aviso.
        pedidos = [c for c in pedidos if c != SEM_ACESSO]
        linhas = _filtrar(q)
        inicio = int(params.get("sysparm_offset") or 0)
        limite = int(params.get("sysparm_limit") or 100)
        fatia = linhas[inicio:inicio + limite]
        return {"result": [{c: l.get(c, "") for c in pedidos} for l in fatia]}
    raise AssertionError(f"caminho inesperado: {caminho}")


def _filtrar(q: str) -> list[dict]:
    """Interpreta o pedaço da encoded query que esta verificação usa."""
    linhas = BASE
    for parte in [p for p in q.split("^") if p]:
        if parte.startswith("ORDERBYDESC"):
            linhas = sorted(linhas, key=lambda l: l.get(parte[11:], ""), reverse=True)
        elif parte.startswith("ORDERBY"):
            linhas = sorted(linhas, key=lambda l: l.get(parte[7:], ""))
        elif parte.startswith("sys_id>"):
            corte = parte[len("sys_id>"):]
            linhas = [l for l in linhas if l["sys_id"] > corte]
            # O ServiceNow real continua recebendo chamados durante a
            # exportação. Aqui um chamado NOVO entra no meio do caminho: com
            # offset isso desloca tudo; por sys_id, não muda o que já passou.
            if _interferir["ligado"] and not _interferir["feito"]:
                _interferir["feito"] = True
                BASE.insert(0, dict(BASE[0], sys_id="0" * 32, number="INC-INTRUSO"))
        elif parte.startswith("numberIN"):
            querem = set(parte[len("numberIN"):].split(","))
            linhas = [l for l in linhas if l["number"] in querem]
        elif "LIKE" in parte and not parte.startswith("NOT"):
            campo, alvo = parte.split("LIKE", 1)
            linhas = [l for l in linhas if alvo in str(l.get(campo, ""))]
        elif "=" in parte:
            campo, alvo = parte.split("=", 1)
            linhas = [l for l in linhas if str(l.get(campo, "")) == alvo]
    return linhas


sc._get = _falso_get
sc._cache_campos.clear()


TETO_TELA_ESPERADO = sc.TETO_TELA

print("[1] As tabelas oferecidas são as que o pedido cita")
d = cliente.get("/api/sn-consulta/tabelas").json()
por_tabela = {t["tabela"]: t for t in d["tabelas"]}
checar("incident" in por_tabela and por_tabela["incident"]["tela"] == "incident.do",
       "incidentes, pela tela incident.do")
checar("sc_req_item" in por_tabela, "RITMs, na tabela sc_req_item")
checar(por_tabela.get("task", {}).get("tela") == "task_list.do",
       "e a tabela-mãe task, que é o que task_list.do lista")
ops = {o["chave"]: o for o in d["operadores"]}
checar(ops["vazio"]["precisa_valor"] is False and ops["contem"]["precisa_valor"] is True,
       "a tela sabe quais operadores não levam valor")


print("\n[2] Todo campo é oferecido, e a sonda diz o que achou de cada um")
# A versão anterior REMOVIA o que a sonda não confirmasse. A intenção era
# evitar coluna vazia no arquivo; o efeito foi pior — campo sumido é
# indistinguível de defeito, e foi assim que `number` e `opened_at` sumiram
# da tela sem nenhuma pista do porquê. Agora nada some: marca-se.
d2 = cliente.get("/api/sn-consulta/campos?tabela=incident").json()
nomes = [c["campo"] for c in d2["campos"]]
por_campo = {c["campo"]: c for c in d2["campos"]}
checar("short_description" in nomes,
       "campo herdado da task aparece — a hierarquia é percorrida")
checar("number" in nomes and "opened_at" in nomes,
       "o número do chamado e a data de abertura estão lá (são campos da task)")
checar("category" in nomes, "e o campo da própria incident também")
checar(d2["hierarquia"] == ["incident", "task"],
       "a tela mostra a cadeia de herança que foi percorrida")
checar(SEM_ACESSO in nomes,
       f"{SEM_ACESSO} continua sendo oferecido, mesmo com a ACL negando")
checar(por_campo[SEM_ACESSO]["lido"] is False,
       "mas marcado: a conta não conseguiu lê-lo")
checar(por_campo["number"]["lido"] is True,
       "e o que a conta lê é marcado como lido")
checar(d2["nao_lidos"] == 1, f"a tela conta quantos estão sem leitura ({d2['nao_lidos']})")
checar(d2["total"] == len(DICIONARIO),
       "a contagem é a do dicionário inteiro — não se perde campo pelo caminho")
checar(d2["conta"] == "zabbix", "a tela diz qual conta respondeu por esses campos")
checar(sc._cfg.SN_API_PASS not in cliente.get("/api/sn-consulta/campos?tabela=incident").text,
       "e a senha da conta não sai em lugar nenhum")
checar(cliente.get("/api/sn-consulta/campos?tabela=sys_user").status_code == 422,
       "tabela fora da lista é recusada")

print("\n[2b] Contraprova: a herança subia pelo RÓTULO, e por isso quebrava")
# `super_class` é referência a sys_db_object, cuja coluna de exibição é
# `label`. Com display_value=true volta "Task"; o `name` é "task". A consulta
# seguinte virava `name=Task` e, num banco que distingue maiúscula, não
# casava — a cadeia parava em ["incident"] e TODOS os campos da task sumiam,
# entre eles `number` e `opened_at`. Aqui se reproduz o caminho antigo.
def _hierarquia_pelo_rotulo(tabela):
    cadeia, atual = [], tabela
    while atual and atual not in cadeia and len(cadeia) < 10:
        cadeia.append(atual)
        dados = _falso_get("/api/now/table/sys_db_object", {
            "sysparm_query": f"name={atual}",
            "sysparm_fields": "super_class",          # sem o dot-walk
            "sysparm_display_value": "true",          # o rótulo, não o nome
            "sysparm_limit": "1",
        })
        linhas = dados.get("result") or []
        atual = (linhas[0].get("super_class") or "") if linhas else ""
    return cadeia

antiga = _hierarquia_pelo_rotulo("incident")
checar(antiga == ["incident", "Task"],
       f"pelo rótulo, a cadeia sai errada: {antiga}")
perdidos = [el for el, _r, _t, tab in DICIONARIO if tab == "task"]
sobravam = [c["campo"] for c in sc._campos_do_dicionario("incident", antiga)]
checar("number" not in sobravam and "opened_at" not in sobravam,
       "e com ela o número do chamado e a data de abertura somem — o defeito relatado")
checar(all(p not in sobravam for p in perdidos),
       f"junto com os outros {len(perdidos)} campos da task")
checar(sc._hierarquia("incident") == ["incident", "task"],
       "pelo dot-walk super_class.name, a cadeia sai certa")

print("\n[2c] Campo do padrão que não vier é REPOSTO, e a tela avisa")
# Se a descoberta falhar de outro jeito no futuro, a tela não pode voltar a
# abrir sem o número do chamado. O portal repõe as colunas padrão e diz que
# repôs — em vez de a coluna simplesmente não existir.
sc._cache_campos.clear()
_dicionario_real = sc._campos_do_dicionario
sc._campos_do_dicionario = lambda tabela, nomes=None: [
    c for c in _dicionario_real(tabela, nomes) if c["campo"] not in ("number", "opened_at")
]
d2c = cliente.get("/api/sn-consulta/campos?tabela=incident&recarregar=true").json()
nomes2c = {c["campo"]: c for c in d2c["campos"]}
checar("number" in nomes2c and "opened_at" in nomes2c,
       "some do dicionário, mas a tela continua oferecendo")
checar(nomes2c["number"]["lido"] is None and nomes2c["number"].get("do_padrao") is True,
       "marcado como não conferido e reposto pelo portal")
checar(set(d2c["padrao_ausentes"]) == {"number", "opened_at"},
       "e a resposta NOMEIA o que faltou — silêncio aqui foi o defeito original")
sc._campos_do_dicionario = _dicionario_real
sc._cache_campos.clear()

print("\n[3] O que a tela manda não vira estrutura de consulta")
def recusa(filtros, desc, esperado=422):
    r = cliente.post("/api/sn-consulta/buscar",
                     json={"tabela": "incident", "filtros": filtros})
    checar(r.status_code == esperado, desc + f" ({r.status_code})")

recusa([{"campo": "state", "operador": "igual", "valor": "6^ORstate=7"}],
       "valor com ^ é recusado — mudaria o sentido da busca")
recusa([{"campo": "state", "operador": "igual", "valor": "6=7"}],
       "valor com = é recusado")
recusa([{"campo": "state", "operador": "igual", "valor": "javascript:gs.getUser()"}],
       "valor com javascript: é recusado — o servidor do SN avaliaria isso")
recusa([{"campo": "nao_existe_esse", "operador": "igual", "valor": "x"}],
       "campo fora da lista lida do dicionário é recusado")
# Filtrar por campo que a conta não lê é PERMITIDO: a marca "sem leitura" é
# um aviso, não uma proibição — a sonda olha um registro só e pode errar, e
# recusar por causa dela seria a tela impedindo o que o ServiceNow permite.
r_marcado = cliente.post("/api/sn-consulta/buscar",
                         json={"tabela": "incident",
                               "filtros": [{"campo": SEM_ACESSO, "operador": "igual",
                                            "valor": "x"}]})
checar(r_marcado.status_code == 200,
       f"filtrar por campo marcado 'sem leitura' é permitido, com aviso ({r_marcado.status_code})")
recusa([{"campo": "state", "operador": "inventado", "valor": "x"}],
       "operador fora da lista fechada é recusado")
recusa([{"campo": "state", "operador": "igual", "valor": "6,7"}],
       "vírgula fora de lista é recusada, em vez de virar filtro estranho")
recusa([{"campo": "state", "operador": "igual", "valor": "  "}],
       "filtro sem valor é recusado, e não vira 'state='")
r = cliente.post("/api/sn-consulta/buscar",
                 json={"tabela": "incident", "campos": ["state", "nao_existe"]})
checar(r.status_code == 422, f"coluna inventada também é recusada ({r.status_code})")

# E o que é legítimo passa, montado do jeito certo.
chamadas.clear()
r = cliente.post("/api/sn-consulta/buscar", json={
    "tabela": "incident",
    "campos": ["number", "state"],
    "filtros": [{"campo": "state", "operador": "igual", "valor": "6"},
                {"campo": "short_description", "operador": "contem", "valor": "chamado 1"},
                {"campo": "category", "operador": "preenchido", "valor": "ignorado"}],
    "ordenar_por": "opened_at", "ordem": "desc",
})
d3 = r.json()
checar(r.status_code == 200, f"consulta legítima responde 200 ({r.status_code})")
checar(d3["query"] == "state=6^short_descriptionLIKEchamado 1^categoryISNOTEMPTY^ORDERBYDESCopened_at",
       "a query sai montada na ordem certa, com o operador sem valor sozinho")
checar(d3["campos"] == ["number", "state"], "e só com as colunas pedidas")
checar(all(set(l) == {"number", "state"} for l in d3["linhas"]),
       "as linhas não trazem campo que ninguém pediu")


print("\n[4] Mais de 5 mil: a conta vem do servidor, a tela traz amostra")
chamadas.clear()
r4 = cliente.post("/api/sn-consulta/buscar",
                  json={"tabela": "incident", "campos": ["number"], "por_pagina": 5000})
d4 = r4.json()
checar(d4["total"] == TOTAL, f"diz o total de verdade ({d4['total']}), sem baixar tudo")
checar(len(d4["linhas"]) == sc.TETO_TELA,
       f"a tela recebe no máximo {sc.TETO_TELA} linhas, mesmo pedindo 5000")
checar(any(c["caminho"].startswith("/api/now/stats/") for c in chamadas),
       "a contagem sai da API de agregação, que conta no servidor")
baixadas = sum(len(c["params"].get("sysparm_fields", "").split(","))
               for c in chamadas if c["caminho"].startswith("/api/now/table/"))
checar(baixadas > 0 and len(chamadas) <= 3,
       "e a tela custa poucas chamadas — não varre a fila para contar")


print("\n[5] Exportação: passa dos 5 mil sem duplicar nem pular")
_interferir["ligado"] = True
_interferir["feito"] = False
chamadas.clear()
r5 = cliente.post("/api/sn-consulta/exportar",
                  json={"tabela": "incident", "campos": ["number", "state"]})
checar(r5.status_code == 200, f"HTTP 200 ({r5.status_code})")
linhas_csv = [l for l in r5.text.splitlines() if l.strip()]
checar(linhas_csv[0].lstrip("﻿").startswith("Número;Estado"),
       "primeira linha: os rótulos, para quem vai ler a planilha")
checar(linhas_csv[1] == "number;state",
       "segunda linha: o nome técnico, para quem vai cruzar com outro sistema")
numeros = [l.split(";")[0] for l in linhas_csv[2:]]
checar(len(numeros) == TOTAL, f"vieram os {TOTAL} chamados ({len(numeros)})")
checar(len(set(numeros)) == len(numeros),
       "nenhum chamado repetido, mesmo com registro novo entrando no meio")
checar(_interferir["feito"], "e o registro novo REALMENTE entrou durante a exportação")
checar("INC-INTRUSO" not in numeros,
       "o chamado que entrou depois não aparece — a foto é do momento da busca")
checar(not any("sysparm_offset" in c["params"] for c in chamadas
               if c["caminho"].startswith("/api/now/table/")),
       "a exportação não usa offset em nenhuma página")
checar(all("ORDERBYsys_id" in c["params"].get("sysparm_query", "")
           for c in chamadas if c["caminho"].startswith("/api/now/table/")),
       "toda página anda por sys_id")
checar("sys_id" not in linhas_csv[1],
       "o sys_id serve para paginar, mas não vira coluna que ninguém pediu")
_interferir["ligado"] = False

# Contraprova do porquê da paginação por sys_id. O mesmo ServiceNow, o mesmo
# registro entrando no meio — só que percorrido por offset, que é o jeito
# óbvio e o que o resto do portal usa. Se o offset também acertasse, a
# complicação do sys_id não se justificaria.
def _percorrer_por_offset(tabela, campos, teto):
    saida, inicio = [], 0
    while len(saida) < teto:
        dados = _falso_get(f"/api/now/table/{tabela}", {
            "sysparm_query": "ORDERBYsys_id",
            "sysparm_fields": ",".join(campos),
            "sysparm_limit": str(sc.PAGINA_REST),
            "sysparm_offset": str(inicio),
        })
        linhas = dados.get("result") or []
        if not linhas:
            break
        saida.extend(linhas)
        inicio += len(linhas)
        # O intruso entra na mesma altura em que entrou na exportação real:
        # depois da primeira página.
        if _interferir["ligado"] and not _interferir["feito"]:
            _interferir["feito"] = True
            BASE.insert(0, dict(BASE[0], sys_id="0" * 32, number="INC-INTRUSO"))
        if len(linhas) < sc.PAGINA_REST:
            break
    return [l["number"] for l in saida]


BASE[:] = [l for l in BASE if l["number"] != "INC-INTRUSO"]
_interferir["ligado"], _interferir["feito"] = True, False
por_offset = _percorrer_por_offset("incident", ["number", "sys_id"], TOTAL)
_interferir["ligado"] = False
perdidos = {l["number"] for l in BASE if l["number"] != "INC-INTRUSO"} - set(por_offset)
checar(len(perdidos) > 0,
       f"contraprova: por offset, {len(perdidos)} chamado(s) some(m) da exportação")
checar(len(set(por_offset)) < len(por_offset) or perdidos,
       "e é exatamente esse deslize que a paginação por sys_id evita")
BASE[:] = [l for l in BASE if l["number"] != "INC-INTRUSO"]

# O teto avisa em vez de truncar calado.
antes = sc.TETO_EXPORTACAO
sc.TETO_EXPORTACAO = 100
r6 = cliente.post("/api/sn-consulta/exportar",
                  json={"tabela": "incident", "campos": ["number"]})
checar("ATENCAO" in r6.text and "100" in r6.text,
       "batendo no teto, o arquivo DIZ que parou — arquivo truncado calado fecha mês errado")
sc.TETO_EXPORTACAO = antes


print("\n[5b] Consultar POR LISTA de chamados, acima de 5 mil")
# O caso que originou a tela: "tenho esta lista de 5 mil chamados, me traga
# estes campos deles".
PEDIDOS = [f"INC{i:07d}" for i in range(0, 5200)]
# Trezentos que NÃO existem, no meio da lista — é o que a conferência
# precisa separar, e no meio (não no fim) para pegar erro de bloco.
INVENTADOS = [f"INC9{i:06d}" for i in range(300)]
LISTA = PEDIDOS[:2600] + INVENTADOS + PEDIDOS[2600:]

chamadas.clear()
r20 = cliente.post("/api/sn-consulta/buscar", json={
    "tabela": "incident", "campos": ["number", "state"],
    "numeros": "\n".join(LISTA),
})
checar(r20.status_code == 200, f"HTTP 200 com {len(LISTA)} chamados ({r20.status_code})")
d20 = r20.json()
checar(d20["por_lista"] is True, "a resposta diz que foi por lista, não por filtro")
checar(d20["pedidos"] == len(LISTA), f"conta os {len(LISTA)} pedidos")
checar(d20["total"] == 5200, f"acha os 5.200 que existem ({d20['total']})")
checar(d20["nao_encontrados_total"] == 300,
       f"e diz que 300 não foram encontrados ({d20['nao_encontrados_total']})")
checar(set(d20["nao_encontrados"]) <= set(INVENTADOS),
       "nomeando exatamente os que não existem")
checar(len(d20["linhas"]) <= TETO_TELA_ESPERADO,
       "a tela recebe uma amostra, não as 5 mil linhas")

# O ponto todo: a lista NÃO cabe numa URL só.
tabela = [c for c in chamadas if c["caminho"].startswith("/api/now/table/")]
checar(len(tabela) >= 22,
       f"a lista foi partida em blocos ({len(tabela)} chamadas ao ServiceNow)")
checar(all(len(c["params"]["sysparm_query"]) <= sc.TETO_QUERY for c in tabela),
       "e nenhum bloco passa do teto de tamanho da query")
# O corte é por TAMANHO, não por contagem: o mesmo lote de 250 itens dá 2,7 KB
# com números de chamado e 8,3 KB com sys_id. Contar itens deixou passar um
# 414 na exportação; medir caracteres não deixa.
checar(sc.TETO_QUERY < LIMITE_URL,
       "o teto da query fica abaixo do que o servidor aceita, com folga")
longos = [f"{i:032x}" for i in range(500)]
blocos_longos = list(sc._blocos(longos, sc.LOTE_NUMEROS, reservado=100))
checar(all(len(",".join(b)) + 100 <= sc.TETO_QUERY for b in blocos_longos),
       "e itens longos (sys_id) geram blocos menores, automaticamente")
checar(len(blocos_longos) > len(longos) // sc.LOTE_NUMEROS,
       "mais blocos que a contagem daria — é o tamanho que manda")
checar(all("numberIN" in c["params"]["sysparm_query"] for c in tabela),
       "cada bloco pergunta pelos números daquele bloco")

# Contraprova: em uma query só, a mesma lista é recusada pelo servidor.
# Sem isto, "partimos em blocos" seria só uma afirmação no comentário.
from fastapi import HTTPException as _HE  # noqa: E402
try:
    _falso_get("/api/now/table/incident", {
        "sysparm_query": "numberIN" + ",".join(LISTA),
        "sysparm_fields": "number", "sysparm_limit": "1",
    })
    estourou = False
except _HE as exc:
    estourou = "414" in str(exc.detail)
checar(estourou,
       "contraprova: os 5.500 numa query só levam 414 — por isso os blocos")

# Lista + filtro se somam: "destes 5 mil, só os resolvidos".
chamadas.clear()
r21 = cliente.post("/api/sn-consulta/buscar", json={
    "tabela": "incident", "campos": ["number", "state"],
    "numeros": ", ".join(PEDIDOS[:500]),
    "filtros": [{"campo": "state", "operador": "igual", "valor": "6"}],
})
d21 = r21.json()
checar(d21["total"] == 250, f"lista e filtro se somam ({d21['total']} de 500)")
checar(d21["nao_encontrados_total"] == 250,
       "e o que o filtro excluiu entra em 'não encontrado' — a tela explica as causas")

# A colagem de verdade: vírgula, espaço, quebra de linha, aspas e repetidos.
r22 = cliente.post("/api/sn-consulta/buscar", json={
    "tabela": "incident", "campos": ["number"],
    "numeros": ' "inc0000001" , INC0000002\nINC0000003;INC0000002\n\n  ',
})
d22 = r22.json()
checar(d22["pedidos"] == 3,
       "aceita vírgula, espaço, quebra de linha e aspas, e descarta repetido")
checar(d22["total"] == 3, "e acha os três — minúscula é normalizada")

for ruim, desc in (("INC1^ORstate=7", "número com ^ é recusado"),
                   ("INC1=2", "número com = é recusado"),
                   ("javascript:x", "número com script é recusado")):
    r23 = cliente.post("/api/sn-consulta/buscar",
                       json={"tabela": "incident", "numeros": ruim})
    checar(r23.status_code == 422, desc + f" ({r23.status_code})")
r24 = cliente.post("/api/sn-consulta/buscar", json={
    "tabela": "incident",
    "numeros": "\n".join(f"INC{i:07d}" for i in range(sc.TETO_NUMEROS + 5)),
})
checar(r24.status_code == 422 and "teto" in r24.json().get("detail", ""),
       "acima do teto, recusa dizendo que pode ser colagem errada")

print("\n[5c] Exportação por lista: o arquivo diz quem faltou")
r25 = cliente.post("/api/sn-consulta/exportar", json={
    "tabela": "incident", "campos": ["number", "state"],
    "numeros": "\n".join(PEDIDOS[:400] + INVENTADOS[:10]),
})
checar(r25.status_code == 200, f"HTTP 200 ({r25.status_code})")
corpo25 = r25.text
numeros25 = [l.split(";")[0] for l in corpo25.splitlines()[2:] if l.startswith("INC")]
checar(len([n for n in numeros25 if n in set(PEDIDOS[:400])]) == 400,
       "os 400 que existem estão no arquivo")
checar("Nao encontrados: 10" in corpo25,
       "e o arquivo fecha com a conta do que faltou")
checar(all(n in corpo25 for n in INVENTADOS[:10]),
       "listando cada um deles — é o que fecha a conferência")
checar("Pedidos: 410" in corpo25, "com o total pedido, para bater a conta")
# No MESMO arquivo: um segundo arquivo se perde entre a pasta de downloads
# e o anexo do e-mail.
checar(corpo25.count("\ufeff") <= 1, "tudo num arquivo só, não em dois")

print("\n[6] Sem sessão e sem permissão, não responde")
anon = TestClient(app)
checar(anon.get("/api/sn-consulta/tabelas").status_code in (401, 403),
       "sem sessão, nada")
checar(anon.post("/api/sn-consulta/buscar", json={"tabela": "incident"}).status_code in (401, 403),
       "nem a busca")

# Ver e exportar são permissões diferentes: exportar tira os chamados do
# portal, e é a ação que se libera a menos gente.
_, ck = security.create_session({"username": "so-ve", "is_admin": False,
                                 "permission_map": {"automacoes": {"can_view": True}}})
so_ve = TestClient(app)
so_ve.cookies.set("spare_session", ck)
checar(so_ve.get("/api/sn-consulta/tabelas").status_code == 200, "quem tem 'ver' consulta")
checar(so_ve.post("/api/sn-consulta/exportar",
                  json={"tabela": "incident"}).status_code == 403,
       "mas exportar pede a permissão própria")

_, ck2 = security.create_session({"username": "nada", "is_admin": False,
                                  "permission_map": {"automacoes": {}}})
sem = TestClient(app)
sem.cookies.set("spare_session", ck2)
checar(sem.get("/api/sn-consulta/campos?tabela=incident").status_code == 403,
       "e sem permissão nenhuma no módulo, 403")


print("\n[7] Sem conta de serviço, a tela diz o que falta")
# A regra mora em `_checar_conta`, chamada por `_get`. Como `_get` está
# trocado pelo ServiceNow de mentira, é a função que se exercita direto —
# testar pela rota aqui só provaria que o mock responde.
sc._cfg.SN_API_USER = ""
try:
    sc._checar_conta()
    faltou = ""
except Exception as exc:  # noqa: BLE001
    faltou = getattr(exc, "detail", str(exc))
checar("SN_API_USER" in faltou and "cofre" in faltou,
       "diz qual chave falta e onde gravá-la")
checar(sc._cfg.SN_API_PASS not in faltou, "sem citar a senha na mensagem")
sc._cfg.SN_API_USER = "zabbix"

# E o TLS não é desligado por conta própria: a sessão sai de integracoes/http.
import inspect  # noqa: E402
fonte = inspect.getsource(sc)
checar("verify=False" not in fonte and "InsecureRequestWarning" not in fonte,
       "o módulo não desliga a verificação de certificado nem silencia o aviso")
checar("http_saida.sessao(" in fonte,
       "a sessão HTTP sai de integracoes/http.py, que decide o TLS num lugar só")

print("\n[8] A tela existe, e é aba do módulo ServiceNow")
js = (RAIZ / "modulos/servicenow_automacoes.js").read_text(encoding="utf-8")
checar("['consulta',   'Consulta de chamados']" in js, "a aba está na lista")
checar("S.tabs(ABAS, sub, 'servicenow_automacoes')" in js, "ligada ao roteador de abas")
checar("renderConsulta" in js and "/sn-consulta/buscar" in js, "e chama a busca")
checar("/sn-consulta/exportar" in js, "e a exportação")
checar("cn-numeros" in js and "Chamados a consultar" in js,
       "o campo da lista de chamados está na tela")
checar("numeros: document.getElementById('cn-numeros').value" in js,
       "e a lista vai no corpo da consulta")
checar("contarNumeros" in js and "bloco(s)" in js,
       "a tela conta os chamados colados e diz em quantos blocos vão")
# A tela conta do mesmo jeito que o servidor: separadores iguais, aspas fora,
# maiúscula, repetido descartado. Contagem diferente seria a tela mentindo.
checar("/[\\s,;]+/" in js, "com os mesmos separadores do servidor")
checar("toUpperCase()" in js and "repetido(s) descartado(s)" in js,
       "e a mesma normalização, senão a tela diz 5.000 e o servidor 4.812")
checar("nao_encontrados" in js and "está em outra tabela" in js,
       "a tela explica as três causas de um chamado não vir")
checar("por_lista" in js, "e separa o resultado por lista do resultado por filtro")
# O aviso é o que faltou quando os campos sumiram: sem ele, lista incompleta
# parece defeito da tela, e ninguém sabe onde procurar.
checar("cn-problemas" in js and "padrao_ausentes" in js,
       "a tela avisa quando a descoberta de campos veio incompleta")
checar("sem leitura" in js and "k.lido" in js,
       "e marca o campo que a conta de serviço não conseguiu ler")
checar("cn-recarregar" in js and "carregarCampos(true)" in js,
       "tem botão para refazer a descoberta — a lista fica 30 min em cache, e sem "
       "isso corrigir permissão no ServiceNow parece não ter efeito")
checar("hierarquia" in js,
       "e mostra a cadeia de herança percorrida, que é onde este defeito morava")
checar("can_export" in js, "o botão de exportar respeita a permissão própria")
# `S.el` faz setAttribute: `disabled: false` DESABILITA, `innerHTML` não
# renderiza e `htmlFor` não vira `for`. Já custou tela quebrada antes.
for erro in ("disabled:", "innerHTML:", "htmlFor"):
    checar(erro not in js, f"a tela não usa '{erro}' em S.el(), que não funciona")

principal = (RAIZ / "main.py").read_text(encoding="utf-8")
checar("from routers.sn_consulta import router" in principal, "o módulo é registrado no main")
i = principal.index("from routers.sn_consulta import router")
checar("try:" in principal[max(0, i - 400):i], "dentro de try/except — não derruba o portal")
cfg = (RAIZ / "config.py").read_text(encoding="utf-8")
checar('"automacoes": ("view", "export", "admin")' in cfg,
       "e 'export' consta em MODULE_ACTIONS, senão a tela de acessos descarta em silêncio")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Consulta de chamados íntegra.")
