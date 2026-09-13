#!/usr/bin/env python3
"""Verificação do espelho do Recebimento no ServiceNow e da saída do MDM.

    python3 scripts/verificar_recebimento_sn.py

Cobre o que a área pediu: ativo ausente é CRIADO no estoque do CD, ativo
existente é ATUALIZADO com os dados do recebimento, o Espaço e Corredor é
obrigatório antes de subir, e o coletor recebido sai do MDM — só ele, não
a base toda.
"""
from __future__ import annotations
import os, re, sys, tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_T = tempfile.mkdtemp(prefix="rec-sn-verif-")
for v in sorted(set(re.findall(r"([A-Z_]*DATABASE_URL)", (RAIZ / "config.py").read_text()))):
    os.environ[v] = f"sqlite:///{_T}/{v.lower()}.db"
os.environ["PORTAL_SESSION_SECRET"] = "verificacao-local"
os.environ.setdefault("EBS_LOGIN_URL", "http://x")
os.environ.setdefault("EBS_SEARCH_URL", "http://x")

import routers.servicenow as sn        # noqa: E402
import routers.obsolescencia as ob     # noqa: E402
import db.obsolescencia as dbo         # noqa: E402
from fastapi import HTTPException      # noqa: E402

falhas, feitos = [], 0
def checar(c, d):
    global feitos; feitos += 1
    print(("  ok   " if c else "  FALHA ") + d)
    if not c: falhas.append(d)

# ── Dublês do ServiceNow ──────────────────────────────────────────
ESCRITAS, INSERIDOS, EXISTENTES = [], [], []
sn._get_http = lambda: (None, None)
sn._lookup_reference = lambda sessao, campo, valor, cache, BS: {
    "SPARE - CD324": "sid-cd324", "Zebra TC21": "sid-modelo",
    "Coletor": "sid-categoria"}.get(valor, "")
sn._hardware_records = lambda sessao, itens: list(EXISTENTES)
def _upd(sessao, tabela, sys_id, alteracao):
    ESCRITAS.append((sys_id, dict(alteracao))); return True
sn._sn_update = _upd
def _ins(sessao, registro):
    INSERIDOS.append(dict(registro)); return True, "novo-sys-id", "criado"
sn._insert_record = _ins

def limpar():
    ESCRITAS.clear(); INSERIDOS.clear(); EXISTENTES.clear()

ITEM_NOVO = {"serial": "SN-NOVO-1", "etiqueta": "RN-NOVO-1", "modelo": "Zebra TC21",
             "categoria": "Coletor", "numero_ativo": "900001"}
ITEM_EXISTE = {"serial": "SN-VELHO-1", "etiqueta": "RN-VELHO-1", "modelo": "Zebra TC21",
               "categoria": "Coletor", "numero_ativo": "900002"}

print("\n[1] Espaço e Corredor é obrigatório")
limpar()
try:
    sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="")
    checar(False, "sem espaço, recusa (não recusou)")
except HTTPException as e:
    checar(e.status_code == 400 and "Espaço e Corredor" in e.detail, "sem espaço, recusa com 400")
checar(not ESCRITAS and not INSERIDOS, "e nada é escrito no ServiceNow")

print("\n[2] Ativo ausente é criado no estoque do CD")
limpar()
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-12")
checar(r["criados"] == 1 and r["atualizados"] == 0, "criou 1, atualizou 0")
novo = INSERIDOS[0]
checar(novo["stockroom"] == "sid-cd324", "estoque SPARE - CD324 resolvido")
checar(novo["install_status"] == "6", "situação em estoque")
checar(novo["aisle_space_location"] == "A-12", "espaço e corredor gravado")
checar(novo["asset_tag"] == "RN-NOVO-1" and novo["serial_number"] == "SN-NOVO-1", "identificadores")
checar(novo["model"] == "sid-modelo" and novo["model_category"] == "sid-categoria", "modelo e categoria do recebimento")

print("\n[3] Ativo existente é atualizado")
limpar()
EXISTENTES.append({"sys_id": "sys-1", "asset_tag": "RN-VELHO-1", "serial_number": ""})
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_EXISTE], aisle_space="B-03")
checar(r["atualizados"] == 1 and r["criados"] == 0, "atualizou 1, não criou")
sys_id, alt = ESCRITAS[0]
checar(sys_id == "sys-1", "escreveu no registro existente")
checar(alt["stockroom"] == "sid-cd324" and alt["install_status"] == "6", "estoque e situação atualizados")
checar(alt["aisle_space_location"] == "B-03", "espaço e corredor atualizado")
checar(alt["model"] == "sid-modelo", "modelo atualizado conforme o recebimento")
checar(alt.get("serial_number") == "SN-VELHO-1", "série que faltava no SN é completada")
checar("asset_tag" not in alt, "etiqueta que já estava certa não é reescrita")

print("\n[4] Estoque inexistente não escreve nada")
limpar()
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], stockroom="NAO EXISTE", aisle_space="A-1")
checar(not ESCRITAS and not INSERIDOS and r["falhas"], "estoque não resolvido aborta sem escrever")

print("\n[5] Criação desligada volta ao comportamento antigo")
limpar()
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1", criar=False)
checar(r["criados"] == 0 and r["nao_encontrados"] == 1 and not INSERIDOS, "sem criar, só conta o ausente")

print("\n[6] Coletor recebido sai do MDM (e só ele)")
dbo.init_db()
with dbo.SessionLocal.begin() as s:
    s.query(dbo.Coletor).delete()
    s.add_all([
        dbo.Coletor(mdm_id="m-1", serie="SN-NOVO-1", usuario="ljr001_coletor", situacao=dbo.ATIVO),
        dbo.Coletor(mdm_id="m-2", serie="SN-OUTRO", usuario="ljr002_coletor", situacao=dbo.ATIVO),
    ])
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1", "mdm_remocao_endpoint": ""})
r = ob.remover_recebidos_do_mdm([ITEM_NOVO], usuario="t")
checar(r["tentados"] == 1 and r["removidos"] == 0 and r["pendentes"] == 1,
       "sem endpoint configurado, fica pendente e não apaga")
checar("não configurado" in r["motivo"], "o motivo diz o que falta")
with dbo.SessionLocal() as s:
    checar(s.query(dbo.Coletor).count() == 2, "nada foi removido da base")
    e = s.query(dbo.Escrita).order_by(dbo.Escrita.id.desc()).first()
    checar(e is not None and e.acao == "deletar" and not e.sucesso and e.mdm_id == "m-1",
           "a tentativa fica na trilha de escrita")

from integracoes import mdm_airwatch as mdm  # noqa: E402
chamadas = []
mdm.remover_dispositivo = lambda sessao, mdm_id, base="", endpoint="", metodo="POST", campo="id": (
    chamadas.append(mdm_id) or (True, "removido"))
ob.sessao_mdm = lambda forcar=False: object()
dbo.gravar_config({"mdm_remocao_endpoint": "/AirWatch/Device/Delete/{id}"})
r = ob.remover_recebidos_do_mdm([ITEM_NOVO], usuario="t")
checar(r["removidos"] == 1 and chamadas == ["m-1"], "com endpoint, remove só o coletor recebido")
checar(r["por_serie"].get("SN-NOVO-1", {}).get("ok") is True,
       "o desfecho volta por série, para o Recebimento registrar no ciclo")
with dbo.SessionLocal() as s:
    restantes = {c.mdm_id for c in s.query(dbo.Coletor).all()}
checar(restantes == {"m-2"}, "o outro coletor da base continua intacto")

r = ob.remover_recebidos_do_mdm([{"serial": "NAO-EXISTE-NO-MDM"}], usuario="t")
checar(r["tentados"] == 0 and r["nao_encontrados"] == 1, "recebido que não está no MDM não é tentado")

dbo.gravar_config({"remover_do_mdm_no_recebimento": "0"})
r = ob.remover_recebidos_do_mdm([{"serial": "SN-OUTRO"}], usuario="t")
checar(r["tentados"] == 0 and "desligado" in r["motivo"], "configuração desliga a remoção")
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1"})

print("\n[7] Remoção no console: caminho mapeado, token anti-CSRF e corpo")
import importlib  # noqa: E402
mdm = importlib.reload(importlib.import_module("integracoes.mdm_airwatch"))

checar(dbo.PADROES["mdm_remocao_endpoint"] == "/AirWatch/Devices/DeleteDevice/{id}",
       "o padrão é o caminho mapeado na leitura do console")
checar(dbo.PADROES["mdm_remocao_campo"] == "SelectedDeviceIds",
       "o campo do corpo é o das ações do console")


class _Resp:
    def __init__(self, status=200, texto="", url=""):
        self.status_code, self.text, self.url = status, texto, url


FORM = ('<form><input name="__RequestVerificationToken" type="hidden" '
        'value="tok-123" /></form>')


class _Sessao:
    def __init__(self, status=200, form=FORM):
        self.status, self.form = status, form
        self.gets, self.posts = [], []

    def get(self, url, **kw):
        self.gets.append(url)
        return _Resp(200, self.form)

    def request(self, metodo, url, data=None, headers=None, **kw):
        self.posts.append((metodo, url, data or {}, headers or {}))
        return _Resp(self.status, "ok")


ses = _Sessao()
ok, detalhe = mdm.remover_dispositivo(
    ses, "691477", "https://mdm.exemplo",
    dbo.PADROES["mdm_remocao_endpoint"], "POST", dbo.PADROES["mdm_remocao_campo"])
checar(ok, f"remoção aceita quando o console responde 200 ({detalhe})")
metodo, url, corpo, cab = ses.posts[0]
checar(url == "https://mdm.exemplo/AirWatch/Devices/DeleteDevice/691477",
       f"o id entra no caminho ({url})")
checar(metodo == "POST", "método POST")
checar(corpo.get("SelectedDeviceIds") == "691477", f"o id também vai no corpo ({corpo})")
checar(corpo.get("__RequestVerificationToken") == "tok-123",
       "o token do formulário vai no corpo")
checar(cab.get("RequestVerificationToken") == "tok-123", "e no cabeçalho")
checar(ses.gets and ses.gets[0].endswith("/AirWatch/Devices/TagAssignment/691477"),
       f"o token é buscado num formulário do próprio aparelho ({ses.gets[:1]})")

ses = _Sessao(form="<form></form>")
ok, _ = mdm.remover_dispositivo(ses, "1", "", "/x/{id}")
checar(ok and "__RequestVerificationToken" not in ses.posts[0][2],
       "console sem token não impede a remoção")
checar(len(ses.gets) == len(mdm.FORMULARIOS_TOKEN),
       "sem token, tenta todos os formulários conhecidos e desiste")

ses = _Sessao(status=403)
ok, detalhe = mdm.remover_dispositivo(ses, "1", "", "/x/{id}")
checar(not ok and "403" in detalhe, f"erro do console vira falha registrada ({detalhe})")

ok, detalhe = mdm.remover_dispositivo(_Sessao(), "", "", "/x/{id}")
checar(not ok and "sem id" in detalhe, "sem id do aparelho nada é enviado")

try:
    mdm.remover_dispositivo(_Sessao(), "1", "", "")
    checar(False, "endpoint em branco deveria levantar RemocaoNaoConfigurada")
except mdm.RemocaoNaoConfigurada:
    checar(True, "endpoint em branco levanta RemocaoNaoConfigurada")

print("\n[8] O ciclo do recebimento guarda o desfecho da remoção no MDM")
import routers.recebimento as rec  # noqa: E402
from db.portal import (  # noqa: E402
    Base as _PBase, engine as _peng, SessionLocal as _PS, Asset as _Asset,
    ReceiptCycle as _Ciclo, Movement as _Mov,
)
_PBase.metadata.create_all(_peng)
with _PS.begin() as _s:
    _a = _Asset(serial_number="SN-NOVO-1", tag_number="RN-NOVO-1", model="Zebra TC21")
    _b = _Asset(serial_number="SN-OUTRO", tag_number="RN-OUTRO", model="Zebra TC21")
    _s.add_all([_a, _b]); _s.flush()
    _s.add_all([_Ciclo(asset_id=_a.id, cycle_number=1, created_by="t"),
                _Ciclo(asset_id=_b.id, cycle_number=1, created_by="t")])

_res = {"por_serie": {
    "SN-NOVO-1": {"ok": True, "detalhe": "ok", "mdm_id": "m-1"},
    "SN-OUTRO": {"ok": False, "detalhe": "HTTP 403", "mdm_id": "m-2"},
}}
_itens = [ITEM_NOVO, {"serial": "SN-OUTRO", "etiqueta": "RN-OUTRO"},
          {"serial": "SEM-CICLO", "etiqueta": ""}]
checar(rec._registrar_mdm_no_ciclo(_itens, _res, "t") == 2,
       "grava um movimento por ativo com desfecho, e só por esses")
with _PS() as _s:
    _movs = {m.asset_id: m for m in _s.query(_Mov).filter(_Mov.origin == "MDM").all()}
    _ids = {a.serial_number: a.id for a in _s.query(_Asset).all()}
checar(len(_movs) == 2, "nada foi gravado para série sem ativo/ciclo")
checar(_movs[_ids["SN-NOVO-1"]].note == "Removido do MDM", "sucesso fica registrado")
checar("HTTP 403" in _movs[_ids["SN-OUTRO"]].note and
       _movs[_ids["SN-OUTRO"]].note.startswith("Remoção do MDM pendente"),
       f"pendência guarda o motivo ({_movs[_ids['SN-OUTRO']].note})")
checar(all(m.username == "t" and m.cycle_id for m in _movs.values()),
       "movimento fica preso ao ciclo e a quem recebeu")
checar(rec._registrar_mdm_no_ciclo(_itens, {"tentados": 0}, "t") == 0,
       "sem desfecho por série, nada é gravado")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Recebimento → ServiceNow e MDM íntegros.")
