#!/usr/bin/env python3
"""Verificação do fluxo depois do lançamento da internalização.

    python3 scripts/verificar_fluxo_internalizacao.py

O lançamento diz QUAL ativo é cada serial. Daí em diante o equipamento
percorre duas etapas, uma tela cada:

  Patrimônio — espera o serial aparecer no EBS. Renner e Camicado têm o
  patrimônio criado lá; Youcom compra por fora e alguém confirma no botão.

  Entrada de Equipamento — o técnico informa espaço e corredor, o ativo sobe
  no ServiceNow e o equipamento entra no estoque do portal.

O que se prova, e por quê:

- A etapa é de CADA equipamento. Numa nota com dez desktops, se sete
  aparecerem no EBS e três não, os sete seguem. Segurar todos pelo atraso de
  um é o que faz fila parar sem motivo.
- Quem tem patrimônio no EBS NÃO pode ser confirmado à mão: seria pular a
  conferência que existe justamente para pegar serial trocado.
- Concluir a entrada cria o ativo e o ciclo no banco do portal. Sem isso,
  "entrou em estoque" seria só uma palavra na tela — o equipamento não
  apareceria na Consulta nem poderia ser separado.
- ServiceNow fora do ar NÃO desfaz o estoque: o equipamento está fisicamente
  na prateleira, e deixá-lo invisível aqui seria pior que a marcação
  pendente. A resposta avisa, em vez de dizer "concluído" e esconder
  trabalho.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
for chave, valor in {
    "DATABASE_URL": f"sqlite:///{_TMP/'portal.db'}",
    "PORTAL_SESSION_SECRET": "verificacao-local-sem-valor",
    "PORTAL_COFRE_DIR": str(_TMP / "cofre"),
    "INTERNALIZACAO_DATABASE_URL": f"sqlite:///{_TMP/'int.db'}",
}.items():
    os.environ[chave] = valor

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
from sqlalchemy import select  # noqa: E402

import db.internalizacao as di  # noqa: E402
import db.portal as dp  # noqa: E402
import integracoes.ebs_oracle as ebs  # noqa: E402
import main  # noqa: E402
import routers.recebimento as rec  # noqa: E402

di.init_db()
dp.init_db()

with di.SessionLocal.begin() as s:
    for i, (bu, serie) in enumerate([("Renner", "SN-R1"), ("Renner", "SN-R2"),
                                     ("Youcom", "SN-Y1")], start=1):
        proc = di.Processo(agendamento_id=100 + i, bu=bu, fornecedor="ACME",
                           nf="NF-1", status="CONCLUIDA")
        proc.ativos.append(di.Ativo(descricao=f"Coletor {i}", numero_serie=serie))
        s.add(proc)
    # Lançamento ainda PENDENTE não entra no fluxo: só entra o que foi concluído.
    aberto = di.Processo(agendamento_id=999, bu="Renner", nf="NF-9", status="PENDENTE")
    aberto.ativos.append(di.Ativo(descricao="Ainda lançando", numero_serie="SN-ABERTO"))
    s.add(aberto)

cli = TestClient(main.app)
_, cookie = security.create_session(
    {"username": "tecnico", "is_admin": True, "permission_map": {}})
cli.cookies.set("spare_session", cookie)


print("\n[1] Patrimônio: só o que foi lançado, separado por quem tem EBS")
d = cli.get("/api/internalizacao/fluxo/patrimonio").json()
checar(d["total"] == 3, f"3 equipamentos esperando ({d['total']})")
checar("SN-ABERTO" not in [x["numero_serie"] for x in d["itens"]],
       "lançamento ainda pendente NÃO entra no fluxo")
checar(d["aguardando_ebs"] == 2, f"2 esperam o EBS ({d['aguardando_ebs']})")
checar(d["aguardando_confirmacao"] == 1,
       f"1 espera confirmação manual ({d['aguardando_confirmacao']})")

ids_youcom = [x["id"] for x in d["itens"] if not x["tem_ebs"]]
ids_renner = [x["id"] for x in d["itens"] if x["tem_ebs"]]


print("\n[2] Confirmação manual: só para quem não tem patrimônio no EBS")
r = cli.post("/api/internalizacao/fluxo/patrimonio/confirmar", json={"ids": ids_youcom})
checar(r.status_code == 200 and r.json()["confirmados"] == ids_youcom,
       "Youcom é confirmada à mão")
r = cli.post("/api/internalizacao/fluxo/patrimonio/confirmar", json={"ids": ids_renner})
checar(r.status_code == 422,
       f"Renner NÃO pode ser confirmada à mão ({r.status_code}) — tem EBS para conferir")
checar(cli.post("/api/internalizacao/fluxo/patrimonio/confirmar",
                json={"ids": []}).status_code == 422, "sem ninguém escolhido: 422")


print("\n[3] Consulta ao EBS: um por um, e quem não aparece continua esperando")
ebs.run_named = lambda nome, binds=None, max_rows=200: (
    [{"ativo": "AT-999", "plaqueta": "PL-999", "descricao": "Coletor TC22"}]
    if (binds or {}).get("numero_serie") == "SN-R1" else [])
r = cli.post("/api/internalizacao/fluxo/patrimonio/consultar")
corpo = r.json()
checar(r.status_code == 200, f"consulta responde ({r.status_code})")
checar(corpo["consultados"] == 2, f"consultou só os 2 de Renner ({corpo['consultados']})")
checar(corpo["encontrados"] == 1, f"achou 1 ({corpo['encontrados']})")
restante = cli.get("/api/internalizacao/fluxo/patrimonio").json()
checar(restante["total"] == 1 and restante["itens"][0]["numero_serie"] == "SN-R2",
       "o que o EBS não achou continua esperando, sozinho")


print("\n[4] Entrada de equipamento")
d = cli.get("/api/internalizacao/fluxo/entrada").json()
checar(d["total"] == 2, f"2 prontos: o achado no EBS e o confirmado à mão ({d['total']})")
achado = [x for x in d["itens"] if x["numero_serie"] == "SN-R1"]
checar(bool(achado) and achado[0]["ebs_ativo"] == "AT-999",
       "o que veio do EBS traz o número do ativo")
ids_prontos = [x["id"] for x in d["itens"]]
r = cli.post("/api/internalizacao/fluxo/entrada",
             json={"ids": ids_prontos, "espaco_corredor": "   "})
checar(r.status_code == 422, f"sem espaço e corredor: 422 ({r.status_code})")

rec._marcar_no_servicenow = lambda itens, req, espaco="": {
    "ativo": True, "criados": len(itens), "falhas": []}
r = cli.post("/api/internalizacao/fluxo/entrada",
             json={"ids": ids_prontos, "espaco_corredor": "A-12"})
checar(r.status_code == 200, f"concluir ({r.status_code})")
checar(sorted(r.json()["concluidos"]) == sorted(ids_prontos), "os dois concluídos")
checar(not r.json().get("aviso"), "sem aviso quando o ServiceNow confirma")


print("\n[5] Entrou MESMO no estoque do portal")
with dp.SessionLocal() as ps:
    ativos = ps.scalars(select(dp.Asset)).all()
    ciclos = ps.scalars(select(dp.ReceiptCycle)).all()
    movs = ps.scalars(select(dp.Movement)).all()
checar(len(ativos) == 2, f"2 ativos no estoque ({len(ativos)})")
checar({a.serial_number for a in ativos} == {"SN-R1", "SN-Y1"},
       "os seriais certos")
checar(all(a.source == "INTERNALIZACAO" for a in ativos),
       "marcados com a origem, para se saber de onde vieram")
checar(len(ciclos) == 2 and all(c.status == "RECEBIDO" for c in ciclos),
       "com ciclo de recebimento — é o que os faz aparecer na tela de Recebimentos")
checar(len(movs) == 2 and all("A-12" in (m.note or "") for m in movs),
       "e com o movimento registrando o espaço e corredor")
checar(cli.get("/api/internalizacao/fluxo/entrada").json()["total"] == 0,
       "a fila de entrada esvaziou")


print("\n[6] ServiceNow fora do ar não desfaz o estoque")
with di.SessionLocal.begin() as s:
    proc = di.Processo(agendamento_id=300, bu="Youcom", nf="NF-2", status="CONCLUIDA")
    proc.ativos.append(di.Ativo(descricao="Extra", numero_serie="SN-X"))
    s.add(proc)
d = cli.get("/api/internalizacao/fluxo/patrimonio").json()
novos = [x["id"] for x in d["itens"] if x["numero_serie"] == "SN-X"]
cli.post("/api/internalizacao/fluxo/patrimonio/confirmar", json={"ids": novos})


def _explode(itens, req, espaco=""):
    raise RuntimeError("ServiceNow fora")


rec._marcar_no_servicenow = _explode
d = cli.get("/api/internalizacao/fluxo/entrada").json()
r = cli.post("/api/internalizacao/fluxo/entrada",
             json={"ids": [x["id"] for x in d["itens"]], "espaco_corredor": "B-3"})
checar(r.status_code == 200, f"conclui mesmo com o ServiceNow fora ({r.status_code})")
checar(bool(r.json().get("aviso")),
       "e AVISA que a marcação ficou pendente, em vez de dizer só 'concluído'")
with dp.SessionLocal() as ps:
    checar(len(ps.scalars(select(dp.Asset)).all()) == 3,
           "o equipamento entrou no estoque assim mesmo — ele está na prateleira")


print("\n[7] Permissão e menu")
anon = TestClient(main.app)
for metodo, rota in (("get", "/api/internalizacao/fluxo/patrimonio"),
                     ("post", "/api/internalizacao/fluxo/patrimonio/consultar"),
                     ("get", "/api/internalizacao/fluxo/entrada"),
                     ("post", "/api/internalizacao/fluxo/entrada")):
    r = getattr(anon, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
    checar(r.status_code in (401, 403), f"{rota} exige sessão ({r.status_code})")
html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
for rota in ("internalizacao/lancamento", "internalizacao/patrimonio",
             "internalizacao/entrada"):
    checar(f'data-route="{rota}"' in html, f"o menu tem {rota}")
js = (RAIZ / "modulos" / "internalizacao.js").read_text(encoding="utf-8")
checar("telaPatrimonio" in js and "telaEntrada" in js, "as duas telas existem no módulo")
sql = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
checar('"ativo_por_serial"' in sql and "FA_ADDITIONS_B" in sql,
       "a consulta do serial no EBS está registrada")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Fluxo íntegro: cada equipamento anda sozinho, e concluir a entrada "
      "coloca o ativo no estoque de verdade.")
