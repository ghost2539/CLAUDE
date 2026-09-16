#!/usr/bin/env python3
"""Verificação das correções de segurança (TLS, sys_id, conversão EBS, acessos).

    python3 scripts/verificar_seguranca.py

Sobe o app em memória com bancos SQLite temporários e confere:
- TLS de saída verificado por padrão e desligável só por VERIFY_SSL=false;
- `sys_id` inválido recusado nos modelos e em `_sn_update`;
- `/api/public-assets` fechado sem sessão/token, com teto de identificadores;
- gestão de usuários só para administrador do portal; ações por módulo;
- mudança de permissão refletida na sessão viva; desativação derruba a sessão;
- cookie `Secure` e HSTS atrás de proxy HTTPS; X-Forwarded-For só de proxy confiável;
- limite de login por usuário além do IP.
"""
import os
import sys
import tempfile

T = tempfile.mkdtemp()
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)

os.environ["DATABASE_URL"] = f"sqlite:///{T}/portal.db"
os.environ["PORTAL_SESSION_SECRET"] = "x" * 64
os.environ["INITIAL_ADMIN_LOGIN"] = "admin.teste"
os.environ["AMBIENTE"] = "testes"
os.environ["SN_INDIC_REFRESH_MIN"] = "0"
os.environ["PUBLIC_ASSETS_TOKEN"] = "token-de-teste-123"
os.environ["CREDENTIALS_DIRECTORY"] = f"{T}/sem-credencial"
for nome in ("INDICADORES", "AUTOMACOES", "MONITORAMENTO", "ORCAMENTO_SPARE",
             "ORCAMENTO_MANUTENCAO", "OBSOLESCENCIA", "TRILHA", "SEPARACAO", "PROJETOS",
             "REVERSA", "INVENTARIO", "VENDA", "REGULARIZACAO", "PLANEJAMENTO",
             "CONSULTA_TIMES", "ATENDIMENTO", "BANCADA", "PREPARACAO", "DESTINACAO",
             "EXTERNO", "ORCAMENTO_EXEC", "ORCAMENTO_SPARE_EXEC", "EBS_FORMS"):
    os.environ[f"{nome}_DATABASE_URL"] = f"sqlite:///{T}/{nome.lower()}.db"
for chave in ("VERIFY_SSL", "PORTAL_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SESSION_COOKIE_SECURE",
              "TRUSTED_PROXIES", "SSL_CERTFILE"):
    os.environ.pop(chave, None)

from fastapi import HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from main import app  # noqa: E402
import core.security as sec  # noqa: E402
from integracoes import http as http_saida  # noqa: E402
from routers import servicenow as sn  # noqa: E402

feitos = 0


def checar(cond, msg):
    global feitos
    if not cond:
        print("  FALHOU:", msg)
        sys.exit(1)
    feitos += 1
    print("  ok  ", msg)


def pedido(host="testclient", **headers) -> Request:
    scope = {"type": "http", "method": "GET", "path": "/", "scheme": "http", "query_string": b"",
             "headers": [(k.lower().replace("_", "-").encode(), v.encode()) for k, v in headers.items()],
             "client": (host, 40000), "server": ("localhost", 8901)}
    return Request(scope)


c = TestClient(app).__enter__()   # com lifespan: init_db cria o admin inicial
COOKIE = sec.COOKIE_SESSAO
SYS_ID = "0123456789abcdef0123456789abcdef"

print("-- TLS de saída")
checar(http_saida.verificacao_tls("x") is True, "padrão: verificação ligada")
http_saida._cfg.VERIFY_SSL = False
checar(http_saida.verificacao_tls("x") is False, "VERIFY_SSL=false desliga (com aviso em log)")
http_saida._cfg.VERIFY_SSL = True
ca = f"{T}/ca.pem"
open(ca, "w").write("x")
http_saida._cfg.CA_BUNDLE = ca
checar(http_saida.verificacao_tls("x") == ca, "PORTAL_CA_BUNDLE existente vira o verify")
http_saida._cfg.CA_BUNDLE = f"{T}/nao-existe.pem"
checar(http_saida.verificacao_tls("y") is True, "CA inexistente cai na padrão (não desliga)")
http_saida._cfg.CA_BUNDLE = ""
checar(http_saida.verificacao_tls("z", "false") is False and http_saida.verificacao_tls("z", "true") is True,
       "política explícita da integração (true/false) prevalece")
s = http_saida.sessao("t", "http://proxy:3128")
checar(s.verify is True and s.proxies["https"] == "http://proxy:3128", "sessao(): verify e proxy")
checar(sn._sn_sessao(json=True).headers["Accept"] == "application/json", "_sn_sessao(json=True) pede JSON")

print("-- sys_id nas escritas do ServiceNow")
checar(sn.sys_id_valido(SYS_ID.upper()) == SYS_ID, "32 hex aceito (normalizado)")
for ruim in ("", "abc", SYS_ID + "^ORinstall_status=6", "x" * 32, SYS_ID + "a"):
    try:
        sn.sys_id_valido(ruim)
        checar(False, f"deveria recusar {ruim!r}")
    except ValueError:
        pass
checar(True, "sys_id com ^OR, curto, longo ou não-hex é recusado")
try:
    sn.SaidaMovIn(sys_id=SYS_ID + "^ORa=b")
    checar(False, "SaidaMovIn deveria recusar")
except ValidationError:
    checar(True, "SaidaMovIn recusa sys_id inválido")
checar(sn.MovInternaIn(sys_id=SYS_ID).sys_id == SYS_ID, "MovInternaIn aceita sys_id válido")
from routers.encerramento import EncerrarIn  # noqa: E402
try:
    EncerrarIn(sys_id="1^ORstate=6")
    checar(False, "EncerrarIn deveria recusar")
except ValidationError:
    checar(True, "EncerrarIn recusa sys_id inválido")


class _SessaoFalsa:
    def post(self, *a, **k):
        raise AssertionError("não deveria chegar ao ServiceNow")


try:
    sn._sn_update(_SessaoFalsa(), "alm_hardware", SYS_ID + "^ORx", {"a": 1})
    checar(False, "_sn_update deveria recusar")
except HTTPException as exc:
    checar(exc.status_code == 400, "_sn_update recusa antes de chamar o ServiceNow")

print("-- sessões e permissões")
_, cookie_admin = sec.create_session({"username": "admin.teste", "is_admin": True, "role": "ADMIN",
                                      "permissions": ["admin"], "permission_map": {}, "user_id": 1,
                                      "ebs_auth": None, "auth_source": "SSO"})
_, cookie_mod = sec.create_session({"username": "mod.parametros", "is_admin": False, "role": "USUÁRIO",
                                    "permissions": ["parametros"], "user_id": 2, "ebs_auth": None,
                                    "permission_map": {"parametros": {"can_view": True, "can_admin": True}}})
ADMIN = {COOKIE: cookie_admin}
MOD = {COOKIE: cookie_mod}

r = c.post("/api/servicenow/saida/move", json={"sys_id": SYS_ID + "^ORa=b"}, cookies=ADMIN)
checar(r.status_code == 422, "POST saida/move com sys_id inválido → 422")

checar(c.get("/api/parametros/permissoes", cookies=MOD).status_code == 403,
       "admin do módulo Parâmetros NÃO lista usuários")
checar(c.put("/api/parametros/permissoes/alguem", json={"is_admin": True}, cookies=MOD).status_code == 403,
       "admin do módulo Parâmetros NÃO altera permissões (escalada fechada)")
checar(c.post("/api/parametros/usuarios", json={"login": "z", "auth_source": "SSO", "is_admin": True},
              cookies=MOD).status_code == 403, "admin do módulo NÃO cria usuário admin")
checar(c.put("/api/parametros/controle-acesso", json={"block_external": True}, cookies=MOD).status_code == 403,
       "admin do módulo NÃO muda o controle de acesso")
checar(c.get("/api/parametros/locais", cookies=MOD).status_code == 200,
       "admin do módulo segue vendo as configurações do módulo")

r = c.get("/api/parametros/permissoes", cookies=ADMIN)
checar(r.status_code == 200 and r.json()["module_actions"]["bemvindo"] == ["view"],
       "administrador lista usuários; bem-vindo só tem 'view'")
checar("export" not in r.json()["module_actions"]["servicenow"] and
       "export" in r.json()["module_actions"]["consulta"], "ações por módulo vêm do servidor")

r = c.post("/api/parametros/usuarios", json={"login": "op.teste", "display_name": "Operador",
                                             "auth_source": "SSO"}, cookies=ADMIN)
checar(r.status_code == 200, "administrador cria usuário")
_, cookie_op = sec.create_session({"username": "op.teste", "is_admin": False, "role": "USUÁRIO",
                                   "permissions": [], "permission_map": {}, "user_id": 3,
                                   "ebs_auth": None, "auth_source": "SSO"})
OP = {COOKIE: cookie_op}
r = c.put("/api/parametros/permissoes/op.teste", cookies=ADMIN, json={
    "active": True, "allowed": True, "is_admin": False,
    "permission_map": {"bemvindo": {"can_view": True, "can_export": True, "can_edit": True},
                       "recebimento": {"can_view": True, "can_edit": True, "can_export": True}}})
checar(r.status_code == 200, "administrador grava permissões")
u = [x for x in c.get("/api/parametros/permissoes", cookies=ADMIN).json()["usuarios"] if x["username"] == "op.teste"][0]
checar(u["permission_map"]["bemvindo"] == {"can_view": True, "can_create": False, "can_edit": False,
                                           "can_export": False, "can_admin": False},
       "ação que não existe no módulo é descartada (bem-vindo sem exportar/editar)")
checar(u["permission_map"]["recebimento"]["can_edit"] and u["permission_map"]["recebimento"]["can_export"],
       "ações existentes são gravadas")
me = c.get("/api/auth/me", cookies=OP).json()
checar(me["permission_map"].get("recebimento", {}).get("can_edit") is True and "recebimento" in me["permissions"],
       "sessão viva já enxerga a permissão nova, sem novo login")
checar(c.get("/api/recebimentos", cookies=OP).status_code == 200, "e o endpoint do módulo libera")

r = c.put("/api/parametros/permissoes/op.teste", cookies=ADMIN, json={"active": False})
checar(r.status_code == 200 and c.get("/api/auth/me", cookies=OP).status_code == 401,
       "desativar derruba a sessão viva")
r = c.put("/api/parametros/permissoes/admin.teste", json={"is_admin": False}, cookies=ADMIN)
checar(r.status_code == 400, f"administrador não rebaixa a si mesmo nem o admin inicial ({r.status_code})")
_, cookie_op2 = sec.create_session({"username": "op.teste", "is_admin": False, "permissions": [],
                                    "permission_map": {}, "user_id": 3, "ebs_auth": None})
c.put("/api/parametros/permissoes/op.teste", cookies=ADMIN, json={"active": True})
checar(c.delete("/api/parametros/usuarios/op.teste", cookies=ADMIN).status_code == 200
       and c.get("/api/auth/me", cookies={COOKIE: cookie_op2}).status_code == 401,
       "excluir derruba a sessão viva")

print("-- conversão EBS (/api/public-assets)")
corpo = {"identificadores": ["ABC123"]}
checar(c.post("/api/public-assets/convert", json=corpo).status_code == 401, "sem sessão nem token → 401")
checar(c.post("/api/public-assets/convert", json=corpo, headers={"X-Api-Key": "errado"}).status_code == 401,
       "token errado → 401")
r = c.post("/api/public-assets/convert", json=corpo, headers={"X-Api-Key": "token-de-teste-123"})
checar(r.status_code in (502, 503), f"token certo passa da autorização (chega ao EBS: {r.status_code})")
r = c.post("/api/public-assets/convert", json={"identificadores": [f"X{i}" for i in range(201)]},
           headers={"X-Api-Key": "token-de-teste-123"})
checar(r.status_code == 422, "mais de 200 identificadores → 422")
r = c.post("/api/public-assets/convert", json=corpo, cookies=ADMIN)
checar(r.status_code in (502, 503), "sessão com permissão passa da autorização")
checar(c.get("/api/public-assets/health").status_code == 401, "health também exige autorização")
from db.portal import SessionLocal, PublicEbsQueryAudit  # noqa: E402
from sqlalchemy import select  # noqa: E402
with SessionLocal() as s_:
    linhas = s_.scalars(select(PublicEbsQueryAudit)).all()
checar(len(linhas) >= 2 and any(l.usuario == "token" for l in linhas) and any(l.usuario == "admin.teste" for l in linhas),
       "auditoria gravada (a tabela agora existe) com quem chamou")

print("-- HTTPS atrás de proxy")
checar(sec.client_ip(pedido("203.0.113.9", x_forwarded_for="10.0.0.1")) == "203.0.113.9",
       "X-Forwarded-For de cliente qualquer é ignorado")
checar(sec.client_ip(pedido("127.0.0.1", x_forwarded_for="10.0.0.1, 127.0.0.1")) == "10.0.0.1",
       "X-Forwarded-For do proxy confiável é usado")
checar(sec.cookie_seguro(pedido("127.0.0.1", x_forwarded_proto="https")) is True,
       "cookie Secure quando o proxy confiável diz https")
checar(sec.cookie_seguro(pedido("203.0.113.9", x_forwarded_proto="https")) is False,
       "X-Forwarded-Proto de cliente qualquer não liga o Secure")
checar(sec.cookie_seguro(pedido("127.0.0.1")) is False, "http puro: cookie sem Secure (modo auto)")
sec._cfg.SESSION_COOKIE_SECURE = "true"
checar(sec.cookie_seguro(pedido("203.0.113.9")) is True, "SESSION_COOKIE_SECURE=true força")
sec._cfg.SESSION_COOKIE_SECURE = "auto"
c_proxy = TestClient(app, client=("127.0.0.1", 50000))
r = c_proxy.get("/api/versao", headers={"X-Forwarded-Proto": "https"})
checar("strict-transport-security" in r.headers, "HSTS quando chega por https via proxy confiável")
checar("strict-transport-security" not in c.get("/api/versao").headers, "sem HSTS em http puro")
csp = c.get("/api/versao").headers["content-security-policy"]
checar("object-src 'none'" in csp and "base-uri 'self'" in csp and "form-action 'self'" in csp,
       "CSP com object-src, base-uri e form-action")

print("-- limite de login por usuário")
for i in range(5):
    sec.check_rate_limit(pedido(f"198.51.100.{i}"), "login", chave_extra="user:fulano")
try:
    sec.check_rate_limit(pedido("198.51.100.99"), "login", chave_extra="user:fulano")
    checar(False, "deveria bloquear")
except HTTPException as exc:
    checar(exc.status_code == 429, "6ª tentativa contra o mesmo login, de outro IP, é bloqueada")

print(f"Segurança íntegra ({feitos} checagens).")
