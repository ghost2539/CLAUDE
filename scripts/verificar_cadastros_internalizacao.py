#!/usr/bin/env python3
"""Verificação dos cadastros que o lançamento consome: etiquetas e imobilizados.

    python3 scripts/verificar_cadastros_internalizacao.py

O que se prova, e por quê:

- Etiqueta se cadastra em massa (lista colada, faixa numerada, ou os dois),
  e o que já existia ou é inválido volta NOMEADO em vez de derrubar o lote:
  o rolo pode ter sido cadastrado pela metade ontem.
- A faixa preserva os zeros à esquerda ("000100"…), porque é o que está
  impresso no rolo.
- A mesma etiqueta não entra duas vezes, nem trocando maiúscula por
  minúscula — plaqueta repetida em dois equipamentos é o erro que o
  patrimônio existe para impedir.
- Etiqueta cancelada não some: fica registrada, para ninguém cadastrá-la
  de novo achando que está no rolo. Só a disponível se apaga, e só o admin.
- O consumo (que o recebimento vai usar) pega as disponíveis na ORDEM de
  cadastro e nunca uma cancelada ou consumida.
- Item imobilizado é único pelo código do EBS, normalizado sem zeros à
  esquerda — a PO devolve "123", o cadastro pode ter "000123", e é o
  mesmo item.
- Permissão: quem só vê não cadastra; quem edita não apaga.
- Menu e abas: as duas telas existem na sidebar e no módulo, sob a
  permissão "internalizacao".
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="int-cadastros-verif-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP}/portal.db"
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-com-64-caracteres-de-sobra-aqui")
os.environ["INITIAL_ADMIN_LOGIN"] = "admin.teste"
os.environ["AMBIENTE"] = "testes"
for _modulo in ("TRILHA", "OBSOLESCENCIA", "REVERSA", "AGENDAMENTOS_FORN",
                "INTERNALIZACAO"):
    os.environ[f"{_modulo}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{_modulo.lower()}.db"
os.environ["PORTAL_COFRE_DIR"] = f"{_TEMP}/cofre"

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
import core.security as sec  # noqa: E402
import db.internalizacao as db  # noqa: E402
import routers.internalizacao as rint  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d) -> None:
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


cliente = TestClient(app)


def _sessao(username, perms, admin=False):
    pm = {"internalizacao": {f"can_{a}": True for a in perms}}
    _, cookie = sec.create_session(
        {"username": username, "is_admin": admin, "permission_map": pm,
         "permissions": ["internalizacao"] if perms else []})
    return {"spare_session": cookie}


ADMIN = _sessao("admin.teste", ("view", "create", "edit", "export", "admin"), admin=True)
OPERA = _sessao("opera.teste", ("view", "create", "edit"))
SO_VE = _sessao("ve.teste", ("view",))
BASE = "/api/internalizacao/cadastro"


def api(metodo, caminho, cookies=ADMIN, **kw):
    return cliente.request(metodo, BASE + caminho, cookies=cookies, **kw)


print("[1] Cadastro de etiquetas em massa")
r = api("POST", "/etiquetas", json={"codigos": "A-100\nA-101, A-102 A-103 a-103\n\nA-1@4",
                                    "local": "Armário 3"})
checar(r.status_code == 201, f"POST responde 201 (veio {r.status_code}: {r.text[:200]})")
d = r.json()
# "a-103" repete "A-103" trocando a caixa: é a mesma plaqueta, entra uma vez.
checar(d["criadas"] == 4, f"4 novas entram, a repetida de caixa diferente não conta (veio {d['criadas']})")
checar(d["invalidas"] == ["A-1@4"], f"a inválida volta nomeada: {d['invalidas']}")
checar(d["repetidas"] == [], "nenhuma repetida na primeira carga")

r = api("POST", "/etiquetas", json={"codigos": "A-101 A-105", "local": "Armário 3"})
d = r.json()
checar(d["criadas"] == 1 and d["repetidas"] == ["A-101"],
       f"segunda carga: 1 nova, a repetida volta nomeada ({d})")

r = api("POST", "/etiquetas", json={"prefixo": "PAT", "de": "000100", "ate": "000103",
                                    "local": "Gaveta 2"})
d = r.json()
checar(r.status_code == 201 and d["criadas"] == 4, f"faixa de 4 entra (veio {r.status_code}: {d})")
lista = api("GET", "/etiquetas?situacao=DISPONIVEL").json()
codigos = [x["codigo"] for x in lista["itens"]]
checar("PAT000100" in codigos and "PAT000103" in codigos,
       f"a faixa preserva os zeros à esquerda: {sorted(c for c in codigos if c.startswith('PAT'))}")
checar(lista["totais"]["DISPONIVEL"] == 9, f"9 disponíveis no total (veio {lista['totais']})")
checar("A-103" in codigos and "a-103" not in codigos, "a etiqueta fica guardada em maiúsculas")
checar(sorted(lista["locais"]) == ["Armário 3", "Gaveta 2"], f"os locais distintos vêm para o filtro: {lista['locais']}")
checar(api("GET", "/etiquetas?local=Gaveta%202").json()["total"] == 4, "filtro por local")
checar(api("GET", "/etiquetas?busca=a-10").json()["total"] == 5, "busca pela etiqueta é parcial e sem caixa")

for corpo, o_que in (
    ({"codigos": "", "local": "X"}, "sem etiqueta nenhuma"),
    ({"codigos": "B-1", "local": ""}, "sem local"),
    ({"de": "10", "ate": "5", "local": "X"}, "faixa invertida"),
    ({"de": "1", "ate": "9999", "local": "X"}, "faixa acima do teto"),
    ({"de": "1a", "ate": "9", "local": "X"}, "faixa com letra"),
):
    r = api("POST", "/etiquetas", json=corpo)
    checar(r.status_code == 422, f"{o_que} → 422 (veio {r.status_code})")

print("\n[2] Cancelar, mover e apagar")
alvo = next(x for x in lista["itens"] if x["codigo"] == "A-100")
r = api("POST", f"/etiquetas/{alvo['id']}/cancelar", json={"motivo": ""})
checar(r.status_code == 422, "cancelar sem motivo → 422")
r = api("POST", f"/etiquetas/{alvo['id']}/cancelar", json={"motivo": "rasgou na impressão"})
checar(r.status_code == 200 and r.json()["situacao"] == "CANCELADA", "cancelada com motivo")
r = api("POST", "/etiquetas", json={"codigos": "A-100", "local": "Armário 3"})
checar(r.json()["repetidas"] == ["A-100"], "cancelada não se cadastra de novo: volta como repetida")
r = api("DELETE", f"/etiquetas/{alvo['id']}")
checar(r.status_code == 409, f"cancelada não se apaga (veio {r.status_code})")
r = api("PATCH", f"/etiquetas/{alvo['id']}", json={"local": "Outro"})
checar(r.status_code == 409, "cancelada não muda de local")

ids_pat = [x["id"] for x in lista["itens"] if x["codigo"].startswith("PAT")]
r = api("POST", "/etiquetas/mover", json={"ids": ids_pat, "local": "Sala do patrimônio"})
checar(r.status_code == 200 and r.json()["movidas"] == 4, f"mover em lote ({r.json()})")
checar(api("GET", "/etiquetas?local=Sala%20do%20patrim%C3%B4nio").json()["total"] == 4,
       "as 4 aparecem no local novo")

a105 = next(x for x in lista["itens"] if x["codigo"] == "A-105")
r = api("DELETE", f"/etiquetas/{a105['id']}", cookies=OPERA)
checar(r.status_code == 403, "quem edita não apaga (403)")
r = api("DELETE", f"/etiquetas/{a105['id']}")
checar(r.status_code == 204, "admin apaga a disponível")
checar(api("GET", "/etiquetas?busca=A-105").json()["total"] == 0, "apagada some")

print("\n[3] Consumo: na ordem de cadastro, só disponíveis")
with db.SessionLocal() as s:
    proximas = [e.codigo for e in rint.etiquetas_disponiveis(s, 3)]
checar(proximas == ["A-101", "A-102", "A-103"],
       f"as 3 próximas são as mais antigas disponíveis, pulando a cancelada: {proximas}")
with db.SessionLocal() as s:
    todas = [e.codigo for e in rint.etiquetas_disponiveis(s, 100)]
checar(len(todas) == 7 and "A-100" not in todas, f"pedir mais do que há devolve só o que há ({len(todas)})")
with db.SessionLocal() as s:
    checar(rint.etiquetas_disponiveis(s, 0) == [], "quantidade zero não consome nada")

print("\n[4] Itens imobilizados")
r = api("POST", "/itens-imobilizados", json={"itens": [{"item_ebs": "000347191", "descricao": "ZEBRA ZT231"}]})
checar(r.status_code == 201 and r.json()["criados"] == 1, f"inclui um (veio {r.status_code}: {r.text[:120]})")
r = api("POST", "/itens-imobilizados", json={"texto": "347191 ZEBRA IMPRESSORA INDUSTRIAL ZT231\n412000\tDESKTOP POSITIVO\n\n"})
d = r.json()
checar(d["criados"] == 1 and d["atualizados"] == 1,
       f"texto colado: '347191' é o MESMO item que '000347191' (descrição atualizada), 412000 é novo ({d})")
lst = api("GET", "/itens-imobilizados").json()
checar([i["item_ebs"] for i in lst["itens"]] == ["347191", "412000"],
       f"a lista guarda o código sem zeros à esquerda: {[i['item_ebs'] for i in lst['itens']]}")
checar(lst["itens"][0]["descricao"] == "ZEBRA IMPRESSORA INDUSTRIAL ZT231", "a descrição colada substituiu a antiga")
checar(api("GET", "/itens-imobilizados?busca=positivo").json()["total"] == 1, "busca pela descrição")
r = api("POST", "/itens-imobilizados", json={"texto": "@@@ inválido"})
checar(r.status_code == 422, "linha colada com código inválido → 422")
r = api("POST", "/itens-imobilizados", json={})
checar(r.status_code == 422, "nada informado → 422")
with db.SessionLocal() as s:
    cods = rint.codigos_imobilizados(s)
checar(cods == {"347191", "412000"}, f"o conjunto que o recebimento consulta: {cods}")

i412 = next(i for i in lst["itens"] if i["item_ebs"] == "412000")
r = api("PATCH", f"/itens-imobilizados/{i412['id']}", json={"item_ebs": "347191", "descricao": "x"})
checar(r.status_code == 409, "editar para um código que já existe → 409")
r = api("PATCH", f"/itens-imobilizados/{i412['id']}", json={"item_ebs": "412001", "descricao": "DESKTOP POSITIVO MASTER"})
checar(r.status_code == 200 and r.json()["item_ebs"] == "412001", "editar código e descrição")
r = api("DELETE", f"/itens-imobilizados/{i412['id']}", cookies=OPERA)
checar(r.status_code == 403, "quem edita não remove da lista (403)")
r = api("DELETE", f"/itens-imobilizados/{i412['id']}")
checar(r.status_code == 204, "admin remove")
with db.SessionLocal() as s:
    checar(rint.codigos_imobilizados(s) == {"347191"}, "removido sai do conjunto")

print("\n[5] Permissão de quem só vê")
checar(api("GET", "/etiquetas", cookies=SO_VE).status_code == 200, "vê as etiquetas")
checar(api("POST", "/etiquetas", cookies=SO_VE, json={"codigos": "Z-1", "local": "x"}).status_code == 403,
       "não cadastra etiqueta")
checar(api("POST", "/itens-imobilizados", cookies=SO_VE, json={"texto": "1 x"}).status_code == 403,
       "não inclui item")
checar(cliente.get(BASE + "/etiquetas").status_code == 401, "sem sessão: 401")

print("\n[6] Menu e módulo")
html = (RAIZ / "static/index.html").read_text(encoding="utf-8")
for rota in ("internalizacao/etiquetas", "internalizacao/imobilizados"):
    checar(f'data-route="{rota}" data-perm="internalizacao"' in html,
           f"item de menu {rota} sob a permissão internalizacao")
js = (RAIZ / "modulos/internalizacao.js").read_text(encoding="utf-8")
checar("['etiquetas',    'Cadastro de Etiquetas']" in js and "function telaEtiquetas" in js,
       "aba e tela de etiquetas no módulo")
checar("['imobilizados', 'Itens Imobilizados']" in js and "function telaImobilizados" in js,
       "aba e tela de imobilizados no módulo")
checar("/internalizacao/cadastro/etiquetas" in js and "/internalizacao/cadastro/itens-imobilizados" in js,
       "as telas falam com as rotas novas")
checar("<script" not in js, "nada de script inline no módulo (CSP)")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhou:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Cadastros de etiquetas e itens imobilizados prontos para o recebimento consumir.")
