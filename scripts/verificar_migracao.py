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
    # integracoes/ebs_oracle.py e routers/ebs_oracle.py NÃO estão aqui: a
    # leitura da base do EBS é conexão de negócio e existe de propósito.
    # O que se barra dela é o dado de acesso no código — seção [2].
    "docs/EBS_ORACLE_BASE.md": "documento com o catálogo e o endereço da base",
    "scripts/verificar_ebs_consulta.py": "verificação da versão que lia credencial do ambiente",
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


print("\n[2] Endereço e credencial de banco fora do código")
# O portal SE LIGA à base do EBS (integracoes/ebs_oracle.py) — isso é
# funcionalidade, não regressão. O que não pode entrar no repositório é
# DADO DE ACESSO: host, porta/instância, SID, esquema, usuário, senha.
# Tudo isso vem do cofre; aqui fica só o NOME da chave.
#
# Esta checagem existe porque foi exatamente por aqui que vazou antes: o
# módulo trazia DSN e usuário escritos como valor padrão.
DADO_DE_ACESSO = (
    # host:porta/instancia — a forma de um DSN Oracle escrito à mão.
    (r"[\w.-]+:\d{4,5}/[A-Za-z]\w{2,}", "endereço de banco (host:porta/instância)"),
    # Credencial de esquema com valor ao lado, em vez do nome da chave.
    (r"ORACLE_EBS_(USER|PASS|PASSWORD|SENHA|DSN)\s*[:=]\s*[\"'][^\"'\s]+",
     "credencial de banco com valor escrito"),
    (r"\bmakedsn\(\s*[\"'][^\"']+[\"']", "DSN montado com valor literal"),
)
MARCAS_DE_EXEMPLO = ("@cofre:", "ALTERAR_", "127.0.0.1", "localhost",
                     "EXEMPLO", "exemplo", "<", "usuario:senha", "SEU_")
achados: list[str] = []
for p in PRODUTO + pys("scripts", "docs", ".env.example"):
    if p.name in ("verificar_migracao.py", "verificar_ebs_oracle.py"):
        continue   # são estes padrões, escritos para procurar
    try:
        conteudo = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    for n, linha in enumerate(conteudo.splitlines(), 1):
        # Exemplo de documentação não é vazamento. O que se procura é
        # endereço REAL colado no repositório; a linha que se anuncia como
        # modelo (marcador do cofre, ALTERAR_, localhost, <...>) sai da
        # conta — senão o .env.example faria a checagem falhar para sempre,
        # e uma checagem que sempre falha ninguém lê.
        if any(marca in linha for marca in MARCAS_DE_EXEMPLO):
            continue
        for padrao, oque in DADO_DE_ACESSO:
            if re.search(padrao, linha):
                achados.append(f"{p.relative_to(RAIZ)}:{n} {oque}")
checar(not achados, f"nenhum dado de acesso a banco escrito no repositório ({achados[:3]})")

# A ligação existe e tem de continuar existindo: é conexão de negócio.
checar((RAIZ / "integracoes" / "ebs_oracle.py").is_file()
       and (RAIZ / "routers" / "ebs_oracle.py").is_file(),
       "a camada de leitura da base do EBS está no lugar")
checar("oracledb" in texto("requirements.txt"),
       "e o driver consta nas dependências")
ebs = texto("integracoes/ebs_oracle.py")
checar("from core.cofre import obter" in ebs and not re.search(r"os\.(environ|getenv)", ebs),
       "a credencial dela vem do cofre, nunca do ambiente direto")
checar("SET TRANSACTION READ ONLY" in ebs and "def sql_livre" not in ebs,
       "só-leitura, e sem SQL livre — a tela roda consulta nomeada")

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


print("\n[12] O catálogo de telas acompanha o menu")
# Documentação envelhece em silêncio: a tela entra no menu e ninguém
# lembra do documento. Aqui o documento é conferido contra o menu de
# verdade, então tela nova sem entrada no catálogo quebra a verificação.
doc = texto("docs/DOCUMENTACAO_SISTEMA.md")
html = texto("static/index.html")

rotas_menu = set(re.findall(r'data-route="([^"]+)"', html))
hrefs_menu = set(re.findall(r'data-href="([^"]+)"', html))
# 'parametros' e 'parametros/conta' são a mesma tela do catálogo.
rotas_menu -= {"parametros/conta"}

faltando = sorted(r for r in rotas_menu if f"#{r}`" not in doc)
checar(not faltando, f"toda rota do menu está no catálogo ({faltando})")
faltando = sorted(h for h in hrefs_menu if f"`{h}`" not in doc)
checar(not faltando, f"toda página do menu está no catálogo ({faltando})")

# As abas de Parâmetros, que é onde as telas novas entraram.
js_param = texto("static/modules/parametros.js")
bloco = js_param[js_param.index("var allTabs = ["):]
bloco = bloco[:bloco.index("];")]
abas = re.findall(r"\['([a-z-]+)',\s*'([^']+)'\]", bloco)
faltando = sorted(rot for chave, rot in abas if rot not in doc)
checar(not faltando, f"toda aba de Configuração está no catálogo ({faltando})")
checar(len(abas) >= 15, f"o catálogo cobre as {len(abas)} abas de Configuração")

# Documento citado que não existe manda quem lê procurar o que não há.
citados = set(re.findall(r"`(docs/[A-Z_]+\.md)`", doc))
sumidos = sorted(c for c in citados if not (RAIZ / c).is_file())
checar(not sumidos, f"todo documento citado existe ({sumidos})")


print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Migração em conformidade: o que foi removido continua removido.")
