#!/usr/bin/env python3
"""As credenciais chegaram ao processo? Diz sim ou não, nunca o valor.

    venv/bin/python scripts/conferir_credenciais.py

Rode NO SERVIDOR, com o mesmo ambiente do serviço:

    set -a; . /run/portal-spare.env; set +a
    venv/bin/python scripts/conferir_credenciais.py

Ou, para ver exatamente o que o serviço enxerga:

    sudo systemctl show portal_spare -p Environment
    sudo tr '\\0' '\\n' < /proc/$(pgrep -f 'portal-spare|main.py' | head -1)/environ \\
        | grep -E '^(CORREIOS_|EBS_|ORACLE_EBS_)' | sed 's/=.*/=<definida>/'

Por que existe
--------------
As chaves dos Correios e do Oracle EBS são injetadas no ambiente do processo
antes do portal subir:

    EnvironmentFile=-/run/portal-spare.env

Quando uma tela reclama de credencial, a primeira pergunta é se a variável
chegou ao PROCESSO — e essa pergunta se responde sem abrir o valor de nada.
Este script mostra só o NOME, se está definida, e o tamanho. Valor nunca.
"""
from __future__ import annotations

import os
import sys

GRUPOS = {
    "Correios": (
        ("CORREIOS_USUARIO", True),
        ("CORREIOS_CHAVE", True),
        ("CORREIOS_CARTOES", True),
        ("CORREIOS_DR", False),
        ("CORREIOS_CONTRATO", False),
    ),
    "Oracle EBS": (
        ("ORACLE_EBS_USER", True),
        ("ORACLE_EBS_PASS", True),
        ("ORACLE_EBS_DSN", True),
    ),
    "ServiceNow (leitura dos Indicadores)": (
        ("SN_API_USER", False),
        ("SN_API_PASS", False),
    ),
}

faltando_obrigatorias: list[str] = []

for grupo, chaves in GRUPOS.items():
    print(f"\n{grupo}")
    for nome, obrigatoria in chaves:
        valor = os.environ.get(nome)
        marca = "*" if obrigatoria else " "
        if valor:
            print(f"  {marca} {nome:24} definida ({len(valor)} caractere(s))")
        else:
            print(f"  {marca} {nome:24} AUSENTE")
            if obrigatoria:
                faltando_obrigatorias.append(nome)

print("\n(* = obrigatória para a integração funcionar. Valor nunca é impresso.)")

# CORREIOS_CARTOES é lista separada por vírgula: vazio depois do split é
# tão ruim quanto ausente, e passa despercebido.
cartoes = os.environ.get("CORREIOS_CARTOES", "")
if cartoes:
    itens = [c.strip() for c in cartoes.split(",") if c.strip()]
    print(f"\nCORREIOS_CARTOES tem {len(itens)} cartão(ões) após separar por vírgula.")
    if not itens:
        print("  ATENÇÃO: está definida mas não sobrou nenhum cartão — só vírgulas/espaços.")
        faltando_obrigatorias.append("CORREIOS_CARTOES (vazia após o split)")

if faltando_obrigatorias:
    print("\nFALTA no ambiente deste processo:")
    for nome in faltando_obrigatorias:
        print("  -", nome)
    print("\nConfira /run/portal-spare.env e reinicie o serviço:")
    print("    sudo systemctl restart portal_spare")
    sys.exit(1)

print("\nTodas as credenciais obrigatórias estão no ambiente deste processo.")
