#!/usr/bin/env python3
"""Verificação dos símbolos: nada é chamado sem estar definido.

    python3 scripts/verificar_simbolos.py

Esta branch nasceu de um merge entre históricos sem ancestral comum. Nesse
tipo de junção o código que CHAMA vem e a definição fica para trás: o
módulo importa normalmente, o servidor sobe, o `/docs` lista a rota — e o
`NameError` só aparece quando alguém usa a tela. `termo_sn`,
`config_gestao_ativos`, `GESTAO_ATIVOS_PADRAO`, `ler_planilha_entrada`,
`_marcar_existe_sn`, `_campo_item`, `_registro_individual`, `_depreciar`,
`_depreciacao_passos`, `RECEBIMENTO_*_PADRAO`, `MODULO`, `import re` e
`mdm.procurar_detalhado` já entraram assim, um a um, cada um descoberto
em produção. Esta é a rede que faltava para essa classe de defeito.

Como confere, em duas frentes:

1. Dentro do módulo, por `ast` e com ESCOPOS DE VERDADE (módulo, função,
   classe, compreensão, lambda). Sem escopo o ruído afoga o sinal: um
   `for x in ...` seria "x indefinido" e ninguém olharia o relatório de
   novo. O corpo de classe não é escopo léxico para os métodos — é isso
   que faz `A` valer dentro de `class D: A = 1` e não dentro de `D.m`.

2. Entre módulos DO PROJETO: `mdm.procurar_detalhado`, `db.ler_config` e
   companhia. O import passa (o módulo existe), o atributo é que não —
   e quando a chamada está dentro de um `try/except Exception`, como no
   diagnóstico do MDM, o defeito nem estoura: a tela só responde "erro"
   para sempre.

`scripts/` fica de fora de propósito: são verificações, várias escritas
contra outro arranjo do código, e o que elas alcançam já é dito pela
própria execução delas.
"""
from __future__ import annotations

import ast
import builtins
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("routers", "db", "core", "integracoes")

BUILTINS = set(dir(builtins)) | {
    # Dunders que o interpretador injeta em todo módulo.
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__annotations__",
}

# ── Exceções ──────────────────────────────────────────────────────
# Achado que o detector aponta e que NÃO é defeito. Cada linha precisa do
# porquê escrito ao lado: exceção sem motivo vira regra afrouxada, e a
# rede para de pegar o que ela existe para pegar. Formato:
# ("caminho/relativo.py", "nome") — sem número de linha, que muda a cada edição.
EXCECOES_NOMES: set[tuple[str, str]] = set()
# (hoje vazio: a varredura desta branch não deixou nenhum falso positivo)

EXCECOES_ATRIBUTOS: set[tuple[str, str]] = set()
# (idem, para `modulo.atributo` entre módulos do projeto)


FALHAS: list[str] = []
TOTAL = 0


def checar(cond: object, msg: str) -> None:
    global TOTAL
    TOTAL += 1
    print(f"  {'ok  ' if cond else 'FALHA'} {msg}")
    if not cond:
        FALHAS.append(msg)


# ── Escopos ───────────────────────────────────────────────────────
class Escopo:
    __slots__ = ("tipo", "pai", "nomes", "globais", "nonlocais", "usos", "filhos")

    def __init__(self, tipo: str, pai: "Escopo | None"):
        self.tipo = tipo            # modulo | funcao | classe | compreensao
        self.pai = pai
        self.nomes: set[str] = set()
        self.globais: set[str] = set()
        self.nonlocais: set[str] = set()
        self.usos: list[tuple[str, int]] = []
        self.filhos: list[Escopo] = []
        if pai is not None:
            pai.filhos.append(self)


class Construtor(ast.NodeVisitor):
    """Monta a árvore de escopos e anota onde cada nome é LIDO.

    Os vínculos são coletados no escopo inteiro antes de resolver, porque
    em Python uma função pode chamar outra definida mais abaixo — resolver
    na ordem do arquivo acusaria meio projeto.
    """

    def __init__(self):
        self.raiz = Escopo("modulo", None)
        self.atual = self.raiz
        self.estrela = False        # `from x import *`: não dá para afirmar nada

    def _vincular(self, nome: str | None) -> None:
        if nome:
            self.atual.nomes.add(nome)

    def _dentro(self, escopo: Escopo, corpo) -> None:
        antigo, self.atual = self.atual, escopo
        try:
            corpo()
        finally:
            self.atual = antigo

    # -- nomes ------------------------------------------------------
    def visit_Name(self, node):
        if isinstance(node.ctx, ast.Load):
            self.atual.usos.append((node.id, node.lineno))
        else:
            self._vincular(node.id)

    def visit_Global(self, node):
        for n in node.names:
            self.atual.globais.add(n)
            self.raiz.nomes.add(n)

    def visit_Nonlocal(self, node):
        for n in node.names:
            self.atual.nonlocais.add(n)

    # -- imports ----------------------------------------------------
    def visit_Import(self, node):
        for a in node.names:
            self._vincular(a.asname or a.name.split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            if a.name == "*":
                self.estrela = True
            else:
                self._vincular(a.asname or a.name)

    # -- funções, lambdas e classes ---------------------------------
    def _argumentos(self, a: ast.arguments) -> list[ast.arg]:
        return (list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs)
                + [x for x in (a.vararg, a.kwarg) if x])

    def visit_FunctionDef(self, node):
        self._vincular(node.name)
        for d in node.decorator_list:
            self.visit(d)
        a = node.args
        # Padrões e anotações são avaliados FORA da função, no escopo de quem a define.
        for d in list(a.defaults) + [x for x in a.kw_defaults if x]:
            self.visit(d)
        for arg in self._argumentos(a):
            if arg.annotation:
                self.visit(arg.annotation)
        if node.returns:
            self.visit(node.returns)
        esc = Escopo("funcao", self.atual)
        for arg in self._argumentos(a):
            esc.nomes.add(arg.arg)
        self._dentro(esc, lambda: [self.visit(s) for s in node.body])

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        a = node.args
        for d in list(a.defaults) + [x for x in a.kw_defaults if x]:
            self.visit(d)
        esc = Escopo("funcao", self.atual)
        for arg in self._argumentos(a):
            esc.nomes.add(arg.arg)
        self._dentro(esc, lambda: self.visit(node.body))

    def visit_ClassDef(self, node):
        for d in node.decorator_list:
            self.visit(d)
        for b in node.bases:
            self.visit(b)
        for k in node.keywords:
            self.visit(k.value)
        self._vincular(node.name)
        esc = Escopo("classe", self.atual)
        self._dentro(esc, lambda: [self.visit(s) for s in node.body])

    # -- compreensões (escopo próprio desde o Python 3) -------------
    def _compreensao(self, node):
        esc = Escopo("compreensao", self.atual)

        def corpo():
            for g in node.generators:
                self.visit(g.iter)
                self.visit(g.target)
                for c in g.ifs:
                    self.visit(c)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
        self._dentro(esc, corpo)

    visit_ListComp = _compreensao
    visit_SetComp = _compreensao
    visit_GeneratorExp = _compreensao
    visit_DictComp = _compreensao

    # -- demais formas de vincular ----------------------------------
    def visit_ExceptHandler(self, node):
        if node.type:
            self.visit(node.type)
        self._vincular(node.name)
        for s in node.body:
            self.visit(s)

    def visit_NamedExpr(self, node):
        self.visit(node.value)
        # O walrus dentro de compreensão vaza para o escopo de fora.
        alvo = self.atual
        while alvo.tipo == "compreensao" and alvo.pai is not None:
            alvo = alvo.pai
        if isinstance(node.target, ast.Name):
            alvo.nomes.add(node.target.id)

    def visit_MatchAs(self, node):
        if node.pattern:
            self.visit(node.pattern)
        self._vincular(node.name)

    def visit_MatchStar(self, node):
        self._vincular(node.name)

    def visit_MatchMapping(self, node):
        for k in node.keys:
            self.visit(k)
        for p in node.patterns:
            self.visit(p)
        self._vincular(node.rest)


def _resolve(escopo: Escopo, nome: str) -> bool:
    if nome in BUILTINS or nome in escopo.nomes:
        return True
    if nome in escopo.globais or nome in escopo.nonlocais:
        return True
    atual = escopo.pai
    while atual is not None:
        # Corpo de classe não é escopo léxico para o que está dentro dele:
        # `self.ATRIB` funciona, `ATRIB` solto no método não.
        if atual.tipo != "classe":
            if nome in atual.nomes:
                return True
        atual = atual.pai
    return False


def _arvore(caminho: Path) -> Construtor:
    c = Construtor()
    for s in ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho)).body:
        c.visit(s)
    return c


def nomes_indefinidos(caminho: Path) -> list[tuple[str, int]]:
    c = _arvore(caminho)
    if c.estrela:
        return []                   # com `import *` o módulo não declara o que tem
    achados: list[tuple[str, int]] = []
    fila = [c.raiz]
    while fila:
        esc = fila.pop()
        fila.extend(esc.filhos)
        for nome, linha in esc.usos:
            if not _resolve(esc, nome):
                achados.append((nome, linha))
    return sorted(achados, key=lambda t: t[1])


# ── Atributos de módulos do projeto ───────────────────────────────
def _mapa_modulos() -> dict[str, Path]:
    mapa: dict[str, Path] = {p.stem: p for p in RAIZ.glob("*.py")}
    for d in PASTAS:
        for p in (RAIZ / d).rglob("*.py"):
            mapa[f"{d}.{p.stem}"] = p
            if p.stem == "__init__":
                mapa[d] = p
    return mapa


def _nomes_de_topo(p: Path) -> set[str] | None:
    """O que o módulo publica. None quando não dá para afirmar nada."""
    fonte = p.read_text(encoding="utf-8")
    c = _arvore(p)
    if c.estrela or "globals()" in fonte or "setattr(" in fonte:
        return None                 # o módulo monta nomes em tempo de execução
    return c.raiz.nomes | BUILTINS


def _apelidos(p: Path, mapa: dict[str, Path]) -> dict[str, Path]:
    """Nome local -> arquivo do módulo do projeto que ele aponta."""
    fora: dict[str, Path] = {}
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in mapa:
                    fora[a.asname or a.name.split(".")[0]] = mapa[a.name]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                alvo = f"{n.module}.{a.name}"
                if a.name != "*" and alvo in mapa:
                    fora[a.asname or a.name] = mapa[alvo]
    return fora


def atributos_indefinidos(caminho: Path, mapa: dict[str, Path],
                          cache: dict[Path, set[str] | None]) -> list[tuple[str, int]]:
    apelidos = _apelidos(caminho, mapa)
    if not apelidos:
        return []
    achados: list[tuple[str, int]] = []
    for n in ast.walk(ast.parse(caminho.read_text(encoding="utf-8"))):
        if not (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                and isinstance(n.ctx, ast.Load) and n.value.id in apelidos):
            continue
        destino = apelidos[n.value.id]
        if destino not in cache:
            cache[destino] = _nomes_de_topo(destino)
        publicados = cache[destino]
        if publicados is not None and n.attr not in publicados:
            achados.append((f"{n.value.id}.{n.attr}", n.lineno))
    return sorted(achados, key=lambda t: t[1])


# ── Execução ──────────────────────────────────────────────────────
def arquivos() -> list[Path]:
    saida = list(RAIZ.glob("*.py"))
    for d in PASTAS:
        saida += list((RAIZ / d).rglob("*.py"))
    return sorted(set(saida))


def main() -> None:
    alvos = arquivos()
    print(f"[1] Nome chamado sem definição no módulo ({len(alvos)} arquivos)")
    mapa = _mapa_modulos()
    cache: dict[Path, set[str] | None] = {}
    sujos_nomes: list[str] = []
    sujos_atrib: list[str] = []
    for p in alvos:
        rel = str(p.relative_to(RAIZ))
        for nome, linha in nomes_indefinidos(p):
            if (rel, nome) in EXCECOES_NOMES:
                continue
            sujos_nomes.append(f"{rel}:{linha}  {nome}")
        for nome, linha in atributos_indefinidos(p, mapa, cache):
            if (rel, nome) in EXCECOES_ATRIBUTOS:
                continue
            sujos_atrib.append(f"{rel}:{linha}  {nome}")
    for s in sujos_nomes:
        print("       " + s)
    checar(not sujos_nomes,
           f"nenhum nome indefinido ({len(sujos_nomes)} achado(s))")

    print("\n[2] Atributo de módulo do projeto que não existe lá")
    for s in sujos_atrib:
        print("       " + s)
    checar(not sujos_atrib,
           f"nenhum atributo indefinido ({len(sujos_atrib)} achado(s))")

    # O detector só vale se pegar o que ele existe para pegar. Estes casos
    # são os que já queimaram em produção, reduzidos ao essencial.
    print("\n[3] O próprio detector, contra casos plantados")
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="simbolos-"))
    plantado = tmp / "caso.py"
    plantado.write_text(
        "from __future__ import annotations\n"
        "import os\n"
        "CONST = 1\n"
        "def legitimo(a, b=CONST, *args, **kw):\n"
        "    c = a + b\n"
        "    for i in range(3):\n"
        "        c += i\n"
        "    with open(os.sep) as fh:\n"
        "        pass\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as exc:\n"
        "        print(exc, fh)\n"
        "    return [y for y in range(c)], {k: v for k, v in kw.items()}\n"
        "def usa_depois():\n"
        "    return definida_abaixo()\n"
        "def definida_abaixo():\n"
        "    return CONST\n"
        "def fecha():\n"
        "    v = 1\n"
        "    def dentro():\n"
        "        return v\n"
        "    return dentro\n"
        "class C:\n"
        "    ATRIB = 2\n"
        "    def metodo(self):\n"
        "        return self.ATRIB + CONST\n"
        "def com_global():\n"
        "    global NOVO\n"
        "    NOVO = 1\n"
        "    return NOVO\n"
        "def com_walrus(xs):\n"
        "    return [z for x in xs if (z := x)]\n"
        "def ROTA_QUEBRADA():\n"
        "    return ler_planilha_entrada('a.csv', b'')\n"
        "def CLASSE_NAO_E_ESCOPO():\n"
        "    class D:\n"
        "        A = 1\n"
        "        def m(self):\n"
        "            return A\n"
        "    return D\n"
        "def DENTRO_DE_LAMBDA():\n"
        "    return lambda q: q + sumiu_no_merge\n",
        encoding="utf-8")
    pegos = {n for n, _l in nomes_indefinidos(plantado)}
    checar(pegos == {"ler_planilha_entrada", "A", "sumiu_no_merge"},
           f"pega os 3 defeitos plantados e nada além deles (pegou {sorted(pegos)})")

    print(f"\n{TOTAL - len(FALHAS)} de {TOTAL} verificações passaram.")
    if FALHAS:
        print("Falhas:\n  - " + "\n  - ".join(FALHAS))
        print("\nCada linha acima é uma chamada que vira erro na cara do usuário.\n"
              "Ache a definição nas outras branches (`git log --all --oneline -S\"def <nome>\"`)\n"
              "e traga-a — ou, se for falso positivo, anote em EXCECOES_* com o porquê.")
        sys.exit(1)
    print("Nenhum símbolo ficou para trás.")


if __name__ == "__main__":
    main()
