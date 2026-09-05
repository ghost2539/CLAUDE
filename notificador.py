"""Canal de alertas por e-mail do Portal SPARE.

Envia avisos operacionais (tentativa de acesso não autorizado, falhas de
API/integração/automação) para o e-mail do responsável pela área.

Regras de projeto:
- A configuração fica no banco ISOLADO de monitoramento e é editável em
  Parâmetros → Monitoramento; o ambiente (.env) e o cofre servem apenas de
  padrão inicial.
- A senha do SMTP nunca trafega de volta para a tela: fica cifrada no banco
  (Fernet quando a lib `cryptography` existe, XOR derivado do SESSION_SECRET
  como alternativa) e o cofre tem prioridade sobre ela.
- Nada aqui pode derrubar o portal: todo envio é tolerante a erro e roda em
  thread separada quando disparado por um fluxo de requisição.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import smtplib
import threading
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate

import config as _config_mod
import database_monitoramento as db

_cfg = _config_mod.get_settings()
_log = logging.getLogger("notificador")

# Chaves usadas no cofre (quando disponível) para a senha do relay SMTP.
COFRE_SMTP_USER_KEY = "SMTP_USUARIO"
COFRE_SMTP_PASS_KEY = "SMTP_SENHA"


# ── Cofre / criptografia local ──────────────────────────────────────────
def _secret(nome: str, default: str = "") -> str:
    """Cofre corporativo quando existir; senão variável de ambiente."""
    try:
        from vcreports_secrets import vcreports_secret  # type: ignore
        v = vcreports_secret(nome)
        if v:
            return str(v)
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(nome, default)


def _fernet():
    """Fernet quando a lib estiver sã; None quando faltar ou estiver quebrada.

    O import de `cryptography` pode falhar com PanicException (binding Rust),
    que não é `Exception` — por isso a captura é ampla, preservando apenas
    interrupção e término do processo.
    """
    try:
        from cryptography.fernet import Fernet  # type: ignore
        key = base64.urlsafe_b64encode(hashlib.sha256(_cfg.SESSION_SECRET.encode()).digest())
        return Fernet(key)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException:  # noqa: BLE001 — sem cripto, cai no XOR local
        return None


def _xor(txt: str) -> str:
    k = hashlib.sha256(_cfg.SESSION_SECRET.encode()).digest()
    b = txt.encode("utf-8")
    return base64.b64encode(bytes(c ^ k[i % len(k)] for i, c in enumerate(b))).decode()


def _xor_dec(blob: str) -> str:
    k = hashlib.sha256(_cfg.SESSION_SECRET.encode()).digest()
    b = base64.b64decode(blob.encode())
    return bytes(c ^ k[i % len(k)] for i, c in enumerate(b)).decode("utf-8", "ignore")


def cifrar(txt: str) -> tuple[str, str]:
    f = _fernet()
    if f:
        return "fernet", f.encrypt(txt.encode()).decode()
    return "xor", _xor(txt)


def decifrar(algo: str, blob: str) -> str:
    if not blob:
        return ""
    try:
        if algo == "fernet":
            f = _fernet()
            return f.decrypt(blob.encode()).decode() if f else ""
        if algo == "xor":
            return _xor_dec(blob)
    except Exception:  # noqa: BLE001
        return ""
    return ""


# ── Configuração efetiva ────────────────────────────────────────────────
def config() -> dict:
    """Config do banco, com o ambiente preenchendo o que estiver vazio."""
    c = db.obter_config(db.CFG_ALERTAS)
    c["host"] = c.get("host") or getattr(_cfg, "SMTP_HOST", "")
    c["porta"] = int(c.get("porta") or getattr(_cfg, "SMTP_PORT", 25) or 25)
    c["seguranca"] = c.get("seguranca") or getattr(_cfg, "SMTP_SEGURANCA", "none")
    c["usuario"] = c.get("usuario") or getattr(_cfg, "SMTP_USUARIO", "")
    c["remetente"] = c.get("remetente") or getattr(_cfg, "SMTP_REMETENTE", "")
    c["destinatarios"] = c.get("destinatarios") or getattr(_cfg, "ALERTA_EMAIL_TO", "")
    return c


def config_publica() -> dict:
    """Config para a tela — sem qualquer material de senha."""
    c = config()
    _u, senha, fonte = credenciais(c)   # antes de remover o material de senha
    c.pop("senha_cifrada", None)
    algo = c.pop("senha_algo", "")
    c["senha_definida"] = bool(senha)
    c["senha_fonte"] = fonte
    c["senha_algo"] = algo
    c["cofre_disponivel"] = bool(_secret(COFRE_SMTP_PASS_KEY))
    c["destinos"] = destinatarios(c)
    return c


def credenciais(c: dict | None = None) -> tuple[str, str, str]:
    """(usuário, senha, fonte). O cofre tem prioridade sobre o store cifrado."""
    c = c if c is not None else config()
    do_cofre = _secret(COFRE_SMTP_PASS_KEY)
    if do_cofre:
        return (_secret(COFRE_SMTP_USER_KEY) or c.get("usuario", ""), do_cofre, "cofre")
    senha = decifrar(c.get("senha_algo", ""), c.get("senha_cifrada", ""))
    if senha:
        return (c.get("usuario", ""), senha, "local")
    amb = os.environ.get("SMTP_SENHA", "")
    if amb:
        return (c.get("usuario", ""), amb, "ambiente")
    return (c.get("usuario", ""), "", "nenhuma")


def destinatarios(c: dict | None = None) -> list[str]:
    c = c if c is not None else config()
    bruto = (c.get("destinatarios") or "").replace(";", ",")
    return [x.strip() for x in bruto.split(",") if x.strip()]


# ── Controle de volume (não inundar a caixa de entrada) ─────────────────
_ultimo_envio: dict[str, float] = {}
_janela_hora: list[float] = []
_trava = threading.Lock()


def _pode_enviar(chave: str, c: dict) -> tuple[bool, str]:
    intervalo = max(0, int(c.get("intervalo_min") or 0)) * 60
    teto = max(1, int(c.get("max_por_hora") or 20))
    agora = time.time()
    with _trava:
        _janela_hora[:] = [t for t in _janela_hora if agora - t < 3600]
        if len(_janela_hora) >= teto:
            return False, f"teto de {teto} e-mails/hora atingido"
        anterior = _ultimo_envio.get(chave, 0.0)
        if intervalo and agora - anterior < intervalo:
            return False, f"mesmo assunto enviado há menos de {intervalo // 60} min"
        _ultimo_envio[chave] = agora
        _janela_hora.append(agora)
    return True, ""


# ── Envio ───────────────────────────────────────────────────────────────
def _montar(c: dict, destinos: list[str], assunto: str, texto: str, html: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = formataddr(("Portal SPARE", c.get("remetente") or "portal-spare@localhost"))
    msg["To"] = ", ".join(destinos)
    msg["Date"] = formatdate(localtime=True)
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def enviar(assunto: str, texto: str, html: str = "", chave: str = "",
           ignorar_limite: bool = False) -> tuple[bool, str]:
    """Envia um e-mail agora (síncrono). Retorna (ok, detalhe) e nunca levanta."""
    try:
        c = config()
        if not c.get("ativo") and not ignorar_limite:
            return False, "canal de alertas desativado"
        host = c.get("host") or ""
        if not host:
            return False, "servidor SMTP não configurado"
        destinos = destinatarios(c)
        if not destinos:
            return False, "nenhum destinatário configurado"

        if not ignorar_limite:
            ok, motivo = _pode_enviar(chave or assunto, c)
            if not ok:
                return False, motivo

        porta = int(c.get("porta") or 25)
        seg = (c.get("seguranca") or "none").lower()
        usuario, senha, _fonte = credenciais(c)
        msg = _montar(c, destinos, assunto, texto, html)

        t0 = time.time()
        if seg == "ssl":
            import ssl as _ssl
            with smtplib.SMTP_SSL(host, porta, timeout=20,
                                  context=_ssl.create_default_context()) as srv:
                if usuario and senha:
                    srv.login(usuario, senha)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, porta, timeout=20) as srv:
                srv.ehlo()
                if seg == "starttls":
                    import ssl as _ssl
                    srv.starttls(context=_ssl.create_default_context())
                    srv.ehlo()
                if usuario and senha:
                    srv.login(usuario, senha)
                srv.send_message(msg)
        return True, f"enviado para {', '.join(destinos)} em {int((time.time() - t0) * 1000)} ms"
    except Exception as exc:  # noqa: BLE001 — alerta nunca derruba o chamador
        _log.warning("Falha ao enviar alerta por e-mail: %s", exc)
        return False, f"{type(exc).__name__}: {exc}"


def enviar_async(assunto: str, texto: str, html: str = "", chave: str = "") -> None:
    """Dispara o envio em segundo plano (para uso dentro de requisições)."""
    def _run():
        ok, detalhe = enviar(assunto, texto, html, chave=chave)
        if not ok and "desativado" not in detalhe:
            db.registrar(severidade="alerta", origem="alerta", alvo="e-mail",
                         detalhe=f"{assunto} — não enviado: {detalhe}")
    try:
        threading.Thread(target=_run, name="alerta-email", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


# ── Modelos de mensagem ─────────────────────────────────────────────────
_ESTILO = (
    "font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2937;"
    "line-height:1.5"
)


def _corpo(titulo: str, linhas: list[tuple[str, str]], rodape: str = "") -> tuple[str, str]:
    txt = titulo + "\n" + ("-" * len(titulo)) + "\n"
    for rot, val in linhas:
        txt += f"{rot}: {val}\n"
    if rodape:
        txt += "\n" + rodape + "\n"

    html = f'<div style="{_ESTILO}"><h2 style="margin:0 0 12px;font-size:17px">{titulo}</h2>'
    html += '<table cellpadding="6" cellspacing="0" style="border-collapse:collapse">'
    for rot, val in linhas:
        html += ('<tr><td style="background:#f3f4f6;font-weight:600;white-space:nowrap">'
                 f'{rot}</td><td>{val}</td></tr>')
    html += "</table>"
    if rodape:
        html += f'<p style="margin-top:14px;color:#6b7280;font-size:12px">{rodape}</p>'
    return txt, html + "</div>"


def alertar_acesso_negado(login: str, ip: str, motivo: str, origem: str = "SSO") -> None:
    """Tentativa de acesso com credencial de rede válida, porém sem liberação."""
    c = config()
    if not c.get("ativo") or not c.get("alerta_acesso_negado"):
        return
    quando = db.localnow().strftime("%d/%m/%Y %H:%M:%S")
    texto, html = _corpo(
        "Tentativa de acesso não autorizado — Portal SPARE",
        [("Usuário", login or "—"), ("Origem", origem or "—"), ("IP", ip or "—"),
         ("Quando", quando), ("Motivo", motivo or "—")],
        "A credencial de rede foi validada, mas o usuário não possui liberação. "
        "Libere ou ignore em Parâmetros → Usuários e Permissões.",
    )
    enviar_async(f"[SPARE] Acesso não autorizado — {login}", texto, html,
                 chave=f"acesso:{(login or '').lower()}")


def alertar_falha(origem: str, alvo: str, detalhe: str, usuario: str = "") -> None:
    """Falha operacional (API, integração ou automação)."""
    c = config()
    if not c.get("ativo") or not c.get("alerta_falhas"):
        return
    quando = db.localnow().strftime("%d/%m/%Y %H:%M:%S")
    texto, html = _corpo(
        "Falha registrada — Portal SPARE",
        [("Origem", origem or "—"), ("Alvo", alvo or "—"), ("Usuário", usuario or "—"),
         ("Quando", quando), ("Detalhe", (detalhe or "—")[:500])],
        "Consulte o histórico em Parâmetros → Monitoramento.",
    )
    enviar_async(f"[SPARE] Falha em {origem}: {alvo}"[:120], texto, html,
                 chave=f"falha:{origem}:{alvo}")
