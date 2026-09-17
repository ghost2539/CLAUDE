#!/usr/bin/env python3
"""Verificação da leitura da base do EBS.

    python3 scripts/verificar_ebs_oracle.py

Não há banco Oracle aqui, e não é disso que se trata. O que se prova é o
contorno da conexão, que é onde mora o risco:

  • a credencial vem do COFRE, nunca de os.environ direto;
  • endereço, instância, usuário e senha NÃO aparecem em resposta nenhuma —
    nem quando o próprio erro do driver os traz dentro;
  • a tela só executa consulta NOMEADA, com bind variables;
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


from core import cofre, security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

cliente = TestClient(main.app)
_, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)


print("\n[1] Sem credencial: diz o que falta e NÃO tenta conectar")
d = cliente.get("/api/ebs-oracle/situacao").json()
checar(d["completo"] is False, "a tela sabe que a credencial está incompleta")
nomes = {c["rotulo"]: c for c in d["chaves"]}
checar({"Endereço do banco (DSN)", "Usuário", "Senha"} <= set(nomes),
       "as três chaves obrigatórias aparecem na tela")
checar(all(not nomes[r]["resolvida"] for r in ("Endereço do banco (DSN)", "Usuário", "Senha")),
       "nenhuma resolvida, porque o cofre está vazio")
r = cliente.post("/api/ebs-oracle/testar")
checar(r.status_code == 503, f"testar sem credencial: 503, não 502 ({r.status_code})")
checar("ORACLE_EBS_DSN" in r.json()["detail"] and "cofre.py definir" in r.json()["detail"],
       "e a mensagem diz qual chave gravar e como")


print("\n[2] A credencial vem do cofre, e o cofre é a única porta")
fonte_mod = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("from core.cofre import obter" in fonte_mod,
       "a camada de acesso resolve segredo por core.cofre.obter")
checar(not re.search(r"os\.(environ|getenv)", fonte_mod),
       "e não lê os.environ em lugar nenhum")
# O original trazia DSN e usuário escritos como padrão. Isso é dado de
# acesso: some do código, e no repositório fica só o NOME da chave.
checar(not re.search(r"(dsn|user)\"?\s*[:=]\s*\"[^\"]*:1521", fonte_mod, re.I),
       "nenhum endereço de banco escrito como padrão no código")
for arquivo in ("integracoes/ebs_oracle.py", "routers/ebs_oracle.py",
                "static/modules/parametros.js", ".env.example"):
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    checar(not re.search(r"\b\w+[-.\w]*:1521/\w+", texto),
           f"{arquivo} não tem endereço de banco escrito")


print("\n[3] Só-leitura, com teto de linhas e de tempo")
checar("SET TRANSACTION READ ONLY" in fonte_mod, "cada consulta abre transação só-leitura")
checar("conn.rollback()" in fonte_mod, "e a conexão nunca comita — rollback no finally")
checar("autocommit = False" in fonte_mod, "autocommit desligado")
checar("call_timeout" in fonte_mod, "timeout por chamada, para não travar sessão no banco")
checar("fetchmany(max_rows)" in fonte_mod, "teto de linhas por consulta")


print("\n[4] SQL digitado na tela não existe")
fonte_router = (RAIZ / "routers" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("def sql_livre" not in fonte_mod, "a camada não expõe sql_livre()")
checar('body).get("sql"' not in fonte_router, "o endpoint não aceita SQL no corpo")
r = cliente.post("/api/ebs-oracle/consultar", json={"sql": "SELECT 1 FROM dual"})
checar(r.status_code == 422, f"mandar SQL é recusado ({r.status_code})")
r = cliente.post("/api/ebs-oracle/consultar", json={})
checar(r.status_code == 422, "sem nome de consulta, 422")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "nao_existe"})
checar(r.status_code == 422, "nome fora da lista, 422")


print("\n[5] As consultas nomeadas, com os binds derivados do próprio SQL")
d = cliente.get("/api/ebs-oracle/consultas").json()
nomes = {q["nome"]: q for q in d["consultas"]}
checar(set(nomes) == {"saldo", "po", "rc", "acordos", "vendor_lookup",
                      "vendor_items", "busca_po", "catalogo"},
       "as oito consultas de negócio estão lá")
checar(nomes["busca_po"]["binds"] == ["numero_po", "p_line_num"],
       "binds de busca_po saem do SQL, não de uma lista à parte")
checar(nomes["po"]["binds"] == ["p_project_number"], "binds de po")
for q in d["consultas"]:
    if ":" in q["sql"]:
        checar(bool(q["binds"]), f"{q['nome']}: o SQL usa bind variable")
        break
# Bind que a consulta não usa é erro de quem chamou: o driver recusaria
# com uma mensagem muito pior.
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "po", "binds": {"inventado": 1}})
checar(r.status_code == 422 and "inventado" in r.json()["detail"],
       "bind que a consulta não usa é recusado, dizendo quais ela espera")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "po", "binds": {"1; DROP": 1}})
checar(r.status_code == 422, "nome de bind que não é identificador é recusado")


print("\n[6] Com credencial gravada: nada do que está no cofre aparece")
cofre.definir("ORACLE_EBS_DSN", DSN)
cofre.definir("ORACLE_EBS_USER", USUARIO)
cofre.definir("ORACLE_EBS_PASS", SENHA)
r = cliente.get("/api/ebs-oracle/situacao")
d = r.json()
checar(d["completo"] is True, "agora a credencial está completa")
nomes = {c["rotulo"]: c for c in d["chaves"]}
checar(nomes["Endereço do banco (DSN)"]["fonte"].startswith("cofre"),
       "e a tela diz que veio do cofre")
checar(all("valor" not in nomes[r] for r in ("Endereço do banco (DSN)", "Usuário", "Senha")),
       "DSN, usuário e senha não trazem o valor — nem o usuário, que não é senha "
       "mas é dado de acesso")
sem_segredo(r.text, "/situacao")


print("\n[7] O erro do driver traz o endereço dentro; a tela, não")
# É o caso real: ORA-12154 e ORA-12541 citam o DSN. Aqui o host não existe,
# então o driver falha na resolução de nome — com o endereço no texto.
r = cliente.post("/api/ebs-oracle/testar")
checar(r.status_code == 502, f"falha de conexão vira 502 ({r.status_code})")
sem_segredo(r.text, "erro de conexão")
r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "vendor_lookup"})
checar(r.status_code == 502, "consulta sem banco também vira 502")
sem_segredo(r.text, "erro de consulta")


print("\n[8] Catálogo: só com prefixo, e só catálogo")
r = cliente.get("/api/ebs-oracle/objetos?prefixo=PO")
checar(r.status_code == 422, "prefixo curto demais é recusado antes de sair")
r = cliente.get("/api/ebs-oracle/objetos?prefixo=PO_HEADERS&owner=A;DROP")
checar(r.status_code == 422, "owner que não é identificador é recusado")
r = cliente.get("/api/ebs-oracle/descrever?objeto=x;drop")
checar(r.status_code == 422, "nome de objeto inválido é recusado")
checar("all_objects" in fonte_mod and "all_tab_columns" in fonte_mod,
       "o catálogo lê all_objects/all_tab_columns — dicionário, não tabela de negócio")


print("\n[9] Permissão")
anon = TestClient(main.app)
for metodo, rota in (("get", "/api/ebs-oracle/situacao"),
                     ("get", "/api/ebs-oracle/consultas"),
                     ("get", "/api/ebs-oracle/objetos?prefixo=PO_HEADERS"),
                     ("post", "/api/ebs-oracle/testar"),
                     ("post", "/api/ebs-oracle/consultar")):
    r = getattr(anon, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
    checar(r.status_code in (401, 403), f"{rota} exige sessão ({r.status_code})")
comum = TestClient(main.app)
_, ck = security.create_session(
    {"username": "sem-admin", "is_admin": False, "permission_map": {}})
comum.cookies.set("spare_session", ck)
checar(comum.get("/api/ebs-oracle/situacao").status_code == 403,
       "sem admin, 403 — é base de produção de outra área")


print("\n[10] Registro e tela")
checar('MODULO = "parametros"' in fonte_router,
       "usa a permissão de Parâmetros, e não uma chave nova sem tela")
principal = (RAIZ / "main.py").read_text(encoding="utf-8")
checar("from routers.ebs_oracle import router" in principal
       and "ebs_oracle" in principal.split("try:")[-1] or "ebs_oracle_router" in principal,
       "registrado em main.py")
js = (RAIZ / "static/modules/parametros.js").read_text(encoding="utf-8")
for trecho, desc in (("/ebs-oracle/situacao", "mostra a situação das chaves"),
                     ("/ebs-oracle/testar", "tem o botão de testar conexão"),
                     ("/ebs-oracle/consultas", "lista as consultas nomeadas"),
                     ("/ebs-oracle/consultar", "executa pelo nome"),
                     ("/ebs-oracle/objetos", "procura no catálogo")):
    checar(trecho in js, f"parametros.js {desc}")
checar(not re.search(r"#[0-9a-fA-F]{3,6}\b", js.split("renderBaseEbs")[1].split("async function")[0]),
       "a tela não tem cor fixa — só tokens do padrão")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Leitura da base do EBS íntegra: credencial pelo cofre, nada de acesso na tela.")
