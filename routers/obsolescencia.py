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
import logging
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.security import get_session

_log = logging.getLogger("obsolescencia")

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
    """Situação do parque, sobre a última coleta gravada."""
    sd = get_session(req)          # exige sessão do portal
    base = {
        "fonte": "MDM de Coletores (Workspace ONE / AirWatch)",
        "usuario": sd.get("display_name") or sd.get("username", ""),
    }
    try:
        import db.obsolescencia as _db
        _db.init_db()
        dados = resumo_parque()
    except Exception as exc:  # noqa: BLE001 — banco fora não derruba a tela
        _log.error("Resumo indisponível: %s", exc, exc_info=True)
        return {**base, "pendente": True, "erro": "Base do módulo indisponível."}

    if not dados.get("coletado_em"):
        return {**base, "pendente": True, "total": 0}
    return {**base, "pendente": False, **dados}


@router.get("/api/obsolescencia/tratativa")
def tratativa(req: Request):
    """Coletores que sumiram do MDM e ainda não foram tratados."""
    from sqlalchemy import select as _select
    get_session(req)
    import db.obsolescencia as _db
    _db.init_db()
    with _db.SessionLocal() as s:
        linhas = list(s.execute(
            _select(_db.Coletor).where(_db.Coletor.situacao == _db.SUMIU)
            .order_by(_db.Coletor.atualizado_em.desc())).scalars())
    return {"total": len(linhas), "coletores": [
        {"mdm_id": c.mdm_id, "nome": c.nome, "bu": c.bu_nome, "loja": c.loja,
         "modelo": c.modelo, "usuario": c.usuario,
         "desde": c.atualizado_em.isoformat() if c.atualizado_em else None}
        for c in linhas]}


@router.get("/api/obsolescencia/config")
def config_ler(req: Request):
    get_session(req)
    import db.obsolescencia as _db
    _db.init_db()
    return _db.ler_config()


@router.post("/api/obsolescencia/coletar")
def coletar(req: Request):
    """Dispara a varredura do parque, sob demanda.

    Sem agendamento por ora, por decisão da área: roda por botão, com alguém
    acompanhando. A credencial é a conta de serviço do cofre — não a senha
    do usuário, que o portal não guarda.
    """
    sd = get_session(req)
    return {
        "ok": False,
        "detalhe": ("Credencial de serviço do MDM ainda não configurada. "
                    "Cadastre-a no cofre para a coleta poder rodar."),
        "usuario": sd.get("username", ""),
    }


# ── Persistência da coleta ────────────────────────────────────────
def _limpar(v) -> str:
    return str(v or "").strip()


def aplicar_coleta(coletores: list[dict], usuario: str = "",
                   total_mdm: int = 0, paginas: int = 0) -> dict:
    """Grava o resultado de uma varredura e apura o que mudou.

    Coletor que não veio nesta rodada NÃO é apagado: muda para `sumiu` e
    entra na fila de tratativa. Apagar em silêncio esconderia justamente o
    caso que precisa de decisão humana.
    """
    import json as _json
    from sqlalchemy import select as _select
    import db.obsolescencia as _db

    cfg = _db.ler_config()
    modo = cfg.get("modo_regra", MODO_PADRAO)
    eol = [m.strip() for m in (cfg.get("modelos_eol") or "").split(",") if m.strip()]
    agora = datetime.now(timezone.utc)

    novos = atualizados = 0
    with _db.SessionLocal.begin() as s:
        coleta = _db.Coleta(usuario=usuario, total_mdm=total_mdm, paginas=paginas,
                            lidos=len(coletores), situacao="aberta")
        s.add(coleta)
        s.flush()

        existentes = {c.mdm_id: c for c in s.execute(_select(_db.Coletor)).scalars()}

        for bruto in coletores:
            mdm_id = _limpar(bruto.get("id"))
            if not mdm_id:
                continue
            loja = identificar_loja(bruto.get("usuario"))
            tags = tags_relevantes(bruto.get("tags"))
            aval = avaliar_obsolescencia(
                {"modelo": bruto.get("modelo"),
                 "data_aquisicao": bruto.get("data_aquisicao"),
                 "android_travado": bruto.get("android_travado")},
                eol, agora, modo)

            linha = existentes.get(mdm_id)
            if linha is None:
                linha = _db.Coletor(mdm_id=mdm_id)
                s.add(linha)
                novos += 1
            else:
                atualizados += 1

            linha.nome = _limpar(bruto.get("nome"))
            linha.usuario = loja["usuario"]
            linha.caminho_og = _limpar(bruto.get("caminho_og"))
            linha.bu = loja["bu"]
            linha.bu_nome = loja["bu_nome"]
            linha.pais = loja["pais"]
            linha.loja = loja["loja"]
            linha.e_loja = loja["e_loja"]
            linha.plataforma = _limpar(bruto.get("plataforma"))
            linha.modelo = _limpar(bruto.get("modelo"))
            linha.versao_os = _limpar(bruto.get("versao_os"))
            linha.propriedade = _limpar(bruto.get("propriedade"))
            linha.gerenciamento = _limpar(bruto.get("gerenciamento"))
            linha.conformidade = _limpar(bruto.get("conformidade"))
            linha.visto_relativo = _limpar(bruto.get("visto_relativo"))
            linha.dias_sem_ver = dias_sem_ver(bruto.get("visto_em"), agora)
            linha.tags = "|".join(tags)
            linha.idade_anos = aval["idade_anos"]
            linha.idade_desconhecida = aval["idade_desconhecida"]
            linha.obsoleto = aval["obsoleto"]
            linha.criterios = _json.dumps(aval["criterios"], ensure_ascii=False)
            linha.situacao = _db.ATIVO
            linha.visto_na_coleta = coleta.id
            linha.atualizado_em = _db.localnow()

        # O que não apareceu nesta rodada vira tratativa.
        sumiram = 0
        for mdm_id, linha in existentes.items():
            if linha.visto_na_coleta != coleta.id and linha.situacao == _db.ATIVO:
                linha.situacao = _db.SUMIU
                linha.atualizado_em = _db.localnow()
                sumiram += 1

        coleta.novos = novos
        coleta.atualizados = atualizados
        coleta.sumiram = sumiram
        coleta.fim = _db.localnow()
        coleta.situacao = "concluida"
        resultado = {"coleta_id": coleta.id, "lidos": len(coletores),
                     "novos": novos, "atualizados": atualizados,
                     "sumiram": sumiram, "total_mdm": total_mdm}
    return resultado


# ── Agregações do painel ──────────────────────────────────────────
def resumo_parque() -> dict:
    """Os recortes que a área pediu, calculados sobre a última coleta."""
    from sqlalchemy import select as _select
    import db.obsolescencia as _db

    cfg = _db.ler_config()
    limite_sem_ver = int(cfg.get("limite_sem_ver") or LIMITE_SEM_VER)

    with _db.SessionLocal() as s:
        todos = list(s.execute(_select(_db.Coletor)).scalars())
        ultima = s.execute(
            _select(_db.Coleta).order_by(_db.Coleta.id.desc()).limit(1)).scalar_one_or_none()

    ativos = [c for c in todos if c.situacao == _db.ATIVO]
    # O CD não é loja: fica fora das contagens por loja/BU, mas continua no
    # parque total — o aparelho existe.
    de_loja = [c for c in ativos if c.e_loja]

    por_bu: dict[str, dict] = {}
    for c in de_loja:
        b = por_bu.setdefault(c.bu or "?", {"bu": c.bu, "bu_nome": c.bu_nome,
                                            "pais": c.pais, "coletores": 0,
                                            "obsoletos": 0, "lojas": set()})
        b["coletores"] += 1
        b["obsoletos"] += 1 if c.obsoleto else 0
        if c.loja:
            b["lojas"].add(c.loja)

    por_loja: dict[tuple, dict] = {}
    for c in de_loja:
        k = (c.bu, c.loja)
        l = por_loja.setdefault(k, {"bu": c.bu, "bu_nome": c.bu_nome, "loja": c.loja,
                                    "coletores": 0, "obsoletos": 0, "sem_ver": 0})
        l["coletores"] += 1
        l["obsoletos"] += 1 if c.obsoleto else 0
        if (c.dias_sem_ver or 0) > limite_sem_ver:
            l["sem_ver"] += 1

    sem_ver = [c for c in ativos
               if c.dias_sem_ver is not None and c.dias_sem_ver > limite_sem_ver]

    # Os mais antigos: só entram os que têm idade conhecida. Ordenar com
    # idade nula no meio daria uma lista sem sentido.
    com_idade = [c for c in ativos if c.idade_anos is not None]
    mais_antigos = sorted(com_idade, key=lambda c: -(c.idade_anos or 0))[:50]

    tags = contar_tags([{"tags": c.tags.split("|") if c.tags else []} for c in ativos])

    return {
        "coletado_em": ultima.fim.isoformat() if ultima and ultima.fim else None,
        "total": len(ativos),
        "em_lojas": len(de_loja),
        "fora_de_loja": len(ativos) - len(de_loja),
        "nao_identificados": sum(1 for c in ativos if not c.bu),
        "obsoletos": sum(1 for c in de_loja if c.obsoleto),
        "idade_desconhecida": sum(1 for c in ativos if c.idade_desconhecida),
        "sem_ver": {"limite_dias": limite_sem_ver, "quantidade": len(sem_ver)},
        "em_tratativa": sum(1 for c in todos if c.situacao == _db.SUMIU),
        "por_bu": sorted(
            ({**b, "lojas": len(b["lojas"])} for b in por_bu.values()),
            key=lambda x: -x["coletores"]),
        "por_loja": sorted(por_loja.values(), key=lambda x: -x["coletores"]),
        "tags": tags,
        "mais_antigos": [
            {"mdm_id": c.mdm_id, "nome": c.nome, "bu": c.bu_nome, "loja": c.loja,
             "modelo": c.modelo, "versao_os": c.versao_os,
             "idade_anos": c.idade_anos, "dias_sem_ver": c.dias_sem_ver}
            for c in mais_antigos],
        "modo_regra": cfg.get("modo_regra"),
    }
