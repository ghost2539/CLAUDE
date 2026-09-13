#!/usr/bin/env python3
"""Verificação do motor de Atendimento a Chamados (A20).

Roda contra bancos temporários — não toca no servidor:

    python3 scripts/verificar_atendimento.py

O atendimento repete a mecânica de sessão do núcleo em outro token. É
justamente onde as duas podem divergir com o tempo, então o que vale
para o ativo é conferido aqui de novo para o chamado.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

_TEMP = tempfile.mkdtemp(prefix="atendimento-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["TRILHA_DATABASE_URL"] = f"sqlite:///{_TEMP}/trilha.db"
os.environ["ATENDIMENTO_DATABASE_URL"] = f"sqlite:///{_TEMP}/atendimento.db"

import db.trilha as dbt  # noqa: E402
import db.atendimento as db  # noqa: E402
from db.atendimento import (  # noqa: E402
    LOJA, FROTA, AG_ATENDIMENTO, EX_ATENDIMENTO, AG_TERCEIRO,
    AG_EQUIPAMENTO, RESOLVIDO, FILA, TRATATIVA, EXTERNO, REMESSA,
)
from routers.atendimento import (  # noqa: E402
    mover, equipamento_disponivel, _vincular, _frente_da_categoria,
    AtendimentoInvalido,
)
from routers.trilha import Calendario, duracao_util, _utc  # noqa: E402

UTC = timezone.utc
falhas: list[str] = []
feitos = 0


def checar(cond: bool, descricao: str) -> None:
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + descricao)
    if not cond:
        falhas.append(descricao)


def esperar_erro(descricao: str, funcao) -> None:
    try:
        funcao()
    except AtendimentoInvalido:
        checar(True, descricao)
    else:
        checar(False, f"{descricao} (não recusou)")


def h(dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 6, dia, hora, minuto, tzinfo=UTC)


dbt.init_db()
db.init_db()
dbt.gravar_config({
    "expediente_dias": "0,1,2,3,4", "expediente_inicio": "08:00",
    "expediente_fim": "18:00", "feriados": "", "fuso_horas": "0",
})
CAL = Calendario(dbt.ler_config())


def tempos(chamado_id: int) -> dict:
    fora = {"total": 0, FILA: 0, TRATATIVA: 0, EXTERNO: 0}
    with db.SessionLocal() as s:
        for i in s.query(db.Intervalo).filter(
                db.Intervalo.chamado_id == chamado_id).all():
            seg = duracao_util(_utc(i.inicio), _utc(i.fim), CAL, h(30, 12))
            fora["total"] += seg
            fora[i.tipo] += seg
    return fora


# ── 1. Roteamento por frente ───────────────────────────────────────
print("\nRoteamento por frente")
checar(_frente_da_categoria("Hardware", "Coletor sem leitura") == FROTA,
       "coletor cai na frota mesmo vindo por categoria genérica")
checar(_frente_da_categoria("Mobilidade", "") == FROTA,
       "a categoria de mobilidade cai na frota")
checar(_frente_da_categoria("Impressora fiscal", "PDV travando") == LOJA,
       "o que não é frota cai na loja")
checar(_frente_da_categoria("", "") == LOJA,
       "sem categoria nem resumo, cai na loja")

# ── 2. Ciclo do atendimento ────────────────────────────────────────
print("\nCiclo do atendimento")

with db.SessionLocal() as s:
    ch = db.Chamado(numero="INC0000001", tipo="INC", frente=LOJA,
                    resumo="Coletores do caixa 3 sem leitura",
                    aberto_em=h(8, 9))
    s.add(ch)
    s.flush()
    mover(s, ch, estado=AG_ATENDIMENTO, quando=h(8, 9))
    mover(s, ch, estado=EX_ATENDIMENTO, usuario="leandro", quando=h(8, 10))
    s.commit()
    cid = ch.id

with db.SessionLocal() as s:
    abertos = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.chamado_id == cid).all() if i.fim is None]
    checar(len(abertos) == 1, "só um relógio corre por chamado")
    checar(abertos[0].estado == EX_ATENDIMENTO, "o aberto é o último estado")
    checar(s.get(db.Chamado, cid).atendente == "leandro",
           "assumir grava quem assumiu")

# ── 3. Pausas não contam contra o atendente ────────────────────────
print("\nPausas")

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    mover(s, ch, estado=AG_TERCEIRO, usuario="leandro",
          motivo="peça com o fornecedor", quando=h(8, 11))
    mover(s, ch, estado=EX_ATENDIMENTO, usuario="leandro", quando=h(9, 9))
    s.commit()

t = tempos(cid)
# 08/06 é segunda. Fila 09→10 = 1h. Atendimento 10→11 = 1h.
# Espera externa: 11h→18h do dia 8 (7h) mais 8h→9h do dia 9 (1h) = 8h.
checar(t[TRATATIVA] > 0 and t[EXTERNO] == 8 * 3600,
       "a espera de terceiro é contada à parte, como tempo externo")
checar(t[FILA] == 3600, "a fila antes de alguém assumir é contada")

with db.SessionLocal() as s:
    sessoes = sorted(i.sessao for i in s.query(db.Intervalo)
                     .filter(db.Intervalo.chamado_id == cid,
                             db.Intervalo.estado == EX_ATENDIMENTO).all())
    checar(sessoes == [1, 2], "retomar abre sessão nova, não reabre a antiga")

# ── 4. Aguardando equipamento ──────────────────────────────────────
print("\nEspera por equipamento")

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    _vincular(s, ch, REMESSA, "SEP-2026-0001", "leandro")
    mover(s, ch, estado=AG_EQUIPAMENTO, usuario="leandro",
          motivo="Separação SEP-2026-0001", quando=h(9, 10))
    s.commit()

antes = tempos(cid)[TRATATIVA]
with db.SessionLocal() as s:
    checar(s.get(db.Chamado, cid).estado == AG_EQUIPAMENTO,
           "o chamado fica aguardando equipamento")

# A separação avisa que enviou.
equipamento_disponivel("SEP-2026-0001", "filipe")

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    checar(ch.estado == AG_ATENDIMENTO,
           "com o equipamento enviado, o chamado volta para a fila")
checar(tempos(cid)[TRATATIVA] == antes,
       "a espera pela separação não entrou no relógio do atendente")

# Gancho não encontra a remessa: não pode explodir nem inventar.
equipamento_disponivel("SEP-2026-9999", "filipe")
with db.SessionLocal() as s:
    checar(s.get(db.Chamado, cid).estado == AG_ATENDIMENTO,
           "aviso de separação desconhecida não muda nada")

# Aviso repetido: o chamado já saiu de AG_EQUIPAMENTO.
equipamento_disponivel("SEP-2026-0001", "filipe")
with db.SessionLocal() as s:
    quantos = s.query(db.Movimentacao).filter(
        db.Movimentacao.chamado_id == cid,
        db.Movimentacao.estado_para == AG_ATENDIMENTO).count()
    checar(quantos == 2, "aviso repetido não movimenta o chamado de novo")

# ── 5. Travas ──────────────────────────────────────────────────────
print("\nTravas")

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    esperar_erro("atender sem usuário é recusado",
                 lambda: mover(s, ch, estado=EX_ATENDIMENTO))
    esperar_erro("estado desconhecido é recusado",
                 lambda: mover(s, ch, estado="INVENTADO"))
    esperar_erro("movimentação para trás no tempo é recusada",
                 lambda: mover(s, ch, estado=EX_ATENDIMENTO, usuario="x",
                               quando=h(1, 9)))
    s.rollback()

# ── 6. Resolução ───────────────────────────────────────────────────
print("\nResolução")

# O gancho da separação abriu o intervalo na hora real (ele não recebe
# data), então daqui em diante os instantes são relativos a agora.
agora = db.utcnow()
with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    mover(s, ch, estado=EX_ATENDIMENTO, usuario="leandro",
          quando=agora + timedelta(minutes=1))
    mover(s, ch, estado=RESOLVIDO, usuario="leandro",
          quando=agora + timedelta(minutes=2))
    s.commit()

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    abertos = [i for i in s.query(db.Intervalo)
               .filter(db.Intervalo.chamado_id == cid).all() if i.fim is None]
    checar(not abertos, "resolver para todos os relógios")
    checar(ch.resolvido_em is not None, "a resolução fica datada")
    esperar_erro("chamado resolvido não aceita movimentação",
                 lambda: mover(s, ch, estado=AG_TERCEIRO, usuario="x"))
    s.rollback()

# ── 7. Vínculo não duplica ─────────────────────────────────────────
print("\nVínculos")

with db.SessionLocal() as s:
    ch = s.get(db.Chamado, cid)
    _vincular(s, ch, REMESSA, "SEP-2026-0001", "leandro")
    _vincular(s, ch, REMESSA, "sep-2026-0001", "outro")
    s.commit()
    quantos = s.query(db.Vinculo).filter(
        db.Vinculo.chamado_id == cid, db.Vinculo.especie == REMESSA).count()
    checar(quantos == 1, "o mesmo vínculo não entra duas vezes")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("\nFalhas:")
    for f in falhas:
        print(f"  - {f}")
    sys.exit(1)
print("Motor de atendimento íntegro.")
