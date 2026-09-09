"""Correios API — autenticação e rastreio de objetos."""
from __future__ import annotations

import base64
import os
import time

from fastapi import APIRouter, Request, HTTPException

from core.security import require_permission

router = APIRouter(prefix="/api/servicenow", tags=["Correios"])

# Proxy de saída: VAZIO por padrão. O servidor novo sai direto para a rede;
# só preencha SN_PROXY onde a saída exigir proxy. Um proxy fixo aqui fazia
# toda chamada aos Correios tentar um endereço que não existe no destino.
SN_PROXY = os.environ.get("SN_PROXY", "")


# ── O que é, de fato, uma entrega ───────────────────────────────────────
# BDE/BDI/BDR é a família "baixa de entrega" do SRO, e o código sozinho NÃO
# quer dizer que o objeto chegou ao destinatário: quem diz isso é o TIPO.
# Só o tipo 01 é "Objeto entregue ao destinatário". O mesmo BDE com tipo 23
# é "Objeto ainda não chegou à unidade" e com tipo 02 é devolução ao
# remetente — ambos estavam sendo lidos como entrega, o que pintava o objeto
# de "Entregue" na tela e fazia a automação encerrar chamado indevidamente.
CODIGOS_BAIXA = ("BDE", "BDI", "BDR")
TIPOS_ENTREGA = {
    t.strip().zfill(2)
    for t in os.environ.get("CORREIOS_TIPOS_ENTREGA", "01").split(",")
    if t.strip()
}


def _tipo_do_evento(ev: dict) -> str:
    """Tipo do evento normalizado em dois dígitos ('1' e 1 viram '01')."""
    tipo = ev.get("tipo", "")
    tipo = "" if tipo is None else str(tipo).strip()
    return tipo.zfill(2) if tipo else ""


def evento_de_entrega(ev: dict) -> bool:
    """True só quando o evento é entrega ao destinatário de verdade."""
    if str(ev.get("codigo", "") or "").strip().upper() not in CODIGOS_BAIXA:
        return False
    tipo = _tipo_do_evento(ev)
    if tipo:
        return tipo in TIPOS_ENTREGA
    # Evento antigo sem tipo: aceita só quando a própria descrição afirma a
    # entrega ao destinatário — na dúvida, não é entrega.
    desc = str(ev.get("descricao", "") or "").lower()
    return "entregue ao destinat" in desc


def _secret(nome: str, default: str = "") -> str:
    """Segredo pelo caminho único do projeto (`core.cofre`): cofre
    corporativo, cofre local cifrado e, por último, variável de ambiente.

    As credenciais dos Correios NÃO devem ficar em variável de ambiente nem
    em arquivo de configuração — só no cofre, que apenas a aplicação lê.
    """
    from core.cofre import obter
    return obter(nome, default)


def _correios_creds():
    """Retorna as credenciais dos Correios no momento do uso (não guarda em
    global), buscando do cofre a cada chamada de autenticação."""
    # Chaves do cofre corporativo (loader oficial: `s(chave)`):
    #     s('CORREIOS_USUARIO')
    #     s('CORREIOS_CHAVE')
    #     s('CORREIOS_CARTOES').split(',')
    usuario = _secret("CORREIOS_USUARIO")
    chave = _secret("CORREIOS_CHAVE")
    cartoes = [c.strip() for c in _secret("CORREIOS_CARTOES", "").split(",") if c.strip()]
    dr = _secret("CORREIOS_DR", "64")
    contrato = _secret("CORREIOS_CONTRATO", "")
    return usuario, chave, cartoes, dr, contrato


CORREIOS_BASE = "https://api.correios.com.br"

_correios_token_cache: dict = {}


def _correios_session(proxy=SN_PROXY):
    import requests as _req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    sess = _req.Session()
    sess.verify = False
    if proxy:
        sess.proxies = {"https": proxy, "http": proxy}
    return sess


def _correios_request(method: str, url: str, **kwargs):
    """Faz request aos Correios tentando o proxy e, se falhar, conexão direta."""
    tentativas = [SN_PROXY, None] if SN_PROXY else [None]
    erros = []
    for proxy in tentativas:
        sess = _correios_session(proxy)
        try:
            return sess.request(method, url, timeout=30, **kwargs)
        except Exception as e:
            origem = f"proxy {proxy}" if proxy else "conexão direta"
            erros.append(f"{origem}: {type(e).__name__}: {e}")
        finally:
            sess.close()
    raise HTTPException(502, "Erro ao conectar com Correios — " + " | ".join(erros))


def _check_credenciais():
    usuario, chave, *_ = _correios_creds()
    if not usuario or not chave:
        raise HTTPException(
            500,
            "Credenciais dos Correios ausentes. Elas vêm do cofre corporativo "
            "(chaves CORREIOS_USUARIO, CORREIOS_CHAVE, "
            "CORREIOS_CARTOES). Confira com: python3 scripts/cofre.py conferir",
        )


def _correios_authenticate() -> str:
    """Autentica com Correios em duas etapas: token básico + cartão de postagem."""
    usuario, chave, cartoes, dr, contrato = _correios_creds()
    if not usuario or not chave:
        _check_credenciais()

    cached = _correios_token_cache.get("token")
    cached_at = _correios_token_cache.get("obtained_at", 0)
    if cached and (time.time() - cached_at) < 3600:
        return cached

    # Etapa 1: autenticação básica
    credentials = base64.b64encode(
        f"{usuario}:{chave}".encode()
    ).decode()

    r = _correios_request(
        "POST",
        f"{CORREIOS_BASE}/token/v1/autentica",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
    )

    if r.status_code not in (200, 201):
        detail = r.text[:300] if r.text else str(r.status_code)
        raise HTTPException(502, f"Correios auth falhou ({r.status_code}): {detail}")

    token_basico = r.json().get("token", "")
    if not token_basico:
        raise HTTPException(502, "Resposta do Correios sem token.")

    # Etapa 2: eleva o token via cartão de postagem ou contrato. É esse
    # token elevado que carrega o escopo do SRO Rastro; o token básico
    # sozinho é recusado pelo gateway com GTW-012.
    tentativas = []

    escopos = [
        ("cartaopostagem", {"numero": c}, f"cartão {c}")
        for c in cartoes
    ]
    if contrato:
        escopos.append(
            ("contrato",
             {"numero": contrato, "dr": dr},
             f"contrato {contrato}/DR {dr}")
        )

    for caminho, corpo, rotulo in escopos:
        try:
            # A elevação usa Basic com usuário/senha, não o Bearer do token
            # básico: o gateway responde GTW-014 pedindo Basic explicitamente.
            r_esc = _correios_request(
                "POST",
                f"{CORREIOS_BASE}/token/v1/autentica/{caminho}",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                json=corpo,
            )
        except HTTPException as e:
            tentativas.append(f"{rotulo}: {e.detail}")
            continue

        if r_esc.status_code in (200, 201):
            token_esc = r_esc.json().get("token", "")
            if token_esc:
                _correios_token_cache["token"] = token_esc
                _correios_token_cache["obtained_at"] = time.time()
                _correios_token_cache["escopo"] = rotulo
                _correios_token_cache["tentativas"] = tentativas
                return token_esc
            tentativas.append(f"{rotulo}: HTTP {r_esc.status_code} sem token no corpo")
        else:
            tentativas.append(f"{rotulo}: HTTP {r_esc.status_code} {r_esc.text[:150]}")

    # Nenhum escopo funcionou. Guardamos o diagnóstico e devolvemos o token
    # básico mesmo assim, para que o erro do gateway venha acompanhado do
    # motivo real em vez de um 403 solto.
    _correios_token_cache["token"] = token_basico
    _correios_token_cache["obtained_at"] = time.time()
    _correios_token_cache["escopo"] = None
    _correios_token_cache["tentativas"] = tentativas
    return token_basico


def _correios_get(token, url):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    r = _correios_request("GET", url, headers=headers)

    if r.status_code in (401, 403):
        _correios_token_cache.clear()
        token = _correios_authenticate()
        headers["Authorization"] = f"Bearer {token}"
        r = _correios_request("GET", url, headers=headers)

    return r


@router.get("/correios/rastrear/{codigo}")
def correios_rastrear(codigo: str, req: Request):
    """Rastreia um objeto pelos Correios usando o código de rastreamento."""
    require_permission(req, "rastreio", "view")
    return consultar_rastreio(codigo)


def consultar_rastreio(codigo: str) -> dict:
    """Consulta o rastreio de um objeto (puro, sem permissão/Request).

    Reutilizável por outras rotinas (ex.: encerramento automático de
    chamados). Retorna o mesmo dicionário do endpoint /rastrear.
    """
    codigo = (codigo or "").strip().upper()
    if not codigo or len(codigo) < 10:
        raise HTTPException(400, "Código de rastreamento inválido.")

    token = _correios_authenticate()
    url = f"{CORREIOS_BASE}/srorastro/v1/objetos?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(token, url)

    if r.status_code != 200:
        detail = r.text[:300] if r.text else str(r.status_code)
        escopo = _correios_token_cache.get("escopo")
        if not escopo:
            falhas = _correios_token_cache.get("tentativas") or []
            detail += (
                " | Token sem escopo: nenhum cartão/contrato foi aceito, "
                "então a consulta usou o token básico. Tentativas: "
                + ("; ".join(falhas) if falhas else "(nenhuma registrada)")
            )
        else:
            detail += f" | Token elevado via {escopo}"
        raise HTTPException(502, f"Correios retornou {r.status_code}: {detail}")

    data = r.json()
    objetos = data.get("objetos", [])
    if not objetos:
        return {"codigo": codigo, "encontrado": False, "eventos": []}

    obj = objetos[0]
    eventos = []
    entrega = None

    tipo_postal = obj.get("tipoPostal", {})

    for ev in obj.get("eventos", []):
        unidade = ev.get("unidade", {})
        endereco = unidade.get("endereco", {})
        local_parts = [
            endereco.get("cidade", ""),
            endereco.get("uf", ""),
        ]
        local_str = " - ".join(p for p in local_parts if p)
        if unidade.get("nome"):
            local_str = f"{unidade['nome']} ({local_str})" if local_str else unidade["nome"]

        ev_code = ev.get("codigo", "")
        evento_data = {
            "data": ev.get("dtHrCriado", ""),
            "descricao": ev.get("descricao", ""),
            "detalhe": ev.get("detalhe", ""),
            "local": local_str,
            "codigo": ev_code,
            "tipo": ev.get("tipo", ""),
            "unidade": unidade.get("nome", ""),
            "municipio": endereco.get("cidade", ""),
            "uf": endereco.get("uf", ""),
        }

        if evento_de_entrega(ev) and not entrega:
            recebedor = ev.get("recebedor", {})
            recebedor = recebedor or {}
            entrega = {
                "entregue": True,
                "data": ev.get("dtHrCriado", ""),
                "descricao": ev.get("descricao", ""),
                "detalhe": ev.get("detalhe", ""),
                "local_entrega": local_str,
                "recebedor_nome": recebedor.get("nome", ""),
                "recebedor_documento": recebedor.get("documento", ""),
                "recebedor_celular": recebedor.get("celular", ""),
                "recebedor_email": recebedor.get("email", ""),
                "recebedor_comentario": recebedor.get("comentario", "")
                or ev.get("comentario", ""),
            }

        eventos.append(evento_data)

    return {
        "codigo": codigo,
        "encontrado": True,
        "tipo": tipo_postal.get("sigla", ""),
        "tipo_nome": tipo_postal.get("nome", ""),
        "tipo_categoria": tipo_postal.get("categoria", ""),
        "eventos": eventos,
        "entrega": entrega,
    }


@router.get("/correios/comprovante/{codigo}")
def correios_comprovante(codigo: str, req: Request):
    """Comprovante de entrega a partir do evento de entrega do SRO.

    O SRO Rastro não devolve a imagem digitalizada do AR — ela é um
    produto à parte dos Correios. O que existe aqui são os dados do
    recebimento (nome, documento, data, local), que é o que o evento
    BDE/BDI/BDR carrega; quando o objeto traz imagens, elas são
    repassadas em `imagens`.
    """
    require_permission(req, "rastreio", "view")

    codigo = codigo.strip().upper()
    if not codigo or len(codigo) < 10:
        raise HTTPException(400, "Código de rastreamento inválido.")

    token = _correios_authenticate()
    url = f"{CORREIOS_BASE}/srorastro/v1/objetos?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(token, url)

    if r.status_code == 404:
        return {"codigo": codigo, "encontrado": False, "mensagem": "Objeto não encontrado."}
    if r.status_code != 200:
        detail = r.text[:300] if r.text else str(r.status_code)
        raise HTTPException(502, f"Correios retornou {r.status_code}: {detail}")

    objetos = r.json().get("objetos", [])
    if not objetos:
        return {"codigo": codigo, "encontrado": False, "mensagem": "Objeto não encontrado."}

    obj = objetos[0]
    for ev in obj.get("eventos", []):
        if not evento_de_entrega(ev):
            continue

        recebedor = ev.get("recebedor") or {}
        unidade = ev.get("unidade", {})
        endereco = unidade.get("endereco", {})
        local = " - ".join(
            p for p in (endereco.get("cidade", ""), endereco.get("uf", "")) if p
        )
        if unidade.get("nome"):
            local = f"{unidade['nome']} ({local})" if local else unidade["nome"]

        return {
            "codigo": codigo,
            "encontrado": True,
            "comprovante": {
                "nome": recebedor.get("nome", ""),
                "documento": recebedor.get("documento", ""),
                "celular": recebedor.get("celular", ""),
                "email": recebedor.get("email", ""),
                "comentario": recebedor.get("comentario", "") or ev.get("comentario", ""),
                "dataRecebimento": ev.get("dtHrCriado", ""),
                "descricao": ev.get("descricao", ""),
                "detalhe": ev.get("detalhe", ""),
                "local": local,
                "imagens": ev.get("imagens", []),
            },
        }

    return {
        "codigo": codigo,
        "encontrado": False,
        "mensagem": "Objeto ainda não foi entregue — não há comprovante.",
    }


@router.post("/correios/rastrear-lote")
def correios_rastrear_lote(body: dict, req: Request):
    """Rastreia múltiplos objetos de uma vez (máximo 20)."""
    require_permission(req, "rastreio", "view")

    codigos = body.get("codigos", [])
    if not codigos:
        raise HTTPException(400, "Informe ao menos um código.")

    resultados = {}
    for cod in codigos:
        cod = str(cod).strip().upper()
        if not cod or len(cod) < 10:
            resultados[cod] = {"encontrado": False, "erro": "Código inválido"}
            continue
        try:
            r = correios_rastrear(cod, req)
            resultados[cod] = r
        except HTTPException as e:
            resultados[cod] = {"encontrado": False, "erro": e.detail}
        except Exception as e:
            resultados[cod] = {"encontrado": False, "erro": str(e)[:200]}

    return {"resultados": resultados}


@router.post("/correios/test")
def correios_test(req: Request):
    """Testa conexão com Correios mostrando detalhes de cada etapa."""
    require_permission(req, "rastreio", "view")
    _check_credenciais()
    _correios_token_cache.clear()

    usuario, chave, cartoes, _dr, _contrato = _correios_creds()
    credentials = base64.b64encode(
        f"{usuario}:{chave}".encode()
    ).decode()

    result = {
        "config": {
            "usuario": usuario,
            "cartoes": cartoes,
            "proxy": SN_PROXY or "(direto)",
        }
    }

    try:
        # 1) Auth básica
        r = _correios_request(
            "POST",
            f"{CORREIOS_BASE}/token/v1/autentica",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
        )
        result["1_auth_basica"] = {
            "status": r.status_code,
            "response": r.text[:600],
        }
        if r.status_code not in (200, 201):
            return result

        auth_data = r.json()
        token_basico = auth_data.get("token", "")
        result["1_auth_basica"]["cartao_retornado"] = auth_data.get("cartaoPostagem", "(nenhum)")

        # O payload do token diz o perfil e quais APIs o usuário pode chamar.
        # É a forma de saber se existe direito a AR digitalizado sem depender
        # de tentativa e erro em endpoints adivinhados.
        result["1_auth_basica"]["perfil"] = {
            k: v for k, v in auth_data.items() if k != "token"
        }
        try:
            import json as _json
            miolo = token_basico.split(".")[1]
            miolo += "=" * (-len(miolo) % 4)
            result["1_auth_basica"]["escopos_do_token"] = _json.loads(
                base64.urlsafe_b64decode(miolo).decode("utf-8", "replace")
            )
        except Exception as e:
            result["1_auth_basica"]["escopos_do_token"] = f"(não decodificado: {e})"

        # 2) Auth com cada cartão de postagem
        token_cp = ""
        cartao_ok = ""
        for cartao in cartoes:
            r_cp = _correios_request(
                "POST",
                f"{CORREIOS_BASE}/token/v1/autentica/cartaopostagem",
                headers={
                    "Authorization": f"Basic {credentials}",
                    "Content-Type": "application/json",
                },
                json={"numero": cartao},
            )
            result[f"2_cartao_{cartao}"] = {
                "status": r_cp.status_code,
                "response": r_cp.text[:300],
            }
            if r_cp.status_code in (200, 201) and not token_cp:
                token_cp = r_cp.json().get("token", "")
                cartao_ok = cartao

        # 3) Rastreio com token básico
        rastro_url = f"{CORREIOS_BASE}/srorastro/v1/objetos?codigosObjetos=AD852897611BR&resultado=T"

        r2 = _correios_request(
            "GET",
            rastro_url,
            headers={"Authorization": f"Bearer {token_basico}", "Accept": "application/json"},
        )
        result["3_rastreio_token_basico"] = {
            "status": r2.status_code,
            "response": r2.text[:500],
        }

        # 4) Rastreio com token do cartão
        if token_cp:
            r3 = _correios_request(
                "GET",
                rastro_url,
                headers={"Authorization": f"Bearer {token_cp}", "Accept": "application/json"},
            )
            result["4_rastreio_token_cartao"] = {
                "status": r3.status_code,
                "cartao_usado": cartao_ok,
                "response": r3.text[:500],
            }

        ok_basico = result["3_rastreio_token_basico"]["status"] == 200
        ok_cartao = result.get("4_rastreio_token_cartao", {}).get("status") == 200
        result["ok"] = ok_basico or ok_cartao
        if ok_cartao:
            result["message"] = f"Rastreio OK com cartão {cartao_ok}!"
        elif ok_basico:
            result["message"] = "Rastreio OK com token básico!"
        else:
            result["message"] = "Auth OK mas rastreio falhou. Verifique permissões dos cartões."

    except HTTPException as e:
        result["erro"] = str(e.detail)
    except Exception as e:
        result["erro"] = f"{type(e).__name__}: {e}"

    return result
