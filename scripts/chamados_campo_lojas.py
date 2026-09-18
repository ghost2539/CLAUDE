#!/usr/bin/env python3
"""Chamados das filas de técnico de campo, com o tempo que ficaram nelas.

    python3 scripts/chamados_campo_lojas.py
    python3 scripts/chamados_campo_lojas.py --desde 2025-01-01 --ate 2025-06-30
    python3 scripts/chamados_campo_lojas.py --saida /tmp/campo.csv

Coleta histórica de envio de técnico de campo às lojas. É consulta À PARTE
do portal: não passa por tela, não grava nada, e usa a conta de serviço que
o portal já usa para LER o ServiceNow — a mesma do `analisar_chamados.py`,
com o mesmo jeito de achar a credencial.

DUAS FASES, nesta ordem, que é o que a torna viável
---------------------------------------------------
1.  **Os chamados**, pela `task_sla` — que é onde o grupo de atribuição
    prende esse tipo de atendimento. Uma página por vez, só os campos
    pedidos.
2.  **O tempo em fila**, pelo histórico de troca de `assignment_group`
    (`sys_audit`), em lotes. Uma leitura por lote de chamados, não uma por
    chamado: para 20 mil chamados são ~120 chamadas em vez de 20 mil.

Fazer o contrário — perguntar o tempo de um chamado por vez enquanto
percorre — levaria horas.

O QUE PODE SAIR ERRADO, E COMO ESTÁ TRATADO
--------------------------------------------
*   **Um chamado aparece várias vezes.** Uma `task` tem quantas linhas de
    `task_sla` quantos forem os seus SLAs. Sem deduplicar por `sys_id` do
    chamado, o mesmo atendimento entraria três, quatro vezes na contagem do
    mês. É deduplicado.
*   **A fila medida é a DO CHAMADO.** São quatro filas; cada chamado é
    medido na que ele casou, não numa fila fixa.
*   **O fim da contagem é a RESOLUÇÃO**, não o encerramento. O encerramento
    é automático dias depois e inflaria o tempo de todo chamado resolvido
    dentro da fila.
*   **A fila pode estar gravada por sys_id** no histórico, em vez de por
    nome. Aí comparar com o nome não casa, e o resultado não é erro: é zero
    para todo mundo. Os sys_id são traduzidos antes de comparar.
*   **`sys_audit.tablename`** guarda a tabela real (`incident`,
    `sc_req_item`), não `task`. Por isso o filtro por tabela não entra aqui.

A conta do intervalo é a mesma que o portal usa
(`routers/sn_tempo_fila.intervalos_da_fila`), importada e não copiada: duas
cópias da mesma conta divergem, e essa conta já esteve errada duas vezes.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# O módulo da conta de serviço, do script que já existe.
from scripts.analisar_chamados import (  # noqa: E402
    _lotes, _v, consultar, preparar_conta,
)

# O cálculo do intervalo vem do portal. Importar exige as variáveis que o
# `config.py` cobra; num servidor já configurado elas estão no ambiente, e
# aqui só se garante um valor inócuo para o import não estourar — nenhum
# banco é aberto por este script.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PORTAL_SESSION_SECRET", "consulta-avulsa-sem-sessao")
from routers.sn_tempo_fila import intervalos_da_fila, _humano  # noqa: E402

# ── As quatro filas de técnico de campo ────────────────────────────────
# Casadas por TRECHO do nome (contém), como pedido: se a instalação tiver
# sufixo no nome do grupo, a busca por igualdade perderia o chamado.
FILAS = [
    "TI_N2_FLD_ENACEL_LOJAS",
    "TI_N2_FLD_RNR_VSiT",
    "TI_N2_FLD_SKY_LOJAS",
    "TI_N2_FLD_RNR_LOJAS_REMOTO",
]

CAMPO_FILA = "assignment_group"
LOTE_SYS_ID = 150          # sys_id tem 32 caracteres; a query viaja na URL
_RE_SYS_ID = re.compile(r"^[0-9a-f]{32}$")
_FMT = "%Y-%m-%d %H:%M:%S"

COLUNAS = [
    ("mes", "Mês"),
    ("numero", "Chamado"),
    ("aberto_em", "Aberto em"),
    ("solicitante", "Solicitante"),
    ("categoria", "Categoria"),
    ("subcategoria", "Subcategoria"),
    ("fila", "Fila"),
    ("horas_na_fila", "Horas na fila"),
    ("tempo_na_fila", "Tempo na fila"),
    ("idas_a_fila", "Idas à fila"),
    ("estado", "Estado"),
    ("fim_contagem", "Fim da contagem"),
    ("fim_origem", "Fim veio de"),
    ("base_medicao", "Base da medição"),
]


def _quando(bruto) -> datetime | None:
    texto = _v(bruto).strip()
    if not texto:
        return None
    for fmt in (_FMT, "%d/%m/%Y %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _qual_fila(nome: str) -> str:
    """Qual das quatro filas este grupo é. Vazio se nenhuma."""
    alto = (nome or "").upper()
    for f in FILAS:
        if f.upper() in alto:
            return f
    return ""


# ── Fase 1: os chamados, pela task_sla ─────────────────────────────────
def buscar_chamados(desde: str, ate: str) -> list[dict]:
    """Um registro por CHAMADO (não por SLA), das quatro filas, no período."""
    # `^OR` agrupa com a condição anterior; um `^` seguinte começa um novo
    # grupo em E. Por isso as quatro filas vêm PRIMEIRO, e as datas depois:
    # (fila1 OU fila2 OU fila3 OU fila4) E (aberto no período).
    ors = "^OR".join(f"task.{CAMPO_FILA}.nameLIKE{f}" for f in FILAS)
    query = (f"{ors}"
             f"^task.opened_at>=javascript:gs.dateGenerate('{desde}','00:00:00')"
             f"^task.opened_at<=javascript:gs.dateGenerate('{ate}','23:59:59')")
    campos = ",".join([
        "task.sys_id", "task.number", "task.opened_at",
        "task.caller_id", "task.opened_by", "task.requested_for",
        "task.category", "task.subcategory",
        f"task.{CAMPO_FILA}.name", "task.state",
        "task.resolved_at", "task.closed_at",
    ])
    print(f"  fase 1: lendo task_sla de {desde} a {ate}…")
    linhas = consultar("task_sla", query, campos, display=True)
    print(f"          {len(linhas)} linhas de SLA")

    # Uma `task` tem uma linha de `task_sla` por SLA. Sem deduplicar, o mesmo
    # atendimento entraria várias vezes na contagem do mês.
    por_chamado: dict[str, dict] = {}
    for l in linhas:
        sid = _v(l.get("task.sys_id"))
        if not sid or sid in por_chamado:
            continue
        grupo = _v(l.get(f"task.{CAMPO_FILA}.name"))
        fila = _qual_fila(grupo)
        if not fila:
            continue
        aberto = _quando(l.get("task.opened_at"))
        por_chamado[sid] = {
            "sys_id": sid,
            "numero": _v(l.get("task.number")),
            "aberto_em": _v(l.get("task.opened_at")),
            "mes": aberto.strftime("%Y-%m") if aberto else "",
            "solicitante": (_v(l.get("task.caller_id"))
                            or _v(l.get("task.requested_for"))
                            or _v(l.get("task.opened_by"))),
            "categoria": _v(l.get("task.category")),
            "subcategoria": _v(l.get("task.subcategory")),
            "fila": fila,
            "estado": _v(l.get("task.state")),
            "_aberto": aberto,
            # A RESOLUÇÃO fecha a conta; o encerramento é automático dias
            # depois e inflaria o tempo.
            "_resolvido": _quando(l.get("task.resolved_at")),
            "_encerrado": _quando(l.get("task.closed_at")),
        }
    print(f"          {len(por_chamado)} chamados distintos "
          f"({len(linhas) - len(por_chamado)} linhas eram SLA repetido do mesmo chamado)")
    return list(por_chamado.values())


# ── Fase 2: o tempo em fila, pelo histórico ────────────────────────────
def buscar_historico(sys_ids: list[str]) -> dict[str, list[dict]]:
    """As trocas de fila, em lotes. Uma leitura por lote, não por chamado."""
    eventos: dict[str, list[dict]] = defaultdict(list)
    lotes = list(_lotes(sys_ids, LOTE_SYS_ID))
    for i, lote in enumerate(lotes, 1):
        print(f"  fase 2: histórico, lote {i}/{len(lotes)}…", end="\r", flush=True)
        # Sem `tablename=`: o sys_audit guarda a tabela REAL (incident,
        # sc_req_item), e filtrar por `task` não casaria com nada — o
        # resultado seria zero para todo mundo, sem erro nenhum.
        linhas = consultar(
            "sys_audit",
            f"fieldname={CAMPO_FILA}^documentkeyIN{','.join(lote)}"
            "^ORDERBYsys_created_on",
            "documentkey,oldvalue,newvalue,sys_created_on",
            display=False, limite=200000)
        for l in linhas:
            eventos[_v(l.get("documentkey"))].append({
                "quando": _quando(l.get("sys_created_on")),
                "de": _v(l.get("oldvalue")),
                "para": _v(l.get("newvalue")),
            })
    print(" " * 60, end="\r")
    print(f"  fase 2: {sum(len(v) for v in eventos.values())} trocas de fila "
          f"em {len(eventos)} chamados")
    return eventos


def traduzir_filas(eventos: dict[str, list[dict]]) -> int:
    """sys_id de grupo → nome. Sem isto, comparar com o nome dá zero em tudo."""
    brutos = {v for lista in eventos.values() for e in lista
              for v in (e["de"], e["para"]) if _RE_SYS_ID.match(v or "")}
    if not brutos:
        return 0
    nomes: dict[str, str] = {}
    for lote in _lotes(sorted(brutos), LOTE_SYS_ID):
        for l in consultar("sys_user_group", f"sys_idIN{','.join(lote)}",
                           "sys_id,name", display=False):
            nomes[_v(l.get("sys_id"))] = _v(l.get("name"))
    trocados = 0
    for lista in eventos.values():
        for e in lista:
            for lado in ("de", "para"):
                novo = nomes.get(e[lado])
                if novo and novo != e[lado]:
                    e[lado] = novo
                    trocados += 1
    if trocados:
        print(f"  fase 2: {trocados} valores de fila traduzidos de sys_id para nome")
    return trocados


def medir(chamados: list[dict], eventos: dict[str, list[dict]]) -> None:
    """Preenche o tempo de fila de cada chamado, na fila DELE."""
    agora = datetime.now(timezone.utc)
    for c in chamados:
        # A resolução fecha a conta. O encerramento só entra quando não houve
        # resolução — o cancelado é o caso comum.
        fim, origem = c["_resolvido"], "resolvido"
        if fim is None:
            fim, origem = c["_encerrado"], ("encerrado" if c["_encerrado"] else "")
        do_chamado = eventos.get(c["sys_id"]) or []
        if do_chamado:
            r = intervalos_da_fila(do_chamado, c["fila"], c["_aberto"], fim, agora)
            base = "histórico"
        else:
            # Nunca trocou de fila: esteve a vida inteira na fila em que está,
            # e ela é a fila procurada (foi assim que ele entrou na lista).
            fechamento = fim or agora
            segundos = (max(0.0, (fechamento - c["_aberto"]).total_seconds())
                        if c["_aberto"] else 0.0)
            r = {"segundos": round(segundos), "horas": round(segundos / 3600, 2),
                 "passagens": 1}
            base = "sem troca de fila"
        c["horas_na_fila"] = r["horas"]
        c["tempo_na_fila"] = _humano(r["segundos"])
        c["idas_a_fila"] = r["passagens"]
        c["fim_contagem"] = fim.strftime(_FMT) if fim else ""
        c["fim_origem"] = origem
        c["base_medicao"] = base if fim or not c["_aberto"] else (
            base if origem else "sem data de encerramento")


# ── Saída ──────────────────────────────────────────────────────────────
def gravar(chamados: list[dict], saida: Path) -> None:
    chamados.sort(key=lambda c: (c["mes"], c["fila"], c["numero"]))
    with saida.open("w", encoding="utf-8", newline="") as f:
        f.write("﻿")   # BOM: o Excel em pt-BR abre com as colunas separadas
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow([rot for _c, rot in COLUNAS])
        for c in chamados:
            w.writerow([c.get(chave, "") for chave, _rot in COLUNAS])

        # Resumo por mês e fila, que é o formato do dado histórico pedido.
        # Cancelado e chamado sem tempo ficam de fora da média: não são
        # atendimento, e puxariam o número respondendo outra pergunta.
        w.writerow([])
        w.writerow(["RESUMO POR MES E FILA"])
        w.writerow(["Mes", "Fila", "Chamados", "Horas somadas", "Media (h)"])
        grupos: dict[tuple, list] = defaultdict(list)
        for c in chamados:
            grupos[(c["mes"], c["fila"])].append(c)
        for (mes, fila), itens in sorted(grupos.items()):
            com_tempo = [i for i in itens if float(i.get("horas_na_fila") or 0) > 0]
            total = sum(float(i["horas_na_fila"]) for i in com_tempo)
            w.writerow([mes, fila, len(itens), round(total, 2),
                        round(total / len(com_tempo), 2) if com_tempo else ""])
    print(f"\n  arquivo: {saida}  ({len(chamados)} chamados)")


def main() -> int:
    hoje = date.today().isoformat()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--desde", default="2025-01-01", help="AAAA-MM-DD (padrão: 2025-01-01)")
    p.add_argument("--ate", default=hoje, help="AAAA-MM-DD (padrão: hoje)")
    p.add_argument("--saida", default="", help="caminho do CSV")
    p.add_argument("--env-file", default="", help="arquivo com SN_API_USER/SN_API_PASS")
    a = p.parse_args()

    for rotulo, valor in (("--desde", a.desde), ("--ate", a.ate)):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", valor):
            raise SystemExit(f"{rotulo} deve ser AAAA-MM-DD; veio {valor!r}")

    saida = Path(a.saida) if a.saida else Path(
        f"chamados_campo_lojas_{a.desde}_a_{a.ate}.csv")

    print("Chamados das filas de técnico de campo")
    print(f"  filas: {', '.join(FILAS)}")
    preparar_conta(Path(a.env_file) if a.env_file else None)

    chamados = buscar_chamados(a.desde, a.ate)
    if not chamados:
        print("\n  Nenhum chamado encontrado. Confira os nomes das filas e o período.")
        return 1
    eventos = buscar_historico([c["sys_id"] for c in chamados])
    traduzir_filas(eventos)
    medir(chamados, eventos)
    gravar(chamados, saida)

    sem_tempo = sum(1 for c in chamados if not float(c.get("horas_na_fila") or 0))
    if sem_tempo == len(chamados):
        print("\n  ATENÇÃO: todos deram ZERO. O mais provável é o nome da fila não "
              "bater com o que está gravado no histórico do ServiceNow.")
    elif sem_tempo:
        print(f"  {sem_tempo} chamado(s) com tempo zero na fila.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
