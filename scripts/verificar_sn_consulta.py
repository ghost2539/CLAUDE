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


def _falso_get(caminho, params, timeout=60):
    chamadas.append({"caminho": caminho, "params": dict(params)})
    q = params.get("sysparm_query", "")

    if caminho == "/api/now/table/sys_db_object":
        nome = q.split("name=")[-1]
        mae = {"incident": "Task", "sc_req_item": "Task", "task": ""}.get(nome, "")
        return {"result": [{"super_class": mae}]}

    if caminho == "/api/now/table/sys_dictionary":
        tabelas = q.split("nameIN")[1].split("^")[0].split(",")
        tabelas = [t.strip().lower() for t in tabelas]
        return {"result": [
            {"element": el, "column_label": rot, "internal_type": tipo, "name": tab}
            for el, rot, tipo, tab in DICIONARIO if tab.lower() in tabelas
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
        elif "LIKE" in parte and not parte.startswith("NOT"):
            campo, alvo = parte.split("LIKE", 1)
            linhas = [l for l in linhas if alvo in str(l.get(campo, ""))]
        elif "=" in parte:
            campo, alvo = parte.split("=", 1)
            linhas = [l for l in linhas if str(l.get(campo, "")) == alvo]
    return linhas


sc._get = _falso_get
sc._cache_campos.clear()


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


print("\n[2] Os campos são os que a conta de serviço LÊ, não os do dicionário")
d2 = cliente.get("/api/sn-consulta/campos?tabela=incident").json()
nomes = [c["campo"] for c in d2["campos"]]
checar("short_description" in nomes,
       "campo herdado da task aparece — a hierarquia é percorrida")
checar("category" in nomes, "e o campo da própria incident também")
checar(SEM_ACESSO not in nomes,
       f"{SEM_ACESSO} está no dicionário e a ACL nega: fica de fora")
checar(d2["total"] == len(d2["campos"]) and d2["total"] == len(DICIONARIO) - 1,
       "a contagem bate com o que sobrou")
checar(d2["conta"] == "zabbix", "a tela diz qual conta respondeu por esses campos")
checar(sc._cfg.SN_API_PASS not in cliente.get("/api/sn-consulta/campos?tabela=incident").text,
       "e a senha da conta não sai em lugar nenhum")
checar(cliente.get("/api/sn-consulta/campos?tabela=sys_user").status_code == 422,
       "tabela fora da lista é recusada")

# Contraprova da sonda: sem ela, o campo barrado passaria.
so_dicionario = [c["campo"] for c in sc._campos_do_dicionario("incident")]
checar(SEM_ACESSO in so_dicionario,
       "contraprova: sem a sonda, o campo barrado entraria na lista")


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
recusa([{"campo": SEM_ACESSO, "operador": "igual", "valor": "x"}],
       "nem filtrar por campo que a conta não lê")
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
