#!/usr/bin/env python3
"""O admin do portal apaga registro, e fica registrado quem apagou.

    python3 scripts/verificar_exclusoes_admin.py

Roda contra bancos SQLite temporários, sem rede.

O que estava errado
-------------------
As rotas de exclusão existiam e ninguém alcançava: a TELA não tinha o
botão. Cadastro de modelos (regras de classificação) e Internalização
tinham `DELETE` no back-end e só Editar/Aplicar na tela.

O que se confere aqui
---------------------
1. Quem NÃO é admin continua barrado (a tela esconder o botão não é
   controle: a rota é chamável direto).
2. O admin do portal passa, mesmo sem a permissão do módulo marcada.
3. A exclusão leva junto o que é filho (os ativos do processo) e não
   deixa órfão.
4. Sobra registro de quem apagou o quê: apagar não desfaz.
5. A tela oferece o botão — e só para o admin do portal.
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
os.environ["INTERNALIZACAO_DATABASE_URL"] = f"sqlite:///{_TMP/'int.db'}"
os.environ["AGENDAMENTOS_FORN_DATABASE_URL"] = f"sqlite:///{_TMP/'agf.db'}"

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

import db.portal as portal  # noqa: E402

portal.init_db()

import core.security as sec  # noqa: E402
import db.internalizacao as dbi  # noqa: E402
import routers.internalizacao as ri  # noqa: E402
import routers.parametros as rp  # noqa: E402

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


class Req:
    headers: dict = {}
    client = None


ADMIN = {"username": "admin.portal", "is_admin": True, "auth_source": "SSO"}
COMUM = {"username": "operador", "is_admin": False, "auth_source": "SSO",
         "permission_map": {"parametros": {"can_view": True},
                            "internalizacao": {"can_view": True, "can_edit": True}}}

_sessao = {"atual": ADMIN}
sec.get_session = lambda req: _sessao["atual"]
ri.client_ip = lambda req: "10.0.0.1"
rp.client_ip = lambda req: "10.0.0.1"


def como(quem: dict) -> None:
    _sessao["atual"] = quem


print("[1] Cadastro de modelos: a regra some, o ativo classificado não")
with portal.SessionLocal.begin() as s:
    regra = portal.Classification(description_pattern="COLETOR HF550%",
                                  company="RENNER", category="Coletor",
                                  model="HF550", active=True)
    s.add(regra)
    s.flush()
    regra_id = regra.id

como(COMUM)
try:
    rp.classification_delete(regra_id, Req())
    checar(False, "sem ser admin, a exclusão deveria ser recusada")
except HTTPException as exc:
    checar(exc.status_code == 403, f"quem não é admin é barrado na ROTA ({exc.status_code})")

como(ADMIN)
rp.classification_delete(regra_id, Req())
with portal.SessionLocal() as s:
    checar(s.get(portal.Classification, regra_id) is None, "o admin do portal apaga a regra")
    ultimo = s.scalars(select(portal.AccessLog)
                       .order_by(portal.AccessLog.id.desc()).limit(1)).first()
    checar(ultimo is not None and "classificação" in (ultimo.detail or "").lower(),
           f"fica no log quem apagou o quê ({(ultimo.detail if ultimo else '')[:52]}…)")
    checar(ultimo is not None and ultimo.login == "admin.portal",
           "o log guarda o login de quem apagou")

try:
    rp.classification_delete(regra_id, Req())
    checar(False, "apagar duas vezes deveria dar 404")
except HTTPException as exc:
    checar(exc.status_code == 404, f"regra que não existe mais dá 404 ({exc.status_code})")

print("\n[2] Internalização: o processo some com os ativos dele")
dbi.ensure_db()
with dbi.SessionLocal.begin() as s:
    proc = dbi.Processo(agendamento_id=77, nf="NF-9988")
    proc.ativos = [dbi.Ativo(descricao=f"Coletor {i}", numero_serie=f"S{i}",
                             plaqueta=f"PAT-{i}") for i in range(3)]
    s.add(proc)

with dbi.SessionLocal() as s:
    checar(s.scalar(select(func.count(dbi.Ativo.id))) == 3, "processo montado com 3 ativos")

como(COMUM)
try:
    ri.excluir(77, Req())
    checar(False, "sem ser admin, a exclusão deveria ser recusada")
except HTTPException as exc:
    checar(exc.status_code == 403,
           f"can_edit no módulo NÃO basta para apagar ({exc.status_code})")

como(ADMIN)
ri.excluir(77, Req())
with dbi.SessionLocal() as s:
    checar(s.scalar(select(func.count(dbi.Processo.id))) == 0, "o processo some")
    checar(s.scalar(select(func.count(dbi.Ativo.id))) == 0,
           "e os ativos dele vão junto — sem órfão na base")

with portal.SessionLocal() as s:
    ultimo = s.scalars(select(portal.AccessLog)
                       .order_by(portal.AccessLog.id.desc()).limit(1)).first()
    detalhe = (ultimo.detail if ultimo else "")
    checar("NF-9988" in detalhe, f"o log diz qual NF ({detalhe[:56]}…)")
    checar("3 ativo" in detalhe, "e quantos ativos se perderam")

try:
    ri.excluir(77, Req())
    checar(False, "apagar duas vezes deveria dar 404")
except HTTPException as exc:
    checar(exc.status_code == 404, f"processo que não existe mais dá 404 ({exc.status_code})")

print("\n[3] A tela oferece o botão, e só para o admin do portal")
# Procurar `is_admin` ou `confirm(` no arquivo inteiro não prova nada: os dois
# aparecem em outros pontos da mesma tela. A conferência é sobre a VIZINHANÇA
# da chamada — a rota e o DELETE no mesmo trecho, e o resto por perto.
import re  # noqa: E402

JANELA = 900   # caracteres em volta da chamada


def _trechos_delete(texto: str, rota: str) -> list[str]:
    """Pedaços do arquivo onde a rota e um método DELETE aparecem juntos."""
    fora = []
    for m in re.finditer(re.escape(rota), texto):
        ini, fim = max(0, m.start() - JANELA), m.end() + JANELA
        trecho = texto[ini:fim]
        if re.search(r"method:\s*['\"]DELETE['\"]", trecho):
            fora.append(trecho)
    return fora


CASOS = (
    ("modulos/recebimento.js", "/parametros/classificacoes/", "Cadastro de modelos"),
    ("modulos/internalizacao.js", "/internalizacao/", "Internalização"),
)
for arquivo, rota, rotulo in CASOS:
    texto = (RAIZ / arquivo).read_text(encoding="utf-8")
    trechos = _trechos_delete(texto, rota)
    checar(bool(trechos), f"{rotulo}: a tela chama DELETE em {rota}")
    checar(any("is_admin" in t for t in trechos),
           f"{rotulo}: o botão fica ao lado de is_admin")
    checar(any("confirm(" in t for t in trechos),
           f"{rotulo}: pede confirmação antes de apagar")

# O próprio detector, contra um caso plantado: sem o DELETE por perto, não vale.
_FALSO = """
    if (u.is_admin) { window.confirm('apaga?'); }
    // ... 2000 caracteres de distancia ...
""" + ("x" * 2000) + """
    await S.api('/parametros/classificacoes/' + id, { method: 'PUT' });
"""
checar(not _trechos_delete(_FALSO, "/parametros/classificacoes/"),
       "o detector não aceita is_admin/confirm longe da chamada")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Exclusão pelo admin com problema:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("Exclusão pelo admin do portal íntegra.")
