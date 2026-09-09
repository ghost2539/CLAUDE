"""Orçamento de Manutenção — reparo de coletores e SLEDs do SPARE.

Substitui a planilha `Manutenção.xlsx`. O contrato completo (modelo, regras
de normalização, regra dos 60 %, agregados do painel e endpoints) está em
`docs/ORCAMENTO_MANUTENCAO.md` — este arquivo só o implementa.

Banco PRÓPRIO (`db/orcamento_manutencao.py`). Exige sessão do portal e o
módulo `orcamento_manutencao` liberado: `view` lê, `create` inclui, `edit`
altera, `export` exporta, `admin` importa planilha, exclui e configura.
"""
from __future__ import annotations

import io
import json
import logging
import numbers
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

import db.orcamento_manutencao as db
from core.security import check_rate_limit, require_permission

_log = logging.getLogger("orcamento_manutencao")

router = APIRouter(prefix="/api/orcamento-manutencao", tags=["Orçamento Manutenção"],
                   include_in_schema=False)

MODULO = "orcamento_manutencao"
R = db.Reparo

# Categorias com rótulo canônico (3.1); qualquer outra vai por família.
CATEGORIAS_CANONICAS = {
    "coletor": ("Coletor", "COLETOR"),
    "coletor hf550x": ("Coletor HF550X", "COLETOR"),
    "coletor s70": ("Coletor S70", "COLETOR"),
    "sled rfid": ("Sled RFID", "SLED"),
    "sled rfr901": ("Sled RFR901", "SLED"),
}


def _exigir(req: Request, acao: str) -> dict:
    db.ensure_db()
    return require_permission(req, MODULO, acao)


def _usuario(sd: dict) -> str:
    return str(sd.get("login") or sd.get("username") or "")[:80]


# ── Utilitários de texto/número ─────────────────────────────────────────
def _vazio(v) -> bool:
    if v is None:
        return True
    try:
        if v != v:  # NaN / NaT
            return True
    except Exception:  # noqa: BLE001
        pass
    return isinstance(v, str) and not v.strip()


def _numero_puro(v) -> bool:
    return isinstance(v, numbers.Number) and not isinstance(v, bool)


def _texto(v, tam: Optional[int] = None) -> str:
    if _vazio(v):
        return ""
    if _numero_puro(v) and float(v).is_integer():
        v = int(v)
    t = " ".join(str(v).replace("\xa0", " ").split())
    return t[:tam] if tam else t


def _sem_acento(v) -> str:
    """Minúsculas, sem acento, espaços únicos — base de toda comparação."""
    t = unicodedata.normalize("NFKD", _texto(v))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _inteiro(v) -> Optional[int]:
    if _vazio(v):
        return None
    if _numero_puro(v):
        return int(v)
    try:
        return int(float(_texto(v).replace(",", ".")))
    except ValueError:
        return None


def _numero(v) -> Optional[float]:
    """Número em formato livre ("1.234,56", "10,4%"); None quando não é número."""
    if _vazio(v):
        return None
    if _numero_puro(v):
        return float(v)
    t = _texto(v).replace("r$", "").replace("R$", "").replace(" ", "")
    pct = t.endswith("%")
    t = t.rstrip("%")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        n = float(t)
    except ValueError:
        return None
    return n / 100 if pct else n


def _texto_rma(v) -> str:
    """RMA sem nenhum espaço (a planilha traz `\\xa0`)."""
    if _vazio(v):
        return ""
    if _numero_puro(v) and float(v).is_integer():
        return str(int(v))
    return "".join(str(v).replace("\xa0", " ").split())[:40]


# ── Normalização (seção 3) — funções puras ──────────────────────────────
def normalizar_categoria(texto) -> tuple[str, str]:
    original = _texto(texto, 40)
    chave = _sem_acento(original)
    if chave in CATEGORIAS_CANONICAS:
        return CATEGORIAS_CANONICAS[chave]
    if "coletor" in chave:
        return original.title(), "COLETOR"
    if "sled" in chave:
        return original.title(), "SLED"
    return original, "OUTRO"


def normalizar_status(texto) -> tuple[str, bool]:
    """Devolve (status canônico, reconhecido). Aceita o canônico e o texto humano."""
    chave = _sem_acento(texto).replace("_", " ")
    if chave.startswith("aprovado"):
        return "APROVADO", True
    if chave.startswith("reprovado"):
        return "REPROVADO", True
    if chave.startswith("aguardando aprovacao"):
        return "AGUARDANDO_APROVACAO", True
    if chave.startswith("aguardando orcamento"):
        return "AGUARDANDO_ORCAMENTO", True
    if chave.startswith("validando"):
        return "VALIDANDO_ORCAMENTO", True
    return "AGUARDANDO_ORCAMENTO", False


def normalizar_tipo(texto) -> tuple[str, bool]:
    chave = _sem_acento(texto)
    if "contrato" in chave:
        return "CONTRATO", True
    if "avulsa" in chave or "po extra" in chave:
        return "AVULSA", True
    return "CONTRATO", False


def normalizar_status_retorno(texto) -> str:
    return "DEVOLVIDO" if "devolvid" in _sem_acento(texto) else "EM_MANUTENCAO"


def normalizar_empresa(texto) -> str:
    chave = _sem_acento(texto).upper()
    for nome in db.EMPRESAS:
        if nome in chave:
            return nome
    return ""


def interpretar_orcamento(valor) -> tuple[float, bool]:
    """(orcamento, garantia). "Garantia" → (0, True); "R$ -" → (0, False);
    outro texto não numérico levanta ValueError com o motivo."""
    if _vazio(valor):
        return 0.0, False
    if _numero_puro(valor):
        return round(float(valor), 2), False
    chave = _sem_acento(valor)
    if "garantia" in chave:
        return 0.0, True
    compacto = chave.replace(" ", "")
    if compacto.replace("r$", "").strip("-.,") == "":
        return 0.0, False
    n = _numero(valor)
    if n is None:
        raise ValueError(f"orçamento não numérico: {_texto(valor)!r}")
    return round(n, 2), False


_RE_MES = re.compile(r"^(\d{4})-(\d{2})")


def mes_referencia(valor) -> Optional[str]:
    """datetime/date/Timestamp → "AAAA-MM"; texto "AAAA-MM" (ou ISO) mantém; outro → None."""
    if _vazio(valor):
        return None
    if hasattr(valor, "year") and hasattr(valor, "month"):
        try:
            return f"{int(valor.year):04d}-{int(valor.month):02d}"
        except (TypeError, ValueError):
            return None
    m = _RE_MES.match(_texto(valor))
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{m.group(2)}"
    return None


def calcular(r, limiar: float) -> bool:
    """Recalcula `percentual`/`avaliacao` e aplica a reprovação automática (3.5).
    Serve para ORM e para o SimpleNamespace da importação. Devolve se algo mudou."""
    antes = (r.percentual, r.avaliacao, r.status, r.status_original)
    orc = float(r.orcamento or 0)
    vc = float(r.valor_compra) if r.valor_compra is not None else 0.0
    perc = None
    if vc > 0:
        perc = orc / vc
        r.percentual = round(perc, 4)
        r.avaliacao = "DENTRO" if perc <= limiar else "FORA"
    else:
        r.percentual = None
        r.avaliacao = ""
    if r.avaliacao == "FORA" and r.status in db.STATUS_PENDENTES:
        r.status = "REPROVADO"
        pct = f"{perc * 100:.1f}".replace(".", ",")
        lim = f"{limiar * 100:g}".replace(".", ",")
        r.status_original = f"Reprovado automaticamente ({pct} % > {lim} %)"[:60]
    return antes != (r.percentual, r.avaliacao, r.status, r.status_original)


def _padrao_categoria(config: dict, categoria: str) -> Optional[float]:
    chave = _sem_acento(categoria)
    for nome, valor in (config.get("valor_compra_padrao") or {}).items():
        if _sem_acento(nome) == chave:
            v = _numero(valor)
            if v and v > 0:
                return round(v, 2)
    return None


def _valor_compra_para(r, config: dict, ebs_custo: Optional[float] = None,
                       razao: Optional[float] = None,
                       sobrescrever_manual: bool = False) -> tuple[Optional[float], str]:
    """Ordem 3.4: EBS → planilha → padrão. Uma fonte mais fraca nunca rebaixa
    um valor já gravado por fonte mais forte; MANUAL só cede se pedido."""
    atual = float(r.valor_compra) if r.valor_compra is not None else None
    fonte = r.valor_compra_fonte or ""
    if atual and fonte == "MANUAL" and not sobrescrever_manual:
        return atual, "MANUAL"
    if ebs_custo is not None and ebs_custo > 0:
        return round(float(ebs_custo), 2), "EBS"
    if atual and fonte == "EBS":
        return atual, "EBS"
    orc = float(r.orcamento or 0)
    if razao is not None and orc > 0 and razao > 0:
        return round(orc / razao, 2), "PLANILHA"
    if atual and fonte == "PLANILHA":
        return atual, "PLANILHA"
    padrao = _padrao_categoria(config, r.categoria)
    if padrao:
        return padrao, "PADRAO"
    if atual and fonte == "MANUAL":
        return atual, "MANUAL"
    return None, ""


# ── EBS ─────────────────────────────────────────────────────────────────
def _ebs_buscar(serie: str) -> dict:
    """Chamada crua ao EBS (isolada para poder ser substituída em testes)."""
    from integracoes.ebs_service import search_one
    from routers.public_assets import _auth
    try:
        res = search_one(_auth(), serie)
    except PermissionError:
        res = {"encontrado": False, "erro": "Sessão EBS expirada"}
    if "expirad" in _sem_acento(res.get("erro") if isinstance(res, dict) else ""):
        res = search_one(_auth(True), serie)
    return res if isinstance(res, dict) else {"encontrado": False, "erro": "resposta inválida do EBS"}


def _consultar_ebs(serie: str) -> dict:
    """Nunca levanta: {"encontrado", "custo", "empresa", "erro"}."""
    saida = {"encontrado": False, "custo": None, "empresa": "", "erro": ""}
    try:
        res = _ebs_buscar(serie)
        if not res.get("encontrado"):
            saida["erro"] = _texto(res.get("erro") or "Ativo não encontrado no EBS", 200)
            return saida
        empresa = res.get("empresa") or ""
        try:
            from routers.public_assets import _company
            empresa = _company(empresa)
        except Exception:  # noqa: BLE001
            pass
        saida.update(encontrado=True, custo=_numero(res.get("custo_asset")),
                     empresa=normalizar_empresa(empresa))
        if not saida["custo"]:
            saida["erro"] = "EBS sem custo do ativo"
    except Exception as exc:  # noqa: BLE001 — falha do EBS nunca derruba a inclusão
        _log.warning("EBS falhou para série %s: %s", serie, exc)
        saida["erro"] = _texto(f"Falha na consulta ao EBS: {exc}", 200)
    return saida


def _aplicar_ebs(r, res: dict, config: dict, sobrescrever_manual: bool) -> bool:
    """Grava o resultado do EBS na linha. Devolve se valor/empresa mudaram."""
    r.ebs_consultado_em = db.utcnow()
    r.ebs_erro = (res.get("erro") or "")[:200]
    mudou = False
    if res.get("encontrado"):
        if res.get("empresa") and r.empresa != res["empresa"]:
            r.empresa = res["empresa"]
            mudou = True
        valor, fonte = _valor_compra_para(r, config, ebs_custo=res.get("custo"),
                                          sobrescrever_manual=sobrescrever_manual)
        if (valor, fonte) != (r.valor_compra, r.valor_compra_fonte):
            r.valor_compra, r.valor_compra_fonte = valor, fonte
            mudou = True
    calcular(r, config["limiar_percentual"])
    return mudou


# ── Entrada ─────────────────────────────────────────────────────────────
class ReparoIn(BaseModel):
    """Corpo do POST. Números aceitam texto ("389,29", "Garantia") — a tela
    pode mandar campos a mais, que são ignorados."""
    model_config = ConfigDict(extra="ignore")
    rma: str
    serie: str
    categoria: str
    loja: Any = None
    empresa: str = ""
    orcamento: Any = None
    garantia: Optional[bool] = None
    valor_compra: Any = None
    status: str = ""
    tipo_manutencao: str = ""
    status_retorno: str = ""
    ano: Any = None
    mes_referencia: Any = None
    ano_devolucao: Any = None
    lote_prime: str = ""
    qtde: Any = None
    observacao: str = ""


class ReparoPatch(BaseModel):
    """Corpo do PUT: só o que vier é alterado."""
    model_config = ConfigDict(extra="ignore")
    rma: Optional[str] = None
    serie: Optional[str] = None
    categoria: Optional[str] = None
    loja: Any = None
    empresa: Optional[str] = None
    orcamento: Any = None
    garantia: Optional[bool] = None
    valor_compra: Any = None
    status: Optional[str] = None
    tipo_manutencao: Optional[str] = None
    status_retorno: Optional[str] = None
    ano: Any = None
    mes_referencia: Any = None
    ano_devolucao: Any = None
    lote_prime: Optional[str] = None
    qtde: Any = None
    observacao: Optional[str] = None


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cota_mensal: Optional[dict[str, Any]] = None
    limiar_percentual: Any = None
    valor_compra_padrao: Optional[dict[str, Any]] = None


def _status_entrada(texto: str) -> tuple[str, str]:
    """(canônico, status_original) para o corpo; texto não reconhecido → 422."""
    status, ok = normalizar_status(texto)
    if texto and not ok:
        raise HTTPException(422, f"Status não reconhecido: {texto!r}")
    return status, (_texto(texto, 60) or db.STATUS_ROTULOS[status])


def _valor_manual(v) -> Optional[float]:
    if _vazio(v):
        return None
    n = _numero(v)
    if n is None or n < 0:
        raise HTTPException(422, f"Valor de compra inválido: {_texto(v)!r}")
    return round(n, 2)


def _mes_entrada(v) -> Optional[str]:
    if _vazio(v):
        return None
    mes = mes_referencia(v)
    if not mes:
        raise HTTPException(422, f"Mês de referência inválido (use AAAA-MM): {_texto(v)!r}")
    return mes


# ── Filtros e serialização ──────────────────────────────────────────────
def _filtrar(stmt, ano=None, mes=None, familia=None, categoria=None, status=None,
             status_retorno=None, empresa=None, tipo_manutencao=None, q=None):
    if ano:
        stmt = stmt.where(R.ano == ano)
    if mes:
        stmt = stmt.where(R.mes_referencia == mes)
    if familia:
        stmt = stmt.where(R.familia == familia.strip().upper())
    if categoria:
        stmt = stmt.where(R.categoria == normalizar_categoria(categoria)[0])
    if status:
        stmt = stmt.where(R.status == normalizar_status(status)[0])
    if status_retorno:
        stmt = stmt.where(R.status_retorno == normalizar_status_retorno(status_retorno))
    if empresa:
        stmt = stmt.where(R.empresa == normalizar_empresa(empresa))
    if tipo_manutencao:
        stmt = stmt.where(R.tipo_manutencao == normalizar_tipo(tipo_manutencao)[0])
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(R.rma.like(like), R.serie.like(like.upper()),
                              R.lote_prime.like(like)))
    return stmt


def _ordenado(stmt):
    return stmt.order_by(R.mes_referencia.desc().nullslast(), R.id.desc())


def _mes_do_filtro(mes: Optional[str], ano: int) -> Optional[str]:
    """Aceita "AAAA-MM", "MM" ou "3"; devolve "AAAA-MM" ou None (ano inteiro)."""
    texto = str(mes or "").strip()
    if not texto or texto.lower() in ("todos", "ano"):
        return None
    if len(texto) >= 7 and texto[4] == "-" and texto[:4].isdigit() and texto[5:7].isdigit():
        numero = int(texto[5:7])
        return f"{int(texto[:4])}-{numero:02d}" if 1 <= numero <= 12 else None
    if texto.isdigit() and 1 <= int(texto) <= 12:
        return f"{ano}-{int(texto):02d}"
    return None


def _dinheiro(v) -> float:
    return round(float(v or 0), 2)


# ── 5.1 Painel ──────────────────────────────────────────────────────────
@router.get("/resumo")
def resumo(req: Request, ano: Optional[int] = None, mes: Optional[str] = None):
    """Painel. `mes` ("AAAA-MM" ou "01".."12") estreita cartões e quebras ao mês;
    as séries mensais continuam mostrando o ano inteiro, para dar contexto."""
    _exigir(req, "view")
    with db.SessionLocal() as s:
        config = db.ler_config(s)
        # Anos: da coluna `ano` e também dos meses de referência (na planilha
        # o mês de contrato pode cair no ano seguinte ao do registro).
        anos = {int(a) for (a,) in s.execute(select(R.ano).distinct()).all() if a}
        anos |= {int(m) for (m,) in s.execute(
            select(func.substr(R.mes_referencia, 1, 4)).distinct()
            .where(R.mes_referencia.isnot(None))).all() if m and str(m).isdigit()}
        if not anos:
            anos = {date.today().year}
        ano = ano or max(anos)

        mes_sel = _mes_do_filtro(mes, ano)
        escopo = (R.mes_referencia == mes_sel) if mes_sel else R.mes_referencia.like(f"{ano}-%")

        # ── Séries mensais (sempre o ano inteiro) ───────────────────────
        vazio = lambda: {"COLETOR": 0.0, "SLED": 0.0, "TOTAL": 0.0}  # noqa: E731
        vazio_tipo = lambda: {"CONTRATO": 0.0, "AVULSA": 0.0, "TOTAL": 0.0}  # noqa: E731
        meses = {}
        for m in range(1, 13):
            meses[f"{ano}-{m:02d}"] = {
                "consumo": vazio(), "reparados": vazio(),
                "reprovados_valor": vazio(), "reprovados_qtde": vazio(),
                "consumo_tipo": vazio_tipo(), "reparados_tipo": vazio_tipo(),
                "consumo_contrato": vazio(), "consumo_avulso": vazio(),
            }
        rows = s.execute(
            select(R.mes_referencia, R.familia, R.tipo_manutencao, R.status, func.count(R.id),
                   func.coalesce(func.sum(R.orcamento), 0),
                   func.coalesce(func.sum(R.valor_compra), 0))
            .where(R.mes_referencia.like(f"{ano}-%"), R.status.in_(("APROVADO", "REPROVADO")))
            .group_by(R.mes_referencia, R.familia, R.tipo_manutencao, R.status)
        ).all()
        for mes_ref, familia, tipo, status, qtde, soma_orc, soma_vc in rows:
            bloco = meses.get(mes_ref)
            if bloco is None:
                continue
            qtde = int(qtde)
            if status == "APROVADO":
                alvo_v, alvo_q, valor = bloco["consumo"], bloco["reparados"], float(soma_orc)
                # Aprovados separam contrato de avulso: a cota mensal é do contrato.
                if tipo in bloco["consumo_tipo"]:
                    bloco["consumo_tipo"][tipo] += valor
                    bloco["reparados_tipo"][tipo] += qtde
                # A mesma quebra por família das demais visões, por tipo.
                por_fam = bloco["consumo_avulso" if tipo == "AVULSA" else "consumo_contrato"]
                if familia in por_fam:
                    por_fam[familia] += valor
                por_fam["TOTAL"] += valor
                bloco["consumo_tipo"]["TOTAL"] += valor
                bloco["reparados_tipo"]["TOTAL"] += qtde
            else:
                alvo_v, alvo_q, valor = bloco["reprovados_valor"], bloco["reprovados_qtde"], float(soma_vc)
            if familia in alvo_v:
                alvo_v[familia] += valor
                alvo_q[familia] += qtde
            alvo_v["TOTAL"] += valor
            alvo_q["TOTAL"] += qtde

        lista_meses = []
        for mes_ref, bloco in meses.items():
            for chave in ("consumo", "reprovados_valor", "consumo_tipo",
                          "consumo_contrato", "consumo_avulso"):
                bloco[chave] = {k: _dinheiro(v) for k, v in bloco[chave].items()}
            for chave in ("reparados", "reprovados_qtde", "reparados_tipo"):
                bloco[chave] = {k: int(v) for k, v in bloco[chave].items()}
            lista_meses.append({"mes": mes_ref, **bloco})

        com_consumo = [b for b in lista_meses if b["consumo"]["TOTAL"] > 0]

        # ── Quebras do escopo (mês selecionado ou ano inteiro) ──────────
        categorias: dict[tuple, dict] = {}
        gerais = {f: {"consumo": 0.0, "reparados": 0} for f in db.FAMILIAS}
        por_tipo = {t: {"consumo": 0.0, "reparados": 0} for t in db.TIPOS_ROTULOS}
        totais = {"aprovados": 0, "aprovados_contrato": 0, "aprovados_avulsa": 0,
                  "reprovados": 0, "pendentes": 0, "garantia": 0,
                  "consumo": 0.0, "consumo_contrato": 0.0, "consumo_avulsa": 0.0,
                  "reprovados_valor": 0.0}
        for cat, fam, tipo, status, garantia, q, soma_orc, soma_vc in s.execute(
            select(R.categoria, R.familia, R.tipo_manutencao, R.status, R.garantia,
                   func.count(R.id), func.coalesce(func.sum(R.orcamento), 0),
                   func.coalesce(func.sum(R.valor_compra), 0))
            .where(escopo)
            .group_by(R.categoria, R.familia, R.tipo_manutencao, R.status, R.garantia)
        ).all():
            c = categorias.setdefault((cat, fam), {
                "categoria": cat, "familia": fam, "consumo": 0.0,
                "consumo_contrato": 0.0, "consumo_avulsa": 0.0, "reparados": 0,
                "reparados_contrato": 0, "reparados_avulsa": 0,
                "reprovados_qtde": 0, "reprovados_valor": 0.0,
            })
            q, valor_orc, valor_vc = int(q), float(soma_orc), float(soma_vc)
            sufixo = "contrato" if tipo == "CONTRATO" else "avulsa"
            if status == "APROVADO":
                c["consumo"] += valor_orc
                c["reparados"] += q
                c[f"consumo_{sufixo}"] += valor_orc
                c[f"reparados_{sufixo}"] += q
                gerais.setdefault(fam, {"consumo": 0.0, "reparados": 0})
                gerais[fam]["consumo"] += valor_orc
                gerais[fam]["reparados"] += q
                por_tipo.setdefault(tipo, {"consumo": 0.0, "reparados": 0})
                por_tipo[tipo]["consumo"] += valor_orc
                por_tipo[tipo]["reparados"] += q
                totais["aprovados"] += q
                totais[f"aprovados_{sufixo}"] += q
                totais["consumo"] += valor_orc
                totais[f"consumo_{sufixo}"] += valor_orc
                if garantia:
                    totais["garantia"] += q
            elif status == "REPROVADO":
                c["reprovados_valor"] += valor_vc
                c["reprovados_qtde"] += q
                totais["reprovados"] += q
                totais["reprovados_valor"] += valor_vc
            else:
                totais["pendentes"] += q

        lista_categorias = sorted(categorias.values(), key=lambda x: -x["consumo"])
        for c in lista_categorias:
            for chave in ("consumo", "consumo_contrato", "consumo_avulsa", "reprovados_valor"):
                c[chave] = _dinheiro(c[chave])
        for bloco in (gerais, por_tipo):
            for dados in bloco.values():
                dados["consumo"] = _dinheiro(dados["consumo"])
                dados["media"] = _dinheiro(dados["consumo"] / dados["reparados"]) if dados["reparados"] else 0.0
        for chave in ("consumo", "consumo_contrato", "consumo_avulsa", "reprovados_valor"):
            totais[chave] = _dinheiro(totais[chave])

        # ── Cartões financeiros ────────────────────────────────────────
        cota = _dinheiro((config.get("cota_mensal") or {}).get(str(ano)))
        if mes_sel:
            foco = next((b for b in lista_meses if b["mes"] == mes_sel), None)
        else:
            foco = com_consumo[-1] if com_consumo else None
        cota_em_uso = foco["mes"] if foco else mes_sel
        consumo_atual = foco["consumo"]["TOTAL"] if foco else 0.0
        consumo_contrato = foco["consumo_tipo"]["CONTRATO"] if foco else 0.0
        consumo_avulsa = foco["consumo_tipo"]["AVULSA"] if foco else 0.0

        totais["meses_com_consumo"] = len(com_consumo)
        total_ano = _dinheiro(sum(b["consumo"]["TOTAL"] for b in lista_meses))
        totais["total_ano"] = total_ano
        totais["media_mensal"] = _dinheiro(total_ano / len(com_consumo)) if com_consumo else 0.0
        totais["cota_anual"] = _dinheiro(cota * 12)
        # A cota mensal acompanha o contrato: o avulso é medido à parte.
        totais["percentual_cota_mes"] = round(consumo_contrato / cota, 4) if cota else None

        # Cards: retrato de agora, sem filtro de ano.
        ag_aprovacao = [
            {"categoria": cat, "familia": fam, "qtde": int(q), "valor": _dinheiro(v)}
            for cat, fam, q, v in s.execute(
                select(R.categoria, R.familia, func.count(R.id),
                       func.coalesce(func.sum(R.orcamento), 0))
                .where(R.status == "AGUARDANDO_APROVACAO")
                .group_by(R.categoria, R.familia).order_by(R.categoria)
            ).all()
        ]
        devolucao: dict[tuple, dict] = {}
        for cat, fam, status, q in s.execute(
            select(R.categoria, R.familia, R.status, func.count(R.id))
            .where(R.status_retorno == "EM_MANUTENCAO")
            .group_by(R.categoria, R.familia, R.status).order_by(R.categoria)
        ).all():
            d = devolucao.setdefault((cat, fam), {
                "categoria": cat, "familia": fam, "total": 0,
                "ag_manutencao": 0, "ag_orcamento": 0, "ag_aprovacao": 0, "reprovado": 0,
            })
            d["total"] += int(q)
            chave = {"APROVADO": "ag_manutencao", "AGUARDANDO_ORCAMENTO": "ag_orcamento",
                     "VALIDANDO_ORCAMENTO": "ag_orcamento",
                     "AGUARDANDO_APROVACAO": "ag_aprovacao", "REPROVADO": "reprovado"}.get(status)
            if chave:
                d[chave] += int(q)

    return {
        "ano": ano,
        "mes": mes_sel,
        "anos_disponiveis": sorted(anos),
        "meses_disponiveis": [
            {"mes": b["mes"], "tem_dado": b["consumo"]["TOTAL"] > 0 or b["reprovados_qtde"]["TOTAL"] > 0}
            for b in lista_meses
        ],
        "cota_mensal": cota,
        "cota_em_uso": cota_em_uso,
        "consumo_atual": consumo_atual,
        "consumo_atual_contrato": consumo_contrato,
        "consumo_atual_avulsa": consumo_avulsa,
        "residual": _dinheiro(cota - consumo_contrato),
        "total_investido": totais["consumo"] if mes_sel else total_ano,
        "limiar_percentual": config["limiar_percentual"],
        "meses": lista_meses,
        "gerais": gerais,
        "por_tipo": por_tipo,
        "aguardando_aprovacao": ag_aprovacao,
        "aguardando_devolucao": list(devolucao.values()),
        "categorias": lista_categorias,
        "totais": totais,
    }


# ── 5.2 Lista ───────────────────────────────────────────────────────────
@router.get("/reparos")
def listar(req: Request, ano: Optional[int] = None, mes: Optional[str] = None,
           familia: Optional[str] = None, categoria: Optional[str] = None,
           status: Optional[str] = None, status_retorno: Optional[str] = None,
           empresa: Optional[str] = None, tipo_manutencao: Optional[str] = None,
           q: Optional[str] = None, limit: int = 100, offset: int = 0):
    _exigir(req, "view")
    limit = max(1, min(int(limit or 100), 1000))
    offset = max(0, int(offset or 0))
    filtros = dict(ano=ano, mes=mes, familia=familia, categoria=categoria, status=status,
                   status_retorno=status_retorno, empresa=empresa,
                   tipo_manutencao=tipo_manutencao, q=q)
    with db.SessionLocal() as s:
        total = s.scalar(_filtrar(select(func.count(R.id)), **filtros)) or 0
        itens = s.scalars(_ordenado(_filtrar(select(R), **filtros))
                          .limit(limit).offset(offset)).all()
        return {"total": int(total), "itens": [r.to_dict() for r in itens]}


# ── 5.3 Inclusão ────────────────────────────────────────────────────────
def _conflito(reparo_id: int) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "RMA já cadastrado", "id": reparo_id})


@router.post("/reparos", status_code=201)
def criar(body: ReparoIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    usuario = _usuario(sd)

    rma = _texto_rma(body.rma)
    serie = _texto(body.serie, 60).upper()
    categoria, familia = normalizar_categoria(body.categoria)
    if not rma or not serie or not categoria:
        raise HTTPException(422, "RMA, série e categoria são obrigatórios.")
    try:
        orcamento, garantia = interpretar_orcamento(body.orcamento)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    status, status_original = _status_entrada(body.status or "")
    mes = _mes_entrada(body.mes_referencia)

    with db.SessionLocal() as s:
        existente = s.scalar(select(R.id).where(R.rma == rma))
        if existente:
            return _conflito(existente)
        config = db.ler_config(s)

    r = R(
        rma=rma, serie=serie, categoria=categoria, familia=familia,
        loja=_inteiro(body.loja), empresa=normalizar_empresa(body.empresa),
        orcamento=orcamento, garantia=bool(garantia or body.garantia),
        status=status, status_original=status_original,
        tipo_manutencao=normalizar_tipo(body.tipo_manutencao)[0],
        tipo_original=_texto(body.tipo_manutencao, 60),
        status_retorno=normalizar_status_retorno(body.status_retorno),
        mes_referencia=mes,
        ano=_inteiro(body.ano) or (int(mes[:4]) if mes else date.today().year),
        ano_devolucao=_inteiro(body.ano_devolucao),
        lote_prime=_texto(body.lote_prime, 200), qtde=_inteiro(body.qtde) or 1,
        observacao=(body.observacao or "").strip(),
        criado_por=usuario, atualizado_por=usuario, origem="PORTAL",
    )

    ebs = {"consultado": False, "erro": ""}
    manual = _valor_manual(body.valor_compra)
    if manual is not None:
        r.valor_compra, r.valor_compra_fonte = manual, "MANUAL"
    else:
        # EBS fora de transação: chamada de rede não segura o banco.
        res = _consultar_ebs(serie)
        ebs = {"consultado": True, "erro": res["erro"]}
        r.ebs_consultado_em = db.utcnow()
        r.ebs_erro = res["erro"][:200]
        if res["empresa"]:
            r.empresa = res["empresa"]
        r.valor_compra, r.valor_compra_fonte = _valor_compra_para(r, config, ebs_custo=res["custo"])
    calcular(r, config["limiar_percentual"])

    try:
        with db.SessionLocal.begin() as s:
            s.add(r)
            s.flush()
            saida = r.to_dict()
    except IntegrityError:
        with db.SessionLocal() as s:
            return _conflito(s.scalar(select(R.id).where(R.rma == rma)) or 0)
    return {"reparo": saida, "ebs": ebs}


# ── 5.4 Edição ──────────────────────────────────────────────────────────
@router.put("/reparos/{reparo_id}")
def atualizar(reparo_id: int, body: ReparoPatch, req: Request):
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    dados = body.model_dump(exclude_unset=True)
    with db.SessionLocal.begin() as s:
        r = s.get(R, reparo_id)
        if not r:
            raise HTTPException(404, "Reparo não encontrado.")
        config = db.ler_config(s)

        if "rma" in dados:
            novo = _texto_rma(dados["rma"])
            if not novo:
                raise HTTPException(422, "RMA não pode ficar vazio.")
            if novo != r.rma and s.scalar(select(R.id).where(R.rma == novo)):
                raise HTTPException(409, "RMA já cadastrado")
            r.rma = novo
        if "serie" in dados:
            r.serie = _texto(dados["serie"], 60).upper()
            if not r.serie:
                raise HTTPException(422, "Série não pode ficar vazia.")
        if "categoria" in dados:
            r.categoria, r.familia = normalizar_categoria(dados["categoria"])
            if not r.categoria:
                raise HTTPException(422, "Categoria não pode ficar vazia.")
        if "loja" in dados:
            r.loja = _inteiro(dados["loja"])
        if "empresa" in dados:
            r.empresa = normalizar_empresa(dados["empresa"])
        if "orcamento" in dados:
            try:
                r.orcamento, garantia = interpretar_orcamento(dados["orcamento"])
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            if garantia:
                r.garantia = True
        if "garantia" in dados and dados["garantia"] is not None:
            r.garantia = bool(dados["garantia"])
        if "status" in dados and dados["status"] is not None:
            r.status, r.status_original = _status_entrada(dados["status"])
        if "tipo_manutencao" in dados and dados["tipo_manutencao"] is not None:
            r.tipo_manutencao = normalizar_tipo(dados["tipo_manutencao"])[0]
            r.tipo_original = _texto(dados["tipo_manutencao"], 60)
        if "status_retorno" in dados and dados["status_retorno"] is not None:
            r.status_retorno = normalizar_status_retorno(dados["status_retorno"])
        if "mes_referencia" in dados:
            r.mes_referencia = _mes_entrada(dados["mes_referencia"])
        if "ano" in dados and _inteiro(dados["ano"]):
            r.ano = _inteiro(dados["ano"])
        if "ano_devolucao" in dados:
            r.ano_devolucao = _inteiro(dados["ano_devolucao"])
        if "lote_prime" in dados and dados["lote_prime"] is not None:
            r.lote_prime = _texto(dados["lote_prime"], 200)
        if "qtde" in dados and _inteiro(dados["qtde"]):
            r.qtde = _inteiro(dados["qtde"])
        if "observacao" in dados and dados["observacao"] is not None:
            r.observacao = str(dados["observacao"]).strip()
        if "valor_compra" in dados:
            manual = _valor_manual(dados["valor_compra"])
            # Valor digitado vira MANUAL; nulo limpa e deixa a ordem 3.4 decidir.
            r.valor_compra, r.valor_compra_fonte = (manual, "MANUAL") if manual is not None else (None, "")

        r.valor_compra, r.valor_compra_fonte = _valor_compra_para(r, config)
        calcular(r, config["limiar_percentual"])
        r.atualizado_por = _usuario(sd)
        s.flush()
        return r.to_dict()


# ── 5.5 Exclusão ────────────────────────────────────────────────────────
@router.delete("/reparos/{reparo_id}")
def excluir(reparo_id: int, req: Request):
    _exigir(req, "admin")
    check_rate_limit(req, "api")
    with db.SessionLocal.begin() as s:
        r = s.get(R, reparo_id)
        if not r:
            raise HTTPException(404, "Reparo não encontrado.")
        s.delete(r)
    return {"ok": True}


# ── 5.6 EBS ─────────────────────────────────────────────────────────────
@router.post("/reparos/ebs-pendentes")
def ebs_pendentes(req: Request, limite: int = 200):
    """Completa valor de compra/empresa pelo EBS nas linhas sem um dos dois,
    as mais recentes primeiro. Não sobrescreve valor MANUAL."""
    sd = _exigir(req, "admin")
    check_rate_limit(req, "api")
    limite = max(1, min(int(limite or 200), 1000))
    with db.SessionLocal() as s:
        alvos = s.execute(
            select(R.id, R.serie)
            .where(or_(R.valor_compra.is_(None), R.empresa == ""))
            .order_by(R.id.desc()).limit(limite)
        ).all()
    # Rede primeiro, banco depois: nada de transação aberta durante as chamadas.
    resultados = [(rid, _consultar_ebs(serie)) for rid, serie in alvos]
    return _gravar_ebs(resultados, _usuario(sd), sobrescrever_manual=False)


@router.post("/reparos/{reparo_id}/ebs")
def ebs_um(reparo_id: int, req: Request):
    """Reconsulta explícita de uma linha — aqui o valor MANUAL cede (3.4)."""
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    with db.SessionLocal() as s:
        r = s.get(R, reparo_id)
        if not r:
            raise HTTPException(404, "Reparo não encontrado.")
        serie = r.serie
    saida = _gravar_ebs([(reparo_id, _consultar_ebs(serie))], _usuario(sd), sobrescrever_manual=True)
    with db.SessionLocal() as s:
        saida["reparo"] = s.get(R, reparo_id).to_dict()
    return saida


def _gravar_ebs(resultados: list, usuario: str, sobrescrever_manual: bool) -> dict:
    atualizados, falhas = 0, []
    with db.SessionLocal.begin() as s:
        config = db.ler_config(s)
        for rid, res in resultados:
            r = s.get(R, rid)
            if not r:
                continue
            if _aplicar_ebs(r, res, config, sobrescrever_manual):
                atualizados += 1
                r.atualizado_por = usuario
            if res.get("erro"):
                falhas.append({"id": rid, "erro": res["erro"]})
    return {"consultados": len(resultados), "atualizados": atualizados, "falhas": falhas}


# ── 5.7 Importação ──────────────────────────────────────────────────────
ALIASES = {
    "serie": ["serie", "numero de serie", "n serie", "serial"],
    "loja": ["loja"],
    "rma": ["rma"],
    "categoria": ["categoria"],
    "orcamento": ["orcamento", "valor orcamento", "valor"],
    "razao": ["60% orcamento", "percentual", "60%"],
    "ano": ["ano"],
    "lote_prime": ["lote prime", "lote"],
    "status": ["status orcamento", "status"],
    "mes_contrato": ["mes contrato", "mes", "mes referencia", "mes_referencia"],
    "tipo": ["tipo de manutencao", "tipo manutencao", "tipo"],
    "status_retorno": ["status de retorno", "status retorno"],
    "ano_devolucao": ["ano devolucao"],
    "avaliacao": ["avaliacao orcamento", "avaliacao"],  # recalculada; só mapeada
    "qtde": ["qtde", "quantidade"],
    "empresa": ["empresa"],
}
OBRIGATORIAS = ("rma", "serie", "categoria")


def _ler_planilha(conteudo: bytes, nome: str):
    import pandas as pd
    sufixo = Path(nome or "").suffix.lower()
    try:
        if sufixo == ".csv":
            try:
                return pd.read_csv(io.BytesIO(conteudo), dtype=object, sep=None,
                                   engine="python", encoding="utf-8-sig")
            except UnicodeDecodeError:
                return pd.read_csv(io.BytesIO(conteudo), dtype=object, sep=None,
                                   engine="python", encoding="latin-1")
        return pd.read_excel(io.BytesIO(conteudo), sheet_name=0, dtype=object)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Arquivo inválido: {exc}")


def _linha_para_colunas(d: SimpleNamespace, agora, usuario: str) -> dict:
    return {
        "rma": d.rma, "serie": d.serie, "loja": d.loja, "categoria": d.categoria,
        "familia": d.familia, "empresa": d.empresa, "orcamento": d.orcamento,
        "garantia": d.garantia, "valor_compra": d.valor_compra,
        "valor_compra_fonte": d.valor_compra_fonte, "percentual": d.percentual,
        "avaliacao": d.avaliacao, "status": d.status, "status_original": d.status_original,
        "tipo_manutencao": d.tipo_manutencao, "tipo_original": d.tipo_original,
        "status_retorno": d.status_retorno, "ano": d.ano, "mes_referencia": d.mes_referencia,
        "ano_devolucao": d.ano_devolucao, "lote_prime": d.lote_prime, "qtde": d.qtde,
        "atualizado_em": agora, "atualizado_por": usuario,
    }


@router.post("/importar")
def importar(req: Request, file: UploadFile = File(...),
             substituir: bool = Form(False)):
    """`substituir=true`: a planilha vira a base. Linhas importadas antes que
    não estão no arquivo são removidas; o que foi digitado no portal fica."""
    sd = _exigir(req, "admin")
    check_rate_limit(req, "api")
    usuario = _usuario(sd)
    nome = Path(file.filename or "planilha.xlsx").name[:200]
    df = _ler_planilha(file.file.read(), nome)

    cmap = {_sem_acento(c): c for c in df.columns}
    colunas = {k: next((cmap[_sem_acento(a)] for a in al if _sem_acento(a) in cmap), None)
               for k, al in ALIASES.items()}
    faltando = [k for k in OBRIGATORIAS if not colunas[k]]
    if faltando:
        raise HTTPException(400, "Colunas obrigatórias ausentes: " + ", ".join(faltando)
                            + f". Colunas encontradas: {', '.join(map(str, df.columns))}")

    detalhes, avisos_detalhes = [], []
    avisos = Counter(status_nao_reconhecido=0, tipo_nao_reconhecido=0,
                     empresa_vazia=0, mes_referencia_nulo=0)
    novos, alterados, vistos = [], [], set()
    # Todo RMA que aparece no arquivo, mesmo em linha rejeitada: com
    # `substituir`, um erro de formato não pode apagar o que já existe.
    no_arquivo: set[str] = set()
    lidas = 0

    def rejeitar(linha: int, motivo: str) -> None:
        if len(detalhes) < 200:
            detalhes.append({"linha": linha, "motivo": motivo})

    with db.SessionLocal.begin() as s:
        config = db.ler_config(s)
        limiar = config["limiar_percentual"]
        # Um SELECT só: o que já existe, com o que a ordem 3.4 precisa preservar.
        existentes = {
            rma: (rid, vc, fonte) for rid, rma, vc, fonte in s.execute(
                select(R.id, R.rma, R.valor_compra, R.valor_compra_fonte)).all()
        }
        agora = db.utcnow()

        for idx, linha in enumerate(df.to_dict("records"), start=2):
            lidas += 1

            def val(k, _l=linha):
                c = colunas.get(k)
                return _l.get(c) if c else None

            rma = _texto_rma(val("rma"))
            if not rma:
                rejeitar(idx, "RMA vazio")
                continue
            no_arquivo.add(rma)
            if rma in vistos:
                rejeitar(idx, f"RMA repetido na planilha: {rma}")
                continue
            serie = _texto(val("serie"), 60).upper()
            if not serie:
                rejeitar(idx, "série vazia")
                continue
            categoria, familia = normalizar_categoria(val("categoria"))
            if not categoria:
                rejeitar(idx, "categoria vazia")
                continue
            try:
                orcamento, garantia = interpretar_orcamento(val("orcamento"))
            except ValueError as exc:
                rejeitar(idx, str(exc))
                continue
            vistos.add(rma)

            status_txt = _texto(val("status"), 60)
            status, ok = normalizar_status(status_txt)
            if not ok:
                avisos["status_nao_reconhecido"] += 1
                if len(avisos_detalhes) < 200:
                    avisos_detalhes.append({"linha": idx, "motivo": f"status não reconhecido: {status_txt!r}"})
            tipo_txt = _texto(val("tipo"), 60)
            tipo, ok = normalizar_tipo(tipo_txt)
            if not ok:
                avisos["tipo_nao_reconhecido"] += 1
                if len(avisos_detalhes) < 200:
                    avisos_detalhes.append({"linha": idx, "motivo": f"tipo não reconhecido: {tipo_txt!r}"})
            empresa = normalizar_empresa(val("empresa"))
            if not empresa:
                avisos["empresa_vazia"] += 1
            mes = mes_referencia(val("mes_contrato"))
            if not mes:
                avisos["mes_referencia_nulo"] += 1

            ant = existentes.get(rma)
            d = SimpleNamespace(
                rma=rma, serie=serie, loja=_inteiro(val("loja")), categoria=categoria,
                familia=familia, empresa=empresa, orcamento=orcamento, garantia=garantia,
                valor_compra=ant[1] if ant else None, valor_compra_fonte=(ant[2] if ant else "") or "",
                percentual=None, avaliacao="", status=status,
                status_original=status_txt or db.STATUS_ROTULOS[status],
                tipo_manutencao=tipo, tipo_original=tipo_txt,
                status_retorno=normalizar_status_retorno(val("status_retorno")),
                ano=_inteiro(val("ano")) or (int(mes[:4]) if mes else date.today().year),
                mes_referencia=mes, ano_devolucao=_inteiro(val("ano_devolucao")),
                lote_prime=_texto(val("lote_prime"), 200), qtde=_inteiro(val("qtde")) or 1,
            )
            d.valor_compra, d.valor_compra_fonte = _valor_compra_para(d, config, razao=_numero(val("razao")))
            calcular(d, limiar)

            cols = _linha_para_colunas(d, agora, usuario)
            if ant:
                alterados.append({"id": ant[0], **cols})
            else:
                novos.append({**cols, "observacao": "", "ebs_erro": "", "ebs_consultado_em": None,
                              "criado_em": agora, "criado_por": usuario, "origem": "PLANILHA"})

        for i in range(0, len(novos), 500):
            s.execute(insert(R), novos[i:i + 500])
        for i in range(0, len(alterados), 500):
            s.execute(update(R), alterados[i:i + 500])

        removidas = 0
        if substituir:
            # Só sai o que veio de planilha: o que foi digitado no portal fica.
            sobrando = [
                rid for rid, rma in s.execute(
                    select(R.id, R.rma).where(R.origem == "PLANILHA")).all()
                if rma not in no_arquivo
            ]
            for i in range(0, len(sobrando), 500):
                s.execute(delete(R).where(R.id.in_(sobrando[i:i + 500])))
            removidas = len(sobrando)

        rejeitadas = lidas - len(novos) - len(alterados)
        s.add(db.Importacao(
            arquivo=nome, usuario=usuario, lidas=lidas, incluidas=len(novos),
            atualizadas=len(alterados), rejeitadas=rejeitadas,
            detalhes=json.dumps({"substituir": bool(substituir), "removidas": removidas,
                                 "rejeicoes": detalhes}, ensure_ascii=False),
        ))

    return {
        "lidas": lidas, "incluidas": len(novos), "atualizadas": len(alterados),
        "rejeitadas": rejeitadas, "removidas": removidas,
        "substituiu": bool(substituir), "detalhes": detalhes,
        "avisos": dict(avisos), "avisos_detalhes": avisos_detalhes,
    }


# ── 5.8 Exportação ──────────────────────────────────────────────────────
@router.get("/exportar.xlsx")
def exportar(req: Request, ano: Optional[int] = None, mes: Optional[str] = None,
             familia: Optional[str] = None, categoria: Optional[str] = None,
             status: Optional[str] = None, status_retorno: Optional[str] = None,
             empresa: Optional[str] = None, tipo_manutencao: Optional[str] = None,
             q: Optional[str] = None):
    _exigir(req, "export")
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    with db.SessionLocal() as s:
        config = db.ler_config(s)
        itens = s.scalars(_ordenado(_filtrar(
            select(R), ano=ano, mes=mes, familia=familia, categoria=categoria, status=status,
            status_retorno=status_retorno, empresa=empresa, tipo_manutencao=tipo_manutencao, q=q,
        ))).all()

    lim = f"{config['limiar_percentual'] * 100:g}".replace(".", ",")
    aval = {"DENTRO": f"Dentro dos {lim}%", "FORA": f"Fora dos {lim}%", "": ""}
    colunas = [
        ("SÉRIE", lambda r: r.serie), ("LOJA", lambda r: r.loja), ("RMA", lambda r: r.rma),
        ("CATEGORIA", lambda r: r.categoria),
        ("ORÇAMENTO", lambda r: "Garantia" if r.garantia else float(r.orcamento or 0)),
        ("60% ORÇAMENTO", lambda r: r.percentual), ("ANO", lambda r: r.ano),
        ("LOTE PRIME", lambda r: r.lote_prime),
        ("STATUS ORÇAMENTO", lambda r: db.STATUS_ROTULOS.get(r.status, r.status)),
        ("MÊS CONTRATO", lambda r: r.mes_referencia or ""),
        ("TIPO DE MANUTENÇÃO", lambda r: db.TIPOS_ROTULOS.get(r.tipo_manutencao, r.tipo_manutencao)),
        ("STATUS DE RETORNO", lambda r: db.RETORNO_ROTULOS.get(r.status_retorno, r.status_retorno)),
        ("ANO DEVOLUÇÃO", lambda r: r.ano_devolucao),
        ("AVALIAÇÃO ORÇAMENTO", lambda r: aval.get(r.avaliacao or "", r.avaliacao)),
        ("QTDE", lambda r: r.qtde), ("EMPRESA", lambda r: r.empresa),
        ("VALOR COMPRA", lambda r: r.valor_compra), ("FONTE VALOR", lambda r: r.valor_compra_fonte),
        ("PERCENTUAL", lambda r: round(r.percentual * 100, 2) if r.percentual is not None else None),
        ("FAMÍLIA", lambda r: r.familia), ("OBSERVAÇÃO", lambda r: r.observacao),
    ]

    wb = Workbook()
    ws = wb.active
    ws.title = "Manutenção"
    ws.append([c for c, _ in colunas])
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="AB4807")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center")
    for r in itens:
        ws.append([fn(r) for _, fn in colunas])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for coluna in ws.columns:
        largura = max(len(str(c.value or "")) for c in coluna) + 2
        ws.column_dimensions[coluna[0].column_letter].width = min(45, max(12, largura))

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    nome = f"orcamento_manutencao_{ano or 'todos'}.xlsx"
    return StreamingResponse(
        stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


# ── 5.9 Configuração ────────────────────────────────────────────────────
@router.get("/config")
def config_ler(req: Request):
    _exigir(req, "view")
    with db.SessionLocal() as s:
        return db.ler_config(s)


def _mapa_numerico(mapa: dict, rotulo: str) -> dict:
    """{chave: número > 0}; entradas vazias/zeradas são removidas."""
    saida = {}
    for k, v in (mapa or {}).items():
        chave = _texto(k, 40)
        if not chave or _vazio(v):
            continue
        n = _numero(v)
        if n is None or n < 0:
            raise HTTPException(422, f"{rotulo}: valor inválido para {chave!r}: {_texto(v)!r}")
        if n > 0:
            saida[chave] = round(n, 2)
    return saida


@router.put("/config")
def config_gravar(body: ConfigIn, req: Request):
    sd = _exigir(req, "admin")
    check_rate_limit(req, "api")
    dados: dict = {}
    if body.cota_mensal is not None:
        cotas = _mapa_numerico(body.cota_mensal, "cota_mensal")
        if any(not k.isdigit() or len(k) != 4 for k in cotas):
            raise HTTPException(422, "cota_mensal: as chaves devem ser o ano (AAAA).")
        dados["cota_mensal"] = cotas
    if body.limiar_percentual is not None:
        n = _numero(body.limiar_percentual)
        if n is None or n <= 0:
            raise HTTPException(422, "limiar_percentual deve ser uma fração maior que zero (ex.: 0.60).")
        dados["limiar_percentual"] = round(n / 100 if n > 1 else n, 4)  # aceita 60 no lugar de 0.60
    if body.valor_compra_padrao is not None:
        dados["valor_compra_padrao"] = _mapa_numerico(body.valor_compra_padrao, "valor_compra_padrao")
    with db.SessionLocal.begin() as s:
        db.gravar_config(s, dados, _usuario(sd))
        return db.ler_config(s)


@router.post("/recalcular")
def recalcular(req: Request):
    """Refaz percentual/avaliação de todas as linhas (e o valor PADRAO, se a
    configuração mudou) e aplica a regra 3.5 nas pendentes."""
    sd = _exigir(req, "admin")
    check_rate_limit(req, "api")
    mudaram = 0
    with db.SessionLocal.begin() as s:
        config = db.ler_config(s)
        linhas = s.scalars(select(R)).all()
        for r in linhas:
            antes = (r.valor_compra, r.valor_compra_fonte)
            r.valor_compra, r.valor_compra_fonte = _valor_compra_para(r, config)
            if calcular(r, config["limiar_percentual"]) or antes != (r.valor_compra, r.valor_compra_fonte):
                mudaram += 1
                r.atualizado_por = _usuario(sd)
    return {"total": len(linhas), "mudaram": mudaram}


# ── 5.10 Opções para os selects ─────────────────────────────────────────
@router.get("/opcoes")
def opcoes(req: Request):
    _exigir(req, "view")
    with db.SessionLocal() as s:
        config = db.ler_config(s)
        categorias = {c for (c,) in s.execute(select(R.categoria).distinct()).all() if c}
        categorias |= set((config.get("valor_compra_padrao") or {}).keys())
        empresas = {e for (e,) in s.execute(select(R.empresa).distinct()).all() if e}
        anos = sorted({int(a) for (a,) in s.execute(select(R.ano).distinct()).all() if a}
                      | {date.today().year}, reverse=True)
        lotes = [l for (l, _) in s.execute(
            select(R.lote_prime, func.count(R.id)).where(R.lote_prime != "")
            .group_by(R.lote_prime).order_by(func.count(R.id).desc()).limit(20)).all()]
    return {
        "categorias": sorted(categorias),
        "familias": list(db.FAMILIAS),
        "status": [{"valor": k, "rotulo": v} for k, v in db.STATUS_ROTULOS.items()],
        "tipos": [{"valor": k, "rotulo": v} for k, v in db.TIPOS_ROTULOS.items()],
        "status_retorno": [{"valor": k, "rotulo": v} for k, v in db.RETORNO_ROTULOS.items()],
        "empresas": sorted(set(db.EMPRESAS) | empresas),
        "fontes_valor": list(db.FONTES_VALOR),
        "anos": anos,
        "lotes": lotes,
        "limiar_percentual": config["limiar_percentual"],
    }
