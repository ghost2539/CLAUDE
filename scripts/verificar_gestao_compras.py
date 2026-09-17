#!/usr/bin/env python3
"""Verificação da ponte HTTP com o módulo Gestão de Compras.

    python3 scripts/verificar_gestao_compras.py

Não há como falar com o módulo de verdade daqui, então ele é REPLICADO: um
stub em PHP com o mesmo contrato de api/auth.php e api/oracle.php (sessão
por cookie, `require_login` que só responde 401 em JSON para XMLHttpRequest,
`{"data"}` / `{"error"}`, os mesmos códigos). Precisa de `php` no PATH.

O que se prova aqui: que o portal fala o protocolo certo, trata sessão
caída, mapeia erro do Oracle vindo de lá, valida parâmetro antes de sair, e
que a senha nunca aparece — nem na resposta, nem no log.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

PHP = shutil.which("php")
if not PHP:
    print("php não encontrado no PATH — esta verificação precisa dele.")
    sys.exit(2)

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP / "cofre")


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORTA = _porta_livre()
os.environ["GESTAO_COMPRAS_URL"] = f"http://127.0.0.1:{PORTA}/gestao_compras/"
os.environ["GESTAO_COMPRAS_TIMEOUT"] = "10"
os.environ["HTTPS_PROXY"] = ""

USUARIO, SENHA = "svc-portal", "senha-do-stub-que-nao-pode-sair"

# ── O stub: mesmo contrato do módulo, sem MySQL/LDAP ────────────────────
def montar_stub(DOC: Path, usuario: str, senha: str) -> Path:
    """Escreve em DOC o api/auth.php e o api/oracle.php do stub. Devolve o
    caminho do registro que o stub escreve (o que chegou a ele)."""
    (DOC / "gestao_compras" / "api").mkdir(parents=True)
    (DOC / "gestao_compras" / "api" / "_comum.php").write_text(r'''<?php
    session_name('VCREPORTS_COMPRAS');
    session_set_cookie_params(['path' => '/gestao_compras/', 'httponly' => true, 'samesite' => 'Lax']);
    session_start();
    function json_response($data, $code = 200) {
        http_response_code($code);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode($data, JSON_UNESCAPED_UNICODE);
        exit;
    }
    // Igual ao functions.php do módulo: 401 em JSON só para XHR; senão, redireciona.
    function require_login() {
        if (empty($_SESSION['logged_in'])) {
            if (!empty($_SERVER['HTTP_X_REQUESTED_WITH']) && $_SERVER['HTTP_X_REQUESTED_WITH'] === 'XMLHttpRequest') {
                json_response(['error' => 'Not authenticated'], 401);
            }
            header('Location: /gestao_compras/');
            exit;
        }
    }
    // Registro do que chegou, para a verificação conferir cabeçalho e parâmetros.
    function registrar($o) { file_put_contents(__DIR__ . '/../../registro.log', json_encode($o) . "\n", FILE_APPEND); }
    ''', encoding="utf-8")

    (DOC / "gestao_compras" / "api" / "auth.php").write_text(r'''<?php
    require_once __DIR__ . '/_comum.php';
    $action = $_GET['action'] ?? $_POST['action'] ?? '';
    switch ($action) {
        case 'login':
            if ($_SERVER['REQUEST_METHOD'] !== 'POST') json_response(['error' => 'Method not allowed'], 405);
            $input = json_decode(file_get_contents('php://input'), true) ?: $_POST;
            $u = trim($input['username'] ?? ''); $p = $input['password'] ?? '';
            registrar(['rota' => 'login', 'usuario' => $u, 'tem_senha' => $p !== '']);
            if ($u === '' || $p === '') json_response(['error' => 'Username and password required'], 400);
            if ($u === 'USUARIO_OK' && $p === 'SENHA_OK') {
                $_SESSION['logged_in'] = true; $_SESSION['user_id'] = 7;
                $_SESSION['username'] = $u; $_SESSION['role'] = 'user';
                json_response(['success' => true, 'user' => ['id' => 7, 'username' => $u, 'role' => 'user']]);
            }
            json_response(['error' => 'Invalid credentials'], 401);
        case 'logout':
            session_destroy(); header('Location: /gestao_compras/'); exit;
        case 'check':
            if (!empty($_SESSION['logged_in'])) json_response(['authenticated' => true,
                'user' => ['id' => 7, 'username' => $_SESSION['username'], 'role' => 'user']]);
            json_response(['authenticated' => false], 401);
        default:
            json_response(['error' => 'Unknown action'], 400);
    }
    '''.replace("USUARIO_OK", usuario).replace("SENHA_OK", senha), encoding="utf-8")

    (DOC / "gestao_compras" / "api" / "oracle.php").write_text(r'''<?php
    require_once __DIR__ . '/_comum.php';
    require_login();
    $action = $_GET['action'] ?? '';
    registrar(['rota' => 'oracle', 'action' => $action, 'get' => $_GET,
               'xhr' => $_SERVER['HTTP_X_REQUESTED_WITH'] ?? '']);
    $po = [['status' => 'APPROVED', 'numero_po' => '4500123 / 1', 'amount_ship' => 1234.5, 'desc_po' => 'COLETOR HF550'],
           ['status' => 'CLOSED',   'numero_po' => '4500124 / 1', 'amount_ship' => 99.0,   'desc_po' => 'SLED RFR900']];
    switch ($action) {
        case 'saldo': case 'po': case 'rc': case 'resumo':
            $project = trim($_GET['project'] ?? '');
            if (!$project) json_response(['error' => 'Project number required'], 400);
            if ($project === 'ERRO') json_response(['error' => 'Oracle error: ORA-01017: invalid username/password; logon denied'], 500);
            if ($action === 'resumo') json_response(['data' => ['saldo_inicial' => 5000, 'comprometido' => 1333.5, 'pago' => 0, 'disponivel' => 3666.5, 'pct_executado' => 26.67, 'nome_projeto' => 'PROJETO TESTE']]);
            json_response(['data' => $project === 'VAZIO' ? [] : $po]);
        case 'busca_po':
            $n = trim($_GET['po'] ?? ''); if (!$n) json_response(['error' => 'PO number required'], 400);
            $line = !empty($_GET['line']) ? (int)$_GET['line'] : null;
            json_response(['data' => [['po_numero' => $n, 'linha' => $line ?? 1, 'fornecedor' => 'ZEBRA']]]);
        case 'acordos':
            json_response(['data' => [['agreement_num' => 'BPA-1', 'days_to_expire' => (int)($_GET['days'] ?? 90)]]]);
        case 'vendors': json_response(['data' => [['vendor_name' => 'ZEBRA'], ['vendor_name' => 'HONEYWELL']]]);
        case 'vendor_items':
            $v = trim($_GET['vendor'] ?? ''); if (!$v) json_response(['error' => 'Vendor name required'], 400);
            json_response(['data' => [['operating_unit' => 'RENNER', 'item_description' => 'ITEM ' . $v]]]);
        case 'catalogo': json_response(['data' => [['item_ebs' => '123', 'descricao' => 'COLETOR']]]);
        case 'forecast':
            $p = trim($_GET['projects'] ?? ''); if (!$p) json_response(['error' => 'Projects required'], 400);
            json_response(['data' => array_map(fn($x) => ['projeto' => $x, 'disponivel' => 1], preg_split('/[,;\s]+/', $p))]);
        default:
            json_response(['error' => 'Unknown action. Available: saldo, po, rc, resumo, acordos, vendors, vendor_items, busca_po, catalogo, forecast'], 400);
    }
    ''', encoding="utf-8")

    return DOC / "registro.log"


DOC = _TMP / "www"
REGISTRO = montar_stub(DOC, USUARIO, SENHA)
servidor = subprocess.Popen([PHP, "-S", f"127.0.0.1:{PORTA}", "-t", str(DOC)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        with socket.create_connection(("127.0.0.1", PORTA), timeout=0.2):
            break
    except OSError:
        time.sleep(0.1)

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def registros() -> list[dict]:
    import json
    if not REGISTRO.exists():
        return []
    return [json.loads(l) for l in REGISTRO.read_text(encoding="utf-8").splitlines() if l.strip()]


try:
    from core import cofre, security  # noqa: E402
    from fastapi.testclient import TestClient  # noqa: E402
    import main  # noqa: E402
    from integracoes import gestao_compras as gc  # noqa: E402

    cliente = TestClient(main.app)
    _, cookie = security.create_session({"username": "verificacao", "is_admin": True, "permission_map": {}})
    cliente.cookies.set("spare_session", cookie)

    print("\n[1] Sem credencial, a tela diz o que falta — e não tenta a rede")
    d = cliente.get("/api/gestao-compras/situacao").json()
    checar(d["url"].endswith("/gestao_compras/"), "a URL do módulo termina com a barra que o PHP espera")
    checar(d["credenciais"]["usuario"] == "" and d["credenciais"]["senha_definida"] is False,
           "sem nada no cofre, usuário e senha aparecem como não definidos")
    checar(set(d["acoes"]) == set(gc.ACOES), "lista as ações que o módulo aceita")
    r = cliente.post("/api/gestao-compras/testar")
    checar(r.status_code == 503 and "GESTAO_COMPRAS_USER" in r.json()["detail"],
           f"testar sem credencial: 503 dizendo qual chave gravar ({r.status_code})")
    checar(not registros(), "e nenhuma chamada chegou ao módulo")

    print("\n[2] Credencial no cofre (pelo loader): login e sessão")
    cofre.definir("GESTAO_COMPRAS_USER", USUARIO)
    cofre.definir("GESTAO_COMPRAS_PASS", SENHA)
    d = cliente.get("/api/gestao-compras/situacao").json()
    checar(d["credenciais"]["usuario"] == USUARIO and d["credenciais"]["usuario_chave"] == "GESTAO_COMPRAS_USER",
           "usuário resolvido, dizendo de qual chave")
    checar(d["credenciais"]["senha_definida"] is True and SENHA not in str(d),
           "senha marcada como definida — e o valor não aparece")
    r = cliente.post("/api/gestao-compras/testar")
    checar(r.status_code == 200 and r.json()["usuario"]["username"] == USUARIO,
           f"login + check no módulo: sessão aberta como {USUARIO} ({r.status_code})")
    reg = registros()
    checar(any(x.get("rota") == "login" and x.get("usuario") == USUARIO and x.get("tem_senha") for x in reg),
           "o módulo recebeu usuário e senha no POST JSON de login")

    print("\n[3] Consultas: o protocolo certo, parâmetro a parâmetro")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "po", "project": "26.0001"})
    d = r.json()
    checar(r.status_code == 200 and d["total"] == 2 and "numero_po" in d["colunas"],
           f"po: linhas e colunas do módulo ({r.status_code})")
    ult = [x for x in registros() if x.get("rota") == "oracle"][-1]
    checar(ult["xhr"] == "XMLHttpRequest", "toda chamada leva X-Requested-With — sem ele o PHP redireciona em vez de 401")
    checar(ult["get"] == {"action": "po", "project": "26.0001"},
           "só os parâmetros da ação vão na query, com o nome que o PHP espera")

    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "resumo", "project": "26.0001"})
    checar(r.status_code == 200 and r.json()["total"] == 1 and r.json()["linhas"][0]["disponivel"] == 3666.5,
           "resumo (objeto único) vira uma linha")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "busca_po", "po": "4500123"})
    ult = [x for x in registros() if x.get("rota") == "oracle"][-1]
    checar(r.status_code == 200 and "line" not in ult["get"],
           "busca_po sem linha: 'line' não vai vazio — o PHP trataria como zero")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "busca_po", "po": "4500123", "line": 2})
    checar(r.json()["linhas"][0]["linha"] == 2, "busca_po com linha: chega inteira")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "acordos", "days": 30})
    checar(r.json()["linhas"][0]["days_to_expire"] == 30, "acordos: days chega")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "vendors"})
    checar(r.json()["total"] == 2, "vendors: sem parâmetro")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "forecast", "projects": "26.1, 26.2"})
    checar(r.json()["total"] == 2, "forecast: lista de projetos")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "po", "project": "VAZIO"})
    checar(r.status_code == 200 and r.json()["total"] == 0, "sem linhas é 200 com total 0, não erro")

    print("\n[4] Erros: barrados aqui ou traduzidos de lá")
    antes = len(registros())
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "po"})
    checar(r.status_code == 422 and "project" in r.json()["detail"],
           "parâmetro obrigatório ausente é recusado antes de sair")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "drop_tudo"})
    checar(r.status_code == 422 and "desconhecida" in r.json()["detail"],
           "ação fora da lista é recusada antes de sair")
    checar(len(registros()) == antes, "e nada disso chegou ao módulo")
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "po", "project": "ERRO"})
    checar(r.status_code == 502 and "ORA-01017" in r.json()["detail"],
           f"erro do Oracle do lado de lá vira 502 com o texto dele ({r.status_code})")

    print("\n[5] Sessão caída do lado de lá: um novo login, sem o usuário perceber")
    import requests as _rq  # noqa: E402
    sess = gc._sessao()
    _rq.get(f"http://127.0.0.1:{PORTA}/gestao_compras/api/auth.php?action=logout",
            cookies=sess.cookies, allow_redirects=False, timeout=5)
    antes = len([x for x in registros() if x.get("rota") == "login"])
    r = cliente.get("/api/gestao-compras/consultar", params={"acao": "po", "project": "26.0001"})
    depois = len([x for x in registros() if x.get("rota") == "login"])
    checar(r.status_code == 200 and r.json()["total"] == 2, "a consulta responde normalmente")
    checar(depois == antes + 1, "porque o portal refez o login uma vez, ao ver o 401")

    print("\n[6] Credencial errada: mensagem clara, sem a senha")
    cofre.definir("GESTAO_COMPRAS_PASS", "errada")
    gc.esquecer_sessao()
    r = cliente.post("/api/gestao-compras/testar")
    checar(r.status_code == 502 and "recusou a credencial" in r.json()["detail"],
           f"login recusado vira erro legível ({r.status_code})")
    checar("errada" not in r.text and SENHA not in r.text, "sem a senha na resposta")
    cofre.definir("GESTAO_COMPRAS_PASS", SENHA)
    gc.esquecer_sessao()

    print("\n[8] Permissão e sigilo")
    anon = TestClient(main.app)
    for caminho in ("/api/gestao-compras/situacao", "/api/gestao-compras/consultar?acao=vendors"):
        checar(anon.get(caminho).status_code in (401, 403), f"{caminho} exige sessão")
    checar(anon.post("/api/gestao-compras/testar").status_code in (401, 403), "testar exige sessão")
    _, c2 = security.create_session({"username": "comum", "is_admin": False, "permission_map": {}})
    comum = TestClient(main.app)
    comum.cookies.set("spare_session", c2)
    checar(comum.get("/api/gestao-compras/situacao").status_code == 403, "sem admin, 403")
    fonte = (RAIZ / "integracoes" / "gestao_compras.py").read_text(encoding="utf-8")
    checar("from core.cofre import obter" in fonte and "os.getenv(" not in fonte and "os.environ[" not in fonte,
           "a credencial passa por core.cofre.obter — pelo loader, nunca por fora")
    checar(SENHA not in REGISTRO.read_text(encoding="utf-8").replace(f'"tem_senha":true', ""),
           "o stub registrou que a senha veio, mas o valor nunca foi escrito em log")

    print("\n[9] As consultas nomeadas do caminho direto são as mesmas do módulo")
    r = cliente.get("/api/ebs-oracle/consultas")
    nomes = {q["nome"]: q for q in r.json()["consultas"]}
    checar(set(nomes) == {"saldo", "po", "rc", "acordos", "vendor_lookup", "vendor_items", "busca_po", "catalogo"},
           "as oito consultas do oracle_helper.py estão semeadas")
    checar(nomes["busca_po"]["binds"] == ["numero_po", "p_line_num"], "binds derivados do SQL (busca_po)")
    checar(nomes["po"]["binds"] == ["p_project_number"], "binds derivados do SQL (po)")
    r = cliente.post("/api/ebs-oracle/consultar", json={"nome": "nao_existe"})
    checar(r.status_code == 422, "consulta nomeada inexistente é recusada")

    print("\n[10] A tela")
    js = (RAIZ / "modulos/parametros.js").read_text(encoding="utf-8")
    for trecho, desc in (("/gestao-compras/situacao", "carrega a situação"),
                         ("/gestao-compras/testar", "tem o botão Testar login"),
                         ("/gestao-compras/consultar", "consulta pelo módulo"),
                         ("data-gc=", "mostra só os campos da ação escolhida"),
                         ("/ebs-oracle/consultas", "oferece as consultas prontas no caminho direto")):
        checar(trecho in js, f"parametros.js {desc}")

    print("\n[11] Módulo fora do ar")
    # A URL é lida na definição de Settings, então não dá para trocá-la em
    # tempo de teste; derrubar o stub reproduz o caso real com fidelidade.
    servidor.terminate(); servidor.wait(timeout=3)
    gc.esquecer_sessao()
    r = cliente.post("/api/gestao-compras/testar")
    checar(r.status_code == 502 and "Sem resposta" in r.json()["detail"],
           f"sem ninguém ouvindo: 502 dizendo que não houve resposta ({r.status_code})")

finally:
    servidor.terminate()
    try:
        servidor.wait(timeout=3)
    except Exception:  # noqa: BLE001
        servidor.kill()

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Ponte com o Gestão de Compras íntegra.")
