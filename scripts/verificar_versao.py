#!/usr/bin/env python3
"""Verificação do endpoint que diz qual código está no ar.

    python3 scripts/verificar_versao.py

Sem login ele responde o mínimo (commit, ramo, ambiente, início): quem
confere se o serviço foi reiniciado está no terminal do servidor, sem
cookie de navegador. Com sessão, vem o detalhe.
"""
import os, sys, tempfile
T = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{T}/p.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "x" * 64)
sys.path.insert(0, "/home/user/CLAUDE")
from main import app
from fastapi.testclient import TestClient
from core.security import create_session
c = TestClient(app)
r = c.get("/api/versao")
assert r.status_code == 200, r.status_code
d = r.json()
assert d["commit_curto"] and "assunto" not in d, d
sid, cookie = create_session({"username": "u", "is_admin": True, "permissions": [],
                              "permission_map": {}, "user_id": 1, "ebs_auth": None})
nome, valor = "spare_session", cookie
r2 = c.get("/api/versao", cookies={nome: valor})
assert r2.status_code == 200 and "assunto" in r2.json(), r2.json()
print("  ok   sem login responde o essencial:", d)
print("  ok   com login traz o detalhe:", sorted(r2.json()))
print("Versão íntegra.")
import os, sys, tempfile
T = tempfile.mkdtemp()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{T}/p.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "x" * 64)
sys.path.insert(0, "/home/user/CLAUDE")
from main import app
from fastapi.testclient import TestClient
from core.security import create_session
c = TestClient(app)
r = c.get("/api/versao")
assert r.status_code == 200, r.status_code
d = r.json()
assert d["commit_curto"] and "assunto" not in d, d
sid, cookie = create_session({"username": "u", "is_admin": True, "permissions": [],
                              "permission_map": {}, "user_id": 1, "ebs_auth": None})
nome, valor = "spare_session", cookie
r2 = c.get("/api/versao", cookies={nome: valor})
assert r2.status_code == 200 and "assunto" in r2.json(), r2.json()
print("  ok   sem login responde o essencial:", d)
print("  ok   com login traz o detalhe:", sorted(r2.json()))
print("Versão íntegra.")
