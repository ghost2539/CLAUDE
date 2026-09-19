#!/usr/bin/env python3
"""Nenhum bundle de módulo sai pelo mount público.

    python3 scripts/verificar_bundle_protegido.py

O portal tem uma pasta `static/` montada SEM sessão — é onde moram o CSS, a
tela de login e as imagens, e tem de ser pública mesmo. O problema é que
qualquer coisa colocada ali vira pública junto, em silêncio.

Foi assim três vezes:

*   o JavaScript dos módulos (`static/modules/*.js`), que entregava a tela
    de administração inteira a quem soubesse o endereço;
*   o `static/app.js`, 27 KB com as rotas dos módulos e os nomes das
    permissões;
*   o `static/controle-orcamento-exec/app.js`, 643 KB com todas as telas de
    orçamento — enquanto a PÁGINA do módulo já exigia sessão e permissão.

O padrão do erro é sempre o mesmo: protege-se a porta e esquece-se a
janela. A página pede credencial, o bundle dela não.

Esta verificação fecha a porta pelo outro lado: em vez de confiar que
ninguém vai colocar código de módulo em `static/`, ela procura.
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
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


from core import security  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
import main  # noqa: E402

cliente = TestClient(main.app)

# Três papéis, porque são três respostas diferentes — e confundir "exige
# login" com "exige autorização" é justamente como um bundle liberado para
# todo mundo logado passaria despercebido.
_sid_a, _cookie_admin = security.create_session(
    {"username": "admin.teste", "is_admin": True,
     "permission_map": {}, "permissions": []})
_sid_b, _cookie_sem = security.create_session(
    {"username": "sem.permissao", "is_admin": False,
     "permission_map": {}, "permissions": []})


def get(caminho, cookie=None):
    c = {"spare_session": cookie} if cookie else {}
    return cliente.get(caminho, cookies=c)


print("[1] O bundle do Controle de Orçamento não é público")
# O caminho antigo tem de estar MORTO. Deixá-lo respondendo seria manter a
# janela aberta ao lado da porta nova.
for antigo in ("/static/controle-orcamento-exec/app.js",
               "/static/controle-orcamento-exec/app.css",
               "/static/controle-orcamento-exec/index.html"):
    checar(get(antigo).status_code == 404, f"{antigo} não existe mais (404)")

print("\n[2] A rota nova segue a MESMA regra da página")
PAGINA = "/controle-orcamento-InfraCSC"
BUNDLE = ["/controle-orcamento-exec/app.js", "/controle-orcamento-exec/app.css"]

r = cliente.get(PAGINA, follow_redirects=False)
checar(r.status_code == 302, f"a página manda o anônimo para o login ({r.status_code})")
for alvo in BUNDLE:
    checar(get(alvo).status_code == 401, f"{alvo}: anônimo leva 401")
    checar(get(alvo, _cookie_sem).status_code == 403,
           f"{alvo}: logado SEM permissão leva 403")
    r_ok = get(alvo, _cookie_admin)
    checar(r_ok.status_code == 200 and len(r_ok.content) > 1000,
           f"{alvo}: quem pode ver a tela recebe ({len(r_ok.content)} bytes)")

print("\n[3] A rota não vira porta para o disco")
for tentativa in ("/controle-orcamento-exec/index.html",
                  "/controle-orcamento-exec/main.py",
                  "/controle-orcamento-exec/..%2f..%2fmain.py",
                  "/controle-orcamento-exec/../../main.py"):
    checar(get(tentativa, _cookie_admin).status_code == 404,
           f"{tentativa.split('/')[-1]} não é servido")

for metodo in ("POST", "PUT", "DELETE"):
    r_m = cliente.request(metodo, BUNDLE[0], cookies={"spare_session": _cookie_admin})
    checar(r_m.status_code == 405, f"{metodo} no bundle responde 405")

print("\n[4] Contraprova: static/ não guarda código de módulo")
# Varre a pasta pública procurando o que NÃO devia estar lá. O login.js é a
# exceção declarada — ele é público por necessidade, e a verificação de
# estáticos confere que ele não carrega rota nem permissão.
PUBLICO_POR_NECESSIDADE = {"static/js/login.js"}

# PENDENTES CONHECIDOS — nomeados, não escondidos.
#
# Estes dois têm o MESMO defeito que o Controle de Orçamento tinha: a
# página exige sessão (302 para o login) e o bundle dela sai pelo mount
# público. Medido:
#
#     ebs-forms       página 302   app.js 200 (25 KB)
#     obsolescencia   página 302   app.js 200 (21 KB)
#
# Não foram corrigidos junto porque o pedido era sobre o Controle de
# Orçamento, e mover o bundle de um módulo exige revalidar aquela tela
# inteira. Ficam LISTADOS para que a verificação não passe fingindo que o
# assunto acabou — e para que um bundle NOVO em static/ seja reprovado na
# hora, em vez de entrar de carona nesta exceção.
#
# `consulta-times` e `indicadores` não entram: as páginas deles respondem
# 200 sem sessão, então o bundle público é coerente com a tela. Se um dia
# essas páginas passarem a exigir login, os bundles têm de vir junto.
PENDENTES = {"static/ebs-forms/app.js", "static/obsolescencia/app.js"}
suspeitos = []
for arq in (RAIZ / "static").rglob("*.js"):
    rel = arq.relative_to(RAIZ).as_posix()
    if rel in PUBLICO_POR_NECESSIDADE or rel in PENDENTES:
        continue
    tamanho = arq.stat().st_size
    texto = arq.read_text(encoding="utf-8", errors="ignore")
    # O que denuncia código de módulo: tamanho de bundle, ou as marcas de
    # quem fala com a API interna.
    if tamanho > 120_000 or "permission_map" in texto or "/api/controle-orcamento" in texto:
        suspeitos.append(f"{rel} ({tamanho} bytes)")
checar(not suspeitos,
       f"nenhum bundle NOVO de módulo em static/ {suspeitos or ''}")

# Os pendentes existem e continuam públicos: a verificação diz isso em voz
# alta a cada execução, em vez de deixar o assunto sumir.
print("\n  Pendentes conhecidos (mesmo defeito, ainda abertos):")
for rel in sorted(PENDENTES):
    arq = RAIZ / rel
    marca = f"{arq.stat().st_size} bytes" if arq.is_file() else "arquivo sumiu"
    print(f"    · {rel} — {marca}")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhou:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Bundles de módulo entregues só a quem pode vê-los.")
