#!/usr/bin/env python3
"""O keep-alive da sessão NOMINAL do ServiceNow.

    python3 scripts/verificar_keepalive_sn.py

O portal guarda os cookies de SSO de quem entrou e, a cada três minutos, faz
uma chamada autenticada ao ServiceNow. Essa chamada é o que reinicia o
relógio de inatividade de lá, e a resposta traz os cookies renovados, que
voltam para a sessão do portal. Se qualquer elo disso falhar, a sessão morre
sozinha.

O que estava errado, e é o que esta verificação passa a sustentar:

*   **Erro de rede virava "ativa".** A intenção era não derrubar o selo por
    um soluço; o efeito era o pior caso — com o ping falhando há horas, nada
    estava sendo renovado, a sessão morria, e o selo continuava VERDE até
    alguma ação falhar. Três estados agora: ativa, expirada e desconhecida.
*   **A reconexão era código morto.** `snReloginModal` existia, estava
    exportada, e ninguém a chamava. Quando a sessão expirava, o keep-alive
    só pintava o selo de vermelho.
*   **Havia DUAS sondas que discordavam.** `/auth/sn-session` batia noutra
    URL, não seguia redirecionamento, tratava erro de rede como expirada —
    e, o principal, NÃO guardava os cookies renovados. Perguntar por ela não
    mantinha nada vivo; ela só parecia um keep-alive.
*   **Não havia como conferir se o keep-alive roda.** A única evidência era
    a ausência de reclamação, e quando a reclamação vinha não havia o que
    olhar.
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
import main  # noqa: E402
import routers.servicenow as sn  # noqa: E402

app = main.app
cliente = TestClient(app)
sid, cookie = security.create_session(
    {"username": "fulano", "is_admin": True, "permission_map": {},
     "sn_cookies": {"JSESSIONID": "sessao-inicial", "glide_user_route": "rota1"}})
cliente.cookies.set("spare_session", cookie)
SESSAO = security.SESSIONS[sid]


# ── ServiceNow de mentira ──────────────────────────────────────────────
class _Resposta:
    def __init__(self, status=200, url="", cookies=None):
        self.status_code, self.url = status, url
        self.history = []
        self._cookies = cookies or {}


class _Sessao:
    """Uma `requests.Session` de mentira, com jar que aceita update()."""

    def __init__(self, resposta=None, erro=None):
        self.cookies = {}
        self.headers = {}
        self._resposta = resposta
        self._erro = erro
        self.verify = None
        self.pedidos = []

    def get(self, url, **kw):
        self.pedidos.append(url)
        if self._erro:
            raise self._erro
        r = self._resposta or _Resposta(200, url)
        # O ServiceNow devolve cookies renovados; o jar fica com eles.
        self.cookies.update(r._cookies)
        return r


def _instalar(resposta=None, erro=None):
    ses = _Sessao(resposta, erro)
    import integracoes.http as http_saida
    http_saida.sessao = lambda *a, **k: ses
    return ses


print("[1] Sessão viva: renova os cookies e diz que renovou")
# É este o ponto do keep-alive. Sem GUARDAR os cookies que voltaram, o
# próximo ping reenvia os antigos e a renovação nunca se acumula.
ses = _instalar(_Resposta(200, f"{sn.SERVICENOW_BASE}/sys_user.do",
                          cookies={"JSESSIONID": "sessao-RENOVADA"}))
d = cliente.get("/api/servicenow/session-status").json()
checar(d["active"] is True and d["estado"] == "ativa", "responde ativa")
checar(SESSAO["sn_cookies"]["JSESSIONID"] == "sessao-RENOVADA",
       "e os cookies renovados FICAM na sessão do portal")
checar(SESSAO["sn_cookies"].get("glide_user_route") == "rota1",
       "sem perder os cookies que o ServiceNow não reenviou")
checar(d["keepalive"]["renovacoes"] == 1 and d["keepalive"]["ultima_renovacao"],
       "com carimbo da renovação, para dar para conferir depois")
checar(ses.pedidos and "sys_user.do" in ses.pedidos[0],
       "a chamada é autenticada de verdade — é ela que reinicia o relógio de lá")

print("\n[2] Sessão expirada: diz expirada, e não renova nada")
antes = dict(SESSAO["sn_cookies"])
_instalar(_Resposta(200, f"{sn.SERVICENOW_BASE}/login.do?redirect=x"))
d2 = cliente.get("/api/servicenow/session-status").json()
checar(d2["active"] is False and d2["estado"] == "expirada",
       "redirecionar para o login é sessão expirada")
checar(SESSAO["sn_cookies"] == antes,
       "e os cookies da tela de login NÃO substituem os bons")
checar(d2["keepalive"]["falhas_seguidas"] == 1, "conta a falha")
_instalar(_Resposta(401, f"{sn.SERVICENOW_BASE}/sys_user.do"))
checar(cliente.get("/api/servicenow/session-status").json()["estado"] == "expirada",
       "401 também é expirada")

print("\n[3] Erro de rede é DESCONHECIDA, nunca ativa")
# O defeito que escondia tudo: com o ping falhando, o selo ficava verde e a
# sessão morria sem que nada na tela indicasse o porquê.
_instalar(erro=OSError("proxy recusou a conexão"))
d3 = cliente.get("/api/servicenow/session-status").json()
checar(d3["estado"] == "desconhecida",
       "o ping que não chega lá não afirma que a sessão está viva")
checar(d3["active"] is False,
       "contraprova: antes isto devolvia active=True e o selo ficava verde")
checar("proxy recusou" in (d3["keepalive"].get("ultima_falha_motivo") or ""),
       "com o motivo guardado, que é o que faltava para investigar")
checar(d3["keepalive"]["falhas_seguidas"] >= 2,
       "e falhas seguidas acumulam — uma é soluço, dez é o keep-alive parado")

print("\n[4] Dá para conferir se o keep-alive está rodando")
_instalar(_Resposta(200, f"{sn.SERVICENOW_BASE}/sys_user.do"))
cliente.get("/api/servicenow/session-status")
diag = cliente.get("/api/servicenow/keepalive-diagnostico").json()
checar(diag["usuario"] == "fulano", "o diagnóstico é do usuário nominal")
checar(diag["tem_cookies"] is True, "diz se há cookies do ServiceNow guardados")
checar(diag["pings"] >= 5 and diag["ultimo_ping"],
       f"conta os pings e carimba o último ({diag['pings']})")
checar(diag["intervalo_esperado_segundos"] == 180,
       "e diz qual o intervalo esperado — pings espaçados demais são ping parado")
checar(diag["falhas_seguidas"] == 0, "zerou as falhas ao voltar a renovar")

print("\n[5] Uma sonda só, e ela renova")
# Havia duas, divergentes, e a de /auth NÃO guardava os cookies renovados.
auth = (RAIZ / "routers" / "auth.py").read_text(encoding="utf-8")
checar("from routers.servicenow import sn_session_status" in auth,
       "/auth/sn-session delega para o keep-alive de verdade")
checar("api/now/table/sys_user" not in auth.split("def sn_relogin")[0]
       or "sn_session_status" in auth,
       "e não tem mais sonda própria discordando da outra")
SESSAO["sn_cookies"] = {"JSESSIONID": "antes-do-apelido"}
_instalar(_Resposta(200, f"{sn.SERVICENOW_BASE}/sys_user.do",
                    cookies={"JSESSIONID": "depois-do-apelido"}))
da = cliente.get("/api/auth/sn-session").json()
checar(da["estado"] == "ativa", "o apelido responde igual")
checar(SESSAO["sn_cookies"]["JSESSIONID"] == "depois-do-apelido",
       "e RENOVA — antes, perguntar por esta rota não mantinha nada vivo")

print("\n[6] Sem cookies, não inventa estado")
SESSAO.pop("sn_cookies", None)
d6 = cliente.get("/api/servicenow/session-status").json()
checar(d6["estado"] == "sem_sessao" and d6["active"] is False,
       "sem cookies é 'sem sessão', não 'expirada' nem 'ativa'")
anon = TestClient(app)
checar(anon.get("/api/servicenow/session-status").status_code in (401, 403),
       "e sem sessão do portal não responde")
checar(anon.get("/api/servicenow/keepalive-diagnostico").status_code in (401, 403),
       "o diagnóstico também exige sessão")

print("\n[7] TLS: a sessão sai de integracoes/http.py")
fonte = (RAIZ / "routers" / "servicenow.py").read_text(encoding="utf-8")
trecho = fonte[fonte.index("def sn_session_status"):fonte.index("def sn_keepalive_diagnostico")]
checar("verify=False" not in trecho and "sess.verify = False" not in trecho,
       "o keep-alive não desliga a verificação de certificado")
checar("http_saida.sessao(" in trecho,
       "e a sessão HTTP vem de onde o TLS é decidido num lugar só")

print("\n[8] A tela mostra os três estados e pede a reconexão")
js = (RAIZ / "js" / "app.js").read_text(encoding="utf-8")
checar("desconhecida: 'orange'" in js or "desconhecida" in js,
       "o selo tem um estado para 'não consegui perguntar'")
checar("dot-orange" in js or "'orange'" in js,
       "que não é verde — verde ali era o defeito")
checar("pedirReconexaoSN" in js and "snReloginModal()" in js,
       "e a reconexão saiu de código morto: é chamada quando a sessão expira")
checar("d.estado === 'expirada'" in js,
       "no estado certo — não a cada erro de rede, que pediria senha à toa")
checar("_snPedindoSenha" in js,
       "e uma vez só: sem empilhar janelas de senha a cada ping")
checar("ka.ultimo_ping" in js and "ka.ultima_falha_motivo" in js,
       "o título do selo carrega o diagnóstico, sem abrir ferramenta nenhuma")
checar("setInterval(snPing, 3 * 60 * 1000)" in js, "o ping é de 3 em 3 minutos")
checar("visibilitychange" in js and "'online'" in js,
       "e também ao voltar para a aba e ao voltar a rede — aba em segundo "
       "plano tem temporizador represado, e máquina suspensa não conta tempo")

print("\n[9] Sessão na memória do processo: o acoplamento está dito")
checar(bool(security.avisar_se_multiprocesso(4)),
       "WORKERS>1 com sessão em memória gera aviso")
checar(security.avisar_se_multiprocesso(1) == "", "e WORKERS=1 não")
principal = (RAIZ / "main.py").read_text(encoding="utf-8")
checar("avisar_se_multiprocesso" in principal, "o aviso sai antes de subir o servidor")
# É a causa de 'expirou' que não tem nada a ver com relógio: com dois
# workers a sessão some de forma intermitente, que é o pior jeito de falhar.
#
# A lista das units é DESCOBERTA, não escrita à mão. Com os nomes fixos,
# apagar uma delas derrubava esta verificação com FileNotFoundError — que
# não é "a regra foi violada", é "o teste quebrou", e some no meio de um
# traceback. E uma unit NOVA passava despercebida, que é o erro mais caro
# dos dois: o arquivo que ninguém confere é justamente o que sobe com
# --workers 4.
unidades = sorted((RAIZ / "deploy").glob("*.service"))
checar(bool(unidades), "há ao menos uma unit de systemd em deploy/ para conferir")
for unidade in unidades:
    texto = unidade.read_text(encoding="utf-8")
    checar("--workers 1" in texto,
           f"deploy/{unidade.name} sobe com um worker só")

# Os .service não são as únicas formas de subir o portal: `portal.sh` sobe
# em nohup e ainda GERA uma unit de usuário, escrevendo o ExecStart na hora.
# Esses pontos montam `--workers` a partir do ambiente, então conferir só os
# arquivos .service deixa de fora justamente o caminho que alguém pode
# apontar para 4 sem trocar nenhum arquivo versionado.
#
# O que se exige aqui não é literal 1 — é que o valor do ambiente tenha
# DEFAULT 1. `${WORKERS:-1}` passa; `${WORKERS}` sem default, ou um número
# maior escrito à mão, não.
_WORKERS = re.compile(r"--workers[= ]+(\S+)")
_ACEITOS = ("1", '"1"', "${WORKERS:-1}", '"${WORKERS:-1}"')
for script in sorted((RAIZ / "deploy").glob("*.sh")):
    for valor in _WORKERS.findall(script.read_text(encoding="utf-8")):
        checar(valor in _ACEITOS,
               f"deploy/{script.name}: --workers {valor} nasce com um worker só")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Keep-alive do ServiceNow íntegro.")
