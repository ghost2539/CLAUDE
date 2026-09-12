"""Painel de Obsolescência do parque de coletores.

Tela separada do portal — abre em outra guia, em tela cheia, para ser
apresentada em reunião —, porém integrada a ele: mesma sessão, mesmo
visual e mesmo processo. Quem já está logado no portal entra direto,
sem liberação à parte.

A origem dos dados é o MDM de Coletores (Workspace ONE / AirWatch),
lido em segundo plano pelo portal. Enquanto a primeira coleta não
existe, o painel assume o estado "sem dados": o levantamento do que o
MDM devolve é feito com `scripts/mdm_airwatch_captura.js`, e o
tratamento entra aqui depois, sobre o formato real.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.security import get_session

router = APIRouter()

# ── Identidade da loja e da BU ────────────────────────────────────
# O usuário do coletor carrega a loja: <sigla><numero>_coletor
#   ljr001_coletor  cm045_coletor  yc012_coletor  las003_coletor
# Argentina e Uruguai repetem a sigla do país com um prefixo numérico
# fixo (13 e 11) que NÃO faz parte do número da loja:
#   ljrar13001_coletor -> Argentina, loja 001
#   ljruy11001_coletor -> Uruguai,  loja 001
# A ordem aqui não importa para o casamento (a regex separa letras de
# dígitos), mas a lista é a fonte única das BUs válidas.
# `e_loja` decide quem entra nos números do painel. O CD é reconhecido de
# propósito — para não cair em "Não identificado", que é o sinal de dado
# sujo —, mas fica fora das contagens por loja/BU: não é loja.
BUS: dict[str, dict] = {
    "LJR":   {"nome": "Renner",           "pais": "BR", "prefixo": "",   "e_loja": True},
    "CM":    {"nome": "Camicado",         "pais": "BR", "prefixo": "",   "e_loja": True},
    "LAS":   {"nome": "Ashua",            "pais": "BR", "prefixo": "",   "e_loja": True},
    "YC":    {"nome": "Youcom",           "pais": "BR", "prefixo": "",   "e_loja": True},
    "LJRAR": {"nome": "Renner Argentina", "pais": "AR", "prefixo": "13", "e_loja": True},
    "LJRUY": {"nome": "Renner Uruguai",   "pais": "UY", "prefixo": "11", "e_loja": True},
    "CD":    {"nome": "Centro de Distribuição", "pais": "BR", "prefixo": "", "e_loja": False},
}

# Letras e dígitos são separados pela própria regex, então "ljrar13001"
# nunca é confundido com a BU "LJR" seguida de "ar13001".
# O sufixo opcional cobre o segundo coletor da mesma loja: ljr417_coletor_2.
_RE_COLETOR = re.compile(r"^([a-z]+)(\d+)_coletor(?:[_-](\S+))?$", re.I)


def identificar_loja(usuario: str) -> dict:
    """Extrai BU e número da loja do usuário do coletor.

    Devolve sempre um dicionário. `reconhecido` diz se o nome bate com o
    padrão e com uma BU conhecida; `e_loja` diz se entra nos números do
    painel — o CD é reconhecido mas não é loja, então fica de fora.

    O que não bate vira "não identificado" em vez de ser descartado em
    silêncio: coletor que some do painel é coletor que ninguém troca.
    """
    bruto = (usuario or "").strip()
    m = _RE_COLETOR.match(bruto)
    if not m:
        return {"reconhecido": False, "e_loja": False, "bu": "",
                "bu_nome": "Não identificado", "pais": "", "loja": "",
                "sufixo": "", "usuario": bruto}

    sigla = m.group(1).upper()
    digitos = m.group(2)
    sufixo = m.group(3) or ""
    info = BUS.get(sigla)
    if not info:
        return {"reconhecido": False, "e_loja": False, "bu": "",
                "bu_nome": "Não identificado", "pais": "", "loja": digitos,
                "sufixo": sufixo, "usuario": bruto}

    loja = digitos
    prefixo = info["prefixo"]
    # Só tira o prefixo do país quando ele realmente está lá e sobra número.
    if prefixo and digitos.startswith(prefixo) and len(digitos) > len(prefixo):
        loja = digitos[len(prefixo):]

    return {"reconhecido": True, "e_loja": info["e_loja"], "bu": sigla,
            "bu_nome": info["nome"], "pais": info["pais"], "loja": loja,
            "sufixo": sufixo, "usuario": bruto}


# ── Tags ──────────────────────────────────────────────────────────
# Interessam as tags que carregam um destes nomes. Existem nove no MDM
# (Manutenção, Em manutenção, Backlog de manutenção, Bloqueio - Inatividade
# N1, …) e o painel conta CADA UMA pelo nome real, sem amassar as nove em
# três baldes: "Backlog de manutenção" e "Manutenção" são situações
# diferentes e somá-las esconderia a informação.
GRUPOS_TAG = ("Inatividade", "Manutenção", "Movimentação")


def _sem_acento(v: str) -> str:
    txt = unicodedata.normalize("NFKD", str(v or ""))
    return "".join(c for c in txt if not unicodedata.combining(c)).lower()


_GRUPOS_NORM = {_sem_acento(g): g for g in GRUPOS_TAG}


def grupo_da_tag(nome: str) -> str:
    """A qual dos três nomes esta tag pertence; vazio se não for de interesse."""
    alvo = _sem_acento(nome)
    for norm, grupo in _GRUPOS_NORM.items():
        if norm in alvo:
            return grupo
    return ""


def tags_relevantes(tags) -> list[str]:
    """Nomes REAIS das tags de interesse do coletor, como estão no MDM.

    Preserva "Backlog de manutenção" em vez de devolver "Manutenção": o
    painel conta por tag, e a agregação por grupo é feita depois, por quem
    quiser vê-la.
    """
    if isinstance(tags, str):
        tags = re.split(r"[;,|]", tags)
    fora = []
    for t in tags or []:
        nome = str(t or "").strip()
        if nome and grupo_da_tag(nome) and nome not in fora:
            fora.append(nome)
    return fora


def contar_tags(coletores) -> list[dict]:
    """Quantos coletores por tag — só as que têm alguém atribuído.

    Tag sem coletor não entra: o painel mostra o que existe no parque, não o
    catálogo de tags do MDM.
    """
    contagem: dict[str, int] = {}
    for c in coletores or []:
        for nome in tags_relevantes(c.get("tags")):
            contagem[nome] = contagem.get(nome, 0) + 1
    return sorted(
        ({"tag": n, "grupo": grupo_da_tag(n), "quantidade": q}
         for n, q in contagem.items() if q > 0),
        key=lambda x: (x["grupo"], -x["quantidade"], x["tag"]),
    )


# ── Obsolescência ─────────────────────────────────────────────────
LIMITE_ANOS = 5          # 5 anos de uso
LIMITE_SEM_VER = 30      # dias sem comunicar

# Como combinar os critérios. Começa em "todos" (E), como a área definiu,
# mas fica configurável: com "qualquer" (OU) o número dispara, e a diferença
# entre os dois é o tamanho do investimento que o painel vai sustentar.
# Modelos em fim de vida, definidos pela área. A comparação é por conteúdo,
# sem acento e sem caixa: "EF500" casa com "Bluebird EF500" e "EF500R".
# ATENÇÃO: NÃO casa com "EF501R", que é o que aparece nas amostras do parque
# — são strings diferentes. Se o EF501R também for EOL, precisa entrar aqui.
MODELOS_EOL = ("EF500", "EF500R")

MODO_TODOS = "todos"
MODO_QUALQUER = "qualquer"
MODO_PADRAO = MODO_TODOS


def dias_sem_ver(last_seen, agora=None) -> int | None:
    """Dias desde a última comunicação. None quando a data não veio."""
    if not last_seen:
        return None
    agora = agora or datetime.now(timezone.utc)
    if isinstance(last_seen, str):
        try:
            last_seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        except ValueError:
            return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return max(0, (agora - last_seen).days)


def idade_anos(data_aquisicao, agora=None) -> float | None:
    if not data_aquisicao:
        return None
    agora = agora or datetime.now(timezone.utc)
    if isinstance(data_aquisicao, str):
        try:
            data_aquisicao = datetime.fromisoformat(data_aquisicao.replace("Z", "+00:00"))
        except ValueError:
            return None
    if data_aquisicao.tzinfo is None:
        data_aquisicao = data_aquisicao.replace(tzinfo=timezone.utc)
    return (agora - data_aquisicao).days / 365.25


def avaliar_obsolescencia(coletor: dict, modelos_eol=(), agora=None,
                          modo: str = MODO_PADRAO) -> dict:
    """Aplica a regra: 5 anos de uso, Android travado sem update possível e
    EOL do modelo atingido.

    `modo` decide a combinação — "todos" (E, o padrão da área) ou "qualquer"
    (OU). Os três critérios voltam separados, e não só o veredito: numa
    apresentação é o motivo que sustenta a troca, não o rótulo.
    """
    idade = idade_anos(coletor.get("data_aquisicao"), agora)
    modelo = _sem_acento(coletor.get("modelo"))
    eol = any(_sem_acento(m) and _sem_acento(m) in modelo for m in modelos_eol)

    criterios = {
        "idade_5_anos": bool(idade is not None and idade >= LIMITE_ANOS),
        "android_travado": bool(coletor.get("android_travado")),
        "modelo_eol": eol,
    }
    atendidos = [k for k, v in criterios.items() if v]
    if modo == MODO_QUALQUER:
        obsoleto = bool(atendidos)
    else:
        obsoleto = len(atendidos) == len(criterios)
    return {
        "obsoleto": obsoleto,
        "modo": modo,
        "criterios": criterios,
        "atendidos": atendidos,
        "idade_anos": round(idade, 1) if idade is not None else None,
        "idade_desconhecida": idade is None,
    }


_DIR = Path(__file__).resolve().parent.parent / "static" / "obsolescencia"


# ── Página ────────────────────────────────────────────────────────
@lru_cache
def _asset_version() -> str:
    """Impede que o navegador sirva um app.js velho depois do deploy."""
    h = hashlib.sha256()
    for nome in ("app.js", "app.css"):
        p = _DIR / nome
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


def _page() -> HTMLResponse:
    html = (_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{v}}", _asset_version()))


def _acesso_pagina(req: Request):
    """Só exige estar logado no portal: a tela é parte do sistema, não um
    módulo à parte. Sem sessão, manda para o login levando o destino."""
    sd = get_session(req, required=False)
    if not sd:
        return RedirectResponse(f"/?next={quote(req.url.path, safe='/')}", status_code=302)
    return _page()


@router.get("/obsolescencia", response_class=HTMLResponse)
def pagina(req: Request):
    return _acesso_pagina(req)


@router.get("/obsolescencia/", response_class=HTMLResponse)
def pagina_barra(req: Request):
    return _acesso_pagina(req)


# ── API ───────────────────────────────────────────────────────────
@router.get("/api/obsolescencia/resumo")
def resumo(req: Request):
    """Situação do parque. Enquanto o coletor do MDM não roda pela
    primeira vez, devolve `pendente` para a tela explicar o que falta."""
    sd = get_session(req)  # exige sessão do portal
    return {
        "pendente": True,
        "coletado_em": None,
        "fonte": "MDM de Coletores (Workspace ONE / AirWatch)",
        "total": 0,
        "usuario": sd.get("display_name") or sd.get("username", ""),
    }
