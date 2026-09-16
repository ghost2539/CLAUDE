#!/usr/bin/env python3
"""Verificação da migração oficial: o que entrou, e o que não pode entrar.

    python3 scripts/verificar_migracao.py

Esta branch é a junção de quatro linhas de trabalho que andaram separadas —
`producao`, `migracao`, `desenvolvimento` e a revisão de segurança. Juntar
código é fácil; o difícil é garantir que, no meio da junção, não voltou o
que já tinha sido tirado.

Então aqui não se testa funcionalidade: testa-se a DECISÃO. Cada checagem
abaixo corresponde a uma coisa que alguém já removeu de propósito, e que um
merge desatento traria de volta sem ninguém perceber.

Roda sem subir o portal: é leitura de arquivo e de AST.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Só para poder importar o config; nada é aberto nem gravado.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")

falhas: list[str] = []
feitos = 0


def checar(cond, descricao):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def texto(rel: str) -> str:
    return (RAIZ / rel).read_text(encoding="utf-8")


def pys(*pastas: str) -> list[Path]:
    """Arquivos .py de produto — sem as verificações, que falam do que barram."""
    saida: list[Path] = []
    for pasta in pastas:
        saida.extend(sorted((RAIZ / pasta).rglob("*.py")) if (RAIZ / pasta).is_dir()
                     else [RAIZ / pasta])
    return [p for p in saida if "__pycache__" not in p.parts
            and not p.name.startswith("verificar_")]


PRODUTO = pys("routers", "integracoes", "core", "db", "config.py", "main.py")


print("\n[1] O que ficou para trás não voltou")
# Cada caminho aqui foi removido por um motivo escrito ao lado. Se algum
# reaparecer, é merge trazendo de volta — não decisão nova.
BARRADOS = {
    "integracoes/ebs_oracle.py": "acesso direto ao banco Oracle do EBS",
    "routers/ebs_oracle.py": "endpoints de SQL livre e exploração de catálogo",
    "docs/EBS_ORACLE_BASE.md": "documentação do acesso Oracle",
    "scripts/verificar_ebs_consulta.py": "verificação do acesso Oracle",
    "scripts/cofre_php.php": "carregador de cofre em PHP, executado pelo portal",
    "routers/reparos.py": "router duplicado — a Central de Reparos é routers/bancada.py",
    "docs/MIGRACAO.md": "roteiro de migração de outra branch",
    "docs/DOCUMENTACAO_SISTEMA.html": "documento gerado, fora do repositório",
    "docs/DOCUMENTACAO_SISTEMA.pdf": "documento gerado, fora do repositório",
    "Diretrizes do projeto.md": "duplicata em caixa baixa de 'Diretrizes do Projeto.md'",
    "static/consulta-times": "espelho antigo da tela dos times; aqui é static/modules-times",
}
for caminho, motivo in BARRADOS.items():
    checar(not (RAIZ / caminho).exists(), f"{caminho} continua fora — {motivo}")


print("\n[2] Endereço e credencial de banco não voltaram para o código")
# Duas coisas diferentes, e só a primeira é regra absoluta:
#
#   1. DADO DE ACESSO nunca fica no repositório — host, instância, SID,
#      esquema, usuário, senha. Não importa como o portal chega ao banco:
#      isso vem do cofre ou do ambiente, nunca do git. É por isso que o
#      histórico foi reescrito para tirar o que já tinha vazado.
#
#   2. A ligação DIRETA ao banco (driver, DSN montado no código, SQL
#      solto) foi retirada nesta linha de trabalho: o portal fala com o
#      EBS por HTTP, e quem consulta a base é o módulo /gestao_compras,
#      do lado de lá. Isso é DECISÃO DE ARQUITETURA, não regra de
#      segurança — se um dia a conexão direta voltar a ser necessária, é
#      só tirar a linha do driver daqui; o item 1 continua valendo.
#
# A palavra "Oracle" sozinha não é problema: o SSO é o Oracle Access
# Manager e o EBS é o Oracle E-Business Suite.
LIGACAO_DIRETA = (
    (r"\bimport\s+(cx_Oracle|oracledb)\b", "driver de banco Oracle"),
    (r"\bORACLE_EBS_(USER|PASS|PASSWORD|SENHA|DSN|TNS)\b", "credencial de esquema do EBS"),
    (r"\b(ORACLE|EBS)_\w*(DSN|TNS|SID|SCHEMA)\b", "endereço do banco"),
    (r"\bmakedsn\(|\bconnect\(.*service_name", "abertura de conexão Oracle"),
    (r"\bfrom\s+apps\.\w+", "SQL direto no esquema APPS"),
)
achados: list[str] = []
for p in PRODUTO:
    for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        for padrao, oque in LIGACAO_DIRETA:
            if re.search(padrao, linha):
                achados.append(f"{p.relative_to(RAIZ)}:{n} {oque}")
checar(not achados, f"nenhum endereço nem credencial de banco no código ({achados[:3]})")
req = texto("requirements.txt")
checar("cx_Oracle" not in req and "oracledb" not in req,
       "e o driver Oracle segue fora das dependências (decisão de arquitetura)")


print("\n[3] TLS: verificação ligada, e uma porta só de saída")
for p in PRODUTO:
    conteudo = p.read_text(encoding="utf-8")
    if p.name == "http.py" and p.parent.name == "integracoes":
        continue
    ruim = re.findall(r"verify\s*=\s*False", conteudo)
    if ruim:
        checar(False, f"{p.relative_to(RAIZ)} desliga a verificação de certificado")
    if "disable_warnings" in conteudo:
        checar(False, f"{p.relative_to(RAIZ)} silencia o aviso de TLS por fora de integracoes/http.py")
checar(True, "nenhum verify=False escondido no código de produto")
http = texto("integracoes/http.py")
checar("PORTAL_CA_BUNDLE" in http or "CA_BUNDLE" in http,
       "o caminho para proxy que intercepta TLS é a CA corporativa, não desligar a conferência")
gc = texto("integracoes/gestao_compras.py")
checar("http_saida.sessao(" in gc and "verify=cfg.GESTAO_COMPRAS_VERIFY" not in gc,
       "a ponte com o Gestão de Compras sai pela fábrica de sessões do portal")


print("\n[4] Segredo: cofre ligado, e nada de credencial solta no ambiente")
cofre = texto("core/cofre.py")
checar('os.environ.get("COFRE_CORPORATIVO", "sim")' in cofre,
       "o cofre corporativo continua LIGADO por padrão")
# Credencial lida direto de os.environ contorna a ordem cofre → cofre local
# → ambiente, e some do diagnóstico. Configuração (URL, host, flag) pode.
MARCAS = r"(PASS|PASSWORD|SENHA|CHAVE|SECRET|TOKEN|CARTOES)"
for p in PRODUTO:
    if p.name in ("cofre.py", "config.py"):
        continue
    for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if re.search(rf"os\.(environ\[|getenv\()[\"'][A-Z_]*{MARCAS}", linha):
            checar(False, f"{p.relative_to(RAIZ)}:{n} lê credencial direto do ambiente")
checar(True, "nenhuma integração pega senha fora do cofre")
cfg = texto("config.py")
checar("def _segredo(" in cfg or "from core.cofre import" in cfg or "_env(" in cfg,
       "config.py resolve segredo pelo cofre (marcador @cofre:NOME@)")


print("\n[5] Proxy: variável declarada e vazia é decisão, não 'continue procurando'")
checar("def _proxy(" in cfg, "config.py tem o _proxy() que respeita a variável vazia")
checar(re.search(r"SN_API_PROXY[^=]*=\s*_proxy\(", cfg) is not None
       and re.search(r"GESTAO_COMPRAS_PROXY[^=]*=\s*_proxy\(", cfg) is not None,
       "e os dois proxies de saída passam por ele")
ambiente = texto("deploy/environment.servidor-novo")
checar("HTTPS_PROXY=" in ambiente and "SN_PROXY=" in ambiente,
       "o environment do servidor declara os proxies vazios — é essa decisão que _proxy() honra")


print("\n[6] Nenhum módulo derruba o portal sozinho")
arvore = ast.parse(texto("main.py"))
dentro_de_try: set[str] = set()
todos: set[str] = set()


def nome_do_router(no: ast.Call) -> str:
    if no.args and isinstance(no.args[0], ast.Name):
        return no.args[0].id
    return ""


for no in ast.walk(arvore):
    if (isinstance(no, ast.Call) and isinstance(no.func, ast.Attribute)
            and no.func.attr == "include_router"):
        r = nome_do_router(no)
        if r:
            todos.add(r)
for no in ast.walk(arvore):
    if isinstance(no, ast.Try):
        for filho in ast.walk(no):
            if (isinstance(filho, ast.Call) and isinstance(filho.func, ast.Attribute)
                    and filho.func.attr == "include_router"):
                r = nome_do_router(filho)
                if r:
                    dentro_de_try.add(r)

# A espinha (auth, consulta, recebimento, parâmetros…) não é isolada de
# propósito: sem ela não existe portal para seguir de pé.
OPCIONAIS = {"capex_spare_router", "gestao_compras_router", "agendamentos_forn_router",
             "internalizacao_router", "cofre_router", "orcamento_spare_router",
             "controle_orcamento_exec_router", "indicadores_router", "cockpit_router"}
faltando = sorted(OPCIONAIS - dentro_de_try)
checar(not faltando, f"cada módulo novo registra dentro do próprio try/except ({faltando})")
checar(len(todos) >= len(OPCIONAIS), f"main.py registra {len(todos)} routers")


print("\n[7] Toda tela do menu tem arquivo, e todo arquivo tem tela")
app_js = texto("static/app.js")
bloco = app_js[app_js.index("var ROUTES = {"):]
bloco = bloco[:bloco.index("};")]
rotas = set(re.findall(r"^\s*'?([a-z_]+)'?\s*:", bloco, re.M))
arquivos = {f.stem for f in (RAIZ / "static/modules").glob("*.js")}
# servicenow.js é carregado sob demanda pela tela de Gestão de Ativos.
SOB_DEMANDA = {"servicenow"}
checar(not (rotas - arquivos - {"bemvindo"}),
       f"nenhuma rota sem módulo ({sorted(rotas - arquivos - {'bemvindo'})})")
checar(not (arquivos - rotas - SOB_DEMANDA),
       f"nenhum módulo órfão ({sorted(arquivos - rotas - SOB_DEMANDA)})")
for novo in ("capex_spare", "agendamentos_forn", "internalizacao"):
    checar(novo in rotas and novo in arquivos, f"{novo} está no menu e tem tela")


print("\n[8] Permissão declarada para cada módulo com tela própria")
from config import get_settings  # noqa: E402

acoes = get_settings().MODULE_ACTIONS
for modulo in ("capex_spare", "agendamentos_forn", "internalizacao"):
    checar(modulo in acoes and "view" in acoes[modulo],
           f"{modulo} está em MODULE_ACTIONS com 'view'")
# Gestão de Compras e Cofre moram dentro de Parâmetros: a permissão é a de lá.
for rel in ("routers/gestao_compras.py", "routers/cofre.py"):
    checar('MODULO = "parametros"' in texto(rel),
           f"{rel} usa a permissão de Parâmetros, e não uma chave nova sem tela")


print("\n[9] Banco novo mora em data/db, declarado no config")
for chave, modulo in (("CAPEX_SPARE_DATABASE_URL", "capex_spare"),
                      ("AGENDAMENTOS_FORN_DATABASE_URL", "agendamentos_forn"),
                      ("INTERNALIZACAO_DATABASE_URL", "internalizacao")):
    checar(re.search(rf'{chave}[^=]*=\s*_env\(\s*\n?\s*"{chave}",\s*_sqlite\("{modulo}"\)',
                     cfg, re.M) is not None,
           f"{chave} declarada com _sqlite('{modulo}')")
for rel in ("db/capex_spare.py", "db/agendamentos_forn.py", "db/internalizacao.py"):
    conteudo = texto(rel)
    checar("get_settings()" in conteudo and "sqlite:///" not in conteudo.replace("sqlite:////", ""),
           f"{rel} pega a URL do config, sem caminho escrito no código")


print("\n[10] Padrão visual mantido nas telas que vieram")
for rel in ("static/modules/capex_spare.js", "static/modules/agendamentos_forn.js",
            "static/modules/internalizacao.js", "static/modules/parametros.js"):
    conteudo = texto(rel)
    hex_fixo = re.findall(r"#[0-9a-fA-F]{3,6}\b", conteudo)
    # '#' de seletor e de âncora não conta: só o que parece cor.
    hex_fixo = [h for h in hex_fixo if re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", h)]
    checar(not hex_fixo, f"{rel} sem cor fixa ({hex_fixo[:3]})")
    checar("fonts.googleapis" not in conteudo and "cdn." not in conteudo,
           f"{rel} sem recurso externo")


print("\n[11] Nada GRAVADO fora de data/")
# Ler /proc e /etc é normal (memória, uptime, arquivo de ambiente). O que
# não pode é GRAVAR: o portal escreve dentro da própria pasta data/, para
# que backup, permissão e limpeza tenham um lugar só.
gravacoes: list[str] = []
for p in PRODUTO:
    for n, linha in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        alvo = re.search(r"""(write_text|write_bytes)\(|open\(\s*['"](/[^'"]+)['"]\s*,\s*['"][wax]""",
                         linha)
        if not alvo:
            continue
        absoluto = re.search(r"""['"](/[^'"]+)['"]""", linha)
        if absoluto and not absoluto.group(1).startswith("/tmp/"):
            gravacoes.append(f"{p.relative_to(RAIZ)}:{n} {absoluto.group(1)}")
checar(not gravacoes, f"nenhuma gravação em caminho absoluto fora de data/ ({gravacoes[:3]})")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Migração em conformidade: o que foi removido continua removido.")
