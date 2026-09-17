#!/usr/bin/env python3
"""Verificação da credencial dos Correios: sai do cofre, não do código.

    python3 scripts/verificar_correios_credencial.py

Roda contra um cofre temporário, sem tocar em nada do servidor e sem rede.
O que está em jogo: a credencial dos Correios era a única integração que
ainda lia `os.environ` direto e estourava `KeyError` quando faltava. Aqui
ela passa pelo caminho único do projeto (`core.cofre`): cofre corporativo,
cofre local cifrado e, por último, variável de ambiente.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP)

from fastapi import HTTPException  # noqa: E402

from core import cofre  # noqa: E402
import routers.correios as correios  # noqa: E402

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


def _limpar_ambiente() -> None:
    for nome in ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES",
                 "CORREIOS_DR", "CORREIOS_CONTRATO"):
        os.environ.pop(nome, None)


def _creds():
    """`_correios_creds` sem deixar o script morrer: a versão antiga estourava
    `KeyError` quando a credencial não estava no ambiente."""
    try:
        return correios._correios_creds()
    except KeyError as e:
        checar(False, f"_correios_creds estourou KeyError ({e})")
        return ("", "", [], "", "")


def _limpar_cofre() -> None:
    for nome in ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES",
                 "CORREIOS_DR", "CORREIOS_CONTRATO"):
        cofre.remover(nome)


print("[1] Sem credencial em lugar nenhum: recusa explicada, não KeyError")
_limpar_ambiente()
_limpar_cofre()
try:
    correios._check_credenciais()
    checar(False, "deveria recusar quando não há credencial")
    checar(False, "(mensagem não avaliada)")
except KeyError:
    checar(False, "ainda estoura KeyError em vez de responder 500")
    checar(False, "(mensagem não avaliada)")
except HTTPException as e:
    checar(e.status_code == 500, f"responde 500, não uma exceção crua ({e.status_code})")
    checar("cofre" in e.detail.lower(),
           f"a mensagem manda o operador para o cofre ({e.detail[:48]}…)")

print("\n[2] Credencial só no cofre, sem variável de ambiente nenhuma")
cofre.definir("CORREIOS_USUARIO", "usuario-de-teste")
cofre.definir("CORREIOS_CHAVE", "chave-de-teste")
cofre.definir("CORREIOS_CARTOES", " 111 , 222 ,")
usuario, chave, cartoes, dr, contrato = _creds()
checar(usuario == "usuario-de-teste", f"usuário vem do cofre ({usuario!r})")
checar(chave == "chave-de-teste", "chave vem do cofre")
checar(cartoes == ["111", "222"],
       f"cartões sem espaço em volta e sem entrada vazia ({cartoes})")
checar(dr == "64", f"DR mantém o padrão quando não é informado ({dr!r})")
checar(contrato == "", "contrato vazio quando não é informado")
try:
    correios._check_credenciais()
    checar(True, "com credencial no cofre, não recusa")
except HTTPException:
    checar(False, "com credencial no cofre, não deveria recusar")

print("\n[3] Trocar no cofre vale na chamada seguinte, sem reiniciar o serviço")
cofre.definir("CORREIOS_CHAVE", "chave-nova")
checar(_creds()[1] == "chave-nova",
       "nada fica guardado em variável de módulo")

print("\n[4] Servidor em transição: sem cofre, o ambiente ainda vale")
_limpar_cofre()
os.environ["CORREIOS_USUARIO"] = "do-ambiente"
os.environ["CORREIOS_CHAVE"] = "chave-ambiente"
os.environ["CORREIOS_CARTOES"] = "999"
usuario, chave, cartoes, *_ = _creds()
checar(usuario == "do-ambiente", f"usuário lido do ambiente ({usuario!r})")
checar(cartoes == ["999"], f"cartões lidos do ambiente ({cartoes})")

print("\n[5] O cofre tem prioridade sobre o ambiente")
cofre.definir("CORREIOS_USUARIO", "do-cofre")
checar(_creds()[0] == "do-cofre",
       "com os dois preenchidos, vence o cofre")

print("\n[6] Nenhuma credencial escrita no código")
fonte = (RAIZ / "routers" / "correios.py").read_text(encoding="utf-8")
checar("os.environ['CORREIOS_" not in fonte and 'os.environ["CORREIOS_' not in fonte,
       "o router não lê CORREIOS_* direto de os.environ")
checar("from core.cofre import obter" in fonte,
       "o router passa pelo caminho único do projeto (core.cofre)")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Credencial dos Correios com problema:")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("Credencial dos Correios íntegra.")
