#!/usr/bin/env python3
"""A rotina encerra o que é de encerrar, e não encaminha tudo.

    python3 scripts/verificar_automacao_encerramento.py

Roda contra SQLite temporário, com ServiceNow e Correios de mentira.
Nenhuma chamada sai da máquina.

O defeito que motivou isto
--------------------------
Há dois fluxos, e quem decide é a SUBCATEGORIA: uma regra encaminha, outra
encerra. No botão "Executar agora" a rotina só encaminhava, nunca encerrava.

A causa estava em `_match_regra`: o casamento é tolerante (por "contém", para
aguentar variação de texto vinda do ServiceNow) e devolvia a PRIMEIRA regra
que batia. Com "Coletor" (encaminhar) antes de "Coletor - Entrega ao usuário"
(encerrar), todo chamado de coletor caía na primeira — "coletor" está contido
em "coletor - entrega ao usuário".

Agora vence a regra MAIS ESPECÍFICA: igual > contido > contém, e apelido mais
longo desempata. A ordem configurada só decide empate de verdade.
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
os.environ["AUTOMACOES_DATABASE_URL"] = f"sqlite:///{_TMP/'automacoes.db'}"

from fastapi import HTTPException  # noqa: E402

import routers.automacoes as ra  # noqa: E402

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


ENCAMINHA = {"nome": "Encaminhar coletores", "acao": "encaminhar", "ativo": True,
             "ordem": 10, "subcategorias": "Coletor",
             "fila_destino": "SPARE-MOBILIDADE", "mensagem": "segue para mobilidade"}
ENCERRA = {"nome": "Encerrar entrega de coletor", "acao": "encerrar", "ativo": True,
           "ordem": 20, "subcategorias": "Coletor - Entrega ao usuário",
           "mensagem": "entregue ao usuário"}
REGRAS = [ENCAMINHA, ENCERRA]

print("[1] A subcategoria escolhe o fluxo — a mais específica vence")
casos = (
    ("Coletor", "Encaminhar coletores", "a genérica pega o caso genérico"),
    ("Coletor - Entrega ao usuário", "Encerrar entrega de coletor",
     "a específica NÃO é engolida pela genérica"),
    ("COLETOR - ENTREGA AO USUÁRIO", "Encerrar entrega de coletor",
     "caixa alta não muda a decisão"),
    ("coletor_-_entrega_ao_usuário", "Encerrar entrega de coletor",
     "underscore no lugar do espaço também casa"),
)
for subcat, esperado, desc in casos:
    r = ra._match_regra(subcat, REGRAS) or {}
    checar(r.get("nome") == esperado, f"{desc} ({subcat!r} → {r.get('nome', 'nenhuma')!r})")

checar(ra._match_regra("Impressora", REGRAS) is None,
       "subcategoria sem regra não casa com nada")
checar(ra._match_regra("", REGRAS) is None, "subcategoria vazia não casa")

print("\n[2] A ordem configurada só decide empate de verdade")
# Invertendo a ordem, a específica continua ganhando: não é a posição que manda.
r = ra._match_regra("Coletor - Entrega ao usuário", [ENCERRA, ENCAMINHA]) or {}
checar(r.get("nome") == "Encerrar entrega de coletor",
       "inverter a ordem não muda quem ganha por especificidade")
# Dois apelidos idênticos: aí sim vale quem vem primeiro.
A = {"nome": "Primeira", "acao": "encerrar", "ativo": True, "subcategorias": "Monitor"}
B = {"nome": "Segunda", "acao": "encaminhar", "ativo": True, "subcategorias": "Monitor"}
checar((ra._match_regra("Monitor", [A, B]) or {}).get("nome") == "Primeira",
       "empate exato: vence a ordem configurada")
checar((ra._match_regra("Monitor", [B, A]) or {}).get("nome") == "Segunda",
       "e respeita a ordem invertida")

print("\n[3] Regra desativada não entra")
desativada = dict(ENCERRA)
desativada["ativo"] = False
r = ra._match_regra("Coletor - Entrega ao usuário", [ENCAMINHA, desativada]) or {}
checar(r.get("nome") == "Encaminhar coletores",
       "com a específica desligada, sobra a genérica")

print("\n[4] A rodada inteira: encerra o que é de encerrar")
import db.automacoes as dba  # noqa: E402

dba.init_db()
# `init_db` semeia duas regras padrão (Access Point → encaminhar, Equipamentos
# → encerrar). Aqui o conjunto precisa ser só o do cenário, senão a semente
# entra na conta e a conferência mede outra coisa.
with dba.SessionLocal.begin() as _s:
    for _r in _s.scalars(dba.select(dba.Regra)).all():
        _s.delete(_r)
for reg in (ENCAMINHA, ENCERRA):
    dba.salvar_regra(dict(reg))
checar(len(dba.listar_regras()) == 2,
       f"o cenário tem só as duas regras ({[r['nome'] for r in dba.listar_regras()]})")

INCIDENTES = [
    {"number": "INC001", "sys_id": "a" * 32, "subcategory": "Coletor - Entrega ao usuário",
     "state": "3", "sys_tags": "AA123456789BR"},
    {"number": "INC002", "sys_id": "b" * 32, "subcategory": "Coletor",
     "state": "2", "sys_tags": "AA223456789BR"},
    {"number": "INC003", "sys_id": "c" * 32, "subcategory": "Coletor - Entrega ao usuário",
     "state": "2", "sys_tags": ""},                       # sem rastreio
    {"number": "INC004", "sys_id": "d" * 32, "subcategory": "Impressora",
     "state": "2", "sys_tags": "AA423456789BR"},          # sem regra
    {"number": "INC005", "sys_id": "e" * 32, "subcategory": "Coletor - Entrega ao usuário",
     "state": "2", "sys_tags": "AA523456789BR"},          # não entregue
]

escritas: list[tuple[str, dict]] = []

ra._sn_query_all = lambda session, tabela, query, fields: list(INCIDENTES)
ra._sn_update = lambda session, tabela, sys_id, campos: escritas.append((sys_id, dict(campos)))
ra._grupo_sys_id = lambda session, nome: "g" * 32


def _query_falsa(session, tabela, query, fields, limite, display_value=True):
    """Releitura da conferência: devolve o que a última escrita gravou."""
    sys_id = query.split("=", 1)[1]
    atual = {}
    for sid, campos in escritas:
        if sid == sys_id:
            atual.update(campos)
    return [atual]


ra._sn_query = _query_falsa
ra.consultar_rastreio = lambda codigo: {"codigo": codigo, "eventos": [
    {"data": "2026-09-17T10:00:00", "codigo": "BDE", "tipo": "01",
     "descricao": "Objeto entregue ao destinatário"}
]} if codigo != "AA523456789BR" else {"codigo": codigo, "eventos": [
    {"data": "2026-09-17T10:00:00", "codigo": "BDE", "tipo": "02",
     "descricao": "Objeto saiu para entrega ao destinatário"}
]}

resumo = ra._rodar(session=None, origem="verificacao", usuario="teste")

checar(resumo["analisados"] == 5, f"analisou os 5 ({resumo['analisados']})")
checar(resumo["encerrados"] == 1, f"encerrou o de entrega ao usuário ({resumo['encerrados']})")
checar(resumo["encaminhados"] == 1, f"encaminhou o genérico ({resumo['encaminhados']})")
checar(resumo["erros"] == 0, f"sem erro ({resumo['erros']}): {resumo['acoes']}")
acoes = {a["number"]: a["acao"] for a in resumo["acoes"]}
checar(acoes.get("INC001") == "encerrar", f"INC001 foi ENCERRADO ({acoes.get('INC001')})")
checar(acoes.get("INC002") == "encaminhar", f"INC002 foi encaminhado ({acoes.get('INC002')})")

# A transição do encerramento: Em Espera vira Em Andamento antes de Resolvido.
por_inc = {}
for sid, campos in escritas:
    por_inc.setdefault(sid, []).append(campos)
estados = [c.get("state") for c in por_inc["a" * 32] if "state" in c]
checar(estados == ["2", "6"], f"INC001 passou por Em Andamento antes de Resolvido ({estados})")
fechamento = [c for c in por_inc["a" * 32] if c.get("state") == "6"][0]
checar(fechamento.get("close_code") == ra.CLOSE_CODE, "com o código de fechamento")
checar(fechamento.get("close_notes") == "entregue ao usuário", "e a mensagem da regra")

print("\n[5] O resumo diz POR QUE cada um foi ignorado")
motivos = resumo["motivos"]
checar(resumo["ignorados"] == 3, f"três ignorados ({resumo['ignorados']})")
checar(motivos["sem_rastreio"] == 1, f"um sem rastreio ({motivos['sem_rastreio']})")
checar(motivos["sem_regra"] == 1, f"um sem regra ({motivos['sem_regra']})")
checar(motivos["nao_entregue"] == 1, f"um ainda não entregue ({motivos['nao_entregue']})")
checar(resumo["por_regra"].get("Encerrar entrega de coletor [encerrar]") == 1,
       f"o resumo diz qual regra pegou quantos ({resumo['por_regra']})")

print("\n[6] Várias regras convivendo: cada subcategoria com o seu apontamento")
# É o uso real: não são duas regras, são muitas, e equipamento diferente tem
# apontamento diferente. O risco com muitas regras não e "existe regra?", e
# sim "QUAL pega?" — por isso a especificidade decide, e existe o testador.
MUITAS = [
    {"nome": "Access Point → DISPATCH", "acao": "encaminhar", "ativo": True, "ordem": 10,
     "subcategorias": "access_point\nAccess Point", "fila_destino": "REMOTO_DISPATCH"},
    {"nome": "Coletor → encerrar", "acao": "encerrar", "ativo": True, "ordem": 20,
     "subcategorias": "Coletor\ncoletor"},
    {"nome": "Coletor avariado → bancada", "acao": "encaminhar", "ativo": True, "ordem": 30,
     "subcategorias": "Coletor - Avariado", "fila_destino": "SPARE-BANCADA"},
    {"nome": "SLED → encerrar", "acao": "encerrar", "ativo": True, "ordem": 40,
     "subcategorias": "RFID Sled\nSled RFID\nSLED"},
    {"nome": "Sled sem leitura → fornecedor", "acao": "encaminhar", "ativo": True, "ordem": 50,
     "subcategorias": "Sled RFID - Sem leitura", "fila_destino": "FORNECEDOR"},
    {"nome": "Teclado fiscal → encerrar", "acao": "encerrar", "ativo": True, "ordem": 60,
     "subcategorias": "Teclado fiscal\ntax_keyboard"},
]
esperado = (
    ("Access Point", "Access Point → DISPATCH"),
    ("Coletor", "Coletor → encerrar"),
    ("Coletor - Avariado", "Coletor avariado → bancada"),
    ("Sled RFID", "SLED → encerrar"),
    ("Sled RFID - Sem leitura", "Sled sem leitura → fornecedor"),
    ("tax_keyboard", "Teclado fiscal → encerrar"),
)
for subcat, nome in esperado:
    r = ra._match_regra(subcat, MUITAS) or {}
    checar(r.get("nome") == nome,
           f"{subcat!r} → {nome!r} (veio {r.get('nome', 'nenhuma')!r})")

# A regra específica ganha da genérica mesmo vindo DEPOIS na ordem, e a
# genérica continua valendo para o caso dela. As duas convivem.
checar((ra._match_regra("Coletor - Avariado", MUITAS) or {}).get("acao") == "encaminhar"
       and (ra._match_regra("Coletor", MUITAS) or {}).get("acao") == "encerrar",
       "genérica e específica do mesmo equipamento convivem, cada uma no seu caso")

print("\n[7] O testador responde qual regra pega, sem tocar em chamado")
with dba.SessionLocal.begin() as _s:
    for _r in _s.scalars(dba.select(dba.Regra)).all():
        _s.delete(_r)
for reg in MUITAS:
    dba.salvar_regra(dict(reg))


class _Req:
    headers: dict = {}
    client = None


ra.get_session = lambda req: {"username": "verificacao"}

d = ra.regras_testar(_Req(), subcategoria="Coletor - Avariado")
checar(d["vencedora"]["nome"] == "Coletor avariado → bancada",
       f"aponta a vencedora ({d['vencedora']['nome']})")
checar(d["vencedora"]["acao"] == "encaminhar"
       and d["vencedora"]["fila_destino"] == "SPARE-BANCADA",
       "e diz o que ela faria")
outras = [c["nome"] for c in d["candidatas"] if c["nome"] != d["vencedora"]["nome"]]
checar("Coletor → encerrar" in outras,
       f"mostra a genérica que também casaria ({outras})")

d2 = ra.regras_testar(_Req(), subcategoria="Monitor")
checar(d2["vencedora"] is None and not d2["candidatas"],
       "subcategoria sem regra é dita como tal")

try:
    ra.regras_testar(_Req(), subcategoria="   ")
    checar(False, "subcategoria vazia deveria ser recusada")
except HTTPException as exc:
    checar(exc.status_code == 422, f"subcategoria vazia recusada ({exc.status_code})")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Automação de encerramento com problema:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("Automação íntegra: a subcategoria escolhe o fluxo e o encerramento acontece.")
