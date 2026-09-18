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

# A REGRA é do router; aqui fica só o transporte. Duas cópias da mesma regra
# divergem, e regra de medição que diverge não dá erro: dá número diferente
# no botão e no script, para a mesma pergunta.
from routers.sn_campo_lojas import (  # noqa: E402
    CAMPOS_TASK_SLA, CAMPO_FILA, COLUNAS, FILAS, LOTE_SYS_ID,
    aplicar_nomes, evento, linhas_resumo, medir_chamados, montar_chamados,
    query_task_sla, sys_ids_de_grupo,
)


# ── Fase 1: os chamados, pela task_sla ─────────────────────────────────
def buscar_chamados(desde: str, ate: str) -> list[dict]:
    print(f"  fase 1: lendo task_sla de {desde} a {ate}…")
    linhas = consultar("task_sla", query_task_sla(desde, ate),
                       CAMPOS_TASK_SLA, display=True)
    print(f"          {len(linhas)} linhas de SLA")
    chamados = montar_chamados(linhas)
    print(f"          {len(chamados)} chamados distintos "
          f"({len(linhas) - len(chamados)} linhas eram SLA repetido ou de outra fila)")
    return chamados


# ── Fase 2: o tempo em fila, pelo histórico ────────────────────────────
def buscar_historico(sys_ids: list[str]) -> dict[str, list[dict]]:
    """As trocas de fila, em lotes. Uma leitura por lote, não por chamado."""
    eventos: dict[str, list[dict]] = defaultdict(list)
    lotes = list(_lotes(sys_ids, LOTE_SYS_ID))
    for i, lote in enumerate(lotes, 1):
        print(f"  fase 2: histórico, lote {i}/{len(lotes)}…", end="\r", flush=True)
        # Sem `tablename=`: o sys_audit guarda a tabela REAL (incident,
        # sc_req_item), e `task` não casaria com nada — zero para todo mundo.
        for l in consultar(
                "sys_audit",
                f"fieldname={CAMPO_FILA}^documentkeyIN{','.join(lote)}"
                "^ORDERBYsys_created_on",
                "documentkey,oldvalue,newvalue,sys_created_on",
                display=False, limite=200000):
            eventos[_v(l.get("documentkey"))].append(evento(l))
    print(" " * 60, end="\r")
    print(f"  fase 2: {sum(len(v) for v in eventos.values())} trocas de fila "
          f"em {len(eventos)} chamados")
    return eventos


def traduzir_filas(eventos: dict[str, list[dict]]) -> int:
    """sys_id de grupo → nome. Sem isto, comparar com o nome dá zero em tudo."""
    alvos = sys_ids_de_grupo(eventos)
    if not alvos:
        return 0
    nomes: dict[str, str] = {}
    for lote in _lotes(alvos, LOTE_SYS_ID):
        for l in consultar("sys_user_group", f"sys_idIN{','.join(lote)}",
                           "sys_id,name", display=False):
            nomes[_v(l.get("sys_id"))] = _v(l.get("name"))
    trocados = aplicar_nomes(eventos, nomes)
    if trocados:
        print(f"  fase 2: {trocados} valores de fila traduzidos de sys_id para nome")
    return trocados


# ── Saída ──────────────────────────────────────────────────────────────
def gravar(chamados: list[dict], saida: Path) -> None:
    chamados.sort(key=lambda c: (c["mes"], c["fila"], c["numero"]))
    with saida.open("w", encoding="utf-8", newline="") as f:
        f.write("\ufeff")   # BOM: o Excel em pt-BR abre com as colunas separadas
        w = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        w.writerow([rot for _c, rot in COLUNAS])
        for c in chamados:
            w.writerow([c.get(chave, "") for chave, _rot in COLUNAS])
        for linha in linhas_resumo(chamados):
            w.writerow(linha)
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
    medir_chamados(chamados, eventos)
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
