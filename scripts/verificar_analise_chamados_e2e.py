#!/usr/bin/env python3
"""Ensaio de ponta a ponta da análise de chamados, com ServiceNow dublê.

    python3 scripts/verificar_analise_chamados_e2e.py

Prova o caminho inteiro sem tocar o ServiceNow: planilha da área (com
número repetido e linha vazia) → consulta → veredito → Excel com as três
abas. Os três chamados são os casos que a área descreveu.
"""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "/home/user/CLAUDE")
import pandas as pd
import scripts.analisar_chamados as ac

T = Path(tempfile.mkdtemp())
# Planilha como a da área: coluna "Chamado", com repetido e linha vazia.
pd.DataFrame({"Chamado": ["INC001", "INC002", "INC003", "INC001", None],
              "Aberto por": ["a", "b", "c", "a", None]}).to_excel(T / "entrada.xlsx", index=False)

INCIDENTES = {
    "INC001": dict(number="INC001", sys_id="s1", opened_at="2026-03-01 09:00:00",
                   assignment_group="Redes", resolved_at="2026-03-05 12:00:00",
                   closed_at="2026-03-06 12:00:00", state="Closed",
                   category="Hardware", subcategory="Coletor",
                   short_description="coletor com defeito"),
    "INC002": dict(number="INC002", sys_id="s2", opened_at="2026-03-01 09:00:00",
                   assignment_group="Service Desk", resolved_at="2026-03-06 10:00:00",
                   closed_at="", state="Resolved", category="Hardware",
                   subcategory="Impressora", short_description="impressora"),
    "INC003": dict(number="INC003", sys_id="s3", opened_at="2026-03-02 08:00:00",
                   assignment_group="SPARE - Equipamentos", resolved_at="2026-03-02 18:00:00",
                   closed_at="", state="Resolved", category="Hardware",
                   subcategory="Coletor", short_description="troca"),
}
AUDIT = [
    dict(documentkey="s1", oldvalue="SPARE - Equipamentos", newvalue="Redes",
         sys_created_on="2026-03-01 11:00:00", user_name="fulano"),
    dict(documentkey="s2", oldvalue="Service Desk", newvalue="SPARE - Equipamentos",
         sys_created_on="2026-03-02 08:00:00", user_name="fulano"),
    dict(documentkey="s2", oldvalue="SPARE - Equipamentos", newvalue="Service Desk",
         sys_created_on="2026-03-03 15:00:00", user_name="ciclano"),
]
NOTAS = [
    dict(element_id="s2", element="work_notes", sys_created_on="2026-03-03 14:00:00",
         sys_created_by="tecnico.spare",
         value="Coletor TC21 enviado para a loja, serie ABC123456, rastreio AB123456789BR"),
    dict(element_id="s3", element="comments", sys_created_on="2026-03-02 17:00:00",
         sys_created_by="tecnico.spare", value="Impressora ZD421 enviada."),
    dict(element_id="s1", element="work_notes", sys_created_on="2026-03-04 10:00:00",
         sys_created_by="redes", value="Equipamento enviado pela equipe de redes."),
]

def consultar(tabela, query, campos, display=True, limite=100000):
    if tabela == "incident":
        nums = query.split("numberIN")[-1].split(",")
        return [INCIDENTES[n] for n in nums if n in INCIDENTES]
    if tabela == "sys_audit":
        ids = query.split("documentkeyIN")[-1].split(",")
        return [a for a in AUDIT if a["documentkey"] in ids]
    if tabela == "sys_journal_field":
        ids = query.split("element_idIN")[-1].split("^")[0].split(",")
        return [n for n in NOTAS if n["element_id"] in ids]
    return []

ac.consultar = consultar
sys.argv = ["x", str(T / "entrada.xlsx"), "--grupo", "SPARE - Equipamentos",
            "--saida", str(T / "saida.xlsx")]
assert ac.main() == 0

df = pd.read_excel(T / "saida.xlsx", sheet_name="Chamados")
print(df[["Chamado", "Atendido pelo time?", "Entrada na nossa fila (bouncing)",
          "Nossa resolução", "TMA (horas)", "Equipamento enviado", "Rastreio"]].to_string(index=False))
linha = {r["Chamado"]: r for _, r in df.iterrows()}
assert len(df) == 3, len(df)
assert linha["INC001"]["Atendido pelo time?"] == "Não"          # abriu conosco, saiu, não voltou
assert linha["INC002"]["Atendido pelo time?"] == "Sim"          # nasceu fora, passou, enviou
assert str(linha["INC002"]["Entrada na nossa fila (bouncing)"]).startswith("2026-03-02 08:00")
assert linha["INC002"]["TMA (horas)"] == 30.0
assert "TC21" in linha["INC002"]["Equipamento enviado"]
assert linha["INC002"]["Rastreio"] == "AB123456789BR"
assert linha["INC003"]["Atendido pelo time?"] == "Sim"          # nasceu conosco e enviou
assert linha["INC003"]["TMA (horas)"] == 9.0
res = pd.read_excel(T / "saida.xlsx", sheet_name="Resumo")
print("\n" + res.to_string(index=False))
sub = pd.read_excel(T / "saida.xlsx", sheet_name="TMA por subcategoria")
print("\n" + sub.to_string(index=False))
print("\nAnálise de chamados íntegra (ponta a ponta).")
