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
import os
import threading
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import re
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

import config as _config_mod
from core.security import get_session

_cfg = _config_mod.get_settings()

_log = logging.getLogger("obsolescencia")

router = APIRouter()


def _exigir_admin(req: Request) -> dict:
    """Coletar e mexer em credencial é de admin do portal. Ver o painel não é."""
    sd = get_session(req)
    if not sd.get("is_admin"):
        raise HTTPException(403, "Ação restrita a administradores do portal.")
    return sd

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


_FORMATOS_DATA = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
                  "%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y")


def interpretar_data(valor) -> datetime | None:
    """Data em ISO ou no formato do tooltip do MDM (dd/mm/aaaa HH:MM).

    O parser da grade entrega a data absoluta como texto do tooltip, não
    ISO. Tratar só ISO fazia `dias_sem_ver` devolver None para o parque
    inteiro — e o painel mostrava zero "sem comunicar" sem ninguém
    perceber o motivo.
    """
    if not valor:
        return None
    if isinstance(valor, datetime):
        dt = valor
    else:
        texto = str(valor).strip()
        dt = None
        try:
            dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            for fmt in _FORMATOS_DATA:
                try:
                    dt = datetime.strptime(texto[:19], fmt)
                    break
                except ValueError:
                    continue
        if dt is None:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dias_sem_ver(last_seen, agora=None) -> int | None:
    """Dias desde a última comunicação. None quando a data não veio."""
    dt = interpretar_data(last_seen)
    if dt is None:
        return None
    agora = agora or datetime.now(timezone.utc)
    return max(0, (agora - dt).days)


def _versao_major(texto: str) -> int | None:
    m = re.search(r"(\d+)", str(texto or ""))
    return int(m.group(1)) if m else None


def android_travado(versao_os: str, modelo: str, versao_minima: str,
                    modelos_sem_update=()) -> bool:
    """Sem atualização possível: versão abaixo da mínima ou modelo sem update."""
    modelo_n = _sem_acento(modelo)
    if any(_sem_acento(m) and _sem_acento(m) in modelo_n for m in modelos_sem_update):
        return True
    atual, minima = _versao_major(versao_os), _versao_major(versao_minima)
    return bool(atual is not None and minima is not None and atual < minima)


def idade_anos(data_aquisicao, agora=None) -> float | None:
    if not data_aquisicao:
        return None
    agora = agora or datetime.now(timezone.utc)
    dt = interpretar_data(data_aquisicao)
    if dt is None:
        return None
    return (agora - dt).days / 365.25


def avaliar_obsolescencia(coletor: dict, modelos_eol=(), agora=None,
                          modo: str = MODO_PADRAO,
                          limite_anos: float = LIMITE_ANOS) -> dict:
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
        "idade_5_anos": bool(idade is not None and idade >= limite_anos),
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


@router.put("/api/obsolescencia/config")
def config_gravar(req: Request, corpo: dict):
    """Regra de obsolescência. A próxima coleta reavalia o parque com ela."""
    sd = _exigir_admin(req)
    import db.obsolescencia as _db
    _db.init_db()
    validos = set(_db.PADROES)
    pares = {k: str(v).strip() for k, v in (corpo or {}).items() if k in validos}
    if not pares:
        raise HTTPException(400, "Nada para gravar.")
    if pares.get("modo_regra") not in (None, MODO_TODOS, MODO_QUALQUER):
        raise HTTPException(400, "modo_regra deve ser 'todos' ou 'qualquer'.")
    _db.gravar_config(pares)
    _log.info("obsolescencia: regra alterada por %s: %s", sd.get("username", "?"), ", ".join(pares))
    return _db.ler_config()


# ── Sessão no MDM ─────────────────────────────────────────────────
# A credencial é de SERVIÇO e vive no cofre: o portal não guarda a senha do
# usuário logado (só os cookies do ServiceNow), então não há como reaproveitá-la.
# O usuário do MDM vai no formato `renner\<login>`.
CHAVE_USUARIO = "MDM_USUARIO"
CHAVE_SENHA = "MDM_SENHA"

_sessao_mdm = None
_trava_sessao = threading.Lock()


def credencial_mdm() -> tuple[str, str]:
    from core import cofre
    return cofre.obter(CHAVE_USUARIO), cofre.obter(CHAVE_SENHA)


def _nova_sessao():
    import requests
    from routers.servicenow import SN_PROXY
    s = requests.Session()
    if SN_PROXY:
        s.proxies = {"https": SN_PROXY, "http": SN_PROXY}
    return s


def sessao_mdm(forcar: bool = False):
    """Sessão autenticada no console, reaproveitada entre chamadas.

    É o keep-alive: em vez de logar a cada uso, guardamos a sessão e só
    refazemos o login quando o console deixa de responder o fragmento.
    """
    global _sessao_mdm
    from integracoes import mdm_airwatch as mdm

    base = getattr(_cfg, "MDM_BASE_URL", "")
    with _trava_sessao:
        if not forcar and _sessao_mdm is not None and mdm.sessao_valida(_sessao_mdm, base):
            return _sessao_mdm
        usuario, senha = credencial_mdm()
        if not usuario or not senha:
            raise HTTPException(
                503, "Credencial de serviço do MDM não configurada no cofre "
                     f"({CHAVE_USUARIO} / {CHAVE_SENHA}).")
        nova = _nova_sessao()
        if not mdm.login(nova, usuario, senha, base):
            raise HTTPException(502, "Login no MDM recusado. Confira a credencial "
                                     "de serviço no cofre.")
        _sessao_mdm = nova
        return _sessao_mdm


def diagnostico_cofre() -> dict:
    """Onde o cofre grava, se dá para gravar ali, e o que fazer se não der.

    O diretório da aplicação costuma ser somente leitura — e deve ser mesmo.
    A norma do projeto reserva `data/` para escrita, então é de lá que sai a
    sugestão, já montada, em vez de deixar quem administra adivinhando.
    """
    from core import cofre
    pasta = Path(str(cofre.DIR))
    try:
        alvo = pasta if pasta.exists() else pasta.parent
        gravavel = os.access(str(alvo), os.W_OK)
    except Exception:  # noqa: BLE001
        gravavel = False

    sugerida = Path(str(getattr(_cfg, "DATA", "data"))) / "cofre"
    try:
        base = sugerida if sugerida.exists() else sugerida.parent
        sugerida_ok = os.access(str(base), os.W_OK)
    except Exception:  # noqa: BLE001
        sugerida_ok = False

    return {
        "cofre_pasta": str(pasta),
        "cofre_gravavel": bool(gravavel),
        "cofre_sugestao": str(sugerida),
        "cofre_sugestao_ok": bool(sugerida_ok),
        "cofre_corporativo": _cofre_corporativo_ok(),
    }


def _cofre_corporativo_ok() -> bool:
    try:
        from core import cofre
        return bool(cofre.corporativo_disponivel())
    except Exception:  # noqa: BLE001
        return False


@router.get("/api/obsolescencia/credencial")
def credencial_status(req: Request):
    """Se a credencial está no cofre — nunca devolve a senha."""
    _exigir_admin(req)
    usuario, senha = credencial_mdm()
    diag = diagnostico_cofre()
    return {"configurada": bool(usuario and senha),
            "usuario": usuario, "chave_usuario": CHAVE_USUARIO,
            "chave_senha": CHAVE_SENHA, **diag}


class CredencialMDM(BaseModel):
    usuario: str
    senha: str


@router.post("/api/obsolescencia/credencial")
def credencial_gravar(req: Request, corpo: CredencialMDM):
    """Guarda a credencial de serviço no cofre. Só admin do portal."""
    sd = _exigir_admin(req)
    from core import cofre
    usuario = (corpo.usuario or "").strip()
    if not usuario or not corpo.senha:
        raise HTTPException(400, "Informe usuário e senha.")
    if "\\" not in usuario and "/" not in usuario:
        raise HTTPException(400, "O usuário do MDM precisa do domínio: renner\\<login>.")
    try:
        cofre.definir(CHAVE_USUARIO, usuario)
        cofre.definir(CHAVE_SENHA, corpo.senha)
    except Exception as exc:  # noqa: BLE001
        # Sem isto vira 500 em texto puro, e a tela mostra só "unexpected
        # token" — que não ajuda ninguém a descobrir que é permissão.
        _log.error("Falha ao gravar credencial no cofre: %s", exc, exc_info=True)
        diag = diagnostico_cofre()
        dica = ""
        if diag.get("cofre_sugestao_ok"):
            dica = (f" A pasta da aplicação costuma ser somente leitura, e deve ser. "
                    f"Aponte o cofre para a área gravável do projeto: acrescente "
                    f"PORTAL_COFRE_DIR={diag['cofre_sugestao']} ao data/environment "
                    f"e reinicie o serviço.")
        raise HTTPException(
            500, f"Não foi possível gravar no cofre em {diag['cofre_pasta']}.{dica} "
                 f"Detalhe: {exc}") from exc
    global _sessao_mdm
    _sessao_mdm = None                      # credencial nova, sessão velha não serve
    _log.info("Credencial do MDM atualizada por %s", sd.get("username", ""))
    return {"ok": True, "configurada": True, "usuario": usuario}


@router.post("/api/obsolescencia/coletar")
def coletar(req: Request):
    """Varre o parque de coletores e grava. Sob demanda, por botão.

    Sem agendamento por ora, por decisão da área. Somente leitura no MDM:
    esta rota nunca chama escrita.
    """
    sd = _exigir_admin(req)
    from integracoes import mdm_airwatch as mdm
    import db.obsolescencia as _db
    _db.init_db()

    base = getattr(_cfg, "MDM_BASE_URL", "")
    sessao = sessao_mdm()
    try:
        varredura = mdm.varrer(sessao, base)
    except mdm.SessaoExpirada:
        # Uma segunda tentativa com login novo; se cair de novo, é problema real.
        varredura = mdm.varrer(sessao_mdm(forcar=True), base)

    for c in varredura["coletores"]:
        c["tipo"] = _db.COLETOR
    resultado = aplicar_coleta(
        varredura["coletores"], usuario=sd.get("username", ""),
        total_mdm=varredura["total"], paginas=varredura["paginas"])
    _log.info("Coleta do MDM por %s: %s", sd.get("username", ""), resultado)
    return {"ok": True, **resultado}


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
    sem_update = [m.strip() for m in (cfg.get("modelos_sem_update") or "").split(",") if m.strip()]
    versao_minima = cfg.get("versao_os_minima") or ""
    try:
        limite_anos = float(cfg.get("limite_anos") or LIMITE_ANOS)
    except ValueError:
        limite_anos = LIMITE_ANOS
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
            travado = bruto.get("android_travado")
            if travado is None:
                travado = android_travado(bruto.get("versao_os"), bruto.get("modelo"),
                                          versao_minima, sem_update)
            aval = avaliar_obsolescencia(
                {"modelo": bruto.get("modelo"),
                 "data_aquisicao": bruto.get("data_aquisicao"),
                 "android_travado": travado},
                eol, agora, modo, limite_anos)

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
            linha.visto_em = interpretar_data(bruto.get("visto_em"))
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


# ── PDVs, via ServiceNow ──────────────────────────────────────────
# Vêm da CMDB (cmdb_ci_computer), filtrados por origem da descoberta
# ACC_VISIBILITY e status Instalado. A integração com o ServiceNow já
# existe no portal, então aqui só traduzimos o registro para o mesmo
# formato dos coletores — o painel não precisa saber a origem.
CAMPOS_PDV = ("sys_id,name,serial_number,model_id,manufacturer,location,"
              "install_status,os,os_version,last_discovered,sys_updated_on")


def _texto_sn(valor) -> str:
    """O JSONv2 devolve referência ora como texto, ora como {value,display}."""
    if isinstance(valor, dict):
        return str(valor.get("display_value") or valor.get("value") or "").strip()
    return str(valor or "").strip()


def traduzir_pdv(reg: dict) -> dict:
    """Registro da CMDB no mesmo formato que o parser do MDM devolve."""
    return {
        "id": _texto_sn(reg.get("sys_id")),
        "tipo": "pdv",
        "nome": _texto_sn(reg.get("name")),
        "serie": _texto_sn(reg.get("serial_number")),
        "modelo": _texto_sn(reg.get("model_id")),
        "fabricante": _texto_sn(reg.get("manufacturer")),
        "local": _texto_sn(reg.get("location")),
        "plataforma": _texto_sn(reg.get("os")) or "PDV",
        "versao_os": _texto_sn(reg.get("os_version")),
        "conformidade": _texto_sn(reg.get("install_status")),
        "visto_em": _texto_sn(reg.get("last_discovered")) or _texto_sn(reg.get("sys_updated_on")),
        # A loja do PDV vem do Local, não de um usuário <sigla><n>_coletor.
        "usuario": "",
        "tags": [],
    }


def coletar_pdvs(sessao_sn) -> list[dict]:
    """Todos os PDVs instalados, por local. Somente leitura."""
    from routers.servicenow import _sn_query_all
    import db.obsolescencia as _db

    cfg = _db.ler_config()
    registros = _sn_query_all(
        sessao_sn, cfg.get("pdv_tabela", "cmdb_ci_computer"),
        query=cfg.get("pdv_query", ""), fields=CAMPOS_PDV,
        page_size=500, max_records=100000)
    return [traduzir_pdv(r) for r in registros]


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

    # Por modelo e por versão de Android: é onde a obsolescência aparece
    # antes de virar número — e é o que o widget do MDM esconde.
    import json as _json
    por_modelo: dict[str, dict] = {}
    por_versao: dict[str, int] = {}
    criterios_qtd = {"idade_5_anos": 0, "android_travado": 0, "modelo_eol": 0}
    em_risco = 0
    for c in ativos:
        m = por_modelo.setdefault(c.modelo or "?", {"modelo": c.modelo or "?", "coletores": 0,
                                                    "obsoletos": 0, "idades": []})
        m["coletores"] += 1
        m["obsoletos"] += 1 if c.obsoleto else 0
        if c.idade_anos is not None:
            m["idades"].append(c.idade_anos)
        v = (c.versao_os or "?").split(".")[0]
        por_versao[v] = por_versao.get(v, 0) + 1
        try:
            crit = _json.loads(c.criterios or "{}")
        except ValueError:
            crit = {}
        atendidos = 0
        for k in criterios_qtd:
            if crit.get(k):
                criterios_qtd[k] += 1
                atendidos += 1
        if atendidos >= 2 and not c.obsoleto:
            em_risco += 1

    return {
        "em_risco": em_risco,
        "criterios": criterios_qtd,
        "por_modelo": sorted(
            ({"modelo": m["modelo"], "coletores": m["coletores"], "obsoletos": m["obsoletos"],
              "idade_media": round(sum(m["idades"]) / len(m["idades"]), 1) if m["idades"] else None}
             for m in por_modelo.values()), key=lambda x: -x["coletores"]),
        "por_versao_os": sorted(({"versao": k, "coletores": v} for k, v in por_versao.items()),
                                key=lambda x: -x["coletores"]),
        "limites": {"anos": cfg.get("limite_anos"), "versao_os_minima": cfg.get("versao_os_minima"),
                    "modelos_eol": cfg.get("modelos_eol")},
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
