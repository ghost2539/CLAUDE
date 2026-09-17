#!/usr/bin/env python3
"""Verificação da consulta ao BASE_REMOVIDA pela tela.

    python3 scripts/verificar_ebs_consulta.py

Sem banco Oracle: o que se verifica aqui é a barreira. A consulta é só de
leitura, e a recusa tem de acontecer ANTES de qualquer coisa sair do
portal — depois já é tarde. A camada de acesso é substituída por uma de
mentira, que registra o que teria sido executado.
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
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

# Camada de acesso de mentira: registra o que receberia e devolve linhas.
executado: dict = {}
falso = types.ModuleType("integracoes.ebs_oracle")


def _query(sql, binds=None, max_rows=200, read_only=True):
    executado.update(sql=sql, binds=binds, max_rows=max_rows, read_only=read_only)
    return [{"serie": "HF550123456", "loja": 464}] * min(3, max_rows)


falso.query = _query
sys.modules["integracoes.ebs_oracle"] = falso

cliente = TestClient(main.app)
_, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


def consultar(**corpo):
    return cliente.post("/api/ebs-oracle/consultar", json=corpo)


print("\n[1] O caminho feliz")
r = consultar(sql="select * from apps.csi_item_instances where instance_number = :serie",
              binds={"serie": "HF550123456"}, limite=50)
checar(r.status_code == 200, f"HTTP 200 ({r.status_code})")
d = r.json()
checar(d["total"] == 3 and d["colunas"] == ["serie", "loja"],
       "devolve linhas e nomes de coluna")
checar(executado["binds"] == {"serie": "HF550123456"},
       "o parâmetro vai como bind variable, não concatenado no SQL")
checar(executado["max_rows"] == 50, "o limite pedido chega à camada de acesso")
checar(":serie" in executado["sql"], "e o SQL vai como foi escrito")
checar(isinstance(d["ms"], int), "informa quanto demorou")

print("\n[2] Só leitura — a recusa é antes de sair do portal")
for sql, motivo in (
    ("update apps.csi_item_instances set loja = 1", "UPDATE"),
    ("delete from apps.csi_item_instances", "DELETE"),
    ("insert into apps.x values (1)", "INSERT"),
    ("begin meu_pacote.faz_algo; end;", "bloco PL/SQL"),
    ("truncate table apps.x", "TRUNCATE"),
    ("  merge into apps.x using dual on (1=1)", "MERGE"),
):
    executado.clear()
    resp = consultar(sql=sql)
    checar(resp.status_code == 422, f"{motivo} é recusado ({resp.status_code})")
    checar(not executado, f"e nada chega ao banco no caso do {motivo}")

executado.clear()
resp = consultar(sql="select 1 from dual; delete from apps.x")
checar(resp.status_code == 422 and not executado,
       "duas consultas na mesma caixa são recusadas — a segunda escaparia da checagem")

print("\n[3] O que é aceito")
for sql, motivo in (
    ("select sysdate from dual", "SELECT simples"),
    ("  \n select 1 from dual ", "com espaço e quebra de linha antes"),
    ("with x as (select 1 a from dual) select * from x", "WITH"),
    ("SELECT SYSDATE FROM DUAL", "maiúsculas"),
    ("select 1 from dual;", "com ponto e vírgula no fim"),
):
    checar(consultar(sql=sql).status_code == 200, f"{motivo} passa")
checar(executado["sql"].endswith("dual"),
       "o ponto e vírgula do fim é aparado antes de executar")

print("\n[4] Limites e parâmetros")
checar(consultar(sql="select 1 from dual", limite=99999).json()["limite"] == 5000,
       "limite acima do teto é reduzido ao teto")
checar(consultar(sql="select 1 from dual", limite=0).json()["limite"] == 200,
       "limite zero cai no padrão")
checar(consultar(sql="select 1 from dual", limite="abc").json()["limite"] == 200,
       "limite não numérico cai no padrão, sem erro")
checar(consultar(sql="select 1 from dual", binds=["a"]).status_code == 422,
       "parâmetros que não são objeto são recusados")
checar(consultar(sql="select 1 from dual", binds={"nome errado": 1}).status_code == 422,
       "nome de parâmetro inválido é recusado")
checar(consultar(sql="").status_code == 422, "consulta vazia é recusada")
checar(consultar(sql="select 1 from dual" + " x" * 20000).status_code == 422,
       "consulta grande demais é recusada")
d3 = consultar(sql="select 1 from dual", limite=3).json()
checar(d3["truncado"] is True,
       "quando enche o limite, avisa que pode haver mais")

print("\n[5] Permissão")
anon = TestClient(main.app)
checar(anon.post("/api/ebs-oracle/consultar", json={"sql": "select 1 from dual"}
                 ).status_code in (401, 403), "sem sessão não consulta")
_, c2 = security.create_session({"username": "comum", "is_admin": False,
                                 "permission_map": {}})
comum = TestClient(main.app)
comum.cookies.set("spare_session", c2)
checar(comum.post("/api/ebs-oracle/consultar", json={"sql": "select 1 from dual"}
                  ).status_code == 403, "sem admin, 403 — é base de produção de outra área")

print("\n[6] A credencial passa pelo loader do cofre")
import inspect  # noqa: E402
import integracoes  # noqa: E402
fonte = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("from core.cofre import obter" in fonte,
       "o módulo do EBS resolve segredo por core.cofre.obter")
checar("os.getenv(\"ORACLE_EBS_PASS\"" not in fonte and "os.environ[" not in fonte,
       "e não lê variável de ambiente por conta própria, passando por fora do loader")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Consulta ao BASE_REMOVIDA íntegra.")
