"""Planejamento de compras — aba do Orçamento Spare.

Por item configurado: histórico mensal de consumo do estoque de
**reposição**, previsão para os próximos meses e necessidade de compra.

De onde vem cada número:

- consumo real: Separação — solicitações enviadas cujo tipo de
  atendimento consome o estoque de reposição (inauguração e reforma
  ficam de fora), por mês de envio e modelo;
- consumo imputado: meses anteriores à data de início do sistema,
  digitados ou importados por planilha (`pln_historico`);
- estoque atual: campo do item, ou contagem no ServiceNow dos modelos
  do item no estoque de reposição (mesma consulta da Separação).

A permissão é a do próprio Orçamento Spare: quem lê a tela lê o
planejamento; quem edita (edit/admin) altera itens, histórico e
configuração.
"""
from __future__ import annotations

import io
import logging
import math
import re
from collections import defaultdict
from datetime import date, datetime, timezone

from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from pydantic import BaseModel
from sqlalchemy import select, func

import db.planejamento as db
from core.previsao import prever, necessidade, meses_entre
from core.security import get_session, check_rate_limit

_log = logging.getLogger("planejamento")
router = APIRouter(prefix="/api/planejamento", tags=["planejamento"])

MODULO = "orcamento_spare"
_RE_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ── Permissão: a do Orçamento Spare ──────────────────────────────────
def _nivel(sd: dict) -> str:
    """view | edit | admin | "" — mesma régua da tela do Orçamento Spare."""
    if sd.get("is_admin"):
        return "admin"
    try:
        from routers.orcamento_spare_exec import _rt
        return _rt._nivel_efetivo(sd)
    except Exception:  # noqa: BLE001 — sem o clone, vale a grade do portal
        perms = (sd.get("permission_map") or {}).get(MODULO, {})
        if perms.get("can_admin"):
            return "admin"
        if perms.get("can_edit"):
            return "edit"
        return "view" if perms.get("can_view") else ""


def _exigir(req: Request, acao: str = "view") -> dict:
    sd = get_session(req)
    nivel = _nivel(sd)
    ordem = {"view": 1, "edit": 2, "admin": 3}
    if ordem.get(nivel, 0) < ordem[acao]:
        raise HTTPException(403, "Permissão insuficiente para este módulo.")
    return sd


# ── Consumo real: Separação ──────────────────────────────────────────
def _tipos_reposicao(cfg_sep: dict) -> list[str]:
    import db.separacao as dsep
    return [t for t in dsep.TIPOS_ATENDIMENTO
            if (cfg_sep.get(f"estoque_{t}") or "reposicao") == "reposicao"]


def consumo_real_por_modelo() -> tuple[dict[str, dict[str, int]], str | None]:
    """{modelo: {AAAA-MM: quantidade}} das saídas de reposição, e o primeiro mês.

    A quantidade de um item é o número de unidades bipadas; sem unidade
    registrada, a quantidade pedida.
    """
    try:
        import db.separacao as dsep
    except Exception:  # noqa: BLE001
        return {}, None
    cfg = dsep.ler_config()
    tipos = _tipos_reposicao(cfg)
    saida: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    primeiro = None
    with dsep.SessionLocal() as s:
        unidades = dict(s.execute(
            select(dsep.Unidade.item_id, func.count(dsep.Unidade.id))
            .group_by(dsep.Unidade.item_id)).all())
        linhas = s.execute(
            select(dsep.Item.id, dsep.Item.modelo, dsep.Item.quantidade,
                   dsep.Solicitacao.enviada_em)
            .join(dsep.Solicitacao, dsep.Solicitacao.id == dsep.Item.solicitacao_id)
            .where(dsep.Solicitacao.estado == dsep.ENVIADA,
                   dsep.Solicitacao.enviada_em.isnot(None),
                   dsep.Solicitacao.tipo_atendimento.in_(tipos))
        ).all()
    for item_id, modelo, qtd, enviada in linhas:
        mes = enviada.strftime("%Y-%m")
        q = unidades.get(item_id) or int(qtd or 0)
        saida[(modelo or "").strip()][mes] += q
        if primeiro is None or mes < primeiro:
            primeiro = mes
    return {k: dict(v) for k, v in saida.items()}, primeiro


def modelos_conhecidos() -> list[str]:
    """Modelos vistos na Separação (o que a área de fato pede)."""
    try:
        import db.separacao as dsep
        with dsep.SessionLocal() as s:
            return sorted({(m or "").strip() for (m,) in s.execute(select(dsep.Item.modelo).distinct()) if m})
    except Exception:  # noqa: BLE001
        return []


# ── Itens ─────────────────────────────────────────────────────────────
def _modelos_lista(txt: str) -> list[str]:
    return [m.strip() for m in (txt or "").split("|") if m.strip()]


def _item_dict(i: db.Item) -> dict:
    return {
        "id": i.id, "nome": i.nome, "modelos": _modelos_lista(i.modelos),
        "categoria": i.categoria or "",
        "estoque_atual": int(i.estoque_atual or 0),
        "estoque_atualizado_em": i.estoque_atualizado_em.isoformat() if i.estoque_atualizado_em else None,
        "estoque_origem": i.estoque_origem or "",
        "pedidos_abertos": int(i.pedidos_abertos or 0),
        "lead_time_dias": int(i.lead_time_dias or 0),
        "seguranca_dias": int(i.seguranca_dias or 0),
        "custo_unitario": float(i.custo_unitario or 0),
        "observacao": i.observacao or "", "ativo": bool(i.ativo),
        "item_ebs": i.item_ebs or "",
        "atualizado_por": i.atualizado_por or "",
    }


class ItemIn(BaseModel):
    nome: str | None = None
    modelos: list[str] | None = None
    categoria: str | None = None
    estoque_atual: int | None = None
    pedidos_abertos: int | None = None
    lead_time_dias: int | None = None
    seguranca_dias: int | None = None
    custo_unitario: float | None = None
    observacao: str | None = None
    ativo: bool | None = None
    item_ebs: str | None = None


def _aplicar(i: db.Item, body: ItemIn, usuario: str) -> None:
    d = body.model_dump(exclude_none=True)
    if "nome" in d:
        nome = d["nome"].strip()[:160]
        if not nome:
            raise HTTPException(400, "Nome obrigatório.")
        i.nome = nome
    if "modelos" in d:
        i.modelos = "|".join(dict.fromkeys(m.strip()[:160] for m in d["modelos"] if m.strip()))
    if "categoria" in d:
        i.categoria = d["categoria"].strip()[:80]
    for campo in ("estoque_atual", "pedidos_abertos", "lead_time_dias", "seguranca_dias"):
        if campo in d:
            v = int(d[campo])
            if v < 0 or v > 1_000_000:
                raise HTTPException(400, f"Valor inválido em {campo}.")
            setattr(i, campo, v)
    if "estoque_atual" in d:
        i.estoque_atualizado_em = db.utcnow()
        i.estoque_origem = "manual"
    if "custo_unitario" in d:
        v = float(d["custo_unitario"])
        if v < 0:
            raise HTTPException(400, "Custo unitário não pode ser negativo.")
        i.custo_unitario = v
    if "observacao" in d:
        i.observacao = d["observacao"].strip()[:2000]
    if "ativo" in d:
        i.ativo = bool(d["ativo"])
    if "item_ebs" in d:
        i.item_ebs = d["item_ebs"].strip()[:60]
    i.atualizado_por = usuario


@router.get("/itens")
def listar_itens(req: Request):
    _exigir(req, "view")
    with db.SessionLocal() as s:
        itens = s.execute(select(db.Item).order_by(db.Item.nome)).scalars().all()
        return {"itens": [_item_dict(i) for i in itens]}


@router.post("/itens", status_code=201)
def criar_item(body: ItemIn, req: Request):
    sd = _exigir(req, "edit")
    check_rate_limit(req)
    if not (body.nome or "").strip():
        raise HTTPException(400, "Nome obrigatório.")
    with db.SessionLocal() as s:
        if s.execute(select(db.Item).where(func.lower(db.Item.nome) == body.nome.strip().lower())).scalar_one_or_none():
            raise HTTPException(409, "Já existe um item com esse nome.")
        i = db.Item(nome=body.nome.strip())
        _aplicar(i, body, sd.get("username", ""))
        s.add(i)
        s.commit()
        return _item_dict(i)


@router.patch("/itens/{item_id}")
def alterar_item(item_id: int, body: ItemIn, req: Request):
    sd = _exigir(req, "edit")
    with db.SessionLocal() as s:
        i = s.get(db.Item, item_id)
        if not i:
            raise HTTPException(404, "Item não encontrado.")
        _aplicar(i, body, sd.get("username", ""))
        s.commit()
        return _item_dict(i)


@router.delete("/itens/{item_id}", status_code=204)
def excluir_item(item_id: int, req: Request):
    _exigir(req, "edit")
    with db.SessionLocal() as s:
        i = s.get(db.Item, item_id)
        if not i:
            raise HTTPException(404, "Item não encontrado.")
        s.delete(i)
        s.commit()
    return None


@router.get("/modelos")
def listar_modelos(req: Request):
    _exigir(req, "view")
    return {"modelos": modelos_conhecidos()}


@router.post("/itens/{item_id}/estoque-servicenow")
def estoque_servicenow(item_id: int, req: Request):
    """Conta no ServiceNow os ativos dos modelos do item no estoque de reposição."""
    sd = _exigir(req, "edit")
    check_rate_limit(req)
    with db.SessionLocal() as s:
        i = s.get(db.Item, item_id)
        if not i:
            raise HTTPException(404, "Item não encontrado.")
        modelos = {m.lower() for m in _modelos_lista(i.modelos)}
    if not modelos:
        raise HTTPException(400, "O item não tem modelos configurados.")
    from routers.separacao import catalogo
    import db.separacao as dsep
    tipo = (_tipos_reposicao(dsep.ler_config()) or [dsep.FRENTE_RETAGUARDA])[0]
    cat = catalogo(req, tipo)
    saldo = sum(x["saldo"] for x in cat["itens"] if (x["modelo"] or "").strip().lower() in modelos)
    with db.SessionLocal() as s:
        i = s.get(db.Item, item_id)
        i.estoque_atual = int(saldo)
        i.estoque_atualizado_em = db.utcnow()
        i.estoque_origem = "servicenow"
        i.atualizado_por = sd.get("username", "")
        s.commit()
        return _item_dict(i)


# ── Configuração ──────────────────────────────────────────────────────
class ConfigIn(BaseModel):
    data_inicio_sistema: str | None = None
    horizonte_meses: int | None = None
    meses_historico: int | None = None
    acordo_alerta_dias: int | None = None


def _config() -> dict:
    cfg = db.ler_config()
    inicio = (cfg.get("data_inicio_sistema") or "").strip()
    if not _RE_MES.match(inicio):
        _, primeiro = consumo_real_por_modelo()
        inicio = primeiro or ""
    try:
        horizonte = max(1, min(36, int(cfg.get("horizonte_meses") or 12)))
    except ValueError:
        horizonte = 12
    try:
        meses_hist = max(6, min(60, int(cfg.get("meses_historico") or 24)))
    except ValueError:
        meses_hist = 24
    try:
        alerta = max(0, min(365, int(cfg.get("acordo_alerta_dias") or 60)))
    except ValueError:
        alerta = 60
    return {"data_inicio_sistema": inicio, "data_inicio_configurada": bool(_RE_MES.match(cfg.get("data_inicio_sistema") or "")),
            "horizonte_meses": horizonte, "meses_historico": meses_hist, "acordo_alerta_dias": alerta}


@router.get("/config")
def ler_configuracao(req: Request):
    _exigir(req, "view")
    return _config()


@router.put("/config")
def gravar_configuracao(body: ConfigIn, req: Request):
    _exigir(req, "edit")
    pares = {}
    if body.data_inicio_sistema is not None:
        v = body.data_inicio_sistema.strip()
        if v and not _RE_MES.match(v):
            raise HTTPException(400, "Data de início inválida (use AAAA-MM).")
        pares["data_inicio_sistema"] = v
    if body.horizonte_meses is not None:
        if not 1 <= body.horizonte_meses <= 36:
            raise HTTPException(400, "Horizonte entre 1 e 36 meses.")
        pares["horizonte_meses"] = str(body.horizonte_meses)
    if body.meses_historico is not None:
        if not 6 <= body.meses_historico <= 60:
            raise HTTPException(400, "Histórico entre 6 e 60 meses.")
        pares["meses_historico"] = str(body.meses_historico)
    if body.acordo_alerta_dias is not None:
        if not 0 <= body.acordo_alerta_dias <= 365:
            raise HTTPException(400, "Alerta de acordo entre 0 e 365 dias.")
        pares["acordo_alerta_dias"] = str(body.acordo_alerta_dias)
    if pares:
        db.gravar_config(pares)
    return _config()


# ── Histórico consolidado ─────────────────────────────────────────────
def _mes_atual() -> str:
    h = date.today()
    return f"{h.year:04d}-{h.month:02d}"


def serie_do_item(item: db.Item, real: dict[str, dict[str, int]], imputado: dict[str, int],
                  inicio_sistema: str, ate: str, desde: str | None = None) -> list[dict]:
    """Um ponto por mês de `desde` a `ate`: real quando o mês está no sistema, senão imputado."""
    modelos = _modelos_lista(item.modelos)
    real_item: dict[str, int] = defaultdict(int)
    for m in modelos:
        for mes, q in real.get(m, {}).items():
            real_item[mes] += q
    if desde is None:
        candidatos = list(imputado) + list(real_item)
        desde = min(candidatos) if candidatos else ate
    pontos = []
    for mes in meses_entre(desde, ate):
        no_sistema = bool(inicio_sistema) and mes >= inicio_sistema
        if no_sistema:
            pontos.append({"mes": mes, "quantidade": real_item.get(mes, 0), "origem": "real"})
        else:
            pontos.append({"mes": mes, "quantidade": imputado.get(mes, 0), "origem": "imputado"})
    return pontos


def _imputados(s, item_ids: list[int]) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = defaultdict(dict)
    if not item_ids:
        return out
    for h in s.execute(select(db.Historico).where(db.Historico.item_id.in_(item_ids))).scalars():
        out[h.item_id][h.mes] = int(h.quantidade or 0)
    return out


@router.get("/historico")
def historico(req: Request, meses: int | None = None):
    """Grade mês × item. Meses antes do início do sistema são editáveis."""
    _exigir(req, "view")
    cfg = _config()
    n = max(6, min(60, int(meses or cfg["meses_historico"])))
    ate = _mes_atual()
    desde = ate
    for _ in range(n - 1):
        a, m = int(desde[:4]), int(desde[5:7])
        desde = f"{a - 1:04d}-12" if m == 1 else f"{a:04d}-{m - 1:02d}"
    real, _ = consumo_real_por_modelo()
    with db.SessionLocal() as s:
        itens = s.execute(select(db.Item).where(db.Item.ativo.is_(True)).order_by(db.Item.nome)).scalars().all()
        imput = _imputados(s, [i.id for i in itens])
        linhas = [{"item": _item_dict(i),
                   "serie": serie_do_item(i, real, imput.get(i.id, {}), cfg["data_inicio_sistema"], ate, desde)}
                  for i in itens]
    return {"meses": meses_entre(desde, ate), "inicio_sistema": cfg["data_inicio_sistema"],
            "linhas": linhas}


class HistoricoIn(BaseModel):
    item_id: int
    mes: str
    quantidade: int


@router.put("/historico")
def gravar_historico(body: list[HistoricoIn], req: Request):
    """Grava imputados. Recusa mês já coberto pelo sistema."""
    sd = _exigir(req, "edit")
    cfg = _config()
    inicio = cfg["data_inicio_sistema"]
    gravados, recusados = 0, []
    with db.SessionLocal() as s:
        for h in body:
            if not _RE_MES.match(h.mes):
                recusados.append({"mes": h.mes, "motivo": "mês inválido"}); continue
            if inicio and h.mes >= inicio:
                recusados.append({"mes": h.mes, "motivo": "mês coberto pela Separação"}); continue
            if h.quantidade < 0 or h.quantidade > 1_000_000:
                recusados.append({"mes": h.mes, "motivo": "quantidade inválida"}); continue
            if not s.get(db.Item, h.item_id):
                recusados.append({"mes": h.mes, "motivo": "item inexistente"}); continue
            linha = s.execute(select(db.Historico).where(
                db.Historico.item_id == h.item_id, db.Historico.mes == h.mes)).scalar_one_or_none()
            if linha is None:
                s.add(db.Historico(item_id=h.item_id, mes=h.mes, quantidade=h.quantidade,
                                   atualizado_por=sd.get("username", "")))
            else:
                linha.quantidade = h.quantidade
                linha.atualizado_por = sd.get("username", "")
            gravados += 1
        s.commit()
    return {"gravados": gravados, "recusados": recusados}


def _ler_planilha(nome: str, conteudo: bytes) -> list[dict]:
    import pandas as pd
    nome = (nome or "").lower()
    if nome.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(conteudo))
    else:
        texto = conteudo.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        df = pd.read_csv(io.StringIO(texto), sep=sep)
    df.columns = [str(c).strip().lower() for c in df.columns]
    col = {}
    for alvo, opcoes in (("item", ("item", "nome", "produto")), ("mes", ("mes", "mês", "competencia", "competência", "periodo", "período")),
                         ("quantidade", ("quantidade", "qtd", "qtde", "consumo", "saida", "saída"))):
        for o in opcoes:
            if o in df.columns:
                col[alvo] = o
                break
        if alvo not in col:
            raise HTTPException(400, f"Planilha sem a coluna '{alvo}'. Esperado: item, mês, quantidade.")
    linhas = []
    for _, r in df.iterrows():
        item = str(r[col["item"]]).strip()
        bruto = r[col["mes"]]
        if isinstance(bruto, (datetime, date)):
            mes = f"{bruto.year:04d}-{bruto.month:02d}"
        else:
            t = str(bruto).strip()
            m = re.match(r"^(\d{4})-(\d{1,2})", t) or re.match(r"^(\d{1,2})/(\d{4})$", t)
            if not m:
                continue
            a, mm = (m.group(1), m.group(2)) if len(m.group(1)) == 4 else (m.group(2), m.group(1))
            mes = f"{int(a):04d}-{int(mm):02d}"
        try:
            q = int(float(r[col["quantidade"]]))
        except (TypeError, ValueError):
            continue
        if item and _RE_MES.match(mes):
            linhas.append({"item": item, "mes": mes, "quantidade": q})
    return linhas


@router.post("/historico/importar")
async def importar_historico(req: Request, arquivo: UploadFile = File(...)):
    """CSV/XLSX com colunas item, mês (AAAA-MM ou MM/AAAA) e quantidade."""
    sd = _exigir(req, "edit")
    check_rate_limit(req)
    conteudo = await arquivo.read()
    if len(conteudo) > 5 * 1024 * 1024:
        raise HTTPException(400, "Arquivo acima de 5 MB.")
    linhas = _ler_planilha(arquivo.filename or "", conteudo)
    cfg = _config()
    inicio = cfg["data_inicio_sistema"]
    with db.SessionLocal() as s:
        por_nome = {i.nome.lower(): i.id for i in s.execute(select(db.Item)).scalars()}
    itens_desconhecidos = sorted({l["item"] for l in linhas if l["item"].lower() not in por_nome})
    corpo = [HistoricoIn(item_id=por_nome[l["item"].lower()], mes=l["mes"], quantidade=l["quantidade"])
             for l in linhas if l["item"].lower() in por_nome and not (inicio and l["mes"] >= inicio)]
    ignorados_sistema = sum(1 for l in linhas if l["item"].lower() in por_nome and inicio and l["mes"] >= inicio)
    r = gravar_historico(corpo, req) if corpo else {"gravados": 0, "recusados": []}
    _log.info("planejamento: %s importou %d linha(s) de histórico", sd.get("username", "?"), r["gravados"])
    return {"lidas": len(linhas), "gravadas": r["gravados"], "itens_desconhecidos": itens_desconhecidos,
            "ignoradas_sistema": ignorados_sistema, "recusadas": r["recusados"]}


# ── Previsão e necessidade ────────────────────────────────────────────
@router.get("/previsao")
def previsao(req: Request, horizonte: int | None = None):
    _exigir(req, "view")
    cfg = _config()
    h = max(1, min(36, int(horizonte or cfg["horizonte_meses"])))
    real, _ = consumo_real_por_modelo()
    # O mês corrente ainda está andando: a série termina no mês anterior.
    atual = _mes_atual()
    a, m = int(atual[:4]), int(atual[5:7])
    ultimo_fechado = f"{a - 1:04d}-12" if m == 1 else f"{a:04d}-{m - 1:02d}"
    saida = []
    alerta_dias = cfg["acordo_alerta_dias"]
    with db.SessionLocal() as s:
        itens = s.execute(select(db.Item).where(db.Item.ativo.is_(True)).order_by(db.Item.nome)).scalars().all()
        imput = _imputados(s, [i.id for i in itens])
        acordos = _acordos_por_item_ebs(s)
        for i in itens:
            serie = serie_do_item(i, real, imput.get(i.id, {}), cfg["data_inicio_sistema"], ultimo_fechado)
            # Corta zeros iniciais: mês antes do primeiro consumo não é "zero", é ausência.
            while serie and serie[0]["quantidade"] == 0 and len(serie) > 1:
                serie.pop(0)
            hist = [(p["mes"], p["quantidade"]) for p in serie]
            prev = prever(hist, h)
            acordo = acordo_do_item(i, acordos)
            nec = necessidade(prev, estoque=i.estoque_atual or 0, pedidos_abertos=i.pedidos_abertos or 0,
                              lead_time_dias=i.lead_time_dias or 0, seguranca_dias=i.seguranca_dias or 0,
                              custo_unitario=custo_do_item(i, acordo))
            saida.append({"item": _item_dict(i), "historico": serie[-24:], "previsao": prev, "necessidade": nec,
                          "acordo": _acordo_resumo(acordo, alerta_dias)})
    total = sum(x["necessidade"]["valor"] for x in saida)
    total90 = sum(x["necessidade"]["valor_p90"] for x in saida)
    proximo = min((x["necessidade"]["data_limite_pedido"] for x in saida if x["necessidade"]["data_limite_pedido"]), default=None)
    return {"horizonte": h, "ultimo_mes_fechado": ultimo_fechado, "itens": saida,
            "resumo": {"itens": len(saida), "itens_com_compra": sum(1 for x in saida if x["necessidade"]["necessidade"] > 0),
                       "valor": round(total, 2), "valor_p90": round(total90, 2), "proximo_pedido": proximo,
                       "unidades": sum(x["necessidade"]["necessidade"] for x in saida)}}


# ══════════════════════════════════════════════════════════════════
#  Acordos de compra
# ══════════════════════════════════════════════════════════════════
def situacao_acordo(vencimento, alerta_dias: int, hoje: date | None = None) -> str:
    """sem_vencimento | vigente | vence_em_breve | vencido."""
    if not vencimento:
        return "sem_vencimento"
    hoje = hoje or date.today()
    v = vencimento.date() if isinstance(vencimento, datetime) else vencimento
    if v < hoje:
        return "vencido"
    if (v - hoje).days <= alerta_dias:
        return "vence_em_breve"
    return "vigente"


def _acordo_dict(a: db.Acordo, alerta_dias: int = 60) -> dict:
    v = a.vencimento.date().isoformat() if a.vencimento else None
    return {"id": a.id, "item_ebs": a.item_ebs, "descricao": a.descricao or "", "valor": float(a.valor or 0),
            "fornecedor_codigo": a.fornecedor_codigo or "", "fornecedor_nome": a.fornecedor_nome or "",
            "fornecedor": " - ".join(x for x in (a.fornecedor_codigo, a.fornecedor_nome) if x),
            "vencimento": v, "numero": a.numero or "", "observacao": a.observacao or "", "ativo": bool(a.ativo),
            "situacao": situacao_acordo(a.vencimento, alerta_dias),
            "dias_para_vencer": (a.vencimento.date() - date.today()).days if a.vencimento else None,
            "atualizado_por": a.atualizado_por or ""}


def _acordo_resumo(a: db.Acordo | None, alerta_dias: int) -> dict | None:
    if a is None:
        return None
    d = _acordo_dict(a, alerta_dias)
    return {k: d[k] for k in ("id", "item_ebs", "valor", "fornecedor", "vencimento", "situacao", "dias_para_vencer")}


def _acordos_por_item_ebs(s) -> dict[str, list[db.Acordo]]:
    out: dict[str, list[db.Acordo]] = defaultdict(list)
    for a in s.execute(select(db.Acordo).where(db.Acordo.ativo.is_(True))).scalars():
        out[(a.item_ebs or "").strip().lower()].append(a)
    return out


def acordo_do_item(i: db.Item, acordos: dict[str, list[db.Acordo]]) -> db.Acordo | None:
    """O acordo que vale para o item: vigente com vencimento mais distante; senão o mais recente."""
    chave = (i.item_ebs or "").strip().lower()
    if not chave or chave not in acordos:
        return None
    lista = acordos[chave]
    hoje = date.today()
    vigentes = [a for a in lista if a.vencimento is None or a.vencimento.date() >= hoje]
    if vigentes:
        return max(vigentes, key=lambda a: (a.vencimento.date() if a.vencimento else date.max))
    return max(lista, key=lambda a: (a.vencimento.date() if a.vencimento else date.min))


def custo_do_item(i: db.Item, acordo: db.Acordo | None) -> float:
    """Custo digitado no item; em branco, o valor do acordo."""
    if i.custo_unitario and i.custo_unitario > 0:
        return float(i.custo_unitario)
    if acordo is not None and acordo.valor:
        return float(acordo.valor)
    return 0.0


def _interpretar_data(v) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    t = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(t[:19] if "T" in fmt or " " in fmt else t[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise HTTPException(400, f"Data inválida: {t} (use AAAA-MM-DD ou DD/MM/AAAA).")


def _separar_fornecedor(codigo, nome) -> tuple[str, str]:
    """Aceita 'código' e 'nome' separados, ou um só campo '3729 - AIDC'."""
    codigo = (str(codigo) if codigo is not None else "").strip()
    nome = (str(nome) if nome is not None else "").strip()
    if codigo.endswith(".0") and codigo[:-2].isdigit():
        codigo = codigo[:-2]
    for campo in (codigo, nome):
        m = re.match(r"^\s*(\d+)\s*[-–]\s*(.+)$", campo)
        if m and (not nome or campo == nome or not codigo or campo == codigo):
            return m.group(1), m.group(2).strip()
    return codigo[:30], nome[:160]


class AcordoIn(BaseModel):
    item_ebs: str | None = None
    descricao: str | None = None
    valor: float | None = None
    fornecedor_codigo: str | None = None
    fornecedor_nome: str | None = None
    fornecedor: str | None = None          # "3729 - AIDC", alternativa aos dois acima
    vencimento: str | None = None          # AAAA-MM-DD, ou "" para limpar
    numero: str | None = None
    observacao: str | None = None
    ativo: bool | None = None


def _aplicar_acordo(a: db.Acordo, body: AcordoIn, usuario: str) -> None:
    d = body.model_dump(exclude_none=True)
    if "item_ebs" in d:
        v = d["item_ebs"].strip()[:60]
        if not v:
            raise HTTPException(400, "Item do EBS obrigatório.")
        a.item_ebs = v
    if "descricao" in d:
        a.descricao = d["descricao"].strip()[:240]
    if "valor" in d:
        if d["valor"] < 0:
            raise HTTPException(400, "Valor não pode ser negativo.")
        a.valor = float(d["valor"])
    if "fornecedor" in d or "fornecedor_codigo" in d or "fornecedor_nome" in d:
        cod, nome = _separar_fornecedor(d.get("fornecedor_codigo", d.get("fornecedor", a.fornecedor_codigo)),
                                        d.get("fornecedor_nome", a.fornecedor_nome if "fornecedor" not in d else ""))
        a.fornecedor_codigo, a.fornecedor_nome = cod, nome
    if "vencimento" in d:
        a.vencimento = _interpretar_data(d["vencimento"])
    if "numero" in d:
        a.numero = d["numero"].strip()[:60]
    if "observacao" in d:
        a.observacao = d["observacao"].strip()[:2000]
    if "ativo" in d:
        a.ativo = bool(d["ativo"])
    a.atualizado_por = usuario


def _checar_duplicado(s, a: db.Acordo) -> None:
    outro = s.execute(select(db.Acordo).where(
        func.lower(db.Acordo.item_ebs) == a.item_ebs.lower(),
        db.Acordo.fornecedor_codigo == a.fornecedor_codigo,
        db.Acordo.id != (a.id or 0))).scalar_one_or_none()
    if outro:
        raise HTTPException(409, f"Já existe acordo do item {a.item_ebs} com o fornecedor {a.fornecedor_codigo or '(sem código)'}.")


@router.get("/acordos")
def listar_acordos(req: Request):
    _exigir(req, "view")
    alerta = _config()["acordo_alerta_dias"]
    with db.SessionLocal() as s:
        lista = s.execute(select(db.Acordo).order_by(db.Acordo.item_ebs, db.Acordo.fornecedor_codigo)).scalars().all()
        acordos = [_acordo_dict(a, alerta) for a in lista]
    return {"acordos": acordos, "alerta_dias": alerta,
            "resumo": {"total": len(acordos),
                       "vencidos": sum(1 for a in acordos if a["ativo"] and a["situacao"] == "vencido"),
                       "vencem_em_breve": sum(1 for a in acordos if a["ativo"] and a["situacao"] == "vence_em_breve")}}


@router.post("/acordos", status_code=201)
def criar_acordo(body: AcordoIn, req: Request):
    sd = _exigir(req, "edit")
    check_rate_limit(req)
    if not (body.item_ebs or "").strip():
        raise HTTPException(400, "Item do EBS obrigatório.")
    with db.SessionLocal() as s:
        a = db.Acordo(item_ebs=body.item_ebs.strip())
        _aplicar_acordo(a, body, sd.get("username", ""))
        _checar_duplicado(s, a)
        s.add(a)
        s.commit()
        return _acordo_dict(a, _config()["acordo_alerta_dias"])


@router.patch("/acordos/{acordo_id}")
def alterar_acordo(acordo_id: int, body: AcordoIn, req: Request):
    sd = _exigir(req, "edit")
    with db.SessionLocal() as s:
        a = s.get(db.Acordo, acordo_id)
        if not a:
            raise HTTPException(404, "Acordo não encontrado.")
        _aplicar_acordo(a, body, sd.get("username", ""))
        _checar_duplicado(s, a)
        s.commit()
        return _acordo_dict(a, _config()["acordo_alerta_dias"])


@router.delete("/acordos/{acordo_id}", status_code=204)
def excluir_acordo(acordo_id: int, req: Request):
    _exigir(req, "edit")
    with db.SessionLocal() as s:
        a = s.get(db.Acordo, acordo_id)
        if not a:
            raise HTTPException(404, "Acordo não encontrado.")
        s.delete(a)
        s.commit()
    return None


_COLUNAS_ACORDO = {
    "item_ebs": ("item", "item ebs", "item_ebs", "codigo", "código", "cod item", "item do ebs"),
    "descricao": ("descricao", "descrição", "desc", "descricao do item", "descrição do item"),
    "valor": ("valor", "preco", "preço", "valor unitario", "valor unitário", "unitario", "unitário"),
    "fornecedor_codigo": ("codigo fornecedor", "código fornecedor", "cod fornecedor", "cód fornecedor", "fornecedor codigo", "fornecedor_codigo", "cod_fornecedor"),
    "fornecedor_nome": ("fornecedor", "nome fornecedor", "fornecedor nome", "fornecedor_nome"),
    "vencimento": ("vencimento", "validade", "vigencia", "vigência", "data vencimento", "fim", "vence em"),
    "numero": ("acordo", "numero", "número", "contrato", "nº acordo", "numero acordo"),
}


def _ler_planilha_acordos(nome: str, conteudo: bytes) -> list[dict]:
    import pandas as pd
    nome = (nome or "").lower()
    if nome.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(conteudo))
    else:
        texto = conteudo.decode("utf-8-sig", errors="replace")
        sep = ";" if texto.count(";") > texto.count(",") else ","
        df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str)
    df.columns = [str(c).strip().lower() for c in df.columns]
    col = {}
    for alvo, opcoes in _COLUNAS_ACORDO.items():
        for o in opcoes:
            if o in df.columns:
                col[alvo] = o
                break
    for obrig in ("item_ebs", "valor"):
        if obrig not in col:
            raise HTTPException(400, "Planilha sem a coluna '" + ("item" if obrig == "item_ebs" else obrig) +
                                     "'. Esperado: item, descrição, valor, código fornecedor, fornecedor, vencimento.")
    linhas = []
    for _, r in df.iterrows():
        def pega(k):
            v = r[col[k]] if k in col else None
            if v is None or (isinstance(v, float) and v != v):   # NaN
                return None
            return v
        item = str(pega("item_ebs") or "").strip()
        if item.endswith(".0") and item[:-2].isdigit():
            item = item[:-2]
        if not item:
            continue
        bruto = pega("valor")
        try:
            valor = float(str(bruto).replace("R$", "").replace(".", "").replace(",", ".").strip()) if isinstance(bruto, str) else float(bruto or 0)
        except ValueError:
            continue
        cod, nome_f = _separar_fornecedor(pega("fornecedor_codigo"), pega("fornecedor_nome"))
        linhas.append({"item_ebs": item[:60], "descricao": str(pega("descricao") or "").strip()[:240], "valor": valor,
                       "fornecedor_codigo": cod, "fornecedor_nome": nome_f,
                       "vencimento": pega("vencimento"), "numero": str(pega("numero") or "").strip()[:60]})
    return linhas


@router.post("/acordos/importar")
async def importar_acordos(req: Request, arquivo: UploadFile = File(...)):
    """CSV/XLSX: item, descrição, valor, código fornecedor, fornecedor (ou '3729 - AIDC'), vencimento."""
    sd = _exigir(req, "edit")
    check_rate_limit(req)
    conteudo = await arquivo.read()
    if len(conteudo) > 5 * 1024 * 1024:
        raise HTTPException(400, "Arquivo acima de 5 MB.")
    linhas = _ler_planilha_acordos(arquivo.filename or "", conteudo)
    usuario = sd.get("username", "")
    novos, atualizados, recusados = 0, 0, []
    with db.SessionLocal() as s:
        for l in linhas:
            try:
                venc = _interpretar_data(l["vencimento"])
            except HTTPException as exc:
                recusados.append({"item_ebs": l["item_ebs"], "motivo": exc.detail}); continue
            a = s.execute(select(db.Acordo).where(
                func.lower(db.Acordo.item_ebs) == l["item_ebs"].lower(),
                db.Acordo.fornecedor_codigo == l["fornecedor_codigo"])).scalar_one_or_none()
            if a is None:
                a = db.Acordo(item_ebs=l["item_ebs"]); s.add(a); novos += 1
            else:
                atualizados += 1
            a.descricao = l["descricao"] or a.descricao
            a.valor = l["valor"]
            a.fornecedor_codigo, a.fornecedor_nome = l["fornecedor_codigo"], l["fornecedor_nome"] or a.fornecedor_nome
            a.vencimento = venc
            if l["numero"]:
                a.numero = l["numero"]
            a.ativo = True
            a.atualizado_por = usuario
        s.commit()
    _log.info("planejamento: %s importou acordos (%d novos, %d atualizados)", usuario, novos, atualizados)
    return {"lidas": len(linhas), "novos": novos, "atualizados": atualizados, "recusados": recusados}


# ══════════════════════════════════════════════════════════════════
#  Obsolescência: previsão de compra para substituir o parque, por BU
# ══════════════════════════════════════════════════════════════════
def parque_obsoleto() -> tuple[dict[str, dict[str, int]], list[dict], str | None]:
    """{modelo: {bu: quantidade}} dos aparelhos obsoletos ativos, a lista de BUs
    e a data da última coleta. O CD entra como BU própria: o aparelho existe
    e vai precisar ser trocado do mesmo jeito."""
    try:
        import db.obsolescencia as dob
    except Exception:  # noqa: BLE001
        return {}, [], None
    matriz: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    bus: dict[str, dict] = {}
    ultima = None
    try:
        with dob.SessionLocal() as s:
            linhas = s.execute(
                select(dob.Coletor.modelo, dob.Coletor.bu, dob.Coletor.bu_nome, dob.Coletor.pais, func.count(dob.Coletor.id))
                .where(dob.Coletor.situacao == dob.ATIVO, dob.Coletor.obsoleto.is_(True))
                .group_by(dob.Coletor.modelo, dob.Coletor.bu, dob.Coletor.bu_nome, dob.Coletor.pais)).all()
            col = s.execute(select(dob.Coleta).order_by(dob.Coleta.id.desc()).limit(1)).scalar_one_or_none()
            ultima = (col.fim or col.inicio).isoformat() if col else None
    except Exception as exc:  # noqa: BLE001 — sem base do MDM, a aba mostra vazio e diz
        _log.warning("planejamento: base de obsolescência indisponível: %s", exc)
        return {}, [], None
    for modelo, bu, bu_nome, pais, n in linhas:
        b = (bu or "").strip() or "?"
        matriz[(modelo or "").strip() or "(sem modelo)"][b] += int(n)
        bus.setdefault(b, {"bu": b, "nome": (bu_nome or b) if b != "?" else "Sem BU", "pais": pais or ""})
    return {k: dict(v) for k, v in matriz.items()}, sorted(bus.values(), key=lambda x: (x["pais"], x["nome"])), ultima


def _sub_dict(x: db.Substituicao) -> dict:
    return {"id": x.id, "modelo_obsoleto": x.modelo_obsoleto, "item_id": x.item_id,
            "custo_unitario": float(x.custo_unitario or 0), "mes_alvo": x.mes_alvo or "",
            "percentual": int(x.percentual if x.percentual is not None else 100),
            "observacao": x.observacao or "", "ativo": bool(x.ativo)}


class SubstituicaoIn(BaseModel):
    modelo_obsoleto: str
    item_id: int | None = None
    custo_unitario: float | None = None
    mes_alvo: str | None = None
    percentual: int | None = None
    observacao: str | None = None
    ativo: bool | None = None


@router.put("/obsolescencia/plano")
def gravar_plano(body: list[SubstituicaoIn], req: Request):
    sd = _exigir(req, "edit")
    with db.SessionLocal() as s:
        for e in body:
            modelo = (e.modelo_obsoleto or "").strip()[:160]
            if not modelo:
                raise HTTPException(400, "Modelo obrigatório.")
            if e.mes_alvo and not _RE_MES.match(e.mes_alvo):
                raise HTTPException(400, f"Mês alvo inválido para {modelo} (use AAAA-MM).")
            if e.percentual is not None and not 0 <= e.percentual <= 100:
                raise HTTPException(400, "Percentual entre 0 e 100.")
            if e.custo_unitario is not None and e.custo_unitario < 0:
                raise HTTPException(400, "Custo não pode ser negativo.")
            if e.item_id is not None and e.item_id > 0 and not s.get(db.Item, e.item_id):
                raise HTTPException(404, f"Item {e.item_id} não encontrado.")
            x = s.execute(select(db.Substituicao).where(db.Substituicao.modelo_obsoleto == modelo)).scalar_one_or_none()
            if x is None:
                x = db.Substituicao(modelo_obsoleto=modelo); s.add(x)
            if e.item_id is not None:
                x.item_id = e.item_id or None
            if e.custo_unitario is not None:
                x.custo_unitario = float(e.custo_unitario)
            if e.mes_alvo is not None:
                x.mes_alvo = e.mes_alvo
            if e.percentual is not None:
                x.percentual = int(e.percentual)
            if e.observacao is not None:
                x.observacao = e.observacao.strip()[:2000]
            if e.ativo is not None:
                x.ativo = bool(e.ativo)
            x.atualizado_por = sd.get("username", "")
        s.commit()
    return previsao_obsolescencia(req)


@router.get("/obsolescencia")
def previsao_obsolescencia(req: Request):
    """Modelo obsoleto × BU, com o plano de substituição e o valor por BU e por mês."""
    _exigir(req, "view")
    cfg = _config()
    matriz, bus, ultima = parque_obsoleto()
    with db.SessionLocal() as s:
        planos = {x.modelo_obsoleto: x for x in s.execute(select(db.Substituicao)).scalars()}
        itens = {i.id: i for i in s.execute(select(db.Item)).scalars()}
        acordos = _acordos_por_item_ebs(s)
    modelos = sorted(set(matriz) | {m for m, x in planos.items() if x.ativo and m in matriz})
    linhas, por_bu, por_mes = [], defaultdict(lambda: {"aparelhos": 0, "unidades": 0, "valor": 0.0}), defaultdict(lambda: {"unidades": 0, "valor": 0.0})
    for m in modelos:
        por_bu_m = matriz.get(m, {})
        total = sum(por_bu_m.values())
        x = planos.get(m)
        item = itens.get(x.item_id) if x and x.item_id else None
        acordo = acordo_do_item(item, acordos) if item else None
        custo = float(x.custo_unitario) if x and x.custo_unitario else (custo_do_item(item, acordo) if item else 0.0)
        pct = int(x.percentual) if x and x.percentual is not None else 100
        ativo = bool(x.ativo) if x else True
        unidades_bu = {b: (math.ceil(q * pct / 100) if ativo else 0) for b, q in por_bu_m.items()}
        unidades = sum(unidades_bu.values())
        valor = round(unidades * custo, 2)
        for b, q in por_bu_m.items():
            por_bu[b]["aparelhos"] += q
            por_bu[b]["unidades"] += unidades_bu[b]
            por_bu[b]["valor"] += unidades_bu[b] * custo
        mes = (x.mes_alvo if x else "") or ""
        if ativo and unidades:
            por_mes[mes or "sem_mes"]["unidades"] += unidades
            por_mes[mes or "sem_mes"]["valor"] += valor
        linhas.append({
            "modelo": m, "aparelhos": total, "por_bu": por_bu_m, "unidades_por_bu": unidades_bu,
            "plano": _sub_dict(x) if x else {"id": None, "modelo_obsoleto": m, "item_id": None, "custo_unitario": 0.0,
                                             "mes_alvo": "", "percentual": 100, "observacao": "", "ativo": True},
            "item": _item_dict(item) if item else None,
            "acordo": _acordo_resumo(acordo, cfg["acordo_alerta_dias"]),
            "custo": round(custo, 2), "unidades": unidades, "valor": valor,
            "sem_custo": ativo and unidades > 0 and custo <= 0,
        })
    linhas.sort(key=lambda l: (-l["valor"], -l["aparelhos"], l["modelo"]))
    return {
        "ultima_coleta": ultima, "bus": bus, "linhas": linhas,
        "por_bu": [{"bu": b["bu"], "nome": b["nome"], "pais": b["pais"], **{k: (round(v, 2) if k == "valor" else v) for k, v in por_bu[b["bu"]].items()}} for b in bus],
        "por_mes": [{"mes": k, **{kk: (round(vv, 2) if kk == "valor" else vv) for kk, vv in v.items()}} for k, v in sorted(por_mes.items(), key=lambda kv: (kv[0] == "sem_mes", kv[0]))],
        "resumo": {"aparelhos": sum(l["aparelhos"] for l in linhas), "unidades": sum(l["unidades"] for l in linhas),
                   "valor": round(sum(l["valor"] for l in linhas), 2), "modelos": len(linhas),
                   "sem_custo": sum(1 for l in linhas if l["sem_custo"]),
                   "sem_mes": sum(1 for l in linhas if l["unidades"] and not l["plano"]["mes_alvo"])},
    }
