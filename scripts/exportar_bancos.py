#!/usr/bin/env python3
"""Copia bancos SQLite com o serviço NO AR, e confere do outro lado.

    # No servidor de ORIGEM — gera as cópias e o manifesto:
    python3 exportar_bancos.py exportar /caminho/data/db /destino/saida

    # No servidor de DESTINO — compara o que chegou com o manifesto:
    python3 exportar_bancos.py conferir /caminho/data/db /destino/saida/manifesto.json

Só usa a biblioteca padrão: roda em qualquer servidor, sem venv, sem
instalar nada, em qualquer branch do projeto.

POR QUE NÃO `cp`
----------------
SQLite guarda as transações recentes num arquivo `-wal` ao lado do banco.
`cp banco.db` leva só o `.db` e deixa o `-wal` para trás: o que estava
escrito ali some, e o arquivo que chega do outro lado abre com
"database disk image is malformed". Foi assim que o
`orcamento_manutencao.db` chegou quebrado no servidor novo.

`sqlite3.Connection.backup()` faz o contrário: abre o banco pelo motor do
SQLite, que enxerga o `-wal`, e escreve um arquivo NOVO já consolidado. É
a mesma coisa que o comando `.backup` do cliente `sqlite3` — que muitos
servidores não têm instalado — só que pelo Python, que está em todo lugar.

O MANIFESTO
-----------
Copiar o arquivo é a parte fácil, e é a que engana: o arquivo chega, com o
tamanho certo, e a tela abre vazia. Por isso a exportação anota quantas
linhas cada tabela tinha NA ORIGEM, e o modo `conferir` compara com o que
está no destino. Diferença é dita tabela a tabela, e o script sai com erro
— nunca "terminou com sucesso" enquanto falta linha.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _tabelas(con: sqlite3.Connection) -> dict[str, int]:
    """Nome → linhas, para toda tabela de verdade (fora as internas do SQLite)."""
    nomes = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    fora = {}
    for n in nomes:
        try:
            fora[n] = con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
        except sqlite3.DatabaseError as exc:
            fora[n] = f"ERRO: {exc}"
    return fora


def _abrir_ro(caminho: Path) -> sqlite3.Connection:
    """Somente leitura, mas SEM `immutable`: o motor precisa enxergar o -wal."""
    return sqlite3.connect(f"file:{caminho}?mode=ro", uri=True)


def exportar(origem: Path, saida: Path) -> int:
    bancos = sorted(origem.glob("*.db")) if origem.is_dir() else [origem]
    if not bancos:
        print(f"ERRO: nenhum .db em {origem}")
        return 1
    saida.mkdir(parents=True, exist_ok=True)
    manifesto: dict = {
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origem": str(origem),
        "bancos": {},
    }
    problemas: list[str] = []
    print(f"{len(bancos)} banco(s) em {origem}\n")
    for src in bancos:
        destino = saida / src.name
        print(f"── {src.name}")
        try:
            con = _abrir_ro(src)
        except sqlite3.DatabaseError as exc:
            print(f"   NÃO ABRIU: {exc}")
            problemas.append(f"{src.name}: não abriu ({exc})")
            continue

        integro = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integro != "ok":
            # Copia mesmo assim: um banco meio quebrado ainda tem dado, e é
            # melhor levar e tratar do outro lado do que deixar para trás.
            print(f"   ATENÇÃO: a ORIGEM já está danificada ({integro[:60]})")
            problemas.append(f"{src.name}: origem danificada ({integro[:60]})")

        tabelas = _tabelas(con)
        try:
            novo = sqlite3.connect(destino)
            con.backup(novo)          # consolida o -wal no arquivo novo
            novo.close()
        except sqlite3.DatabaseError as exc:
            print(f"   FALHOU a cópia: {exc}")
            problemas.append(f"{src.name}: cópia falhou ({exc})")
            con.close()
            continue
        con.close()

        conf = sqlite3.connect(destino)
        integro_dst = conf.execute("PRAGMA integrity_check").fetchone()[0]
        tabelas_dst = _tabelas(conf)
        conf.close()

        linhas = sum(v for v in tabelas.values() if isinstance(v, int))
        print(f"   {len(tabelas)} tabela(s), {linhas} linha(s)  →  {destino.name}"
              f"  [{destino.stat().st_size/1048576:.1f} MB]")
        if integro_dst != "ok":
            print(f"   A CÓPIA saiu danificada: {integro_dst[:60]}")
            problemas.append(f"{src.name}: cópia danificada")
        if tabelas_dst != tabelas:
            for t in sorted(set(tabelas) | set(tabelas_dst)):
                if tabelas.get(t) != tabelas_dst.get(t):
                    print(f"   DIFERENÇA em {t}: origem={tabelas.get(t)} "
                          f"cópia={tabelas_dst.get(t)}")
            problemas.append(f"{src.name}: cópia não bate com a origem")

        manifesto["bancos"][src.name] = {
            "integridade": integro,
            "tabelas": tabelas,
            "bytes": destino.stat().st_size,
        }

    alvo = saida / "manifesto.json"
    alvo.write_text(json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifesto: {alvo}")
    if problemas:
        print(f"\n{len(problemas)} problema(s) na exportação:")
        for p in problemas:
            print("  -", p)
        print("\nLeve os arquivos assim mesmo, mas trate isto antes de liberar a tela.")
        return 1
    print("Todas as cópias saíram íntegras e com as mesmas contagens da origem.")
    return 0


def conferir(destino: Path, manifesto_path: Path) -> int:
    manifesto = json.loads(manifesto_path.read_text(encoding="utf-8"))
    print(f"Manifesto de {manifesto.get('gerado_em', '?')} "
          f"(origem: {manifesto.get('origem', '?')})\n")
    faltando, divergentes = [], []
    for nome, esperado in sorted(manifesto["bancos"].items()):
        arq = destino / nome
        if not arq.is_file():
            print(f"── {nome}\n   NÃO CHEGOU")
            faltando.append(nome)
            continue
        con = sqlite3.connect(f"file:{arq}?mode=ro", uri=True)
        integro = con.execute("PRAGMA integrity_check").fetchone()[0]
        tabelas = _tabelas(con)
        con.close()

        difs = []
        for t in sorted(set(esperado["tabelas"]) | set(tabelas)):
            na_origem = esperado["tabelas"].get(t, "—")
            aqui = tabelas.get(t, "—")
            if na_origem != aqui:
                difs.append(f"{t}: origem={na_origem} aqui={aqui}")
        marca = "ok" if (integro == "ok" and not difs) else "PROBLEMA"
        linhas = sum(v for v in tabelas.values() if isinstance(v, int))
        print(f"── {nome}  {len(tabelas)} tabela(s), {linhas} linha(s)  [{marca}]")
        if integro != "ok":
            print(f"   INTEGRIDADE: {integro[:80]}")
            divergentes.append(nome)
        for d in difs:
            print(f"   {d}")
        if difs:
            divergentes.append(nome)

    print()
    if faltando:
        print("Não chegaram: " + ", ".join(faltando))
    if divergentes:
        print("Com diferença: " + ", ".join(sorted(set(divergentes))))
    if faltando or divergentes:
        print("\nNÃO libere a tela. Corrija e confira de novo.")
        return 1
    print("Todos os bancos chegaram íntegros e com as contagens da origem.")
    return 0


def main() -> int:
    if len(sys.argv) < 4 or sys.argv[1] not in ("exportar", "conferir"):
        print(__doc__)
        return 1
    modo, a, b = sys.argv[1], Path(sys.argv[2]).expanduser(), Path(sys.argv[3]).expanduser()
    if modo == "exportar":
        if not a.exists():
            print(f"ERRO: não achei {a}")
            return 1
        return exportar(a, b)
    if not a.is_dir():
        print(f"ERRO: não achei a pasta de bancos {a}")
        return 1
    if not b.is_file():
        print(f"ERRO: não achei o manifesto {b}")
        return 1
    return conferir(a, b)


if __name__ == "__main__":
    sys.exit(main())
