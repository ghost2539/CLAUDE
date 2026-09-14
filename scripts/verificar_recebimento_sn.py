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
_procurar_detalhado_real = mdm.procurar_detalhado
_remover_dispositivo_real = mdm.remover_dispositivo
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
checar(ses.gets and ses.gets[0].endswith("/AirWatch/Device/Details/Summary/691477"),
       f"o token é buscado na página do próprio aparelho ({ses.gets[:1]})")

ses = _Sessao(form="<form></form>")
ok, _ = mdm.remover_dispositivo(ses, "1", "", "/x/{id}")
checar(ok and "__RequestVerificationToken" not in ses.posts[0][2],
       "console sem token não impede a remoção")
_tentativas = [u for u in ses.gets
               if any(f.split("{")[0] in u for f in mdm.FORMULARIOS_TOKEN)]
checar(len(_tentativas) >= len(mdm.FORMULARIOS_TOKEN),
       f"sem token, tenta todos os formulários conhecidos e desiste ({len(_tentativas)})")

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
mdm.procurar_detalhado = lambda sessao, texto, base="": {
    "coletores": [{"id": "m-55", "nome": "ljr055_coletor"}] if texto == "SN-ACHA" else [],
    "rodape": {"de": 1, "ate": 1, "total": 1} if texto == "SN-ACHA" else None,
    "erro": "", "http": 200, "url": ""}

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

print("\n[14] Diagnóstico da depreciação diz em qual passo ela para")
class _Resp:
    def __init__(self, status=200, texto="", url=""):
        self.status_code, self.text, self.url = status, texto, url

class _BS:
    """BeautifulSoup de mentira: só o formulário; a ação vem do HTML cru."""
    def __init__(self, texto, _parser):
        self.texto = texto
    def find(self, tag, *a, **k):
        if tag == "form":
            return _Form() if "<form" in self.texto else None
        return None

class _Form(dict):
    def __bool__(self):
        return True          # formulário vazio ainda é um formulário
    def find_all(self, _tag):
        return []
    def get(self, chave, padrao=None):
        return padrao

class _SessaoSN:
    def __init__(self, pagina, status=200, url="https://sn/alm_hardware.do"):
        self.pagina, self.status, self.url = pagina, status, url
        self.posts = []
    def get(self, url, **k):
        return _Resp(self.status, self.pagina, self.url)
    def post(self, url, **k):
        self.posts.append(url)
        return _Resp(200, "ok", "https://sn/alm_hardware.do")

_bs_real = sn._get_http
sn._get_http = lambda: (None, _BS)

d = sn._depreciacao_passos(_SessaoSN("", 200, "https://sn/login.do"), "sys-1", _BS)
checar(not d["ok"] and "login" in d["motivo"], f"sessão caída no login é nomeada ({d['motivo'][:40]})")

_ACAO = ('<a gsft_action_name="sysverb_calculate_depreciation" href="#">'
         'Calculate Depreciation</a>')
_FORM = '<form name="alm_hardware.do" action="alm_hardware.do"></form>'

d = sn._depreciacao_passos(_SessaoSN("<html>pagina sem acao</html>"), "sys-1", _BS)
checar(not d["ok"] and "calcular depreciação" in d["motivo"],
       "ação ausente no formulário é nomeada")

d = sn._depreciacao_passos(_SessaoSN(_ACAO), "sys-1", _BS)
checar(not d["ok"] and "formulário do ativo" in d["motivo"], "formulário ausente é nomeado")

d = sn._depreciacao_passos(_SessaoSN(_ACAO + _FORM), "sys-1", _BS, executar=False)
checar(d["ok"] and "não executa" in d["motivo"], "no modo diagnóstico não executa a ação")
checar(len(d["passos"]) >= 3, "e mostra o caminho passo a passo")

ses = _SessaoSN(_ACAO + _FORM)
d = sn._depreciacao_passos(ses, "sys-1", _BS, executar=True)
checar(d["ok"] and ses.posts, "com o caminho inteiro, executa e confirma")

# O caso que a área encontrou: interface em português.
_ACAO_PT = ('<a gsft_action_name="sysverb_calc_dep" href="#">'
            '<span>Calcular Depreciação</span></a>')
ses = _SessaoSN(_ACAO_PT + _FORM)
d = sn._depreciacao_passos(ses, "sys-1", _BS, executar=True)
checar(d["ok"] and ses.posts, "com a interface em português também executa")
sn._get_http = _bs_real

print("\n[15] Busca no MDM denuncia filtro ignorado pelo console")
mdm.procurar_detalhado = _procurar_detalhado_real   # aqui a função real é o alvo
class _SessaoMDM:
    def __init__(self, html):
        self.html = html
    def get(self, url, **k):
        return _Resp(200, self.html, url)

GRADE_UMA = ('<table class="DeviceGrid"><tr data-view-url="/AirWatch/Device/Details/Summary/77">'
             '<td data-property="FriendlyName"><span id="FriendlyName">ljr077</span></td></tr>'
             '</table>Items 1 - 1 of 1')
d = mdm.procurar_detalhado(_SessaoMDM(GRADE_UMA), "SN-X", "https://mdm")
checar(len(d["coletores"]) == 1 and d["rodape"]["total"] == 1,
       "busca que filtra devolve um aparelho e o rodapé bate")
d = mdm.procurar_detalhado(_SessaoMDM("<html>login</html>"), "SN-X", "https://mdm")
checar(not d["coletores"] and "fragmento" in d["erro"],
       f"resposta que não é a grade vira erro explicado ({d['erro'][:30]})")

print("\n[16] Diagnóstico sem série avisa em vez de dizer \"não encontrado\"")
sn.require_permission = lambda req, m, a: {"username": "u"}
sn._sn_session_from_portal = lambda req: object()
d = sn.diagnostico_depreciacao(REQ_DIAG, serie="", etiqueta="")
checar(d.get("informado") is False and "sem série" in d["motivo"].lower(),
       f"série vazia é apontada como falta de dado ({d['motivo'][:40]})")
checar(d.get("recebido", {}).get("serie") == "",
       "e a resposta ecoa o que o servidor recebeu")
INDIVIDUAIS.clear()
d = sn.diagnostico_depreciacao(REQ_DIAG, serie="SN-QUE-NAO-EXISTE")
checar(d.get("informado") is True and "SN-QUE-NAO-EXISTE" in d["motivo"],
       "com série informada, o motivo repete o que foi procurado")

print("\n[17] O resultado diz o que aconteceu com CADA série")
limpar()
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    DEPRECIADOS.append(sys_id) or True)
sem_custo = dict(ITEM_NOVO); sem_custo["custo"] = ""; sem_custo["serial"] = "SN-SEM-CUSTO"
EXISTENTES.append({"sys_id": "sys-30", "asset_tag": "RN-VELHO-1",
                   "serial_number": "SN-VELHO-1"})
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO, ITEM_EXISTE, sem_custo],
                                   aisle_space="A-1")
por = {x["serial"]: x for x in r["por_item"]}
checar(len(r["por_item"]) == 3, f"uma linha por ativo lido ({len(r['por_item'])})")
checar(por["SN-NOVO-1"]["acao"] == "criado" and por["SN-NOVO-1"]["depreciacao"] == "calculada",
       f"o criado diz que depreciou ({por['SN-NOVO-1']})")
checar(por["SN-VELHO-1"]["acao"] == "atualizado" and por["SN-VELHO-1"]["sys_id"] == "sys-30",
       "o atualizado traz o sys_id do registro")
checar(por["SN-SEM-CUSTO"]["acao"] == "não subiu" and "custo" in por["SN-SEM-CUSTO"]["motivo"],
       f"o incompleto diz por que ficou de fora ({por['SN-SEM-CUSTO']['motivo']})")

limpar()
sn._calculate_depreciation = lambda sessao, sys_id, BS: False
r = sn.marcar_recebidos_em_estoque(object(), [ITEM_NOVO], aisle_space="A-1")
checar(r["por_item"][0]["depreciacao"] == "não calculada",
       "depreciação que não roda aparece na linha do ativo")
sn._calculate_depreciation = lambda sessao, sys_id, BS: (
    DEPRECIADOS.append(sys_id) or True)

print("\n[18] \"Removido do MDM\" só depois de conferir que sumiu")
_remover_real = _remover_dispositivo_real

class _R2:
    def __init__(self, status=200, texto="", url=""):
        self.status_code, self.text, self.url = status, texto, url

class _Console:
    """Console de mentira: responde ao POST e ao GET de conferência."""
    def __init__(self, resposta_post=None, ainda_la=True, erro_get=False):
        self.resposta_post = resposta_post or _R2(200, "")
        self.ainda_la, self.erro_get = ainda_la, erro_get
        self.gets, self.posts = [], []
    def get(self, url, **k):
        self.gets.append(url)
        if self.erro_get:
            raise RuntimeError("rede caiu")
        if "TagAssignment" in url or "Summary" in url and "form" in url:
            return _R2(200, "<form></form>")
        if self.ainda_la:
            return _R2(200, "<div>aparelho 691477 ainda aqui</div>", url)
        return _R2(404, "Device not found", url)
    def request(self, metodo, url, **k):
        self.posts.append(url)
        return self.resposta_post

ENDPOINT = "/AirWatch/Devices/DeleteDevice/{id}"
ses = _Console(ainda_la=True)
ok, detalhe = _remover_real(ses, "691477", "https://mdm", ENDPOINT)
checar(not ok and "CONTINUA" in detalhe,
       f"HTTP 200 com o aparelho ainda inscrito NÃO é remoção ({detalhe[:60]})")

ses = _Console(ainda_la=False)
ok, detalhe = _remover_real(ses, "691477", "https://mdm", ENDPOINT)
checar(ok and "conferido" in detalhe, f"só confirma quando o aparelho some ({detalhe})")

ses = _Console(resposta_post=_R2(200, '{"Success":false,"Message":"sem permissão"}'))
ok, detalhe = _remover_real(ses, "691477", "https://mdm", ENDPOINT)
checar(not ok and "recusou" in detalhe, "corpo com Success:false é recusa, não sucesso")

ses = _Console(ainda_la=False, erro_get=True)
ok, detalhe = _remover_real(ses, "691477", "https://mdm", ENDPOINT)
checar(not ok and "CONTINUA" in detalhe,
       "sem conseguir conferir, não se afirma que removeu")

checar(mdm.ainda_existe(_Console(ainda_la=False), "1", "https://mdm") is False,
       "conferência: 404 é aparelho removido")
checar(mdm.ainda_existe(_Console(ainda_la=True), "691477", "https://mdm") is True,
       "conferência: página com o id é aparelho ainda lá")

print("\n[19] Se o caminho configurado não apaga, os outros são tentados e conferidos")
tentados = []
def _remove_fake(sessao, mdm_id, base="", endpoint="", metodo="POST", campo="x"):
    tentados.append(endpoint)
    # Só o terceiro caminho conhecido apaga de verdade neste console falso.
    if endpoint == "/AirWatch/Devices/DeleteBulkDevices":
        return True, "removido e conferido (HTTP 200)"
    return False, "o console respondeu HTTP 200 mas o aparelho CONTINUA inscrito"
mdm.remover_dispositivo = _remove_fake
ok, detalhe, trilha, venceu = mdm.remover_tentando(object(), "77", "https://mdm")
checar(ok and venceu == "/AirWatch/Devices/DeleteBulkDevices",
       f"para no caminho que realmente apaga ({venceu})")
checar(len(trilha) == 3 and not trilha[0]["ok"],
       f"a trilha guarda o que cada tentativa respondeu ({len(trilha)})")

tentados.clear()
mdm.remover_dispositivo = lambda *a, **k: (False, "não apagou")
ok, detalhe, trilha, venceu = mdm.remover_tentando(object(), "77", "https://mdm")
checar(not ok and not venceu and len(trilha) == len(mdm.CAMINHOS_REMOCAO),
       "nenhum funcionando, todos ficam registrados")
checar("DeleteDevice" in detalhe, "e o detalhe resume o que cada um respondeu")

# No fluxo do recebimento: o caminho vencedor vira o configurado.
dbo.gravar_config({"remover_do_mdm_no_recebimento": "1",
                   "mdm_remocao_endpoint": "/AirWatch/Devices/DeleteDevice/{id}"})
with dbo.SessionLocal.begin() as s:
    s.query(dbo.Coletor).delete()
    s.add(dbo.Coletor(mdm_id="m-88", serie="SN-TROCA", usuario="ljr088_coletor",
                      situacao=dbo.ATIVO))
mdm.remover_dispositivo = _remove_fake
tentados.clear()
r = ob.remover_recebidos_do_mdm([{"serial": "SN-TROCA"}], usuario="t")
checar(r["removidos"] == 1, f"removeu usando o caminho que funciona ({r['removidos']})")
checar(dbo.ler_config()["mdm_remocao_endpoint"] == "/AirWatch/Devices/DeleteBulkDevices",
       "e o caminho vencedor fica gravado para a próxima vez")
mdm.remover_dispositivo = _remover_dispositivo_real
dbo.gravar_config({"mdm_remocao_endpoint": "/AirWatch/Devices/DeleteDevice/{id}"})

print("\n[20] A ação de depreciação é achada em inglês E em português")
PAGINA_EN = ('<div><a class="linked" gsft_action_name="sysverb_calculate_depreciation" '
             'href="#">Calculate Depreciation</a></div>')
PAGINA_PT = ('<div><a class="linked" gsft_action_name="sysverb_calc_dep" '
             'href="#"><span>Calcular Depreciação</span></a></div>')
PAGINA_ES = ('<div><a gsft_action_name="accion_x" href="#">Calcular '
             'Depreciación</a></div>')
PAGINA_SEM = ('<div><a gsft_action_name="sysverb_update" href="#">Atualizar</a>'
              '<a href="#">Depreciação (só texto, sem ação)</a></div>')
PAGINA_DUAS = ('<a gsft_action_name="ver_agenda" href="#">Depreciation schedule</a>'
               '<a gsft_action_name="sysverb_calculate_depreciation" href="#">'
               'Calculate Depreciation</a>')
checar(sn.achar_acao_depreciacao(PAGINA_EN) == "sysverb_calculate_depreciation",
       "inglês: acha pelo texto e pelo nome interno")
checar(sn.achar_acao_depreciacao(PAGINA_PT) == "sysverb_calc_dep",
       f"português: acha mesmo com o texto traduzido ({sn.achar_acao_depreciacao(PAGINA_PT)})")
checar(sn.achar_acao_depreciacao(PAGINA_ES) == "accion_x", "espanhol também")
checar(sn.achar_acao_depreciacao(PAGINA_SEM) == "",
       "página sem a ação continua sendo página sem a ação")
checar(sn.achar_acao_depreciacao(PAGINA_DUAS) == "sysverb_calculate_depreciation",
       "entre duas, escolhe a de CALCULAR, não a agenda")
checar(sn.achar_acao_depreciacao("") == "", "página vazia não inventa ação")

print("\n[21] A recusa do console chega inteira, e a requisição parece vir da página")
class _Console2(_Console):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.cabecalhos = None
    def request(self, metodo, url, **k):
        self.cabecalhos = k.get("headers") or {}
        return super().request(metodo, url, **k)

ses = _Console2(resposta_post=_R2(200, '{"RedirectUrl":null,"IsSuccess":false,'
                                       '"Message":"Save Failed: device is enrolled"}'))
ok, detalhe = _remover_real(ses, "691477", "https://mdm", ENDPOINT)
checar(not ok and "Save Failed: device is enrolled" in detalhe,
       f"a mensagem do console vem inteira ({detalhe[:70]})")
checar((ses.cabecalhos or {}).get("Referer", "").endswith("/Summary/691477"),
       f"a requisição diz que veio da página do aparelho ({(ses.cabecalhos or {}).get('Referer')})")
checar(mdm.FORMULARIOS_TOKEN[0].startswith("/AirWatch/Device/Details"),
       "o token é buscado primeiro na página onde o botão de excluir vive")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Recebimento → ServiceNow e MDM íntegros.")
