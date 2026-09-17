"""Níveis de acesso: uma escolha por módulo, no lugar de cinco caixas.

Liberar alguém exigia marcar até 92 caixas — cinco ações em trinta módulos.
Na prática elas nunca são independentes: quem pode editar precisa ver, quem
administra faz tudo. São um NÍVEL, e a grade de caixas obrigava a
redescobrir isso a cada usuário, com o risco de deixar um "editar" ligado
sem o "visualizar" e a tela abrir quebrada para a pessoa.

O que muda é só a forma de escolher. As colunas do banco continuam as
mesmas (`can_view`, `can_create`, …), e `require_permission` não sabe que
nível existe — então permissão já concedida segue valendo, e nenhuma
checagem do servidor precisou ser reescrita.

A tradução mora aqui, e não na tela, porque dois lugares a fariam: o
formulário e a aplicação de perfil. Em dois lugares elas divergem, e
divergir aqui significa alguém com acesso que a tela jura que não deu.
"""
from __future__ import annotations

# Do menor para o maior. A ordem é significativa: um nível concede tudo o
# que os anteriores concedem.
NIVEIS: tuple[str, ...] = ("nenhum", "consultar", "operar", "administrar")

ROTULOS: dict[str, str] = {
    "nenhum": "Sem acesso",
    "consultar": "Consultar",
    "operar": "Operar",
    "administrar": "Administrar",
}

# Que ações cada nível liga, quando o módulo as oferece. Módulo que não tem
# "export", por exemplo, simplesmente não ganha a chave — a lista do que
# existe vem de config.MODULE_ACTIONS.
_ACOES_DO_NIVEL: dict[str, tuple[str, ...]] = {
    "nenhum": (),
    "consultar": ("view",),
    "operar": ("view", "create", "edit", "export"),
    "administrar": ("view", "create", "edit", "export", "admin"),
}

_TODAS = ("view", "create", "edit", "export", "admin")


def acoes_do_modulo(modulo: str) -> tuple[str, ...]:
    """O que aquele módulo oferece, segundo a configuração do portal."""
    from config import get_settings
    return tuple(getattr(get_settings(), "MODULE_ACTIONS", {}).get(modulo, ("view",)))


def flags_do_nivel(modulo: str, nivel: str) -> dict[str, bool]:
    """Nível → as colunas `can_*`, respeitando o que o módulo oferece."""
    nivel = (nivel or "nenhum").strip().lower()
    if nivel not in _ACOES_DO_NIVEL:
        nivel = "nenhum"
    disponiveis = set(acoes_do_modulo(modulo))
    concedidas = set(_ACOES_DO_NIVEL[nivel]) & disponiveis
    return {f"can_{a}": (a in concedidas) for a in _TODAS}


def nivel_das_flags(modulo: str, flags: dict) -> str:
    """As colunas `can_*` → o nível que a tela mostra.

    Serve para abrir a tela com o que já está gravado, inclusive o que foi
    concedido pela grade antiga de caixas.

    A regra é o MENOR nível que produz exatamente aquelas flags. Parece
    detalhe e não é: há módulo que não oferece as ações do meio — Parâmetros
    só tem "visualizar" e "administrar" —, e ali "Consultar" e "Operar"
    geram o mesmo resultado. Escolher o maior faria a tela dizer "Operar"
    para quem só pode consultar: exagerar o acesso na tela é pior que
    mostrar de menos, porque quem revisa confia no que está escrito.

    Combinação fora do padrão (editar sem exportar, feita na grade antiga)
    não casa com nível nenhum; aí vale o maior nível inteiramente contido,
    que é o que a pessoa de fato tem.
    """
    if not flags or not flags.get("can_view"):
        return "nenhum"
    for nivel in NIVEIS:
        if flags_do_nivel(modulo, nivel) == {k: bool(flags.get(k))
                                             for k in flags_do_nivel(modulo, nivel)}:
            return nivel
    disponiveis = set(acoes_do_modulo(modulo))
    escolhido = "nenhum"
    for nivel in NIVEIS:
        precisa = set(_ACOES_DO_NIVEL[nivel]) & disponiveis
        if precisa and all(flags.get(f"can_{a}") for a in precisa):
            escolhido = nivel
    return escolhido


def mapa_de_niveis(niveis: dict) -> dict[str, dict[str, bool]]:
    """{modulo: nivel} → {modulo: {can_*}}, pronto para gravar."""
    saida: dict[str, dict[str, bool]] = {}
    for modulo, nivel in (niveis or {}).items():
        flags = flags_do_nivel(str(modulo), str(nivel))
        if flags.get("can_view"):
            saida[str(modulo)] = flags
    return saida


def niveis_do_mapa(permission_map: dict) -> dict[str, str]:
    """{modulo: {can_*}} → {modulo: nivel}, para a tela abrir."""
    return {m: nivel_das_flags(m, f or {}) for m, f in (permission_map or {}).items()}
