"""Internalização (menu Entrada) — API /api/internalizacao.

Recebe os agendamentos cujo recebimento foi confirmado (status RECEBIDO) e,
para cada equipamento fisicamente recebido, registra: item do EBS, descrição,
plaqueta (nº do bem), número de série e a NF. Grava em banco próprio
(`db/internalizacao.py`) e exporta a planilha "Placa Patrimonial" no mesmo
layout do modelo oficial.

Lê o banco dos Agendamentos apenas para LISTAR os recebidos e tirar o
snapshot do processo; nunca escreve nele.

Permissão pelo módulo "internalizacao": view lê, create/edit lançam e salvam,
export exporta, admin exclui.
"""
from __future__ import annotations

import io
import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, field_validator

import db.internalizacao as db
import db.agendamentos_forn as agf_db
from core.security import check_rate_limit, client_ip, require_permission

_log = logging.getLogger("internalizacao")
MODULO = "internalizacao"

router = APIRouter(prefix="/api/internalizacao", tags=["Internalização"])

_XLSX_MIME = ("application/vnd.openxmlformats-officedocument."
              "spreadsheetml.sheet")


# ── Entrada validada ──────────────────────────────────────────────────────
class AtivoIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ebs_item: str = ""
    descricao: str = ""
    plaqueta: str = ""
    numero_serie: str = ""

    @field_validator("ebs_item", "descricao", "plaqueta", "numero_serie")
    @classmethod
    def _limpa(cls, v: str) -> str:
        return (v or "").strip()[:200]


class SalvarIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ativos: list[AtivoIn] = []
    concluir: bool = False


def _exigir(req: Request, acao: str) -> dict:
    return require_permission(req, MODULO, acao)


def _garantir_processo(s, agendamento_id: int):
    """Retorna o Processo do agendamento, criando-o (com snapshot) se preciso.
    O snapshot vem do banco dos Agendamentos e só é lido para copiar os dados."""
    from sqlalchemy import select
    proc = s.scalar(select(db.Processo).where(
        db.Processo.agendamento_id == agendamento_id))
    if proc:
        return proc
    agf_db.ensure_db()
    with agf_db.SessionLocal() as sa:
        ag = sa.get(agf_db.Agendamento, agendamento_id)
        if not ag:
            raise HTTPException(404, "Agendamento não encontrado.")
        if ag.status != "RECEBIDO":
            raise HTTPException(409, "O recebimento deste agendamento ainda não "
                                     "foi confirmado.")
        dados = ag.to_dict()
    proc = db.Processo(
        agendamento_id=agendamento_id,
        bu=dados["bu"], fornecedor=dados["fornecedor"], nf=dados["nf"],
        estoque_destino=dados["estoque_destino"],
        estoque_destino_rotulo=dados["estoque_destino_rotulo"],
        data_recebimento=(date.fromisoformat(dados["data_recebimento"])
                          if dados["data_recebimento"] else None),
        status="PENDENTE",
    )
    s.add(proc)
    s.flush()
    return proc


# ── Rotas ─────────────────────────────────────────────────────────────────
@router.get("")
@router.get("/")
def listar(req: Request, status: str = "", busca: str = ""):
    """Lista os agendamentos recebidos e o andamento da internalização de cada."""
    _exigir(req, "view")
    from sqlalchemy import select
    agf_db.ensure_db()
    db.ensure_db()
    # processos já existentes, por agendamento
    with db.SessionLocal() as s:
        procs = {p.agendamento_id: p.to_dict(com_ativos=False)
                 for p in s.scalars(select(db.Processo)).all()}
    # agendamentos recebidos (a origem da fila)
    with agf_db.SessionLocal() as sa:
        recs = sa.scalars(
            select(agf_db.Agendamento)
            .where(agf_db.Agendamento.status == "RECEBIDO")
            .order_by(agf_db.Agendamento.id.desc())
        ).all()
        linhas = []
        for ag in recs:
            d = ag.to_dict()
            p = procs.get(ag.id)
            linhas.append({
                "agendamento_id": ag.id,
                "nf": d["nf"], "po": d["po"], "bu": d["bu"],
                "fornecedor": d["fornecedor"],
                "estoque_destino_rotulo": d["estoque_destino_rotulo"],
                "data_recebimento": d["data_recebimento"],
                "equipamentos": d["equipamentos"],
                "status": (p or {}).get("status", "PENDENTE"),
                "status_rotulo": (p or {}).get("status_rotulo", "Pendente"),
                "total_ativos": (p or {}).get("total_ativos", 0),
            })
    st = (status or "").strip().upper()
    if st in db.STATUS:
        linhas = [l for l in linhas if l["status"] == st]
    termo = (busca or "").strip().lower()
    if termo:
        linhas = [l for l in linhas
                  if any(termo in str(l.get(c, "")).lower()
                         for c in ("nf", "po", "fornecedor", "bu"))]
    return {"total": len(linhas), "itens": linhas}


@router.get("/{agendamento_id}")
def obter(agendamento_id: int, req: Request):
    """Abre a internalização de um agendamento (cria o processo se preciso) e
    devolve os ativos já lançados + os equipamentos esperados (para semear)."""
    _exigir(req, "view")
    db.ensure_db()
    with db.SessionLocal.begin() as s:
        proc = _garantir_processo(s, agendamento_id)
        dado = proc.to_dict()
    # equipamentos esperados (do agendamento) para orientar o lançamento
    agf_db.ensure_db()
    with agf_db.SessionLocal() as sa:
        ag = sa.get(agf_db.Agendamento, agendamento_id)
        dado["equipamentos_esperados"] = (
            [e.to_dict() for e in ag.equipamentos] if ag else [])
    return dado


@router.put("/{agendamento_id}")
def salvar(agendamento_id: int, body: SalvarIn, req: Request):
    """Substitui a lista de ativos internalizados do agendamento."""
    sd = _exigir(req, "edit")
    db.ensure_db()
    usuario = sd.get("username", "")
    with db.SessionLocal.begin() as s:
        proc = _garantir_processo(s, agendamento_id)
        proc.ativos.clear()
        for a in body.ativos:
            if not (a.ebs_item or a.descricao or a.plaqueta or a.numero_serie):
                continue   # linha em branco: ignora
            proc.ativos.append(db.Ativo(
                ebs_item=a.ebs_item, descricao=a.descricao,
                plaqueta=a.plaqueta, numero_serie=a.numero_serie,
                criado_por=usuario,
            ))
        proc.status = "CONCLUIDA" if body.concluir else "PENDENTE"
        proc.atualizado_em = db.utcnow()
        s.flush()
        dado = proc.to_dict()
    _log.info("internalização salva agendamento=%s ativos=%s status=%s por=%s ip=%s",
              agendamento_id, len(dado["ativos"]), dado["status"], usuario,
              client_ip(req))
    return dado


@router.get("/{agendamento_id}/exportar")
def exportar(agendamento_id: int, req: Request):
    """Gera a planilha "Placa Patrimonial" (mesmo layout do modelo oficial)."""
    _exigir(req, "export")
    db.ensure_db()
    from sqlalchemy import select
    with db.SessionLocal() as s:
        proc = s.scalar(select(db.Processo).where(
            db.Processo.agendamento_id == agendamento_id))
        if not proc:
            raise HTTPException(404, "Nada internalizado para este agendamento.")
        nf = proc.nf or ""
        linhas = [a.to_dict() for a in proc.ativos]
    conteudo = _montar_xlsx(nf, linhas)
    nome = f"Placa_Patrimonial_NF_{nf or agendamento_id}.xlsx"
    return StreamingResponse(
        io.BytesIO(conteudo), media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@router.delete("/{agendamento_id}", status_code=204)
def excluir(agendamento_id: int, req: Request):
    """Remove o processo de internalização.

    Some o que foi registrado AQUI — patrimônio, plaquetas, confirmações e os
    ativos do processo. O agendamento em si e o que já deu entrada no estoque
    do portal NÃO são tocados: são de outros módulos.

    Não dá para desfazer, então fica no log de acesso com quantos ativos o
    processo tinha. Na tela o botão é só do admin do portal inteiro."""
    sd = _exigir(req, "admin")
    db.ensure_db()
    from sqlalchemy import func, select
    with db.SessionLocal.begin() as s:
        proc = s.scalar(select(db.Processo).where(
            db.Processo.agendamento_id == agendamento_id))
        if not proc:
            raise HTTPException(404, "Processo não encontrado.")
        nf = proc.nf or ""
        ativos = s.scalar(select(func.count(db.Ativo.id)).where(
            db.Ativo.processo_id == proc.id)) or 0
        s.delete(proc)
    _registrar_exclusao(sd, req, agendamento_id, nf, ativos)
    return None


def _registrar_exclusao(sd: dict, req: Request, agendamento_id: int,
                        nf: str, ativos: int) -> None:
    """Grava no log de acesso do PORTAL (não no banco deste módulo, que acabou
    de perder a linha). Falha aqui nunca desfaz a exclusão já efetivada."""
    try:
        from db.portal import AccessLog, SessionLocal as PortalSession
        with PortalSession.begin() as s:
            s.add(AccessLog(
                login=sd.get("username", ""),
                auth_source=sd.get("auth_source", "LOCAL"),
                success=True,
                ip=client_ip(req),
                detail=(f"Internalização do agendamento {agendamento_id} "
                        f"(NF {nf}) excluída — {ativos} ativo(s)")[:500],
            ))
    except Exception as exc:  # noqa: BLE001
        _log.warning("Exclusão da internalização %s não foi registrada: %s",
                     agendamento_id, exc)


# ── Exportação Excel (idêntica ao modelo) ──────────────────────────────────
def _montar_xlsx(nf: str, linhas: list[dict]) -> bytes:
    """Monta a planilha no MESMO desenho do modelo: aba 'Placa Patrimonial',
    faixa vermelha (C00000) com texto branco, cabeçalho NF | Descrição do item
    | Plaqueta | Número de série | Item, dados a partir da linha 7."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Placa Patrimonial"
    ws.sheet_view.showGridLines = False

    vermelho = PatternFill("solid", fgColor="C00000")
    branco_bold = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
    corpo = Font(name="Calibri", size=11)
    centro = Alignment(horizontal="center", vertical="center")
    fina = Side(style="thin")
    borda = Border(left=fina, right=fina, top=fina, bottom=fina)

    # larguras (iguais ao modelo)
    for col, larg in {"A": 7.57, "B": 123.29, "C": 14.14,
                      "D": 22.14, "E": 8.14, "F": 13.0}.items():
        ws.column_dimensions[col].width = larg

    # faixa título (A5:D5 mesclada)
    ws.merge_cells("A5:D5")
    c = ws["A5"]
    c.value = "Placa Patrimonial (N° do bem)"
    c.fill = vermelho
    c.font = branco_bold
    c.alignment = centro
    for col in "ABCD":
        cc = ws[f"{col}5"]
        cc.fill = vermelho
        cc.border = borda
    ws.row_dimensions[5].height = 15.75

    # cabeçalho (linha 6)
    cabec = ["NF", "Descrição do item", "Plaqueta", "Número de série", "Item"]
    for i, titulo in enumerate(cabec):
        cell = ws.cell(row=6, column=i + 1, value=titulo)
        cell.fill = vermelho
        cell.font = branco_bold
        cell.alignment = centro
        cell.border = borda
    ws.row_dimensions[6].height = 15.75

    # dados (a partir da linha 7)
    r = 7
    for it in linhas:
        valores = [
            int(nf) if str(nf).isdigit() else nf,
            it.get("descricao", ""),
            it.get("plaqueta", ""),
            it.get("numero_serie", ""),
            int(it["ebs_item"]) if str(it.get("ebs_item", "")).isdigit()
            else it.get("ebs_item", ""),
        ]
        for i, v in enumerate(valores):
            cell = ws.cell(row=r, column=i + 1, value=v)
            cell.font = corpo
            cell.alignment = centro
            cell.border = borda
        ws.row_dimensions[r].height = 12.75
        r += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ══════════════════════════════════════════════════════════════════════════
#  Depois do lançamento: Patrimônio e Entrada de Equipamento
#
#  O lançamento (acima) diz QUAL ativo é cada serial. Daí em diante o
#  equipamento percorre duas etapas, uma tela cada:
#
#  1. Patrimônio — espera o serial aparecer no EBS. Renner e Camicado têm o
#     patrimônio criado lá, então a consulta responde sozinha; Youcom compra
#     por fora, não há o que consultar, e alguém confirma no botão.
#  2. Entrada de Equipamento — o técnico de gestão de ativos informa o
#     espaço e corredor, sobe o ativo no ServiceNow e o equipamento entra no
#     estoque do portal.
#
#  A etapa é de CADA equipamento, não da remessa: numa nota com dez
#  desktops, se sete aparecerem no EBS e três não, os sete seguem.
# ══════════════════════════════════════════════════════════════════════════


def _linha_ativo(a, proc) -> dict:
    """O equipamento com o contexto da remessa que a tela precisa mostrar."""
    d = a.to_dict()
    d.update({
        "processo_id": proc.id,
        "agendamento_id": proc.agendamento_id,
        "bu": proc.bu or "",
        "fornecedor": proc.fornecedor or "",
        "nf": proc.nf or "",
        "estoque_destino": proc.estoque_destino or "",
        "estoque_destino_rotulo": proc.estoque_destino_rotulo or "",
        "data_recebimento": (proc.data_recebimento.isoformat()
                             if proc.data_recebimento else ""),
        # Youcom não tem consulta ao EBS: a tela mostra o botão em vez de
        # "aguardando", que seria uma espera que nunca termina.
        "tem_ebs": (proc.bu or "") in db.BUS_COM_EBS,
    })
    return d


def _ativos_na_etapa(s, etapa: str) -> list[dict]:
    from sqlalchemy import select
    saida = []
    for proc in s.scalars(select(db.Processo)).all():
        if proc.status != "CONCLUIDA":
            continue   # ainda em lançamento: não entrou no fluxo
        for a in proc.ativos:
            if (a.etapa or db.ETAPA_PATRIMONIO) == etapa:
                saida.append(_linha_ativo(a, proc))
    saida.sort(key=lambda x: (x.get("data_recebimento") or "", x["id"]))
    return saida


@router.get("/fluxo/patrimonio")
def patrimonio_listar(req: Request):
    """Equipamentos esperando o patrimônio aparecer no EBS."""
    _exigir(req, "view")
    db.ensure_db()
    with db.SessionLocal() as s:
        itens = _ativos_na_etapa(s, db.ETAPA_PATRIMONIO)
    return {"itens": itens, "total": len(itens),
            "aguardando_ebs": sum(1 for x in itens if x["tem_ebs"]),
            "aguardando_confirmacao": sum(1 for x in itens if not x["tem_ebs"])}


@router.post("/fluxo/patrimonio/consultar")
def patrimonio_consultar(req: Request):
    """Procura no EBS o serial de cada equipamento que ainda espera.

    Roda no botão, nunca sozinha: automação que dispara por conta própria
    não tem quem responda por ela quando falha, e o portal não tem agendador.

    Quem não é Renner nem Camicado fica de fora — não há patrimônio no EBS
    para encontrar, e insistir só geraria consulta inútil e ruído no log.
    """
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    db.ensure_db()

    with db.SessionLocal() as s:
        pendentes = [x for x in _ativos_na_etapa(s, db.ETAPA_PATRIMONIO)
                     if x["tem_ebs"] and x["numero_serie"]]
    if not pendentes:
        return {"ok": True, "consultados": 0, "encontrados": 0, "ids": [],
                "aviso": "Nenhum equipamento de Renner ou Camicado esperando "
                         "com número de série preenchido."}

    try:
        from integracoes import ebs_oracle
    except ImportError as exc:
        raise HTTPException(
            503, f"O driver Oracle não está instalado neste servidor: {exc}") from exc

    achados, erros = {}, []
    for item in pendentes:
        try:
            linhas = ebs_oracle.run_named(
                "ativo_por_serial", {"numero_serie": item["numero_serie"]}, max_rows=5)
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ == "EbsOracleSemCredencial":
                raise HTTPException(503, str(exc)) from exc
            # Um serial que falha não pode derrubar a varredura inteira: os
            # outros seguem, e o erro é contado para a tela dizer quantos.
            _log.warning("consulta do serial %s falhou: %s", item["numero_serie"], exc)
            erros.append(item["numero_serie"])
            continue
        if linhas:
            achados[item["id"]] = linhas[0]

    if not achados:
        return {"ok": True, "consultados": len(pendentes), "encontrados": 0,
                "erros": len(erros), "ids": [],
                "aviso": "Nenhum dos seriais consultados está no EBS ainda."}

    from sqlalchemy import select
    agora = db.utcnow()
    with db.SessionLocal.begin() as s:
        for ativo_id, linha in achados.items():
            a = s.get(db.Ativo, ativo_id)
            if not a or a.etapa != db.ETAPA_PATRIMONIO:
                continue   # alguém mexeu entre a consulta e a gravação
            a.ebs_ativo = str(linha.get("ativo") or "")[:60]
            a.ebs_descricao = str(linha.get("descricao") or "")[:200]
            a.ebs_encontrado_em = agora
            # A plaqueta do EBS é a oficial: se o lançamento ficou sem ela,
            # é aqui que ela chega.
            if not a.plaqueta and linha.get("plaqueta"):
                a.plaqueta = str(linha["plaqueta"])[:60]
            a.etapa = db.ETAPA_ENTRADA
    _log.info("patrimônio: %s de %s seriais achados no EBS por=%s ip=%s",
              len(achados), len(pendentes), sd.get("username", ""), client_ip(req))
    return {"ok": True, "consultados": len(pendentes), "encontrados": len(achados),
            "erros": len(erros), "ids": sorted(achados)}


class ConfirmarIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: list[int] = []


@router.post("/fluxo/patrimonio/confirmar")
def patrimonio_confirmar(body: ConfirmarIn, req: Request):
    """Confirma à mão a entrada do ativo (BU sem patrimônio no EBS)."""
    sd = _exigir(req, "edit")
    db.ensure_db()
    if not body.ids:
        raise HTTPException(422, "Escolha ao menos um equipamento.")
    usuario = sd.get("username", "")
    agora = db.utcnow()
    confirmados, recusados = [], []
    with db.SessionLocal.begin() as s:
        for ativo_id in body.ids[:500]:
            a = s.get(db.Ativo, ativo_id)
            if not a or a.etapa != db.ETAPA_PATRIMONIO:
                recusados.append(ativo_id)
                continue
            # Renner e Camicado têm patrimônio no EBS: confirmar à mão aqui
            # seria pular a conferência que existe justamente para pegar
            # serial trocado. Quem tem EBS espera o EBS.
            if (a.processo.bu or "") in db.BUS_COM_EBS:
                recusados.append(ativo_id)
                continue
            a.confirmado_por = usuario
            a.confirmado_em = agora
            a.etapa = db.ETAPA_ENTRADA
            confirmados.append(ativo_id)
    if not confirmados:
        raise HTTPException(
            422, "Nada a confirmar: os equipamentos escolhidos já saíram desta "
                 "etapa, ou são de BU que tem patrimônio no EBS e precisa da "
                 "consulta em vez da confirmação manual.")
    _log.info("patrimônio confirmado à mão ids=%s por=%s ip=%s",
              confirmados, usuario, client_ip(req))
    return {"ok": True, "confirmados": confirmados, "recusados": recusados}


@router.get("/fluxo/entrada")
def entrada_listar(req: Request):
    """Equipamentos com patrimônio resolvido, esperando o técnico."""
    _exigir(req, "view")
    db.ensure_db()
    with db.SessionLocal() as s:
        itens = _ativos_na_etapa(s, db.ETAPA_ENTRADA)
    return {"itens": itens, "total": len(itens)}


class EntradaIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ids: list[int] = []
    espaco_corredor: str = ""

    @field_validator("espaco_corredor")
    @classmethod
    def _espaco(cls, v: str) -> str:
        v = " ".join((v or "").split())[:60]
        if not v:
            raise ValueError("Informe o espaço e corredor onde o equipamento fica.")
        return v


@router.post("/fluxo/entrada")
def entrada_concluir(body: EntradaIn, req: Request):
    """Sobe o equipamento no ServiceNow e o coloca no estoque do portal.

    É a última etapa do fluxo, e a que faz "entrou em estoque" significar
    alguma coisa: além de marcar no ServiceNow, cria o ativo e o ciclo de
    recebimento no banco do portal. Sem isso o equipamento ficaria só
    registrado como concluído aqui, sem aparecer na Consulta nem poder ser
    separado — e a tela mentiria sobre onde ele está.

    O espaço e corredor é obrigatório pelo mesmo motivo que já é no
    Recebimento: sem ele o ServiceNow recebe o ativo sem lugar, e depois
    ninguém acha o equipamento na prateleira.

    A escrita no ServiceNow sai no nome de quem está operando — nunca numa
    conta de serviço.
    """
    sd = _exigir(req, "edit")
    check_rate_limit(req, "api")
    db.ensure_db()
    if not body.ids:
        raise HTTPException(422, "Escolha ao menos um equipamento.")

    usuario = sd.get("username", "")
    # 1. Junta o que vai subir, conferindo a etapa de cada um.
    with db.SessionLocal() as s:
        alvos, recusados = [], []
        for ativo_id in body.ids[:200]:
            a = s.get(db.Ativo, ativo_id)
            if not a or a.etapa != db.ETAPA_ENTRADA:
                recusados.append(ativo_id)
                continue
            alvos.append(_linha_ativo(a, a.processo))
    if not alvos:
        raise HTTPException(
            422, "Nada a concluir: os equipamentos escolhidos não estão na "
                 "etapa de entrada.")

    # 2. Estoque do portal. Vem ANTES do ServiceNow porque é o que o portal
    #    controla: se o ServiceNow estiver fora, o equipamento ainda entra no
    #    estoque e a tela diz que a marcação lá ficou pendente — o contrário
    #    deixaria o equipamento fisicamente na prateleira e invisível aqui.
    from datetime import date as _date
    from sqlalchemy import func, select as _select
    from db.portal import (SessionLocal as PortalSession, Asset, ReceiptCycle,
                           Movement)
    from routers.helpers import upsert_asset

    criados = []
    with PortalSession.begin() as ps:
        for item in alvos:
            ativo = upsert_asset(ps, {
                "empresa": item.get("bu") or "",
                "ativo": item.get("ebs_ativo") or item.get("plaqueta") or "",
                "etiqueta": item.get("plaqueta") or "",
                "numero_serie": item.get("numero_serie") or "",
                "descricao": item.get("ebs_descricao") or item.get("descricao") or "",
                "fonte": "INTERNALIZACAO",
            })
            ps.flush()
            n = (ps.scalar(_select(func.max(ReceiptCycle.cycle_number))
                           .where(ReceiptCycle.asset_id == ativo.id)) or 0) + 1
            hoje = _date.today()
            iso = hoje.isocalendar()
            ciclo = ReceiptCycle(
                asset_id=ativo.id, cycle_number=n, received_date=hoje,
                iso_week=f"{iso.year}-S{iso.week:02d}", status="RECEBIDO",
                origem_entrada="FORNECEDOR", po="", nf=item.get("nf") or "",
                lot_number="", open=False,
                note=f"Internalização do agendamento {item.get('agendamento_id')}",
                created_by=usuario, updated_by=usuario)
            ps.add(ciclo)
            ps.flush()
            ps.add(Movement(asset_id=ativo.id, cycle_id=ciclo.id,
                            new_status="RECEBIDO", origin="INTERNALIZACAO",
                            note=f"Espaço e corredor: {body.espaco_corredor}",
                            username=usuario))
            criados.append({"ativo_id": item["id"], "asset_id": ativo.id})

    # 3. ServiceNow. Falha aqui NÃO desfaz o estoque: fica registrado como
    #    pendente e a pessoa repete a marcação depois.
    itens_sn = [{
        "etiqueta": x.get("plaqueta") or "",
        "numero_serie": x.get("numero_serie") or "",
        "descricao": x.get("ebs_descricao") or x.get("descricao") or "",
        "ativo": x.get("ebs_ativo") or "",
    } for x in alvos]
    resumo_sn = {"ativo": False, "falhas": []}
    try:
        from routers.recebimento import _marcar_no_servicenow
        resumo_sn = _marcar_no_servicenow(itens_sn, req, body.espaco_corredor)
    except Exception as exc:  # noqa: BLE001
        resumo_sn = {"ativo": True, "falhas": [str(exc)]}
        _log.error("ServiceNow: %s equipamento(s) entraram sem marcação: %s",
                   len(itens_sn), exc)

    # 4. Fecha a etapa no fluxo.
    agora = db.utcnow()
    por_ativo = {c["ativo_id"]: c["asset_id"] for c in criados}
    with db.SessionLocal.begin() as s:
        for ativo_id, asset_id in por_ativo.items():
            a = s.get(db.Ativo, ativo_id)
            if not a or a.etapa != db.ETAPA_ENTRADA:
                continue
            a.espaco_corredor = body.espaco_corredor
            a.entrada_por = usuario
            a.entrada_em = agora
            a.asset_id = asset_id
            a.etapa = db.ETAPA_CONCLUIDO

    _log.info("entrada concluída ids=%s espaco=%r por=%s ip=%s",
              sorted(por_ativo), body.espaco_corredor, usuario, client_ip(req))
    return {"ok": True, "concluidos": sorted(por_ativo), "recusados": recusados,
            "servicenow": resumo_sn,
            "aviso": ("O ServiceNow não confirmou a marcação; o equipamento já "
                      "está no estoque do portal. Repita a marcação depois.")
                     if resumo_sn.get("falhas") else ""}
