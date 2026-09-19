"""Recebimento → Fornecedores — API /api/recebimento/fornecedores."""
from __future__ import annotations

import logging
import re
from datetime import date

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, field_validator

import db.agendamentos_forn as agf_db
import db.internalizacao as int_db
from core.security import check_rate_limit, client_ip, require_permission
from routers import agendamentos_forn as agf
from routers import internalizacao as internal

_log = logging.getLogger("recebimento_fornecedores")
MODULO = "recebimento"
AREA_TMP = "recebimento_forn"
_MAX_ARQUIVO_NF = 10 * 1024 * 1024
_MAX_UNIDADES = 2000

router = APIRouter(prefix="/api/recebimento/fornecedores",
                   tags=["Recebimento de Fornecedores"])

def _exigir(req: Request, acao: str) -> dict:
    return require_permission(req, MODULO, acao)

def _tem_ebs(bu: str) -> bool:
    return (bu or "") in agf.BUS_COM_EBS

def _limpar_expirados() -> None:
    try:
        from core import arquivos_temporarios
        arquivos_temporarios.limpar_expirados(area=AREA_TMP)
    except Exception as exc:  # noqa: BLE001
        _log.debug("limpeza dos arquivos temporários não rodou: %s", exc)

def _gravar_tmp(agendamento_id: int, nome: str, conteudo: bytes) -> str:
    from core import arquivos_temporarios
    caminho = arquivos_temporarios.gravar(AREA_TMP, agendamento_id, nome, conteudo)
    return caminho.name

def _caminho_tmp(agendamento_id: int, nome: str):
    from core import arquivos_temporarios
    return arquivos_temporarios.caminho(AREA_TMP, agendamento_id, nome)

def _certificado_da_bu(bu: str) -> bool:
    try:
        from integracoes import nfe_sefaz
        return nfe_sefaz.certificado_da_bu(bu) is not None
    except Exception:  # noqa: BLE001
        return False

def _resumo(a) -> dict:
    d = a.to_dict()
    d["tem_ebs"] = _tem_ebs(d.get("bu", ""))
    return d

@router.get("")
@router.get("/")
def listar(req: Request, status: str = "", busca: str = ""):
    _exigir(req, "view")
    _limpar_expirados()
    from sqlalchemy import select
    agf_db.ensure_db()
    st = (status or "").strip().upper()
    termo = (busca or "").strip().lower()
    with agf_db.SessionLocal() as s:
        q = select(agf_db.Agendamento)
        if st in agf_db.STATUS:
            q = q.where(agf_db.Agendamento.status == st)
        linhas = [_resumo(a) for a in s.scalars(q).all()]
    if termo:
        def bate(a):
            campos = [a.get("nf", ""), a.get("po", ""), a.get("fornecedor", ""), a.get("bu", "")]
            campos += [p.get("po", "") for p in a.get("pedidos", [])]
            campos += [p.get("nf", "") for p in a.get("pedidos", [])]
            return any(termo in str(c).lower() for c in campos)
        linhas = [a for a in linhas if bate(a)]
    linhas.sort(key=lambda a: (
        0 if a["status"] == "AGENDADO" else 1,
        a["data_agendada"] if a["status"] == "AGENDADO" else "",
        -a["id"]))
    return {"total": len(linhas), "itens": linhas}

def _carregar(s, agendamento_id: int):
    a = s.get(agf_db.Agendamento, agendamento_id)
    if not a:
        raise HTTPException(404, "Agendamento não encontrado.")
    return a

def _quantidade_na_nf(nota_itens: list[dict], item_ebs: str, descricao: str):
    alvo = int_db.normalizar_item_ebs(item_ebs or "")
    desc = " ".join((descricao or "").split()).lower()
    for it in nota_itens or []:
        cod = int_db.normalizar_item_ebs(str(it.get("codigo") or ""))
        if alvo and cod and cod == alvo:
            return int(it.get("quantidade") or 0)
    for it in nota_itens or []:
        d = " ".join(str(it.get("descricao") or "").split()).lower()
        if desc and d and d == desc:
            return int(it.get("quantidade") or 0)
    return None

@router.get("/{agendamento_id}/preparar")
def preparar(agendamento_id: int, req: Request):
    _exigir(req, "view")
    check_rate_limit(req, "api")
    agf_db.ensure_db()
    int_db.ensure_db()

    with agf_db.SessionLocal() as s:
        a = _carregar(s, agendamento_id)
        if a.status == "RECEBIDO" or a.recebimento is not None:
            raise HTTPException(409, "Este agendamento já foi recebido.")
        dado = _resumo(a)
        notas = {n.nf: n.to_dict() for n in a.notas}
    for p in dado["pedidos"]:
        if p.get("nf") and p["nf"] not in notas:
            notas[p["nf"]] = {"nf": p["nf"], "chave": "", "vencimento": "", "origem": "",
                              "origem_rotulo": "", "tem_xml": False, "tem_pdf": False,
                              "itens": [], "emitente": "", "cstat": "", "erro": ""}
    nf_da_po = {p["po"]: p.get("nf", "") for p in dado["pedidos"]}

    with int_db.SessionLocal() as si:
        imobilizados = internal.codigos_imobilizados(si)
        etiquetas = len(internal.etiquetas_disponiveis(si, 100000))

    avisos: list[str] = []
    itens: list[dict] = []
    fora: list[dict] = []
    if _tem_ebs(dado["bu"]):
        if not dado["pedidos"]:
            raise HTTPException(422, "O agendamento não tem PO. Inclua a PO no agendamento antes de receber.")
        for ped in dado["pedidos"]:
            po = agf.buscar_po_no_ebs(ped["po"], dado["bu"])
            for l in po.get("itens", []):
                codigo = int_db.normalizar_item_ebs(l.get("item_ebs") or "")
                nf = ped.get("nf", "")
                linha = {
                    "po": ped["po"], "nf": nf, "linha": l.get("linha"),
                    "item_ebs": codigo, "descricao": l.get("descricao") or "",
                    "unidade": l.get("unidade") or "",
                    "quantidade_pedida": int(l.get("quantidade_pedida") or 0),
                    "quantidade_recebida_ebs": int(l.get("quantidade_recebida") or 0),
                    "quantidade_pendente": int(l.get("quantidade_pendente") or 0),
                    "quantidade_nf": _quantidade_na_nf(
                        (notas.get(nf) or {}).get("itens", []), codigo, l.get("descricao") or ""),
                    "imobilizado": codigo in imobilizados,
                }
                (itens if linha["imobilizado"] else fora).append(linha)
        if not itens:
            raise HTTPException(
                422, "Nenhum item desta(s) PO(s) está na lista de itens imobilizados. "
                     "Cadastre em Internalização → Itens Imobilizados e tente de novo.")
        if fora:
            avisos.append(f"{len(fora)} item(ns) da PO não são imobilizados: seguem para o "
                          "pagamento junto com a nota, sem etiqueta nem lançamento.")
    else:
        if not dado["bu"]:
            avisos.append("Agendamento sem BU: conferência manual, sem consulta ao EBS.")
        else:
            avisos.append(f"A BU {dado['bu']} não tem pedido no EBS: confira os itens à mão.")
        po_unica = dado["pedidos"][0]["po"] if dado["pedidos"] else ""
        for eq in dado["equipamentos"]:
            itens.append({
                "po": po_unica, "nf": nf_da_po.get(po_unica, ""), "linha": None,
                "item_ebs": "", "descricao": eq.get("descricao") or "",
                "unidade": "", "quantidade_pedida": int(eq.get("quantidade") or 0),
                "quantidade_recebida_ebs": 0,
                "quantidade_pendente": int(eq.get("quantidade") or 0),
                "quantidade_nf": _quantidade_na_nf(
                    (notas.get(nf_da_po.get(po_unica, "")) or {}).get("itens", []),
                    "", eq.get("descricao") or ""),
                "imobilizado": True, "manual": True,
            })

    unidades = sum(i["quantidade_pendente"] for i in itens)
    if etiquetas < unidades:
        avisos.append(f"Há {etiquetas} etiqueta(s) disponível(is) para {unidades} unidade(s) "
                      "pendentes. Cadastre etiquetas antes de confirmar.")
    return {
        "agendamento": dado,
        "tem_ebs": _tem_ebs(dado["bu"]),
        "itens": itens,
        "nao_imobilizados": fora,
        "notas": list(notas.values()),
        "etiquetas_disponiveis": etiquetas,
        "certificado_bu": _certificado_da_bu(dado["bu"]),
        "avisos": avisos,
    }

class NotaIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    nf: str = ""
    chave: str = ""
    vencimento: str = ""

    @field_validator("nf")
    @classmethod
    def _nf(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or len(v) > 40:
            raise ValueError("Informe o número da NF.")
        return v

    @field_validator("chave")
    @classmethod
    def _chave(cls, v: str) -> str:
        v = re.sub(r"\D", "", v or "")
        if v and len(v) != 44:
            raise ValueError("A chave de acesso tem 44 dígitos.")
        return v

    @field_validator("vencimento")
    @classmethod
    def _venc(cls, v: str) -> str:
        v = (v or "").strip()
        if v:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise ValueError("Vencimento inválido (use AAAA-MM-DD).")
        return v

def _guardar_xml(agendamento_id: int, nf: str, xml: bytes, campos: dict) -> dict:
    seguro = re.sub(r"[^A-Za-z0-9_-]", "_", nf)[:40]
    saida = {
        "xml_arquivo": _gravar_tmp(agendamento_id, f"nf_{seguro}.xml", xml),
        "itens": campos.get("itens") or [],
        "emitente": (campos.get("emit_nome") or "")[:160],
        "erro": "",
    }
    try:
        from integracoes import nfe_sefaz
        pdf = nfe_sefaz.danfe_pdf(xml)
        saida["pdf_arquivo"] = _gravar_tmp(agendamento_id, f"nf_{seguro}.pdf", pdf)
    except Exception as exc:  # noqa: BLE001
        saida["erro"] = f"XML guardado; o PDF da DANFE não foi gerado: {str(exc)[:200]}"
        _log.warning("DANFE da NF %s não gerada: %s", nf, exc)
    return saida

def _campos_do_xml(xml: bytes) -> dict:
    try:
        from integracoes import nfe_sefaz
        return nfe_sefaz.campos(xml)
    except ImportError:
        from core import nf_pdf
        return nf_pdf.extrair_de_xml(xml)

@router.post("/{agendamento_id}/nota")
def registrar_nota(agendamento_id: int, body: NotaIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    agf_db.ensure_db()
    usuario = sd.get("username", "")

    with agf_db.SessionLocal() as s:
        a = _carregar(s, agendamento_id)
        if a.status == "RECEBIDO":
            raise HTTPException(409, "Este agendamento já foi recebido.")
        bu = a.bu or ""
        nfs_do_agendamento = {p.nf for p in a.pedidos if p.nf}
    if nfs_do_agendamento and body.nf not in nfs_do_agendamento:
        raise HTTPException(422, f"A NF {body.nf} não está no agendamento "
                                 f"({', '.join(sorted(nfs_do_agendamento))}).")

    campos_nota: dict = {"chave": body.chave}
    if body.vencimento:
        campos_nota["vencimento"] = date.fromisoformat(body.vencimento)

    if body.chave and _certificado_da_bu(bu):
        from integracoes import nfe_sefaz
        try:
            res = nfe_sefaz.buscar_por_chave(body.chave, bu)
        except Exception as exc:  # noqa: BLE001
            res = None
            campos_nota.update({"origem": "DIGITADA", "erro": f"SEFAZ: {str(exc)[:250]}"})
            _log.warning("SEFAZ chave %s: %s", body.chave, exc)
        if res is not None:
            campos_nota["cstat"] = str(getattr(res, "cstat", "") or "")
            if getattr(res, "ok", False) and getattr(res, "completa", False) and res.xml:
                campos = _campos_do_xml(res.xml)
                campos_nota.update(_guardar_xml(agendamento_id, body.nf, res.xml, campos))
                campos_nota["origem"] = "SEFAZ"
                if not body.vencimento and campos.get("vencimento"):
                    campos_nota["vencimento"] = date.fromisoformat(campos["vencimento"])
                if campos.get("nf") and str(campos["nf"]) != body.nf.lstrip("0"):
                    campos_nota["erro"] = (f"Atenção: a chave é da NF {campos['nf']}, "
                                           f"e o agendamento diz NF {body.nf}.")
            else:
                campos_nota["origem"] = "DIGITADA"
                campos_nota["erro"] = (getattr(res, "erro", "") or getattr(res, "xmotivo", "")
                                       or "A SEFAZ não devolveu a NF-e.")[:300]
    elif body.chave:
        campos_nota["origem"] = "DIGITADA"
        campos_nota["erro"] = ("Sem certificado para esta BU: envie o XML ou o PDF da nota."
                               if bu else "Agendamento sem BU: envie o XML ou o PDF da nota.")
    else:
        campos_nota.setdefault("origem", "DIGITADA")

    with agf_db.SessionLocal.begin() as s:
        a = _carregar(s, agendamento_id)
        nota = agf.registrar_nota(s, a, body.nf, usuario, **campos_nota)
        dado = nota.to_dict()
    _log.info("nota %s do agendamento %s: origem=%s por=%s ip=%s",
              body.nf, agendamento_id, dado["origem"], usuario, client_ip(req))
    return dado

@router.post("/{agendamento_id}/nota/arquivo")
async def enviar_arquivo_nota(agendamento_id: int, req: Request,
                              nf: str = Form(...), arquivo: UploadFile = File(...)):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    agf_db.ensure_db()
    usuario = sd.get("username", "")
    nf = (nf or "").strip()[:40]
    if not nf:
        raise HTTPException(422, "Informe o número da NF.")
    dados = await arquivo.read(_MAX_ARQUIVO_NF + 1)
    if not dados:
        raise HTTPException(422, "Arquivo vazio.")
    if len(dados) > _MAX_ARQUIVO_NF:
        raise HTTPException(413, "Arquivo maior que 10 MB.")

    with agf_db.SessionLocal() as s:
        a = _carregar(s, agendamento_id)
        if a.status == "RECEBIDO":
            raise HTTPException(409, "Este agendamento já foi recebido.")

    from core import nf_pdf
    campos_nota: dict = {"origem": "ARQUIVO", "erro": ""}
    if nf_pdf._eh_xml(dados):
        try:
            campos = _campos_do_xml(dados)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"O XML não é uma NF-e legível: {str(exc)[:200]}") from exc
        campos_nota.update(_guardar_xml(agendamento_id, nf, dados, campos))
    elif dados[:5] == b"%PDF-":
        try:
            campos = nf_pdf.extrair_campos(dados)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"Não consegui ler o PDF: {str(exc)[:200]}") from exc
        seguro = re.sub(r"[^A-Za-z0-9_-]", "_", nf)[:40]
        campos_nota["pdf_arquivo"] = _gravar_tmp(agendamento_id, f"nf_{seguro}.pdf", dados)
        campos_nota["itens"] = campos.get("itens") or []
        if campos_nota["itens"]:
            campos_nota["erro"] = "Itens lidos do PDF são estimados: confira as quantidades."
    else:
        raise HTTPException(422, "Envie o XML da NF-e ou o PDF da DANFE.")
    if campos.get("chave"):
        campos_nota["chave"] = campos["chave"]
    if campos.get("vencimento"):
        campos_nota["vencimento"] = date.fromisoformat(campos["vencimento"])

    with agf_db.SessionLocal.begin() as s:
        a = _carregar(s, agendamento_id)
        nota = agf.registrar_nota(s, a, nf, usuario, **campos_nota)
        dado = nota.to_dict()
    _log.info("arquivo da nota %s do agendamento %s (%s bytes) por=%s ip=%s",
              nf, agendamento_id, len(dados), usuario, client_ip(req))
    return dado

@router.get("/{agendamento_id}/nota/{nf}/{tipo}")
def baixar_nota(agendamento_id: int, nf: str, tipo: str, req: Request):
    _exigir(req, "view")
    if tipo not in ("xml", "pdf"):
        raise HTTPException(404, "Arquivo não encontrado.")
    agf_db.ensure_db()
    with agf_db.SessionLocal() as s:
        a = _carregar(s, agendamento_id)
        nota = next((n for n in a.notas if n.nf == nf), None)
        nome = (nota.xml_arquivo if tipo == "xml" else nota.pdf_arquivo) if nota else ""
    caminho = _caminho_tmp(agendamento_id, nome) if nome else None
    if not caminho:
        raise HTTPException(404, "Arquivo não encontrado (pode ter passado dos cinco dias).")
    return FileResponse(str(caminho), filename=caminho.name,
                        media_type="application/xml" if tipo == "xml" else "application/pdf")

class ItemConferidoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    po: str = ""
    nf: str = ""
    linha: int | None = None
    item_ebs: str = ""
    descricao: str = ""
    unidade: str = ""
    quantidade_pedida: int = 0
    quantidade_recebida: int = 0
    imobilizado: bool = True
    seriais: list[str] = []

    @field_validator("po", "nf", "unidade")
    @classmethod
    def _curto(cls, v: str) -> str:
        return (v or "").strip()[:40]

    @field_validator("item_ebs")
    @classmethod
    def _item(cls, v: str) -> str:
        return int_db.normalizar_item_ebs((v or "").strip()[:60])

    @field_validator("descricao")
    @classmethod
    def _desc(cls, v: str) -> str:
        return " ".join((v or "").split())[:200]

    @field_validator("quantidade_pedida", "quantidade_recebida")
    @classmethod
    def _qtd(cls, v: int) -> int:
        v = int(v or 0)
        if v < 0:
            raise ValueError("Quantidade não pode ser negativa.")
        return v

    @field_validator("seriais")
    @classmethod
    def _seriais(cls, v: list[str]) -> list[str]:
        return [" ".join(str(x).split())[:80] for x in (v or []) if str(x).strip()]

class ConfirmarIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    itens: list[ItemConferidoIn] = []
    observacao: str = ""

    @field_validator("observacao")
    @classmethod
    def _obs(cls, v: str) -> str:
        return " ".join((v or "").split())[:500]

def _seriais_ja_usados(seriais: set[str]) -> dict[str, str]:
    from sqlalchemy import func, select
    onde: dict[str, str] = {}
    if not seriais:
        return onde
    alvos = [x.upper() for x in seriais]
    with agf_db.SessionLocal() as s:
        for u in s.scalars(select(agf_db.RecebimentoUnidade).where(
                func.upper(agf_db.RecebimentoUnidade.serial).in_(alvos))).all():
            onde[u.serial.upper()] = f"recebimento do agendamento {u.item.recebimento.agendamento_id}"
    with int_db.SessionLocal() as si:
        for a in si.scalars(select(int_db.Ativo).where(
                func.upper(int_db.Ativo.numero_serie).in_(alvos))).all():
            onde.setdefault(a.numero_serie.upper(), f"lançamento do agendamento {a.processo.agendamento_id}")
    return onde

@router.post("/{agendamento_id}/confirmar")
def confirmar(agendamento_id: int, body: ConfirmarIn, req: Request):
    sd = _exigir(req, "create")
    check_rate_limit(req, "api")
    agf_db.ensure_db()
    int_db.ensure_db()
    usuario = sd.get("username", "")

    with agf_db.SessionLocal() as s:
        a = _carregar(s, agendamento_id)
        if a.status == "RECEBIDO" or a.recebimento is not None:
            raise HTTPException(409, "Este agendamento já foi recebido.")
        dado = _resumo(a)
        notas = {n.nf: n.itens() for n in a.notas}
    tem_ebs = dado["tem_ebs"]

    imobilizados = [i for i in body.itens if i.imobilizado]
    if not imobilizados:
        raise HTTPException(422, "Nenhum item imobilizado na conferência.")
    if sum(i.quantidade_recebida for i in imobilizados) == 0:
        raise HTTPException(422, "Nada recebido: informe a quantidade de ao menos um item.")
    if sum(i.quantidade_recebida for i in imobilizados) > _MAX_UNIDADES:
        raise HTTPException(422, f"Mais de {_MAX_UNIDADES} unidades numa conferência só: divida.")

    with int_db.SessionLocal() as si:
        lista = internal.codigos_imobilizados(si)
        etiquetas = len(internal.etiquetas_disponiveis(si, _MAX_UNIDADES + 1))

    problemas: list[str] = []
    vistos: dict[str, str] = {}
    for it in imobilizados:
        nome = it.descricao or it.item_ebs or f"PO {it.po} linha {it.linha}"
        if tem_ebs and it.item_ebs not in lista:
            problemas.append(f"{nome}: o item {it.item_ebs or '(sem código)'} não está na lista de imobilizados.")
        if not tem_ebs and not it.descricao:
            problemas.append("Item sem descrição.")
        if len(it.seriais) != it.quantidade_recebida:
            problemas.append(f"{nome}: {it.quantidade_recebida} recebido(s), "
                             f"{len(it.seriais)} serial(is) informado(s).")
        for serial in it.seriais:
            chave = serial.upper()
            if chave in vistos:
                problemas.append(f"Serial {serial} repetido ({vistos[chave]} e {nome}).")
            vistos[chave] = nome
        qtd_nf = _quantidade_na_nf(notas.get(it.nf, []), it.item_ebs, it.descricao)
        if qtd_nf is not None and qtd_nf != it.quantidade_recebida:
            problemas.append(f"{nome}: a NF {it.nf} traz {qtd_nf}, e a conferência diz "
                             f"{it.quantidade_recebida}. Acerte a quantidade ou a nota.")
    usados = _seriais_ja_usados(set(vistos))
    for serial, onde in usados.items():
        problemas.append(f"Serial {serial} já está no portal ({onde}).")
    if problemas:
        raise HTTPException(422, " ".join(problemas[:12]))

    unidades = sum(i.quantidade_recebida for i in imobilizados)
    if etiquetas < unidades:
        raise HTTPException(
            409, f"Faltam {unidades - etiquetas} etiqueta(s) no estoque para {unidades} "
                 "unidade(s). Cadastre em Internalização → Cadastro de Etiquetas e confirme de novo.")

    faltantes = [{"descricao": i.descricao or i.item_ebs,
                  "faltam": i.quantidade_pedida - i.quantidade_recebida}
                 for i in imobilizados if i.quantidade_recebida < i.quantidade_pedida]
    itens_gravar = []
    for it in body.itens:
        d = it.model_dump()
        d["quantidade_nf"] = _quantidade_na_nf(notas.get(it.nf, []), it.item_ebs, it.descricao)
        if not it.imobilizado:
            d["seriais"] = []
        itens_gravar.append(d)
    dados_rec = {
        "itens": itens_gravar,
        "entrega_parcial": bool(faltantes),
        "tem_nao_imobilizados": any(not i.imobilizado for i in body.itens),
        "observacao": body.observacao,
    }

    with agf_db.SessionLocal.begin() as s:
        a = _carregar(s, agendamento_id)
        rec = agf.registrar_recebimento(s, a, dados_rec, usuario)
        rec_dado = rec.to_dict()
        ag_dado = _resumo(a)

    processo_id = None
    aviso = ""
    try:
        proc = internal.abrir_processo_do_recebimento(ag_dado, rec_dado, usuario)
        processo_id = proc["id"]
    except HTTPException as exc:
        aviso = f"Recebimento gravado, mas o lançamento não abriu: {exc.detail}"
        _log.error("agendamento %s recebido sem processo: %s", agendamento_id, exc.detail)
    except Exception as exc:  # noqa: BLE001
        aviso = f"Recebimento gravado, mas o lançamento não abriu: {str(exc)[:200]}"
        _log.error("agendamento %s recebido sem processo: %s", agendamento_id, exc, exc_info=True)

    _log.info("recebimento confirmado agendamento=%s unidades=%s parcial=%s por=%s ip=%s",
              agendamento_id, unidades, bool(faltantes), usuario, client_ip(req))
    return {
        "ok": True,
        "entrega_parcial": bool(faltantes),
        "faltantes": faltantes,
        "processo_id": processo_id,
        "etiquetas_consumidas": unidades if processo_id else 0,
        "agendamento": ag_dado,
        "aviso": aviso,
    }
