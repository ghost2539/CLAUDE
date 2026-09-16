#!/usr/bin/env python3
"""Verificação da regra de obsolescência (G02), em banco temporário.

    python3 scripts/verificar_obsolescencia.py
"""
from __future__ import annotations
import os, sys, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_T = tempfile.mkdtemp(prefix="obs-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_T}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["OBSOLESCENCIA_DATABASE_URL"] = f"sqlite:///{_T}/obs.db"
import db.obsolescencia as db  # noqa: E402
import routers.obsolescencia as ob  # noqa: E402

falhas, feitos = [], 0
def checar(c, d):
    global feitos; feitos += 1
    print(("  ok   " if c else "  FALHA ") + d)
    if not c: falhas.append(d)

assert db.DATABASE_URL.endswith("obs.db"), f"config sem OBSOLESCENCIA_DATABASE_URL: {db.DATABASE_URL}"
db.init_db()
agora = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

print("\n[1] Datas do MDM (tooltip dd/mm/aaaa) e ISO")
checar(ob.dias_sem_ver("01/08/2026 10:30", agora) == 43, "dd/mm/aaaa HH:MM → 43 dias")
checar(ob.dias_sem_ver("2026-09-10T08:00:00Z", agora) == 3, "ISO → 3 dias")
checar(ob.dias_sem_ver("13/09/2026 11:59", agora) == 0, "hoje → 0")
checar(ob.dias_sem_ver("", agora) is None and ob.dias_sem_ver("lixo", agora) is None, "vazio/lixo → None")

print("\n[2] Android travado")
checar(ob.android_travado("Android 9.0", "TC21", "11", []), "9 < 11 → travado")
checar(not ob.android_travado("13", "TC21", "11", []), "13 ≥ 11 → ok")
checar(ob.android_travado("13", "Bluebird EF501R", "11", ["EF501"]), "modelo sem update → travado")
checar(not ob.android_travado("", "TC21", "11", []), "sem versão → não afirma")

print("\n[3] Regra E/OU e limite de anos configurável")
c = {"modelo": "Bluebird EF500R", "data_aquisicao": (agora - timedelta(days=365*6)).isoformat(), "android_travado": True}
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "todos", 5)
checar(a["obsoleto"] and a["atendidos"] == ["idade_5_anos", "android_travado", "modelo_eol"], "3 critérios → obsoleto (E)")
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "todos", 7)
checar(not a["obsoleto"] and "idade_5_anos" not in a["atendidos"], "limite 7 anos: idade não atende → não obsoleto (E)")
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "qualquer", 7)
checar(a["obsoleto"], "mesmo caso no OU → obsoleto")

print("\n[4] aplicar_coleta grava visto_em, calcula travado e o resumo conta")
db.gravar_config({"versao_os_minima": "11", "modelos_eol": "EF500", "limite_anos": "5", "modo_regra": "qualquer"})
brutos = [
  {"id": "1", "nome": "A", "usuario": "ljr001_coletor", "modelo": "Bluebird EF500R", "versao_os": "8.1", "visto_em": "01/08/2026 10:00", "tags": []},
  {"id": "2", "nome": "B", "usuario": "ljr001_coletor", "modelo": "Zebra TC21", "versao_os": "13", "visto_em": "13/09/2026 09:00", "tags": []},
  {"id": "3", "nome": "C", "usuario": "ljr002_coletor", "modelo": "Zebra TC21", "versao_os": "10", "visto_em": "", "tags": []},
]
r = ob.aplicar_coleta(brutos, usuario="t")
checar(r["novos"] == 3, "3 coletores gravados")
with db.SessionLocal() as s:
    from sqlalchemy import select
    linhas = {c.mdm_id: c for c in s.execute(select(db.Coletor)).scalars()}
checar(linhas["1"].visto_em is not None and linhas["1"].dias_sem_ver is not None and linhas["1"].dias_sem_ver > 30, "visto_em gravado e dias_sem_ver > 30 para o A")
checar(linhas["1"].obsoleto and linhas["3"].obsoleto and not linhas["2"].obsoleto, "OU: A (EOL+travado) e C (travado) obsoletos; B não")
res = ob.resumo_parque()
checar(res["criterios"]["android_travado"] == 2 and res["criterios"]["modelo_eol"] == 1, "contagem por critério")
checar(res["sem_ver"]["quantidade"] == 1, "1 sem comunicar acima do limite")
checar(any(m["modelo"] == "Zebra TC21" and m["coletores"] == 2 for m in res["por_modelo"]), "por modelo")
checar(any(v["versao"] == "13" for v in res["por_versao_os"]), "por versão de Android")

print("\n[5] Sessão do MDM: certificado, proxy e erros de rede viram 502/504, nunca 500")
import requests, ssl, subprocess, threading, http.server
from types import SimpleNamespace
from integracoes import mdm_airwatch as mdm
from fastapi import HTTPException

ob._cfg.VERIFY_SSL = False; ob._cfg.MDM_CA_BUNDLE = ""
checar(ob._nova_sessao().verify is False, "VERIFY_SSL=false → sessão do MDM sem verificação (como o ServiceNow)")
ob._cfg.MDM_CA_BUNDLE = "/tmp/ca-corporativa.pem"
checar(ob._nova_sessao().verify == "/tmp/ca-corporativa.pem", "MDM_CA_BUNDLE → verifica com a cadeia informada")
ob._cfg.MDM_CA_BUNDLE = ""; ob._cfg.VERIFY_SSL = True; ob._cfg.CA_BUNDLE = ""
checar(ob._nova_sessao().verify is True, "VERIFY_SSL=true sem CA própria → verificação padrão")
ob._cfg.VERIFY_SSL = False

def _status(fn):
    try:
        fn(); return None
    except HTTPException as e:
        return e.status_code, e.detail
ob.credencial_mdm = lambda: ("renner\\svc", "segredo")
_login_original = mdm.login
def _login_erro(exc):
    def f(sessao, u, s_, base=""):
        raise exc
    return f
mdm.login = _login_erro(requests.exceptions.SSLError("certificate verify failed"))
st = _status(lambda: ob.sessao_mdm(forcar=True))
checar(st and st[0] == 502 and "certificado" in st[1] and "MDM_CA_BUNDLE" in st[1], "SSLError no login → 502 com orientação (VERIFY_SSL / MDM_CA_BUNDLE)")
mdm.login = _login_erro(requests.exceptions.ProxyError("407"))
checar(_status(lambda: ob.sessao_mdm(forcar=True))[0] == 502, "ProxyError → 502")
mdm.login = _login_erro(requests.exceptions.ConnectTimeout("t"))
checar(_status(lambda: ob.sessao_mdm(forcar=True))[0] == 504, "Timeout → 504")
mdm.login = _login_erro(requests.exceptions.ConnectionError("refused"))
checar(_status(lambda: ob.sessao_mdm(forcar=True))[0] == 502, "ConnectionError → 502")
mdm.login = lambda sessao, u, s_, base="": False
st = _status(lambda: ob.sessao_mdm(forcar=True))
checar(st[0] == 502 and "recusado" in st[1], "login recusado → 502")
mdm.login = _login_original

ob._exigir_admin = lambda req: {"username": "t"}
ob.sessao_mdm = lambda forcar=False: object()
_varrer_original = mdm.varrer
mdm.varrer = lambda sessao, base="", **k: (_ for _ in ()).throw(requests.exceptions.SSLError("handshake"))
st = _status(lambda: ob.coletar(SimpleNamespace(cookies={}, headers={}, client=None)))
checar(st and st[0] == 502 and "certificado" in st[1], "SSLError na varredura → 502, não 500")
def _expira(sessao, base="", **k):
    raise mdm.SessaoExpirada("fragmento")
mdm.varrer = _expira
st = _status(lambda: ob.coletar(SimpleNamespace(cookies={}, headers={}, client=None)))
checar(st and st[0] == 502 and "grade" in st[1], "sessão expirada duas vezes → 502 explicando")
mdm.varrer = _varrer_original

# Servidor HTTPS local com certificado autoassinado: o caso real do proxy
# que apresenta uma cadeia que o sistema não conhece.
try:
    cert = os.path.join(_T, "cert.pem"); key = os.path.join(_T, "key.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", cert,
                    "-days", "1", "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    class _H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<html><form action='/AirWatch/Login' method='post'><input name='username'><input name='password'></form></html>")
        def do_POST(self):
            self.do_GET()
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(cert, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"https://127.0.0.1:{srv.server_address[1]}"
    import routers.servicenow as _sn
    _sn.SN_PROXY = ""   # sem proxy para o servidor local
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1"   # o ambiente pode ter proxy global
    ultimo_erro = {}
    def _tenta():
        s_ = ob._nova_sessao()
        try:
            mdm.login(s_, "u", "p", base); return "ok"
        except Exception as exc:  # noqa: BLE001
            ultimo_erro["exc"] = repr(exc)[:160]
            return ob._erro_de_rede(exc, base).status_code
    ob._cfg.VERIFY_SSL = True; ob._cfg.MDM_CA_BUNDLE = ""
    checar(_tenta() == 502, "certificado desconhecido com verificação ligada → 502 (era o 500)")
    ob._cfg.VERIFY_SSL = False
    r_ = _tenta(); checar(r_ == "ok", "VERIFY_SSL=false → conecta" + ("" if r_ == "ok" else f" ({ultimo_erro.get('exc')})"))
    ob._cfg.VERIFY_SSL = True; ob._cfg.MDM_CA_BUNDLE = cert
    r_ = _tenta(); checar(r_ == "ok", "MDM_CA_BUNDLE com a cadeia certa → conecta verificando" + ("" if r_ == "ok" else f" ({ultimo_erro.get('exc')})"))
    ob._cfg.VERIFY_SSL = False; ob._cfg.MDM_CA_BUNDLE = ""
    srv.shutdown()
except FileNotFoundError:
    print("  (openssl ausente: teste com servidor HTTPS local pulado)")

print("\n[6] Progresso da coleta")
from types import SimpleNamespace as _NS
ob.get_session = lambda req, required=True: {"username": "t"}
ob._exigir_admin = lambda req: {"username": "t"}
REQ = _NS(cookies={}, headers={}, client=None)

ob._progresso.update({"rodando": False, "pagina": 0, "lidos": 0, "total": 0, "fase": "",
                      "usuario": "", "iniciada_em": None, "terminada_em": None,
                      "erro": "", "resultado": None})
p0 = ob.coleta_progresso(REQ)
checar(p0["rodando"] is False and p0["percentual"] == 0, "sem coleta: parado, 0%")

# Varredura falsa que reporta páginas; guarda o que a tela veria em cada uma.
vistos = []
def _varrer_com_progresso(sessao, base="", progresso=None, **k):
    for pag, (ate, total) in enumerate([(500, 1200), (1000, 1200), (1200, 1200)]):
        progresso(pag, {"de": ate - 499, "ate": ate, "total": total}, ate)
        vistos.append(ob.coleta_progresso(REQ))
    return {"coletores": [{"id": "1", "usuario": "ljr001_coletor", "modelo": "Zebra TC21",
                           "versao_os": "13", "visto_em": "13/09/2026 09:00", "tags": []}],
            "total": 1200, "paginas": 3}
mdm.varrer = _varrer_com_progresso
ob.sessao_mdm = lambda forcar=False: object()
r = ob.coletar(REQ)
checar(r["ok"] is True, "coleta conclui")
checar([v["pagina"] for v in vistos] == [1, 2, 3], "página informada a cada passo")
checar([v["percentual"] for v in vistos] == [41, 83, 99], "percentual sobe e não passa de 99 antes do fim")
checar(all(v["rodando"] for v in vistos), "durante a varredura, rodando=True")
checar(vistos[0]["total"] == 1200 and vistos[0]["fase"].startswith("Lendo a grade"), "total e fase visíveis")
fim_ = ob.coleta_progresso(REQ)
checar(not fim_["rodando"] and fim_["percentual"] == 100 and fim_["fase"] == "Concluída", "no fim: 100% e concluída")
checar(fim_["resultado"] and fim_["terminada_em"], "resultado e horário guardados")

# Falha precisa desligar o "rodando", senão a tela fica presa e o botão trava.
import requests as _rq
mdm.varrer = lambda sessao, base="", progresso=None, **k: (_ for _ in ()).throw(_rq.exceptions.SSLError("x"))
try:
    ob.coletar(REQ)
except HTTPException as e:
    pass
f = ob.coleta_progresso(REQ)
checar(not f["rodando"] and f["fase"] == "Falhou" and "certificado" in f["erro"], "falha encerra o progresso com o motivo")

# Duas coletas ao mesmo tempo não podem existir.
ob._progresso["rodando"] = True
try:
    ob.coletar(REQ); checar(False, "coleta simultânea é recusada")
except HTTPException as e:
    checar(e.status_code == 409, "coleta simultânea é recusada (409)")
ob._progresso["rodando"] = False

print("\n[7] Só coletor de loja entra no parque")
with db.SessionLocal.begin() as _s:
    _s.query(db.Coletor).delete(); _s.query(db.Coleta).delete()
db.gravar_config({"somente_coletores": "1", "modo_regra": "qualquer"})
brutos2 = [
  {"id": "c1", "usuario": "ljr001_coletor", "modelo": "Zebra TC21", "versao_os": "13", "visto_em": "", "tags": ["Manutenção"]},
  {"id": "c2", "usuario": "cm0042_coletor", "modelo": "Zebra TC26", "versao_os": "13", "visto_em": "", "tags": []},
  {"id": "c3", "usuario": "cd0001_coletor", "modelo": "Zebra TC21", "versao_os": "13", "visto_em": "", "tags": []},
  {"id": "x1", "usuario": "joao.silva", "modelo": "Samsung A54", "versao_os": "14", "visto_em": "", "tags": ["Manutenção"]},
  {"id": "x2", "usuario": "ljr001_celular", "modelo": "Samsung A54", "versao_os": "14", "visto_em": "", "tags": []},
  {"id": "x3", "usuario": "xyz999_coletor", "modelo": "Tablet Teste", "versao_os": "12", "visto_em": "", "tags": []},
  {"id": "x4", "usuario": "", "modelo": "Zebra TC21", "versao_os": "13", "visto_em": "", "tags": []},
]
r2 = ob.aplicar_coleta(brutos2, usuario="t", total_mdm=7, paginas=1)
checar(r2["descartados"] == 4 and r2["coletores"] == 3, "4 descartados (celular, login solto, BU desconhecida, sem usuário)")
checar(r2["novos"] == 3, "só os 3 coletores de loja foram gravados")
with db.SessionLocal() as _s:
    guardados = {c.mdm_id for c in _s.query(db.Coletor).all()}
checar(guardados == {"c1", "c2", "c3"}, "base tem só os coletores")
res2 = ob.resumo_parque()
checar(all(m["modelo"] != "Samsung A54" for m in res2["por_modelo"]), "por modelo não traz celular")
checar(all(m["modelo"] != "Tablet Teste" for m in res2["por_modelo"]), "por modelo não traz o de BU desconhecida")
checar(sum(t["quantidade"] for t in res2["tags"]) == 1, "tags contam só o coletor (o celular tinha tag)")
checar(res2["coleta"]["total_mdm"] == 7 and res2["coleta"]["descartados"] == 4,
       "o resumo diz quantos vieram do MDM e quantos ficaram de fora")
checar(res2["total"] == 3 and res2["em_lojas"] == 2 and res2["fora_de_loja"] == 1, "CD conta no parque, fora de loja")

# O que já estava na base e não é coletor sai de vez, não vira "sumiu".
with db.SessionLocal.begin() as _s:
    _s.add(db.Coletor(mdm_id="velho", usuario="fulano.tal", modelo="Samsung A54", situacao=db.ATIVO))
r3 = ob.aplicar_coleta(brutos2, usuario="t", total_mdm=7, paginas=1)
with db.SessionLocal() as _s:
    checar(_s.query(db.Coletor).filter_by(mdm_id="velho").count() == 0, "registro antigo fora do padrão é removido")
    checar(_s.query(db.Coletor).filter_by(situacao=db.SUMIU).count() == 0, "…e não entra na fila de tratativa")

db.gravar_config({"somente_coletores": "0"})
r4 = ob.aplicar_coleta(brutos2, usuario="t", total_mdm=7, paginas=1)
checar(r4["descartados"] == 0 and r4["coletores"] == 7, "filtro desligado pela configuração traz tudo")
db.gravar_config({"somente_coletores": "1"})

print("\n[8] Caminho de remoção mapeado chega às bases antigas")
db.gravar_config({"mdm_remocao_endpoint": "", "mdm_remocao_campo": "id"})
db.init_db()
_cfg = db.ler_config()
checar(_cfg["mdm_remocao_endpoint"] == "/AirWatch/Devices/DeleteDevice/{id}",
       "endpoint em branco de coleta antiga passa a usar o mapeado")
checar(_cfg["mdm_remocao_campo"] == "SelectedDeviceIds", "e o campo do corpo também")
db.gravar_config({"mdm_remocao_endpoint": "/outro/{id}"})
db.init_db()
checar(db.ler_config()["mdm_remocao_endpoint"] == "/outro/{id}",
       "endpoint escolhido pela área não é sobrescrito")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Obsolescência íntegra.")
