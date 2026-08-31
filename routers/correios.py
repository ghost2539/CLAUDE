"""Correios API — autenticação e rastreio de objetos."""
from __future__ import annotations

import base64
import os
import time

from fastapi import APIRouter, Request, HTTPException

from security import require_permission

router = APIRouter(prefix="/api/servicenow", tags=["Correios"])

SN_PROXY = os.environ.get("SN_PROXY", "http://10.115.35.45:8888")

# Credenciais vêm do .env (fora do controle de versão).
CORREIOS_USUARIO = os.environ.get("CORREIOS_USUARIO", "")
CORREIOS_CHAVE = os.environ.get("CORREIOS_CHAVE", "")
CORREIOS_CARTOES = [
    c.strip() for c in os.environ.get("CORREIOS_CARTOES", "").split(",") if c.strip()
]
CORREIOS_DR = os.environ.get("CORREIOS_DR", "64")

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
    if not CORREIOS_USUARIO or not CORREIOS_CHAVE:
        raise HTTPException(
            500,
            "Credenciais dos Correios ausentes. Defina CORREIOS_USUARIO, "
            "CORREIOS_CHAVE e CORREIOS_CARTOES no arquivo .env do servidor.",
        )


def _correios_authenticate() -> str:
    """Autentica com Correios em duas etapas: token básico + cartão de postagem."""
    _check_credenciais()
    cached = _correios_token_cache.get("token")
    cached_at = _correios_token_cache.get("obtained_at", 0)
    if cached and (time.time() - cached_at) < 3600:
        return cached

    # Etapa 1: autenticação básica
    credentials = base64.b64encode(
        f"{CORREIOS_USUARIO}:{CORREIOS_CHAVE}".encode()
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

    # Etapa 2: autenticação com cartão de postagem
    for cartao in CORREIOS_CARTOES:
        try:
            r_cp = _correios_request(
                "POST",
                f"{CORREIOS_BASE}/token/v1/autentica/cartaopostagem",
                headers={
                    "Authorization": f"Bearer {token_basico}",
                    "Content-Type": "application/json",
                },
                json={"numero": cartao},
            )
        except HTTPException:
            continue
        if r_cp.status_code in (200, 201):
            token_cp = r_cp.json().get("token", "")
            if token_cp:
                _correios_token_cache["token"] = token_cp
                _correios_token_cache["obtained_at"] = time.time()
                _correios_token_cache["cartao"] = cartao
                return token_cp

    # Fallback: usa token básico se nenhum cartão funcionou
    _correios_token_cache["token"] = token_basico
    _correios_token_cache["obtained_at"] = time.time()
    _correios_token_cache["cartao"] = None
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
    require_permission(req, "servicenow", "view")

    codigo = codigo.strip().upper()
    if not codigo or len(codigo) < 10:
        raise HTTPException(400, "Código de rastreamento inválido.")

    token = _correios_authenticate()
    url = f"{CORREIOS_BASE}/srorastro/v1/objetos?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(token, url)

    if r.status_code != 200:
        detail = r.text[:300] if r.text else str(r.status_code)
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

        if ev_code in ("BDE", "BDI", "BDR") and not entrega:
            recebedor = ev.get("recebedor", {})
            entrega = {
                "entregue": True,
                "data": ev.get("dtHrCriado", ""),
                "descricao": ev.get("descricao", ""),
                "detalhe": ev.get("detalhe", ""),
                "local_entrega": local_str,
                "recebedor_nome": recebedor.get("nome", "") if recebedor else "",
                "recebedor_documento": recebedor.get("documento", "") if recebedor else "",
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
    """Busca comprovante de entrega (AR eletrônico) de um objeto."""
    require_permission(req, "servicenow", "view")

    codigo = codigo.strip().upper()
    if not codigo or len(codigo) < 10:
        raise HTTPException(400, "Código de rastreamento inválido.")

    token = _correios_authenticate()
    url = f"{CORREIOS_BASE}/srorastro/v1/objetos?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(token, url)

    if r.status_code == 404:
        return {"codigo": codigo, "encontrado": False, "mensagem": "Comprovante não disponível."}
    if r.status_code != 200:
        detail = r.text[:300] if r.text else str(r.status_code)
        raise HTTPException(502, f"Correios retornou {r.status_code}: {detail}")

    data = r.json()
    return {
        "codigo": codigo,
        "encontrado": True,
        "comprovante": data,
    }


@router.post("/correios/rastrear-lote")
def correios_rastrear_lote(body: dict, req: Request):
    """Rastreia múltiplos objetos de uma vez (máximo 20)."""
    require_permission(req, "servicenow", "view")

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
    require_permission(req, "servicenow", "view")
    _check_credenciais()
    _correios_token_cache.clear()

    credentials = base64.b64encode(
        f"{CORREIOS_USUARIO}:{CORREIOS_CHAVE}".encode()
    ).decode()

    result = {
        "config": {
            "usuario": CORREIOS_USUARIO,
            "cartoes": CORREIOS_CARTOES,
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

        # 2) Auth com cada cartão de postagem
        token_cp = ""
        cartao_ok = ""
        for cartao in CORREIOS_CARTOES:
            r_cp = _correios_request(
                "POST",
                f"{CORREIOS_BASE}/token/v1/autentica/cartaopostagem",
                headers={
                    "Authorization": f"Bearer {token_basico}",
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
