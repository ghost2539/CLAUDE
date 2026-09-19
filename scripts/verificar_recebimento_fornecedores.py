#!/usr/bin/env python3
"""Verificação do Recebimento de Fornecedores pelo agendamento."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="rec-forn-verif-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEMP}/portal.db"
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-com-64-caracteres-de-sobra-aqui")
os.environ["INITIAL_ADMIN_LOGIN"] = "admin.teste"
os.environ["AMBIENTE"] = "testes"
for _m in ("TRILHA", "OBSOLESCENCIA", "REVERSA", "AGENDAMENTOS_FORN", "INTERNALIZACAO"):
    os.environ[f"{_m}_DATABASE_URL"] = f"sqlite:///{_TEMP}/{_m.lower()}.db"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")
os.environ["PORTAL_COFRE_DIR"] = f"{_TEMP}/cofre"

import config as _config  # noqa: E402
_config.get_settings().DATA = Path(_TEMP) / "data"

POS = {
    "4512345": [
        {"po_numero": "4512345", "liberacao": None, "fornecedor": "ZEBRA DO BRASIL", "status_po": "APPROVED",
         "moeda": "BRL", "linha": 1, "item_ebs": "347191", "descricao": "ZEBRA IMPRESSORA INDUSTRIAL ZT231",
         "unidade": "UN", "quantidade_pedida": 3, "quantidade_recebida": 0, "quantidade_pendente": 3},
        {"po_numero": "4512345", "liberacao": None, "fornecedor": "ZEBRA DO BRASIL", "status_po": "APPROVED",
         "moeda": "BRL", "linha": 2, "item_ebs": "990001", "descricao": "CABO USB ZEBRA",
         "unidade": "UN", "quantidade_pedida": 3, "quantidade_recebida": 0, "quantidade_pendente": 3},
    ],
    "4599999": [
        {"po_numero": "4599999", "liberacao": None, "fornecedor": "X", "status_po": "APPROVED", "moeda": "BRL",
         "linha": 1, "item_ebs": "990002", "descricao": "FONTE", "unidade": "UN",
         "quantidade_pedida": 1, "quantidade_recebida": 0, "quantidade_pendente": 1},
    ],
}
falso = types.ModuleType("integracoes.ebs_oracle")
falso.run_named = lambda nome, binds, max_rows=500: list(POS.get(binds["numero_po"], []))
sys.modules["integracoes.ebs_oracle"] = falso

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from main import app  # noqa: E402
import core.security as sec  # noqa: E402
import db.agendamentos_forn as agf_db  # noqa: E402
import db.internalizacao as int_db  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d) -> None:
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


c = TestClient(app)


def sessao(nome, modulos, admin=False):
    pm = {m: {"can_view": True, "can_create": True, "can_edit": True, "can_admin": True} for m in modulos}
    _, cookie = sec.create_session({"username": nome, "is_admin": admin, "permission_map": pm,
                                    "permissions": list(modulos)})
    return {"spare_session": cookie}


ADMIN = sessao("admin.teste", ("recebimento", "internalizacao", "agendamentos_forn"), admin=True)
REC = sessao("rec.teste", ("recebimento",))
SO_AGF = sessao("agf.teste", ("agendamentos_forn",))
B = "/api/recebimento/fornecedores"


def agendar(bu, pedidos, equipamentos, fornecedor="ZEBRA DO BRASIL"):
    r = c.post("/api/agendamentos-forn", cookies=ADMIN, json={
        "bu": bu, "fornecedor": fornecedor, "estoque_destino": "REPOSICAO",
        "data_agendada": "2026-09-30", "pedidos": pedidos, "equipamentos": equipamentos})
    assert r.status_code == 201, r.text
    return r.json()["id"]


print("[1] Rota antiga e digitação solta fechadas")
r = c.post("/api/agendamentos-forn/1/receber", cookies=ADMIN)
checar(r.status_code in (404, 405), f"POST /agendamentos-forn/1/receber não existe mais ({r.status_code})")
r = c.post("/api/recebimento/bulk-submit", cookies=ADMIN, json={
    "origem": "FORNECEDOR", "espaco_corredor": "A-1",
    "items": [{"descricao": "x", "numero_serie": "S1", "po": "1", "nf": "2", "subcategoria": "PDV"}]})
checar(r.status_code == 400 and "agendamento" in r.json()["detail"].lower(),
       "bulk-submit com origem FORNECEDOR aponta o agendamento")

print("\n[2] Lista e permissões")
a1 = agendar("Renner", [{"po": "4512345", "nf": "200153"}], [{"descricao": "ZEBRA ZT231", "quantidade": 3}])
checar(c.get(B, cookies=REC).status_code == 200, "quem tem recebimento lista")
checar(c.get(B, cookies=SO_AGF).status_code == 403, "quem só tem agendamentos não lista (403)")
checar(c.get(B).status_code == 401, "sem sessão 401")
d = c.get(B, cookies=REC).json()
checar(d["total"] == 1 and d["itens"][0]["tem_ebs"] and d["itens"][0]["status"] == "AGENDADO",
       "o agendamento aparece com tem_ebs e status")

print("\n[3] Preparar: PO no EBS, lista de imobilizados, etiquetas")
r = c.get(f"{B}/{a1}/preparar", cookies=REC)
checar(r.status_code == 422 and "imobilizados" in r.json()["detail"].lower(),
       f"sem item na lista de imobilizados → 422 ({r.status_code})")
c.post("/api/internalizacao/cadastro/itens-imobilizados", cookies=ADMIN,
       json={"itens": [{"item_ebs": "000347191", "descricao": "ZEBRA ZT231"}]})
r = c.get(f"{B}/{a1}/preparar", cookies=REC)
checar(r.status_code == 200, f"com o item na lista, prepara ({r.status_code}: {r.text[:120]})")
p = r.json()
checar(len(p["itens"]) == 1 and p["itens"][0]["item_ebs"] == "347191" and p["itens"][0]["linha"] == 1
       and p["itens"][0]["quantidade_pendente"] == 3, f"o item imobilizado vem com linha e pendente: {p['itens']}")
checar(len(p["nao_imobilizados"]) == 1 and p["nao_imobilizados"][0]["item_ebs"] == "990001",
       "o cabo fica em não imobilizados")
checar(p["notas"] == [{"nf": "200153", "chave": "", "vencimento": "", "origem": "", "origem_rotulo": "",
                       "tem_xml": False, "tem_pdf": False, "itens": [], "emitente": "", "cstat": "", "erro": ""}],
       "a NF do agendamento aparece sem chave")
checar(p["etiquetas_disponiveis"] == 0 and any("etiqueta" in a for a in p["avisos"]),
       "avisa que não há etiquetas")
checar(p["certificado_bu"] is False, "sem certificado configurado")

a_semPO = agendar("Camicado", [{"po": "0000000", "nf": "1"}], [])
r = c.get(f"{B}/{a_semPO}/preparar", cookies=REC)
checar(r.status_code == 404 and "0000000" in r.json()["detail"], f"PO não achada no EBS → 404 dizendo qual ({r.status_code})")
a_fora = agendar("Renner", [{"po": "4599999", "nf": "2"}], [])
checar(c.get(f"{B}/{a_fora}/preparar", cookies=REC).status_code == 422, "PO só com não imobilizados → 422")

print("\n[4] Nota: chave sem certificado, arquivo XML")
r = c.post(f"{B}/{a1}/nota", cookies=REC, json={"nf": "200153", "chave": "1" * 43})
checar(r.status_code == 422, "chave com 43 dígitos → 422")
r = c.post(f"{B}/{a1}/nota", cookies=REC, json={"nf": "999", "chave": ""})
checar(r.status_code == 422, "NF que não está no agendamento → 422")
CHAVE = "35250912345678000199550010000020010000002015"
r = c.post(f"{B}/{a1}/nota", cookies=REC, json={"nf": "200153", "chave": " ".join(CHAVE[i:i+4] for i in range(0, 44, 4)), "vencimento": "2026-10-30"})
checar(r.status_code == 200 and r.json()["chave"] == CHAVE
       and r.json()["origem"] == "DIGITADA" and "certificado" in r.json()["erro"].lower(),
       f"chave gravada, sem certificado explica ({r.status_code}: {r.text[:160]})")
checar(r.json()["vencimento"] == "2026-10-30", "vencimento gravado")

XML = """<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe35250912345678000199550010000020010000002015" versao="4.00">
<ide><nNF>200153</nNF><serie>1</serie><dhEmi>2026-09-10T10:00:00-03:00</dhEmi></ide>
<emit><CNPJ>12345678000199</CNPJ><xNome>ZEBRA DO BRASIL</xNome></emit>
<dest><CNPJ>92754738000162</CNPJ></dest>
<det nItem="1"><prod><cProd>347191</cProd><xProd>ZEBRA IMPRESSORA INDUSTRIAL ZT231</xProd><qCom>2.0000</qCom><xPed>4512345</xPed></prod></det>
<det nItem="2"><prod><cProd>990001</cProd><xProd>CABO USB ZEBRA</xProd><qCom>2.0000</qCom></prod></det>
<total><ICMSTot><vNF>15000.00</vNF></ICMSTot></total>
<cobr><dup><nDup>001</nDup><dVenc>2026-10-30</dVenc><vDup>15000.00</vDup></dup></cobr>
</infNFe></NFe></nfeProc>"""
r = c.post(f"{B}/{a1}/nota/arquivo", cookies=REC, data={"nf": "200153"},
           files={"arquivo": ("nf.xml", XML.encode("utf-8"), "application/xml")})
checar(r.status_code == 200, f"upload do XML ({r.status_code}: {r.text[:200]})")
n = r.json()
checar(n["origem"] == "ARQUIVO" and n["tem_xml"] and len(n["itens"]) == 2 and n["itens"][0]["quantidade"] == 2,
       f"XML lido: origem, arquivo e 2 itens com quantidade ({n['itens']})")
r = c.get(f"{B}/{a1}/nota/200153/xml", cookies=REC)
checar(r.status_code == 200 and b"<nfeProc" in r.content, "o XML guardado baixa")
checar(c.get(f"{B}/{a1}/nota/200153/pdf", cookies=REC).status_code in (200, 404), "o PDF baixa ou diz que não há")
checar(c.get(f"{B}/{a1}/nota/200153/exe", cookies=REC).status_code == 404, "tipo desconhecido → 404")
checar(sorted(x.name for x in (Path(_TEMP) / "data" / "tmp" / "recebimento_forn" / str(a1)).iterdir())[:1] == ["nf_200153.xml"]
       or (Path(_TEMP) / "data" / "tmp" / "recebimento_forn" / str(a1) / "nf_200153.xml").exists(),
       "o XML mora em data/tmp/recebimento_forn/<id>/")
p = c.get(f"{B}/{a1}/preparar", cookies=REC).json()
checar(p["itens"][0]["quantidade_nf"] == 2, f"preparar casa o item com a NF: quantidade_nf=2 ({p['itens'][0]['quantidade_nf']})")
r = c.post(f"{B}/{a1}/nota/arquivo", cookies=REC, data={"nf": "200153"},
           files={"arquivo": ("x.txt", b"nada", "text/plain")})
checar(r.status_code == 422, "arquivo que não é XML nem PDF → 422")

print("\n[5] Confirmar: validações antes de gravar")
item = {"po": "4512345", "nf": "200153", "linha": 1, "item_ebs": "347191",
        "descricao": "ZEBRA IMPRESSORA INDUSTRIAL ZT231", "unidade": "UN",
        "quantidade_pedida": 3, "quantidade_recebida": 2, "imobilizado": True, "seriais": ["S1", "S2"]}
cabo = {"po": "4512345", "nf": "200153", "linha": 2, "item_ebs": "990001", "descricao": "CABO USB ZEBRA",
        "unidade": "UN", "quantidade_pedida": 3, "quantidade_recebida": 2, "imobilizado": False, "seriais": []}


def confirmar(itens, cookies=REC, obs=""):
    return c.post(f"{B}/{a1}/confirmar", cookies=cookies, json={"itens": itens, "observacao": obs})


r = confirmar([dict(item, seriais=["S1"])])
checar(r.status_code == 422 and "serial" in r.json()["detail"].lower(), "2 recebidos com 1 serial → 422")
r = confirmar([dict(item, seriais=["S1", "s1"])])
checar(r.status_code == 422 and "repetido" in r.json()["detail"].lower(), "serial repetido (sem caixa) → 422")
r = confirmar([dict(item, quantidade_recebida=3, seriais=["S1", "S2", "S3"])])
checar(r.status_code == 422 and "NF 200153 traz 2" in r.json()["detail"], f"recebido 3 com a NF dizendo 2 → 422 ({r.json()['detail'][:100]})")
r = confirmar([dict(item, item_ebs="777")])
checar(r.status_code == 422 and "lista de imobilizados" in r.json()["detail"], "item fora da lista de imobilizados → 422")
r = confirmar([dict(cabo)])
checar(r.status_code == 422, "só não imobilizado → 422")
r = confirmar([dict(item), dict(cabo)])
checar(r.status_code == 409 and "Faltam 2 etiqueta" in r.json()["detail"], f"sem etiquetas → 409 dizendo quantas faltam ({r.json()['detail'][:80]})")
with agf_db.SessionLocal() as s:
    checar(s.get(agf_db.Agendamento, a1).status == "AGENDADO" and s.scalar(select(agf_db.Recebimento)) is None,
           "nada foi gravado em nenhuma recusa")
checar(confirmar([dict(item)], cookies=SO_AGF).status_code == 403, "quem só tem agendamentos não confirma")

print("\n[6] Confirmar: grava, consome etiquetas, abre o lançamento")
c.post("/api/internalizacao/cadastro/etiquetas", cookies=ADMIN, json={"prefixo": "PAT", "de": "0001", "ate": "0003", "local": "Armário 3"})
r = confirmar([dict(item), dict(cabo)], obs="chegou às 10h")
checar(r.status_code == 200, f"confirma ({r.status_code}: {r.text[:200]})")
d = r.json()
checar(d["ok"] and d["entrega_parcial"] and d["faltantes"] == [{"descricao": "ZEBRA IMPRESSORA INDUSTRIAL ZT231", "faltam": 1}],
       f"entrega parcial marcada com o que falta ({d.get('faltantes')})")
checar(d["processo_id"] and d["etiquetas_consumidas"] == 2 and d["aviso"] == "", f"processo aberto, 2 etiquetas ({d.get('aviso')})")
checar(d["agendamento"]["status"] == "RECEBIDO" and d["agendamento"]["entrega_parcial"], "agendamento RECEBIDO e parcial")
with agf_db.SessionLocal() as s:
    ag = s.get(agf_db.Agendamento, a1)
    rec = ag.recebimento
    checar(rec.recebido_por == "rec.teste" and rec.tem_nao_imobilizados and rec.observacao == "chegou às 10h",
           "recebimento gravado com quem, não imobilizados e observação")
    checar([(i.item_ebs, i.quantidade_recebida, i.quantidade_nf, i.imobilizado, [u.serial for u in i.unidades]) for i in rec.itens]
           == [("347191", 2, 2, True, ["S1", "S2"]), ("990001", 2, 2, False, [])], "itens e unidades gravados")
with int_db.SessionLocal() as si:
    proc = si.scalar(select(int_db.Processo).where(int_db.Processo.agendamento_id == a1))
    ativos = [(a.plaqueta, a.numero_serie, a.ebs_item, a.po, a.linha, a.nf, a.etiqueta_local) for a in proc.ativos]
    checar(ativos == [("PAT0001", "S1", "347191", "4512345", 1, "200153", "Armário 3"),
                      ("PAT0002", "S2", "347191", "4512345", 1, "200153", "Armário 3")],
           f"ativos do lançamento com etiqueta, serial, item, PO/linha, NF e local: {ativos}")
    etqs = {e.codigo: (e.situacao, e.ativo_id is not None) for e in si.scalars(select(int_db.Etiqueta)).all()}
    checar(etqs == {"PAT0001": ("CONSUMIDA", True), "PAT0002": ("CONSUMIDA", True), "PAT0003": ("DISPONIVEL", False)},
           f"duas etiquetas consumidas na ordem, uma sobrou: {etqs}")
checar(confirmar([dict(item)]).status_code == 409, "confirmar de novo → 409")
checar(c.get(f"{B}/{a1}/preparar", cookies=REC).status_code == 409, "preparar depois de recebido → 409")
r = c.get(f"{B}/{a1}/confirmar", cookies=REC)
d = c.get(B + "?status=RECEBIDO", cookies=REC).json()
checar(d["total"] == 1 and d["itens"][0]["entrega_parcial"], "a lista mostra o recebido como parcial")

print("\n[7] O Lançamento vê as linhas prontas e não perde a etiqueta ao salvar")
r = c.get(f"/api/internalizacao/{a1}", cookies=ADMIN)
checar(r.status_code == 200 and len(r.json()["ativos"]) == 2 and r.json()["ativos"][0]["etiqueta_local"] == "Armário 3",
       "GET /internalizacao/{id} devolve os ativos com o local da etiqueta")
checar(r.json()["recebimento"]["entrega_parcial"] and r.json()["notas"][0]["nf"] == "200153", "vem com o recebimento e as notas")
lst = c.get("/api/internalizacao", cookies=ADMIN).json()
checar(lst["itens"][0]["entrega_parcial"] and lst["itens"][0]["total_ativos"] == 2, "a lista do lançamento marca parcial e conta 2")
ids = [a["id"] for a in r.json()["ativos"]]
r = c.put(f"/api/internalizacao/{a1}", cookies=ADMIN, json={"ativos": [
    {"id": ids[0], "ebs_item": "347191", "descricao": "ZEBRA ZT231", "plaqueta": "TROCADA", "numero_serie": "S1-CORRIGIDO"},
    {"id": ids[1], "ebs_item": "347191", "descricao": "ZEBRA ZT231", "plaqueta": "PAT0002", "numero_serie": "S2"},
], "concluir": False})
checar(r.status_code == 200, f"salvar ({r.status_code}: {r.text[:120]})")
at = r.json()["ativos"]
checar(at[0]["plaqueta"] == "PAT0001" and at[0]["numero_serie"] == "S1-CORRIGIDO", "serial corrigido, etiqueta mantida")
r = c.put(f"/api/internalizacao/{a1}", cookies=ADMIN, json={"ativos": [
    {"id": ids[0], "ebs_item": "347191", "descricao": "ZEBRA ZT231", "plaqueta": "PAT0001", "numero_serie": "S1"}], "concluir": False})
with int_db.SessionLocal() as si:
    e2 = si.scalar(select(int_db.Etiqueta).where(int_db.Etiqueta.codigo == "PAT0002"))
    checar(e2.situacao == "DISPONIVEL" and e2.ativo_id is None, "linha removida devolve a etiqueta ao estoque")

print("\n[8] Youcom: manual, sem EBS")
a2 = agendar("Youcom", [{"po": "YC-1", "nf": "777"}], [{"descricao": "NOTEBOOK DELL", "quantidade": 2}], fornecedor="DELL")
p = c.get(f"{B}/{a2}/preparar", cookies=REC).json()
checar(p["tem_ebs"] is False and p["itens"][0]["manual"] and p["itens"][0]["descricao"] == "NOTEBOOK DELL"
       and p["itens"][0]["quantidade_pedida"] == 2, f"itens vêm do agendamento, editáveis ({p['itens']})")
r = c.post(f"{B}/{a2}/confirmar", cookies=REC, json={"itens": [
    {"po": "YC-1", "nf": "777", "item_ebs": "", "descricao": "NOTEBOOK DELL", "quantidade_pedida": 3,
     "quantidade_recebida": 3, "imobilizado": True, "seriais": ["DL1", "DL2", "DL3"]},
    {"po": "YC-1", "nf": "777", "item_ebs": "", "descricao": "MOUSE", "quantidade_pedida": 2,
     "quantidade_recebida": 2, "imobilizado": False, "seriais": []}]})
checar(r.status_code == 409 and "Faltam 1" in r.json()["detail"], f"Youcom com 3 unidades e 2 etiquetas → 409 ({r.json().get('detail', '')[:60]})")
r = c.post(f"{B}/{a2}/confirmar", cookies=REC, json={"itens": [
    {"po": "YC-1", "nf": "777", "item_ebs": "", "descricao": "NOTEBOOK DELL", "quantidade_pedida": 2,
     "quantidade_recebida": 2, "imobilizado": True, "seriais": ["DL1", "S1"]}]})
checar(r.status_code == 422 and "S1" in r.json()["detail"] and "já está no portal" in r.json()["detail"],
       "serial já recebido noutro agendamento → 422")
r = c.post(f"{B}/{a2}/confirmar", cookies=REC, json={"itens": [
    {"po": "YC-1", "nf": "777", "item_ebs": "", "descricao": "NOTEBOOK DELL", "quantidade_pedida": 2,
     "quantidade_recebida": 2, "imobilizado": True, "seriais": ["DL1", "DL2"]},
    {"po": "YC-1", "nf": "777", "item_ebs": "", "descricao": "MOUSE", "quantidade_pedida": 2,
     "quantidade_recebida": 2, "imobilizado": False, "seriais": []}]})
checar(r.status_code == 200 and not r.json()["entrega_parcial"] and r.json()["etiquetas_consumidas"] == 2,
       f"Youcom confirma completo com 2 etiquetas ({r.status_code}: {r.text[:120]})")
with int_db.SessionLocal() as si:
    proc2 = si.scalar(select(int_db.Processo).where(int_db.Processo.agendamento_id == a2))
    checar(sorted(a.plaqueta for a in proc2.ativos) == ["PAT0002", "PAT0003"], "as duas etiquetas que restavam foram consumidas")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhou:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Recebimento de Fornecedores: conferência, etiquetas e lançamento aberto.")
