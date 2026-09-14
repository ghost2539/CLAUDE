"""Núcleo da análise de chamados — funções puras, sem rede e sem planilha.

A pergunta que este módulo responde, por chamado: **o time atendeu?**

Atendido, na definição da área, é ter passado pela nossa fila **e** ter
enviado equipamento. Não é onde o chamado nasceu, nem onde ele foi
encerrado: chamado que abre na nossa fila, é puxado para outra e não
volta conta como nosso na planilha de hoje, e não deveria; chamado que
nasce em outra fila, passa por nós e sai também é atendimento nosso, e
hoje não é contado.

Tudo aqui recebe listas e dicionários e devolve dicionários. A rede, a
autenticação e o Excel ficam em `analisar_chamados.py`, para que a regra
— que é o que a área precisa conferir — possa ser verificada sozinha.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

# ── Datas ─────────────────────────────────────────────────────────
# O ServiceNow devolve "2026-09-13 14:32:07" (UTC) na API, e
# "13/09/2026 11:32:07" quando o campo vem em display value. As duas
# formas aparecem no mesmo relatório dependendo de quem exportou.
_FORMATOS = ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
             "%d/%m/%Y %H:%M", "%Y-%m-%d")


def ler_data(valor) -> datetime | None:
    if isinstance(valor, datetime):
        return valor
    texto = str(valor or "").strip().replace("Z", "")
    if not texto:
        return None
    for f in _FORMATOS:
        try:
            return datetime.strptime(texto, f)
        except ValueError:
            continue
    return None


def _texto_simples(v: str) -> str:
    """Sem acento, sem caixa, sem espaço sobrando — para comparar nomes."""
    t = unicodedata.normalize("NFKD", str(v or "").strip().lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def e_nossa(grupo: str, nossos: list[str]) -> bool:
    """O grupo é do time? Compara sem acento e sem caixa.

    Nome de fila é digitado por gente e exportado por sistema: "SPARE  BR"
    e "Spare BR" são a mesma fila, e tratá-las como diferentes joga
    atendimento fora da conta.
    """
    alvo = _texto_simples(grupo)
    if not alvo:
        return False
    return any(alvo == _texto_simples(n) for n in nossos if str(n).strip())


# ── Linha do tempo das filas ──────────────────────────────────────
def periodos_de_fila(historico: list[dict], abertura: datetime | None,
                     grupo_atual: str = "",
                     fim: datetime | None = None) -> list[dict]:
    """Por quais filas o chamado passou, e quando.

    `historico` são as trocas de `assignment_group` (cada uma com
    `de`, `para` e `quando`), em qualquer ordem. Sem histórico, o chamado
    passou a vida inteira na fila atual — que é o que o relatório mostra.

    Devolve [{"grupo", "inicio", "fim"}], com `fim` None no período que
    ainda está aberto.
    """
    trocas = sorted(
        [h for h in (historico or []) if ler_data(h.get("quando"))],
        key=lambda h: ler_data(h.get("quando")),
    )
    if not trocas:
        return [{"grupo": grupo_atual, "inicio": abertura, "fim": fim}] if grupo_atual else []

    periodos: list[dict] = []
    # A primeira troca diz de onde ele saiu: essa é a fila de nascimento.
    inicial = str(trocas[0].get("de") or "").strip()
    atual = inicial or grupo_atual
    desde = abertura or ler_data(trocas[0].get("quando"))
    for t in trocas:
        quando = ler_data(t.get("quando"))
        destino = str(t.get("para") or "").strip()
        if atual:
            periodos.append({"grupo": atual, "inicio": desde, "fim": quando})
        atual, desde = destino, quando
    if atual:
        periodos.append({"grupo": atual, "inicio": desde, "fim": fim})
    return [p for p in periodos if p["grupo"]]


def passagens_do_time(periodos: list[dict], nossos: list[str]) -> list[dict]:
    """Os períodos em que o chamado esteve com o time."""
    return [p for p in periodos if e_nossa(p["grupo"], nossos)]


# ── Evidência de envio de equipamento ─────────────────────────────
# Verbos que a área usa para confirmar o envio. A lista é curta de
# propósito: "vamos enviar" não é envio, "enviado" é.
_RE_ENVIO = re.compile(
    r"\b(enviad[oa]s?|despachad[oa]s?|postad[oa]s?|expedid[oa]s?|"
    r"remessa|entregue\s+(?:ao|a|para)|coletado\s+pelos?\s+correios)\b", re.I)

# Rastreio dos Correios: 2 letras + 9 dígitos + BR.
_RE_RASTREIO = re.compile(r"\b([A-Z]{2}\d{9}[A-Z]{2})\b")

# Modelo é o que identifica o aparelho; tipo é a palavra genérica que a
# nota usa. Misturar os dois enche a coluna "equipamento" de "COLETOR" e
# esconde o que interessa — o modelo e a série. A lista fica configurável
# porque o parque muda.
MODELOS_PADRAO = (
    "TC21", "TC26", "TC22", "TC27", "MC33", "MC93", "EF500", "EF501",
    "RFD40", "ZQ511", "ZQ521", "ZD421", "ZD621", "DS2278", "DS8178",
)

TIPOS_PADRAO = (
    "COLETOR", "SLED", "IMPRESSORA", "PDV", "MONITOR", "TECLADO", "LEITOR",
    "BALANCA", "BALANÇA", "GAVETA", "PINPAD", "SWITCH", "ACCESS POINT",
    "NOTEBOOK", "DESKTOP", "CARREGADOR", "BATERIA", "FONTE", "CABO",
)

# Série: bloco alfanumérico com pelo menos 6 caracteres e ao menos um
# dígito. Evita pegar palavra comum do texto.
_RE_SERIE = re.compile(r"\b(?=[A-Z0-9-]{6,20}\b)(?=[^\s]*\d)[A-Z0-9][A-Z0-9-]{5,19}\b")

# Palavras que aparecem coladas em números e NÃO são série.
_FALSOS = {"INC", "RITM", "TASK", "CHG", "SCTASK", "PRB"}


def achar_envio(notas: list[dict], janela: tuple[datetime | None, datetime | None],
                modelos: tuple[str, ...] = MODELOS_PADRAO,
                folga_horas: int = 24) -> dict | None:
    """A nota que confirma o envio do equipamento, dentro da janela do time.

    `notas`: [{"quando", "texto", "autor", "tipo"}]. A janela é o período
    em que o chamado esteve com o time; a folga cobre a nota lançada logo
    depois de transferir o chamado, que é rotina.

    Devolve a ÚLTIMA nota que confirma envio (é ela que marca o fim do
    nosso atendimento), com o equipamento e o rastreio que der para ler.
    """
    inicio, fim = janela
    candidatas = []
    for n in notas or []:
        quando = ler_data(n.get("quando"))
        if quando is None:
            continue
        if inicio and quando < inicio - timedelta(hours=folga_horas):
            continue
        if fim and quando > fim + timedelta(hours=folga_horas):
            continue
        texto = str(n.get("texto") or "")
        if not _RE_ENVIO.search(texto):
            continue
        candidatas.append((quando, n, texto))
    if not candidatas:
        return None

    candidatas.sort(key=lambda x: x[0])
    quando, nota, texto = candidatas[-1]
    return {
        "quando": quando,
        "autor": nota.get("autor", ""),
        # Duas coisas diferentes, com nome diferente: de que tipo é a
        # ANOTAÇÃO (trabalho ou normal) e de que tipo é o EQUIPAMENTO.
        "tipo_nota": nota.get("tipo", ""),
        "equipamento": ", ".join(achar_equipamento(texto, modelos)),
        "tipo_equipamento": ", ".join(achar_tipo(texto)),
        "rastreio": ", ".join(sorted(set(_RE_RASTREIO.findall(texto.upper())))),
        "trecho": _trecho(texto),
    }


def achar_equipamento(texto: str, modelos: tuple[str, ...] = MODELOS_PADRAO) -> list[str]:
    """Modelos e séries citados no texto, na ordem em que aparecem."""
    achados: list[str] = []
    alvo = (texto or "").upper()
    for m in modelos:
        if m.upper() in alvo and m.upper() not in achados:
            achados.append(m.upper())
    rastreios = set(_RE_RASTREIO.findall(alvo))
    for s in _RE_SERIE.findall(alvo):
        if s in _FALSOS or any(s.startswith(f) for f in _FALSOS):
            continue
        # Rastreio dos Correios tem coluna própria; repetido aqui só polui
        # a resposta de "que equipamento foi enviado".
        if s in rastreios:
            continue
        if s in achados or s in [m.upper() for m in modelos]:
            continue
        achados.append(s)
    return achados[:6]


def achar_tipo(texto: str, tipos: tuple[str, ...] = TIPOS_PADRAO) -> list[str]:
    """A palavra que diz QUE tipo de equipamento é (coletor, impressora…)."""
    alvo = _texto_simples(texto).upper()
    return [t.upper() for t in tipos if _texto_simples(t).upper() in alvo][:4]


def _trecho(texto: str, limite: int = 220) -> str:
    """A frase do envio, para quem for conferir na mão não abrir o chamado."""
    limpo = " ".join(str(texto or "").split())
    m = _RE_ENVIO.search(limpo)
    if not m:
        return limpo[:limite]
    ini = max(0, m.start() - 90)
    return ("…" if ini else "") + limpo[ini:ini + limite].strip()


# ── Veredito por chamado ──────────────────────────────────────────
def analisar(chamado: dict, historico: list[dict], notas: list[dict],
             nossos: list[str], modelos: tuple[str, ...] = MODELOS_PADRAO) -> dict:
    """O veredito de um chamado, com as datas que a área vai contabilizar.

    - **entrada**: quando o chamado chegou à nossa fila (o "bouncing").
      Se ele já nasceu conosco, é a abertura — como a área pediu.
    - **nossa_resolucao**: quando NÓS resolvemos, que é a confirmação do
      envio. Sem nota de envio, cai para a saída da nossa fila; e, em
      último caso, para a resolução formal. Nunca é o encerramento
      administrativo quando existe marco melhor.
    - **atendido**: passou por nós E enviou equipamento.
    """
    abertura = ler_data(chamado.get("abertura"))
    resolucao = ler_data(chamado.get("resolucao")) or ler_data(chamado.get("encerramento"))
    periodos = periodos_de_fila(historico, abertura,
                                str(chamado.get("grupo_atual") or ""), resolucao)
    passagens = passagens_do_time(periodos, nossos)

    saida = {
        "numero": chamado.get("numero", ""),
        "subcategoria": chamado.get("subcategoria", ""),
        "categoria": chamado.get("categoria", ""),
        "abertura": abertura,
        "grupo_abertura": periodos[0]["grupo"] if periodos else "",
        "grupo_final": periodos[-1]["grupo"] if periodos else chamado.get("grupo_atual", ""),
        "filas": " → ".join(p["grupo"] for p in periodos),
        "passou_por_nos": bool(passagens),
        "entrada": None, "saida_da_fila": None, "nossa_resolucao": None,
        "base_da_data": "", "tma_horas": None,
        "atendido": False, "motivo": "",
        "equipamento": "", "tipo_equipamento": "", "rastreio": "",
        "evidencia": "", "quem_enviou": "", "tipo_nota": "",
        "resolucao_formal": resolucao,
    }

    if not passagens:
        saida["motivo"] = "o chamado nunca esteve na nossa fila"
        return saida

    # Interessa a passagem em que houve envio; na falta dela, a primeira.
    escolhida, envio = passagens[0], None
    for p in passagens:
        achado = achar_envio(notas, (p["inicio"], p["fim"]), modelos)
        if achado:
            escolhida, envio = p, achado
            break

    saida["entrada"] = escolhida["inicio"] or abertura
    saida["saida_da_fila"] = escolhida["fim"]

    if envio:
        saida.update({
            "atendido": True,
            "motivo": "passou pela nossa fila e o envio do equipamento está registrado",
            "nossa_resolucao": envio["quando"],
            "base_da_data": "nota de envio",
            "equipamento": envio["equipamento"],
            "tipo_equipamento": envio["tipo_equipamento"],
            "tipo_nota": envio["tipo_nota"],
            "rastreio": envio["rastreio"],
            "evidencia": envio["trecho"],
            "quem_enviou": envio["autor"],
        })
    else:
        saida["motivo"] = ("esteve na nossa fila, mas não há nota confirmando "
                           "envio de equipamento")
        if escolhida["fim"]:
            saida["nossa_resolucao"] = escolhida["fim"]
            saida["base_da_data"] = "saída da nossa fila"
        elif resolucao:
            saida["nossa_resolucao"] = resolucao
            saida["base_da_data"] = "resolução do chamado"

    inicio, fim = saida["entrada"], saida["nossa_resolucao"]
    if inicio and fim and fim >= inicio:
        saida["tma_horas"] = round((fim - inicio).total_seconds() / 3600, 2)
    return saida


def resumir(linhas: list[dict]) -> dict:
    """Resumo por subcategoria, só do que o time atendeu de verdade."""
    atendidos = [l for l in linhas if l.get("atendido")]
    por_sub: dict[str, list[float]] = {}
    for l in atendidos:
        if l.get("tma_horas") is None:
            continue
        por_sub.setdefault(l.get("subcategoria") or "(sem subcategoria)", []).append(
            float(l["tma_horas"]))

    def _mediana(vs: list[float]) -> float:
        vs = sorted(vs)
        n = len(vs)
        if not n:
            return 0.0
        meio = n // 2
        return vs[meio] if n % 2 else (vs[meio - 1] + vs[meio]) / 2

    return {
        "total": len(linhas),
        "atendidos": len(atendidos),
        "passaram_sem_envio": sum(1 for l in linhas
                                  if l.get("passou_por_nos") and not l.get("atendido")),
        "nunca_foram_nossos": sum(1 for l in linhas if not l.get("passou_por_nos")),
        "por_subcategoria": sorted(
            ({"subcategoria": k, "quantidade": len(v),
              "tma_medio_h": round(sum(v) / len(v), 2),
              "tma_mediana_h": round(_mediana(v), 2)}
             for k, v in por_sub.items()),
            key=lambda x: -x["quantidade"]),
    }
