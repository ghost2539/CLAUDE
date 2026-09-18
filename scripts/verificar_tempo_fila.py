#!/usr/bin/env python3
"""Verificação do tempo de fila — quanto o chamado ficou numa fila.

    python3 scripts/verificar_tempo_fila.py

O cálculo é uma função pura (`intervalos_da_fila`) justamente para poder ser
conferido com casos escritos à mão, minuto a minuto, sem ServiceNow nenhum.
É onde a medição erra, e errar aqui não dá erro: dá um número plausível e
errado, que vira slide de RMR.

Os quatro modos de errar que estão cobertos:

*   **Fechar o último intervalo em `agora`.** Um chamado encerrado há dois
    anos, cuja última fila foi SPARE, mostraria dois anos de fila.
*   **Somar só a primeira passagem.** Chamado que vai, volta e é atendido de
    novo tem o atendimento subestimado — e é o caso que o campo único
    (`u_data_bouncing`) não consegue representar.
*   **Supor a fila inicial.** Antes da primeira troca o chamado esteve na
    fila de ONDE ele saiu, não na fila atual.
*   **Confundir zero com não sei.** Chamado sem histórico guardado não tem
    zero hora de fila: tem medição ausente. Se virar zero, a média cai e
    ninguém percebe.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402
import routers.sn_consulta as sc  # noqa: E402
import routers.sn_tempo_fila as tf  # noqa: E402

SPARE = "TI_N2_FLD_RNR_LOJAS_SPARE"
OUTRA = "TI_N1_SERVICE_DESK"
TERCEIRA = "TI_N3_REDES"


def t(dia, hora=0, minuto=0):
    return datetime(2026, 9, dia, hora, minuto, tzinfo=timezone.utc)


def ev(quando, de, para):
    return {"quando": quando, "de": de, "para": para}


print("[1] Uma passagem simples pela fila")
r = tf.intervalos_da_fila(
    [ev(t(1, 10), OUTRA, SPARE), ev(t(3, 10), SPARE, TERCEIRA)],
    "SPARE", abertura=t(1, 8), fim=t(5, 12))
checar(r["segundos"] == 2 * 86400, f"dois dias exatos ({r['segundos']}s)")
checar(r["passagens"] == 1, "uma passagem")
checar(r["horas"] == 48.0, "e 48 horas")

print("\n[2] O chamado que VOLTA para a fila")
# É o caso que um campo de data só não consegue representar — e o motivo de
# a medida sair do histórico e não de `u_data_bouncing`.
r = tf.intervalos_da_fila([
    ev(t(1, 10), OUTRA, SPARE),      # entra
    ev(t(2, 10), SPARE, TERCEIRA),   # sai  (1 dia)
    ev(t(4, 10), TERCEIRA, SPARE),   # volta
    ev(t(6, 10), SPARE, OUTRA),      # sai  (2 dias)
], "SPARE", abertura=t(1, 8), fim=t(7, 0))
checar(r["segundos"] == 3 * 86400, f"soma as duas passagens: 3 dias ({r['horas']}h)")
checar(r["passagens"] == 2, "e diz que foram duas idas à fila")
# Contraprova do jeito ingênuo: só a primeira passagem daria 1 dia.
checar(r["detalhe"][0]["segundos"] == 86400 and r["detalhe"][1]["segundos"] == 2 * 86400,
       "contraprova: só a primeira passagem daria 1 dia em vez de 3")

print("\n[3] Chamado ENCERRADO na fila: o intervalo fecha no encerramento")
# O erro mais caro. Sem isto, um chamado fechado há dois anos com a última
# fila em SPARE aparece com dois anos de fila, e a média do mês vai junto.
agora = t(30, 12)
r = tf.intervalos_da_fila(
    [ev(t(1, 10), OUTRA, SPARE)], "SPARE",
    abertura=t(1, 8), fim=t(2, 10), agora=agora)
checar(r["segundos"] == 86400, f"um dia, não 29 ({r['horas']}h)")

# Contraprova: fechando em `agora`, como se o encerramento fosse ignorado.
errado = tf.intervalos_da_fila(
    [ev(t(1, 10), OUTRA, SPARE)], "SPARE",
    abertura=t(1, 8), fim=None, agora=agora)
checar(errado["horas"] > r["horas"] * 20,
       f"contraprova: ignorando o encerramento daria {errado['horas']}h")

print("\n[4] Chamado ABERTO na fila: conta até agora")
r = tf.intervalos_da_fila(
    [ev(t(1, 10), OUTRA, SPARE)], "SPARE",
    abertura=t(1, 8), fim=None, agora=t(3, 10))
checar(r["segundos"] == 2 * 86400, f"dois dias de fila em aberto ({r['horas']}h)")

print("\n[5] A fila de ANTES da primeira troca")
# O chamado nasceu no SPARE e foi encaminhado: o trecho inicial é do SPARE,
# e ele é o `oldvalue` da primeira troca — não a fila atual.
r = tf.intervalos_da_fila(
    [ev(t(2, 8), SPARE, OUTRA)], "SPARE", abertura=t(1, 8), fim=t(4, 0))
checar(r["segundos"] == 86400, f"conta da abertura até a saída ({r['horas']}h)")
checar(r["passagens"] == 1, "como uma passagem")

# E o contrário: nasceu em outra fila, nunca passou pelo SPARE.
r = tf.intervalos_da_fila(
    [ev(t(2, 8), OUTRA, TERCEIRA)], "SPARE", abertura=t(1, 8), fim=t(4, 0))
checar(r["segundos"] == 0 and r["passagens"] == 0,
       "chamado que nunca passou pela fila dá zero, e zero de verdade")

print("\n[6] Casos que não podem virar número errado")
r = tf.intervalos_da_fila([], "SPARE", abertura=t(1, 8), fim=t(4, 0))
checar(r["segundos"] == 0 and r["trechos"] == 0,
       "sem eventos não se monta trecho nenhum — quem decide é o chamador")
# Relógio torto: dois carimbos fora de ordem não podem gerar tempo negativo
# e reduzir o total.
r = tf.intervalos_da_fila(
    [ev(t(3, 10), OUTRA, SPARE), ev(t(1, 10), SPARE, TERCEIRA)],
    "SPARE", abertura=t(1, 8), fim=t(5, 0))
checar(r["segundos"] >= 0, "eventos fora de ordem não geram tempo negativo")
# O nome da fila casa por trecho e sem caixa: a fila do SPARE tem prefixo.
r = tf.intervalos_da_fila(
    [ev(t(1, 0), OUTRA, "ti_n2_fld_rnr_lojas_spare"), ev(t(2, 0), "x", "y")],
    "spare", abertura=t(1, 0), fim=t(3, 0))
checar(r["segundos"] == 86400, "casa por trecho do nome e sem distinguir maiúscula")

print("\n[7] '3d 04:12' para gente, número para planilha")
checar(tf._humano(0) == "00:00", "zero")
checar(tf._humano(3 * 86400 + 4 * 3600 + 12 * 60) == "3d 04:12", "dias, horas e minutos")
checar(tf._humano(3600) == "01:00", "menos de um dia não inventa '0d'")


# ── ServiceNow de mentira ──────────────────────────────────────────────
print("\n[8] Medição de uma lista de chamados, pelo sys_audit")
sc._cfg.SN_API_USER, sc._cfg.SN_API_PASS = "zabbix", "senha-que-nao-pode-sair"

CHAMADOS = {
    # sys_id: (número, abertura, encerramento, eventos)
    "a" * 32: ("INC0000001", t(1, 8), t(7, 0), [
        (t(1, 10), OUTRA, SPARE), (t(2, 10), SPARE, TERCEIRA),
        (t(4, 10), TERCEIRA, SPARE), (t(6, 10), SPARE, OUTRA)]),
    "b" * 32: ("INC0000002", t(1, 8), t(3, 8), [
        (t(1, 9), OUTRA, TERCEIRA)]),                       # nunca no SPARE
    "c" * 32: ("INC0000003", t(1, 8), None, []),            # sem histórico
}
chamadas: list[dict] = []


def _falso_get(caminho, params, timeout=60):
    chamadas.append({"caminho": caminho, "params": dict(params)})
    q = params.get("sysparm_query", "")
    if caminho == "/api/now/table/sys_audit":
        querem = set(q.split("documentkeyIN")[1].split("^")[0].split(",")) \
            if "documentkeyIN" in q else set(CHAMADOS)
        fora = []
        for sid, (_n, _a, _f, eventos) in CHAMADOS.items():
            if sid not in querem:
                continue
            for quando, de, para in eventos:
                fora.append({"documentkey": sid, "oldvalue": de, "newvalue": para,
                             "sys_created_on": quando.strftime("%Y-%m-%d %H:%M:%S")})
        return {"result": fora}
    if caminho == "/api/now/table/metric_instance":
        return {"result": []}
    # A seção [11] passa pela consulta inteira, que descobre os campos antes
    # de buscar. Aqui só o bastante para ela andar — quem confere a descoberta
    # é scripts/verificar_sn_consulta.py.
    if caminho == "/api/now/table/sys_db_object":
        return {"result": [{"super_class.name": ""}]}
    if caminho == "/api/now/table/sys_dictionary":
        return {"result": [
            {"element": c, "column_label": c, "internal_type": "string",
             "name": "incident"}
            for c in ("number", "state", "opened_at", "closed_at",
                      "resolved_at", "sys_id", "short_description", "priority",
                      "category", "subcategory", "assignment_group",
                      "assigned_to", "caller_id")]}
    if caminho.startswith("/api/now/table/"):
        return {"result": []}
    if caminho.startswith("/api/now/stats/"):
        return {"result": {"stats": {"count": "0"}}}
    raise AssertionError(f"caminho inesperado: {caminho}")


tf._get = _falso_get
sc._get = _falso_get

entrada = [{"sys_id": sid,
            "opened_at": ab.strftime("%Y-%m-%d %H:%M:%S"),
            "closed_at": (fe.strftime("%Y-%m-%d %H:%M:%S") if fe else "")}
           for sid, (_n, ab, fe, _e) in CHAMADOS.items()]
res = tf.medir("incident", entrada, "SPARE")

checar(res["a" * 32]["medido"] is True and res["a" * 32]["horas"] == 72.0,
       f"o que foi e voltou: 3 dias ({res['a' * 32]['horas']}h)")
checar(res["a" * 32]["passagens"] == 2, "com duas idas à fila")
checar(res["b" * 32]["medido"] is True and res["b" * 32]["segundos"] == 0,
       "o que nunca passou pela fila: medido, e zero")
checar(res["c" * 32]["medido"] is False and res["c" * 32]["segundos"] is None,
       "o sem histórico: NÃO medido, e sem número")
checar("histórico" in res["c" * 32]["motivo"], "dizendo por quê")
# É a distinção que salva a média. Zero e "não sei" na mesma coluna fariam o
# chamado sem histórico puxar a média para baixo.
checar(res["b" * 32]["segundos"] == 0 and res["c" * 32]["segundos"] is None,
       "zero e 'não sei' NÃO são a mesma célula")

checar(all(c["params"].get("sysparm_display_value") == "false"
           for c in chamadas if c["caminho"] == "/api/now/table/sys_audit"),
       "o histórico é lido em UTC e formato fixo, não no fuso do usuário")
checar(all("fieldname=assignment_group" in c["params"]["sysparm_query"]
           for c in chamadas if c["caminho"] == "/api/now/table/sys_audit"),
       "e só as trocas de fila, não a auditoria inteira do chamado")

print("\n[9] A média não conta quem nunca passou pela fila")
app = main.app
cliente = TestClient(app)
_, cookie = security.create_session(
    {"username": "verificador", "is_admin": True, "permission_map": {}})
cliente.cookies.set("spare_session", cookie)

r9 = cliente.post("/api/sn-consulta/tempo-fila",
                  json={"tabela": "incident", "chamados": entrada, "fila": "SPARE"})
checar(r9.status_code == 200, f"HTTP 200 ({r9.status_code})")
d9 = r9.json()
checar(d9["pedidos"] == 3 and d9["medidos"] == 2 and d9["sem_historico"] == 1,
       "separa pedidos, medidos e sem histórico")
checar(d9["passaram_pela_fila"] == 1, "e quantos realmente passaram pela fila")
checar(d9["media_horas"] == 72.0,
       f"a média é dos que passaram: 72h, não 36 nem 24 ({d9['media_horas']})")
checar(sc._cfg.SN_API_PASS not in r9.text, "sem vazar a senha da conta")
checar(cliente.post("/api/sn-consulta/tempo-fila",
                    json={"tabela": "incident", "chamados": [], "fila": " "}
                    ).status_code == 422,
       "sem o nome da fila, recusa — medir 'tudo' não quer dizer nada")
anon = TestClient(app)
checar(anon.post("/api/sn-consulta/tempo-fila",
                 json={"tabela": "incident"}).status_code in (401, 403),
       "e exige sessão")

print("\n[10] Antes de confiar: qual fonte esta instalação tem")
r10 = cliente.get("/api/sn-consulta/tempo-fila/fontes?tabela=incident")
d10 = r10.json()
checar(r10.status_code == 200, f"HTTP 200 ({r10.status_code})")
fontes = {f["fonte"]: f for f in d10["fontes"]}
checar("sys_audit" in fontes and "metric_instance" in fontes,
       "sonda as duas fontes possíveis")
checar(d10["pode_medir"] is True, "com auditoria respondendo, diz que dá para medir")
checar(fontes["sys_audit"]["exemplo"].get("newvalue") == SPARE,
       "e mostra um valor de exemplo — é ele que diz se a fila vem por NOME")


def _sem_audit(caminho, params, timeout=60):
    if caminho == "/api/now/table/sys_audit":
        return {"result": []}
    return _falso_get(caminho, params, timeout)


tf._get = _sem_audit
d10b = cliente.get("/api/sn-consulta/tempo-fila/fontes?tabela=incident").json()
checar(d10b["pode_medir"] is False, "sem auditoria, diz que NÃO dá para medir")
checar("zero" in d10b["recado"] and "não medido" in d10b["recado"],
       "e avisa que a resposta será 'não medido', não zero")
tf._get = _falso_get

print("\n[11] Na consulta: as colunas entram, e só quando pedidas")
fonte = (RAIZ / "routers" / "sn_consulta.py").read_text(encoding="utf-8")
checar("tempo_fila: str = \"\"" in fonte, "a medição é opção do corpo da consulta")
checar("CAMPOS_PARA_MEDIR" in fonte and "sys_id" in fonte,
       "e pede sys_id e os carimbos, que a medição precisa")
checar('"tempo_fila_medido"' in fonte or "tempo_fila_medido" in fonte,
       "a coluna que separa medido de não medido existe")
# Sem pedir, nada muda: quem só quer a lista não paga a leitura do histórico.
chamadas.clear()
cliente.post("/api/sn-consulta/buscar", json={"tabela": "incident", "campos": ["number"]})
checar(not any(c["caminho"] == "/api/now/table/sys_audit" for c in chamadas),
       "sem pedir a medição, o histórico nem é consultado")

js = (RAIZ / "modulos/servicenow_automacoes.js").read_text(encoding="utf-8")
checar("cn-tempo-fila" in js, "a tela tem o campo da fila a medir")
checar("tempo_fila:" in js, "e manda a opção na consulta")
# O rótulo das colunas vem do servidor (COLUNAS_TEMPO), não do JS — a tela só
# desenha o que `rotulos` manda. O que é DELA é avisar que mediu só a amostra.
checar('("Medição"' in fonte or '"Medição"' in fonte,
       "o rótulo da coluna de medição está no servidor")
checar("tempo_fila_so_amostra" in js and "mede todas" in js,
       "a tela avisa quando mediu só a amostra — senão a média da tela "
       "parece a do total")
checar("/tempo-fila/fontes" in js, "e oferece a conferência da fonte de histórico")

print("\n[12] A exportação mede tudo, e o arquivo separa medido de não medido")
# A tela mede a amostra; o arquivo é que leva a conta fechada. Se a coluna de
# medição não fosse para o CSV, "0" e "não sei" virariam a mesma célula na
# planilha — e a média de quem somasse a coluna sairia menor que a verdade.
def _com_chamados(caminho, params, timeout=60):
    if caminho.startswith("/api/now/table/incident"):
        pedidos = [c for c in (params.get("sysparm_fields") or "").split(",") if c]
        # Uma página só: o `sys_id>` da segunda volta vazio.
        if "sys_id>" in params.get("sysparm_query", ""):
            return {"result": []}
        linhas = []
        for sid, (num, ab, fe, _e) in CHAMADOS.items():
            linha = {"sys_id": sid, "number": num,
                     "opened_at": ab.strftime("%Y-%m-%d %H:%M:%S"),
                     "closed_at": fe.strftime("%Y-%m-%d %H:%M:%S") if fe else "",
                     "resolved_at": "", "state": "7"}
            linhas.append({c: linha.get(c, "") for c in pedidos})
        return {"result": linhas}
    return _falso_get(caminho, params, timeout)


sc._get = _com_chamados
tf._get = _com_chamados
r12 = cliente.post("/api/sn-consulta/exportar", json={
    "tabela": "incident", "campos": ["number"], "tempo_fila": "SPARE",
})
checar(r12.status_code == 200, f"HTTP 200 ({r12.status_code})")
linhas12 = [l for l in r12.text.splitlines() if l.strip()]
checar("Horas na fila" in linhas12[0] and "Medição" in linhas12[0],
       "o cabeçalho traz as colunas calculadas")
checar(linhas12[1].endswith("tempo_fila_medido"),
       "e a linha técnica também, para quem for cruzar com outro sistema")
por_num = {l.split(";")[0]: l for l in linhas12[2:] if l.startswith("INC")}
checar("72.0" in por_num["INC0000001"] and "3d 00:00" in por_num["INC0000001"],
       "o que foi e voltou sai com as duas passagens somadas")
checar("2" == por_num["INC0000001"].split(";")[3], "com as duas idas contadas")
# Comparado como número: a coluna é numérica na planilha, e "0" vs "0.0" é
# formatação, não conteúdo.
checar(float(por_num["INC0000002"].split(";")[1]) == 0.0,
       "o que nunca passou pela fila sai com zero")
checar(por_num["INC0000003"].split(";")[1] == "",
       "e o sem histórico sai VAZIO, não zero — somar a coluna não mente")
checar("histórico" in por_num["INC0000003"],
       "com o motivo escrito na linha")
sc._get = _falso_get
tf._get = _falso_get

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Tempo de fila íntegro.")
