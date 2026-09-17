#!/usr/bin/env python3
"""Verificação dos diagnósticos, POR HTTP — como o navegador chama.

    python3 scripts/verificar_diagnosticos.py

Chamar a função direto não prova nada sobre o parâmetro chegar: é preciso
passar pela rota. Aqui a série vai por query string E por caminho, e a
resposta tem de ecoar o que o servidor recebeu — sem esse eco, "informei
a série" e "a série não chegou" viram discussão em vez de diagnóstico.
"""
import os, sys, tempfile
T = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{T}/p.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "x" * 64)
sys.path.insert(0, "/home/user/CLAUDE")
from main import app
from fastapi.testclient import TestClient
from core.security import create_session
import routers.obsolescencia as ob
import routers.servicenow as sn
import db.obsolescencia as dbo
dbo.init_db()

vistos = {}
ob.sessao_mdm = lambda forcar=False: object()
from integracoes import mdm_airwatch as mdm
mdm.procurar_detalhado = lambda sessao, texto, base="": (
    vistos.setdefault("mdm", []).append(texto) or
    {"coletores": [], "rodape": None, "erro": "", "http": 200, "url": ""})
sn._sn_session_from_portal = lambda req: object()
sn._get_http = lambda: (None, None)
sn._sn_query = lambda *a, **k: (vistos.setdefault("sn", []).append(a[2]) or [])

sid, cookie = create_session({"username": "u", "is_admin": True, "permissions": ["admin"],
                              "permission_map": {}, "user_id": 1, "ebs_auth": None,
                              "sn_cookies": {"x": "y"}})
c = TestClient(app, cookies={"spare_session": cookie})
for rota in ["/api/obsolescencia/mdm/diagnostico?serie=SN-ABC123",
             "/api/obsolescencia/mdm/diagnostico/SN-ABC123"]:
    vistos.clear()
    r = c.get(rota)
    assert r.json().get("recebido", {}).get("serie") == "SN-ABC123", r.json()
assert vistos.get("mdm") == ["SN-ABC123"], vistos
print("  ok   MDM", r.status_code, "| recebido:", r.json().get("recebido", {}).get("serie"),
          "| buscou:", vistos.get("mdm"))
for rota in ["/api/servicenow/diagnostico/depreciacao?serie=SN-ABC123",
             "/api/servicenow/diagnostico/depreciacao/SN-ABC123"]:
    vistos.clear()
    r = c.get(rota)
    assert r.json().get("recebido", {}).get("serie") == "SN-ABC123", r.json()
assert vistos.get("sn") == ["serial_number=SN-ABC123"], vistos
print("  ok   SN ", r.status_code, "| recebido:", r.json().get("recebido", {}).get("serie"),
          "| consultou:", vistos.get("sn"))
r = c.get("/api/obsolescencia/mdm/diagnostico")
assert "SEM série" in r.json()["conclusao"], r.json()["conclusao"]
print("  ok   sem série, a resposta diz que o servidor recebeu vazio")
print("Diagnósticos íntegros.")
