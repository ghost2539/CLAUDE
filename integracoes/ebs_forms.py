"""EBS Forms — RPA sobre o cliente Oracle Forms do E-Business Suite.

A API REST do EBS que o portal usa devolve PO/NF vazios e a base Oracle não
está ao nosso alcance. O caminho que FUNCIONA hoje é o que uma pessoa faz:
entrar no EBS pelo SSO, abrir a responsabilidade `RENNER_FA_CONSULTA`,
clicar em "Informações Financeiras" (que baixa um `frmservlet.jnlp`) e usar a
tela "Localizar Ativos". Este módulo faz exatamente isso, em segundo plano.

Peças:

1. `Sessao` — login SSO (OAM, o mesmo do ServiceNow) e obtenção do .jnlp da
   função. O jnlp carrega tickets de sessão de vida curta; é pedido na hora
   e apagado depois de usado.
2. `Cliente` — tela virtual (Xvfb) + `LancadorForms` (nossa JVM que hospeda
   o applet do Forms, ver `integracoes/ebs_forms_java/`). Conversa por
   linhas: tecla, texto, copiar, foto...
3. `Roteiro` — a sequência de teclas para a tela, editável no banco
   (`db/ebs_forms.py`) sem reiniciar o serviço. Tela de Forms muda de
   ordem de campo, de atalho, de tempo de resposta; isso é dado, não código.

Tudo que fica em disco fica em `data/ebs_forms/` (jars em cache, capturas de
tela, logs da JVM). Nunca guardamos senha: vem do cofre na hora do login.
"""
from __future__ import annotations

import base64
import html
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

import requests

import config as _config_mod

_log = logging.getLogger("ebs_forms")
_cfg = _config_mod.get_settings()

DIR = Path(_cfg.ROOT) / "data" / "ebs_forms"
DIR_BIN = DIR / "bin"
DIR_JARS = DIR / "jars"
DIR_CAPTURAS = DIR / "capturas"
DIR_LOGS = DIR / "logs"
DIR_DEPURACAO = DIR / "depuracao"
FONTE_JAVA = Path(__file__).parent / "ebs_forms_java" / "LancadorForms.java"

# Só uma sessão Forms por vez: é um usuário de verdade logado no EBS, e duas
# JVMs disputando o mesmo teclado virtual seria caos.
_trava = threading.Lock()


def _c(nome: str, padrao: str = "") -> str:
    return getattr(_cfg, nome, padrao) or padrao


class ErroForms(RuntimeError):
    """Falha esperável do RPA (login, jnlp, JVM, roteiro) — vai para o log da execução."""


# ═══════════════════════════════════════════════════════════════════════
# 1. Sessão HTTP: SSO + jnlp
# ═══════════════════════════════════════════════════════════════════════
class _Form(HTMLParser):
    """Extrai o primeiro <form> (action + inputs) sem depender de bs4."""

    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.campos: dict[str, str] = {}
        self.tipos: dict[str, str] = {}
        self._dentro = False
        self._fechado = False
        self.links: list[tuple[str, str]] = []
        self._href: Optional[str] = None
        self._texto: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        a = dict(attrs)
        if tag == "form" and not self._fechado and not self._dentro:
            self._dentro = True
            self.action = a.get("action") or ""
        elif tag == "input" and self._dentro:
            nome = a.get("name")
            if nome:
                self.campos[nome] = html.unescape(a.get("value") or "")
                self.tipos[nome] = (a.get("type") or "text").lower()
        elif tag == "a":
            self._href = a.get("href")
            self._texto = []

    def handle_data(self, data):  # noqa: ANN001
        if self._href is not None:
            self._texto.append(data)

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag == "form" and self._dentro:
            self._dentro = False
            self._fechado = True
        elif tag == "a" and self._href is not None:
            self.links.append((html.unescape(self._href), " ".join("".join(self._texto).split())))
            self._href = None


def _analisar(texto: str) -> _Form:
    p = _Form()
    try:
        p.feed(texto)
    except Exception:  # noqa: BLE001 — HTML quebrado não pode parar o login
        pass
    return p


class Sessao:
    """Login SSO no EBS e obtenção do .jnlp da função de consulta."""

    def __init__(self, usuario: str, senha: str, registrar: Callable[[str], None] | None = None):
        self.usuario = usuario
        self._senha = senha
        self.http = requests.Session()
        self.http.verify = _c("EBS_FORMS_VERIFY", "false").lower() == "true"
        self.http.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 Edg/124.0"
        )
        proxy = _c("EBS_FORMS_PROXY")
        if proxy:
            self.http.proxies = {"http": proxy, "https": proxy}
        else:
            self.http.trust_env = False
        self.home = _c("EBS_FORMS_HOME_URL",
                       "http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE")
        self.timeout = int(_c("EBS_FORMS_TIMEOUT", "40"))
        self._registrar = registrar or (lambda m: _log.info("%s", m))
        self.jnlp_pronto: str = ""
        self.url_jnlp: str = ""

    # ── SSO (Oracle Access Manager) ─────────────────────────────────────
    def _seguir_formularios(self, r: requests.Response, saltos: int = 0) -> requests.Response:
        """OAM devolve páginas com <form> auto-submetido no meio do caminho."""
        if saltos > 6:
            return r
        f = _analisar(r.text)
        if f.action is None:
            return r
        visiveis = [n for n, t in f.tipos.items() if t in ("text", "password", "email")]
        if visiveis:
            return r  # é um formulário de verdade, não um redirecionamento
        acao = urljoin(r.url, f.action or r.url)
        r2 = self.http.post(acao, data=f.campos, allow_redirects=True, timeout=self.timeout)
        return self._seguir_formularios(r2, saltos + 1)

    _RE_META = re.compile(r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\'][^;"\']*;\s*url=([^"\'>]+)', re.I)
    _RE_JS = re.compile(r'(?:window\.|document\.|top\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']', re.I)

    def _logado(self, r: requests.Response) -> bool:
        """Só conta como autenticado quando estamos numa página de aplicação do
        EBS — não no AccessGate/AppsLogin, que também moram no mesmo host."""
        u = urlparse(r.url)
        caminho = u.path.lower()
        if "ebscorporativo" not in u.netloc:
            return False
        if any(x in caminho for x in ("accessgate", "appslogin", "dossologin", "applogin")):
            return False
        return "oa_html" in caminho or "/forms/" in caminho

    def entrar(self) -> None:
        inicio = _c("EBS_FORMS_LOGIN_URL", "http://ebscorporativo.lojasrenner.com.br/OA_HTML/AppsLogin")
        self._registrar(f"SSO: abrindo {inicio}")
        r = self.http.get(inicio, allow_redirects=True, timeout=self.timeout)
        enviou_senha = False
        for salto in range(1, 10):
            self._registrar(f"SSO: salto {salto} -> {r.url} ({r.status_code})")
            self._guardar_depuracao(f"sso_salto{salto}.html", r.text)
            if self._e_jnlp(r):
                # O OAM devolveu direto ao frmservlet: o jnlp já está na mão.
                self.jnlp_pronto = r.text
                self.url_jnlp = r.url
                self._registrar("SSO: autenticado e o jnlp já veio no redirecionamento")
                return
            if self._logado(r):
                self._registrar("SSO: autenticado no EBS")
                return
            f = _analisar(r.text)
            senha_campo = next((n for n, t in f.tipos.items() if t == "password"), None)
            if f.action is not None and senha_campo:
                if enviou_senha:
                    raise ErroForms("SSO devolveu o formulário de login de novo: usuário ou senha do robô recusados.")
                acao = urljoin(r.url, f.action or r.url)
                campos = dict(f.campos)
                campos[senha_campo] = self._senha
                usuario_campo = next((n for n in ("username", "userid", "user", "login", "j_username", "ssousername")
                                      if n in campos), None)
                if usuario_campo is None:
                    usuario_campo = next((n for n, t in f.tipos.items() if t in ("text", "email")), "username")
                campos[usuario_campo] = self.usuario
                self._registrar(f"SSO: enviando credenciais para {acao} (campos {sorted(campos)})")
                r = self.http.post(acao, data=campos, allow_redirects=True, timeout=self.timeout)
                enviou_senha = True
                continue
            if f.action is not None and f.campos:
                # formulário sem senha = auto-submit do OAM/AccessGate
                acao = urljoin(r.url, f.action or r.url)
                self._registrar(f"SSO: formulário automático -> {acao}")
                r = self.http.post(acao, data=f.campos, allow_redirects=True, timeout=self.timeout)
                continue
            m = self._RE_META.search(r.text) or self._RE_JS.search(r.text)
            if m:
                destino = urljoin(r.url, html.unescape(m.group(1).strip()))
                self._registrar(f"SSO: redirecionamento na página -> {destino}")
                r = self.http.get(destino, allow_redirects=True, timeout=self.timeout)
                continue
            # AccessGate com erro e sem saída: tenta o ponto de entrada de login do OAM
            if "accessgate" in r.url.lower() and salto == 1:
                alt = urljoin(r.url, "/OA_HTML/AppsLocalLogin.jsp")
                self._registrar(f"SSO: AccessGate sem formulário; tentando {alt}")
                r = self.http.get(alt, allow_redirects=True, timeout=self.timeout)
                continue
            break
        raise ErroForms(
            f"SSO não levou ao EBS (parou em {r.url}). As páginas de cada salto estão em "
            "data/ebs_forms/depuracao/sso_salto*.html."
        )

    # ── jnlp ────────────────────────────────────────────────────────────
    def _e_jnlp(self, r: requests.Response) -> bool:
        ct = r.headers.get("Content-Type", "").lower()
        return "jnlp" in ct or r.text.lstrip().startswith("<?xml") and "<jnlp" in r.text[:2000]

    def obter_jnlp(self) -> str:
        """Devolve o XML do jnlp da função configurada.

        Caminho preferido: `EBS_FORMS_FUNCAO_URL` (o link que a home do EBS
        chama ao clicar em "Informações Financeiras" — algo como
        `.../OA_HTML/RF.jsp?function_id=...&resp_id=...&resp_appl_id=...`).
        Sem ele, procura na home um link cujo texto seja a função.
        """
        if self.jnlp_pronto:
            j, self.jnlp_pronto = self.jnlp_pronto, ""
            if self.url_jnlp:
                # Pede de novo para sair com tickets recém-emitidos.
                try:
                    r = self.http.get(self.url_jnlp, allow_redirects=True, timeout=self.timeout)
                    if self._e_jnlp(r):
                        self._registrar("jnlp renovado antes de abrir o Forms")
                        return r.text
                except requests.RequestException as exc:
                    self._registrar(f"jnlp: renovação falhou ({exc}); usando o anterior")
            return j
        url = _c("EBS_FORMS_FUNCAO_URL").strip()
        if url and not url.lower().startswith("http"):
            raise ErroForms(f"EBS_FORMS_FUNCAO_URL não é um endereço válido: {url[:60]!r}. "
                            "Cole o link real de 'Informações Financeiras' (começa com http).")
        if url:
            url = self._sem_tokens_de_aba(url)
            self._registrar(f"função: pedindo {url}")
            r = self.http.get(url, allow_redirects=True, timeout=self.timeout)
            r = self._seguir_formularios(r)
            self._registrar(f"função: chegou em {r.url} ({r.status_code}, {r.headers.get('Content-Type')})")
            if self._e_jnlp(r):
                return r.text
            self._guardar_depuracao("funcao_nao_jnlp.html", r.text)
            # Alguns caminhos devolvem uma página com o link do frmservlet.
            for href, _ in _analisar(r.text).links:
                if "frmservlet" in href or ".jnlp" in href:
                    r2 = self.http.get(urljoin(r.url, href), timeout=self.timeout)
                    if self._e_jnlp(r2):
                        return r2.text
            raise ErroForms(
                f"EBS_FORMS_FUNCAO_URL não devolveu um jnlp (veio {r.headers.get('Content-Type')}; "
                f"página guardada em data/ebs_forms/depuracao/funcao_nao_jnlp.html)."
            )
        # Descoberta pela home: link com o nome da função.
        funcao = _c("EBS_FORMS_FUNCAO", "Informações Financeiras").casefold()
        r = self.http.get(self.home, allow_redirects=True, timeout=self.timeout)
        self._guardar_depuracao("home.html", r.text)
        for href, texto in _analisar(r.text).links:
            if texto.casefold() == funcao or ("RF.jsp" in href and funcao in texto.casefold()):
                r2 = self.http.get(urljoin(r.url, href), allow_redirects=True, timeout=self.timeout)
                r2 = self._seguir_formularios(r2)
                if self._e_jnlp(r2):
                    return r2.text
        raise ErroForms(
            "Não achei o link da função na home do EBS. Copie o link de "
            f"'{_c('EBS_FORMS_FUNCAO', 'Informações Financeiras')}' no navegador "
            "e informe em EBS_FORMS_FUNCAO_URL (a home foi guardada em data/ebs_forms/depuracao/home.html)."
        )

    @staticmethod
    def _sem_tokens_de_aba(url: str) -> str:
        """Links copiados da home do EBS trazem tokens presos à aba do navegador
        (oas, oapc, transactionid...). Fora dela viram "erro inesperado";
        sem eles o RF.jsp funciona como link direto."""
        from urllib.parse import parse_qsl, urlencode, urlunparse
        u = urlparse(url)
        descartar = {"oas", "oapc", "transactionid", "_ti", "retainam", "addbreadcrumb", "oapb", "oaspid"}
        q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in descartar]
        return urlunparse(u._replace(query=urlencode(q, safe="/:'%,")))

    def baixar(self, url: str, destino: Path) -> Path:
        """Baixa com a sessão autenticada (usado para o que o Forms manda abrir no navegador)."""
        r = self.http.get(url, timeout=self.timeout, allow_redirects=True)
        r.raise_for_status()
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(r.content)
        return destino

    def _guardar_depuracao(self, nome: str, texto: str) -> None:
        try:
            DIR_DEPURACAO.mkdir(parents=True, exist_ok=True)
            p = DIR_DEPURACAO / nome
            p.write_text(texto, encoding="utf-8", errors="replace")
            os.chmod(p, 0o600)
        except Exception:  # noqa: BLE001
            pass


# ═══════════════════════════════════════════════════════════════════════
# 2. Cliente: Xvfb + JVM
# ═══════════════════════════════════════════════════════════════════════
def _display_livre(display: str) -> bool:
    """Um display X ":N" ocupado tem um socket em /tmp/.X11-unix/XN."""
    n = display.lstrip(":").split(".")[0]
    return not Path(f"/tmp/.X11-unix/X{n}").exists()


def servidor_x(display: str) -> list[str] | None:
    """Linha de comando do servidor X virtual: Xvfb, ou Xvnc quando só ele existe.

    Nem todo repositório tem o pacote do Xvfb; o Xvnc (tigervnc-server-minimal)
    é um servidor X completo e ainda permite olhar a tela por VNC ao depurar.
    Ele só escuta em localhost (túnel SSH para ver).
    """
    tam = _c("EBS_FORMS_TELA", "1280x900x24")
    largura_altura, _, prof = tam.rpartition("x")
    preferido = _c("EBS_FORMS_XSERVER")  # caminho ou nome, se quiser forçar
    candidatos = [preferido] if preferido else ["Xvfb", "Xvnc"]
    for nome in candidatos:
        exe = shutil.which(nome) if not os.path.isabs(nome) else (nome if os.access(nome, os.X_OK) else None)
        if not exe:
            continue
        if os.path.basename(exe).lower() == "xvnc":
            porta = 5900 + int(display.lstrip(":").split(".")[0])
            return [exe, display, "-geometry", largura_altura, "-depth", prof or "24",
                    "-SecurityTypes", "None", "-localhost", "-rfbport", str(porta), "-AlwaysShared"]
        return [exe, display, "-screen", "0", tam, "-nolisten", "tcp", "-noreset"]
    return None


class Xvfb:
    """Tela virtual. Uma por processo; reaproveitada entre sessões."""

    _proc: Optional[subprocess.Popen] = None
    _display: str = ""

    @classmethod
    def garantir(cls) -> str:
        display = _c("EBS_FORMS_DISPLAY", ":99")
        if cls._proc and cls._proc.poll() is None:
            return cls._display
        if not _display_livre(display):
            # Alguém (ou uma execução anterior) já mantém o display; usamos.
            cls._display = display
            return display
        cmd = servidor_x(display)
        if not cmd:
            return cls._garantir_weston()
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        log = open(DIR_LOGS / "xvfb.log", "ab")  # noqa: SIM115 — vive com o processo
        cls._proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        cls._display = display
        for _ in range(50):
            if not _display_livre(display):
                break
            if cls._proc.poll() is not None:
                raise ErroForms("Xvfb encerrou ao iniciar; veja data/ebs_forms/logs/xvfb.log")
            time.sleep(0.1)
        return display

    @classmethod
    def _garantir_weston(cls) -> str:
        """RHEL/Oracle Linux 10 não têm mais Xorg (nem Xvfb, nem Xvnc): só
        Wayland. O Weston em modo *headless* sobe um compositor sem monitor e,
        com --xwayland, um servidor X por cima — para o Java é um X normal, e
        o Weston ainda faz o papel de gerenciador de janelas (foco). O número
        do display é escolhido pelo Xwayland; lemos do log do Weston."""
        weston = _c("EBS_FORMS_WESTON") or shutil.which("weston")
        if not weston or not shutil.which("Xwayland"):
            raise ErroForms("Nenhuma tela virtual disponível. Instale Xvfb (xorg-x11-server-Xvfb) ou, em "
                            "RHEL/OL 10, weston + xorg-x11-server-Xwayland. Veja scripts/ebs_forms_preparar.sh.")
        tam = _c("EBS_FORMS_TELA", "1280x900x24").split("x")
        # Servidor que nunca rodou X não tem /tmp/.X11-unix; sem ela o Xwayland
        # do Weston não consegue criar o socket do display e o Weston cai.
        x11 = Path("/tmp/.X11-unix")
        try:
            x11.mkdir(mode=0o1777, exist_ok=True)
            os.chmod(x11, 0o1777)
        except PermissionError:
            if not x11.is_dir():
                raise ErroForms("/tmp/.X11-unix não existe e não consegui criá-la; como root: "
                                "mkdir -m 1777 /tmp/.X11-unix")
        runtime = DIR / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        os.chmod(runtime, 0o700)  # o Wayland exige XDG_RUNTIME_DIR só do dono
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        log_path = DIR_LOGS / "weston.log"
        try:
            log_path.unlink()
        except FileNotFoundError:
            pass
        # Sobras de uma rodada anterior (Weston órfão, lock e socket) impedem
        # o novo de subir: "unable to lock lockfile". Só mexemos no nosso
        # socket nomeado, nunca em outro compositor da máquina.
        subprocess.run(["pkill", "-f", "weston .*--socket=portal-ebs-forms"], capture_output=True)
        time.sleep(0.3)
        for sobra in ("portal-ebs-forms", "portal-ebs-forms.lock"):
            try:
                (runtime / sobra).unlink()
            except FileNotFoundError:
                pass
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = str(runtime)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        cmd = [weston, "--backend=headless", "--xwayland", f"--width={tam[0]}", f"--height={tam[1]}",
               "--socket=portal-ebs-forms", "--idle-time=0", f"--log={log_path}"]
        extra = _c("EBS_FORMS_WESTON_OPCOES")
        if extra:
            cmd += extra.split()
        saida = open(DIR_LOGS / "weston.saida.log", "ab")  # noqa: SIM115 — vive com o processo
        cls._proc = subprocess.Popen(cmd, stdout=saida, stderr=saida, stdin=subprocess.DEVNULL, env=env)
        cls._xdg_runtime = str(runtime)
        for _ in range(150):
            if cls._proc.poll() is not None:
                raise ErroForms(f"weston encerrou ao iniciar (código {cls._proc.returncode}); "
                                f"veja data/ebs_forms/logs/weston.log e weston.saida.log")
            try:
                m = re.search(r"listening on display (:\d+)", log_path.read_text(errors="replace"))
            except FileNotFoundError:
                m = None
            if m:
                cls._display = m.group(1)
                # O Xwayland sobe "preguiçoso", no primeiro cliente X; dá um
                # respiro para o compositor terminar de montar a área de trabalho.
                time.sleep(1.0)
                return cls._display
            time.sleep(0.1)
        raise ErroForms("weston subiu mas não anunciou o display X (--xwayland); veja data/ebs_forms/logs/weston.log")

    _xdg_runtime: str = ""


def compilado() -> bool:
    return (DIR_BIN / "LancadorForms.class").exists()


def compilar() -> str:
    """Compila o lançador Java para data/ebs_forms/bin (precisa de javac)."""
    javac = _c("EBS_FORMS_JAVAC") or shutil.which("javac")
    if not javac:
        raise ErroForms("javac não encontrado: instale o pacote -devel do OpenJDK ou compile em outra máquina "
                        "e copie as classes para data/ebs_forms/bin.")
    DIR_BIN.mkdir(parents=True, exist_ok=True)
    fontes = sorted(str(f) for f in FONTE_JAVA.parent.rglob("*.java"))
    r = subprocess.run([javac, "-Xlint:-removal", "-d", str(DIR_BIN), *fontes],
                       capture_output=True, text=True, timeout=120)
    saida = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        raise ErroForms(f"javac falhou: {saida[-800:]}")
    return saida


class Cliente:
    """Uma JVM com o cliente Forms dentro; comandos por linha."""

    def __init__(self, jnlp_xml: str, registrar: Callable[[str], None] | None = None):
        self._registrar = registrar or (lambda m: _log.info("%s", m))
        self.eventos: list[str] = []
        self.documentos: list[str] = []
        self.proc: Optional[subprocess.Popen] = None
        self._jnlp = DIR / f"sessao-{os.getpid()}-{int(time.time())}.jnlp"
        DIR.mkdir(parents=True, exist_ok=True)
        self._jnlp.write_text(jnlp_xml, encoding="utf-8")
        os.chmod(self._jnlp, 0o600)  # carrega tickets de sessão

    def iniciar(self) -> None:
        if not compilado():
            compilar()
        display = Xvfb.garantir()
        java = _c("EBS_FORMS_JAVA") or shutil.which("java") or "java"
        env = dict(os.environ)
        env["DISPLAY"] = display
        if Xvfb._xdg_runtime:
            env["XDG_RUNTIME_DIR"] = Xvfb._xdg_runtime
        env.pop("JAVA_TOOL_OPTIONS", None)  # nada de proxy/truststore herdado
        env["LANG"] = env.get("LANG") or "pt_BR.UTF-8"
        tam = _c("EBS_FORMS_TELA", "1280x900x24").split("x")
        classe = _c("EBS_FORMS_CLASSE")  # vazio = a classe que o jnlp declara
        DIR_LOGS.mkdir(parents=True, exist_ok=True)
        self.log_path = DIR_LOGS / f"jvm-{datetime.now():%Y%m%d-%H%M%S}.log"
        self._log_f = open(self.log_path, "wb")  # noqa: SIM115 — fechado em encerrar()
        # O cliente Forms foi escrito para o Java 8 e usa pacotes internos
        # (sun.awt, sun.java2d...) que o Java 21 fecha por padrão.
        abrir = []
        for pacote in ("sun.awt", "sun.java2d", "sun.awt.X11", "sun.font", "sun.swing", "java.awt",
                       "java.awt.event", "java.awt.peer", "javax.swing", "java.applet"):
            abrir += ["--add-opens", f"java.desktop/{pacote}=ALL-UNNAMED"]
        for pacote in ("java.lang", "java.lang.reflect", "java.net", "java.util", "java.io", "sun.net.www.protocol.http"):
            abrir += ["--add-opens", f"java.base/{pacote}=ALL-UNNAMED"]
        cmd = [
            java, *abrir, "-Djava.security.manager=allow", "-Djava.awt.headless=false", f"-Dforms.classe={classe}",
            "-Dsun.java2d.xrender=false", "-Xmx512m",
            "-cp", str(DIR_BIN), "LancadorForms", str(self._jnlp), str(DIR_JARS), tam[0], tam[1],
        ]
        opcoes = _c("EBS_FORMS_JAVA_OPCOES")
        if opcoes:
            cmd[1:1] = opcoes.split()
        # EBS_FORMS_PARAMS="clientBrowser=chrome;outro=valor" sobrepõe parâmetros do jnlp.
        for par in _c("EBS_FORMS_PARAMS").split(";"):
            if "=" in par:
                nome, valor = par.split("=", 1)
                cmd.insert(1, f"-Dforms.param.{nome.strip()}={valor.strip()}")
        self._registrar(f"JVM: {java} (display {display}, classe {classe or 'a do jnlp'})")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._log_f, env=env, cwd=str(DIR), text=True,
                                     encoding="utf-8", bufsize=1)
        self._iniciar_leitor()
        resp = self._ler_ate_resposta(timeout=int(_c("EBS_FORMS_ESPERA_JVM", "180")))
        if not resp.startswith("OK"):
            raise ErroForms(f"Lançador não ficou pronto: {resp}")

    def _iniciar_leitor(self) -> None:
        """readline() na saída da JVM bloqueia sem limite quando ela fica muda;
        uma thread lê e enfileira, e quem espera tem timeout de verdade."""
        import queue
        self._fila: "queue.Queue[str | None]" = queue.Queue()

        def _ler() -> None:
            try:
                for linha in self.proc.stdout:  # type: ignore[union-attr]
                    self._fila.put(linha.rstrip("\n"))
            finally:
                self._fila.put(None)

        threading.Thread(target=_ler, name="ebs-forms-leitor", daemon=True).start()

    def _ler_ate_resposta(self, timeout: float) -> str:
        import queue
        assert self.proc
        fim = time.time() + timeout
        while time.time() < fim:
            try:
                linha = self._fila.get(timeout=0.5)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise ErroForms(f"JVM encerrou (código {self.proc.returncode}). Fim do log: {self._cauda_log()}")
                continue
            if linha is None:
                self.proc.wait(timeout=5)
                raise ErroForms(f"JVM encerrou (código {self.proc.returncode}). Fim do log: {self._cauda_log()}")
            if linha.startswith("EVENTO "):
                self.eventos.append(linha[7:])
                if linha.startswith("EVENTO documento "):
                    self.documentos.append(linha[17:])
                self._registrar(f"Forms: {linha[7:]}")
                continue
            if linha.startswith("OK") or linha.startswith("ERRO"):
                return linha
        raise ErroForms(f"Lançador não respondeu em {timeout}s")

    def ordem(self, cmd: str, arg: str = "", timeout: float = 60) -> str:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(f"{cmd} {arg}".strip() + "\n")
        self.proc.stdin.flush()
        resp = self._ler_ate_resposta(timeout)
        if resp.startswith("ERRO"):
            raise ErroForms(f"{cmd}: {resp[5:]}")
        return resp[3:] if len(resp) > 3 else ""

    # atalhos
    def tecla(self, combos: str) -> None:
        self.ordem("tecla", combos)

    def texto(self, s: str) -> None:
        self.ordem("texto", base64.b64encode(s.encode("utf-8")).decode())

    def digitar(self, s: str) -> None:
        self.ordem("digitar", base64.b64encode(s.encode("utf-8")).decode())

    def copiar(self) -> str:
        return base64.b64decode(self.ordem("copiar")).decode("utf-8", "replace")

    def esperar(self, ms: int) -> None:
        self.ordem("esperar", str(ms), timeout=ms / 1000 + 30)

    def janelas(self) -> list[str]:
        return base64.b64decode(self.ordem("janelas")).decode("utf-8", "replace").splitlines()

    def foto(self, nome: str) -> str:
        DIR_CAPTURAS.mkdir(parents=True, exist_ok=True)
        seguro = re.sub(r"[^A-Za-z0-9_.-]", "_", nome)
        caminho = DIR_CAPTURAS / f"{datetime.now():%Y%m%d-%H%M%S}-{seguro}.png"
        self.ordem("foto", str(caminho))
        return caminho.name

    def _cauda_log(self) -> str:
        try:
            self._log_f.flush()
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-1200:]
        except Exception:  # noqa: BLE001
            return ""

    def encerrar(self) -> None:
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write("sair\n")  # type: ignore[union-attr]
                    self.proc.stdin.flush()  # type: ignore[union-attr]
                    self.proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    self.proc.kill()
        finally:
            try:
                self._log_f.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._jnlp.unlink()
            except Exception:  # noqa: BLE001
                pass


# ═══════════════════════════════════════════════════════════════════════
# 3. Roteiros
# ═══════════════════════════════════════════════════════════════════════
# Um passo é {"acao": ..., "arg": ..., "nome": ...}. Ações:
#   esperar <ms>            pausa
#   tecla <COMBOS>          ex.: "TAB", "CTRL+F11", "ALT+L", "TAB TAB ENTER"
#   texto <valor>           cola texto (aceita {numero} {etiqueta} {serie} {livro})
#   digitar <valor>         digita tecla a tecla (ASCII)
#   copiar                  lê o campo atual e guarda em resultado[nome]
#   foto                    captura a tela (guardada na execução)
#   se_vazio <nome>         pula os próximos passos até "fim_se" se resultado[nome] tiver valor
#   fim_se
#   documento               baixa o que o Forms mandou abrir (Exportar) e guarda em resultado[nome]
#
# Os roteiros padrão são um ponto de partida para a tela "Localizar Ativos"
# — a ordem dos campos e os atalhos se ajustam na tela de administração,
# olhando as capturas de cada passo.
ROTEIROS_PADRAO: dict[str, dict[str, Any]] = {
    "abrir": {
        "descricao": "Espera o Forms abrir a tela Localizar Ativos e fotografa.",
        "passos": [
            {"acao": "esperar", "arg": "20000", "nome": "abrindo"},
            {"acao": "foto", "nome": "tela_inicial"},
        ],
    },
    "localizar_ativo": {
        "descricao": "Preenche o critério e o Livro na tela Localizar Ativos e dispara Localizar.",
        "passos": [
            {"acao": "foto", "nome": "antes"},
            {"acao": "texto", "arg": "{criterio}", "nome": "criterio"},
            {"acao": "tecla", "arg": "TAB", "nome": "proximo_campo"},
            {"acao": "foto", "nome": "criterio_preenchido"},
            {"acao": "tecla", "arg": "ALT+L", "nome": "localizar"},
            {"acao": "esperar", "arg": "4000", "nome": "consultando"},
            {"acao": "foto", "nome": "resultado"},
        ],
    },
    "ler_ativo": {
        "descricao": "Lê os campos da linha selecionada na tela Ativos (Tab entre campos).",
        "passos": [
            {"acao": "copiar", "nome": "nr_ativo"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "descricao"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "etiqueta"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "categoria"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "serie"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "chave"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "tipo_ativo"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "unidades"},
            {"acao": "tecla", "arg": "TAB"},
            {"acao": "copiar", "nome": "tipo_propriedade"},
            {"acao": "foto", "nome": "lido"},
        ],
    },
    "linhas_origem": {
        "descricao": "Abre Linhas de Origem (OC/NF do ativo) e lê a primeira linha.",
        "passos": [
            {"acao": "tecla", "arg": "ALT+L", "nome": "botao_linhas_origem"},
            {"acao": "esperar", "arg": "3000"},
            {"acao": "foto", "nome": "linhas_origem"},
        ],
    },
}


def _substituir(valor: str, variaveis: dict[str, str]) -> str:
    def _v(m: re.Match) -> str:
        return variaveis.get(m.group(1), "")
    return re.sub(r"\{(\w+)\}", _v, valor or "")


def executar_roteiro(cliente: Cliente, passos: list[dict], variaveis: dict[str, str],
                     sessao: Sessao | None, registrar: Callable[[str], None],
                     capturas: list[str]) -> dict[str, Any]:
    resultado: dict[str, Any] = {}
    pulando = False
    for i, passo in enumerate(passos, 1):
        acao = (passo.get("acao") or "").lower()
        arg = _substituir(str(passo.get("arg", "")), variaveis)
        nome = passo.get("nome") or f"passo{i}"
        if pulando:
            if acao == "fim_se":
                pulando = False
            continue
        registrar(f"passo {i} {acao} {nome}")
        if acao == "esperar":
            cliente.esperar(int(arg or "1000"))
        elif acao == "tecla":
            cliente.tecla(arg)
        elif acao == "texto":
            cliente.texto(arg)
        elif acao == "digitar":
            cliente.digitar(arg)
        elif acao == "copiar":
            resultado[nome] = cliente.copiar().strip()
        elif acao == "foto":
            capturas.append(cliente.foto(nome))
        elif acao == "se_vazio":
            pulando = bool(str(resultado.get(arg, "")).strip())
        elif acao == "fim_se":
            pass
        elif acao == "documento":
            if not cliente.documentos:
                cliente.esperar(1500)
            if cliente.documentos and sessao is not None:
                url = cliente.documentos.pop(0)
                destino = DIR_DEPURACAO / f"documento-{datetime.now():%Y%m%d-%H%M%S}.txt"
                sessao.baixar(url, destino)
                resultado[nome] = destino.read_text(encoding="latin-1", errors="replace")
            else:
                resultado[nome] = ""
        else:
            raise ErroForms(f"passo {i}: ação desconhecida '{acao}'")
    return resultado


# ═══════════════════════════════════════════════════════════════════════
# 4. Operações de alto nível
# ═══════════════════════════════════════════════════════════════════════
def _segredo(nome: str) -> str:
    """Cofre local primeiro; variável de ambiente só como último recurso.

    Resolvido aqui, e não pelo config.py, para o módulo rodar igual no branch
    de produção (que ainda não tem a expansão `@cofre:` no config).
    """
    try:
        from core.cofre import obter
        v = obter(nome, "")
        if v:
            return v
    except Exception:  # noqa: BLE001 — sem cofre, segue para o ambiente
        pass
    return os.environ.get(nome, "")


def credenciais() -> tuple[str, str]:
    """Conta que o robô usa no EBS — pelo cofre, nunca escrita no environment."""
    usuario = _segredo("EBS_FORMS_USER")
    senha = _segredo("EBS_FORMS_PASS")
    if not usuario or not senha:
        raise ErroForms("Credenciais do robô ausentes: defina EBS_FORMS_USER e EBS_FORMS_PASS no cofre "
                        "(python3 scripts/cofre.py definir EBS_FORMS_USER).")
    return usuario, senha


def diagnostico() -> dict[str, Any]:
    """O que falta para o RPA rodar — sem expor nenhum valor secreto."""
    java = _c("EBS_FORMS_JAVA") or shutil.which("java")
    javac = _c("EBS_FORMS_JAVAC") or shutil.which("javac")
    versao = ""
    if java:
        try:
            r = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=15,
                               env={**os.environ, "JAVA_TOOL_OPTIONS": ""})
            versao = (r.stderr or r.stdout).splitlines()[0] if (r.stderr or r.stdout) else ""
        except Exception as exc:  # noqa: BLE001
            versao = f"erro: {exc}"
    try:
        usuario, _ = credenciais()
        cred = f"ok ({usuario})"
    except ErroForms as exc:
        cred = str(exc)
    display = _c("EBS_FORMS_DISPLAY", ":99")
    return {
        "java": java or "", "java_versao": versao, "javac": javac or "",
        "lancador_compilado": compilado(),
        "xvfb": (servidor_x(display) or [""])[0] or (("weston+Xwayland") if shutil.which("weston") and shutil.which("Xwayland") else ""),
        "display": display,
        "display_ativo": not _display_livre(display),
        "credenciais": cred,
        "home_url": _c("EBS_FORMS_HOME_URL", "http://ebscorporativo.lojasrenner.com.br/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE"),
        "funcao_url_definida": bool(_c("EBS_FORMS_FUNCAO_URL")),
        "funcao": _c("EBS_FORMS_FUNCAO", "Informações Financeiras"),
        "responsabilidade": _c("EBS_FORMS_RESPONSABILIDADE", "RENNER_FA_CONSULTA"),
        "livros": livros(),
        "pasta": str(DIR),
        "servidor": socket.gethostname(),
        "ocupado": _trava.locked(),
    }


def livros() -> list[str]:
    return [x.strip() for x in _c("EBS_FORMS_LIVROS", "FA_RENNER,FA_RENNER_FIS").split(",") if x.strip()]


def testar_abertura(registrar: Callable[[str], None], roteiro_abrir: list[dict] | None = None) -> dict[str, Any]:
    """Só entra, abre o Forms, fotografa e fecha. É o primeiro teste em servidor novo."""
    if not _trava.acquire(timeout=5):
        raise ErroForms("Já existe uma sessão Forms em andamento.")
    capturas: list[str] = []
    cliente: Cliente | None = None
    try:
        usuario, senha = credenciais()
        if not compilado():
            compilar()
        registrar(f"tela virtual: display {Xvfb.garantir()}")
        sessao = Sessao(usuario, senha, registrar)
        sessao.entrar()
        # Os tickets do jnlp são de vida curta: ele é pedido por último, com a
        # tela virtual já de pé e os jars já em cache.
        jnlp = sessao.obter_jnlp()
        registrar(f"jnlp obtido ({len(jnlp)} bytes)")
        cliente = Cliente(jnlp, registrar)
        cliente.iniciar()
        passos = roteiro_abrir or ROTEIROS_PADRAO["abrir"]["passos"]
        resultado = executar_roteiro(cliente, passos, {}, sessao, registrar, capturas)
        janelas = cliente.janelas()
        return {"resultado": resultado, "janelas": janelas, "eventos": cliente.eventos[-30:],
                "capturas": capturas, "log_jvm": cliente.log_path.name}
    finally:
        if cliente:
            cliente.encerrar()
        _trava.release()


def consultar_ativo(criterio: str, registrar: Callable[[str], None], roteiros: dict[str, list[dict]],
                    livros_tentar: list[str] | None = None) -> dict[str, Any]:
    """Consulta um ativo (número, etiqueta ou série) tentando cada Livro na ordem."""
    if not _trava.acquire(timeout=5):
        raise ErroForms("Já existe uma sessão Forms em andamento.")
    capturas: list[str] = []
    cliente: Cliente | None = None
    try:
        usuario, senha = credenciais()
        if not compilado():
            compilar()
        registrar(f"tela virtual: display {Xvfb.garantir()}")
        sessao = Sessao(usuario, senha, registrar)
        sessao.entrar()
        cliente = Cliente(sessao.obter_jnlp(), registrar)
        cliente.iniciar()
        executar_roteiro(cliente, roteiros.get("abrir") or ROTEIROS_PADRAO["abrir"]["passos"],
                         {}, sessao, registrar, capturas)
        dados: dict[str, Any] = {}
        livro_ok = ""
        for livro in (livros_tentar or livros()):
            variaveis = {"criterio": criterio, "livro": livro}
            registrar(f"tentando livro {livro}")
            executar_roteiro(cliente, roteiros.get("localizar_ativo") or ROTEIROS_PADRAO["localizar_ativo"]["passos"],
                             variaveis, sessao, registrar, capturas)
            lido = executar_roteiro(cliente, roteiros.get("ler_ativo") or ROTEIROS_PADRAO["ler_ativo"]["passos"],
                                    variaveis, sessao, registrar, capturas)
            if any(str(v).strip() for v in lido.values()):
                dados = lido
                livro_ok = livro
                break
            if "voltar_localizar" in roteiros:
                executar_roteiro(cliente, roteiros["voltar_localizar"], variaveis, sessao, registrar, capturas)
        if dados and roteiros.get("linhas_origem"):
            origem = executar_roteiro(cliente, roteiros["linhas_origem"],
                                      {"criterio": criterio, "livro": livro_ok}, sessao, registrar, capturas)
            dados["linhas_origem"] = origem
        return {"criterio": criterio, "livro": livro_ok, "dados": dados, "encontrado": bool(dados),
                "capturas": capturas, "eventos": cliente.eventos[-30:], "log_jvm": cliente.log_path.name}
    finally:
        if cliente:
            cliente.encerrar()
        _trava.release()


def ler_json(caminho: Path, padrao: Any) -> Any:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return padrao
