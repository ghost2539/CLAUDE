"""Previsão de consumo e necessidade de compra — funções puras.

Nenhuma caixa-preta: o método é escolhido pelo tamanho da série e a
resposta diz qual foi. Quem lê a tela precisa conseguir refazer a conta.

    < 3 meses    média simples
    3 a 23 meses média móvel ponderada (6 meses) + tendência linear amortecida
    ≥ 24 meses   o mesmo, vezes o índice sazonal do mês (anos completos)

P50 é a previsão. P90 = P50 + 1,28 × desvio dos erros de um passo, medido
refazendo a previsão sobre a própria série (backtest). Sem banco, sem
rede: recebe listas, devolve dicionários.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

Z90 = 1.2816


# ── Utilitários de mês ────────────────────────────────────────────────
def mes_seguinte(ym: str) -> str:
    a, m = int(ym[:4]), int(ym[5:7])
    return f"{a + (m // 12):04d}-{(m % 12) + 1:02d}"


def meses_entre(ini: str, fim: str) -> list[str]:
    """Lista de 'AAAA-MM' de ini a fim, inclusive."""
    out, cur = [], ini
    while cur <= fim:
        out.append(cur)
        cur = mes_seguinte(cur)
        if len(out) > 600:
            break
    return out


def primeiro_dia(ym: str) -> date:
    return date(int(ym[:4]), int(ym[5:7]), 1)


# ── Previsão ──────────────────────────────────────────────────────────
def _tendencia(serie: list[float]) -> float:
    """Inclinação da regressão linear (unidades por mês)."""
    n = len(serie)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(serie) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, serie)) / sxx


def _nivel_ponderado(serie: list[float], janela: int = 6) -> float:
    """Média dos últimos `janela` meses com peso crescente (o recente pesa mais)."""
    ult = serie[-janela:]
    pesos = list(range(1, len(ult) + 1))
    return sum(v * p for v, p in zip(ult, pesos)) / sum(pesos)


def _indices_sazonais(serie: list[float], mes_final: int) -> list[float] | None:
    """Índice por mês do calendário (1..12), só com anos completos.

    `mes_final` é o mês do último ponto da série. Devolve None quando não
    há 24 meses (dois anos) — um ano só não separa sazonalidade de acaso.
    """
    n = len(serie)
    if n < 24:
        return None
    anos = n // 12
    corte = serie[n - anos * 12:]
    media = sum(corte) / len(corte)
    if media <= 0:
        return None
    soma = [0.0] * 12
    cont = [0] * 12
    # O último ponto é `mes_final`; anda-se para trás.
    m = mes_final
    for v in reversed(corte):
        soma[m - 1] += v
        cont[m - 1] += 1
        m = 12 if m == 1 else m - 1
    idx = [(soma[i] / cont[i]) / media if cont[i] else 1.0 for i in range(12)]
    # Índice extremo em série curta vira ruído: limita entre 0,3 e 3.
    return [min(3.0, max(0.3, x)) for x in idx]


def _projetar(serie: list[float], mes_final: int, horizonte: int) -> tuple[list[float], str]:
    n = len(serie)
    if n == 0:
        return [0.0] * horizonte, "sem histórico"
    if n < 3:
        media = sum(serie) / n
        return [media] * horizonte, f"média simples ({n} {'meses' if n > 1 else 'mês'})"

    idx = _indices_sazonais(serie, mes_final)
    base_serie = serie
    if idx:
        # Nível e tendência são medidos na série sem sazonalidade; o índice
        # do mês volta a entrar na projeção. Senão um pico de novembro
        # vira "nível" e infla janeiro.
        base_serie, m = [], mes_final
        for v in reversed(serie):
            base_serie.append(v / idx[m - 1])
            m = 12 if m == 1 else m - 1
        base_serie.reverse()
    nivel = _nivel_ponderado(base_serie)
    tend = _tendencia(base_serie[-12:] if n >= 12 else base_serie)
    # Amortecimento: a tendência perde força a cada mês (0,85^h), para não
    # extrapolar uma subida de três meses para o ano inteiro.
    saida = []
    m = mes_final
    acum = 0.0
    for h in range(1, horizonte + 1):
        acum += tend * (0.85 ** h)
        base = max(0.0, nivel + acum)
        m = 1 if m == 12 else m + 1
        if idx:
            base *= idx[m - 1]
        saida.append(base)
    metodo = ("média móvel ponderada + tendência + sazonalidade"
              if idx else "média móvel ponderada + tendência")
    return saida, f"{metodo} ({n} meses)"


def _desvio_backtest(serie: list[float], mes_final: int) -> float:
    """Desvio-padrão dos erros de um passo: prevê cada mês com o que havia antes."""
    n = len(serie)
    if n < 4:
        return math.sqrt(sum((v - sum(serie) / n) ** 2 for v in serie) / n) if n else 0.0
    erros = []
    inicio = max(3, n - 12)
    for i in range(inicio, n):
        m = ((mes_final - (n - i)) - 1) % 12 + 1
        prev, _ = _projetar(serie[:i], m, 1)
        erros.append(serie[i] - prev[0])
    if not erros:
        return 0.0
    media = sum(erros) / len(erros)
    return math.sqrt(sum((e - media) ** 2 for e in erros) / len(erros))


def prever(historico: list[tuple[str, float]], horizonte: int = 12) -> dict:
    """Previsão mensal.

    `historico`: [('AAAA-MM', quantidade), …] em ordem crescente e sem
    buracos (mês sem consumo entra com 0). Devolve meses, p50, p90, método
    e desvio.
    """
    if horizonte < 1:
        horizonte = 1
    serie = [float(q) for _, q in historico]
    if historico:
        ultimo = historico[-1][0]
        mes_final = int(ultimo[5:7])
    else:
        hoje = date.today()
        ultimo = f"{hoje.year:04d}-{hoje.month:02d}"
        mes_final = hoje.month
    p50, metodo = _projetar(serie, mes_final, horizonte)
    desvio = _desvio_backtest(serie, mes_final) if serie else 0.0
    meses, cur = [], ultimo
    for _ in range(horizonte):
        cur = mes_seguinte(cur)
        meses.append(cur)
    return {
        "meses": meses,
        "p50": [round(v, 2) for v in p50],
        "p90": [round(v + Z90 * desvio, 2) for v in p50],
        "metodo": metodo,
        "desvio": round(desvio, 2),
        "meses_historico": len(serie),
    }


# ── Necessidade de compra ─────────────────────────────────────────────
def necessidade(previsao: dict, *, estoque: float, pedidos_abertos: float = 0,
                lead_time_dias: int = 0, seguranca_dias: int = 0,
                custo_unitario: float = 0) -> dict:
    """Quanto comprar, e até quando pedir, para atravessar o horizonte.

    A segurança em unidades é o consumo médio previsto vezes os dias de
    segurança sobre 30. Os pedidos em aberto entram no primeiro mês.
    """
    p50, p90, meses = previsao["p50"], previsao["p90"], previsao["meses"]
    h = len(p50)
    media = (sum(p50) / h) if h else 0.0
    seguranca = media * max(0, seguranca_dias) / 30.0

    projecao, saldo, ruptura = [], float(estoque), None
    for i, (m, q) in enumerate(zip(meses, p50)):
        if i == 0:
            saldo += float(pedidos_abertos or 0)
        saldo -= q
        projecao.append({"mes": m, "saldo": round(saldo, 1)})
        if ruptura is None and saldo < seguranca:
            ruptura = m

    total50 = sum(p50)
    total90 = sum(p90)
    disponivel = float(estoque) + float(pedidos_abertos or 0)
    nec50 = max(0.0, total50 + seguranca - disponivel)
    nec90 = max(0.0, total90 + seguranca - disponivel)

    limite = None
    if ruptura:
        limite = (primeiro_dia(ruptura) - timedelta(days=max(0, lead_time_dias))).isoformat()

    cobertura = None
    if media > 0:
        cobertura = round(disponivel / media, 1)  # meses de consumo cobertos

    return {
        "seguranca_unidades": round(seguranca, 1),
        "consumo_previsto": round(total50, 1),
        "consumo_previsto_p90": round(total90, 1),
        "necessidade": math.ceil(nec50 - 1e-9) if nec50 > 0 else 0,
        "necessidade_p90": math.ceil(nec90 - 1e-9) if nec90 > 0 else 0,
        "valor": round((math.ceil(nec50 - 1e-9) if nec50 > 0 else 0) * float(custo_unitario or 0), 2),
        "valor_p90": round((math.ceil(nec90 - 1e-9) if nec90 > 0 else 0) * float(custo_unitario or 0), 2),
        "mes_ruptura": ruptura,
        "data_limite_pedido": limite,
        "cobertura_meses": cobertura,
        "projecao": projecao,
    }
