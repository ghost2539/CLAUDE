#!/usr/bin/env python3
"""Analisa um HAR exportado do MDM de Coletores (Workspace ONE / AirWatch).

Por que HAR: a captura pelo console vive dentro da página e morre a cada
recarregamento — e a tela do MDM recarrega ao entrar em "Devices", ao
pesquisar e ao abrir um coletor. O HAR é gravado pelo próprio navegador
com "Preservar log" ligado, então atravessa todos esses recarregamentos.

Como gerar o arquivo (Chrome ou Edge):
    1. F12 > aba "Network" / "Rede".
    2. Marque "Preserve log" / "Preservar log".
    3. Faça o caminho todo: entrar, clicar em Devices, pesquisar,
       abrir um coletor, paginar.
    4. Clique com o botão direito na lista > "Save all as HAR (sanitized)"
       (ou o botão de download da barra). Prefira SEMPRE a opção
       "sanitized": ela remove cookies e cabeçalhos de autenticação.

Uso:
    python3 scripts/mdm_analisar_har.py arquivo.har
    python3 scripts/mdm_analisar_har.py arquivo.har --amostras 3 --saida analise.json
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlparse

# Campos que nunca entram na análise: são credenciais ou identificadores de
# sessão e não dizem nada sobre o formato dos dados.
SENSIVEL = re.compile(
    r"(authoriz|cookie|token|senha|password|passwd|secret|api[-_]?key"
    r"|sessionid|jsessionid|aw[-_]?tenant|bearer|credential)", re.I)

ESTATICO = re.compile(r"\.(js|css|png|jpe?g|gif|svg|woff2?|ttf|eot|ico|map)(\?|$)", re.I)

MAX_TEXTO = 300
MAX_PROF = 6


def cortar(s: str) -> str:
    s = str(s)
    return s if len(s) <= MAX_TEXTO else s[:MAX_TEXTO] + f"…(+{len(s) - MAX_TEXTO})"


def corpo_json(conteudo: dict):
    """Devolve o corpo da resposta já decodificado, ou None se não for JSON."""
    texto = conteudo.get("text")
    if not texto:
        return None
    if conteudo.get("encoding") == "base64":
        try:
            texto = base64.b64decode(texto).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — corpo ilegível não interrompe a análise
            return None
    try:
        return json.loads(texto)
    except (ValueError, TypeError):
        return None


def achar_lista(corpo):
    """Acha a lista principal da resposta (Devices, data, results, rows...)."""
    if not isinstance(corpo, (dict, list)):
        return None
    if isinstance(corpo, list):
        return ("(raiz)", len(corpo))
    for k, v in corpo.items():
        if isinstance(v, list) and v:
            return (k, len(v))
    for k, v in corpo.items():          # às vezes vem um nível abaixo
        if isinstance(v, dict):
            dentro = achar_lista(v)
            if dentro:
                return (f"{k}.{dentro[0]}", dentro[1])
    return None


def inferir(v, prof: int = 0):
    """Descreve o formato: para cada campo, o tipo e um exemplo curto."""
    if prof > MAX_PROF:
        return "…"
    if v is None:
        return "nulo"
    if isinstance(v, list):
        return OrderedDict([
            ("«lista»", f"{len(v)} item(ns)"),
            ("«item»", inferir(v[0], prof + 1) if v else "lista vazia"),
        ])
    if isinstance(v, dict):
        return OrderedDict(
            (k, "«removido»" if SENSIVEL.search(k) else inferir(val, prof + 1))
            for k, val in v.items()
        )
    if isinstance(v, bool):
        return f"booleano · ex: {v}"
    if isinstance(v, (int, float)):
        return f"número · ex: {v}"
    ex = cortar(v)
    return "texto · ex: " + (ex[:60] + "…" if len(ex) > 60 else ex)


def podar(v, max_amostras: int, prof: int = 0):
    """Encurta a estrutura preservando o formato, e remove credenciais."""
    if prof > MAX_PROF:
        return "…"
    if isinstance(v, list):
        return [podar(x, max_amostras, prof + 1) for x in v[:max_amostras]]
    if isinstance(v, dict):
        return {
            k: ("«removido»" if SENSIVEL.search(k) else podar(val, max_amostras, prof + 1))
            for k, val in v.items()
        }
    if isinstance(v, str):
        return cortar(v)
    return v


def analisar(har: dict, max_amostras: int) -> list[dict]:
    entradas = (har.get("log") or {}).get("entries") or []
    achados: "OrderedDict[str, dict]" = OrderedDict()

    for e in entradas:
        req = e.get("request") or {}
        resp = e.get("response") or {}
        url = req.get("url") or ""
        if not url or ESTATICO.search(url):
            continue
        corpo = corpo_json(resp.get("content") or {})
        if corpo is None:
            continue

        p = urlparse(url)
        chave = f"{req.get('method', 'GET')} {p.path}"
        lista = achar_lista(corpo)
        registros = lista[1] if lista else (1 if corpo else 0)

        atual = achados.get(chave)
        if atual is None:
            achados[chave] = {
                "metodo": req.get("method", "GET"),
                "caminho": p.path,
                "consulta_exemplo": p.query[:300],
                "status": resp.get("status"),
                "chamadas": 1,
                "registros": registros,
                "campo_lista": lista[0] if lista else None,
                "amostra": podar(corpo, max_amostras),
                "schema": inferir(corpo),
            }
        else:
            atual["chamadas"] += 1
            # fica com a chamada mais rica: é a que mostra melhor o formato
            if registros > atual["registros"]:
                atual.update({
                    "registros": registros,
                    "campo_lista": lista[0] if lista else None,
                    "consulta_exemplo": p.query[:300],
                    "amostra": podar(corpo, max_amostras),
                    "schema": inferir(corpo),
                })

    return sorted(achados.values(), key=lambda x: x["registros"], reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Analisa um HAR do MDM de Coletores.")
    ap.add_argument("har", help="arquivo .har exportado do navegador")
    ap.add_argument("--amostras", type=int, default=2,
                    help="registros de exemplo guardados por endpoint (padrão: 2)")
    ap.add_argument("--saida", default="", help="grava a análise em um .json")
    ap.add_argument("--schema", default="",
                    help="mostra o schema completo dos endpoints cujo caminho contém este texto")
    args = ap.parse_args()

    caminho = Path(args.har)
    if not caminho.exists():
        print(f"Arquivo não encontrado: {caminho}", file=sys.stderr)
        return 1

    try:
        har = json.loads(caminho.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        print(f"HAR inválido: {exc}", file=sys.stderr)
        return 1

    achados = analisar(har, args.amostras)
    if not achados:
        print("Nenhuma resposta JSON encontrada no HAR. Confira se 'Preservar log' "
              "estava ligado e se a lista chegou a carregar.")
        return 1

    print(f"\n{len(achados)} endpoint(s) com dados — do que mais traz para o que menos traz:\n")
    print(f"{'REGISTROS':>9}  {'CHAM':>4}  {'MÉTODO':<6}  {'CAMPO DA LISTA':<22}  CAMINHO")
    print("-" * 108)
    for a in achados:
        print(f"{a['registros']:>9}  {a['chamadas']:>4}  {a['metodo']:<6}  "
              f"{str(a['campo_lista'] or '-'):<22}  {a['caminho'][:52]}")

    alvos = [a for a in achados if not args.schema or args.schema.lower() in a["caminho"].lower()]
    for a in alvos[:3 if not args.schema else len(alvos)]:
        print(f"\n{'=' * 108}\n{a['metodo']} {a['caminho']}")
        if a["consulta_exemplo"]:
            print(f"consulta: ?{a['consulta_exemplo']}")
        print(f"{'-' * 108}")
        print(json.dumps(a["schema"], indent=2, ensure_ascii=False)[:6000])

    if args.saida:
        Path(args.saida).write_text(
            json.dumps({"origem": caminho.name, "endpoints": achados}, indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\nAnálise gravada em {args.saida}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
