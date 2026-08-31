"""Correios API — autenticação e rastreio de objetos."""
from __future__ import annotations

import base64
import os
import time

from fastapi import APIRouter, Request, HTTPException

from database import SessionLocal, Setting
from security import require_permission

router = APIRouter(prefix="/api/servicenow", tags=["Correios"])

SN_PROXY = os.environ.get("SN_PROXY", "http://10.115.35.45:8888")

CORREIOS_URLS = {
    "producao": {
        "token": "https://api.correios.com.br/token/v1/autentica",
        "rastro": "https://api.correios.com.br/srorastro/v1/objetos",
    },
    "homologacao": {
        "token": "https://apihom.correios.com.br/token/v1/autentica",
        "rastro": "https://apihom.correios.com.br/srorastro/v1/objetos",
    },
}

_correios_token_cache: dict = {}


def _get_correios_config():
    with SessionLocal() as s:
        row = s.get(Setting, "correios")
        if not row or not row.value:
            raise HTTPException(400, "API dos Correios não configurada. Vá em Parâmetros > Correios API.")
        cfg = row.value
        if not cfg.get("usuario") or not cfg.get("chave_acesso") or not cfg.get("contrato"):
            raise HTTPException(400, "Configuração dos Correios incompleta (usuário, chave de acesso e contrato são obrigatórios).")
        return cfg


def _correios_authenticate(cfg: dict) -> str:
    cached = _correios_token_cache.get("token")
    cached_at = _correios_token_cache.get("obtained_at", 0)
    if cached and (time.time() - cached_at) < 21600:
        return cached

    import requests as _req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    credentials = base64.b64encode(
        f"{cfg['usuario']}:{cfg['chave_acesso']}".encode()
    ).decode()

    sess = _req.Session()
    sess.verify = False
    if SN_PROXY:
        sess.proxies = {"https": SN_PROXY, "http": SN_PROXY}

    try:
        r = sess.post(
            "https://api.correios.com.br/token/v1/autentica",
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
    except Exception as e:
        raise HTTPException(502, f"Erro ao conectar com Correios: {e}")
    finally:
        sess.close()

    if r.status_code not in (200, 201):
        detail = r.text[:300] if r.text else str(r.status_code)
        raise HTTPException(502, f"Correios retornou {r.status_code}: {detail}")

    data = r.json()
    token = data.get("token")
    if not token:
        raise HTTPException(502, "Resposta do Correios sem token.")

    _correios_token_cache["token"] = token
    _correios_token_cache["obtained_at"] = time.time()
    return token


def _correios_get(cfg, token, url):
    import requests as _req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    sess = _req.Session()
    sess.verify = False
    if SN_PROXY:
        sess.proxies = {"https": SN_PROXY, "http": SN_PROXY}

    try:
        r = sess.get(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }, timeout=30)

        if r.status_code == 401:
            _correios_token_cache.clear()
            token = _correios_authenticate(cfg)
            r = sess.get(url, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }, timeout=30)

        return r
    except Exception as e:
        raise HTTPException(502, f"Erro ao consultar Correios: {e}")
    finally:
        sess.close()


@router.get("/correios/rastrear/{codigo}")
def correios_rastrear(codigo: str, req: Request):
    """Rastreia um objeto pelos Correios usando o código de rastreamento."""
    require_permission(req, "servicenow", "view")

    codigo = codigo.strip().upper()
    if not codigo or len(codigo) < 10:
        raise HTTPException(400, "Código de rastreamento inválido.")

    cfg = _get_correios_config()
    token = _correios_authenticate(cfg)

    ambiente = cfg.get("ambiente", "producao")
    urls = CORREIOS_URLS.get(ambiente, CORREIOS_URLS["producao"])

    url = f"{urls['rastro']}?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(cfg, token, url)

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
            dest = ev.get("unidadeDestino", {})
            dest_end = dest.get("endereco", {}) if dest else {}
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

    cfg = _get_correios_config()
    token = _correios_authenticate(cfg)

    ambiente = cfg.get("ambiente", "producao")
    urls = CORREIOS_URLS.get(ambiente, CORREIOS_URLS["producao"])

    url = f"{urls['rastro']}?codigosObjetos={codigo}&resultado=T"
    r = _correios_get(cfg, token, url)

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
    """Testa conexão com Correios: autenticação + rastreio de teste."""
    require_permission(req, "servicenow", "view")
    cfg = _get_correios_config()
    _correios_token_cache.clear()

    import requests as _req
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    sess = _req.Session()
    sess.verify = False
    if SN_PROXY:
        sess.proxies = {"https": SN_PROXY, "http": SN_PROXY}

    credentials = base64.b64encode(
        f"{cfg['usuario']}:{cfg['chave_acesso']}".encode()
    ).decode()

    result = {"config": {"usuario": cfg["usuario"], "contrato": cfg.get("contrato", ""), "proxy": SN_PROXY or "(nenhum)"}}

    try:
        r = sess.post(
            "https://api.correios.com.br/token/v1/autentica",
            headers={"Authorization": f"Basic {credentials}", "Content-Type": "application/json"},
            timeout=30,
        )
        result["auth"] = {
            "status": r.status_code,
            "url": r.url,
            "response": r.text[:500],
        }
        if r.status_code not in (200, 201):
            sess.close()
            return result

        token = r.json().get("token", "")
        _correios_token_cache["token"] = token
        _correios_token_cache["obtained_at"] = time.time()

        r2 = sess.get(
            "https://api.correios.com.br/srorastro/v1/objetos?codigosObjetos=AD852897611BR&resultado=T",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        result["rastreio_test"] = {
            "status": r2.status_code,
            "url": r2.url,
            "response": r2.text[:500],
        }
    except Exception as e:
        result["erro"] = str(e)
    finally:
        sess.close()

    result["ok"] = result.get("auth", {}).get("status") in (200, 201) and result.get("rastreio_test", {}).get("status") == 200
    if result["ok"]:
        result["message"] = "Autenticação e rastreio OK!"
    return result
