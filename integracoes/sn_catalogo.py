"""Service Catalog do ServiceNow — abrir um pedido COMO O USUÁRIO LOGADO, com anexos."""
from __future__ import annotations

import json
import re
import unicodedata
from urllib.parse import urlsplit

import requests

TIMEOUT = 30
TIMEOUT_ANEXO = 60

TABELAS_ANEXO = ("sc_req_item", "sc_request")

PAGINAS_COM_TOKEN = ("/nav_to.do?uri=%2Fhome.do", "/esc")

_RE_SYS_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_RE_G_CK = re.compile(r"""g_ck["']?\s*[:=]\s*["']([0-9a-fA-F]{72})["']""")
_RE_LOGIN = re.compile(r"login\.do|/login", re.I)
_RE_EMAIL_LOGIN = re.compile(r"^[A-Za-z0-9._@-]{1,120}$")
_RE_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FRASE_DESCRICAO = ("Prezados, gentileza enviar pra lançamento e cadastro de "
                   "patrimonio a {nf} referente à PO {po}.")

MARCA_POR_BU = (("renner", "Renner Brasil"), ("camicado", "Camicado"), ("youcom", "Youcom"))

COLUNAS_NOTA = ("question_order_number_po", "question_document_number", "question_due_date")
COLUNA_QUE_IDENTIFICA_MRVS = "question_document_number"

_MARCAS_MRVS = {"mrvs", "multi_row", "multi_row_variable_set", "multirow", "multi-row"}

class ErroServiceNow(RuntimeError):

    def __init__(self, status: int, texto: str = ""):
        self.status = int(status)
        self.texto = (texto or "")[:300]
        super().__init__(f"ServiceNow retornou {self.status}: {self.texto}")

class SessaoServiceNowExpirada(ErroServiceNow):

    def __init__(self, texto: str = "sessão expirada — entre de novo no portal (Logon AD)"):
        super().__init__(401, texto)

def sys_id_valido(valor: str, rotulo: str = "sys_id") -> str:
    valor = (valor or "").strip()
    if not _RE_SYS_ID.match(valor):
        raise ValueError(f"{rotulo} inválido: esperado 32 hexadecimais.")
    return valor.lower()

def _chave(texto) -> str:
    t = unicodedata.normalize("NFKD", str(texto if texto is not None else ""))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return " ".join(t.casefold().split())

def _bool(valor) -> bool:
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "sim", "yes")
    return bool(valor)

def _tipo_normalizado(bruto: dict) -> str:
    candidatos = [_chave(bruto.get(k)) for k in ("type", "friendly_type", "set_type", "variable_set_type")]
    if any(c in _MARCAS_MRVS for c in candidatos) or _bool(bruto.get("multi_row")):
        return "mrvs"
    return candidatos[1] or candidatos[0]

def _normalizar_variavel(bruto: dict) -> dict:
    nome = bruto.get("name") or bruto.get("internal_name") or bruto.get("variable_set_name") or ""
    tipo = _tipo_normalizado(bruto)
    escolhas = []
    for c in bruto.get("choices") or []:
        if not isinstance(c, dict):
            continue
        escolhas.append({"label": str(c.get("label") or c.get("text") or c.get("displayValue") or ""),
                         "value": str(c.get("value") if c.get("value") is not None else "")})
    filhos = [_normalizar_variavel(f) for f in (bruto.get("children") or []) if isinstance(f, dict)]
    return {
        "sys_id": str(bruto.get("id") or bruto.get("sys_id") or ""),
        "name": str(nome),
        "label": str(bruto.get("label") or bruto.get("displayName") or bruto.get("question_text") or nome),
        "type": tipo,
        "mandatory": _bool(bruto.get("mandatory")),
        "read_only": _bool(bruto.get("read_only")),
        "choices": escolhas,
        "children": filhos,
    }

def indice_variaveis(item_descrito: dict) -> dict:
    indice: dict = {}

    def visitar(lista):
        for v in lista or []:
            if v.get("name"):
                indice.setdefault(v["name"], v)
            if v.get("type") != "mrvs":
                visitar(v.get("children"))

    visitar(item_descrito.get("variables"))
    return indice

def resolver_opcao(variavel: dict, rotulo: str) -> str:
    alvo = _chave(rotulo)
    for c in variavel.get("choices") or []:
        if _chave(c.get("label")) == alvo or _chave(c.get("value")) == alvo:
            return c.get("value")
    disponiveis = ", ".join(repr(c.get("label")) for c in variavel.get("choices") or []) or "nenhuma"
    raise KeyError(f"opção '{rotulo}' não existe em '{variavel.get('name')}' (opções: {disponiveis})")

class CatalogoServiceNow:

    def __init__(self, sessao: requests.Session, base_url: str):
        self._sessao = sessao
        self.base_url = (base_url or "").rstrip("/")
        if not self.base_url:
            raise ValueError("base_url do ServiceNow vazia.")
        self._host = urlsplit(self.base_url).netloc.lower()
        self._token: str | None = None

    def _url(self, caminho: str) -> str:
        return self.base_url + caminho

    def _fora_da_sessao(self, r: requests.Response) -> bool:
        final = (r.url or "").lower()
        return (r.status_code == 401 or bool(_RE_LOGIN.search(final))
                or urlsplit(final).netloc != self._host)

    def token(self) -> str:
        if self._token:
            return self._token
        ultimo = None
        for pagina in PAGINAS_COM_TOKEN:
            r = self._sessao.get(self._url(pagina), headers={"Accept": "text/html"},
                                 timeout=TIMEOUT, allow_redirects=True)
            if self._fora_da_sessao(r):
                raise SessaoServiceNowExpirada()
            m = _RE_G_CK.search(r.text or "")
            if m:
                self._token = m.group(1)
                return self._token
            ultimo = r
        status = ultimo.status_code if ultimo is not None else 0
        raise ErroServiceNow(status, "nenhuma página de UI devolveu o g_ck (X-UserToken)")

    def _chamar(self, metodo: str, caminho: str, *, params=None, json_body=None,
                data=None, headers=None, timeout: int = TIMEOUT) -> requests.Response:
        for tentativa in (1, 2):
            renovado = self._token is None
            cab = {"Accept": "application/json", "X-UserToken": self.token(),
                   "X-Requested-With": "XMLHttpRequest"}
            if headers:
                cab.update(headers)
            r = self._sessao.request(metodo, self._url(caminho), params=params, json=json_body,
                                     data=data, headers=cab, timeout=timeout, allow_redirects=True)
            if r.status_code == 401 and not renovado and tentativa == 1:
                self._token = None
                continue
            break
        if self._fora_da_sessao(r):
            raise SessaoServiceNowExpirada()
        if not 200 <= r.status_code < 300:
            raise ErroServiceNow(r.status_code, r.text)
        return r

    @staticmethod
    def _resultado(r: requests.Response):
        try:
            corpo = r.json()
        except ValueError:
            raise ErroServiceNow(r.status_code, "resposta não é JSON: " + (r.text or "")) from None
        if not isinstance(corpo, dict) or "result" not in corpo:
            raise ErroServiceNow(r.status_code, "resposta sem 'result': " + (r.text or ""))
        return corpo["result"]

    def usuario_atual(self, completar: bool = True) -> dict:
        bruto = self._resultado(self._chamar("GET", "/api/now/ui/user/current_user")) or {}
        u = {
            "sys_id": str(bruto.get("user_sys_id") or bruto.get("sys_id") or ""),
            "user_name": str(bruto.get("user_name") or ""),
            "name": str(bruto.get("user_display_name") or bruto.get("name") or ""),
            "email": str(bruto.get("email") or bruto.get("user_email") or ""),
        }
        if completar and u["sys_id"] and not (u["email"] and u["name"]):
            regs = self._sys_user(f"sys_id={sys_id_valido(u['sys_id'], 'sys_id do usuário')}")
            if regs:
                for campo in ("email", "name", "user_name"):
                    u[campo] = u[campo] or str(regs[0].get(campo) or "")
        return u

    def _sys_user(self, consulta: str) -> list:
        r = self._chamar("GET", "/api/now/table/sys_user", params={
            "sysparm_query": consulta,
            "sysparm_fields": "sys_id,name,email,user_name",
            "sysparm_limit": "1",
        })
        res = self._resultado(r)
        return res if isinstance(res, list) else []

    def procurar_usuario(self, email_ou_login: str) -> dict | None:
        valor = (email_ou_login or "").strip()
        if not _RE_EMAIL_LOGIN.match(valor):
            raise ValueError("E-mail/login com caracteres inválidos.")
        regs = self._sys_user(f"email={valor}^ORuser_name={valor}")
        if not regs:
            return None
        u = regs[0]
        return {campo: str(u.get(campo) or "") for campo in ("sys_id", "name", "email", "user_name")}

    def descrever_item(self, sys_id: str) -> dict:
        sid = sys_id_valido(sys_id, "sys_id do item")
        bruto = self._resultado(self._chamar("GET", f"/api/sn_sc/servicecatalog/items/{sid}")) or {}
        variaveis = [_normalizar_variavel(v) for v in (bruto.get("variables") or []) if isinstance(v, dict)]
        return {
            "sys_id": str(bruto.get("sys_id") or sid),
            "name": str(bruto.get("name") or ""),
            "short_description": str(bruto.get("short_description") or ""),
            "variables": variaveis,
        }

    def enviar_pedido(self, sys_id: str, variaveis: dict, quantidade: int = 1) -> dict:
        sid = sys_id_valido(sys_id, "sys_id do item")
        corpo = {"sysparm_quantity": str(int(quantidade)), "variables": {}}
        for nome, valor in (variaveis or {}).items():
            if isinstance(valor, (list, dict)):
                valor = json.dumps(valor, ensure_ascii=False)
            corpo["variables"][str(nome)] = "" if valor is None else str(valor)
        res = self._resultado(self._chamar("POST", f"/api/sn_sc/servicecatalog/items/{sid}/order_now",
                                           json_body=corpo)) or {}
        pedido = {
            "request_sys_id": str(res.get("request_id") or res.get("sys_id") or ""),
            "request_number": str(res.get("request_number") or res.get("number") or ""),
        }
        if not pedido["request_sys_id"]:
            raise ErroServiceNow(200, "order_now sem request_id: " + json.dumps(res)[:200])
        return pedido

    def ritm_do_pedido(self, request_sys_id: str) -> dict | None:
        rid = sys_id_valido(request_sys_id, "sys_id do pedido")
        r = self._chamar("GET", "/api/now/table/sc_req_item", params={
            "sysparm_query": f"request={rid}",
            "sysparm_fields": "sys_id,number",
            "sysparm_limit": "1",
        })
        res = self._resultado(r)
        if not isinstance(res, list) or not res:
            return None
        return {"sys_id": str(res[0].get("sys_id") or ""), "number": str(res[0].get("number") or "")}

    def anexar(self, tabela: str, registro_sys_id: str, nome_arquivo: str,
               conteudo: bytes, tipo_mime: str) -> str:
        if tabela not in TABELAS_ANEXO:
            raise ValueError(f"Anexo só em {', '.join(TABELAS_ANEXO)}.")
        rid = sys_id_valido(registro_sys_id, "sys_id do registro")
        nome = (nome_arquivo or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not nome:
            raise ValueError("Nome do arquivo vazio.")
        if not isinstance(conteudo, (bytes, bytearray)) or not conteudo:
            raise ValueError("Conteúdo do anexo vazio.")
        r = self._chamar("POST", "/api/now/attachment/file",
                         params={"table_name": tabela, "table_sys_id": rid, "file_name": nome},
                         data=bytes(conteudo),
                         headers={"Content-Type": tipo_mime or "application/octet-stream"},
                         timeout=TIMEOUT_ANEXO)
        res = self._resultado(r) or {}
        anexo = str(res.get("sys_id") or "")
        if not anexo:
            raise ErroServiceNow(r.status_code, "anexo sem sys_id: " + (r.text or ""))
        return anexo

def _marca_da_bu(bu: str) -> str:
    chave = _chave(bu)
    for marca, rotulo in MARCA_POR_BU:
        if marca in chave:
            return rotulo
    raise ValueError(f"BU desconhecida para o chamado: {bu!r} (esperado Renner, Camicado ou Youcom).")

def _valor(variavel: dict, candidatos) -> str:
    if isinstance(candidatos, str):
        candidatos = [candidatos]
    if not variavel.get("choices"):
        return str(candidatos[0])
    erro = None
    for rotulo in candidatos:
        try:
            return resolver_opcao(variavel, rotulo)
        except KeyError as exc:
            erro = exc
    raise erro

def _achar_mrvs(item_descrito: dict) -> dict | None:
    for v in indice_variaveis(item_descrito).values():
        if v.get("type") == "mrvs" and any(
                f.get("name") == COLUNA_QUE_IDENTIFICA_MRVS for f in v.get("children") or []):
            return v
    return None

def montar_variaveis_lancamento_nf(item_descrito: dict, dados: dict) -> tuple[dict, list[str]]:
    notas = [n for n in (dados.get("notas") or []) if isinstance(n, dict)]
    if not notas:
        raise ValueError("Nenhuma nota fiscal para o chamado.")
    for n in notas:
        venc = str(n.get("vencimento") or "").strip()
        if venc and not _RE_DATA.match(venc):
            raise ValueError(f"Vencimento da NF {n.get('nf')!r} fora do formato AAAA-MM-DD: {venc!r}")

    indice = indice_variaveis(item_descrito)
    variaveis: dict = {}
    faltantes: list[str] = []

    def por(nome, candidatos):
        v = indice.get(nome)
        if v is None:
            faltantes.append(nome)
            return
        variaveis[nome] = _valor(v, candidatos)

    por("requested_for", str(dados.get("requested_for_sys_id") or ""))
    if "u_phone" in indice:
        if not indice["u_phone"].get("read_only"):
            variaveis["u_phone"] = str(dados.get("telefone") or "")
    else:
        faltantes.append("u_phone")
    por("u_impact_employee", str(dados.get("impact_employee_sys_id") or ""))
    por("question_brand", _marca_da_bu(str(dados.get("bu") or "")))
    por("question_document_type", "Material")
    por("question_demand_type", "Others")
    por("question_has_a_purchase_order", ["Yes", "Sim"])
    por("question_supplier", str(dados.get("fornecedor") or ""))
    por("question_nature_of_the_transaction", "Fixed assets (CAPEX)")
    por("question_the_document_is_being_sent_after_the_deadline", ["No", "Não"])
    por("question_number_of_documents_being_sent", str(len(notas)))

    bloco = _achar_mrvs(item_descrito)
    if bloco is None:
        faltantes.append(f"bloco multilinha (MRVS com {COLUNA_QUE_IDENTIFICA_MRVS})")
    else:
        colunas = {f.get("name") for f in bloco.get("children") or []}
        for col in COLUNAS_NOTA:
            if col not in colunas:
                faltantes.append(f"{bloco['name']}.{col}")
        linhas = []
        for n in notas:
            linha = {
                "question_order_number_po": str(n.get("po") or ""),
                "question_document_number": str(n.get("nf") or ""),
                "question_due_date": str(n.get("vencimento") or ""),
            }
            linhas.append({k: v for k, v in linha.items() if k in colunas})
        variaveis[bloco["name"]] = json.dumps(linhas, ensure_ascii=False)

    frases = [FRASE_DESCRICAO.format(nf=str(n.get("nf") or ""), po=str(n.get("po") or "")) for n in notas]
    por("question_description", " ".join(frases))
    return variaveis, faltantes
