#!/usr/bin/env python3
"""Verificação da pasta de consultas do EBS e das POs por projeto.

    python3 scripts/verificar_consultas_ebs.py

Duas coisas juntas, porque uma existe por causa da outra:

*   **A pasta `consultas/ebs/`.** As consultas moravam num dicionário
    `QUERIES` dentro de `integracoes/ebs_oracle.py`. Aquele módulo faz
    `import oracledb` no topo, então num servidor sem o driver a tela ficava
    sem a lista — e o contorno era arrancar o dicionário do arquivo-fonte
    com regex e rodar `exec` nele. Aqui se confere que o `exec` sumiu, que a
    pasta é a MESMA para a tela e para a execução, e que nome de consulta
    não vira caminho de arquivo.

*   **`orcamento_po_do_projeto`**, que responde "quais POs este projeto tem,
    quanto valem, e já viraram nota fiscal?". O SQL não roda aqui (não há
    Oracle nem acesso à base do EBS): o que se confere é o contrato —
    parâmetro validado, erro traduzido, e nada gravado.
"""
from __future__ import annotations

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

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


PASTA = RAIZ / "consultas" / "ebs"

print("[1] As consultas estão em arquivo, não no código")
checar(PASTA.is_dir(), "a pasta consultas/ebs/ existe")
arquivos = sorted(PASTA.glob("*.sql"))
checar(len(arquivos) >= 13, f"com as consultas dentro ({len(arquivos)} arquivos)")

acesso = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("QUERIES: dict[str, str] = {" not in acesso,
       "o dicionário literal saiu de integracoes/ebs_oracle.py")
checar("carregar_consultas(" in acesso, "e no lugar dele ficou o carregador da pasta")

router = (RAIZ / "routers" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("exec(" not in router,
       "routers/ebs_oracle.py não roda mais exec() no próprio arquivo-fonte")
checar("QUERIES: dict" not in router and "re.search(r\"QUERIES" not in router,
       "nem arranca o dicionário por regex")
# O ponto todo: se tela e execução lessem lugares diferentes, a tela ofereceria
# uma consulta e o portal rodaria outra — ou nenhuma.
checar('"consultas" / "ebs"' in router and '"consultas" / "ebs"' in acesso,
       "tela e execução leem a MESMA pasta")

print("\n[2] Nome de consulta não vira caminho de arquivo")
# `run_named` recebe o nome; um dia ele vem de uma tela. "../../etc/passwd"
# não pode virar leitura de arquivo, e nem "sub/pasta".
checar("_RE_NOME_CONSULTA" in acesso and "_RE_NOME_CONSULTA" in router,
       "os dois lados filtram o nome pelo mesmo padrão")
padrao = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
for ruim in ("../segredo", "a/b", ".hidden", "Abc", "a" * 62, "", "a-b"):
    checar(not padrao.match(ruim), f"{ruim!r} não passa como nome de consulta")
for bom in ("po_itens", "orcamento_po_do_projeto", "saldo"):
    checar(bool(padrao.match(bom)), f"{bom!r} passa")

print("\n[3] O carregador lê o que está na pasta")
escopo = {"_P": Path, "re": re, "__file__": str(RAIZ / "integracoes" / "ebs_oracle.py")}
bloco = acesso[acesso.index("PASTA_CONSULTAS ="):acesso.index("def run_named(")]
exec(bloco, escopo)  # noqa: S102 — é o próprio trecho sob verificação
QUERIES, BINDS = escopo["QUERIES"], escopo["BINDS"]
checar(len(QUERIES) == len(arquivos), "uma consulta por arquivo .sql")
checar(set(QUERIES) == {a.stem for a in arquivos}, "e o nome do arquivo é o nome da consulta")
checar(BINDS["po_itens"] == ("numero_po", "liberacao"),
       "os binds saem do próprio SQL, na ordem em que aparecem")
# Um `:coisa` escrito num comentário viraria campo no formulário da tela.
checar("p_project_number" in BINDS["orcamento_po_do_projeto"]
       and len(BINDS["orcamento_po_do_projeto"]) == 1,
       "comentário não gera bind fantasma")

print("\n[4] As consultas antigas atravessaram inteiras")
# A migração foi mecânica; o risco é ter perdido um pedaço de SQL no caminho.
for nome, marca in (("saldo", "PA_BUDGET_LINES"), ("po", "PO_LINE_LOCATIONS_ALL"),
                    ("rc", "po_requisition_headers_all"), ("acordos", "BLANKET"),
                    ("busca_po", "PO_ACTION_HISTORY"), ("catalogo", "ROW_NUMBER() OVER"),
                    ("po_itens", "quantidade_pendente"),
                    ("ativo_por_serial", "FA_ADDITIONS_B"),
                    ("vendor_lookup", "PO_VENDORS"), ("vendor_items", "MTL_SYSTEM_ITEMS_B")):
    checar(marca in QUERIES.get(nome, ""), f"{nome} chegou inteira ({marca})")
for nome, sql in QUERIES.items():
    sem_comentario = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
    checar(sem_comentario.strip().upper().startswith(("SELECT", "WITH")),
           f"{nome} continua sendo só leitura (começa em SELECT/WITH)")

print("\n[5] POs do projeto: o que a consulta promete")
po = QUERIES["orcamento_po_do_projeto"]
checar("AP_INVOICE_DISTRIBUTIONS_ALL" in po and "AP_INVOICES_ALL" in po,
       "a NF é achada pela distribuição da PO, que é o que amarra os dois")
checar("'Executada'" in po and "'Em andamento'" in po,
       "os dois status do pedido estão na consulta")
checar("EXISTS (SELECT 1 FROM nf" in po,
       "e 'Executada' é exatamente 'tem NF', não status de aprovação")
checar("nf_chave" in po and "[0-9]{44}" in po,
       "a chave da NF é procurada, e só vale o que tem 44 dígitos")
checar(po.count("global_attribute") >= 20,
       "varre os vinte GLOBAL_ATTRIBUTE — qual guarda a chave varia por instalação")
# A armadilha do rateio: somar a PO inteira num projeto que paga metade.
checar("JOIN proj p ON p.project_id = pd.project_id" in po,
       "as linhas contadas são as da distribuição DESTE projeto")
checar("ROWNUM" not in po, "sem ROWNUM pendurado em join — aquilo duplicava linha")
checar("cancel_flag" in po, "linha cancelada não entra no valor")

itens = QUERIES["orcamento_po_itens"]
checar("JOIN proj p ON p.project_id = pd.project_id" in itens,
       "o detalhe usa o mesmo recorte do resumo, senão as somas não batem")
checar(":numero_po" in itens and ":p_project_number" in itens,
       "e pede projeto E PO — o detalhe é de uma PO dentro de um projeto")

print("\n[6] Descobrir onde está a chave da NF nesta instalação")
desc = QUERIES["nf_onde_esta_a_chave"]
checar(desc.count("GLOBAL_ATTRIBUTE") >= 20, "conta as notas por coluna candidata")
checar(":p_meses" in desc, "com janela de tempo, para não varrer a tabela inteira")

print("\n[7] O endpoint valida, traduz o erro e não grava nada")
from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import routers.controle_orcamento_exec as co  # noqa: E402

app = main.app
cliente = TestClient(app)
_, cookie = security.create_session(
    {"username": "verificador", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)

for ruim in ("a b", "x" * 60, "proj;drop", "../etc"):
    r = cliente.get(f"/api/controle-orcamento-exec/ebs/projeto/{ruim}/pos")
    checar(r.status_code in (404, 422),
           f"projeto {ruim!r} é recusado antes de ir ao banco ({r.status_code})")

chamadas: list[tuple] = []


def _ebs_de_mentira(consulta, binds, max_rows=2000):
    chamadas.append((consulta, dict(binds)))
    return [{
        "projeto_numero": "120345", "projeto_nome": "Projeto de teste",
        "po_numero": "2570313", "fornecedor": "FORN LTDA",
        "status_po": "APPROVED", "moeda": "BRL", "qtd_itens": 2,
        "qtd_total": 30, "qtd_recebida": 30, "valor_total": 45000,
        "nf_qtd": 1, "nf_numero": "12345",
        "nf_chave": "12" * 22, "nf_valor": 45000,
        "status_execucao": "Executada",
    }, {
        "projeto_numero": "120345", "projeto_nome": "Projeto de teste",
        "po_numero": "2570314", "fornecedor": "OUTRA SA",
        "status_po": "APPROVED", "moeda": "BRL", "qtd_itens": 1,
        "qtd_total": 5, "qtd_recebida": 0, "valor_total": 8000,
        "nf_qtd": 0, "nf_numero": None, "nf_chave": None, "nf_valor": None,
        "status_execucao": "Em andamento",
    }, {
        # NF lançada e chave não encontrada: o caso que a tela precisa
        # separar de "não tem NF", senão alguém conclui que a nota não existe.
        "projeto_numero": "120345", "projeto_nome": "Projeto de teste",
        "po_numero": "2570315", "fornecedor": "TERCEIRA ME",
        "status_po": "APPROVED", "moeda": "BRL", "qtd_itens": 1,
        "qtd_total": 2, "qtd_recebida": 2, "valor_total": 1200,
        "nf_qtd": 1, "nf_numero": "999", "nf_chave": None, "nf_valor": 1200,
        "status_execucao": "Executada",
    }]


co._chamar_ebs = _ebs_de_mentira
r = cliente.get("/api/controle-orcamento-exec/ebs/projeto/120345/pos")
checar(r.status_code == 200, f"projeto válido responde 200 ({r.status_code})")
d = r.json()
checar(chamadas and chamadas[0][0] == "orcamento_po_do_projeto",
       "chama a consulta pelo NOME — o SQL não é montado aqui")
checar(chamadas[0][1] == {"p_project_number": "120345"},
       "e o projeto entra como bind variable, não concatenado")
checar(d["total"] == 3 and d["executadas"] == 2 and d["em_andamento"] == 1,
       "conta executadas e em andamento")
checar(d["nf_sem_chave"] == 1,
       "e separa 'tem NF e não achei a chave' de 'não tem NF' — são coisas diferentes")

# Nada gravado: é o pedido explícito ("não altere os dados do dashboard ainda").
fonte_co = (RAIZ / "routers" / "controle_orcamento_exec.py").read_text(encoding="utf-8")
trecho = fonte_co[fonte_co.index("def ebs_pos_do_projeto"):]
for escrita in ("s.add(", "s.commit(", "session.add(", "INSERT", "UPDATE "):
    checar(escrita not in trecho, f"o endpoint não faz {escrita.strip()} — só consulta")


def _ebs_sem_credencial(consulta, binds, max_rows=2000):
    from fastapi import HTTPException
    raise HTTPException(503, "ORACLE_EBS_DSN não está definido.")


co._chamar_ebs = _ebs_sem_credencial
r = cliente.get("/api/controle-orcamento-exec/ebs/projeto/120345/pos")
checar(r.status_code == 503, f"sem credencial é 503, não 502 ({r.status_code})")
checar("ORACLE_EBS_DSN" in r.json().get("detail", ""), "e diz qual chave falta")

# O tradutor de erro do módulo passa pela máscara: mensagem de driver traz o
# endereço da base de produção de outra área dentro dela.
checar("sem_dado_de_acesso" in fonte_co,
       "erro do driver passa pela máscara antes de chegar à tela")

print("\n[8] A tela de Configurações tem o campo")
jsx = (RAIZ / "frontend/controle-orcamento-exec/src/App.jsx").read_text(encoding="utf-8")
checar("POs do projeto (EBS)" in jsx, "o cartão existe na aba Configurações")
checar("/ebs/projeto/" in jsx and "/pos" in jsx, "e chama o endpoint")
checar("Não grava nada e não altera o dashboard" in jsx,
       "a tela diz que não mexe no dashboard — era a condição do pedido")
checar("Chave da NF" in jsx and "Situação" in jsx,
       "as colunas pedidas estão lá: chave da NF e situação")
checar("não encontrada" in jsx,
       "e a tela distingue chave não encontrada de PO sem NF")
checar('import { Fragment,' in jsx,
       "Fragment é importado — <React.Fragment> sem React quebra o build")
bundle = RAIZ / "static/controle-orcamento-exec/app.js"
checar(bundle.is_file() and "POs do projeto (EBS)" in bundle.read_text(encoding="utf-8"),
       "e o bundle foi reconstruído com o cartão dentro")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Consultas do EBS íntegras.")
