#!/usr/bin/env python3
"""Verificação da liberação de acesso por nível e por perfil.

    python3 scripts/verificar_niveis_acesso.py

O que se prova, e por que cada coisa:

- O nível traduz para as MESMAS colunas que o servidor confere. Se a
  tradução divergir da checagem, a tela diz um acesso e o portal aplica
  outro — e ninguém descobre olhando a tela.
- A ida e volta nunca EXAGERA o acesso. Há módulo sem as ações do meio;
  ali "Consultar" e "Operar" dão no mesmo, e mostrar o maior faria a tela
  afirmar um poder que a pessoa não tem.
- TODO módulo que o portal conhece pode receber permissão. O defeito que
  motivou esta seção: a lista de módulos era escrita à mão e ficou para
  trás; a tela oferecia torre, atendimento, separação e mais onze, o admin
  marcava, e a gravação descartava em silêncio.
- Excluir um perfil não tira acesso de ninguém: o perfil é modelo, e o que
  vale são as linhas já gravadas em cada usuário.
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
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


import config as _config_mod  # noqa: E402
import core.permissoes as perm  # noqa: E402
import db.portal as dbp  # noqa: E402
import main  # noqa: E402
from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_cfg = _config_mod.get_settings()
dbp.init_db()
with dbp.SessionLocal.begin() as s:
    s.add(dbp.User(login="adm", display_name="Admin", is_admin=True, auth_source="SSO"))
    s.add(dbp.User(login="joao", display_name="João", is_admin=False, auth_source="SSO"))
    s.add(dbp.User(login="maria", display_name="Maria", is_admin=False, auth_source="SSO"))

adm = TestClient(main.app)
_, ck = security.create_session({"username": "adm", "is_admin": True,
                                 "permission_map": {}, "auth_source": "SSO"})
adm.cookies.set("spain", "")
adm.cookies.set("spare_session", ck)


print("\n[1] Todo módulo do portal pode receber permissão")
faltando = sorted(set(_cfg.MODULE_ACTIONS) - set(_cfg.MODULES))
checar(not faltando, f"nenhum módulo de fora da lista de permissões ({faltando})")
d = adm.get("/api/parametros/permissoes").json()
checar(len(d["modules"]) == len(_cfg.MODULE_ACTIONS),
       f"a API oferece os {len(_cfg.MODULE_ACTIONS)} módulos ({len(d['modules'])})")
checar([n["chave"] for n in d["niveis"]] == list(perm.NIVEIS),
       "o vocabulário de níveis vem do servidor, não de uma cópia na tela")


print("\n[2] O nível vira exatamente as colunas que o servidor confere")
for modulo in ("recebimento", "parametros", "consulta"):
    disponiveis = set(perm.acoes_do_modulo(modulo))
    flags = perm.flags_do_nivel(modulo, "administrar")
    checar(all(flags[f"can_{a}"] for a in disponiveis),
           f"{modulo}: 'Administrar' liga tudo o que o módulo oferece")
    checar(not perm.flags_do_nivel(modulo, "nenhum")["can_view"],
           f"{modulo}: 'Sem acesso' não deixa nem ver")


print("\n[3] A ida e volta nunca diz mais do que foi concedido")
for modulo in _cfg.MODULES:
    for nivel in perm.NIVEIS:
        flags = perm.flags_do_nivel(modulo, nivel)
        volta = perm.nivel_das_flags(modulo, flags)
        if perm.flags_do_nivel(modulo, volta) != flags:
            checar(False, f"{modulo}/{nivel}: a volta ({volta}) concede diferente")
            break
    else:
        continue
    break
else:
    checar(True, f"ida e volta consistente nos {len(_cfg.MODULES)} módulos")
# O caso que motivou a regra: módulo sem as ações do meio.
checar(perm.nivel_das_flags("parametros", perm.flags_do_nivel("parametros", "consultar"))
       == "consultar",
       "módulo sem as ações do meio não vira 'Operar' por acidente")
# Combinação da grade antiga, que não casa com nível nenhum.
antiga = {"can_view": True, "can_edit": True, "can_create": False,
          "can_export": False, "can_admin": False}
checar(perm.nivel_das_flags("recebimento", antiga) in perm.NIVEIS,
       "combinação feita na grade antiga ainda abre em algum nível")


print("\n[4] Gravar por nível chega no banco e vale na hora de autorizar")
alvo = {"torre": "consultar", "atendimento": "operar", "separacao": "operar",
        "venda": "consultar", "recebimento": "administrar"}
r = adm.put("/api/parametros/permissoes/joao",
            json={"active": True, "allowed": True, "is_admin": False, "niveis": alvo})
checar(r.status_code == 200, f"PUT por nível ({r.status_code})")
d = adm.get("/api/parametros/permissoes").json()
joao = [u for u in d["usuarios"] if u["username"] == "joao"][0]
checar(joao["niveis"] == alvo, f"todos os módulos gravaram ({joao['niveis']})")
_, k = security.create_session({"username": "joao", "is_admin": False,
                                "permission_map": joao["permission_map"]})
cj = TestClient(main.app)
cj.cookies.set("spare_session", k)
checar(cj.get("/modulos/torre.js").status_code == 200, "o módulo liberado é entregue")
checar(cj.get("/modulos/projetos.js").status_code == 403, "o não liberado é negado")


print("\n[5] Perfil: criar, aplicar e a exceção de quem é admin")
r = adm.post("/api/parametros/perfis", json={
    "nome": "Operador", "descricao": "Estoque",
    "niveis": {"recebimento": "operar", "consulta": "consultar",
               "modulo_que_nao_existe": "operar", "torre": "nivel_invalido"}})
checar(r.status_code == 200, f"criar perfil ({r.status_code})")
pid = r.json()["perfil"]["id"]
checar(sorted(r.json()["perfil"]["niveis"]) == ["consulta", "recebimento"],
       "módulo inexistente e nível inválido são descartados, não gravados")
checar(adm.post("/api/parametros/perfis",
                json={"nome": "operador", "niveis": {}}).status_code == 409,
       "nome repetido é recusado, ignorando maiúsculas")
r = adm.post(f"/api/parametros/perfis/{pid}/aplicar",
             json={"logins": ["joao", "maria", "adm", "nao_existe"]})
checar(r.status_code == 200, f"aplicar ({r.status_code})")
corpo = r.json()
checar(sorted(corpo["aplicados"]) == ["joao", "maria"], "aplica a quem não é admin")
checar("adm" in corpo["ignorados"],
       "admin total é ignorado: perfil não limita quem já pode tudo")
checar("nao_existe" in corpo["ignorados"], "login inexistente é ignorado, não quebra")
d = adm.get("/api/parametros/permissoes").json()
joao = [u for u in d["usuarios"] if u["username"] == "joao"][0]
checar(joao["niveis"] == {"recebimento": "operar", "consulta": "consultar"},
       "as permissões antigas foram SUBSTITUÍDAS pelas do perfil")
checar(joao["perfil"] == "Operador", "o rótulo do perfil fica no usuário")
checar(adm.post("/api/parametros/perfis/999999/aplicar",
                json={"logins": ["joao"]}).status_code == 404, "perfil inexistente: 404")
checar(adm.post(f"/api/parametros/perfis/{pid}/aplicar",
                json={"logins": []}).status_code == 422, "sem usuário: 422")


print("\n[6] O perfil é modelo, não vínculo")
checar(adm.delete(f"/api/parametros/perfis/{pid}").status_code == 200, "excluir perfil")
d = adm.get("/api/parametros/permissoes").json()
joao = [u for u in d["usuarios"] if u["username"] == "joao"][0]
checar(joao["niveis"] == {"recebimento": "operar", "consulta": "consultar"},
       "quem foi liberado NÃO perde acesso quando o perfil some")


print("\n[7] Permissão")
anon = TestClient(main.app)
for metodo, rota in (("get", "/api/parametros/perfis"),
                     ("post", "/api/parametros/perfis"),
                     ("get", "/api/parametros/permissoes")):
    r = getattr(anon, metodo)(rota, **({"json": {}} if metodo == "post" else {}))
    checar(r.status_code in (401, 403), f"{rota} exige sessão ({r.status_code})")
comum = TestClient(main.app)
_, kc = security.create_session({"username": "joao", "is_admin": False, "permission_map": {}})
comum.cookies.set("spare_session", kc)
checar(comum.get("/api/parametros/perfis").status_code == 403,
       "sem admin de Parâmetros, 403")


print("\n[8] A tela usa o que o servidor manda")
js = (RAIZ / "modulos" / "parametros_admin.js").read_text(encoding="utf-8")
checar("perm-nivel" in js and "gradeDeNiveis" in js,
       "o editor monta seletor de nível, não caixas soltas")
checar("niveis:   lerNiveis(box)" in js or "niveis:" in js,
       "e envia 'niveis' ao salvar")
checar("niveisVocab = d.niveis" in js,
       "o vocabulário vem da resposta do servidor")
import re as _re  # noqa: E402
bloco = js.split("var MODULE_LABELS = {", 1)[1].split("};", 1)[0]
rotulados = set(_re.findall(r"(\w+):\s*['\"]", bloco))
sem_rotulo = [m for m in _cfg.MODULES if m not in rotulados]
checar(not sem_rotulo, f"todo módulo tem rótulo na grade ({sem_rotulo})")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Liberação por nível íntegra: o que a tela mostra é o que o portal aplica.")
