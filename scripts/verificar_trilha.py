#!/usr/bin/env python3
"""Verificação do núcleo da Trilha do Ativo.

Roda contra um banco temporário — não toca no banco do servidor. Serve
para conferir, depois de qualquer alteração no núcleo, que as regras que
sustentam os indicadores continuam valendo:

    python3 scripts/verificar_trilha.py

O núcleo é a única peça do portal cuja falha atrapalha todos os módulos.
Por isso ele tem verificação própria, e os módulos de tela não têm.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Banco descartável e variáveis mínimas: a verificação não pode depender
# de um .env preenchido nem escrever no banco de produção.
_TEMP = tempfile.mkdtemp(prefix="trilha-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"

import db.trilha as db  # noqa: E402
from db.trilha import FISICA, ADMINISTRATIVA, FILA, TRATATIVA, EXTERNO, ADMIN  # noqa: E402
from routers.trilha import (  # noqa: E402
    Calendario, duracao_util, abrir_ativo, mover, encerrar,
    trilha_do_ativo, filas, TrilhaInvalida, _utc,
)

UTC = timezone.utc
falhas: list[str] = []
feitos = 0


def checar(condicao: bool, descricao: str) -> None:
    global feitos
    feitos += 1
    if condicao:
        print(f"  ok   {descricao}")
    else:
        print(f"  FALHA {descricao}")
        falhas.append(descricao)


def esperar_erro(descricao: str, funcao) -> None:
    try:
        funcao()
    except TrilhaInvalida:
        checar(True, descricao)
    else:
        checar(False, f"{descricao} (não recusou)")


def h(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Um instante de 2026-06-DD em UTC (junho de 2026 começa na segunda)."""
    return datetime(2026, 6, dia, hora, minuto, tzinfo=UTC)


# ── 1. Calendário ──────────────────────────────────────────────────
print("\nCalendário de expediente")

util = Calendario({
    "expediente_dias": "0,1,2,3,4", "expediente_inicio": "08:00",
    "expediente_fim": "18:00", "feriados": "2026-06-11", "fuso_horas": "0",
})
corrido = Calendario({"expediente_dias": "", "fuso_horas": "0"})

# 2026-06-08 é uma segunda-feira.
checar(duracao_util(h(8, 9), h(8, 11), util) == 2 * 3600,
       "duas horas dentro do expediente contam duas horas")
checar(duracao_util(h(8, 17), h(9, 9), util) == 2 * 3600,
       "a noite entre dois dias úteis não conta")
checar(duracao_util(h(12, 17), h(15, 9), util) == 2 * 3600,
       "o fim de semana não conta")
checar(duracao_util(h(10, 17), h(12, 9), util) == 2 * 3600,
       "o feriado de 11/06 não conta")
checar(duracao_util(h(8, 6), h(8, 7), util) == 0,
       "antes de abrir o expediente não conta")
checar(duracao_util(h(13, 9), h(13, 17), util) == 0,
       "sábado inteiro não conta")
checar(duracao_util(h(8, 9), h(8, 11), corrido) == 2 * 3600,
       "sem calendário definido o relógio corre direto")
checar(duracao_util(h(12, 17), h(15, 9), corrido) == 64 * 3600,
       "sem calendário o fim de semana conta como tempo corrido")
checar(duracao_util(h(8, 11), h(8, 9), util) == 0,
       "fim antes do início devolve zero, não negativo")

fuso = Calendario({
    "expediente_dias": "0,1,2,3,4", "expediente_inicio": "08:00",
    "expediente_fim": "18:00", "fuso_horas": "-3",
})
# 11h UTC = 08h em Brasília: o expediente abre exatamente aí.
checar(duracao_util(h(8, 9), h(8, 12), fuso) == 3600,
       "o fuso desloca a janela do expediente")

# ── 2. Motor ───────────────────────────────────────────────────────
print("\nMotor de movimentação")

db.init_db()

# O calendário do banco é fixado aqui de propósito: a verificação é do
# motor, não do padrão que veio de fábrica. Fuso zero deixa as contas
# legíveis — os instantes do teste são os mesmos em UTC e em tela.
db.gravar_config({
    "expediente_dias": "0,1,2,3,4", "expediente_inicio": "08:00",
    "expediente_fim": "18:00", "feriados": "", "fuso_horas": "0",
})

with db.SessionLocal() as s:
    ativo = abrir_ativo(s, serial="tst-0001", usuario="raphael",
                        modelo="EF501R", origem="FORNECEDOR")
    s.commit()
    checar(ativo.serial == "TST-0001", "o serial é normalizado em maiúsculas")

with db.SessionLocal() as s:
    esperar_erro("serial repetido é recusado",
                 lambda: abrir_ativo(s, serial="TST-0001", usuario="raphael"))
    s.rollback()

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    mover(s, a, estado="AG_RECEBIMENTO", tipo=FILA, processo="A01",
          quando=h(8, 9))
    mover(s, a, estado="EX_RECEBIMENTO", tipo=TRATATIVA, processo="A01",
          usuario="sergio", quando=h(8, 10))
    s.commit()

with db.SessionLocal() as s:
    abertos = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.ativo_id == ativo.id).all() if i.fim is None]
    checar(len(abertos) == 1, "só um intervalo fica aberto por trilha")
    checar(abertos[0].estado == "EX_RECEBIMENTO", "o aberto é o último estado")
    fechado = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.ativo_id == ativo.id).all() if i.fim][0]
    checar(_utc(fechado.fim) == h(8, 10),
           "o intervalo anterior fecha na hora da movimentação")
    checar(s.get(db.Ativo, ativo.id).estado_fisico == "EX_RECEBIMENTO",
           "o estado corrente do ativo acompanha a movimentação")

# Fila não tem dono; tratativa tem.
with db.SessionLocal() as s:
    fila = [i for i in s.query(db.Intervalo)
            .filter(db.Intervalo.estado == "AG_RECEBIMENTO").all()][0]
    trat = [i for i in s.query(db.Intervalo)
            .filter(db.Intervalo.estado == "EX_RECEBIMENTO").all()][0]
    checar(fila.usuario == "", "intervalo de fila não fica no nome de ninguém")
    checar(trat.usuario == "sergio", "intervalo de tratativa registra quem assumiu")

# ── 3. Sessões: pausar e retomar ───────────────────────────────────
print("\nSessões (pausa e retomada)")

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    mover(s, a, estado="EX_REPARO", tipo=TRATATIVA, processo="A02",
          usuario="willian", quando=h(8, 11))
    mover(s, a, estado="AG_PECAS", tipo=EXTERNO, processo="A02",
          quando=h(8, 12))
    mover(s, a, estado="EX_REPARO", tipo=TRATATIVA, processo="A02",
          usuario="willian", quando=h(9, 9))
    mover(s, a, estado="AG_INTERNALIZACAO", tipo=FILA, processo="A08",
          quando=h(9, 10))
    s.commit()

with db.SessionLocal() as s:
    reparos = sorted(
        [i for i in s.query(db.Intervalo)
         .filter(db.Intervalo.ativo_id == ativo.id,
                 db.Intervalo.estado == "EX_REPARO").all()],
        key=lambda i: i.sessao)
    checar(len(reparos) == 2, "a retomada cria uma sessão nova, não reabre a antiga")
    checar([r.sessao for r in reparos] == [1, 2], "as sessões são numeradas em ordem")

dados = trilha_do_ativo("TST-0001")
por_estado = {e["estado"]: e["segundos_uteis"] for e in dados["por_estado"]}
checar(por_estado["EX_REPARO"] == 2 * 3600,
       "o tempo do reparo é a soma das duas sessões, sem a espera de peça")
# A espera vai das 12h do dia 8 às 9h do dia 9: 6h no primeiro dia
# (até fechar às 18h) + 1h no segundo (a partir das 8h). A noite entre
# eles não conta, e nada disso entra no relógio do reparo.
checar(por_estado["AG_PECAS"] == 7 * 3600,
       "a espera de peça é contada à parte, como tempo externo")
checar(len(dados["movimentacoes"]) == 6, "toda transição virou movimentação")

# ── 4. Duas trilhas ao mesmo tempo ─────────────────────────────────
print("\nTrilhas em paralelo")

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    mover(s, a, estado="AG_ABERTURA_CHAMADO", tipo=FILA, processo="A08.1",
          trilha=ADMINISTRATIVA, quando=h(9, 10))
    s.commit()

with db.SessionLocal() as s:
    abertos = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.ativo_id == ativo.id).all() if i.fim is None]
    checar(len(abertos) == 2,
           "física e administrativa correm juntas, uma aberta em cada")
    checar({i.trilha for i in abertos} == {FISICA, ADMINISTRATIVA},
           "uma aberta em cada trilha, não duas na mesma")

# ── 5. Travas ──────────────────────────────────────────────────────
print("\nTravas do núcleo")

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    esperar_erro("tratativa sem usuário é recusada",
                 lambda: mover(s, a, estado="EX_ALGO", tipo=TRATATIVA))
    esperar_erro("correção manual sem justificativa é recusada",
                 lambda: mover(s, a, estado="EX_ALGO", tipo=FILA, origem=ADMIN))
    esperar_erro("tipo de intervalo desconhecido é recusado",
                 lambda: mover(s, a, estado="EX_ALGO", tipo="INVENTADO"))
    esperar_erro("trilha desconhecida é recusada",
                 lambda: mover(s, a, estado="EX_ALGO", tipo=FILA, trilha="OUTRA"))
    esperar_erro("estado vazio é recusado",
                 lambda: mover(s, a, estado="  ", tipo=FILA))
    esperar_erro("movimentação para trás no tempo é recusada",
                 lambda: mover(s, a, estado="EX_ALGO", tipo=FILA, quando=h(1, 9)))
    s.rollback()

# Correção manual COM justificativa passa.
with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    mov = mover(s, a, estado="AG_INTERNALIZACAO", tipo=FILA, origem=ADMIN,
                usuario="raphael", justificativa="bipe registrado na bancada errada",
                quando=h(9, 11))
    s.commit()
    checar(mov.justificativa != "", "correção com justificativa é aceita e guardada")

# ── 6. Encerramento ────────────────────────────────────────────────
print("\nEncerramento")

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    encerrar(s, a, estado="ENTREGUE", processo="A15", quando=h(9, 12))
    s.commit()

with db.SessionLocal() as s:
    a = s.get(db.Ativo, ativo.id)
    abertos = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.ativo_id == a.id,
                       db.Intervalo.trilha == FISICA).all() if i.fim is None]
    checar(a.encerrado, "o ativo fica marcado como encerrado")
    checar(not abertos, "nenhum relógio da trilha física continua correndo")
    esperar_erro("ativo encerrado não aceita movimentação",
                 lambda: mover(s, a, estado="AG_ALGO", tipo=FILA))
    s.rollback()

# ── 7. Filas ───────────────────────────────────────────────────────
print("\nPainel de filas")

with db.SessionLocal() as s:
    outro = abrir_ativo(s, serial="TST-0002", usuario="raphael")
    mover(s, outro, estado="AG_TRIAGEM", tipo=FILA, processo="A02", quando=h(8, 9))
    s.commit()

linhas = {f["estado"]: f for f in filas()}
checar("AG_TRIAGEM" in linhas, "o estado com relógio correndo aparece na fila")
checar("ENTREGUE" not in linhas, "estado encerrado não aparece na fila")
checar(linhas["AG_TRIAGEM"]["quantidade"] == 1, "a fila conta os ativos parados nela")

# ── 8. Recálculo retroativo ────────────────────────────────────────
print("\nRecálculo ao trocar o calendário")

antes = trilha_do_ativo("TST-0001")["tempo_total_uteis"]
db.gravar_config({"expediente_dias": ""})          # passa a contar corrido
depois = trilha_do_ativo("TST-0001")["tempo_total_uteis"]
db.gravar_config({"expediente_dias": "0,1,2,3,4"})  # volta ao que era
devolta = trilha_do_ativo("TST-0001")["tempo_total_uteis"]
checar(depois > antes,
       "trocar o calendário recalcula o histórico inteiro, sem migrar dado")
checar(devolta == antes, "voltar o calendário devolve o número anterior")

# ── Resultado ──────────────────────────────────────────────────────
print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("\nFalhas:")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("Núcleo íntegro.")
