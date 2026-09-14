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
ESCRITAS, INSERIDOS, EXISTENTES, DEPRECIADOS, CONSULTAS = [], [], [], [], []
# INDIVIDUAIS: o que a busca um-a-um (rede contra duplicidade) acha.
INDIVIDUAIS: list[dict] = []
sn._get_http = lambda: (None, None)
sn._lookup_reference = lambda sessao, campo, valor, cache, BS: {
    "SPARE - CD324": "sid-cd324", "Zebra TC21": "sid-modelo",
    "Coletor": "sid-categoria", "SL 5 Years": "sid-depre"}.get(valor, "")
_hardware_records_real = sn._hardware_records
sn._hardware_records = lambda sessao, itens: list(EXISTENTES)
def _upd(sessao, tabela, sys_id, alteracao):
    ESCRITAS.append((sys_id, dict(alteracao))); return True
sn._sn_update = _upd
def _ins(sessao, registro):
    INSERIDOS.append(dict(registro)); return True, "novo-sys-id", "criado"
sn._insert_record = _ins
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    DEPRECIADOS.append(sys_id) or True)
def _consulta(sessao, tabela, query, campos, limit=50, offset=0, display_value=True):
    CONSULTAS.append(query)
    return list(INDIVIDUAIS)
sn._sn_query = _consulta

def limpar():
    for lista in (ESCRITAS, INSERIDOS, EXISTENTES, DEPRECIADOS, CONSULTAS, INDIVIDUAIS):
        lista.clear()

# Custo e DPIS fazem parte do item: sem eles o ativo não sobe.
ITEM_NOVO = {"serial": "SN-NOVO-1", "etiqueta": "RN-NOVO-1", "modelo": "Zebra TC21",
             "categoria": "Coletor", "numero_ativo": "900001",
             "custo": "1234,56", "dpis": "01/03/2023"}
ITEM_EXISTE = {"serial": "SN-VELHO-1", "etiqueta": "RN-VELHO-1", "modelo": "Zebra TC21",
               "categoria": "Coletor", "numero_ativo": "900002",
               "custo": "999.90", "dpis": "2022-07-15"}

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
REQ_DIAG = object()
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

print("\n[9] Ativo que já existe NÃO é duplicado")
limpar()
# A consulta em lote é a real: item em dict tem de produzir termos de busca.
sn._hardware_records = _hardware_records_real
INDIVIDUAIS.append({"sys_id": "sys-9", "asset_tag": "RN-NOVO-1",
                    "serial_number": "SN-NOVO-1"})
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(any("asset_tag" in q or "serial_number" in q for q in CONSULTAS),
       f"a busca usa os identificadores do item em dict ({CONSULTAS[:1]})")
checar(r["atualizados"] == 1 and r["criados"] == 0 and not INSERIDOS,
       "ativo existente é atualizado, nunca duplicado")

limpar()
sn._hardware_records = _hardware_records_real
# Índice em lote vazio (consulta do lote falhou): a busca individual salva.
INDIVIDUAIS.append({"sys_id": "sys-10", "asset_tag": "", "serial_number": "SN-NOVO-1"})
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(r["criados"] == 0 and ESCRITAS and ESCRITAS[0][0] == "sys-10",
       "achado só na segunda busca, é atualizado em vez de criado")
sn._hardware_records = lambda sessao, itens: list(EXISTENTES)

print("\n[10] Nunca subir pela metade: custo e depreciação são obrigatórios")
limpar()
sem_custo = dict(ITEM_NOVO); sem_custo["custo"] = ""
r = sn.marcar_recebidos_em_estoque(object(), [sem_custo], aisle_space="A-1")
checar(r["incompletos"] == 1 and not INSERIDOS and not ESCRITAS,
       "sem custo não sobe nada")
checar(any("custo" in f for f in r["falhas"]), f"a falha diz o que falta ({r['falhas'][:1]})")

limpar()
sem_dpis = dict(ITEM_NOVO); sem_dpis["dpis"] = ""
r = sn.marcar_recebidos_em_estoque(object(), [sem_dpis], aisle_space="A-1")
checar(r["incompletos"] == 1 and not INSERIDOS, "sem data de aquisição não sobe nada")

limpar()
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
novo = INSERIDOS[0]
checar(novo["cost"] == "1234.56", f"custo vai com ponto decimal ({novo.get('cost')})")
checar(novo["depreciation"] == "sid-depre", "plano de depreciação resolvido")
checar(novo["purchase_date"] == "2023-03-01", f"data de aquisição ({novo.get('purchase_date')})")
checar(novo["depreciation_date"].startswith("2023-03-01"), "data da depreciação")
checar(novo["cost.currency_type"], "moeda informada")
checar(DEPRECIADOS == ["novo-sys-id"], "o cálculo da depreciação roda depois de criar")

limpar()
EXISTENTES.append({"sys_id": "sys-11", "asset_tag": "RN-VELHO-1", "serial_number": "SN-VELHO-1"})
sn.marcar_recebidos_em_estoque(object(), [ITEM_EXISTE], aisle_space="B-1")
_sid, alt = ESCRITAS[0]
checar(alt["cost"] == "999.90" and alt["depreciation"] == "sid-depre",
       "atualização também leva custo e depreciação")
checar(DEPRECIADOS == ["sys-11"], "e recalcula a depreciação do que já existia")

limpar()
sn._lookup_reference = lambda sessao, campo, valor, cache, BS: {
    "SPARE - CD324": "sid-cd324"}.get(valor, "")
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(not INSERIDOS and not ESCRITAS and any("depreciação" in f for f in r["falhas"]),
       "sem plano de depreciação no ServiceNow, nada sobe")
sn._lookup_reference = lambda sessao, campo, valor, cache, BS: {
    "SPARE - CD324": "sid-cd324", "Zebra TC21": "sid-modelo",
    "Coletor": "sid-categoria", "SL 5 Years": "sid-depre"}.get(valor, "")

print("\n[11] O coletor recebido é achado no console quando o parque não tem a série")
limpar()
dbo.init_db()
with dbo.SessionLocal.begin() as s:
    s.query(dbo.Coletor).delete()
    # Como a grade não publica a série, o parque guarda o coletor SEM ela.
    s.add(dbo.Coletor(mdm_id="m-77", serie="", nome="ljr077_coletor",
                      usuario="ljr077_coletor", situacao=dbo.ATIVO))
buscas, apagados = [], []
mdm.procurar = lambda sessao, texto, base="": (
    buscas.append(texto) or ([{"id": "m-99"}] if texto == "SN-SEM-PARQUE" else []))
mdm.remover_dispositivo = lambda sessao, mdm_id, base="", endpoint="", metodo="POST", campo="x": (
    apagados.append(mdm_id) or (True, "removido"))
ob.sessao_mdm = lambda forcar=False: object()
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1"})
r = ob.remover_recebidos_do_mdm([{"serial": "SN-SEM-PARQUE", "etiqueta": "RN-1"}], usuario="t")
checar(buscas and buscas[0] == "SN-SEM-PARQUE", "procura a série no console")
checar(apagados == ["m-99"] and r["removidos"] == 1,
       f"remove o aparelho que o console achou ({apagados})")

apagados.clear(); buscas.clear()
r = ob.remover_recebidos_do_mdm([{"serial": "NAO-EXISTE-EM-LUGAR-NENHUM"}], usuario="t")
checar(not apagados and r["nao_encontrados"] == 1 and r["removidos"] == 0,
       "o que não é achado fica registrado como não encontrado, sem apagar nada")
checar("não encontrado" in r["por_serie"]["NAO-EXISTE-EM-LUGAR-NENHUM"]["detalhe"],
       "e o motivo volta por série")

apagados.clear()
mdm.procurar = lambda sessao, texto, base="": [{"id": "a"}, {"id": "b"}]
r = ob.remover_recebidos_do_mdm([{"serial": "AMBIGUA"}], usuario="t")
checar(not apagados, "busca ambígua não apaga nada: deletar do MDM não tem volta")

print("\n[12] Depreciação que não roda nunca passa em silêncio")
limpar()
sn._calculate_depreciation = lambda sessao, sys_id, BS: False
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(r["criados"] == 1, "o ativo sobe")
checar(r["sem_depreciacao"] == 1, "e o resumo conta que ele ficou sem depreciação")
checar(any("depreciação" in f for f in r["falhas"]),
       f"a falha diz que a depreciação não foi calculada ({r['falhas'][:1]})")

limpar()
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    _ for _ in ()).throw(RuntimeError("sessão do formulário expirou"))
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(r["sem_depreciacao"] == 1 and any("expirou" in f for f in r["falhas"]),
       "erro no cálculo aparece com a causa")

limpar()
DEPRECIADOS_2 = []
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    DEPRECIADOS_2.append(sys_id) or True)
# Inserção que não devolve sys_id: o registro é reprocurado para depreciar.
sn._insert_record = lambda sessao, registro: (True, "N/A", "criado")
INDIVIDUAIS.append({"sys_id": "sys-reachado", "asset_tag": "RN-NOVO-1",
                    "serial_number": "SN-NOVO-1"})
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(DEPRECIADOS_2 == ["sys-reachado"],
       f"sem sys_id na resposta, o ativo é reprocurado e depreciado ({DEPRECIADOS_2})")
checar(r.get("depreciados") == 1, "e o resumo conta a depreciação feita")
sn._insert_record = _ins
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    DEPRECIADOS.append(sys_id) or True)

print("\n[13] Diagnóstico do MDM diz onde a remoção para — sem apagar nada")
ob._exigir_admin = lambda req: {"username": "admin", "is_admin": True}
apagados_diag = []
mdm.remover_dispositivo = lambda *a, **k: (apagados_diag.append(a) or (True, "x"))
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1",
                   "mdm_remocao_endpoint": "/AirWatch/Devices/DeleteDevice/{id}"})
mdm.procurar = lambda sessao, texto, base="": (
    [{"id": "m-55", "nome": "ljr055_coletor"}] if texto == "SN-ACHA" else [])

d = ob.mdm_diagnostico(REQ_DIAG, serie="SN-ACHA")
checar(d["remocao_ligada"] and d["endpoint"].endswith("{id}"), "mostra a configuração vigente")
checar(d["sessao_ok"], "diz se a sessão do console abriu")
checar(d["busca"][0]["quantidade"] == 1, "mostra o que a busca do console devolveu")
checar("deve funcionar" in d["conclusao"], f"conclui que acharia ({d['conclusao'][:40]})")
checar(not apagados_diag, "o diagnóstico não apaga nada")

d = ob.mdm_diagnostico(REQ_DIAG, serie="SN-QUE-NAO-EXISTE")
checar("não acham" in d["conclusao"] or "Nem o parque" in d["conclusao"],
       f"quando não acha, diz isso ({d['conclusao'][:40]})")

dbo.gravar_config({"remover_do_mdm_no_recebimento": "0"})
d = ob.mdm_diagnostico(REQ_DIAG, serie="SN-ACHA")
checar("desligada" in d["conclusao"], "remoção desligada aparece como causa")
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1"})

e = ob.mdm_escritas(REQ_DIAG, limite=5)
checar("escritas" in e and isinstance(e["escritas"], list),
       "a trilha de escritas no MDM é consultável")
checar(all("resposta" in x for x in e["escritas"]), "com a resposta do console em cada uma")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Recebimento → ServiceNow e MDM íntegros.")
