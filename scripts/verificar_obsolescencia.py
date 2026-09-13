#!/usr/bin/env python3
"""Verificação da regra de obsolescência (G02), em banco temporário.

    python3 scripts/verificar_obsolescencia.py
"""
from __future__ import annotations
import os, sys, tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_T = tempfile.mkdtemp(prefix="obs-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_T}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local")
os.environ["OBSOLESCENCIA_DATABASE_URL"] = f"sqlite:///{_T}/obs.db"
import db.obsolescencia as db  # noqa: E402
import routers.obsolescencia as ob  # noqa: E402

falhas, feitos = [], 0
def checar(c, d):
    global feitos; feitos += 1
    print(("  ok   " if c else "  FALHA ") + d)
    if not c: falhas.append(d)

assert db.DATABASE_URL.endswith("obs.db"), f"config sem OBSOLESCENCIA_DATABASE_URL: {db.DATABASE_URL}"
db.init_db()
agora = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)

print("\n[1] Datas do MDM (tooltip dd/mm/aaaa) e ISO")
checar(ob.dias_sem_ver("01/08/2026 10:30", agora) == 43, "dd/mm/aaaa HH:MM → 43 dias")
checar(ob.dias_sem_ver("2026-09-10T08:00:00Z", agora) == 3, "ISO → 3 dias")
checar(ob.dias_sem_ver("13/09/2026 11:59", agora) == 0, "hoje → 0")
checar(ob.dias_sem_ver("", agora) is None and ob.dias_sem_ver("lixo", agora) is None, "vazio/lixo → None")

print("\n[2] Android travado")
checar(ob.android_travado("Android 9.0", "TC21", "11", []), "9 < 11 → travado")
checar(not ob.android_travado("13", "TC21", "11", []), "13 ≥ 11 → ok")
checar(ob.android_travado("13", "Bluebird EF501R", "11", ["EF501"]), "modelo sem update → travado")
checar(not ob.android_travado("", "TC21", "11", []), "sem versão → não afirma")

print("\n[3] Regra E/OU e limite de anos configurável")
c = {"modelo": "Bluebird EF500R", "data_aquisicao": (agora - timedelta(days=365*6)).isoformat(), "android_travado": True}
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "todos", 5)
checar(a["obsoleto"] and a["atendidos"] == ["idade_5_anos", "android_travado", "modelo_eol"], "3 critérios → obsoleto (E)")
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "todos", 7)
checar(not a["obsoleto"] and "idade_5_anos" not in a["atendidos"], "limite 7 anos: idade não atende → não obsoleto (E)")
a = ob.avaliar_obsolescencia(c, ["EF500"], agora, "qualquer", 7)
checar(a["obsoleto"], "mesmo caso no OU → obsoleto")

print("\n[4] aplicar_coleta grava visto_em, calcula travado e o resumo conta")
db.gravar_config({"versao_os_minima": "11", "modelos_eol": "EF500", "limite_anos": "5", "modo_regra": "qualquer"})
brutos = [
  {"id": "1", "nome": "A", "usuario": "ljr001_coletor", "modelo": "Bluebird EF500R", "versao_os": "8.1", "visto_em": "01/08/2026 10:00", "tags": []},
  {"id": "2", "nome": "B", "usuario": "ljr001_coletor", "modelo": "Zebra TC21", "versao_os": "13", "visto_em": "13/09/2026 09:00", "tags": []},
  {"id": "3", "nome": "C", "usuario": "ljr002_coletor", "modelo": "Zebra TC21", "versao_os": "10", "visto_em": "", "tags": []},
]
r = ob.aplicar_coleta(brutos, usuario="t")
checar(r["novos"] == 3, "3 coletores gravados")
with db.SessionLocal() as s:
    from sqlalchemy import select
    linhas = {c.mdm_id: c for c in s.execute(select(db.Coletor)).scalars()}
checar(linhas["1"].visto_em is not None and linhas["1"].dias_sem_ver is not None and linhas["1"].dias_sem_ver > 30, "visto_em gravado e dias_sem_ver > 30 para o A")
checar(linhas["1"].obsoleto and linhas["3"].obsoleto and not linhas["2"].obsoleto, "OU: A (EOL+travado) e C (travado) obsoletos; B não")
res = ob.resumo_parque()
checar(res["criterios"]["android_travado"] == 2 and res["criterios"]["modelo_eol"] == 1, "contagem por critério")
checar(res["sem_ver"]["quantidade"] == 1, "1 sem comunicar acima do limite")
checar(any(m["modelo"] == "Zebra TC21" and m["coletores"] == 2 for m in res["por_modelo"]), "por modelo")
checar(any(v["versao"] == "13" for v in res["por_versao_os"]), "por versão de Android")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas)); sys.exit(1)
print("Obsolescência íntegra.")
