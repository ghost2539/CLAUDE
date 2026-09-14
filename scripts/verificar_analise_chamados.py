#!/usr/bin/env python3
"""Verificação da regra de "o time atendeu?" — sem rede, sem planilha.

    python3 scripts/verificar_analise_chamados.py

Os casos aqui são os que a área descreveu: chamado que nasce na nossa
fila e é puxado sem voltar (hoje conta como nosso e não deveria), chamado
que nasce fora, passa por nós e sai (hoje não conta e deveria), e o
chamado que passou pela fila sem envio nenhum.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.chamados_nucleo import (  # noqa: E402
    analisar, achar_envio, achar_equipamento, achar_tipo, e_nossa,
    periodos_de_fila, passagens_do_time, resumir, ler_data,
)

falhas: list[str] = []
feitos = 0


def checar(cond, d):
    global feitos
    feitos += 1
    print(("  ok   " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


def dt(txt):
    return datetime.strptime(txt, "%Y-%m-%d %H:%M:%S")


NOSSOS = ["SPARE - Equipamentos"]


def troca(quando, de, para):
    return {"quando": quando, "de": de, "para": para}


def nota(quando, texto, autor="tecnico", tipo="work_notes"):
    return {"quando": quando, "texto": texto, "autor": autor, "tipo": tipo}


print("\n[1] Nome de fila casa sem depender de acento, caixa ou espaço")
checar(e_nossa("spare - equipamentos", NOSSOS), "mesma fila em caixa baixa")
checar(e_nossa("SPARE  -  Equipamentos", NOSSOS), "espaço sobrando não separa a fila")
checar(not e_nossa("SPARE - Redes", NOSSOS), "outra fila continua sendo outra fila")
checar(not e_nossa("", NOSSOS), "fila vazia não é nossa")

print("\n[2] Linha do tempo das filas sai do histórico")
hist = [troca("2026-03-02 10:00:00", "SPARE - Equipamentos", "Redes"),
        troca("2026-03-03 08:00:00", "Redes", "SPARE - Equipamentos")]
per = periodos_de_fila(hist, dt("2026-03-01 09:00:00"), "SPARE - Equipamentos",
                       dt("2026-03-04 17:00:00"))
checar([p["grupo"] for p in per] == ["SPARE - Equipamentos", "Redes", "SPARE - Equipamentos"],
       f"as três passagens aparecem na ordem ({[p['grupo'] for p in per]})")
checar(per[0]["inicio"] == dt("2026-03-01 09:00:00"), "a primeira passagem começa na abertura")
checar(per[-1]["fim"] == dt("2026-03-04 17:00:00"), "a última fecha na resolução")
checar(len(passagens_do_time(per, NOSSOS)) == 2, "duas passagens são nossas")

sem_hist = periodos_de_fila([], dt("2026-03-01 09:00:00"), "SPARE - Equipamentos", None)
checar(len(sem_hist) == 1 and sem_hist[0]["grupo"] == "SPARE - Equipamentos",
       "sem histórico, o chamado viveu na fila atual")

print("\n[3] O caso que infla o número: abre conosco, sai e não volta")
chamado = {"numero": "INC1", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Coletor", "grupo_atual": "Redes",
           "resolucao": "2026-03-05 12:00:00"}
hist = [troca("2026-03-01 11:00:00", "SPARE - Equipamentos", "Redes")]
r = analisar(chamado, hist, [], NOSSOS)
checar(r["passou_por_nos"] and not r["atendido"],
       "passou pela fila, mas sem envio não é atendimento nosso")
checar(r["grupo_abertura"] == "SPARE - Equipamentos" and r["grupo_final"] == "Redes",
       "o relatório mostra onde nasceu e onde terminou")
checar("não há nota confirmando envio" in r["motivo"], f"o motivo é explícito ({r['motivo']})")

print("\n[4] O caso que ninguém conta: nasce fora, passa por nós, sai")
chamado = {"numero": "INC2", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Impressora", "grupo_atual": "Service Desk",
           "resolucao": "2026-03-06 10:00:00"}
hist = [troca("2026-03-02 08:00:00", "Service Desk", "SPARE - Equipamentos"),
        troca("2026-03-03 15:00:00", "SPARE - Equipamentos", "Service Desk")]
notas = [nota("2026-03-02 09:00:00", "Em análise, separando o equipamento."),
         nota("2026-03-03 14:00:00",
              "Equipamento ZQ521 enviado para a loja, série XPTO123456, "
              "rastreio AB123456789BR.")]
r = analisar(chamado, hist, notas, NOSSOS)
checar(r["atendido"], "com envio registrado, é atendimento nosso")
checar(r["entrada"] == dt("2026-03-02 08:00:00"),
       f"a data de entrada é o bouncing, não a abertura ({r['entrada']})")
checar(r["nossa_resolucao"] == dt("2026-03-03 14:00:00"),
       "a data da nossa resolução é a da nota de envio")
checar(r["base_da_data"] == "nota de envio", "e o relatório diz de onde a data veio")
checar(r["tma_horas"] == 30.0, f"TMA conta do bouncing ao envio ({r['tma_horas']}h)")
checar("ZQ521" in r["equipamento"] and "XPTO123456" in r["equipamento"],
       f"modelo e série saem da nota ({r['equipamento']})")
checar(r["tipo_equipamento"] == "", "sem palavra genérica na nota, o tipo fica vazio")
checar(r["tipo_nota"] == "work_notes",
       f"o relatório diz se a evidência veio de anotação de trabalho ou normal ({r['tipo_nota']})")
checar(r["rastreio"] == "AB123456789BR", "o rastreio dos Correios é extraído")
checar("enviado" in r["evidencia"].lower(), "o trecho da nota vai junto para conferência")

print("\n[5] Nasceu conosco: a data de entrada é a abertura")
chamado = {"numero": "INC3", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Coletor", "grupo_atual": "SPARE - Equipamentos",
           "resolucao": "2026-03-01 15:00:00"}
notas = [nota("2026-03-01 14:00:00", "Coletor TC21 enviado ao CD.")]
r = analisar(chamado, [], notas, NOSSOS)
checar(r["entrada"] == dt("2026-03-01 09:00:00"), "sem bouncing, vale a abertura")
checar(r["tipo_equipamento"] == "COLETOR", "o tipo do equipamento sai da nota")
checar(r["atendido"] and r["tma_horas"] == 5.0, f"TMA de 5h ({r['tma_horas']})")

print("\n[6] Nunca foi nosso")
chamado = {"numero": "INC4", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Rede", "grupo_atual": "Redes",
           "resolucao": "2026-03-02 09:00:00"}
r = analisar(chamado, [], [nota("2026-03-01 10:00:00", "Equipamento enviado.")], NOSSOS)
checar(not r["passou_por_nos"] and not r["atendido"], "fora da nossa fila não é nosso")
checar(r["tma_horas"] is None, "e não gera TMA")

print("\n[7] Nota de envio fora da nossa janela não conta")
chamado = {"numero": "INC5", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Coletor", "grupo_atual": "Redes",
           "resolucao": "2026-03-10 09:00:00"}
hist = [troca("2026-03-02 08:00:00", "Service Desk", "SPARE - Equipamentos"),
        troca("2026-03-02 12:00:00", "SPARE - Equipamentos", "Redes")]
tarde = [nota("2026-03-09 10:00:00", "Equipamento enviado pela equipe de redes.")]
r = analisar(chamado, hist, tarde, NOSSOS)
checar(not r["atendido"],
       "envio feito dias depois, por outra fila, não vira atendimento nosso")

print("\n[8] Promessa não é envio")
chamado = {"numero": "INC6", "abertura": "2026-03-01 09:00:00",
           "subcategoria": "Coletor", "grupo_atual": "SPARE - Equipamentos",
           "resolucao": "2026-03-02 09:00:00"}
r = analisar(chamado, [], [nota("2026-03-01 10:00:00",
                                "Vamos separar e enviar o coletor amanhã.")], NOSSOS)
checar(not r["atendido"], '"vamos enviar" não conta como envio')
r3 = analisar(chamado, [], [nota("2026-03-01 10:00:00", "Coletor enviado.",
                                 tipo="comments")], NOSSOS)
checar(r3["atendido"] and r3["tipo_nota"] == "comments",
       "anotação normal (comments) também vale como evidência")
r2 = analisar(chamado, [], [nota("2026-03-01 10:00:00", "Coletor enviado."),
                            nota("2026-03-01 16:00:00", "Coletor TC26 enviado.")], NOSSOS)
checar(r2["nossa_resolucao"] == dt("2026-03-01 16:00:00"),
       "com duas notas de envio, vale a última — é ela que fecha o atendimento")

print("\n[9] Leitura do que foi enviado")
checar(achar_equipamento("Enviado coletor TC21, série ABC123XYZ") == ["TC21", "ABC123XYZ"],
       "equipamento traz modelo e série, sem a palavra genérica")
checar(achar_tipo("Enviado coletor TC21, série ABC123XYZ") == ["COLETOR"],
       "o tipo sai em coluna própria")
checar(achar_tipo("Impressora térmica enviada") == ["IMPRESSORA"],
       "tipo reconhecido mesmo sem modelo")
checar("INC0012345" not in achar_equipamento("Conforme INC0012345, enviado TC21"),
       "número de chamado não é série")
checar(achar_equipamento("equipamento enviado") == [],
       "sem modelo nem série, não inventa equipamento")
checar(achar_equipamento("TC21 enviado, rastreio AB123456789BR") == ["TC21"],
       "o rastreio não vira equipamento: ele tem coluna própria")

print("\n[10] Resumo por subcategoria")
linhas = [
    {"atendido": True, "passou_por_nos": True, "subcategoria": "Coletor", "tma_horas": 10.0},
    {"atendido": True, "passou_por_nos": True, "subcategoria": "Coletor", "tma_horas": 20.0},
    {"atendido": True, "passou_por_nos": True, "subcategoria": "Impressora", "tma_horas": 5.0},
    {"atendido": False, "passou_por_nos": True, "subcategoria": "Coletor", "tma_horas": 99.0},
    {"atendido": False, "passou_por_nos": False, "subcategoria": "Rede", "tma_horas": None},
]
res = resumir(linhas)
checar(res["total"] == 5 and res["atendidos"] == 3, "conta o que foi atendido")
checar(res["passaram_sem_envio"] == 1 and res["nunca_foram_nossos"] == 1,
       "e separa os dois tipos de 'não foi nosso'")
coletor = [x for x in res["por_subcategoria"] if x["subcategoria"] == "Coletor"][0]
checar(coletor["quantidade"] == 2 and coletor["tma_medio_h"] == 15.0,
       f"o TMA médio ignora quem não foi atendido ({coletor})")

print("\n[11] Datas em qualquer formato que o ServiceNow devolve")
checar(ler_data("2026-03-01 09:00:00") == dt("2026-03-01 09:00:00"), "formato da API")
checar(ler_data("01/03/2026 09:00:00") == dt("2026-03-01 09:00:00"), "formato de tela")
checar(ler_data("") is None and ler_data("qualquer coisa") is None, "vazio e lixo viram None")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhas:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Regra de atendimento íntegra.")
