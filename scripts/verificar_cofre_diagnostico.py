#!/usr/bin/env python3
"""Verificação do diagnóstico do cofre visto de dentro do serviço.

    python3 scripts/verificar_cofre_diagnostico.py

Sobe o portal em memória, entra como admin e chama `/api/cofre/*`. O que
mais importa aqui não é o cofre responder — no servidor de teste ele nem
existe — e sim que a tela diga a verdade e que **nenhum valor de segredo
saia na resposta**.
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
# Cofre local próprio: a verificação nunca toca no cofre de ninguém.
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import cofre, security  # noqa: E402

# Segredo de mentira no cofre local, para provar que o valor não vaza.
SEGREDO = "valor-secreto-que-nao-pode-aparecer"
cofre.definir("CORREIOS_CHAVE", SEGREDO)
cofre.definir("CORREIOS_USUARIO", "usuario-de-teste")

from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

app = main.app
cliente = TestClient(app)

# Sessão de admin direto na memória: o login de verdade depende do AD.
sid, cookie = security.create_session(
    {"username": "verificacao", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)

print("\n[1] O diagnóstico responde e diz quem o serviço é")
r = cliente.get("/api/cofre/diagnostico")
checar(r.status_code == 200, f"HTTP 200 ({r.status_code})")
d = r.json() if r.status_code == 200 else {}
checar(bool(d.get("usuario_do_servico")), "diz o usuário do processo")
checar("corporativo_ok" in d, "diz se o cofre corporativo responde")
checar(isinstance(d.get("corporativo_detalhe"), str) and d["corporativo_detalhe"],
       "explica por que responde ou não")
checar(isinstance(d.get("grupos"), list) and len(d["grupos"]) >= 4,
       "traz os grupos de chaves (Correios, EBS, ServiceNow, MDM)")
checar(d.get("grupos", [{}])[0].get("nome") == "Correios",
       "os Correios vêm primeiro — é a referência já validada")

print("\n[2] Nenhum valor de segredo sai na resposta")
bruto = r.text
checar(SEGREDO not in bruto, "a chave dos Correios não aparece no JSON")
correios = next((g for g in d.get("grupos", []) if g["nome"] == "Correios"), {})
por_chave = {c["chave"]: c for c in correios.get("chaves", [])}
checar(por_chave.get("CORREIOS_CHAVE", {}).get("resolvida") is True,
       "mesmo assim, a chave consta como resolvida")
checar("valor" not in por_chave.get("CORREIOS_CHAVE", {}),
       "e sem o campo valor")
checar(por_chave.get("CORREIOS_CHAVE", {}).get("tamanho") == len(SEGREDO),
       "o tamanho basta para separar 'não existe' de 'existe e veio vazio'")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("valor") == "usuario-de-teste",
       "o que não é segredo aparece, para conferir se é o valor esperado")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("no_local") is True,
       "aponta o cofre local como origem")
checar(por_chave.get("CORREIOS_USUARIO", {}).get("no_corporativo") is False,
       "e que o corporativo não tem — é assim que se enxerga o sombreamento")
checar(por_chave.get("CORREIOS_CARTOES", {}).get("resolvida") is False,
       "chave ausente aparece como não resolvida, sem inventar valor")

print("\n[3] Permissão do arquivo do cofre corporativo")
for campo in ("modulo_corporativo", "arquivo_corporativo"):
    a = d.get(campo) or {}
    checar(set(("caminho", "existe", "legivel", "pasta_acessivel")) <= set(a),
           f"{campo}: diz caminho, existência, leitura e acesso à pasta")
checar(isinstance(d.get("permissoes_corporativo"), dict),
       "traz dono/grupo/modo do arquivo — é onde a falha de permissão aparece")

print("\n[4] Sondagem avulsa")
r2 = cliente.get("/api/cofre/sondar/CORREIOS_CHAVE")
checar(r2.status_code == 200, f"HTTP 200 ({r2.status_code})")
checar(SEGREDO not in r2.text, "também não vaza o valor")
r3 = cliente.get("/api/cofre/sondar/nao;existe")
checar(r3.status_code == 422, f"nome inválido é recusado ({r3.status_code})")
r4 = cliente.get("/api/cofre/sondar/correios_usuario")
checar(r4.status_code == 200 and r4.json().get("chave") == "CORREIOS_USUARIO",
       "o nome é normalizado para maiúsculas")

print("\n[5] Teste dos Correios: falta de chave não vira 500")
r5 = cliente.post("/api/cofre/testar-correios")
checar(r5.status_code == 200, f"responde 200 com o diagnóstico ({r5.status_code})")
d5 = r5.json() if r5.status_code == 200 else {}
checar(d5.get("ok") is False, "sem CORREIOS_CARTOES, o teste falha")
checar(d5.get("etapa") == "cofre", "e diz que parou no cofre, não na API")
checar("CORREIOS_CARTOES" in (d5.get("detalhe") or ""), "nomeando a chave que falta")
checar(SEGREDO not in r5.text, "sem vazar segredo nem aqui")

print("\n[6] Sem sessão, nada responde")
anonimo = TestClient(app)
for caminho in ("/api/cofre/diagnostico", "/api/cofre/sondar/CORREIOS_CHAVE"):
    checar(anonimo.get(caminho).status_code in (401, 403), f"{caminho} exige sessão")
checar(anonimo.post("/api/cofre/testar-correios").status_code in (401, 403),
       "/api/cofre/testar-correios exige sessão")

# Usuário comum, sem admin: a tela é de administração.
sid2, cookie2 = security.create_session(
    {"username": "comum", "is_admin": False, "permission_map": {}})
comum = TestClient(app)
comum.cookies.set("spare_session", cookie2)
checar(comum.get("/api/cofre/diagnostico").status_code == 403,
       "usuário sem admin recebe 403")

print("\n[7] A aba existe na tela de Parâmetros")
js = (RAIZ / "static/modules/parametros.js").read_text(encoding="utf-8")
checar("['cofre',           'Cofre de segredos']" in js, "a aba está na lista")
checar("cofre:          renderCofre" in js, "e ligada ao renderizador")
checar("'cofre', 'ebs-oracle'" in js, "é aba de admin")
checar("/cofre/testar-correios" in js, "a tela chama o teste dos Correios")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Diagnóstico do cofre íntegro.")
