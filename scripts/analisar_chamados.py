#!/usr/bin/env python3
"""Quem atendeu o chamado, de verdade — a partir da planilha e do ServiceNow.

    python3 scripts/analisar_chamados.py chamados.xlsx \\
        --grupo "SPARE - Equipamentos" --amostra 30
    python3 scripts/analisar_chamados.py chamados.xlsx \\
        --grupo "SPARE - Equipamentos" --saida analise.xlsx

Roda FORA do portal, no servidor, com a conta de serviço do ServiceNow
(SN_API_USER / SN_API_PASS / SN_API_BASE — as mesmas do portal). Como não
roda dentro do serviço, ele lê o arquivo de ambiente do serviço sozinho;
use --env-file se o seu estiver em outro lugar.

Para cada chamado da planilha ele responde:

  • passou pela nossa fila? quando entrou (o "bouncing") e quando saiu
  • enviamos equipamento? qual, com a evidência e o rastreio
  • quando NÓS resolvemos (a nota de envio), não quando encerraram
  • o TMA, do bouncing (ou da abertura) até o nosso envio

A regra vive em `chamados_nucleo.py` e é verificada por
`verificar_analise_chamados.py`. Aqui só tem leitura de planilha, rede e
Excel — de propósito: assim a regra pode ser conferida sem ServiceNow.

Só leitura. Nada é escrito no ServiceNow.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from scripts.chamados_nucleo import (  # noqa: E402
    MODELOS_PADRAO, TIPOS_PADRAO, analisar, resumir,
)

LOTE = 60          # números por consulta; a query do SN tem limite de tamanho
PAGINA = 1000


# ── ServiceNow (conta de serviço, leitura) ────────────────────────
# Onde o ambiente do serviço costuma estar. O script é de linha de
# comando: ele não roda dentro do serviço e, portanto, não herda essas
# variáveis — lê o arquivo, como o systemd faria.
ENVS_CONHECIDOS = (
    "/etc/portal_operacoes_spare_testes/environment",
    "/etc/portal_operacoes_spare/environment",
    "~/.config/portal-spare-testes/environment",
    "~/.config/portal-spare/environment",
)

_CHAVES = ("SN_API_BASE", "SN_API_USER", "SN_API_PASS", "SN_API_PROXY", "VERIFY_SSL")


class Conta:
    """Só o que o script precisa. Não depende do config do portal, que
    exige banco — aqui não há banco nenhum."""

    def __init__(self, valores: dict):
        self.SN_API_BASE = (valores.get("SN_API_BASE") or "").rstrip("/")
        self.SN_API_USER = valores.get("SN_API_USER") or ""
        self.SN_API_PASS = valores.get("SN_API_PASS") or ""
        self.SN_API_PROXY = valores.get("SN_API_PROXY") or ""
        v = str(valores.get("VERIFY_SSL", "1")).strip().lower()
        self.VERIFY_SSL = v not in ("0", "nao", "não", "false", "no")


def ler_env(caminho: Path) -> dict:
    """KEY=VALUE de um arquivo de ambiente, com aspas e comentários."""
    valores: dict[str, str] = {}
    try:
        texto = caminho.expanduser().read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return valores
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave = chave.strip().removeprefix("export ").strip()
        valor = valor.strip().strip('"').strip("'")
        if chave:
            valores[chave] = valor
    return valores


_CONTA: Conta | None = None


def preparar_conta(env_file: Path | None = None) -> Conta:
    """Monta a conta a partir do ambiente, do arquivo indicado ou dos conhecidos."""
    global _CONTA
    import os
    valores = {k: os.environ[k] for k in _CHAVES if os.environ.get(k)}
    origens = ["ambiente"] if valores.get("SN_API_USER") else []

    candidatos = [env_file] if env_file else [Path(p) for p in ENVS_CONHECIDOS]
    for caminho in candidatos:
        if not caminho:
            continue
        if valores.get("SN_API_USER") and valores.get("SN_API_PASS") and valores.get("SN_API_BASE"):
            break
        do_arquivo = ler_env(Path(caminho))
        if not do_arquivo:
            continue
        achou = False
        for k in _CHAVES:
            if not valores.get(k) and do_arquivo.get(k):
                valores[k] = do_arquivo[k]
                achou = True
        if achou:
            origens.append(str(Path(caminho).expanduser()))

    faltando = [n for n in ("SN_API_BASE", "SN_API_USER", "SN_API_PASS")
                if not valores.get(n)]
    if faltando:
        raise SystemExit(
            "Conta de serviço do ServiceNow não encontrada: falta "
            + ", ".join(faltando) + ".\n"
            "Passe --env-file com o arquivo de ambiente do serviço, ou exporte "
            "as variáveis antes de rodar. Procurei em:\n  "
            + "\n  ".join(str(Path(p).expanduser()) for p in candidatos if p))
    if valores.get("SN_API_PASS", "").startswith("@cofre:"):
        raise SystemExit(
            "A senha está guardada no cofre (@cofre:…). Rode com as variáveis "
            "já resolvidas — por exemplo, exportando-as a partir do serviço.")
    print(f"  conta de serviço: {valores['SN_API_USER']} @ {valores['SN_API_BASE']}"
          f" (de {', '.join(origens) or 'ambiente'})")
    _CONTA = Conta(valores)
    return _CONTA


def _cfg() -> Conta:
    return _CONTA or preparar_conta()


def consultar(tabela: str, query: str, campos: str, *, display=True,
              limite: int = 100000) -> list[dict]:
    """Table API paginada. Mesmo caminho que o portal usa."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    c = _cfg()
    proxies = ({"http": c.SN_API_PROXY, "https": c.SN_API_PROXY}
               if getattr(c, "SN_API_PROXY", "") else None)
    url = f"{c.SN_API_BASE}/api/now/table/{tabela}"
    fora: list[dict] = []
    deslocamento = 0
    while len(fora) < limite:
        pagina = min(PAGINA, limite - len(fora))
        r = requests.get(
            url,
            params={"sysparm_query": query, "sysparm_fields": campos,
                    "sysparm_display_value": "true" if display else "false",
                    "sysparm_exclude_reference_link": "true",
                    "sysparm_limit": str(pagina),
                    "sysparm_offset": str(deslocamento)},
            auth=(c.SN_API_USER, c.SN_API_PASS),
            headers={"Accept": "application/json"},
            proxies=proxies, verify=getattr(c, "VERIFY_SSL", True), timeout=90,
        )
        if r.status_code == 401:
            raise SystemExit("ServiceNow 401 — conta de serviço inválida ou sem papel de API.")
        if r.status_code == 403:
            raise SystemExit(
                f"ServiceNow 403 ao ler {tabela} — a conta de serviço não tem "
                f"permissão nessa tabela.")
        r.raise_for_status()
        linhas = r.json().get("result", [])
        fora.extend(linhas)
        if len(linhas) < pagina:
            break
        deslocamento += len(linhas)
    return fora


def _v(x):
    """Valor plano: a API devolve string ou {display_value, value}."""
    if isinstance(x, dict):
        return x.get("display_value") or x.get("value") or ""
    return "" if x is None else str(x)


def _lotes(itens: list, n: int):
    for i in range(0, len(itens), n):
        yield itens[i:i + n]


# ── Coleta ────────────────────────────────────────────────────────
def buscar_chamados(numeros: list[str]) -> dict[str, dict]:
    campos = ("number,sys_id,opened_at,assignment_group,resolved_at,closed_at,"
              "state,category,subcategory,short_description,resolved_by,closed_by")
    achados: dict[str, dict] = {}
    for lote in _lotes(numeros, LOTE):
        for l in consultar("incident", "numberIN" + ",".join(lote), campos):
            achados[_v(l.get("number"))] = {
                "numero": _v(l.get("number")),
                "sys_id": _v(l.get("sys_id")),
                "abertura": _v(l.get("opened_at")),
                "grupo_atual": _v(l.get("assignment_group")),
                "resolucao": _v(l.get("resolved_at")),
                "encerramento": _v(l.get("closed_at")),
                "estado": _v(l.get("state")),
                "categoria": _v(l.get("category")),
                "subcategoria": _v(l.get("subcategory")),
                "descricao": _v(l.get("short_description")),
            }
    return achados


def buscar_historico(sys_ids: list[str]) -> dict[str, list[dict]]:
    """Trocas de fila, do sys_audit. Cai para sys_history_line se preciso.

    O sys_audit é a fonte natural, mas em algumas instâncias ele é
    restrito ou rotacionado — e aí o histórico existe em outro lugar.
    Trocar de fonte em silêncio seria pior: o script diz qual usou.
    """
    por_chamado: dict[str, list[dict]] = {}
    try:
        for lote in _lotes(sys_ids, LOTE):
            consulta = ("tablename=incident^fieldname=assignment_group"
                        "^documentkeyIN" + ",".join(lote))
            for l in consultar("sys_audit", consulta,
                               "documentkey,oldvalue,newvalue,sys_created_on,user_name"):
                por_chamado.setdefault(_v(l.get("documentkey")), []).append({
                    "de": _v(l.get("oldvalue")), "para": _v(l.get("newvalue")),
                    "quando": _v(l.get("sys_created_on")),
                    "quem": _v(l.get("user_name")),
                })
        if por_chamado:
            print(f"  histórico de fila: sys_audit ({sum(len(v) for v in por_chamado.values())} trocas)")
            return por_chamado
        print("  sys_audit não devolveu trocas; tentando sys_history_line")
    except SystemExit as exc:
        print(f"  {exc}\n  tentando sys_history_line")

    for lote in _lotes(sys_ids, LOTE):
        consulta = ("element=assignment_group^set.idIN" + ",".join(lote))
        try:
            linhas = consultar("sys_history_line", consulta,
                               "set.id,old,new,update_time,user")
        except SystemExit as exc:
            print(f"  {exc}")
            return por_chamado
        for l in linhas:
            por_chamado.setdefault(_v(l.get("set.id")), []).append({
                "de": _v(l.get("old")), "para": _v(l.get("new")),
                "quando": _v(l.get("update_time")), "quem": _v(l.get("user")),
            })
    print(f"  histórico de fila: sys_history_line "
          f"({sum(len(v) for v in por_chamado.values())} trocas)")
    return por_chamado


def buscar_notas(sys_ids: list[str]) -> dict[str, list[dict]]:
    """Anotações de trabalho e normais, do sys_journal_field."""
    por_chamado: dict[str, list[dict]] = {}
    for lote in _lotes(sys_ids, LOTE):
        consulta = ("element_idIN" + ",".join(lote) +
                    "^elementINwork_notes,comments")
        for l in consultar("sys_journal_field", consulta,
                           "element_id,element,value,sys_created_on,sys_created_by"):
            por_chamado.setdefault(_v(l.get("element_id")), []).append({
                "quando": _v(l.get("sys_created_on")),
                "texto": _v(l.get("value")),
                "autor": _v(l.get("sys_created_by")),
                "tipo": _v(l.get("element")),
            })
    return por_chamado


# ── Planilha ──────────────────────────────────────────────────────
_COLUNAS_NUMERO = ("number", "numero", "número", "chamado", "incidente",
                   "incident", "ticket", "task")


def ler_numeros(caminho: Path) -> list[str]:
    import pandas as pd
    df = (pd.read_csv(caminho, dtype=str) if caminho.suffix.lower() == ".csv"
          else pd.read_excel(caminho, dtype=str))
    coluna = None
    for c in df.columns:
        if str(c).strip().lower() in _COLUNAS_NUMERO:
            coluna = c
            break
    if coluna is None:
        # Sem cabeçalho reconhecido, vale a coluna que parece número de chamado.
        for c in df.columns:
            amostra = df[c].dropna().astype(str).head(20)
            if len(amostra) and (amostra.str.match(r"(?i)^(inc|ritm|task)\d+").mean() > 0.6):
                coluna = c
                break
    if coluna is None:
        raise SystemExit(
            "Não achei a coluna com o número do chamado. Renomeie para "
            "'number' (ou 'chamado') e rode de novo.\nColunas encontradas: "
            + ", ".join(str(c) for c in df.columns))
    numeros = [str(v).strip() for v in df[coluna].dropna() if str(v).strip()]
    vistos, unicos = set(), []
    for n in numeros:
        if n.upper() not in vistos:
            vistos.add(n.upper())
            unicos.append(n)
    print(f"  coluna do chamado: \"{coluna}\" — {len(unicos)} números únicos "
          f"({len(numeros) - len(unicos)} repetidos ignorados)")
    return unicos


def gravar(linhas: list[dict], resumo: dict, saida: Path) -> None:
    import pandas as pd
    colunas = [
        ("numero", "Chamado"), ("subcategoria", "Subcategoria"),
        ("categoria", "Categoria"),
        ("atendido", "Atendido pelo time?"), ("motivo", "Por quê"),
        ("abertura", "Abertura"), ("entrada", "Entrada na nossa fila (bouncing)"),
        ("nossa_resolucao", "Nossa resolução"), ("base_da_data", "Origem da data"),
        ("tma_horas", "TMA (horas)"),
        ("equipamento", "Equipamento enviado"), ("tipo_equipamento", "Tipo"),
        ("rastreio", "Rastreio"), ("quem_enviou", "Quem registrou"),
        ("tipo_nota", "Origem da evidência"), ("evidencia", "Evidência (trecho)"),
        ("grupo_abertura", "Fila de abertura"), ("grupo_final", "Fila final"),
        ("filas", "Filas por onde passou"),
        ("saida_da_fila", "Saída da nossa fila"),
        ("resolucao_formal", "Resolução formal"),
    ]
    df = pd.DataFrame([{rot: l.get(ch) for ch, rot in colunas} for l in linhas])
    df["Atendido pelo time?"] = df["Atendido pelo time?"].map(
        lambda v: "Sim" if v else "Não")
    r = pd.DataFrame([
        {"Indicador": "Chamados na planilha", "Valor": resumo["total"]},
        {"Indicador": "Atendidos pelo time (com envio)", "Valor": resumo["atendidos"]},
        {"Indicador": "Passaram pela fila SEM envio", "Valor": resumo["passaram_sem_envio"]},
        {"Indicador": "Nunca estiveram na nossa fila", "Valor": resumo["nunca_foram_nossos"]},
    ])
    sub = pd.DataFrame(resumo["por_subcategoria"]).rename(columns={
        "subcategoria": "Subcategoria", "quantidade": "Atendidos",
        "tma_medio_h": "TMA médio (h)", "tma_mediana_h": "TMA mediana (h)"})
    with pd.ExcelWriter(saida, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="Chamados", index=False)
        r.to_excel(w, sheet_name="Resumo", index=False)
        if len(sub):
            sub.to_excel(w, sheet_name="TMA por subcategoria", index=False)


# ── Programa ──────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("planilha", type=Path, help="xlsx ou csv com os números dos chamados")
    ap.add_argument("--grupo", action="append", required=True,
                    help="nome da fila do time (pode repetir)")
    ap.add_argument("--saida", type=Path, default=Path("analise_chamados.xlsx"))
    ap.add_argument("--amostra", type=int, default=0,
                    help="processa só os N primeiros — use para conferir o critério antes")
    ap.add_argument("--modelos", type=Path,
                    help="arquivo com um modelo por linha, para somar aos conhecidos")
    ap.add_argument("--env-file", type=Path, dest="env_file",
                    help="arquivo de ambiente com SN_API_BASE/USER/PASS "
                         "(por padrão procura o do serviço)")
    args = ap.parse_args()

    preparar_conta(args.env_file)

    modelos = MODELOS_PADRAO
    if args.modelos and args.modelos.exists():
        extras = tuple(l.strip().upper() for l in
                       args.modelos.read_text(encoding="utf-8").splitlines() if l.strip())
        modelos = tuple(dict.fromkeys(MODELOS_PADRAO + extras))
        print(f"  modelos: {len(modelos)} (padrão + {len(extras)} do arquivo)")

    print(f"\nLendo {args.planilha}")
    numeros = ler_numeros(args.planilha)
    if args.amostra:
        numeros = numeros[:args.amostra]
        print(f"  amostra: {len(numeros)} chamados")

    inicio = time.time()
    print("Buscando os chamados no ServiceNow…")
    chamados = buscar_chamados(numeros)
    faltando = [n for n in numeros if n not in chamados]
    print(f"  {len(chamados)} encontrados"
          + (f", {len(faltando)} não achados (ex.: {', '.join(faltando[:3])})" if faltando else ""))
    if not chamados:
        print("Nenhum chamado encontrado — confira os números e o ambiente.")
        return 1

    sys_ids = [c["sys_id"] for c in chamados.values() if c["sys_id"]]
    print("Buscando o histórico de filas…")
    historico = buscar_historico(sys_ids)
    print("Buscando as anotações…")
    notas = buscar_notas(sys_ids)
    print(f"  {sum(len(v) for v in notas.values())} anotações lidas")

    linhas = []
    for numero in numeros:
        c = chamados.get(numero)
        if not c:
            linhas.append({"numero": numero, "atendido": False,
                           "passou_por_nos": False,
                           "motivo": "chamado não encontrado no ServiceNow"})
            continue
        linhas.append(analisar(c, historico.get(c["sys_id"], []),
                               notas.get(c["sys_id"], []), args.grupo, modelos))

    resumo = resumir(linhas)
    gravar(linhas, resumo, args.saida)

    print(f"\n── Resultado ({time.time() - inicio:.0f}s) ──")
    print(f"  chamados na planilha ............ {resumo['total']}")
    print(f"  atendidos pelo time (com envio) . {resumo['atendidos']}")
    print(f"  passaram pela fila SEM envio .... {resumo['passaram_sem_envio']}")
    print(f"  nunca estiveram na nossa fila ... {resumo['nunca_foram_nossos']}")
    if resumo["por_subcategoria"]:
        print("\n  TMA por subcategoria (só atendidos):")
        for s in resumo["por_subcategoria"][:15]:
            print(f"    {s['subcategoria'][:38]:<38} {s['quantidade']:>5}  "
                  f"médio {s['tma_medio_h']:>8.1f}h  mediana {s['tma_mediana_h']:>8.1f}h")
    print(f"\nPlanilha: {args.saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
