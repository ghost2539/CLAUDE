#!/usr/bin/env python3
"""Diagnóstico passo a passo do RPA na tela real, sem heredoc no terminal.

Uso:  python3 scripts/ebs_forms_diagnostico.py [ativo]

Percorre o caminho completo (abrir, consultar, Atribuições, fechar, Linhas de
Origem, reabrir busca) reportando CADA passo com o que o Forms respondeu.
Também lista os itens dos menus Arquivo e Verificar, que é o que permite
acertar os nomes de "Fechar Janela" e "Localizar" desta instalação.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DADOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ebs_forms")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DADOS}/portal_isolado.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "diagnostico")
os.environ.setdefault("INITIAL_ADMIN_LOGIN", "diagnostico")
os.environ.setdefault("COFRE_CORPORATIVO", "nao")

import integracoes.ebs_forms as f  # noqa: E402  (depois das variáveis)

ATIVO = sys.argv[1] if len(sys.argv) > 1 else "11837250"
_t0 = time.time()


def log(msg: str) -> None:
    print(f"[{time.time() - _t0:6.1f}s] {msg}", flush=True)


def passo(titulo: str, fn) -> object:
    log(f"── {titulo}")
    try:
        r = fn()
        log(f"   ok: {str(r)[:300]}")
        return r
    except Exception as exc:  # noqa: BLE001 — diagnóstico mostra tudo
        log(f"   FALHOU: {exc}")
        return None


def main() -> int:
    capturas: list[str] = []
    usuario, _ = f.credenciais()
    log(f"usuário do robô: {usuario}")
    if not f.compilado():
        log("compilando o lançador…")
        f.compilar()
    log(f"tela virtual: {f.Xvfb.garantir()}")

    sessao = f.Sessao(*f.credenciais(), log)
    passo("login SSO", sessao.entrar)
    jnlp = sessao.obter_jnlp()
    log(f"jnlp obtido ({len(jnlp)} bytes)")
    cliente = f.Cliente(jnlp, log)
    passo("abrir o Forms", cliente.iniciar)
    v = f.variaveis_da_tela()

    try:
        passo("esperar a tela Localizar Ativos",
              lambda: cliente.ordem("esperarate", "janela:Localizar Ativos 90000", timeout=120))

        for menu in ("Arquivo", "Verificar", "Janela"):
            passo(f"itens do menu {menu}",
                  lambda m=menu: __import__("base64").b64decode(
                      cliente.ordem("itensmenu", m, timeout=60)).decode("utf-8", "replace"))

        passo("preencher o critério", lambda: (cliente.focar_campo(v["campo_criterio"]),
                                               cliente.texto(ATIVO),
                                               cliente.ler_campo(v["campo_criterio"]))[-1])
        passo("preencher o livro", lambda: (cliente.focar_campo(v["campo_livro"]),
                                            cliente.texto("FA_RENNER"),
                                            cliente.ler_campo(v["campo_livro"]))[-1])
        passo("clicar em Localizar", lambda: cliente.clicar(v["botao_localizar"]))
        passo("esperar a grade", lambda: cliente.ordem("esperarate", "grade 20000", timeout=40))
        passo("ler a grade de Ativos", lambda: json.dumps(cliente.dados(), ensure_ascii=False)[:400])

        passo("focar a grade", lambda: cliente.focar_campo(v["campo_grade"]))
        passo("clicar em Atribuições", lambda: cliente.clicar(v["botao_atribuicoes"]))
        time.sleep(1)
        passo("aviso na tela?", lambda: json.dumps(cliente.dialogo(), ensure_ascii=False))
        passo("confirmar OK (se houver)", lambda: cliente.clicar_texto("OK"))
        passo(f"esperar Atribuições - {ATIVO}",
              lambda: cliente.ordem("esperarate", f"janela:Atribuições - {ATIVO} 20000", timeout=40))
        passo("ler Atribuições", lambda: json.dumps(cliente.dados(), ensure_ascii=False)[:600])
        capturas.append(cliente.foto("diag_atribuicoes"))

        passo("FECHAR a janela Atribuições", lambda: cliente.ordem("fecharjanela", "Atribuições", timeout=60))
        passo("janelas abertas agora", lambda: cliente.janelas())
        passo("quadros abertos agora",
              lambda: [q.get("titulo") for q in (cliente.dados() or {}).get("quadros", [])])
        capturas.append(cliente.foto("diag_apos_fechar"))

        passo("focar a grade de novo", lambda: cliente.focar_campo(v["campo_grade"]))
        passo("clicar em Linhas de Origem", lambda: cliente.clicar(v["botao_linhas_origem"]))
        time.sleep(1)
        passo("confirmar OK (se houver)", lambda: cliente.clicar_texto("OK"))
        passo("esperar Linhas de Origem",
              lambda: cliente.ordem("esperarate", "janela:Linhas de Origem 20000", timeout=40))
        origem = passo("ler Linhas de Origem", lambda: cliente.dados())
        capturas.append(cliente.foto("diag_linhas_origem"))
        if isinstance(origem, dict):
            for q in origem.get("quadros", []):
                if "origem" in (q.get("titulo") or "").lower():
                    log(f"   CAMPOS DE LINHAS DE ORIGEM: {json.dumps(q.get('campos'), ensure_ascii=False)}")

        passo("fechar Linhas de Origem", lambda: cliente.ordem("fecharjanela", "Linhas de Origem", timeout=30))
        passo("reabrir a busca pelo menu",
              lambda: cliente.ordem("menu", v["menu_localizar"], timeout=60))
        passo("a busca voltou?",
              lambda: cliente.ordem("esperarate", "janela:Localizar Ativos 20000", timeout=40))
        capturas.append(cliente.foto("diag_busca_reaberta"))
    finally:
        log(f"capturas: {capturas}")
        cliente.encerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
