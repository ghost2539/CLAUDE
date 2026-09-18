#!/usr/bin/env python3
"""Verificação da coleta dos chamados de técnico de campo.

    python3 scripts/verificar_campo_lojas.py

`scripts/chamados_campo_lojas.py` é consulta avulsa: roda uma vez, entrega
uma planilha, e o número dela vira decisão. Não há tela onde o erro apareça
— por isso o caminho inteiro é exercitado aqui contra um ServiceNow de
mentira, antes de rodar contra o de verdade.

Os cinco jeitos de essa coleta sair errada sem dar erro:

*   **Chamado contado várias vezes.** Uma `task` tem uma linha de `task_sla`
    por SLA. Sem deduplicar, o mesmo atendimento entra três, quatro vezes na
    contagem do mês.
*   **Filtrar o histórico por `tablename=task`.** O `sys_audit` guarda a
    tabela REAL (`incident`, `sc_req_item`); `task` não casa com nada, e o
    resultado é zero para todo mundo.
*   **A fila gravada por sys_id** no histórico. Comparar com o nome não casa
    e dá zero em tudo.
*   **Fechar a conta no encerramento** em vez da resolução. O encerramento é
    automático dias depois e infla o tempo.
*   **A ordem do E/OU na query.** `^OR` agrupa com a condição anterior; com
    as datas antes das filas, o período valeria só para a última fila.
"""
from __future__ import annotations

import csv
import io
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Antes de qualquer import do projeto: `chamados_campo_lojas` define
# DATABASE_URL=:memory: no import dele, e o `:memory:` usa um pool que o
# engine do portal recusa. Um arquivo real resolve, e nada é gravado nele.
_TMP = Path(tempfile.mkdtemp())
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP/'portal.db'}"
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


import scripts.chamados_campo_lojas as cl  # noqa: E402
# A lógica é do router; o script só a transporta.
import routers.sn_campo_lojas as regra  # noqa: E402

FILA = "TI_N2_FLD_ENACEL_LOJAS"
OUTRA_DAS_QUATRO = "TI_N2_FLD_SKY_LOJAS"
FORA = "TI_N1_SERVICE_DESK"
ID_FILA = "9" * 32          # a fila gravada por sys_id no histórico

# 1..4 na fila (com DOIS SLAs cada, para exercitar a deduplicação);
# 5 fora das quatro filas; 6 na fila e sem histórico de troca.
SLA = []
for i in range(1, 7):
    grupo = {5: FORA}.get(i, FILA if i != 3 else OUTRA_DAS_QUATRO)
    for _ in range(2):
        SLA.append({
            "task.sys_id": f"{i:032x}", "task.number": f"INC{i:07d}",
            "task.opened_at": f"2025-0{i}-01 08:00:00",
            "task.caller_id": f"Fulano {i}", "task.opened_by": "",
            "task.requested_for": "", "task.category": "Hardware",
            "task.subcategory": "Loja", "task.assignment_group.name": grupo,
            "task.state": "7",
            "task.resolved_at": f"2025-0{i}-03 08:00:00",
            # Encerrado UMA SEMANA depois: se a conta usar isto, infla.
            "task.closed_at": f"2025-0{i}-10 08:00:00",
        })

consultas: list[dict] = []


def _falso(tabela, query, campos, *, display=True, limite=100000):
    consultas.append({"tabela": tabela, "query": query, "campos": campos})
    if tabela == "task_sla":
        return SLA
    if tabela == "sys_audit":
        ids = set(query.split("documentkeyIN")[1].split("^")[0].split(","))
        # O chamado 6 não tem troca de fila nenhuma.
        return [{"documentkey": s, "oldvalue": FORA, "newvalue": ID_FILA,
                 "sys_created_on": f"2025-0{int(s, 16):01d}-01 10:00:00"}
                for s in sorted(ids) if int(s, 16) <= 4]
    if tabela == "sys_user_group":
        return [{"sys_id": ID_FILA, "name": FILA}]
    raise AssertionError(f"tabela inesperada: {tabela}")


cl.consultar = _falso
cl.preparar_conta = lambda *a, **k: None

SAIDA = _TMP / "campo.csv"
sys.argv = ["x", "--desde", "2025-01-01", "--ate", "2025-06-30",
            "--saida", str(SAIDA)]
cl.main()
linhas = list(csv.reader(io.StringIO(SAIDA.read_text(encoding="utf-8").lstrip("﻿")),
                         delimiter=";"))
cabecalho = linhas[0]
# O resumo, no fim do arquivo, também começa com o mês. Sem cortar no
# marcador, as linhas dele entram na contagem de chamados.
corte = next((i for i, l in enumerate(linhas)
              if l and l[0].startswith("RESUMO")), len(linhas))
dados = [l for l in linhas[1:corte] if l and l[0].startswith("2025-")]
do_resumo = linhas[corte:]
por_num = {l[1]: dict(zip([c for c, _r in cl.COLUNAS], l)) for l in dados}


print("\n[1] As colunas pedidas, e o mês")
for rotulo in ("Mês", "Chamado", "Aberto em", "Solicitante", "Categoria",
               "Subcategoria", "Horas na fila"):
    checar(rotulo in cabecalho, f"coluna {rotulo}")
checar(por_num["INC0000001"]["mes"] == "2025-01", "o mês sai da abertura do chamado")

print("\n[2] Um chamado por linha, mesmo com vários SLAs")
# Doze linhas de SLA, seis chamados, cinco nas filas pedidas.
checar(len(dados) == 5, f"cinco chamados nas filas ({len(dados)})")
checar(len(set(l[1] for l in dados)) == len(dados),
       "nenhum chamado repetido — a task_sla traz uma linha por SLA")
checar("INC0000005" not in por_num,
       "e o chamado de fila fora das quatro fica de fora")

print("\n[3] Cada chamado é medido na fila DELE")
checar(por_num["INC0000003"]["fila"] == OUTRA_DAS_QUATRO,
       "são quatro filas; a do chamado é a que vale")
checar(por_num["INC0000001"]["fila"] == FILA, "e a dos outros também")

print("\n[4] A conta fecha na RESOLUÇÃO")
# Entra na fila dia 01 às 10h, resolve dia 03 às 8h = 46h.
# Pelo encerramento (dia 10) seriam 214h.
checar(float(por_num["INC0000001"]["horas_na_fila"]) == 46.0,
       f"46h ({por_num['INC0000001']['horas_na_fila']}), não 214")
checar(por_num["INC0000001"]["fim_origem"] == "resolvido",
       "e a linha diz de onde veio a data de fim")
checar("2025-01-03" in por_num["INC0000001"]["fim_contagem"],
       "com a data da resolução, não a do encerramento")

print("\n[5] O histórico é lido sem filtrar por tabela")
# sys_audit.tablename guarda `incident`/`sc_req_item`, não `task`. Filtrar
# por `task` não casaria com nada — e o resultado seria zero em tudo.
audit = [c for c in consultas if c["tabela"] == "sys_audit"]
checar(bool(audit), "o histórico é consultado")
checar(all("tablename=" not in c["query"] for c in audit),
       "sem `tablename=` na query — `task` não casaria com nada")
checar(all("fieldname=assignment_group" in c["query"] for c in audit),
       "e só as trocas de fila")

print("\n[6] Fila gravada por sys_id é traduzida antes de comparar")
# O falso ServiceNow devolve o sys_id da fila, não o nome. Sem traduzir,
# comparar com o nome não casa e TODOS dariam zero.
checar(float(por_num["INC0000002"]["horas_na_fila"]) > 0,
       "o chamado com histórico por sys_id foi medido")
grupos = [c for c in consultas if c["tabela"] == "sys_user_group"]
checar(bool(grupos), "os sys_id de grupo são resolvidos para nome")

print("\n[7] Sem troca de fila, conta a vida do chamado")
checar(por_num["INC0000006"]["base_medicao"] == "sem troca de fila",
       "o chamado sem histórico é medido por outra base, e a linha diz qual")
checar(float(por_num["INC0000006"]["horas_na_fila"]) == 48.0,
       f"da abertura à resolução ({por_num['INC0000006']['horas_na_fila']}h)")

print("\n[8] A query junta as filas em OU e o período em E")
# `^OR` agrupa com a condição anterior; um `^` seguinte começa novo grupo em
# E. Com as datas ANTES das filas, o período valeria só para a última fila —
# e viriam anos de chamados das outras três.
q = [c for c in consultas if c["tabela"] == "task_sla"][0]["query"]
checar(q.count("^OR") == 3, "as quatro filas entram em OU")
checar(q.index("opened_at>=") > q.rindex("^OR"),
       "e o período vem DEPOIS do último ^OR — senão valeria só para a última fila")
for f in cl.FILAS:
    checar(f in q, f"a fila {f} está na consulta")
checar("nameLIKE" in q, "casadas por trecho do nome, como pedido")

print("\n[9] O resumo por mês e fila")
checar(any("RESUMO POR MES E FILA" in (l[0] if l else "") for l in do_resumo),
       "o arquivo traz o resumo por mês e fila")
linhas_resumo = [l for l in do_resumo if len(l) == 5 and l[0].startswith("2025-")]
checar(len(linhas_resumo) == 5, f"uma linha por mês e fila ({len(linhas_resumo)})")
checar(all(l[2] and l[3] for l in linhas_resumo),
       "com a contagem de chamados e as horas somadas")

print("\n[10] O dot-walk volta em três formatos, e os três são lidos")
# `sysparm_fields=task.sys_id` pode voltar como chave pontilhada, aninhado
# na referência, ou só a referência crua. Ler um formato só descarta a linha
# inteira em silêncio nos outros dois — e o efeito não é erro: é arquivo com
# cabeçalho e nada. Foi o que aconteceu.
FORMAS = [
    ({"task.sys_id": "a" * 32}, "chave pontilhada"),
    ({"task": {"sys_id": "a" * 32}}, "aninhado na referência"),
    ({"task": {"value": "a" * 32, "display_value": "INC1"}}, "só a referência"),
]
for linha, nome in FORMAS:
    checar(regra.campo(linha, "task.sys_id") == "a" * 32, f"sys_id lido: {nome}")
checar(regra.campo({"task": {"value": "x"}}, "task.number") == "",
       "e o que não veio devolve vazio, sem inventar")

# Montagem completa a partir do formato aninhado — o que o código antigo
# descartava inteiro.
aninhado = [{"task": {"value": "b" * 32, "display_value": "INC0000099"},
             "task.number": "INC0000099",
             "task.opened_at": "2025-02-01 08:00:00",
             "task.caller_id": "Fulano",
             "task.assignment_group.name": FILA,
             "task.state": "7",
             "task.resolved_at": "2025-02-03 08:00:00",
             "task.closed_at": "2025-02-10 08:00:00"}]
montados = regra.montar_chamados(aninhado)
checar(len(montados) == 1,
       "contraprova: no formato aninhado o chamado é montado (antes: zero)")
checar(montados[0]["numero"] == "INC0000099" and montados[0]["fila"] == FILA,
       "com número e fila certos")

print("\n[11] Zero chamados: o arquivo diz POR QUÊ")
# As causas levam a ações opostas, e só os números as separam: 0 linhas de
# SLA é consulta/permissão; linhas de SLA e 0 chamados é campo não lido.
diag = regra.montar_chamados([])
checar(diag == [], "sem linhas, nenhum chamado")
fonte = (RAIZ / "routers" / "sn_campo_lojas.py").read_text(encoding="utf-8")
checar("NENHUM CHAMADO MONTADO - diagnostico" in fonte,
       "o arquivo traz um bloco de diagnóstico em vez de sair vazio")
checar("chaves que a API devolveu" in fonte,
       "listando as CHAVES da primeira linha — é o que mostra o formato do dot-walk")
checar("query usada" in fonte,
       "e a query, quando nem linha de SLA veio")
checar("le a tabela task_sla" in fonte,
       "apontando a permissão como uma das causas")

print("\n[12] O botão exporta só a LISTA, e não é streaming")
# Com StreamingResponse o HTTP 200 e os cabeçalhos saem antes da primeira
# linha: um erro depois disso não vira mensagem na tela, vira download
# truncado. Foi assim que a exportação chegou vazia SEM informação de erro.
from core import security  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import routers.sn_consulta as _sc  # noqa: E402

_sc._cfg.SN_API_USER, _sc._cfg.SN_API_PASS = "zabbix", "senha-que-nao-pode-sair"
cliente = TestClient(main.app)
with cliente:
    _, ck = security.create_session(
        {"username": "verificador", "is_admin": True, "permission_map": {}})
    cliente.cookies.set("spare_session", ck)

    linhas_sla = [{"task": {"value": f"{i:032x}"}, "task.number": f"INC{i:07d}",
                   "task.opened_at": "2025-02-01 08:00:00",
                   "task.caller_id": "Fulano", "task.category": "Hardware",
                   "task.subcategory": "Loja",
                   "task.assignment_group.name": FILA, "task.state": "7"}
                  for i in range(1, 4)]
    regra._get = lambda c, p, timeout=60: {
        "result": linhas_sla if p.get("sysparm_offset", "0") == "0" else []}
    r = cliente.post("/api/sn-consulta/campo-lojas/chamados",
                     json={"desde": "2025-01-01", "ate": "2025-03-31"})
    checar(r.status_code == 200, f"HTTP 200 ({r.status_code})")
    checar(r.headers.get("X-Chamados") == "3",
           "o cabeçalho diz quantos vieram — a tela avisa sem abrir o arquivo")
    corpo = r.text.lstrip("\ufeff")
    checar("Horas na fila" not in corpo,
           "a lista NÃO traz tempo de fila: ele sai depois, pela aba Consulta")
    checar(corpo.splitlines()[0].startswith("Mês;Chamado;Aberto em;Solicitante;"
                                            "Categoria;Subcategoria"),
           "com as colunas pedidas")
    checar("RESUMO POR MES E FILA" in corpo, "e a contagem por mês e fila")
    checar("cole a coluna Chamado" in corpo,
           "e diz como obter o tempo em fila na segunda passada")
    # Só uma consulta: sem histórico, sem sys_user_group. É o que a torna rápida.
    chamadas = []
    regra._get = lambda c, p, timeout=60: (
        chamadas.append(c) or {"result": linhas_sla if p.get("sysparm_offset", "0") == "0" else []})
    cliente.post("/api/sn-consulta/campo-lojas/chamados",
                 json={"desde": "2025-01-01", "ate": "2025-03-31"})
    checar(all("task_sla" in c for c in chamadas),
           f"só a task_sla é consultada ({len(chamadas)} chamada(s))")
    checar(not any("sys_audit" in c for c in chamadas),
           "sem tocar no histórico — é o que fazia a passada longa")

    print("\n[13] Falha vira MENSAGEM, não arquivo vazio")
    def _nega(c, p, timeout=60):
        raise HTTPException(502, "A conta de serviço não tem acesso a esta tabela (403).")

    regra._get = _nega
    r2 = cliente.post("/api/sn-consulta/campo-lojas/chamados",
                      json={"desde": "2025-01-01", "ate": "2025-03-31"})
    checar(r2.status_code == 502,
           f"erro no ServiceNow vira erro HTTP ({r2.status_code}), não 200 com nada")
    checar("403" in r2.json().get("detail", ""),
           "com o motivo, que a tela mostra em toast")
    fonte_r = (RAIZ / "routers" / "sn_campo_lojas.py").read_text(encoding="utf-8")
    trecho = fonte_r[fonte_r.index("def exportar_chamados"):
                     fonte_r.index('@router.post("/exportar")')]
    # A CHAMADA, não a palavra: o comentário da própria função explica por que
    # o streaming saiu, e procurar o nome solto acusava o texto que explica.
    checar("StreamingResponse(" not in trecho and "return Response(" in trecho,
           "a rota da lista devolve resposta pronta, sem streaming — é o "
           "streaming que engolia o erro")

    print("\n[14] Zero chamados: 200 com diagnóstico, e a tela avisa")
    regra._get = lambda c, p, timeout=60: {"result": []}
    r3 = cliente.post("/api/sn-consulta/campo-lojas/chamados",
                      json={"desde": "2025-01-01", "ate": "2025-03-31"})
    checar(r3.status_code == 200 and r3.headers.get("X-Chamados") == "0",
           "vazio é 200 com a contagem zerada no cabeçalho")
    checar("NENHUM CHAMADO MONTADO" in r3.text and "query usada" in r3.text,
           "e o arquivo traz o diagnóstico, não silêncio")

js = (RAIZ / "modulos" / "servicenow_automacoes.js").read_text(encoding="utf-8")
checar("campo-lojas/chamados" in js, "o botão chama a rota da lista")
checar("X-Chamados" in js, "e lê a contagem do cabeçalho")
checar("Nenhum chamado montado" in js,
       "avisando quando vier zero — antes o arquivo vazio passava despercebido")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Coleta de campo/lojas íntegra.")
