"""Quanto tempo o chamado ficou numa fila — pelo histórico, não por um campo.

O portal já tinha uma medida de atendimento: `SN_TMA_START_FIELD`
(`u_data_bouncing`), um campo que marca quando o chamado entrou. Um campo só
responde "entrou quando?", não "ficou quanto tempo?", e erra inteiro no caso
que mais acontece: o chamado que vai para a fila, sai, e VOLTA. O campo
guarda uma data; a segunda passagem sobrescreve a primeira ou não aparece.

Aqui a conta sai do histórico de mudanças do `assignment_group`: cada
mudança é um par (valor antigo → valor novo) com hora. Com a sequência
inteira dá para montar os intervalos e somar os que pertencem à fila
procurada — quantas vezes forem.

FONTES POSSÍVEIS, em ordem de preferência
-----------------------------------------
1.  `metric_instance` — se a instalação tem a *Metric Definition* de
    "Assignment group" ligada, o próprio ServiceNow já calculou a duração de
    cada passagem. É a fonte mais barata e a mais confiável, porque é o
    número que o próprio ServiceNow usa.
2.  `sys_audit` — o log de auditoria de campo: `fieldname=assignment_group`,
    com `oldvalue`, `newvalue` e `sys_created_on`. Existe sempre que a
    auditoria do campo estiver ligada, e é o que a aba "Histórico" do
    formulário mostra.
3.  Nada disso. Aí não se inventa número: a resposta é "não sei", que é
    diferente de zero.

Qual existe NESTA instalação não se adivinha — `/tempo-fila/fontes`
pergunta. Zero e "não medi" são coisas diferentes e a tela separa as duas:
um chamado com 0 h de SPARE porque nunca passou por lá e um chamado sem
histórico guardado não podem virar a mesma linha na planilha.

O QUE TORNA O NÚMERO ERRADO
---------------------------
*   **O fim do último intervalo.** Um chamado fechado há dois anos, cuja
    última fila foi SPARE, continuaria "na fila" até hoje se o intervalo
    final fosse fechado em `agora`. Ele fecha em `resolved_at` — a data em
    que o chamado foi RESOLVIDO —, e só cai para `closed_at` quando não há
    resolução (o cancelado é o caso comum). No ServiceNow o encerramento é
    automático dias depois da resolução, e medir até ele infla o tempo de
    fila de todo chamado resolvido dentro dela.
*   **O começo.** Antes da primeira mudança o chamado esteve na fila que é o
    `oldvalue` dessa primeira mudança — não na fila atual.
*   **Ida e volta.** Somar só a primeira passagem subestima o atendimento de
    quem recebeu o chamado de volta.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.security import require_permission
from routers.sn_consulta import (
    LOTE_NUMEROS, MODULO, TABELAS, _blocos, _get, _valor_plano,
)

router = APIRouter(prefix="/api/sn-consulta/tempo-fila", tags=["ServiceNow - Consulta"])
_log = logging.getLogger("sn_tempo_fila")

# O campo cuja troca marca entrada e saída de fila.
CAMPO_FILA = "assignment_group"
# Formato dos carimbos do ServiceNow com `display_value=false`: sempre UTC,
# sempre assim. Com display_value=true viria no fuso e no formato do usuário
# da integração, que muda o resultado sem avisar.
_FMT = "%Y-%m-%d %H:%M:%S"
# `documentkey` é o sys_id do registro auditado.
_RE_SYS_ID = re.compile(r"^[0-9a-f]{32}$")


def _quando(bruto) -> datetime | None:
    texto = _valor_plano(bruto).strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:19], _FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        _log.debug("carimbo fora do formato esperado: %r", texto)
        return None


# ── O cálculo ──────────────────────────────────────────────────────────
def intervalos_da_fila(eventos: list[dict], fila: str, abertura: datetime | None,
                       fim: datetime | None, agora: datetime | None = None) -> dict:
    """Os intervalos em que o chamado esteve numa fila cujo nome contém `fila`.

    `eventos`: [{quando, de, para}] — as trocas de fila, em qualquer ordem.
    `fim`: quando o chamado encerrou; `None` se ainda está aberto.

    Função pura de propósito: é aqui que mora o erro de medição, e assim ela
    se confere com casos escritos à mão, sem ServiceNow nenhum.
    """
    agora = agora or datetime.now(timezone.utc)
    alvo = (fila or "").strip().upper()
    ordenados = sorted([e for e in eventos if e.get("quando")], key=lambda e: e["quando"])

    # Fecha no encerramento, não em `agora`: um chamado encerrado há dois anos
    # cuja última fila foi SPARE mostraria dois anos de fila.
    limite = fim or agora

    # Antes da primeira troca o chamado esteve na fila de ONDE ele saiu, não
    # na fila atual. Sem histórico nenhum não há trecho inicial a montar: a
    # sequência é vazia e quem decide é o chamador.
    trechos: list[tuple[str, datetime, datetime]] = []
    if ordenados:
        inicio = abertura or ordenados[0]["quando"]
        trechos.append((ordenados[0].get("de") or "", inicio, ordenados[0]["quando"]))
        for atual, seguinte in zip(ordenados, ordenados[1:]):
            trechos.append((atual.get("para") or "", atual["quando"], seguinte["quando"]))
        trechos.append((ordenados[-1].get("para") or "", ordenados[-1]["quando"], limite))

    segundos, passagens = 0.0, []
    for grupo, comeco, termino in trechos:
        if alvo not in (grupo or "").upper():
            continue
        # Um carimbo depois do outro por desencontro de relógio não pode virar
        # tempo negativo e reduzir o total.
        duracao = max(0.0, (min(termino, limite) - comeco).total_seconds())
        segundos += duracao
        passagens.append({"de": comeco.isoformat(), "ate": min(termino, limite).isoformat(),
                          "segundos": round(duracao)})
    return {
        "segundos": round(segundos),
        "horas": round(segundos / 3600, 2),
        "passagens": len(passagens),
        "detalhe": passagens,
        "trechos": len(trechos),
    }


def _humano(segundos: float) -> str:
    """'3d 04:12' — planilha lê número, gente lê isto."""
    d = timedelta(seconds=int(segundos))
    dias, resto = d.days, d.seconds
    return (f"{dias}d " if dias else "") + f"{resto // 3600:02d}:{(resto % 3600) // 60:02d}"


# ── Onde estão as trocas de fila ───────────────────────────────────────
def _audit_por_chamado(sys_ids: list[str], tabela: str) -> dict[str, list[dict]]:
    """As trocas de `assignment_group`, do `sys_audit`, por sys_id.

    O `documentkey` é o sys_id do registro. A consulta vai em blocos pelo
    mesmo motivo da lista de chamados: a query viaja na URL.
    """
    saida: dict[str, list[dict]] = {}
    # O que a query gasta ANTES dos ids. Sem descontar isto o bloco estoura a
    # URL: foi daqui que veio um 414 na exportação — 250 `sys_id` de 32
    # caracteres dão 8,3 KB, contra 2,7 KB dos mesmos 250 números de chamado.
    # `sys_audit.tablename` guarda a tabela REAL do registro: `incident`,
    # `sc_req_item`. Filtrar por `task` — a tabela-mãe — não casa com nenhuma
    # linha, e o resultado não seria erro: seria zero para todo mundo. Quando
    # os chamados vêm misturados (de `task` ou de `task_sla`), o filtro por
    # tabela sai e sobra o `documentkey`, que já é um sys_id único.
    filtro_tabela = f"tablename={tabela}^" if tabela and tabela != "task" else ""
    fixo = len(f"{filtro_tabela}fieldname={CAMPO_FILA}"
               "^documentkeyIN^ORDERBYsys_created_on") + 40
    for bloco in _blocos(sys_ids, LOTE_NUMEROS, reservado=fixo):
        dados = _get("/api/now/table/sys_audit", {
            "sysparm_query": (f"{filtro_tabela}fieldname={CAMPO_FILA}"
                              "^documentkeyIN" + ",".join(bloco)
                              + "^ORDERBYsys_created_on"),
            "sysparm_fields": "documentkey,oldvalue,newvalue,sys_created_on",
            # UTC e formato fixo. Com display_value=true o carimbo sai no fuso
            # do usuário da integração e a conta muda sem ninguém mexer nela.
            "sysparm_display_value": "false",
            "sysparm_exclude_reference_link": "true",
            "sysparm_limit": "10000",
        }, timeout=90)
        for linha in dados.get("result") or []:
            chave = _valor_plano(linha.get("documentkey"))
            saida.setdefault(chave, []).append({
                "quando": _quando(linha.get("sys_created_on")),
                "de": _valor_plano(linha.get("oldvalue")),
                "para": _valor_plano(linha.get("newvalue")),
            })
    return saida


_cache_grupos: dict[str, str] = {}


def _nomes_dos_grupos(ids: list[str]) -> dict[str, str]:
    """sys_id de fila → nome. Sem isto, procurar "SPARE" não acha nada.

    O `sys_audit` guarda o valor do campo como o ServiceNow o guardou. Para
    uma referência isso pode ser o NOME do grupo ou o SYS_ID dele, conforme a
    versão e a configuração da instância. Quando é sys_id, comparar com
    "SPARE" nunca casa — e o resultado não é erro: é **zero para todo mundo**,
    que foi exatamente o que apareceu.

    Então: o que parecer sys_id é traduzido antes de comparar. O que já vier
    por nome passa direto.
    """
    faltando = [i for i in dict.fromkeys(ids)
                if _RE_SYS_ID.match(i) and i not in _cache_grupos]
    # Mesma conta do sys_audit: são sys_id, e sys_id é longo.
    for bloco in _blocos(faltando, LOTE_NUMEROS, reservado=len("sys_idIN") + 40):
        try:
            dados = _get("/api/now/table/sys_user_group", {
                "sysparm_query": "sys_idIN" + ",".join(bloco),
                "sysparm_fields": "sys_id,name",
                "sysparm_display_value": "false",
                "sysparm_exclude_reference_link": "true",
                "sysparm_limit": str(len(bloco)),
            }, timeout=60)
        except HTTPException as exc:
            # Sem tradução o número sai errado (zero), então isto não pode
            # passar calado: quem chamou decide, mas fica registrado.
            _log.warning("não deu para traduzir sys_id de fila: %s", exc.detail)
            break
        for linha in dados.get("result") or []:
            _cache_grupos[_valor_plano(linha.get("sys_id"))] = _valor_plano(linha.get("name"))
    return {i: _cache_grupos.get(i, i) for i in dict.fromkeys(ids)}


def _traduzir_filas(por_chamado: dict[str, list[dict]]) -> int:
    """Troca sys_id por nome nos eventos. Devolve quantos traduziu."""
    brutos = [v for eventos in por_chamado.values() for e in eventos
              for v in (e.get("de"), e.get("para")) if v]
    sids = [v for v in brutos if _RE_SYS_ID.match(v)]
    if not sids:
        return 0
    mapa = _nomes_dos_grupos(sids)
    trocados = 0
    for eventos in por_chamado.values():
        for e in eventos:
            for lado in ("de", "para"):
                novo = mapa.get(e.get(lado) or "")
                if novo and novo != e.get(lado):
                    e[lado] = novo
                    trocados += 1
    return trocados


@router.get("/fontes")
def fontes(req: Request, tabela: str = "incident"):
    """Qual fonte de histórico esta instalação tem, e se a conta a lê.

    Rode antes de confiar nos números. Sem isto, "0 h de fila" pode ser
    "nunca passou pela fila" ou "a auditoria do campo está desligada" — e as
    duas leituras levam a decisões opostas.
    """
    require_permission(req, MODULO, "view")
    if tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {tabela}")
    achados = []

    def sondar(nome: str, rotulo: str, params: dict, explica: str):
        try:
            dados = _get(f"/api/now/table/{nome}", params, timeout=45)
            linhas = dados.get("result") or []
            achados.append({
                "fonte": nome, "rotulo": rotulo, "acessivel": True,
                "tem_dado": bool(linhas), "amostra": len(linhas), "explica": explica,
                # O valor de exemplo diz se a fila vem por NOME ou por sys_id —
                # procurar "SPARE" num sys_id não acha nada, e o resultado
                # seria "nenhum chamado passou pela fila".
                "exemplo": ({k: _valor_plano(v) for k, v in linhas[0].items()}
                            if linhas else {}),
            })
        except HTTPException as exc:
            achados.append({"fonte": nome, "rotulo": rotulo, "acessivel": False,
                            "tem_dado": False, "erro": exc.detail, "explica": explica})

    sondar("sys_audit", "Auditoria de campo", {
        "sysparm_query": f"tablename={tabela}^fieldname={CAMPO_FILA}^ORDERBYDESCsys_created_on",
        "sysparm_fields": "documentkey,oldvalue,newvalue,sys_created_on",
        "sysparm_display_value": "false", "sysparm_limit": "1",
    }, "As trocas de fila com hora. É desta que o cálculo sai.")
    sondar("metric_instance", "Métrica de duração por fila", {
        "sysparm_query": "definition.field=" + CAMPO_FILA + "^ORDERBYDESCsys_created_on",
        "sysparm_fields": "id,field_value,duration,start,end,calculation_complete",
        "sysparm_display_value": "false", "sysparm_limit": "1",
    }, "Duração já calculada pelo próprio ServiceNow, se a Metric Definition "
       "de assignment group estiver ligada. Mais barata que a auditoria.")

    audit = achados[0]
    pode = audit.get("acessivel") and audit.get("tem_dado")
    return {
        "tabela": tabela,
        "fontes": achados,
        "pode_medir": bool(pode),
        "recado": (
            "A auditoria de assignment_group responde: dá para medir."
            if pode else
            "Sem auditoria de assignment_group acessível, o tempo de fila não "
            "pode ser calculado — e o portal vai dizer 'não medido' em vez de "
            "devolver zero. Peça a leitura de sys_audit para a conta de "
            "serviço, ou a ativação da auditoria do campo."
        ),
    }


class TempoFilaIn(BaseModel):
    tabela: str = "incident"
    # Os chamados vêm por sys_id porque é assim que o sys_audit os endereça
    # (documentkey). A tela já os tem na resposta da consulta.
    chamados: list[dict] = Field(default_factory=list)
    fila: str = "SPARE"


# Estados que contam como CANCELADO. Sai da mesma configuração que os
# Indicadores usam, para as duas telas não discordarem sobre o que é um
# chamado cancelado.
try:
    from config import get_settings as _gs
    _ESTADOS_CANCELADO = {x.strip() for x in
                          (getattr(_gs(), "SN_STATE_CANCELADO", "8") or "8").split(",")
                          if x.strip()}
except Exception:  # noqa: BLE001 — sem config, o valor padrão do ServiceNow
    _ESTADOS_CANCELADO = {"8"}
_RE_CANCELADO = re.compile(r"cancel", re.I)


# Estados em que o chamado ainda corre. Só serve para saber se a falta de
# data de fim é normal (chamado aberto) ou suspeita (chamado terminado sem
# carimbo nenhum).
try:
    _ESTADOS_ABERTO = {x.strip() for x in
                       (getattr(_gs(), "SN_STATE_ABERTO", "1,2,3") or "1,2,3").split(",")
                       if x.strip()}
except Exception:  # noqa: BLE001
    _ESTADOS_ABERTO = {"1", "2", "3"}


def _ativo(estado_cru: str, estado_rotulo: str) -> bool:
    return (estado_cru or "").strip() in _ESTADOS_ABERTO


def _cancelado(estado_cru: str, estado_rotulo: str) -> bool:
    """Cancelado pelo código do estado ou pelo rótulo.

    Pelos dois porque nenhum dos dois basta sozinho: o código 8 é o padrão do
    ServiceNow mas instância customizada muda, e o rótulo depende do idioma
    da conta de serviço. Casar em qualquer um erra menos que casar em um só.
    """
    return (estado_cru or "").strip() in _ESTADOS_CANCELADO or bool(
        _RE_CANCELADO.search(estado_rotulo or ""))


def medir(tabela: str, chamados: list[dict], fila: str) -> dict[str, dict]:
    """Por sys_id: quanto tempo na fila, quantas passagens, e em que base.

    `chamados`: [{sys_id, opened_at, closed_at, resolved_at, fila_atual,
    estado, estado_rotulo}] — tudo vindo da própria consulta, sem ida extra
    ao ServiceNow.

    Três bases de medição, e a coluna diz qual foi usada em cada chamado:

    *   **histórico** — houve troca de fila; os intervalos saem dela.
    *   **sem troca de fila** — o chamado nunca mudou de fila, então esteve a
        vida inteira na fila em que está. Se essa fila é a procurada, o tempo
        é da abertura até o encerramento. Antes isto respondia "não medido",
        o que era conservador demais: um chamado que nasce no SPARE e é
        resolvido no SPARE tem tempo de fila, e ele é a vida toda do chamado.
    *   **cancelado** — o chamado foi cancelado. O tempo é dito assim mesmo,
        mas marcado: cancelado não é atendimento, e misturá-lo na média do
        TMA responde outra pergunta.
    """
    ids = [c["sys_id"] for c in chamados
           if _RE_SYS_ID.match((c.get("sys_id") or "").strip())]
    eventos = _audit_por_chamado(ids, tabela) if ids else {}
    # Antes de comparar com "SPARE": o histórico pode guardar a fila por
    # sys_id, e aí nada casa e tudo dá zero.
    traduzidos = _traduzir_filas(eventos)
    if traduzidos:
        _log.info("traduzidos %d valores de fila de sys_id para nome", traduzidos)

    agora = datetime.now(timezone.utc)
    alvo = (fila or "").strip().upper()
    saida: dict[str, dict] = {}
    for c in chamados:
        sid = (c.get("sys_id") or "").strip()
        estado = _valor_plano(c.get("estado_rotulo")) or _valor_plano(c.get("estado"))
        cancelado = _cancelado(_valor_plano(c.get("estado")), estado)
        abertura = _quando(c.get("opened_at"))
        # RESOLVIDO primeiro, encerrado depois. No ServiceNow o encerramento
        # é automático dias depois da resolução: usar `closed_at` estica o
        # último intervalo por esses dias e infla o tempo de fila de TODO
        # chamado resolvido dentro dela. Estava ao contrário aqui.
        #
        # `closed_at` continua como segunda opção porque há chamado que
        # encerra sem passar por resolvido — o cancelado é o caso comum, e
        # nele `resolved_at` vem vazio.
        fim = _quando(c.get("resolved_at"))
        origem_fim = "resolvido"
        if fim is None:
            fim = _quando(c.get("closed_at"))
            origem_fim = "encerrado" if fim else ""
        do_chamado = eventos.get(sid) or []

        if do_chamado:
            r = intervalos_da_fila(do_chamado, fila, abertura, fim, agora)
            base = "histórico"
        else:
            # Sem troca de fila, o chamado esteve sempre na fila em que está.
            atual = _valor_plano(c.get("fila_atual"))
            if not atual:
                saida[sid] = {"medido": False, "segundos": None, "horas": None,
                              "passagens": None, "legivel": "", "estado": estado,
                              "cancelado": cancelado, "base": "não medido",
                              "fila_atual": "",
                              "motivo": "sem troca de fila e sem a fila atual do chamado"}
                continue
            if alvo not in atual.upper():
                r = {"segundos": 0, "horas": 0.0, "passagens": 0}
            else:
                fechamento = fim or agora
                segundos = max(0.0, (fechamento - abertura).total_seconds()) if abertura else 0.0
                r = {"segundos": round(segundos), "horas": round(segundos / 3600, 2),
                     "passagens": 1}
            base = "sem troca de fila"

        # Chamado que não está mais ativo e sem data nenhuma de fim: o último
        # intervalo vai até AGORA, e isso infla o número sem avisar. É raro,
        # mas quando acontece a linha tem de dizer.
        sem_fim = fim is None and not _ativo(_valor_plano(c.get("estado")), estado)
        saida[sid] = {
            "medido": True, "segundos": r["segundos"], "horas": r["horas"],
            "passagens": r["passagens"], "legivel": _humano(r["segundos"]),
            "estado": estado, "cancelado": cancelado,
            "base": ("sem data de encerramento" if sem_fim
                     else ("cancelado" if cancelado else base)),
            # A data que fechou a conta, e qual campo a deu. É o que permite
            # conferir o número sem abrir o chamado.
            "fim": fim.isoformat() if fim else "",
            "fim_origem": origem_fim,
            "fila_atual": _valor_plano(c.get("fila_atual")),
            "motivo": "",
        }
    return saida


@router.post("")
def calcular(corpo: TempoFilaIn, req: Request):
    """O tempo de fila de uma lista de chamados já consultados."""
    require_permission(req, MODULO, "view")
    if corpo.tabela not in TABELAS:
        raise HTTPException(422, f"Tabela não oferecida: {corpo.tabela}")
    if not (corpo.fila or "").strip():
        raise HTTPException(422, "Informe o nome (ou parte) da fila a medir.")
    if len(corpo.chamados) > 50000:
        raise HTTPException(422, "Lista grande demais; meça em partes.")
    por_id = medir(corpo.tabela, corpo.chamados, corpo.fila)
    medidos = [v for v in por_id.values() if v["medido"]]
    passou = [v for v in medidos if v["segundos"]]
    # Cancelado fora da média do atendimento: o chamado não foi atendido, foi
    # cancelado, e o tempo dele responde outra pergunta. O número continua na
    # linha, para quem quiser somar por conta própria.
    para_media = [v for v in passou if not v["cancelado"]]
    total = sum(v["segundos"] for v in para_media)
    return {
        "fila": corpo.fila,
        "por_sys_id": por_id,
        "pedidos": len(corpo.chamados),
        "medidos": len(medidos),
        "nao_medidos": len(por_id) - len(medidos),
        "cancelados": sum(1 for v in medidos if v["cancelado"]),
        "sem_troca_de_fila": sum(1 for v in medidos if v["base"] == "sem troca de fila"),
        # Chamado que nunca passou pela fila não entra na média: ele arrastaria
        # o número para baixo respondendo outra pergunta.
        "passaram_pela_fila": len(passou),
        "media_horas": (round(total / len(para_media) / 3600, 2)
                        if para_media else None),
        "total_horas": round(total / 3600, 2),
    }
