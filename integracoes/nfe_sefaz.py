"""Cliente do NFeDistribuicaoDFe (Ambiente Nacional da NF-e) com certificado A1."""
from __future__ import annotations

import base64
import gzip
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from config import get_settings
from core.nf_pdf import extrair_de_xml, _campos_da_chave
from integracoes import http as http_saida

_cfg = get_settings()
_log = logging.getLogger("nfe_sefaz")

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
NS_WSDL = "http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"
NS_SOAP12 = "http://www.w3.org/2003/05/soap-envelope"
VERSAO_DIST = "1.01"
OPERACAO = "nfeDistDFeInteresse"

URLS = {
    "producao": "https://www1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
    "homologacao": "https://hom1.nfe.fazenda.gov.br/NFeDistribuicaoDFe/NFeDistribuicaoDFe.asmx",
}
CABECALHOS = {
    "Content-Type": f'application/soap+xml; charset=utf-8; action="{NS_WSDL}/{OPERACAO}"',
}

CSTAT_LOCALIZADO = "138"
CSTAT_NENHUM = "137"
CSTAT_CONSUMO_INDEVIDO = "656"

OID_CNPJ_ICP = "2.16.76.1.3.3"

_BUS_COM_CERTIFICADO = ("RENNER", "CAMICADO")

class NfeSemCertificado(RuntimeError):
    pass

class ChaveInvalida(ValueError):
    pass

class SefazIndisponivel(RuntimeError):
    pass

class SemBibliotecaDanfe(RuntimeError):
    pass

class SemBibliotecaMtls(RuntimeError):
    pass

@dataclass
class Certificado:
    bu: str
    caminho: str
    cnpj: str
    valido_ate: datetime
    sujeito: str

    @property
    def vencido(self) -> bool:
        return self.valido_ate <= datetime.now(timezone.utc)

@dataclass
class ResultadoNfe:
    chave: str
    ok: bool
    completa: bool
    xml: bytes | None
    cstat: str
    xmotivo: str
    erro: str

    @property
    def resumo(self) -> bool:
        return self.xml is not None and not self.completa

def _dv_modulo11(digitos43: str) -> int:
    peso, soma = 2, 0
    for c in reversed(digitos43):
        soma += int(c) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    return 0 if resto < 2 else 11 - resto

def validar_chave(chave: str) -> str:
    limpa = re.sub(r"[\s.\-]", "", str(chave or ""))
    if not limpa:
        raise ChaveInvalida("Informe a chave de acesso da NF-e (44 dígitos).")
    if not re.fullmatch(r"\d{44}", limpa):
        raise ChaveInvalida(
            f"A chave de acesso tem 44 dígitos numéricos; veio '{limpa[:50]}' "
            f"({len(limpa)} caracteres).")
    if _dv_modulo11(limpa[:43]) != int(limpa[43]):
        raise ChaveInvalida(
            "O dígito verificador da chave não confere — algum dígito foi "
            "digitado errado.")
    return limpa

def _normalizar_bu(bu: str) -> str:
    return re.sub(r"[^A-Z]", "", str(bu or "").upper())

def _config_da_bu(bu: str) -> tuple[str, str] | None:
    nome = _normalizar_bu(bu)
    if nome.startswith("RENNER"):
        return _cfg.NFE_CERT_RENNER_PFX, _cfg.NFE_CERT_RENNER_SENHA
    if nome.startswith("CAMICADO"):
        return _cfg.NFE_CERT_CAMICADO_PFX, _cfg.NFE_CERT_CAMICADO_SENHA
    return None

def _conteudo_der(der: bytes) -> str:
    if len(der) < 2:
        return ""
    n, ini = der[1], 2
    if n & 0x80:
        qtd = n & 0x7F
        n = int.from_bytes(der[2:2 + qtd], "big")
        ini = 2 + qtd
    return der[ini:ini + n].decode("latin-1", "ignore")

def _cnpj_do_certificado(cert) -> str:
    from cryptography import x509
    from cryptography.x509.oid import NameOID

    cn = ""
    for atributo in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
        cn = str(atributo.value)
        break
    m = re.search(r":\s*(\d{14})\s*$", cn)
    if m:
        return m.group(1)
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for outro in san.get_values_for_type(x509.OtherName):
            if outro.type_id.dotted_string == OID_CNPJ_ICP:
                digitos = re.sub(r"\D", "", _conteudo_der(outro.value))
                if len(digitos) == 14:
                    return digitos
    except x509.ExtensionNotFound:
        pass
    m = re.search(r"(?<!\d)(\d{14})(?!\d)", cn)
    return m.group(1) if m else ""

def _validade(cert) -> datetime:
    try:
        return cert.not_valid_after_utc
    except AttributeError:
        return cert.not_valid_after.replace(tzinfo=timezone.utc)

def _carregar(bu: str) -> tuple[Certificado, bytes, str] | None:
    from cryptography.hazmat.primitives.serialization import pkcs12

    conf = _config_da_bu(bu)
    if conf is None:
        return None
    caminho, senha = conf
    if not caminho:
        return None
    try:
        with open(caminho, "rb") as f:
            dados = f.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise NfeSemCertificado(
            f"O certificado da BU {bu} não pôde ser lido em {caminho}: "
            f"{exc.strerror or exc}. Veja docs/NFE_CERTIFICADO.md.") from exc
    try:
        _chave, cert, _cadeia = pkcs12.load_key_and_certificates(
            dados, senha.encode("utf-8") if senha else None)
    except ValueError as exc:
        raise NfeSemCertificado(
            f"O certificado da BU {bu} não abriu: senha incorreta ou arquivo "
            f"que não é um .pfx ({caminho}).") from exc
    if cert is None:
        raise NfeSemCertificado(
            f"O arquivo {caminho} não contém certificado (só chave?).")
    return (
        Certificado(bu=bu, caminho=caminho, cnpj=_cnpj_do_certificado(cert),
                    valido_ate=_validade(cert), sujeito=cert.subject.rfc4514_string()),
        dados, senha,
    )

def certificado_da_bu(bu: str) -> Certificado | None:
    carregado = _carregar(bu)
    return carregado[0] if carregado else None

def url_do_ambiente(ambiente: str | None = None) -> str:
    amb = (ambiente if ambiente is not None else _cfg.NFE_AMBIENTE).strip().lower()
    if amb not in URLS:
        raise ValueError(
            f"NFE_AMBIENTE='{amb}' não é válido: use producao ou homologacao.")
    return URLS[amb]

def montar_envelope(chave: str, cnpj: str, ambiente: str | None = None) -> bytes:
    amb = (ambiente if ambiente is not None else _cfg.NFE_AMBIENTE).strip().lower()
    url_do_ambiente(amb)
    tp_amb = "1" if amb == "producao" else "2"
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<soap12:Envelope xmlns:soap12="{NS_SOAP12}">'
        '<soap12:Body>'
        f'<{OPERACAO} xmlns="{NS_WSDL}">'
        '<nfeDadosMsg>'
        f'<distDFeInt xmlns="{NS_NFE}" versao="{VERSAO_DIST}">'
        f'<tpAmb>{tp_amb}</tpAmb>'
        f'<CNPJ>{cnpj}</CNPJ>'
        f'<consChNFe><chNFe>{chave}</chNFe></consChNFe>'
        '</distDFeInt>'
        '</nfeDadosMsg>'
        f'</{OPERACAO}>'
        '</soap12:Body>'
        '</soap12:Envelope>'
    ).encode("utf-8")

def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def _achar(elem, nome: str):
    if elem is None:
        return None
    for f in elem.iter():
        if _ln(f.tag) == nome:
            return f
    return None

def _txt(elem, nome: str, default: str = "") -> str:
    f = _achar(elem, nome)
    return (f.text or "").strip() if f is not None and f.text else default

def _descompactar(doc_zip: str) -> bytes:
    try:
        return gzip.decompress(base64.b64decode((doc_zip or "").strip()))
    except (ValueError, OSError, EOFError) as exc:
        raise SefazIndisponivel(
            f"A SEFAZ devolveu um docZip que não abre ({exc}).") from exc

def _texto_do_fault(raiz) -> str:
    fault = _achar(raiz, "Fault")
    if fault is None:
        return ""
    return _txt(fault, "Text") or _txt(fault, "faultstring") or _txt(fault, "Reason")

def interpretar_resposta(chave: str, corpo: bytes) -> ResultadoNfe:
    try:
        raiz = ET.fromstring(corpo)
    except ET.ParseError as exc:
        raise SefazIndisponivel(
            "A resposta da SEFAZ não é XML — costuma ser página de erro do "
            "proxy ou do balanceador.") from exc
    ret = _achar(raiz, "retDistDFeInt")
    if ret is None:
        fault = _texto_do_fault(raiz)
        raise SefazIndisponivel(
            "A SEFAZ respondeu uma falha SOAP: " + fault if fault
            else "A resposta da SEFAZ veio sem retDistDFeInt.")
    cstat = _txt(ret, "cStat")
    xmotivo = _txt(ret, "xMotivo")
    r = ResultadoNfe(chave=chave, ok=False, completa=False, xml=None,
                     cstat=cstat, xmotivo=xmotivo, erro="")
    if cstat == CSTAT_LOCALIZADO:
        docs = [(el.get("schema", ""), el.text or "")
                for el in ret.iter() if _ln(el.tag) == "docZip"]
        proc = next((t for s, t in docs if s.lower().startswith("procnfe")), None)
        res = next((t for s, t in docs if s.lower().startswith("resnfe")), None)
        if proc:
            r.xml, r.ok, r.completa = _descompactar(proc), True, True
        elif res:
            r.xml = _descompactar(res)
            r.erro = (
                "A SEFAZ localizou a nota, mas só entregou o resumo: a NF-e "
                "completa (com os itens) só é distribuída ao destinatário depois "
                "da Ciência da Operação, feita pelo fiscal. Peça a ciência e "
                "consulte de novo, ou informe os itens a partir do DANFE.")
        else:
            r.erro = ("A SEFAZ localizou o documento, mas o lote não trouxe a "
                      f"NF-e (schemas: {', '.join(s for s, _ in docs) or 'nenhum'}).")
    elif cstat == CSTAT_NENHUM:
        r.erro = (
            "Nenhum documento localizado para esta chave (cStat 137). Confira "
            "a chave e o destinatário: a SEFAZ só distribui a NF-e ao CNPJ "
            "destinatário (ou à mesma raiz de CNPJ do certificado).")
    elif cstat == CSTAT_CONSUMO_INDEVIDO:
        r.erro = (
            "Consumo indevido (cStat 656): a SEFAZ bloqueou as consultas deste "
            "CNPJ. Espere 1 hora antes de tentar de novo.")
    else:
        r.erro = f"A SEFAZ recusou a consulta (cStat {cstat or '?'}: {xmotivo or 'sem motivo'})."
    return r

def _sessao_mtls(dados_pfx: bytes, senha: str) -> requests.Session:
    try:
        from requests_pkcs12 import Pkcs12Adapter
    except ImportError as exc:
        raise SemBibliotecaMtls(
            "A biblioteca requests-pkcs12 não está instalada no servidor "
            "(pip install requests-pkcs12).") from exc
    s = http_saida.sessao("sefaz", proxy=_cfg.NFE_PROXY or None)
    s.mount("https://", Pkcs12Adapter(pkcs12_data=dados_pfx, pkcs12_password=senha or None))
    return s

def buscar_por_chave(chave: str, bu: str) -> ResultadoNfe:
    chave = validar_chave(chave)
    carregado = _carregar(bu)
    if carregado is None:
        raise NfeSemCertificado(
            f"A BU {bu} não tem certificado A1 configurado; a NF entra por "
            "arquivo (XML ou PDF).")
    cert, dados, senha = carregado
    if cert.vencido:
        raise NfeSemCertificado(
            f"O certificado da BU {bu} venceu em "
            f"{cert.valido_ate.astimezone().strftime('%d/%m/%Y')}; substitua o "
            "arquivo (docs/NFE_CERTIFICADO.md).")
    if not cert.cnpj:
        raise NfeSemCertificado(
            f"Não achei o CNPJ no certificado da BU {bu} ({cert.sujeito}); "
            "ele precisa ser um e-CNPJ.")

    envelope = montar_envelope(chave, cert.cnpj)
    url = url_do_ambiente()
    inicio = time.monotonic()
    s = _sessao_mtls(dados, senha)
    try:
        resp = s.post(url, data=envelope, headers=CABECALHOS, timeout=_cfg.NFE_TIMEOUT)
    except requests.RequestException as exc:
        raise SefazIndisponivel(
            f"Sem resposta da SEFAZ ({exc.__class__.__name__}): "
            f"{str(exc)[:200]}") from exc
    finally:
        s.close()
    if resp.status_code != 200:
        detalhe = ""
        try:
            detalhe = _texto_do_fault(ET.fromstring(resp.content))
        except ET.ParseError:
            pass
        raise SefazIndisponivel(
            f"A SEFAZ respondeu HTTP {resp.status_code}"
            + (f": {detalhe}" if detalhe else "") + ".")
    resultado = interpretar_resposta(chave, resp.content)
    _log.info("SEFAZ %s chave %s: cStat %s (%s) %s em %.1fs",
              bu, chave, resultado.cstat, resultado.xmotivo,
              "completa" if resultado.completa else ("resumo" if resultado.resumo else "sem xml"),
              time.monotonic() - inicio)
    return resultado

def danfe_pdf(xml: bytes) -> bytes:
    try:
        from brazilfiscalreport.danfe import Danfe
    except ImportError as exc:
        raise SemBibliotecaDanfe(
            "A biblioteca brazilfiscalreport não está instalada no servidor "
            "(pip install brazilfiscalreport).") from exc
    dados = xml if isinstance(xml, bytes) else str(xml).encode("utf-8")
    if _achar(ET.fromstring(dados), "infNFe") is None:
        raise ValueError("O DANFE exige a NF-e completa; este XML é só o resumo.")
    return bytes(Danfe(xml=dados).output())

def _data(texto: str) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", (texto or "").strip())
    return m.group(1) if m else ""

def _valor(texto: str) -> float | None:
    try:
        return round(float((texto or "").replace(",", ".")), 2)
    except ValueError:
        return None

def campos(xml: bytes) -> dict:
    raiz = ET.fromstring(xml)
    if _achar(raiz, "infNFe") is not None:
        base = extrair_de_xml(xml)
        ide = _achar(raiz, "ide")
        emit = _achar(raiz, "emit")
        dest = _achar(raiz, "dest")
        tot = _achar(raiz, "ICMSTot")
        cobr = _achar(raiz, "cobr")
        base.update({
            "vencimento": _txt(cobr, "dVenc") if cobr is not None else "",
            "dest_cnpj": _txt(dest, "CNPJ") if dest is not None else "",
            "emit_nome": _txt(emit, "xNome") if emit is not None else "",
            "emit_cnpj": (_txt(emit, "CNPJ") if emit is not None else "") or base.get("emitente_cnpj", ""),
            "valor_total": _valor(_txt(tot, "vNF")) if tot is not None else None,
            "data_emissao": _data(_txt(ide, "dhEmi") or _txt(ide, "dEmi")) if ide is not None else "",
        })
        return base

    chave = re.sub(r"\D", "", _txt(raiz, "chNFe"))[:44]
    base = {
        "formato": "resumo", "chave": chave, "nf": "", "serie": "",
        "emitente_cnpj": _txt(raiz, "CNPJ"), "po": "", "itens": [],
        "confiavel": bool(chave),
    }
    base.update(_campos_da_chave(chave))
    base.update({
        "vencimento": "",
        "dest_cnpj": "",
        "emit_nome": _txt(raiz, "xNome"),
        "emit_cnpj": _txt(raiz, "CNPJ"),
        "valor_total": _valor(_txt(raiz, "vNF")),
        "data_emissao": _data(_txt(raiz, "dhEmi")),
    })
    return base
