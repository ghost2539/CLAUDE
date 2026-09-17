"""Tira dado de acesso do texto que vai para a tela.

O driver Oracle põe o endereço dentro da própria mensagem de erro. ORA-12154
e ORA-12541 trazem o DSN inteiro; DPY-6003 escreve host, porta e instância
separados, numa frase ("SID ... is not registered with the listener at host
... port 1521"). Repassar essa mensagem crua publica no navegador o que nem
no repositório pode ficar — e publica justamente para quem está vendo o
sistema falhar, que é quando todo mundo copia e cola o texto em chamado.

O código do erro (ORA-xxxxx, DPY-xxxx) fica: é o que diz o que aconteceu, e
sozinho não identifica servidor nenhum. O que sai é o endereço, suas partes
e o usuário. O texto completo continua indo para o log, onde só chega quem
já tem acesso ao servidor.

Fica aqui, e não dentro de um router, porque mais de uma tela conversa com o
banco do EBS: duas cópias desta função significaria consertar uma e deixar a
outra vazando.
"""
from __future__ import annotations

import re

# O que se procura no texto, na ordem. São nomes de chave, não valores.
_CHAVES_ENDERECO = ("ORACLE_EBS_DSN", "EBS_ORACLE_DSN")
_CHAVES_USUARIO = ("ORACLE_EBS_USER", "EBS_ORACLE_USER")

# Pedaço curto demais não se mascara: "1", "db" ou "ab" apareceriam no meio
# de qualquer palavra e a mensagem viraria uma fileira de <omitido>.
_MINIMO = 4


def _valores(chaves: tuple[str, ...]) -> list[str]:
    try:
        from core.cofre import obter
    except Exception:  # noqa: BLE001
        return []
    achados = []
    for nome in chaves:
        v = (obter(nome, "") or "").strip()
        if v:
            achados.append(v)
    return achados


def sem_dado_de_acesso(texto: str) -> str:
    """Devolve o texto com endereço, partes do endereço e usuário trocados."""
    saida = str(texto)
    for dsn in _valores(_CHAVES_ENDERECO):
        saida = saida.replace(dsn, "<endereço do banco>")
        # DPY-6003 não repete o DSN inteiro: ele cita host, porta e instância
        # em pontos diferentes da frase. Por isso as partes vão uma a uma.
        for pedaco in re.split(r"[/:@,()= ]+", dsn):
            if len(pedaco) >= _MINIMO:
                saida = saida.replace(pedaco, "<omitido>")
    for user in _valores(_CHAVES_USUARIO):
        if len(user) >= _MINIMO:
            saida = saida.replace(user, "<usuário>")
            saida = saida.replace(user.upper(), "<usuário>")
            saida = saida.replace(user.lower(), "<usuário>")
    return saida
