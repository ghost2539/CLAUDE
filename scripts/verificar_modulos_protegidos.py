#!/usr/bin/env python3
"""Verificação da entrega do JavaScript dos módulos.

    python3 scripts/verificar_modulos_protegidos.py

O que se prova é quem recebe o quê. O defeito que motivou isto: os módulos
moravam em `static/modules/` e saíam pelo mount público, então
`GET /static/modules/parametros.js` devolvia a tela de administração inteira
para quem não tinha sessão nenhuma.

Duas armadilhas que esta verificação existe para pegar:

1. A rota ficar MAIS restritiva que o menu. Aí o item aparece, a pessoa
   clica, e a tela morre em "Falha ao carregar módulo" — defeito pior que o
   original, porque parece intermitente e ninguém liga uma coisa à outra.
2. Alguém remontar `modulos/` como estático, por engano ou por conveniência,
   e reabrir o buraco em silêncio.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config as _config_mod  # noqa: E402
import main  # noqa: E402

_cfg = _config_mod.get_settings()


def cliente(**sessao):
    c = TestClient(main.app)
    if sessao:
        _, ck = security.create_session(sessao)
        c.cookies.set("spare_session", ck)
    return c


anon = cliente()
admin = cliente(username="adm", is_admin=True, permission_map={})
pelado = cliente(username="op", is_admin=False, permission_map={})

PORTAL = sorted(p.stem for p in (RAIZ / "modulos").glob("*.js"))
TIMES = sorted(p.stem for p in (RAIZ / "modulos-times").glob("*.js"))


print("\n[1] Sem sessão não sai nada")
checar(bool(PORTAL) and bool(TIMES), "há módulos para verificar")
ruins = [n for n in PORTAL if anon.get(f"/modulos/{n}.js").status_code != 401]
checar(not ruins, f"os {len(PORTAL)} módulos do portal exigem sessão (sobraram: {ruins})")
ruins = [n for n in TIMES if anon.get(f"/modulos-times/{n}.js").status_code != 401]
checar(not ruins, f"os {len(TIMES)} módulos do espaço Times exigem sessão (sobraram: {ruins})")


print("\n[2] O caminho antigo não existe mais")
# Enquanto o arquivo estiver sob `static/`, qualquer mount o publica de novo.
for caminho in ("/static/modules/parametros.js", "/static/modules-times/consulta.js",
                "/static/modules/torre.js"):
    checar(anon.get(caminho).status_code == 404, f"{caminho} → 404")
checar(not (RAIZ / "static" / "modules").exists(),
       "a pasta static/modules não voltou a existir")
checar(not (RAIZ / "static" / "modules-times").exists(),
       "nem static/modules-times")
principal = (RAIZ / "main.py").read_text(encoding="utf-8")
checar('directory="modulos"' not in principal and "StaticFiles(directory=RAIZ" not in principal,
       "main.py não monta os módulos como estático")


print("\n[3] Permissão: cada um recebe o que pode abrir")
# Sem permissão nenhuma: só o que é de todos.
checar(pelado.get("/modulos/parametros.js").status_code == 200,
       "parametros.js vai para qualquer pessoa logada — a aba Minha conta é de todos")
checar(pelado.get("/modulos/parametros_admin.js").status_code == 403,
       "mas as telas de administração, não")
checar(pelado.get("/modulos/torre.js").status_code == 403,
       "módulo com permissão própria é negado a quem não a tem")
com_torre = cliente(username="t", is_admin=False,
                    permission_map={"torre": {"can_view": True}})
checar(com_torre.get("/modulos/torre.js").status_code == 200,
       "e entregue a quem tem")
checar(com_torre.get("/modulos/venda.js").status_code == 403,
       "ter uma permissão não abre as outras")
checar(admin.get("/modulos/parametros_admin.js").status_code == 200,
       "o admin recebe as telas de administração")
# A tela abre essas abas só com `u.is_admin`. Se a rota aceitasse a
# permissão granular, quem tem "parametros" sem ser admin não veria as abas
# mas baixaria o arquivo pela URL — o vazamento voltava por uma fresta.
for chave in ("can_view", "can_admin"):
    c = cliente(username="p", is_admin=False,
                permission_map={"parametros": {chave: True}})
    checar(c.get("/modulos/parametros_admin.js").status_code == 403,
           f"parametros.{chave} sem ser admin NÃO baixa as telas de administração")
    checar(c.get("/modulos/parametros.js").status_code == 200,
           f"mas parametros.{chave} continua com a Minha conta")
ruins = [n for n in PORTAL if admin.get(f"/modulos/{n}.js").status_code != 200]
checar(not ruins, f"e todos os módulos do portal (sobraram: {ruins})")


print("\n[4] O menu e a rota concordam")
# Se o item aparece para alguém, o arquivo tem de chegar. A chave vem do
# `data-perm`, que nem sempre é o nome da rota: Gestão de Ativos usa
# "servicenow" para ninguém perder acesso quando a tela muda de lugar.
html = (RAIZ / "static" / "index.html").read_text(encoding="utf-8")
itens = re.findall(r'data-route="([^"]+)"[^>]*?(?:data-perm="([^"]*)")?>', html)
conferidos = 0
for rota, perm in itens:
    modulo = rota.split("/")[0]
    if modulo not in PORTAL:
        continue
    chave = perm or modulo
    c = cliente(username="x", is_admin=False, permission_map={chave: {"can_view": True}})
    r = c.get(f"/modulos/{modulo}.js")
    if r.status_code != 200:
        checar(False, f"o menu mostra '{rota}' com a permissão '{chave}', "
                      f"mas a rota devolveu {r.status_code}")
    conferidos += 1
checar(conferidos > 0, f"itens de menu conferidos: {conferidos}")


print("\n[5] Nome de módulo não é caminho")
for nome in ("../config", "..%2fconfig", "../../etc/passwd", "Config", "config",
             "main", "", ".", "a/b", "servicenow.js"):
    r = admin.get(f"/modulos/{nome}.js")
    checar(r.status_code == 404,
           f"{nome!r} recusado ({r.status_code})")
# A defesa não pode depender só da expressão regular: prova que nenhum
# arquivo de fora do diretório é alcançado, mesmo com nome que a regex passa.
checar(admin.get("/modulos/config.js").status_code == 404,
       "nome válido que não é módulo não vira leitura de outro arquivo")


print("\n[6] O que sai não tem comentário nem cache trocado")
r = admin.get("/modulos/torre.js")
checar(r.status_code == 200 and r.content, "o módulo vem com conteúdo")
checar("javascript" in (r.headers.get("content-type") or ""),
       "e com o tipo certo, senão o navegador recusa executar")
checar(not any(l.strip().startswith("//") for l in r.text.splitlines()),
       "sem linha de comentário")
checar("/*" not in r.text or "/*!" in r.text,
       "sem bloco de comentário (licença de terceiro pode ficar)")
etag = r.headers.get("etag")
checar(bool(etag), "tem ETag")
checar(admin.get("/modulos/torre.js", headers={"If-None-Match": etag}).status_code == 304,
       "e responde 304 quando o navegador já tem essa versão")
checar("cookie" in (r.headers.get("vary") or "").lower(),
       "Vary: Cookie — o módulo é de quem pediu, e cache de proxy não pode "
       "guardar a resposta de um e servir a outro")


print("\n[7] O carregador da tela aponta para a rota, não para /static")
app_js = (RAIZ / "js" / "app.js").read_text(encoding="utf-8")
checar("'/modulos/'" in app_js and "'/modulos-times/'" in app_js,
       "app.js carrega pelo caminho novo")
checar("/static/modules" not in app_js, "e não sobrou o caminho antigo")
for arq in ("modulos/gestao_ativos.js", "modulos-times/gestao_ativos.js"):
    texto = (RAIZ / arq).read_text(encoding="utf-8")
    checar("/static/modules" not in texto, f"{arq} também não")
    checar("app-base" in texto,
           f"{arq} leva o prefixo do proxy — caminho absoluto some atrás de /portal-spare")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Entrega dos módulos íntegra: sessão sempre, permissão quando há, "
      "e nada do que é de admin sai para quem não é.")
