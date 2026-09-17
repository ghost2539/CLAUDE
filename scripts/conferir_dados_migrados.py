#!/usr/bin/env python3
"""Confere se os dados trazidos do servidor antigo são LIDOS pelo código novo.

    python3 scripts/conferir_dados_migrados.py            # confere e resume
    python3 scripts/conferir_dados_migrados.py --contagem # com contagem de linhas

Rode no servidor NOVO, depois de restaurar e antes de liberar para o time.
Só lê: nenhum CREATE, nenhum ALTER, nenhum INSERT.

Copiar o arquivo do banco é a parte fácil, e é a que dá a falsa sensação de
que deu certo: o arquivo está lá, com o tamanho certo, e a tela abre vazia.
Isso acontece quando o código novo procura uma TABELA com outro nome. Foi o
que aconteceu com o Orçamento Spare: os dois servidores têm um
`orcamento_spare.db`, mas são módulos diferentes — um grava em
`spare_projeto`, o outro em `orc_spare_projeto`. Copiar o arquivo não
resolve, e não copiar também não; é decisão de produto, não de arquivo.

Por isso a conferência é por TABELA, e o que ela procura é o desencontro:

- tabela que o código espera e o arquivo não tem → a tela abre vazia (ou é
  criada vazia na subida, que dá no mesmo para quem olha a tela);
- tabela que o arquivo tem com LINHAS e o código não conhece → dado que
  veio na bagagem e ninguém mais alcança. É o caso grave, e é o único que
  faz este script sair com erro: o resto pode ser normal (módulo novo, que
  nasce vazio mesmo).
"""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

COM_CONTAGEM = "--contagem" in sys.argv

import config as _config_mod  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

_cfg = _config_mod.get_settings()

# Qual módulo de `db/` responde por qual chave de configuração. O nome do
# arquivo .db não serve como chave: `orcamento_exec.py` guarda em
# `controle_orcamento_exec.db`, e é justamente esse tipo de descasamento
# que esta conferência existe para mostrar.
def _modulos_de_banco() -> dict[str, str]:
    """{nome do módulo em db/: atributo de URL em config}."""
    achados = {}
    for arq in sorted((RAIZ / "db").glob("*.py")):
        nome = arq.stem
        if nome.startswith("_"):
            continue
        if nome == "portal":
            achados[nome] = "DATABASE_URL"
            continue
        for atributo in (f"{nome.upper()}_DATABASE_URL",
                         f"{nome.upper().replace('ORCAMENTO_EXEC', 'ORCAMENTO_EXEC')}_DATABASE_URL"):
            if hasattr(_cfg, atributo):
                achados[nome] = atributo
                break
    return achados


def _tabelas_do_codigo(nome: str) -> set[str]:
    """As tabelas que os modelos daquele módulo declaram."""
    try:
        mod = __import__(f"db.{nome}", fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ! db/{nome}.py não importa: {type(exc).__name__}: {exc}")
        return set()
    base = getattr(mod, "Base", None)
    return set(base.metadata.tables) if base is not None else set()


def _resumo_url(url: str) -> str:
    """A URL sem a senha. Endereço de banco é dado de acesso."""
    if "://" not in url:
        return url
    esquema, resto = url.split("://", 1)
    if "@" in resto:
        resto = "…@" + resto.split("@", 1)[1]
    return f"{esquema}://{resto}"


problemas: list[str] = []
avisos: list[str] = []
conferidos = 0

print("Conferência dos dados migrados — só leitura\n")

for modulo, atributo in sorted(_modulos_de_banco().items()):
    url = getattr(_cfg, atributo, "") or ""
    if not url:
        avisos.append(f"{modulo}: {atributo} está vazio")
        continue
    esperadas = _tabelas_do_codigo(modulo)
    if not esperadas:
        continue

    try:
        engine = create_engine(url)
        with engine.connect() as cx:
            existentes = set(inspect(cx).get_table_names())
            contagens = {}
            if COM_CONTAGEM:
                for t in sorted(existentes):
                    try:
                        contagens[t] = cx.execute(
                            text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
                    except Exception:  # noqa: BLE001
                        contagens[t] = -1
    except Exception as exc:  # noqa: BLE001
        avisos.append(f"{modulo}: não consegui abrir {_resumo_url(url)} "
                      f"({type(exc).__name__})")
        continue

    conferidos += 1
    faltando = sorted(esperadas - existentes)
    # Tabela do arquivo que o código não conhece. Só interessa se tiver
    # linha: tabela órfã vazia é lixo de versão antiga, não perda de dado.
    orfas = sorted(existentes - esperadas)
    orfas_com_dado = []
    if orfas:
        try:
            with create_engine(url).connect() as cx:
                for t in orfas:
                    try:
                        n = cx.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
                    except Exception:  # noqa: BLE001
                        n = 0
                    if n > 0:
                        orfas_com_dado.append((t, n))
        except Exception:  # noqa: BLE001
            pass

    marca = "!!" if orfas_com_dado else ("? " if faltando else "ok")
    print(f"[{marca}] {modulo:24} {_resumo_url(url)}")
    if COM_CONTAGEM:
        for t in sorted(esperadas & existentes):
            print(f"        {t:32} {contagens.get(t, '?'):>8} linha(s)")
    if faltando:
        print(f"        tabelas que o código espera e não existem: {', '.join(faltando)}")
        print("        (serão criadas vazias na subida — a tela abre sem dado)")
    for t, n in orfas_com_dado:
        print(f"        *** {t}: {n} linha(s) que NENHUMA tela lê ***")
        problemas.append(f"{modulo}.{t}: {n} linha(s) órfãs")
    if orfas and not orfas_com_dado:
        print(f"        tabelas sem uso, vazias: {', '.join(orfas)}")

print(f"\n{conferidos} banco(s) conferido(s).")
for a in avisos:
    print("  aviso:", a)

if problemas:
    print("\nDADO QUE VEIO E NINGUÉM LÊ:")
    for p in problemas:
        print("  -", p)
    print("\nIsso não se resolve copiando de novo: o código novo procura outra\n"
          "tabela. Ou o dado é convertido para o modelo novo, ou a tela do\n"
          "servidor antigo é a que deve continuar valendo. É decisão de quem\n"
          "usa o módulo, não do script.")
    sys.exit(1)

print("\nTudo que veio é lido por alguma tela.")
