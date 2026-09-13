#!/usr/bin/env python3
"""Verificação de Projetos de Loja (A16).

Roda contra bancos temporários, sem ServiceNow — as funções da Separação
que falam com ele são substituídas por dublês que anotam o que fariam:

    python3 scripts/verificar_projetos.py

O que se confere aqui é a máquina de estados do item e o que ela deixa
no núcleo: pausa vira EXTERNO, bancada vira TRATATIVA, fila vira FILA,
envio encerra, e em nenhum momento um token tem dois relógios abertos.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TEMP = tempfile.mkdtemp(prefix="projetos-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["SEPARACAO_DATABASE_URL"] = f"sqlite:///{_TEMP}/separacao.db"
os.environ["PROJETOS_DATABASE_URL"] = f"sqlite:///{_TEMP}/projetos.db"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402

import db.trilha as dbt  # noqa: E402
import db.projetos as db  # noqa: E402
import routers.separacao as sep  # noqa: E402
import routers.projetos as prj  # noqa: E402
from routers.trilha import abrir_ativo, mover  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond: bool, descricao: str) -> None:
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def esperar_409(descricao: str, funcao) -> None:
    try:
        funcao()
    except HTTPException as exc:
        checar(exc.status_code == 409, f"{descricao} (HTTP {exc.status_code})")
    else:
        checar(False, f"{descricao} (não recusou)")


# ── Dublês ─────────────────────────────────────────────────────────
anotado = {"reservas": [], "liberacoes": [], "atualizacoes": []}

sep.consultar_chamado = lambda req, numero: {
    "chamado": numero.upper(), "tipo": "RITM", "resumo": "Inauguração loja 999",
    "destino": "LOJA 999", "solicitante": "x", "estado": "2"}
sep.localizar_no_estoque = lambda req, serial, tipo: {"sys_id": "sys-" + serial}


def _reserva(req, sys_id, serial, reservar):
    (anotado["reservas"] if reservar else anotado["liberacoes"]).append(serial)


sep._escrever_reserva = _reserva

import routers.servicenow as sn  # noqa: E402
sn._sn_session_from_portal = lambda req: object()
sn._get_http = lambda: (None, None)
sn._lookup_reference = lambda session, tabela, nome, cache, BS: "loc-999"


def _update(session, tabela, sys_id, alteracao):
    anotado["atualizacoes"].append((sys_id, dict(alteracao)))
    return True


sn._sn_update = _update

prj.require_permission = lambda req, modulo, acao: {"username": "tester"}
prj.check_rate_limit = lambda req: None
REQ = object()

import db.separacao as dbsep  # noqa: E402
dbt.init_db()
dbsep.init_db()   # api_enviar lê os parâmetros de envio da Separação
db.init_db()
dbt.gravar_config({"expediente_dias": "0,1,2,3,4,5,6", "expediente_inicio": "00:00",
                   "expediente_fim": "23:59", "feriados": "", "fuso_horas": "0"})


# ── Leitura do núcleo ──────────────────────────────────────────────
def token(serial: str):
    with dbt.SessionLocal() as s:
        a = s.execute(select(dbt.Ativo).where(dbt.Ativo.serial == serial)).scalar_one_or_none()
        if a is None:
            return None
        abertos = s.execute(select(dbt.Intervalo).where(
            dbt.Intervalo.ativo_id == a.id, dbt.Intervalo.fim.is_(None))).scalars().all()
        todos = s.execute(select(dbt.Intervalo).where(
            dbt.Intervalo.ativo_id == a.id).order_by(dbt.Intervalo.id)).scalars().all()
        return {"estado": a.estado_fisico, "encerrado": a.encerrado,
                "abertos": [(i.estado, i.tipo, i.usuario) for i in abertos],
                "tipos": [(i.estado, i.tipo) for i in todos]}


def item(numero, n):
    return prj.api_detalhe(numero, REQ)["itens"][n]


# ══════════════════════════════════════════════════════════════════
print("\n[1] Criação: projeto nasce do chamado, item nasce em AG_DEFINICAO como EXTERNO")
d = prj.api_criar(prj.ProjetoIn(chamado="ritm0001", tipo="INAUGURACAO",
                                itens=[prj.ItemIn(modelo="TC21", quantidade=2, area="Frente"),
                                       prj.ItemIn(modelo="AP 505", quantidade=1)]), REQ)
NUM = d["numero"]
checar(NUM.startswith("PRJ-"), f"número {NUM}")
checar(d["loja"] == "LOJA 999", "loja veio do chamado")
checar(d["estado"] == "PLANEJAMENTO", "projeto em planejamento")
checar(len(d["itens"]) == 2 and all(i["estado"] == "AG_DEFINICAO" for i in d["itens"]),
       "dois itens em AG_DEFINICAO")
t0 = token(d["itens"][0]["token"])
# Só TRATATIVA tem dono no intervalo: pausa e fila não são de ninguém.
checar(t0 is not None and t0["abertos"] == [("AG_DEFINICAO", dbt.EXTERNO, "")],
       "token no núcleo com um relógio EXTERNO aberto, sem dono")
checar(all(i["pausado"] for i in d["itens"]), "definição conta como pausa")
checar(d["itens"][0]["prazo"] is not None, "prazo calculado")

esperar_409("segundo projeto no mesmo chamado é recusado",
            lambda: prj.api_criar(prj.ProjetoIn(chamado="RITM0001"), REQ))

print("\n[2] Definir: item entra na fila (FILA), projeto sai do planejamento")
i0 = item(NUM, 0)
esperar_409("assumir separação antes de definir é recusado",
            lambda: prj.api_assumir_separacao(NUM, i0["id"], REQ))
d = prj.api_definir(NUM, i0["id"], REQ)
checar(d["estado"] == "EM_ANDAMENTO", "projeto em andamento")
checar(item(NUM, 0)["estado"] == "AG_SEPARACAO_PROJ", "item em AG_SEPARACAO_PROJ")
t = token(i0["token"])
checar(t["abertos"] == [("AG_SEPARACAO_PROJ", dbt.FILA, "")], "relógio de fila, sem dono")
checar(t["tipos"][0] == ("AG_DEFINICAO", dbt.EXTERNO), "intervalo de definição fechado e mantido")

print("\n[3] Separar: TRATATIVA com dono; bipe reserva antes de gravar")
prj.api_assumir_separacao(NUM, i0["id"], REQ)
t = token(i0["token"])
checar(t["abertos"] == [("EX_SEPARACAO_PROJ", dbt.TRATATIVA, "tester")], "relógio de bancada com dono")
checar(item(NUM, 0)["responsavel"] == "tester", "responsável gravado")

# Um equipamento de verdade já na trilha, para conferir que ele também anda.
with dbt.SessionLocal() as s:
    a = abrir_ativo(s, serial="SER1", usuario="rec", modelo="TC21")
    mover(s, a, estado="DISPONIVEL", tipo=dbt.FILA, processo="A08", usuario="rec")
    s.commit()

esperar_409("concluir separação sem as unidades é recusado",
            lambda: prj.api_concluir_separacao(NUM, i0["id"], REQ))
prj.api_bipar(NUM, i0["id"], prj.BipeIn(serial="ser1"), REQ)
checar(anotado["reservas"] == ["SER1"], "reserva escrita no ServiceNow")
checar(token("SER1")["abertos"] == [("EX_SEPARACAO_PROJ", dbt.TRATATIVA, "tester")],
       "equipamento real moveu na trilha")
esperar_409("mesma série duas vezes é recusada",
            lambda: prj.api_bipar(NUM, i0["id"], prj.BipeIn(serial="SER1"), REQ))
prj.api_bipar(NUM, i0["id"], prj.BipeIn(serial="SER2"), REQ)
esperar_409("terceira unidade num item de 2 é recusada",
            lambda: prj.api_bipar(NUM, i0["id"], prj.BipeIn(serial="SER3"), REQ))
checar(item(NUM, 0)["separados"] == 2, "duas unidades no item")

print("\n[4] Pausa por estoque: EXTERNO; retomar volta à fila")
prj.api_sem_estoque(NUM, i0["id"], prj.PausaIn(motivo="falta 1"), REQ)
t = token(i0["token"])
checar(t["abertos"] == [("AG_ESTOQUE", dbt.EXTERNO, "")], "AG_ESTOQUE é EXTERNO")
checar(item(NUM, 0)["pausado"], "item marcado como pausado")
prj.api_retomar(NUM, i0["id"], REQ)
checar(item(NUM, 0)["estado"] == "AG_SEPARACAO_PROJ", "retomou para a fila de separação")
checar(item(NUM, 0)["separados"] == 2, "unidades já bipadas continuam no item")
prj.api_assumir_separacao(NUM, i0["id"], REQ)
prj.api_concluir_separacao(NUM, i0["id"], REQ)
checar(item(NUM, 0)["estado"] == "AG_CONFIGURACAO_PROJ", "separação concluída → fila de configuração")

print("\n[5] Configuração: reprovar devolve a unidade à bancada e pausa o item")
prj.api_assumir_configuracao(NUM, i0["id"], REQ)
checar(token(i0["token"])["abertos"][0][1] == dbt.TRATATIVA, "configuração é TRATATIVA")
prj.api_reprovar(NUM, i0["id"], prj.ReprovaIn(serial="SER1", motivo="não liga"), REQ)
checar(anotado["liberacoes"] == ["SER1"], "reserva da reprovada foi solta")
checar(token("SER1")["abertos"] == [("AG_TRIAGEM", dbt.FILA, "")], "equipamento voltou à triagem")
it = item(NUM, 0)
checar(it["estado"] == "AG_REPARO_PROJ" and it["pausado"], "item em AG_REPARO_PROJ (pausa)")
checar(it["separados"] == 1 and it["devolvidas"][0]["serial"] == "SER1",
       "unidade saiu do item e ficou registrada como devolvida")
checar(token(i0["token"])["abertos"][0][1] == dbt.EXTERNO, "AG_REPARO_PROJ é EXTERNO")

print("\n[6] Repor a unidade e enviar")
prj.api_retomar(NUM, i0["id"], REQ)
prj.api_assumir_separacao(NUM, i0["id"], REQ)
prj.api_bipar(NUM, i0["id"], prj.BipeIn(serial="SER1"), REQ)   # voltou do reparo, pode de novo
checar(item(NUM, 0)["separados"] == 2, "série reprovada pode voltar ao mesmo item depois do reparo")
prj.api_concluir_separacao(NUM, i0["id"], REQ)
prj.api_assumir_configuracao(NUM, i0["id"], REQ)
prj.api_concluir_configuracao(NUM, i0["id"], REQ)
checar(item(NUM, 0)["estado"] == "PRONTO_PROJ", "pronto para envio")
esperar_409("enviar item que não está pronto é recusado",
            lambda: prj.api_enviar(NUM, item(NUM, 1)["id"], REQ))
anotado["atualizacoes"].clear()
d = prj.api_enviar(NUM, i0["id"], REQ)
checar(len(anotado["atualizacoes"]) == 2
       and all(a[1]["install_status"] == "1" and a[1]["location"] == "loc-999"
               for a in anotado["atualizacoes"]),
       "duas unidades postas em uso na loja no ServiceNow")
it = item(NUM, 0)
checar(it["estado"] == "ENVIADO_PROJ" and it["encerrado"], "item enviado e encerrado")
t = token(i0["token"])
checar(t["encerrado"] and not t["abertos"], "token do item encerrado no núcleo, sem relógio aberto")
checar(token("SER1")["estado"] == "ENTREGUE", "equipamento real entregue")
checar(d["estado"] == "EM_ANDAMENTO", "projeto segue aberto: ainda há item pendente")

print("\n[7] Cancelar o último item fecha o projeto")
i1 = item(NUM, 1)
prj.api_definir(NUM, i1["id"], REQ)
prj.api_assumir_separacao(NUM, i1["id"], REQ)
prj.api_bipar(NUM, i1["id"], prj.BipeIn(serial="SER9"), REQ)
anotado["liberacoes"].clear()
d = prj.api_cancelar_item(NUM, i1["id"], prj.EncerraIn(motivo="loja desistiu"), REQ)
checar(anotado["liberacoes"] == ["SER9"], "cancelar item solta a reserva")
checar(d["estado"] == "EM_ANDAMENTO", "cancelar item não conclui o projeto sozinho")
esperar_409("agir em item encerrado é recusado",
            lambda: prj.api_definir(NUM, i1["id"], REQ))

print("\n[8] Cancelar projeto encerra o que sobrou")
d2 = prj.api_criar(prj.ProjetoIn(chamado="RITM0002", itens=[prj.ItemIn(modelo="X")]), REQ)
d2 = prj.api_cancelar_projeto(d2["numero"], prj.EncerraIn(motivo="adiado"), REQ)
checar(d2["estado"] == "CANCELADO" and d2["itens"][0]["estado"] == "CANCELADO_PROJ",
       "projeto e item cancelados")
checar(token(d2["itens"][0]["token"])["encerrado"], "token do item cancelado encerrado no núcleo")

print("\n[9] Fila e lista")
f = prj.api_fila(REQ)
checar(all(x["estado"] not in ("ENVIADO_PROJ", "CANCELADO_PROJ") for x in f["itens"]),
       "fila não lista encerrados")
lst = prj.api_lista(REQ)
checar(lst["contagem"]["CANCELADO"] == 1 and lst["contagem"]["EM_ANDAMENTO"] == 1, "contagem por estado")

print("\n[10] Invariante: nenhum token com dois relógios abertos")
with dbt.SessionLocal() as s:
    abertos = s.execute(select(dbt.Intervalo.ativo_id).where(dbt.Intervalo.fim.is_(None))).scalars().all()
checar(len(abertos) == len(set(abertos)), "um relógio aberto por ativo, no máximo")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:")
    for f_ in falhas:
        print("  - " + f_)
    sys.exit(1)
print("Projetos de loja íntegro.")
