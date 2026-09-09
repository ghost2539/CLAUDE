#!/usr/bin/env python3
"""Testa as credenciais dos Correios de ponta a ponta, sem subir o portal.

    venv/bin/python scripts/testar_correios.py
    venv/bin/python scripts/testar_correios.py AA123456789BR   # rastreia um objeto

Mostra de onde cada credencial veio (cofre corporativo, cofre local ou
ambiente), autentica na API e, se você passar um código, faz um rastreio de
verdade. Nenhum valor de segredo é exibido — só o tamanho e a origem.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def marca(ok: bool) -> str:
    return "OK  " if ok else "FALHA"


def main() -> int:
    from core import cofre

    print("=" * 70)
    print("1. Cofre")
    disp, motivo = cofre.diagnostico_corporativo()
    print(f"   corporativo: {'disponível' if disp else 'INDISPONÍVEL'} — {motivo}")

    print("\n2. Credenciais (valores nunca são exibidos)")
    faltando = []
    for chave in ("CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES"):
        valor = cofre.obter(chave)
        origem = cofre.fonte(chave)
        if valor:
            print(f"   [{marca(True)}] {chave:20s} {len(valor):>3} caracteres · {origem}")
        else:
            print(f"   [{marca(False)}] {chave:20s} não encontrada")
            faltando.append(chave)

    if faltando:
        print("\n   Sem essas chaves não dá para autenticar. Diagnóstico:")
        print("     python3 scripts/cofre.py acesso")
        return 1

    import routers.correios as co
    usuario, sen, cartoes, dr, contrato = co._correios_creds()
    print(f"\n   usuário : {usuario}")
    print(f"   cartões : {cartoes if cartoes else '(nenhum)'}")
    print(f"   DR      : {dr}   contrato: {contrato or '(nenhum)'}")
    print(f"   proxy   : {co.SN_PROXY or '(nenhum — saída direta)'}")

    print("\n3. Autenticação na API dos Correios")
    try:
        token = co._correios_authenticate()
        print(f"   [{marca(True)}] token obtido ({len(token)} caracteres)")
    except Exception as exc:  # noqa: BLE001
        detalhe = getattr(exc, "detail", None) or str(exc)
        print(f"   [{marca(False)}] {detalhe}")
        print("\n   Se falhou no gateway (GTW-012), o token básico não foi elevado:")
        print("   confira se CORREIOS_CARTOES tem o cartão de postagem certo.")
        return 1

    codigo = sys.argv[1] if len(sys.argv) > 1 else ""
    if not codigo:
        print("\n" + "=" * 70)
        print("Credenciais OK e API respondendo.")
        print("Para testar um rastreio de verdade, passe um código:")
        print("  venv/bin/python scripts/testar_correios.py AA123456789BR")
        return 0

    print(f"\n4. Rastreio de {codigo}")
    try:
        r = co._correios_request(
            "GET",
            f"{co.CORREIOS_BASE}/srorastro/v1/objetos/{codigo}?resultado=T",
            headers={"Authorization": f"Bearer {token}"},
        )
        print(f"   HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"   [{marca(False)}] {r.text[:300]}")
            return 1
        objetos = (r.json() or {}).get("objetos", [])
        if not objetos:
            print(f"   [{marca(False)}] resposta sem objetos")
            return 1
        obj = objetos[0]
        if obj.get("mensagem"):
            print(f"   [{marca(False)}] {obj['mensagem']}")
            return 1
        eventos = obj.get("eventos", []) or []
        print(f"   [{marca(True)}] {len(eventos)} evento(s)")
        for ev in eventos[:3]:
            print(f"      {ev.get('dtHrCriado','')}  {ev.get('descricao','')}")
    except Exception as exc:  # noqa: BLE001
        print(f"   [{marca(False)}] {getattr(exc, 'detail', None) or exc}")
        return 1

    print("\n" + "=" * 70)
    print("Rastreio funcionando de ponta a ponta.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
