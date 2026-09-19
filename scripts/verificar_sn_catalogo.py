#!/usr/bin/env python3
"""O cliente do Service Catalog abre o pedido como o usuário — e só como ele."""
from __future__ import annotations

import http.server
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ.setdefault("VERIFY_SSL", "true")

falhas: list[str] = []
feitos = 0

def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)

from integracoes import http as http_saida  # noqa: E402
from integracoes import sn_catalogo as cat  # noqa: E402
from integracoes.sn_catalogo import (  # noqa: E402
    CatalogoServiceNow, ErroServiceNow, SessaoServiceNowExpirada,
    montar_variaveis_lancamento_nf, resolver_opcao,
)

TOKEN = "a1b2c3d4e5f6" * 6                       # 72 hexadecimais, como o g_ck
ITEM = "66831e771b37b5901e870e9fe54bcb11"
USUARIO = "1111111111111111aaaaaaaaaaaaaaaa"
LUCAS = "2222222222222222bbbbbbbbbbbbbbbb"
REQ = "3333333333333333cccccccccccccccc"
RITM = "4444444444444444dddddddddddddddd"
ANEXO = "5555555555555555eeeeeeeeeeeeeeee"

ESTADO = {"modo": "normal", "pagina": 0, "chamadas": [], "ultimo": {}}

def _opcoes(*pares):
    return [{"label": r, "value": v} for r, v in pares]

def _item():
    return {"sys_id": ITEM, "name": "Lançamento de NF", "short_description": "Envio de NF ao CSC",
            "variables": [
                {"name": "requested_for", "label": "Solicitado para", "type": 8, "friendly_type": "reference",
                 "mandatory": "true", "read_only": False, "choices": []},
                {"name": "u_phone", "label": "Telefone", "type": 6, "friendly_type": "single_line_text",
                 "mandatory": False, "read_only": True, "choices": []},
                {"name": "u_impact_employee", "label": "Colaborador impactado", "type": 8,
                 "friendly_type": "reference", "mandatory": True, "read_only": False},
                {"name": "question_brand", "label": "Marca", "type": 5, "friendly_type": "select_box",
                 "mandatory": True, "read_only": False,
                 "choices": _opcoes(("Renner Brasil", "renner_br"), ("Camicado", "cam"), ("Youcom", "you"))},
                {"name": "question_document_type", "label": "Tipo", "type": 5, "friendly_type": "select_box",
                 "choices": _opcoes(("Material", "mat"), ("Serviço", "serv"))},
                {"name": "question_demand_type", "label": "Demanda", "type": 5, "friendly_type": "select_box",
                 "choices": _opcoes(("Others", "oth"), ("Import", "imp"))},
                {"name": "container_po", "label": "Pedido", "type": 19, "friendly_type": "container_start",
                 "children": [
                     {"name": "question_has_a_purchase_order", "label": "Tem pedido de compra?", "type": 1,
                      "friendly_type": "yes_no", "choices": _opcoes(("Sim", "Yes"), ("Não", "No"))},
                     {"name": "question_supplier", "label": "Fornecedor", "type": 6,
                      "friendly_type": "single_line_text", "choices": []},
                 ]},
                {"name": "question_nature_of_the_transaction", "label": "Natureza", "type": 5,
                 "friendly_type": "select_box",
                 "choices": _opcoes(("Fixed assets (CAPEX)", "capex"), ("Expenses (OPEX)", "opex"))},
                {"name": "question_the_document_is_being_sent_after_the_deadline", "label": "Fora do prazo?",
                 "type": 1, "friendly_type": "yes_no", "choices": _opcoes(("Yes", "Yes"), ("No", "No"))},
                {"name": "question_number_of_documents_being_sent", "label": "Quantos documentos", "type": 5,
                 "friendly_type": "select_box", "choices": _opcoes(("1", "1"), ("2", "2"), ("3", "3"))},
                {"name": "documentos", "label": "Add", "type": "multi_row", "mandatory": True,
                 "children": [
                     {"name": "question_order_number_po", "label": "PO", "type": 6},
                     {"name": "question_document_number", "label": "Documento", "type": 6},
                     {"name": "question_due_date", "label": "Vencimento", "type": 9},
                 ]},
                {"name": "question_description", "label": "Descrição", "type": 2,
                 "friendly_type": "multi_line_text", "choices": []},
            ]}

class _SN(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):  # silêncio: o relatório é o do checar()
        pass

    def _responder(self, status, corpo=b"", tipo="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, status, obj):
        self._responder(status, json.dumps(obj).encode("utf-8"))

    def _login(self):
        self._responder(302, b"", "text/html", {"Location": "/login.do"})

    def _autenticado(self) -> bool:
        return (self.headers.get("X-UserToken") == TOKEN
                and self.headers.get("X-Requested-With") == "XMLHttpRequest")

    def _tratar(self, metodo):
        u = urlsplit(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        tam = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tam) if tam else b""
        ESTADO["chamadas"].append({"metodo": metodo, "rota": u.path, "query": q,
                                   "accept": self.headers.get("Accept", ""),
                                   "content_type": self.headers.get("Content-Type", ""),
                                   "corpo": corpo})
        ESTADO["ultimo"] = ESTADO["chamadas"][-1]

        if u.path == "/login.do":
            return self._responder(200, b"<html><title>Login</title></html>", "text/html")
        if u.path in ("/nav_to.do", "/esc"):
            ESTADO["pagina"] += 1
            if ESTADO["modo"] == "pagina_expirada":
                return self._login()
            html = ("<html><script>var NOW = {}; window.g_ck = '" + TOKEN + "';</script></html>").encode()
            return self._responder(200, html, "text/html")

        if not self._autenticado():
            return self._json(401, {"error": {"message": "User Not Authenticated"}})
        if ESTADO["modo"] == "expirado":
            return self._login()
        if ESTADO["modo"] == "401":
            return self._json(401, {"error": {"message": "User Not Authenticated"}})
        if ESTADO["modo"] == "erro500":
            return self._responder(500, b"<html>Erro interno " + b"x" * 500 + b"</html>", "text/html")

        if u.path == "/api/now/ui/user/current_user":
            return self._json(200, {"result": {"user_sys_id": USUARIO, "user_name": "fulano.tal",
                                               "user_display_name": "Fulano Tal", "user_initials": "FT"}})
        if u.path == "/api/now/table/sys_user":
            cons = q.get("sysparm_query", "")
            if cons == f"sys_id={USUARIO}":
                return self._json(200, {"result": [{"sys_id": USUARIO, "name": "Fulano Tal",
                                                    "email": "fulano.tal@exemplo.com.br", "user_name": "fulano.tal"}]})
            if cons.startswith("email=lucas.gonzaga@"):
                return self._json(200, {"result": [{"sys_id": LUCAS, "name": "Lucas Gonzaga",
                                                    "email": "lucas.gonzaga@exemplo.com.br", "user_name": "lucas.gonzaga"}]})
            return self._json(200, {"result": []})
        if u.path == f"/api/sn_sc/servicecatalog/items/{ITEM}" and metodo == "GET":
            return self._json(200, {"result": _item()})
        if u.path == f"/api/sn_sc/servicecatalog/items/{ITEM}/order_now" and metodo == "POST":
            return self._json(200, {"result": {"sys_id": REQ, "number": "REQ0099001",
                                               "request_number": "REQ0099001", "request_id": REQ}})
        if u.path == "/api/now/table/sc_req_item":
            if q.get("sysparm_query") == f"request={REQ}":
                return self._json(200, {"result": [{"sys_id": RITM, "number": "RITM0099001"}]})
            return self._json(200, {"result": []})
        if u.path == "/api/now/attachment/file" and metodo == "POST":
            return self._json(201, {"result": {"sys_id": ANEXO, "file_name": q.get("file_name"),
                                               "size_bytes": str(len(corpo))}})
        return self._json(404, {"error": {"message": "rota desconhecida no ServiceNow de mentira"}})

    def do_GET(self):
        self._tratar("GET")

    def do_POST(self):
        self._tratar("POST")

srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SN)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

def _rotas(metodo=None):
    return [c for c in ESTADO["chamadas"] if not metodo or c["metodo"] == metodo]

def _limpar(modo="normal"):
    ESTADO.update(modo=modo, pagina=0, chamadas=[], ultimo={})

def _cliente():
    ses = http_saida.sessao("sn-catalogo-teste", trust_env=False)
    ses.cookies.set("JSESSIONID", "cookie-sso-de-teste")
    return ses, CatalogoServiceNow(ses, BASE + "/")

print("[1] Token: lido de uma página, uma vez, e mandado em toda chamada")
_limpar()
ses, cli = _cliente()
checar(cli.base_url == BASE, "base_url sem a barra final")
t = cli.token()
checar(t == TOKEN, "g_ck lido do `window.g_ck = '…'` da página")
checar(cli.token() == TOKEN and ESTADO["pagina"] == 1, "segunda pedida não volta à página")
u = cli.usuario_atual()
cli.procurar_usuario("lucas.gonzaga@exemplo.com.br")
cli.descrever_item(ITEM)
checar(ESTADO["pagina"] == 1, "três chamadas de API depois, a página continua lida uma vez só")
apis = [c for c in _rotas() if c["rota"].startswith("/api/")]
checar(apis and all(c["accept"] == "application/json" for c in apis),
       "toda chamada de API pede Accept: application/json")
r_sem = ses.get(BASE + "/api/now/ui/user/current_user")
checar(r_sem.status_code == 401, "contraprova: o servidor de mentira nega quem chega sem os headers")

print("\n[2] Sessão morta vira SessaoServiceNowExpirada, não um erro genérico")
_limpar("expirado")
_, cli = _cliente()
try:
    cli.usuario_atual()
    checar(False, "API redirecionando ao login.do → SessaoServiceNowExpirada")
except SessaoServiceNowExpirada:
    checar(True, "API redirecionando ao login.do → SessaoServiceNowExpirada")
checar(isinstance(SessaoServiceNowExpirada(), ErroServiceNow),
       "e ela É um ErroServiceNow — quem trata o genérico não a deixa passar")

_limpar("pagina_expirada")
_, cli = _cliente()
try:
    cli.token()
    checar(False, "página do token caindo no login → SessaoServiceNowExpirada")
except SessaoServiceNowExpirada:
    checar(True, "página do token caindo no login → SessaoServiceNowExpirada")

_limpar("normal")
_, cli = _cliente()
cli.token()
ESTADO["modo"] = "401"
try:
    cli.usuario_atual()
    checar(False, "401 com token guardado → renova o token UMA vez e depois desiste")
except SessaoServiceNowExpirada:
    checar(ESTADO["pagina"] == 2 and len([c for c in _rotas() if c["rota"].startswith("/api/")]) == 2,
           "401 com token guardado → renova o token UMA vez e depois desiste")

_limpar("erro500")
_, cli = _cliente()
try:
    cli.usuario_atual()
    checar(False, "500 → ErroServiceNow(status, texto)")
except ErroServiceNow as exc:
    checar(exc.status == 500 and len(exc.texto) <= 300 and "Erro interno" in exc.texto,
           f"500 → ErroServiceNow(500, texto cortado em {len(exc.texto)} caracteres)")

print("\n[3] Usuários")
_limpar()
_, cli = _cliente()
u = cli.usuario_atual()
checar(u == {"sys_id": USUARIO, "user_name": "fulano.tal", "name": "Fulano Tal",
             "email": "fulano.tal@exemplo.com.br"},
       "current_user normalizado, com o e-mail completado pela sys_user")
lucas = cli.procurar_usuario("lucas.gonzaga@exemplo.com.br")
checar(lucas and lucas["sys_id"] == LUCAS and lucas["user_name"] == "lucas.gonzaga",
       "procurar_usuario acha pelo e-mail")
q = ESTADO["ultimo"]["query"]
checar(q.get("sysparm_query") == "email=lucas.gonzaga@exemplo.com.br^ORuser_name=lucas.gonzaga@exemplo.com.br"
       and q.get("sysparm_fields") == "sys_id,name,email,user_name" and q.get("sysparm_limit") == "1",
       "com a encoded query, os campos e o limite do contrato")
checar(cli.procurar_usuario("ninguem") is None, "quem não existe volta None")
antes = len(ESTADO["chamadas"])
for ruim in ("a^ORactive=true", "x=y", "fulano tal", "", "a,b", "n!o"):
    try:
        cli.procurar_usuario(ruim)
        checar(False, f"{ruim!r} recusado antes de virar consulta")
    except ValueError:
        checar(True, f"{ruim!r} recusado antes de virar consulta")
checar(len(ESTADO["chamadas"]) == antes, "e nenhuma dessas chegou ao servidor")

print("\n[4] O item vem normalizado")
antes = len(ESTADO["chamadas"])
for ruim in ("123", "66831e771b37b5901e870e9fe54bcb1g", "../sys_user", ""):
    try:
        cli.descrever_item(ruim)
        checar(False, f"sys_id {ruim!r} → ValueError")
    except ValueError:
        checar(True, f"sys_id {ruim!r} → ValueError")
checar(len(ESTADO["chamadas"]) == antes, "sem tocar o servidor")
item = cli.descrever_item(ITEM)
checar(item["sys_id"] == ITEM and item["name"] == "Lançamento de NF", "sys_id e nome")
idx = cat.indice_variaveis(item)
checar(idx["requested_for"]["type"] == "reference" and idx["requested_for"]["mandatory"] is True,
       "`friendly_type` vira o tipo legível; mandatory 'true' (string) vira bool")
checar(idx["u_phone"]["read_only"] is True, "read_only preservado")
checar(idx["question_brand"]["choices"] == [{"label": "Renner Brasil", "value": "renner_br"},
                                            {"label": "Camicado", "value": "cam"},
                                            {"label": "Youcom", "value": "you"}],
       "opções como {label, value}")
checar("question_supplier" in idx and "question_has_a_purchase_order" in idx,
       "variáveis dentro de container clássico entram no índice")
mrvs = idx.get("documentos")
checar(mrvs is not None and mrvs["type"] == "mrvs", "`multi_row` normalizado para `mrvs`")
checar([f["name"] for f in mrvs["children"]] == list(cat.COLUNAS_NOTA),
       "com as três colunas em children")
checar("question_document_number" not in idx, "e as colunas do MRVS NÃO viram variáveis soltas")
checar(cat._tipo_normalizado({"type": "mrvs"}) == "mrvs" and cat._tipo_normalizado({"type": 5}) == "5",
       "`mrvs` também é aceito; tipo numérico sem friendly_type vira string")

print("\n[5] Opções resolvidas por rótulo")
checar(resolver_opcao(idx["question_brand"], "renner brasil") == "renner_br", "'renner brasil' acha 'Renner Brasil'")
checar(resolver_opcao(idx["question_nature_of_the_transaction"], "FIXED ASSETS (capex)") == "capex", "caixa não importa")
checar(resolver_opcao(idx["question_has_a_purchase_order"], "nao") == "No", "'nao' acha 'Não' (acento não importa)")
checar(resolver_opcao(idx["question_has_a_purchase_order"], "Yes") == "Yes", "bater pelo próprio value também serve")
try:
    resolver_opcao(idx["question_brand"], "Ashua")
    checar(False, "opção inexistente → KeyError")
except KeyError as exc:
    checar("question_brand" in str(exc) and "Renner Brasil" in str(exc),
           "opção inexistente → KeyError dizendo a variável e as opções")

print("\n[6] Variáveis do Lançamento de NF, como a tabela do contrato")
dados = {"requested_for_sys_id": USUARIO, "impact_employee_sys_id": LUCAS,
         "telefone": "+55 (11) 93098-4829", "bu": "Renner", "fornecedor": "ZEBRA DO BRASIL",
         "notas": [{"po": "2570313-25", "nf": "200153", "vencimento": "2026-10-30"}]}
vars_, falt = montar_variaveis_lancamento_nf(item, dados)
esperado = {
    "requested_for": USUARIO,
    "u_impact_employee": LUCAS,
    "question_brand": "renner_br",
    "question_document_type": "mat",
    "question_demand_type": "oth",
    "question_has_a_purchase_order": "Yes",
    "question_supplier": "ZEBRA DO BRASIL",
    "question_nature_of_the_transaction": "capex",
    "question_the_document_is_being_sent_after_the_deadline": "No",
    "question_number_of_documents_being_sent": "1",
    "documentos": '[{"question_order_number_po": "2570313-25", "question_document_number": "200153", '
                  '"question_due_date": "2026-10-30"}]',
    "question_description": "Prezados, gentileza enviar pra lançamento e cadastro de patrimonio a 200153 "
                            "referente à PO 2570313-25.",
}
checar(vars_ == esperado, "dict exatamente o esperado" + ("" if vars_ == esperado else f"\n        {vars_}"))
checar("u_phone" not in vars_ and "u_phone" not in falt, "u_phone read_only: nem enviado, nem faltante")
checar(falt == [], "nada faltante no item completo")
checar(isinstance(vars_["documentos"], str) and json.loads(vars_["documentos"])[0]["question_document_number"] == "200153",
       "MRVS é STRING JSON de lista de dicts")

item2 = json.loads(json.dumps(item))
item2["variables"][1]["read_only"] = False
item2["variables"][6]["children"] = [f for f in item2["variables"][6]["children"] if f["name"] != "question_supplier"]
vars2, falt2 = montar_variaveis_lancamento_nf(item2, dados)
checar(vars2.get("u_phone") == "+55 (11) 93098-4829", "u_phone editável → enviado")
checar(falt2 == ["question_supplier"] and "question_supplier" not in vars2,
       "variável que o item não tem → faltantes, e não vai no pedido")

dados2 = dict(dados, bu="camicado", notas=[{"po": "100-1", "nf": "1", "vencimento": "2026-11-01"},
                                           {"po": "100-2", "nf": "2", "vencimento": ""}])
vars3, _ = montar_variaveis_lancamento_nf(item, dados2)
checar(vars3["question_brand"] == "cam" and vars3["question_number_of_documents_being_sent"] == "2",
       "Camicado → 'Camicado'; duas notas → '2'")
checar(len(json.loads(vars3["documentos"])) == 2 and json.loads(vars3["documentos"])[1]["question_due_date"] == "",
       "uma linha por nota no bloco (vencimento vazio permitido)")
checar(vars3["question_description"] == ("Prezados, gentileza enviar pra lançamento e cadastro de patrimonio a 1 "
                                         "referente à PO 100-1. Prezados, gentileza enviar pra lançamento e cadastro "
                                         "de patrimonio a 2 referente à PO 100-2."),
       "uma frase por nota, separadas por espaço")
checar(montar_variaveis_lancamento_nf(item, dict(dados, bu="YouCom"))[0]["question_brand"] == "you",
       "Youcom → 'Youcom' (sem depender da caixa da BU)")
for errado, motivo in ((dict(dados, bu="Ashua"), "BU desconhecida"),
                       (dict(dados, notas=[]), "sem notas"),
                       (dict(dados, notas=[{"po": "1", "nf": "2", "vencimento": "30/10/2026"}]), "vencimento fora de AAAA-MM-DD")):
    try:
        montar_variaveis_lancamento_nf(item, errado)
        checar(False, f"{motivo} → ValueError")
    except ValueError:
        checar(True, f"{motivo} → ValueError")
item3 = json.loads(json.dumps(item))
item3["variables"][3]["choices"] = _opcoes(("Renner Argentina", "ar"))
try:
    montar_variaveis_lancamento_nf(item3, dados)
    checar(False, "opção da marca sumiu do item → KeyError (o item mudou; conferir formulário)")
except KeyError:
    checar(True, "opção da marca sumiu do item → KeyError (o item mudou; conferir formulário)")
item4 = json.loads(json.dumps(item))
item4["variables"] = [v for v in item4["variables"] if v["name"] != "documentos"]
_, falt4 = montar_variaveis_lancamento_nf(item4, dados)
checar(any("multilinha" in f for f in falt4), "sem o bloco multilinha → faltante nomeado")

print("\n[7] order_now e RITM")
_limpar()
_, cli = _cliente()
pedido = cli.enviar_pedido(ITEM, vars_)
checar(pedido == {"request_sys_id": REQ, "request_number": "REQ0099001"}, "devolve sys_id e número do REQ")
env = ESTADO["ultimo"]
corpo = json.loads(env["corpo"])
checar(env["metodo"] == "POST" and env["rota"] == f"/api/sn_sc/servicecatalog/items/{ITEM}/order_now",
       "POST na rota order_now do item")
checar(env["content_type"].startswith("application/json") and corpo["sysparm_quantity"] == "1",
       "corpo JSON com sysparm_quantity '1'")
checar(corpo["variables"] == esperado, "e as variáveis exatamente como montadas")
checar(isinstance(corpo["variables"]["documentos"], str), "MRVS chega como string JSON, não como lista")
cli.enviar_pedido(ITEM, {"documentos": [{"a": "1"}], "n": 3, "vazio": None})
c2 = json.loads(ESTADO["ultimo"]["corpo"])["variables"]
checar(c2 == {"documentos": '[{"a": "1"}]', "n": "3", "vazio": ""},
       "lista vira string JSON, número vira texto, None vira vazio")
ritm = cli.ritm_do_pedido(REQ)
checar(ritm == {"sys_id": RITM, "number": "RITM0099001"}, "ritm_do_pedido acha o RITM do REQ")
q = ESTADO["ultimo"]["query"]
checar(q.get("sysparm_query") == f"request={REQ}" and q.get("sysparm_fields") == "sys_id,number",
       "consultando sc_req_item por request=<id>")
checar(cli.ritm_do_pedido(RITM) is None, "pedido sem RITM ainda → None")
try:
    cli.enviar_pedido("nao-e-sys-id", {})
    checar(False, "enviar_pedido com sys_id inválido → ValueError")
except ValueError:
    checar(True, "enviar_pedido com sys_id inválido → ValueError")

print("\n[8] Anexos")
pdf = b"%PDF-1.4\n" + bytes(range(256)) * 4
sid = cli.anexar("sc_req_item", RITM, "NF_200153.pdf", pdf, "application/pdf")
checar(sid == ANEXO, "devolve o sys_id do anexo")
env = ESTADO["ultimo"]
checar(env["rota"] == "/api/now/attachment/file" and env["query"] == {
    "table_name": "sc_req_item", "table_sys_id": RITM, "file_name": "NF_200153.pdf"},
    "POST /api/now/attachment/file com tabela, registro e nome")
checar(env["corpo"] == pdf and env["content_type"] == "application/pdf",
       "bytes íntegros, com o Content-Type do arquivo")
cli.anexar("sc_request", REQ, "pasta\\sub/Cadastro.xlsx", b"x",
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
checar(ESTADO["ultimo"]["query"]["file_name"] == "Cadastro.xlsx", "só o nome do arquivo, sem caminho")
antes = len(ESTADO["chamadas"])
for args, motivo in ((("sys_user", RITM, "a.pdf", b"x", "application/pdf"), "tabela fora de sc_req_item/sc_request"),
                     (("sc_req_item", "zz", "a.pdf", b"x", "application/pdf"), "sys_id inválido"),
                     (("sc_req_item", RITM, "", b"x", "application/pdf"), "nome vazio"),
                     (("sc_req_item", RITM, "a.pdf", b"", "application/pdf"), "conteúdo vazio")):
    try:
        cli.anexar(*args)
        checar(False, f"{motivo} → ValueError")
    except ValueError:
        checar(True, f"{motivo} → ValueError")
checar(len(ESTADO["chamadas"]) == antes, "sem tocar o servidor")

print("\n[9] Contraprova: o módulo não decide TLS nem lê credencial")
fonte = (RAIZ / "integracoes" / "sn_catalogo.py").read_text(encoding="utf-8")
checar(not re.search(r"verify\s*=", fonte), "nenhum `verify=` — TLS é decisão de integracoes/http.py")
checar("disable_warnings" not in fonte and "InsecureRequestWarning" not in fonte,
       "não silencia aviso de TLS")
checar(not re.search(r"os\.(environ|getenv)|import os\b|from os\b", fonte),
       "não lê o ambiente")
checar("cofre" not in fonte.lower() and not re.search(r"(?i)(senha|password|passwd)\s*=", fonte),
       "não pega credencial de lugar nenhum")
checar("requests.Session()" not in fonte and "requests.get(" not in fonte and "requests.post(" not in fonte,
       "não cria sessão própria — usa a que recebeu")
checar("X-UserToken" in fonte and "X-Requested-With" in fonte, "manda os headers que a autenticação por cookie exige")

srv.shutdown()

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhou:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("O pedido no Service Catalog sai como o usuário logado, com o corpo que o contrato pede.")
