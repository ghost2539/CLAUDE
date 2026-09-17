#!/usr/bin/env python3
"""Verificação da origem da entrada: fornecedor ou reversa.

    python3 scripts/verificar_recebimento_origem.py

Reversa é o que volta da loja: já existe no EBS e entra pela leitura.
Fornecedor é compra nova, que não existe em lugar nenhum — por isso o
operador digita descrição, série, PO e nota, e nenhum dos quatro é
opcional: sem série o ativo é impossível de achar depois, sem nota é
impossível de conferir com o financeiro.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="rec-origem-verif-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP}/portal.db"
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-com-64-caracteres-de-sobra-aqui")
os.environ["INITIAL_ADMIN_LOGIN"] = "admin.teste"
os.environ["AMBIENTE"] = "testes"
for _modulo in ("TRILHA", "OBSOLESCENCIA", "REVERSA"):
    os.environ[f"{_modulo}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{_modulo.lower()}.db"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")

from fastapi.testclient import TestClient            # noqa: E402
from sqlalchemy import select                        # noqa: E402

from main import app                                 # noqa: E402
import core.security as sec                          # noqa: E402
import routers.recebimento as rec                    # noqa: E402
from db.portal import SessionLocal, Setting, ReceiptCycle  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d) -> None:
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


c = TestClient(app).__enter__()   # com lifespan: init_db cria as tabelas
_, cookie = sec.create_session({"username": "admin.teste", "is_admin": True,
                                "permissions": ["admin"], "permission_map": {},
                                "user_id": 1, "ebs_auth": None})
CK = {sec.COOKIE_SESSAO: cookie}

# O espelho no ServiceNow fica de fora: aqui se verifica a gravação, não
# a integração — essa tem script próprio.
with SessionLocal.begin() as s:
    linha = s.get(Setting, rec.CONFIG_SN) or Setting(key=rec.CONFIG_SN)
    linha.value = {"ativo": False}
    s.add(linha)


def enviar(origem, itens, espaco="A-12"):
    return c.post("/api/recebimento/bulk-submit", cookies=CK,
                  json={"items": itens, "espaco_corredor": espaco, "origem": origem})


def ultimo_ciclo() -> dict:
    """O último ciclo já resolvido em dicionário: fora da sessão, o ORM
    não consegue mais buscar o ativo relacionado."""
    with SessionLocal() as s:
        c = s.scalar(select(ReceiptCycle).order_by(ReceiptCycle.id.desc()))
        return {"origem": c.origem_entrada, "po": c.po, "nf": c.nf,
                "serie": c.asset.serial_number, "descricao": c.asset.description,
                "fonte": c.asset.source}


print("[1] Fornecedor exige o que o EBS não tem como dar")
COMPRA = {"descricao": "Coletor TC22", "numero_serie": "SN-NOVO-1", "po": "PO-900",
          "nf": "NF-77", "destino_entrada": "TRIAGEM", "subcategoria": "Coletor"}
for campo, rotulo in (("descricao", "descrição do item"), ("numero_serie", "serial number"),
                      ("po", "PO"), ("nf", "NF")):
    item = dict(COMPRA)
    item[campo] = ""
    r = enviar("FORNECEDOR", [item])
    checar(r.status_code == 400 and rotulo in r.json().get("detail", ""),
           f"sem {rotulo}: recusa dizendo o que falta")
checar(enviar("PIRATA", [dict(COMPRA)]).status_code == 422, "origem inventada é recusada")

print("\n[2] Compra gravada")
r = enviar("FORNECEDOR", [dict(COMPRA)])
checar(r.status_code == 200 and r.json()["criados"] == 1, "com os quatro campos, grava")
ciclo = ultimo_ciclo()
checar(ciclo["origem"] == "FORNECEDOR", "origem fica no ciclo, que é o evento de entrada")
checar(ciclo["po"] == "PO-900" and ciclo["nf"] == "NF-77", "PO e nota ficam no ciclo")
checar(ciclo["serie"] == "SN-NOVO-1" and ciclo["descricao"] == "Coletor TC22",
       "série e descrição ficam no ativo")
checar(ciclo["fonte"] == "FORNECEDOR", "a fonte do ativo é o fornecedor, não o EBS")
registro = c.get("/api/recebimentos", cookies=CK).json()["registros"][0]
checar(registro["origem_entrada"] == "FORNECEDOR" and registro["po"] == "PO-900"
       and registro["nf"] == "NF-77", "a listagem devolve origem, PO e nota")

print("\n[3] Reversa segue como era")
r = enviar("REVERSA", [{"etiqueta": "ETQ-1", "numero_serie": "SN-REV-1", "descricao": "PDV",
                        "categoria": "PDV", "modelo": "Dell", "destino_entrada": "TRIAGEM",
                        "subcategoria": "PDV"}])
checar(r.status_code == 200 and r.json()["criados"] == 1, "grava sem PO nem nota")
ciclo = ultimo_ciclo()
checar(ciclo["origem"] == "REVERSA" and ciclo["po"] == "" and ciclo["nf"] == "",
       "fica marcada como reversa, sem nota")
checar(enviar("REVERSA", [{"etiqueta": "ETQ-2", "numero_serie": "SN-REV-2",
                           "destino_entrada": "TRIAGEM", "subcategoria": "PDV"}]).status_code == 200,
       "e não passou a exigir descrição")
checar(enviar("REVERSA", [{"etiqueta": "ETQ-3", "numero_serie": "SN-REV-3",
                           "destino_entrada": "TRIAGEM"}]).status_code == 400,
       "a subcategoria continua obrigatória para ir à triagem")

print("\n[4] A tela")
tela = (RAIZ / "static/modules/recebimento.js").read_text(encoding="utf-8")
checar("id=\"rec-origem\"" in tela, "a tela começa pela escolha da origem")
for campo in ("fo-desc", "fo-serie", "fo-po", "fo-nf"):
    checar(campo in tela, f"o formulário do fornecedor tem {campo}")
checar("Envie ou descarte a sessão atual antes de trocar a origem" in tela,
       "não deixa misturar compra com devolução na mesma sessão")
checar("origem: origem" in tela, "o envio leva a origem")
menu = (RAIZ / "static/index.html").read_text(encoding="utf-8")
entrada = menu.split('sidebar-grupo-titulo">Entrada</div>')[1].split("</div>")[0]
checar("recebimento" in entrada and "identificacao" in entrada and "reversa" not in entrada,
       "Entrada tem só Recebimento e Identificação")
atendimento = menu.split('sidebar-grupo-titulo">Atendimento</div>')[1].split("<div class=\"sidebar-grupo\"")[0]
checar('data-route="reversa"' in atendimento, "Logística Reversa agora fica em Atendimento")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Origem da entrada íntegra.")
