#!/usr/bin/env python3
"""Verificação da credencial dos Correios: vem do AMBIENTE do serviço.

    python3 scripts/verificar_correios_credencial.py

Roda isolado, sem tocar em nada do servidor e sem rede.

A REGRA
As credenciais dos Correios (e as do Oracle EBS) são injetadas no ambiente
do processo pelo próprio serviço, antes do portal subir:

        EnvironmentFile=-/run/portal-spare.env

Então o portal lê `os.environ` e ponto. `integracoes/ebs_oracle.py` faz igual.

POR QUE ISTO É CONFERIDO
Uma tentativa de "padronizar" pôs `core.cofre.obter` na frente dessa leitura.
Além de indireção desnecessária, `obter` chama `_fernet()`, que CRIA o
diretório do cofre e grava um arquivo de chave quando não existe — I/O em
disco a cada leitura de uma variável que já está na memória do processo.
Esta verificação trava a decisão: a credencial sai do ambiente, e o cofre
não entra nesse caminho.
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

from fastapi import HTTPException  # noqa: E402

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


print("[1] Sem credencial no ambiente: recusa explicada, não KeyError")
_limpar_ambiente()
try:
    correios._check_credenciais()
    checar(False, "deveria recusar quando não há credencial")
    checar(False, "(mensagem não avaliada)")
except KeyError:
    checar(False, "ainda estoura KeyError em vez de responder 500")
    checar(False, "(mensagem não avaliada)")
except HTTPException as e:
    checar(e.status_code == 500, f"responde 500, não uma exceção crua ({e.status_code})")
    checar("ambiente do serviço" in e.detail,
           f"a mensagem manda olhar o ambiente do serviço ({e.detail[:46]}…)")

print("\n[2] Credencial lida do ambiente do processo")
os.environ["CORREIOS_USUARIO"] = "usuario-de-teste"
os.environ["CORREIOS_CHAVE"] = "chave-de-teste"
os.environ["CORREIOS_CARTOES"] = " 111 , 222 ,"
usuario, chave, cartoes, dr, contrato = _creds()
checar(usuario == "usuario-de-teste", f"usuário vem do ambiente ({usuario!r})")
checar(chave == "chave-de-teste", "chave vem do ambiente")
checar(cartoes == ["111", "222"],
       f"cartões sem espaço em volta e sem entrada vazia ({cartoes})")
checar(dr == "64", f"DR mantém o padrão quando não é informado ({dr!r})")
checar(contrato == "", "contrato vazio quando não é informado")
try:
    correios._check_credenciais()
    checar(True, "com credencial no ambiente, não recusa")
except HTTPException:
    checar(False, "com credencial no ambiente, não deveria recusar")

print("\n[3] Lê no momento do uso, sem guardar em variável de módulo")
os.environ["CORREIOS_CHAVE"] = "chave-nova"
checar(_creds()[1] == "chave-nova", "trocar a variável vale na chamada seguinte")

print("\n[4] O cofre NÃO entra neste caminho")
# `core.cofre.obter` cria diretório e grava arquivo de chave quando precisa
# montar o Fernet. Isso não pode acontecer ao ler uma variável de ambiente.
import core.cofre as _cofre  # noqa: E402

_chamou = {"obter": 0}
_obter_real = _cofre.obter


def _espiao(nome, default=""):
    _chamou["obter"] += 1
    return _obter_real(nome, default)


_cofre.obter = _espiao
try:
    _creds()
finally:
    _cofre.obter = _obter_real
checar(_chamou["obter"] == 0,
       f"ler a credencial não chama o cofre ({_chamou['obter']} chamada(s))")

fonte = (RAIZ / "routers" / "correios.py").read_text(encoding="utf-8")
import ast as _ast  # noqa: E402

_chamadas_cofre = []
for _no in _ast.walk(_ast.parse(fonte)):
    if isinstance(_no, _ast.ImportFrom) and (_no.module or "").startswith("core.cofre"):
        _chamadas_cofre.append(f"linha {_no.lineno}")
checar(not _chamadas_cofre,
       f"o router não importa core.cofre ({_chamadas_cofre or 'nenhum'})")

print("\n[5] O EBS Oracle segue a mesma regra")
ebs = (RAIZ / "integracoes" / "ebs_oracle.py").read_text(encoding="utf-8")
checar("os.environ.get(nome)" in ebs,
       "integracoes/ebs_oracle.py também lê o ambiente primeiro")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("Credencial dos Correios com problema:")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("Credencial dos Correios íntegra: sai do ambiente do serviço.")
