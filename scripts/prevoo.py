#!/usr/bin/env python3
"""Pré-voo: confere se o portal tem tudo para subir, sem subir.

    venv/bin/python scripts/prevoo.py

Sete verificações, do básico ao que só falha em produção. Cada uma diz o que
fazer quando falha. Nada aqui altera o sistema.
"""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

ENVFILE = Path(os.environ.get(
    "PORTAL_ENVFILE", Path.home() / ".config" / "portal-spare" / "environment"))


def _carregar_env(caminho: Path) -> int:
    """Coloca o arquivo de ambiente em os.environ, como o portal.sh faz.

    Sem isso o pré-voo leria o arquivo mas testaria um processo sem ele —
    e acusaria falta de configuração que existe.
    """
    if not caminho.is_file():
        return 0
    n = 0
    for linha in caminho.read_text(encoding="utf-8", errors="replace").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        chave, valor = chave.strip(), valor.split(" #")[0].strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ[chave] = valor
        n += 1
    return n


_CARREGADAS = _carregar_env(ENVFILE)

_falhas = 0
_alertas = 0

# Sem estes o portal não sobe, ou sobe inútil (ninguém consegue liberar
# acesso). O resto é integração: faltando, só aquele módulo fica indisponível
# — e isso não pode impedir um teste de subida.
ESSENCIAIS = {"PORTAL_SESSION_SECRET", "INITIAL_ADMIN_LOGIN"}


def ok(titulo: str, detalhe: str = "") -> None:
    print(f"  [ OK ] {titulo}" + (f" — {detalhe}" if detalhe else ""))


def alerta(titulo: str, detalhe: str) -> None:
    global _alertas
    _alertas += 1
    print(f"  [aten] {titulo} — {detalhe}")


def falha(titulo: str, detalhe: str, comando: str = "") -> None:
    global _falhas
    _falhas += 1
    print(f"  [FALHA] {titulo} — {detalhe}")
    if comando:
        print(f"          {comando}")


def main() -> int:
    print("=" * 70)
    print(f"Pré-voo do Portal SPARE — {RAIZ}")
    print("=" * 70)

    # 1. Python e dependências
    print("\n1. Ambiente Python")
    ok("interpretador", sys.executable)
    faltando = []
    for mod in ("fastapi", "uvicorn", "sqlalchemy", "pydantic", "itsdangerous",
                "requests", "pandas", "openpyxl", "multipart"):
        try:
            __import__(mod)
        except ImportError:
            faltando.append(mod)
    if faltando:
        falha("dependências", f"faltam: {', '.join(faltando)}",
              "venv/bin/pip install -r requirements.txt")
    else:
        ok("dependências", "todas presentes")
    from core.cofre import algoritmo
    algo = algoritmo()
    if algo == "fernet":
        ok("cifra do cofre", "Fernet")
    else:
        alerta("cifra do cofre", f"'{algo}' — a 'cryptography' não carregou; "
                                 "cifra mais fraca")

    # 2. Arquivo de ambiente
    print("\n2. Arquivo de ambiente")
    envfile = ENVFILE
    if not envfile.is_file():
        falha("environment", f"não encontrado em {envfile}",
              "bash deploy/instalar_usuario.sh")
        print("\nSem o ambiente não dá para seguir.")
        return 1
    ok("environment", f"{envfile} ({_CARREGADAS} variáveis carregadas)")
    modo = oct(envfile.stat().st_mode & 0o777)[2:]
    (ok if modo == "600" else alerta)(
        "permissão", modo if modo == "600" else f"{modo} (esperado 600: chmod 600 {envfile})")

    # 3. Variáveis obrigatórias
    print("\n3. Configuração obrigatória")
    cfg = None
    try:
        from config import get_settings
        cfg = get_settings()
        ok("config carrega", "")
    except Exception as exc:  # noqa: BLE001
        # Não abortar aqui: quase sempre é um segredo que falta no cofre, e
        # o quadro completo (seção 4) é justamente o que diz qual.
        falha("config", str(exc)[:200],
              "venv/bin/python scripts/cofre.py preparar")

    if cfg is None:
        pass
    elif not cfg.INITIAL_ADMIN_LOGIN:
        falha("INITIAL_ADMIN_LOGIN", "vazio — ninguém conseguirá liberar acesso",
              "venv/bin/python scripts/cofre.py definir INITIAL_ADMIN_LOGIN")
    else:
        ok("INITIAL_ADMIN_LOGIN", cfg.INITIAL_ADMIN_LOGIN)
    if cfg is not None:
        if len(cfg.SESSION_SECRET) < 32:
            alerta("PORTAL_SESSION_SECRET", f"curto ({len(cfg.SESSION_SECRET)} caracteres)")
        else:
            ok("PORTAL_SESSION_SECRET", f"{len(cfg.SESSION_SECRET)} caracteres")

    # 4. Cofre
    print("\n4. Cofre")
    from core import cofre
    disp, motivo = cofre.diagnostico_corporativo()
    (ok if disp else alerta)("cofre corporativo", motivo)
    ok("cofre local", f"{len(cofre.listar())} segredo(s) em {cofre.ARQ_COFRE}")
    perm_ok, problemas = cofre.permissoes_ok()
    if perm_ok:
        ok("permissões do cofre", "só o dono lê")
    else:
        for p in problemas:
            alerta("permissão do cofre", p)

    essenciais_faltando, opcionais_faltando = [], []
    for linha in envfile.read_text(encoding="utf-8", errors="replace").splitlines():
        if linha.lstrip().startswith("#") or "=" not in linha:
            continue
        var, _, valor = linha.partition("=")
        for ref in cofre.referencias(valor):
            if cofre.obter(ref):
                continue
            (essenciais_faltando if ref in ESSENCIAIS else opcionais_faltando).append(ref)

    if essenciais_faltando:
        falha("segredos essenciais sem valor", ", ".join(essenciais_faltando),
              "venv/bin/python scripts/cofre.py preparar")
    else:
        ok("segredos essenciais", "presentes")

    if opcionais_faltando:
        alerta("integrações sem credencial",
               f"{len(opcionais_faltando)} — {', '.join(opcionais_faltando[:6])}"
               + (" …" if len(opcionais_faltando) > 6 else ""))
        print("         o portal sobe; esses módulos é que ficam indisponíveis")
    else:
        ok("credenciais de integração", "todas presentes")

    # 5. Banco
    print("\n5. Bancos de dados")
    if cfg is None:
        alerta("banco", "não verificado — a configuração não carregou")
    else:
      try:
        from sqlalchemy import text
        import db.portal as dbp
        with dbp.engine.connect() as c:
            c.execute(text("SELECT 1"))
        ok("banco principal", cfg.DATABASE_URL.split("@")[-1][:60])
      except Exception as exc:  # noqa: BLE001
        falha("banco principal", str(exc)[:200], "confira DATABASE_URL no environment")

    destino = RAIZ / "data" / "db"
    if os.access(destino if destino.exists() else RAIZ / "data", os.W_OK):
        ok("data/db gravável", str(destino))
    else:
        falha("data/db", f"sem permissão de escrita em {destino}")

    # 6. Rede
    print("\n6. Rede")
    porta = int(os.environ.get("PORT", "8901"))
    with socket.socket() as sk:
        sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sk.bind((os.environ.get("HOST", "0.0.0.0"), porta))
            ok(f"porta {porta}", "livre")
        except OSError as exc:
            falha(f"porta {porta}", f"ocupada ({exc})", "deploy/portal.sh stop")
    if cfg is not None:
        proxy = cfg.SN_API_PROXY
        (alerta if proxy else ok)("proxy de saída", proxy or "nenhum — saída direta")

    # 7. A aplicação carrega?
    print("\n7. Aplicação")
    try:
        # Os módulos isolados falham em silêncio de propósito (nunca derrubam
        # o portal), registrando só no log. Capturamos esse log: um módulo
        # que não sobe é exatamente o que este pré-voo tem de pegar.
        import io
        import logging
        captura = io.StringIO()
        h = logging.StreamHandler(captura)
        h.setLevel(logging.ERROR)
        raiz_log = logging.getLogger()
        raiz_log.addHandler(h)
        raiz_log.setLevel(logging.ERROR)
        import main
        raiz_log.removeHandler(h)

        def anda(rotas, achadas):
            for r in rotas:
                orig = getattr(r, "original_router", None)
                if orig is not None:
                    anda(orig.routes, achadas); continue
                sub = getattr(r, "routes", None)
                if sub:
                    anda(sub, achadas); continue
                p = getattr(r, "path", "")
                if p:
                    achadas.add(p)
            return achadas

        n = len(anda(main.app.routes, set()))
        problemas = [l for l in captura.getvalue().splitlines()
                     if "NÃO carregado" in l or "NAO carregado" in l]
        if problemas:
            falha(f"módulos que não subiram ({len(problemas)})", f"{n} rotas registradas")
            for l in problemas:
                print(f"          {l.strip()[:150]}")
        else:
            ok("importa e registra rotas", f"{n} rotas, nenhum módulo falhou")
    except Exception as exc:  # noqa: BLE001
        falha("aplicação", f"{type(exc).__name__}: {exc}")

    print("\n" + "=" * 70)
    if _falhas:
        print(f"{_falhas} falha(s) e {_alertas} alerta(s). Resolva as falhas antes de subir.")
        return 1
    if _alertas:
        print(f"Nenhuma falha, {_alertas} alerta(s). Dá para subir:")
    else:
        print("Tudo pronto. Pode subir:")
    print("  deploy/portal.sh start")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
