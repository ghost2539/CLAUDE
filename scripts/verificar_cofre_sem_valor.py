#!/usr/bin/env python3
"""O Cofre de Segredos diz se a chave existe — e mais nada.

    python3 scripts/verificar_cofre_sem_valor.py

Roda contra um cofre temporário. Sem rede, sem tocar no servidor.

O que estava vazando
--------------------
A aba devolvia, por chave: o TAMANHO do valor, de quais FONTES ele veio, e —
quando o nome não batia com a lista de segredos — o próprio VALOR. "USUARIO"
e "USER" não estavam nessa lista, então `CORREIOS_USUARIO` e
`ORACLE_EBS_USER` saíam por extenso. Usuário de banco é dado de acesso, igual
ao endereço e à senha.

E escondê-lo na tela não resolveria: o JSON vai inteiro para o navegador e
aparece nas ferramentas de desenvolvedor. Por isso a conferência é sobre a
RESPOSTA da API, não sobre o desenho.

`/cofre/tudo` ainda despejava `os.environ` inteiro — o nome de toda variável
do serviço (proxy, banco, caminhos). Isso é mapa de configuração, não
diagnóstico de cofre.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TMP = Path(tempfile.mkdtemp())
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP/'portal.db'}")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-sem-valor")
os.environ["PORTAL_COFRE_DIR"] = str(_TMP)

import routers.cofre as rc  # noqa: E402
import routers.ebs_oracle as reo  # noqa: E402

falhas: list[str] = []
total = 0


def checar(cond: bool, desc: str) -> None:
    global total
    total += 1
    if cond:
        print(f"  ok   {desc}")
    else:
        print(f"  FALHA {desc}")
        falhas.append(desc)


class Req:
    headers: dict = {}
    client = None
    # `_prefixo_em_uso` lê daqui; sem isto o router registra um erro à toa e
    # a saída da verificação fica poluída com traceback que não é do teste.
    scope: dict = {"root_path": ""}


rc._exigir = lambda req: {"username": "verificacao", "is_admin": True}

# Valores plantados que NÃO podem aparecer em resposta nenhuma.
PLANTADOS = {
    "CORREIOS_USUARIO": "usuario-secreto-dos-correios",
    "CORREIOS_CHAVE": "chave-secreta-dos-correios",
    "CORREIOS_CARTOES": "1234567890",
    "ORACLE_EBS_USER": "usuario-do-banco-ebs",
    "ORACLE_EBS_PASS": "senha-do-banco-ebs",
    "ORACLE_EBS_DSN": "servidor-scan.interno:1521/EBSPRD",
    "SN_API_USER": "conta.servico.sn",
}
for nome, valor in PLANTADOS.items():
    os.environ[nome] = valor


def _vaza(payload) -> list[str]:
    """Quais valores plantados aparecem no JSON — em qualquer profundidade."""
    texto = json.dumps(payload, ensure_ascii=False, default=str)
    return [n for n, v in PLANTADOS.items() if v in texto]


print("[1] Sondar uma chave: nome e se foi localizada, só")
item = rc._sondar("CORREIOS_USUARIO")
checar(set(item) == {"chave", "resolvida"},
       f"a resposta tem só chave e resolvida ({sorted(item)})")
checar(item["resolvida"] is True, "a chave que existe é dita como localizada")
checar(rc._sondar("CHAVE_QUE_NAO_EXISTE_XYZ")["resolvida"] is False,
       "a que não existe é dita como não localizada")
for campo in ("tamanho", "valor", "fonte", "fontes_com_valor",
              "no_corporativo", "no_local", "no_ambiente", "sombreado"):
    checar(campo not in item, f"não devolve '{campo}'")

print("\n[2] O diagnóstico inteiro não carrega nenhum valor")
d = rc.diagnostico(Req())
vazados = _vaza(d)
checar(not vazados, f"/cofre/diagnostico sem valor plantado ({vazados or 'nenhum'})")
grupos = {g["nome"]: g for g in d.get("grupos", [])}
chaves_ebs = [k["chave"] for k in grupos.get("Base EBS (Oracle)", {}).get("chaves", [])]
checar("ORACLE_CLIENT_LIB_DIR" not in chaves_ebs,
       f"ORACLE_CLIENT_LIB_DIR saiu da lista ({chaves_ebs})")
checar("ORACLE_EBS_USER" in chaves_ebs, "as chaves que importam continuam listadas")

print("\n[3] /cofre/tudo não despeja o ambiente do processo")
os.environ["UMA_VARIAVEL_QUALQUER_DO_SERVICO"] = "seja-o-que-for"
t = rc.tudo(Req())
nomes = {i["chave"] for i in t["itens"]}
checar("UMA_VARIAVEL_QUALQUER_DO_SERVICO" not in nomes,
       "variável do ambiente que não é do portal não é listada")
checar("CORREIOS_USUARIO" in nomes, "as chaves que o portal procura continuam listadas")
checar(not _vaza(t), "e nenhum valor sai junto")
checar(all(set(i) == {"chave", "resolvida"} for i in t["itens"]),
       "cada item tem só chave e resolvida")

print("\n[4] Base EBS: nem para chave 'não sigilosa'")
checar("ORACLE_CLIENT_LIB_DIR" not in reo.CHAVES,
       f"a chave do Instant Client saiu ({reo.CHAVES})")
checar(reo.PADROES == {},
       f"e com ela o padrão com caminho do servidor ({reo.PADROES})")
situacao = reo._situacao_das_chaves()
checar(not _vaza(situacao), "a situação das chaves não traz valor")
checar(all("valor" not in i for i in situacao),
       "nenhum item tem o campo 'valor'")

print("\n[5] O detector pega o que tem de pegar")
checar(_vaza({"x": "usuario-do-banco-ebs"}) == ["ORACLE_EBS_USER"],
       "o próprio detector acusa um valor plantado")
checar(_vaza({"x": "nada aqui"}) == [], "e não acusa o que não vazou")

print(f"\n{total - len(falhas)} de {total} verificações passaram.")
if falhas:
    print("O Cofre de Segredos ainda conta demais:")
    for f in falhas:
        print("  -", f)
    sys.exit(1)
print("Cofre de Segredos íntegro: diz se a chave existe, e mais nada.")
