#!/usr/bin/env python3
"""Verificação da Consulta de ativos: credencial protegida do EBS ausente
vira 503 com orientação (nunca 500), e presente a consulta responde.

    python3 scripts/verificar_consulta.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_T = tempfile.mkdtemp(prefix="consulta-verif-")
import re
for v in sorted(set(re.findall(r"([A-Z_]*DATABASE_URL)", (RAIZ / "config.py").read_text()))):
    os.environ[v] = f"sqlite:///{_T}/{v.lower()}.db"
os.environ["PORTAL_SESSION_SECRET"] = "verificacao-local"
os.environ["CREDENTIALS_DIRECTORY"] = ""          # como num serviço sem LoadCredential
os.environ.setdefault("EBS_LOGIN_URL", "http://ebs.invalido/login")
os.environ.setdefault("EBS_SEARCH_URL", "http://ebs.invalido/search")
import logging; logging.disable(logging.CRITICAL)

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from core.security import create_session  # noqa: E402
import routers.public_assets as pub  # noqa: E402
import integracoes.ebs_service as ebs  # noqa: E402

falhas, feitos = [], 0
def checar(c, d):
    global feitos; feitos += 1
    print(("  ok   " if c else "  FALHA ") + d)
    if not c: falhas.append(d)

c = TestClient(main.app)
sid, cookie = create_session({"username": "sso.user", "display_name": "SSO", "is_admin": True, "auth_source": "SSO",
                              "permissions": ["admin"], "permission_map": {}, "user_id": 1, "ebs_auth": None})
c.cookies.set("spare_session", cookie)

print("\n[1] Sem credencial protegida (CREDENTIALS_DIRECTORY em branco)")
r = c.post("/api/consulta", json={"identificadores": ["ABC123"]})
checar(r.status_code == 503, f"POST /api/consulta → 503 (veio {r.status_code})")
checar("Credencial protegida" in r.text and "CREDENTIALS_DIRECTORY" in r.text, "mensagem diz o que falta")
r = c.get("/api/consulta/single", params={"identificador": "ABC123"})
checar(r.status_code == 503, f"GET /api/consulta/single → 503 (veio {r.status_code})")

print("\n[2] Diretório apontando para o serviço errado (arquivos não existem)")
pub._cfg.CREDENTIALS_DIRECTORY = "/run/credentials/portal_spare.service"
r = c.post("/api/consulta", json={"identificadores": ["ABC123"]})
checar(r.status_code == 503 and "portal_spare.service" in r.text, "503 mostrando o diretório configurado")

print("\n[3] Com credencial e EBS respondendo")
cred = os.path.join(_T, "creds"); os.makedirs(cred)
open(os.path.join(cred, "ebs_public_username"), "w").write("svc\n")
open(os.path.join(cred, "ebs_public_password"), "w").write("segredo\n")
pub._cfg.CREDENTIALS_DIRECTORY = cred
pub._auth_cache["value"] = None; pub._auth_cache["expires"] = 0
chamadas = {}
pub.ebs_login = lambda u, p: chamadas.setdefault("login", (u, p)) and {"cookies": {}, "token": "t"}
ebs.search_many = lambda auth, ids: [{"pesquisado": i, "encontrado": True, "empresa": "RENNER", "descricao": "PDV", "imobilizado": "1"} for i in ids]
r = c.post("/api/consulta", json={"identificadores": ["ABC123"]})
checar(r.status_code == 200 and r.json()["encontrados"] == 1, f"POST /api/consulta → 200 com resultado (veio {r.status_code})")
checar(chamadas.get("login") == ("svc", "segredo"), "login no EBS com a credencial protegida")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Consulta íntegra.")
