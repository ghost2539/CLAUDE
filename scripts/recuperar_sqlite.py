#!/usr/bin/env python3
"""Reconstrói um banco SQLite danificado, salvando o que ainda se lê.

    python3 scripts/recuperar_sqlite.py <arquivo.db>              # só confere
    python3 scripts/recuperar_sqlite.py <arquivo.db> --gravar     # reconstrói

Nunca escreve no arquivo de origem. A saída vai para `<arquivo>.recuperado`,
e trocar um pelo outro é decisão sua, depois de ver as contagens.

Quando serve: `PRAGMA integrity_check` acusa erro mas os dados aparentemente
estão lá. Isso acontece quando algumas páginas do arquivo se perderam — em
geral porque a cópia foi feita com um `cp` enquanto a aplicação escrevia, e
as transações que estavam no arquivo `-wal` ficaram para trás.

Copiar de novo não conserta: o dano está no arquivo, não no caminho. O que
resolve é ler tabela por tabela e regravar em um arquivo novo, que nasce
íntegro. O que não se conseguir ler é CONTADO e dito, nunca engolido — um
banco "recuperado" que perdeu linhas em silêncio é pior que o danificado,
porque ninguém vai procurar o que sumiu.

A leitura é linha a linha de propósito: uma página ruim derruba o SELECT
inteiro, e lendo assim a perda fica limitada às linhas daquele trecho em vez
da tabela toda.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

GRAVAR = "--gravar" in sys.argv
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
if not ARGS:
    print(__doc__)
    sys.exit(1)

ORIGEM = Path(ARGS[0]).expanduser()
if not ORIGEM.is_file():
    print(f"ERRO: não achei o arquivo: {ORIGEM}")
    sys.exit(1)
DESTINO = ORIGEM.with_suffix(ORIGEM.suffix + ".recuperado")


def _abrir(caminho: Path) -> sqlite3.Connection:
    # `immutable=1` faz o SQLite ler sem tentar recuperar o journal nem
    # escrever nada — é o que permite ler um arquivo danificado sem piorá-lo.
    con = sqlite3.connect(f"file:{caminho}?immutable=1", uri=True)
    # Texto com byte inválido é comum em arquivo danificado, e o decodificador
    # padrão do sqlite3 levanta exceção nesse caso — perderíamos a linha
    # inteira por causa de um caractere. Lendo como bytes, a linha vem; o
    # `_texto()` faz a conversão de volta na gravação.
    con.text_factory = bytes
    return con


def _texto(valor):
    """De volta para texto, sem perder o que não for texto.

    Ler tudo como bytes salva linhas, mas gravar assim transformaria cada
    campo de texto num BLOB — o portal leria lixo onde espera uma string, e
    o banco pareceria recuperado enquanto todo dado textual estaria errado.
    Aqui o que for UTF-8 válido volta a ser texto; o que não for continua
    binário, porque provavelmente é mesmo (ou está corrompido, e inventar
    caractere seria pior que preservar o byte).
    """
    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8")
        except UnicodeDecodeError:
            return valor
    return valor


print(f"Origem : {ORIGEM}  ({ORIGEM.stat().st_size/1048576:.1f} MB)")
con = _abrir(ORIGEM)

try:
    integridade = con.execute("PRAGMA integrity_check").fetchone()[0]
    if isinstance(integridade, bytes):
        integridade = integridade.decode("utf-8", "replace")
except Exception as exc:  # noqa: BLE001
    integridade = f"não foi possível verificar: {exc}"
print(f"Integridade: {integridade}")

try:
    objetos = con.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
    ).fetchall()
except Exception as exc:  # noqa: BLE001
    print(f"\nERRO: nem o catálogo do banco se lê ({exc}).")
    print("Nada a recuperar por aqui — recorra ao backup mais recente.")
    sys.exit(1)

tabelas = [(n.decode() if isinstance(n, bytes) else n,
            s.decode(errors="replace") if isinstance(s, bytes) else s)
           for t, n, s in objetos
           if (t.decode() if isinstance(t, bytes) else t) == "table"]
outros = [(t.decode() if isinstance(t, bytes) else t,
           n.decode() if isinstance(n, bytes) else n,
           s.decode(errors="replace") if isinstance(s, bytes) else s)
          for t, n, s in objetos
          if (t.decode() if isinstance(t, bytes) else t) != "table"]

def _ler_tabela(con, nome: str):
    """Lê o máximo possível de uma tabela, em faixas de rowid.

    A leitura direta (`SELECT * FROM t`) morre na PRIMEIRA página ruim e
    leva junto tudo o que vinha depois: num teste com 2000 linhas e seis
    páginas danificadas, ela trouxe 151. Varrendo por faixa de rowid, uma
    página ruim custa só as linhas daquela faixa — o resto da tabela
    continua alcançável, porque cada faixa é uma consulta nova.

    Tabela WITHOUT ROWID não tem essa saída: nela vale a leitura direta, com
    o que der. É raro no portal, e é dito na contagem.
    """
    # Quantas linhas por faixa. Pequeno demais fica lento; grande demais
    # volta a perder muita coisa por página ruim.
    PASSO = 200

    def _direta():
        lidas, perdas, seguidas = [], 0, 0
        try:
            cur = con.execute(f'SELECT * FROM "{nome}"')
        except Exception:  # noqa: BLE001
            return [], 1
        while True:
            try:
                linha = cur.fetchone()
                seguidas = 0
            except Exception:  # noqa: BLE001 — página danificada
                perdas += 1
                seguidas += 1
                # Sem este limite, um cursor morto faz o laço girar para
                # sempre: ele levanta o mesmo erro a cada tentativa.
                if seguidas >= 3:
                    break
                continue
            if linha is None:
                break
            lidas.append(linha)
        return lidas, perdas

    try:
        maior = con.execute(f'SELECT MAX(rowid) FROM "{nome}"').fetchone()[0]
    except Exception:  # noqa: BLE001 — sem rowid, ou nem isso se lê
        return _direta()
    if not maior:
        return _direta()

    por_rowid, perdas = {}, 0
    inicio = 0
    while inicio <= int(maior):
        fim = inicio + PASSO
        try:
            cur = con.execute(
                f'SELECT rowid, * FROM "{nome}" WHERE rowid > ? AND rowid <= ?',
                (inicio, fim))
            for linha in cur.fetchall():
                por_rowid[linha[0]] = linha[1:]
        except Exception:  # noqa: BLE001 — a faixa inteira se perde, não a tabela
            perdas += 1
        inicio = fim
    # Uma passada direta por cima: em tabela pouco danificada ela é mais
    # rápida e pode trazer linha que a faixa perdeu. Sem duplicar nada.
    diretas, _ = _direta()
    if len(diretas) > len(por_rowid):
        return diretas, perdas
    return [por_rowid[k] for k in sorted(por_rowid)], perdas


print(f"\n{'tabela':32} {'lidas':>9} {'perdidas':>9}")
plano: dict[str, list] = {}
perdas_totais = 0
for nome, _sql in tabelas:
    if nome.startswith("sqlite_"):
        continue
    linhas, perdidas = _ler_tabela(con, nome)
    plano[nome] = linhas
    perdas_totais += perdidas
    print(f"{nome:32} {len(linhas):>9} {perdidas:>9}")

if not GRAVAR:
    print("\nSimulação. Nada foi gravado. Para reconstruir:")
    print(f"    python3 {Path(__file__).name} {ORIGEM} --gravar")
    sys.exit(0)

if DESTINO.exists():
    print(f"\nERRO: {DESTINO} já existe. Tire-o da frente antes.")
    sys.exit(1)

print(f"\nGravando {DESTINO} …")
novo = sqlite3.connect(DESTINO)
for nome, sql in tabelas:
    if nome.startswith("sqlite_"):
        continue
    try:
        novo.execute(sql)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! não criei a tabela {nome}: {exc}")
        continue
    linhas = plano.get(nome) or []
    if not linhas:
        continue
    marcas = ",".join("?" * len(linhas[0]))
    gravadas = 0
    for linha in linhas:
        try:
            novo.execute(f'INSERT INTO "{nome}" VALUES ({marcas})',
                         tuple(_texto(v) for v in linha))
            gravadas += 1
        except Exception:  # noqa: BLE001
            perdas_totais += 1
    novo.commit()
    print(f"  {nome:32} {gravadas:>9} gravada(s)")

# Índices, views e gatilhos por último: dependem das tabelas existirem.
for tipo, nome, sql in outros:
    try:
        novo.execute(sql)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! {tipo} {nome}: {exc}")
novo.commit()

conferido = novo.execute("PRAGMA integrity_check").fetchone()[0]
novo.close()
print(f"\nIntegridade do arquivo novo: {conferido}")
if perdas_totais:
    print(f"ATENÇÃO: {perdas_totais} linha(s) ou objeto(s) NÃO vieram. "
          "Compare as contagens com o servidor de origem antes de trocar.")
print("\nPara adotar o arquivo novo, com o portal PARADO:")
print(f"    cp -p {ORIGEM} {ORIGEM}.danificado")
print(f"    mv {DESTINO} {ORIGEM}")
