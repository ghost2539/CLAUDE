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

ID_SPARE = "9" * 32          # o sys_id da fila do SPARE, para a tradução
CHAMADOS = {
    # sys_id: (número, abertura, encerramento, estado, fila atual, eventos)
    "a" * 32: ("INC0000001", t(1, 8), t(7, 0), "7", OUTRA, [
        (t(1, 10), OUTRA, SPARE), (t(2, 10), SPARE, TERCEIRA),
        (t(4, 10), TERCEIRA, SPARE), (t(6, 10), SPARE, OUTRA)]),
    "b" * 32: ("INC0000002", t(1, 8), t(3, 8), "7", TERCEIRA, [
        (t(1, 9), OUTRA, TERCEIRA)]),                       # nunca no SPARE
    # Sem troca de fila e ATUALMENTE no SPARE: nasceu e morreu na fila. Antes
    # isto respondia "não medido"; agora conta a vida inteira do chamado.
    "c" * 32: ("INC0000003", t(1, 8), t(3, 8), "7", SPARE, []),
    # Sem troca de fila e em outra fila: zero, e zero de verdade.
    "d" * 32: ("INC0000004", t(1, 8), t(2, 8), "7", TERCEIRA, []),
    # CANCELADO, e passou pelo SPARE: o tempo sai, mas marcado.
    "e" * 32: ("INC0000005", t(1, 8), t(2, 8), "8", SPARE, [
        (t(1, 8), OUTRA, SPARE)]),
    # O histórico deste guarda a fila por SYS_ID, não por nome. Sem tradução,
    # "SPARE" não casa e o chamado vira zero — o defeito relatado.
    "f" * 32: ("INC0000006", t(1, 8), t(3, 8), "7", SPARE, [
        (t(1, 8), "0" * 32, ID_SPARE)]),
}
chamadas: list[dict] = []


# O que servidor e proxy fazem com URL grande: 414. Sem isto a verificação
# passava feliz com uma query de 8,3 KB que estourava em produção — e foi
# assim que o 414 da exportação escapou daqui.
LIMITE_URL = 8000


def _falso_get(caminho, params, timeout=60):
    chamadas.append({"caminho": caminho, "params": dict(params)})
    tamanho = sum(len(str(k)) + len(str(v)) + 2 for k, v in params.items())
    if tamanho > LIMITE_URL:
        from fastapi import HTTPException as _HE
        raise _HE(502, f"ServiceNow retornou 414 (URL de {tamanho} bytes).")
    q = params.get("sysparm_query", "")
    if caminho == "/api/now/table/sys_audit":
        querem = set(q.split("documentkeyIN")[1].split("^")[0].split(",")) \
            if "documentkeyIN" in q else set(CHAMADOS)
        fora = []
        for sid, (_n, _a, _f, _e, _g, eventos) in CHAMADOS.items():
            if sid not in querem:
                continue
            for quando, de, para in eventos:
                fora.append({"documentkey": sid, "oldvalue": de, "newvalue": para,
                             "sys_created_on": quando.strftime("%Y-%m-%d %H:%M:%S")})
        return {"result": fora}
    if caminho == "/api/now/table/sys_user_group":
        pedidos = set(q.split("sys_idIN")[1].split("^")[0].split(","))
        return {"result": [{"sys_id": ID_SPARE, "name": SPARE}]
                if ID_SPARE in pedidos else {"result": []}["result"]}
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
            "closed_at": (fe.strftime("%Y-%m-%d %H:%M:%S") if fe else ""),
            "estado": est, "estado_rotulo": "", "fila_atual": grupo}
           for sid, (_n, ab, fe, est, grupo, _e) in CHAMADOS.items()]
res = tf.medir("incident", entrada, "SPARE")

checar(res["a" * 32]["medido"] is True and res["a" * 32]["horas"] == 72.0,
       f"o que foi e voltou: 3 dias ({res['a' * 32]['horas']}h)")
checar(res["a" * 32]["passagens"] == 2, "com duas idas à fila")
checar(res["a" * 32]["base"] == "histórico", "medido pelo histórico")
checar(res["b" * 32]["medido"] is True and res["b" * 32]["segundos"] == 0,
       "o que nunca passou pela fila: medido, e zero")

print("\n[8b] Sem troca de fila: conta a vida do chamado, se a fila casa")
checar(res["c" * 32]["medido"] is True and res["c" * 32]["horas"] == 48.0,
       f"nasceu e morreu no SPARE: 2 dias ({res['c' * 32]['horas']}h)")
checar(res["c" * 32]["base"] == "sem troca de fila",
       "e a coluna diz que a base foi outra — o número não sai do histórico")
checar(res["d" * 32]["segundos"] == 0 and res["d" * 32]["base"] == "sem troca de fila",
       "sem troca e em outra fila: zero, e zero de verdade")

print("\n[8c] Cancelado é dito, e o tempo sai marcado")
checar(res["e" * 32]["cancelado"] is True, "o estado 8 é reconhecido como cancelado")
checar(res["e" * 32]["base"] == "cancelado",
       "a base da medição diz 'cancelado' — muda a leitura do número")
checar(res["e" * 32]["horas"] == 24.0,
       f"e o tempo sai assim mesmo ({res['e' * 32]['horas']}h), para quem quiser somar")
checar(res["e" * 32]["estado"] == "8", "com o estado na linha")
# Pelo rótulo também, porque o código muda em instância customizada.
checar(tf._cancelado("99", "Cancelado pelo solicitante") is True,
       "e reconhece pelo rótulo quando o código não é o padrão")
checar(tf._cancelado("7", "Closed Complete") is False, "sem falso positivo")

print("\n[8d] Fila guardada por sys_id: traduz antes de comparar")
# Sem isto, comparar "SPARE" com um sys_id não casa nunca — e o resultado
# não é erro, é ZERO PARA TODO MUNDO. Foi o que apareceu.
checar(res["f" * 32]["horas"] == 48.0,
       f"o histórico por sys_id é traduzido e mede certo ({res['f' * 32]['horas']}h)")
bruto = {"x": [{"quando": t(1, 0), "de": "", "para": ID_SPARE}]}
semtrad = tf.intervalos_da_fila(bruto["x"], "SPARE", t(1, 0), t(3, 0))
checar(semtrad["segundos"] == 0,
       "contraprova: sem traduzir, o mesmo chamado daria zero")

checar(all(c["params"].get("sysparm_display_value") == "false"
           for c in chamadas if c["caminho"] == "/api/now/table/sys_audit"),
       "o histórico é lido em UTC e formato fixo, não no fuso do usuário")
checar(all("fieldname=assignment_group" in c["params"]["sysparm_query"]
           for c in chamadas if c["caminho"] == "/api/now/table/sys_audit"),
       "e só as trocas de fila, não a auditoria inteira do chamado")

print("\n[8e] Lista longa de chamados: a URL não estoura")
# O 414 que apareceu na exportação. O lote de 250 foi dimensionado para
# NÚMEROS de chamado (INC1234567, 11 caracteres) e depois reusado para listas
# de sys_id, que têm 32: os mesmos 250 itens passaram de 2,7 KB para 8,3 KB.
# Na tela não aparecia (ela pede 100 por vez); só a exportação chegava lá.
MUITOS = [{"sys_id": f"{i:032x}", "opened_at": "2026-09-01 08:00:00",
           "closed_at": "2026-09-05 08:00:00", "estado": "7",
           "estado_rotulo": "", "fila_atual": SPARE}
          for i in range(1, 1201)]
chamadas.clear()
res_muitos = tf.medir("incident", MUITOS, "SPARE")
checar(len(res_muitos) == 1200, "1.200 chamados medidos sem estourar a URL")
consultas = [c for c in chamadas if c["caminho"] == "/api/now/table/sys_audit"]
checar(len(consultas) >= 6,
       f"a lista foi partida por TAMANHO, não por contagem ({len(consultas)} blocos)")
maior = max(len(c["params"]["sysparm_query"]) for c in consultas)
checar(maior <= sc.TETO_QUERY,
       f"e a maior query cabe no teto ({maior} de {sc.TETO_QUERY} caracteres)")

# Contraprova: 250 sys_ids por bloco, como era antes, passa do limite.
fixo = len("tablename=incident^fieldname=assignment_group"
           "^documentkeyIN^ORDERBYsys_created_on")
antes = fixo + len(",".join(m["sys_id"] for m in MUITOS[:250]))
checar(antes > LIMITE_URL,
       f"contraprova: 250 sys_ids num bloco davam {antes} caracteres — o 414")
checar(fixo + len(",".join(f"INC{i:07d}" for i in range(250))) < LIMITE_URL,
       "e com 250 NÚMEROS cabia, que é por isso que o defeito passou despercebido")

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
checar(d9["pedidos"] == 6 and d9["medidos"] == 6 and d9["nao_medidos"] == 0,
       "separa pedidos, medidos e não medidos")
checar(d9["sem_troca_de_fila"] == 2,
       "conta quantos foram medidos sem histórico de troca de fila")
checar(d9["cancelados"] == 1, "e quantos estão cancelados")
# Passaram pela fila: a, c, e, f (b e d deram zero).
checar(d9["passaram_pela_fila"] == 4, "e quantos realmente passaram pela fila")
# A média exclui o cancelado (e = 24h): (72 + 48 + 48) / 3 = 56.
checar(d9["media_horas"] == 56.0,
       f"a média deixa o cancelado de fora: 56h ({d9['media_horas']})")
# Contraprova: incluindo o cancelado a média cairia, porque cancelamento é
# rápido e não é atendimento.
com_cancelado = round((72 + 48 + 48 + 24) / 4, 2)
checar(com_cancelado < d9["media_horas"],
       f"contraprova: com o cancelado dentro a média cairia para {com_cancelado}h")
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
checar("tempo_fila_base" in fonte and "estado_chamado" in fonte,
       "as colunas de base da medição e de estado do chamado existem")
checar('"assignment_group.name"' in fonte,
       "a fila atual é pedida por .name — o dot-walk traz o NOME mesmo quando "
       "o campo volta como sys_id")
checar('"sysparm_display_value": "all"' in fonte,
       "medindo, pede-se `all`: UTC nas datas E rótulo no estado, na mesma resposta")
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
checar('"Base da medição"' in fonte and '"Estado"' in fonte,
       "os rótulos das colunas calculadas estão no servidor")
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
        for sid, (num, ab, fe, est, grupo, _e) in CHAMADOS.items():
            # `display_value=all`: cada campo volta como {value, display_value}.
            # É assim que a data sai em UTC e o estado sai com rótulo na mesma
            # resposta, e é o formato que `_valor_cru`/`_valor_plano` esperam.
            crus = {"sys_id": sid, "number": num,
                    "opened_at": ab.strftime("%Y-%m-%d %H:%M:%S"),
                    "closed_at": fe.strftime("%Y-%m-%d %H:%M:%S") if fe else "",
                    "resolved_at": "", "state": est,
                    "assignment_group.name": grupo}
            rotulos = dict(crus, state={"7": "Encerrado", "8": "Cancelado"}.get(est, est))
            linhas.append({c: {"value": crus.get(c, ""),
                               "display_value": rotulos.get(c, "")} for c in pedidos})
        return {"result": linhas}
    return _falso_get(caminho, params, timeout)


sc._get = _com_chamados
tf._get = _com_chamados
r12 = cliente.post("/api/sn-consulta/exportar", json={
    "tabela": "incident", "campos": ["number"], "tempo_fila": "SPARE",
})
checar(r12.status_code == 200, f"HTTP 200 ({r12.status_code})")
linhas12 = [l for l in r12.text.splitlines() if l.strip()]
checar("Horas na fila" in linhas12[0] and "Base da medição" in linhas12[0]
       and "Estado" in linhas12[0],
       "o cabeçalho traz as colunas calculadas")
checar(linhas12[1].endswith("tempo_fila_base"),
       "e a linha técnica também, para quem for cruzar com outro sistema")
por_num = {l.split(";")[0]: l for l in linhas12[2:] if l.startswith("INC")}
checar("72.0" in por_num["INC0000001"] and "3d 00:00" in por_num["INC0000001"],
       "o que foi e voltou sai com as duas passagens somadas")
checar("2" == por_num["INC0000001"].split(";")[3], "com as duas idas contadas")
# Comparado como número: a coluna é numérica na planilha, e "0" vs "0.0" é
# formatação, não conteúdo.
checar(float(por_num["INC0000002"].split(";")[1]) == 0.0,
       "o que nunca passou pela fila sai com zero")
checar(float(por_num["INC0000003"].split(";")[1]) == 48.0,
       "o sem troca de fila conta a vida do chamado")
checar("sem troca de fila" in por_num["INC0000003"],
       "e a linha diz que a base foi essa, não o histórico")
# O que muda a leitura do número: cancelado precisa estar na MESMA linha.
checar("Cancelado" in por_num["INC0000005"],
       "o cancelado é dito na linha dele, na coluna de estado e na base")
checar(float(por_num["INC0000005"].split(";")[1]) == 24.0,
       "com o tempo saindo assim mesmo")
checar(float(por_num["INC0000006"].split(";")[1]) == 48.0,
       "e o histórico gravado por sys_id é traduzido antes de comparar")

print("\n[12b] O arquivo diz o que aconteceu com a medição")
# "O arquivo não trouxe os tempos" é indiagnosticável de fora: quem está com
# a planilha na mão não consegue separar "a medição não foi pedida" de "foi
# pedida e nenhum chamado tinha histórico" de "a fila está escrita errada".
# O rodapé responde os três.
rodape = r12.text.strip().splitlines()
resumo = [l for l in rodape if l.startswith("Medicao de tempo na fila")]
checar(len(resumo) == 1, "o arquivo traz um resumo da medição no fim")
checar("'SPARE'" in resumo[0], "dizendo qual fila foi medida")
checar("apurados:" in resumo[0] and "sem apuracao:" in resumo[0]
       and "com tempo maior que zero:" in resumo[0],
       "e as três contagens que separam as causas")
checar(any(l.strip().startswith("base '") for l in rodape),
       "com a contagem por base de medição — é o que separa as causas")


def _nada_medido(caminho, params, timeout=60):
    if caminho == "/api/now/table/sys_audit":
        return {"result": []}
    if caminho.startswith("/api/now/table/incident"):
        # Sem histórico E em outra fila: medido, mas zero.
        d = _com_chamados(caminho, params, timeout)
        for linha in d.get("result") or []:
            if "assignment_group.name" in linha:
                linha["assignment_group.name"] = {"value": OUTRA, "display_value": OUTRA}
        return d
    return _com_chamados(caminho, params, timeout)


sc._get = _nada_medido
tf._get = _nada_medido
r12b = cliente.post("/api/sn-consulta/exportar", json={
    "tabela": "incident", "campos": ["number"], "tempo_fila": "SPARE",
})
# Medido e ZERO não é "não medido": zero é uma medição. O que o arquivo tem
# de avisar é quando TUDO deu zero, que é o caso que mais parece "a
# exportação não trouxe os tempos" — a coluna vem, preenchida com zero.
checar("todos os chamados deram ZERO na fila" in r12b.text,
       "tudo zerado tem aviso próprio, separado de 'não apurado'")
checar("o nome da fila" in r12b.text,
       "apontando a causa mais provável: o nome da fila escrito diferente")
checar("com tempo maior que zero: 0" in r12b.text,
       "e o resumo separa apurado de apurado-com-tempo")
sc._get = _com_chamados
tf._get = _com_chamados

print("\n[13] Erro no meio da exportação não vira arquivo vazio")
# Com StreamingResponse o HTTP 200 e os cabeçalhos já saíram quando a
# primeira linha é gerada. Uma exceção depois disso dava download truncado —
# e foi assim que uma exportação chegou vazia sem ninguém saber por quê.
def _audit_negado(caminho, params, timeout=60):
    if caminho == "/api/now/table/sys_audit":
        raise HTTPException(502, "A conta de serviço não tem acesso a esta tabela (403).")
    return _com_chamados(caminho, params, timeout)


from fastapi import HTTPException  # noqa: E402

sc._get = _audit_negado
tf._get = _audit_negado
r13 = cliente.post("/api/sn-consulta/exportar", json={
    "tabela": "incident", "campos": ["number"], "tempo_fila": "SPARE",
})
checar(r13.status_code == 200, f"responde 200, como qualquer streaming ({r13.status_code})")
checar("INCOMPLETO" in r13.text,
       "mas o ARQUIVO diz que está incompleto — vazio calado é o pior resultado")
checar("403" in r13.text,
       "e traz o motivo, para não virar caça ao fantasma")
sc._get = _com_chamados
tf._get = _com_chamados
sc._get = _falso_get
tf._get = _falso_get

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Tempo de fila íntegro.")
