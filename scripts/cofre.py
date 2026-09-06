#!/usr/bin/env python3
"""Gerência do cofre local de segredos.

    python3 scripts/cofre.py listar
    python3 scripts/cofre.py definir DB_SENHA            # pede o valor escondido
    python3 scripts/cofre.py definir DB_SENHA --valor X  # útil em script
    python3 scripts/cofre.py remover DB_SENHA
    python3 scripts/cofre.py conferir                    # permissões + o que falta
    python3 scripts/cofre.py importar-env ARQUIVO        # migra um environment antigo

O `importar-env` é o caminho da migração: lê o `environment` do servidor
antigo, move o que é segredo para o cofre e escreve um `environment` novo em
que a senha vira `@cofre:NOME@`. O arquivo de origem não é alterado.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import cofre  # noqa: E402

# Nomes tratados como segredo ao importar um environment.
PADRAO_SEGREDO = re.compile(
    r"(SENHA|PASSWORD|SECRET|_KEY$|_CHAVE|TOKEN|CREDENTIAL|PASS$)", re.I
)

# Vão para o cofre mesmo sem "cara" de segredo: identificam contas de
# serviço e o administrador do portal. Nome de usuário não é senha, mas
# entrega metade do caminho a quem estiver olhando o arquivo.
SEMPRE_COFRE = {
    "SN_API_USER", "SN_API_USUARIO",
    "SN_AUTOMACAO_USUARIO",
    "INITIAL_ADMIN_LOGIN", "INITIAL_ADMIN_PASSWORD",
    "ORACLE_EBS_USER",
    "EBS_CAPEX_USER",
    "SMTP_USUARIO",
    "CORREIOS_USUARIO", "CORREIOS_CARTOES", "CORREIOS_CONTRATO",
}
# Exceções: têm cara de segredo, mas são só configuração.
NAO_SEGREDO = {"SMTP_SEGURANCA", "EBS_CAPEX_TOKEN_SCHEME", "SESSION_TTL_MINUTES"}

# Proxy do servidor antigo. No servidor novo a saída é direta, e carregar o
# proxy velho faria TODA chamada de API tentar um endereço que não existe
# ali. Não apagamos: comentamos, para o valor não se perder.
PROXIES = {"SN_PROXY", "SN_API_PROXY", "HTTPS_PROXY", "HTTP_PROXY",
           "https_proxy", "http_proxy", "EBS_CAPEX_PROXY", "EBS_CAPEX_FX_PROXY"}

# URLs de conexão: a senha fica embutida e precisa ser extraída.
COM_SENHA_NA_URL = re.compile(r"^(?P<pre>\w[\w+]*://[^:/@]+):(?P<senha>[^@]+)@(?P<pos>.+)$")


def _eh_segredo(nome: str) -> bool:
    if nome in NAO_SEGREDO:
        return False
    return nome in SEMPRE_COFRE or bool(PADRAO_SEGREDO.search(nome))


# Nomes prováveis das chaves no cofre corporativo. Como só dá para LER, a
# única forma de descobrir o nome é tentar — daí a lista de candidatos.
CANDIDATOS: dict[str, list[str]] = {
    "Correios": [
        "CORREIOS_USUARIO", "CORREIOS_CHAVE", "CORREIOS_CARTOES",
        "CORREIOS_CONTRATO", "CORREIOS_DR",
    ],
    "ServiceNow": [
        "SN_API_USER", "SN_API_USUARIO", "SN_USER", "SN_USUARIO",
        "SERVICENOW_USER", "SERVICENOW_USUARIO", "SNOW_USER",
        "SN_API_PASS", "SN_API_PASSWORD", "SN_API_SENHA", "SN_SENHA",
        "SN_PASS", "SN_PASSWORD", "SERVICENOW_SENHA", "SERVICENOW_PASSWORD",
        "SNOW_SENHA", "SNOW_PASSWORD",
        "SN_TOKEN", "SERVICENOW_TOKEN",
    ],
    "EBS / Oracle": [
        "ORACLE_EBS_USER", "ORACLE_EBS_USUARIO", "ORACLE_EBS_PASS",
        "ORACLE_EBS_SENHA", "EBS_USER", "EBS_USUARIO", "EBS_SENHA",
        "EBS_PASSWORD", "EBS_CAPEX_USER", "EBS_CAPEX_PASS", "EBS_CAPEX_TOKEN",
    ],
    "E-mail": ["SMTP_USUARIO", "SMTP_USER", "SMTP_SENHA", "SMTP_PASSWORD"],
    "Banco": ["DB_SENHA", "DB_PASSWORD", "PORTAL_DB_SENHA", "DATABASE_PASSWORD"],
}


def cmd_sondar(args) -> int:
    """Descobre QUAIS chaves o cofre corporativo responde.

    Só leitura, e o valor nunca é exibido — apenas se existe e o tamanho,
    que basta para reconhecer a chave certa sem expor nada.
    """
    if not cofre.corporativo_disponivel():
        print("Cofre corporativo (vcreports_secrets) indisponível neste servidor.")
        return 1

    grupos = dict(CANDIDATOS)
    if args.nome:
        grupos = {"Informados por você": list(args.nome)}

    print("Sondando o cofre corporativo — só leitura, valores não são exibidos.\n")
    achadas: list[str] = []
    for grupo, nomes in grupos.items():
        print(f"{grupo}:")
        alguma = False
        for nome in nomes:
            valor = cofre._corporativo(nome)
            if valor:
                achadas.append(nome)
                alguma = True
                print(f"  [existe]  {nome:28s} ({len(valor)} caracteres)")
            elif args.tudo:
                print(f"  [   -  ]  {nome}")
        if not alguma and not args.tudo:
            print("  (nenhuma das tentativas respondeu)")
        print()

    if achadas:
        print("Para usar uma dessas, aponte o marcador no environment:")
        print(f"  SN_API_PASS=@cofre:{achadas[-1]}@")
        print("\nO cofre corporativo tem prioridade: onde o nome bater, é ele que responde.")
    else:
        print("Nenhuma chave conhecida respondeu. Peça ao time que mantém o cofre")
        print("a lista de nomes disponíveis e sonde com:")
        print("  python3 scripts/cofre.py sondar NOME1 NOME2 ...")
    return 0


def cmd_listar(_args) -> int:
    nomes = cofre.listar()
    print(f"Cofre local: {cofre.ARQ_COFRE}")
    print(f"Cofre corporativo disponível: {'sim' if cofre.corporativo_disponivel() else 'não'}")
    if not nomes:
        print("\n(vazio)")
        return 0
    print(f"\n{len(nomes)} segredo(s) — valores nunca são exibidos:\n")
    for n in nomes:
        print(f"  {n:32s}  fonte efetiva: {cofre.fonte(n)}")
    return 0


def cmd_definir(args) -> int:
    valor = args.valor
    if valor is None:
        valor = getpass.getpass(f"Valor de {args.nome} (não aparece na tela): ")
        if valor != getpass.getpass("Repita: "):
            print("Os valores não conferem. Nada foi gravado.")
            return 1
    if not valor:
        print("Valor vazio. Nada foi gravado.")
        return 1
    cofre.definir(args.nome, valor)
    print(f"'{args.nome}' gravado no cofre ({cofre.ARQ_COFRE}).")
    if cofre.algoritmo() != "fernet":
        print("AVISO: cifra fraca em uso ('cryptography' não carregou). "
              "Rode 'conferir' para os detalhes.")
    return 0


def cmd_remover(args) -> int:
    if cofre.remover(args.nome):
        print(f"'{args.nome}' removido.")
        return 0
    print(f"'{args.nome}' não estava no cofre.")
    return 1


def cmd_conferir(args) -> int:
    print(f"Diretório do cofre: {cofre.DIR}")
    print(f"  chave:  {cofre.ARQ_CHAVE} {'(existe)' if cofre.ARQ_CHAVE.exists() else '(ainda não criada)'}")
    print(f"  cofre:  {cofre.ARQ_COFRE} {'(existe)' if cofre.ARQ_COFRE.exists() else '(ainda não criado)'}")
    print(f"  cofre corporativo: {'disponível' if cofre.corporativo_disponivel() else 'indisponível neste servidor'}")

    algo = cofre.algoritmo()
    print(f"  cifra em uso: {algo}")
    if algo != "fernet":
        print("    ! A biblioteca 'cryptography' não carregou; o cofre caiu na")
        print("      cifra derivada, mais fraca. Resolva antes de usar em produção:")
        print("      venv/bin/pip install --force-reinstall cryptography")

    ok, problemas = cofre.permissoes_ok()
    print(f"\nPermissões: {'OK — só o dono lê' if ok else 'PROBLEMA'}")
    for p in problemas:
        print(f"  ! {p}")

    envfile = Path(args.env or os.environ.get(
        "PORTAL_ENVFILE", Path.home() / ".config" / "portal-spare" / "environment"))
    if not envfile.is_file():
        print(f"\nArquivo de ambiente não encontrado: {envfile}")
        return 0 if ok else 1

    print(f"\nReferências ao cofre em {envfile}:")
    faltando = []
    achou = False
    for linha in envfile.read_text(encoding="utf-8").splitlines():
        if linha.strip().startswith("#") or "=" not in linha:
            continue
        nome, _, valor = linha.partition("=")
        for ref in cofre.referencias(valor):
            achou = True
            tem = bool(cofre.obter(ref))
            print(f"  {nome.strip():28s} -> @cofre:{ref}@  {'OK' if tem else 'FALTANDO'}")
            if not tem:
                faltando.append(ref)
    if not achou:
        print("  (nenhuma)")

    if faltando:
        print("\nSegredos citados no ambiente e ausentes do cofre:")
        for f in sorted(set(faltando)):
            print(f"  python3 scripts/cofre.py definir {f}")
        return 1
    return 0 if ok else 1


def cmd_importar_env(args) -> int:
    origem = Path(args.arquivo)
    if not origem.is_file():
        print(f"ERRO: não encontrei {origem}")
        return 1

    destino = Path(args.saida) if args.saida else origem.with_name(origem.name + ".sem-segredo")
    saida: list[str] = []
    movidos: list[tuple[str, str]] = []
    neutralizados: list[str] = []

    for linha in origem.read_text(encoding="utf-8", errors="replace").splitlines():
        crua = linha.rstrip("\n")
        if not crua.strip() or crua.lstrip().startswith("#") or "=" not in crua:
            saida.append(crua)
            continue

        nome, _, valor = crua.partition("=")
        nome, valor = nome.strip(), valor.strip().strip('"').strip("'")

        if not valor or "@cofre:" in valor:
            saida.append(crua)
            continue

        if nome in PROXIES:
            saida.append(f"{nome}=")
            saida.append(f"#   ^ vinha como {valor} no servidor antigo.")
            saida.append("#     Zerado: este servidor sai direto para a rede.")
            neutralizados.append(nome)
            continue

        # URL com senha embutida: só a senha sai de linha.
        m = COM_SENHA_NA_URL.match(valor)
        if m and not _eh_segredo(nome):
            chave = f"{nome}_SENHA"
            if not args.simular:
                cofre.definir(chave, m.group("senha"))
            movidos.append((chave, f"senha de {nome}"))
            saida.append(f"{nome}={m.group('pre')}:@cofre:{chave}@@{m.group('pos')}")
            continue

        if _eh_segredo(nome):
            if not args.simular:
                cofre.definir(nome, valor)
            movidos.append((nome, "valor inteiro"))
            saida.append(f"{nome}=@cofre:{nome}@")
            continue

        saida.append(crua)

    cabecalho = [
        "# Ambiente do Portal SPARE — sem segredo em texto claro.",
        "# Cada @cofre:NOME@ é resolvido em tempo de execução pelo cofre",
        "# (corporativo primeiro; senão o local em ~/.config/portal-spare).",
        "#   conferir:  python3 scripts/cofre.py conferir",
        "",
    ]
    texto = "\n".join(cabecalho + saida) + "\n"

    if args.simular:
        print("== SIMULAÇÃO — nada foi gravado ==\n")
        print(texto)
    else:
        destino.write_text(texto, encoding="utf-8")
        destino.chmod(0o600)
        print(f"Ambiente sem segredo escrito em: {destino}")

    if movidos:
        print(f"\n{len(movidos)} segredo(s) {'seriam movidos' if args.simular else 'movidos'} para o cofre:")
        for nome, oque in movidos:
            print(f"  {nome:32s} ({oque})")
    else:
        print("\nNenhum segredo identificado no arquivo de origem.")

    if neutralizados:
        print(f"\n{len(neutralizados)} proxy(s) zerado(s) — este servidor sai direto:")
        for n in neutralizados:
            print(f"  {n}")
        print("  (o valor antigo ficou como comentário, caso precise voltar)")

    if not args.simular:
        print(f"\nO arquivo de origem NÃO foi alterado: {origem}")
        print("Confira o resultado e, quando estiver certo, coloque o novo no lugar:")
        print(f"  cp {destino} ~/.config/portal-spare/environment")
        print(f"  shred -u {origem}   # apaga o antigo, que ainda tem as senhas")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Cofre local de segredos do Portal SPARE.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("listar", help="Nomes guardados (nunca os valores).").set_defaults(fn=cmd_listar)

    p = sub.add_parser("definir", help="Grava ou substitui um segredo.")
    p.add_argument("nome")
    p.add_argument("--valor", help="Evite: fica no histórico do shell.")
    p.set_defaults(fn=cmd_definir)

    p = sub.add_parser("remover", help="Apaga um segredo.")
    p.add_argument("nome")
    p.set_defaults(fn=cmd_remover)

    p = sub.add_parser("conferir", help="Permissões e segredos citados no ambiente.")
    p.add_argument("--env", help="Caminho do environment (padrão: o do usuário).")
    p.set_defaults(fn=cmd_conferir)

    p = sub.add_parser("sondar", help="Descobre quais chaves o cofre corporativo responde.")
    p.add_argument("nome", nargs="*", help="Nomes específicos a testar (padrão: a lista conhecida).")
    p.add_argument("--tudo", action="store_true", help="Mostra também o que não respondeu.")
    p.set_defaults(fn=cmd_sondar)

    p = sub.add_parser("importar-env", help="Migra um environment antigo para o cofre.")
    p.add_argument("arquivo")
    p.add_argument("--saida", help="Onde escrever o ambiente limpo.")
    p.add_argument("--simular", action="store_true", help="Mostra o resultado sem gravar.")
    p.set_defaults(fn=cmd_importar_env)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
