#!/usr/bin/env python3
"""Verificação do cliente NF-e (SEFAZ) sem rede: certificado de teste, envelope, respostas canned, DANFE."""
from __future__ import annotations

import base64
import gzip
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
_TEMP = tempfile.mkdtemp(prefix="nfe-verif-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEMP}/portal.db")
os.environ.setdefault("PORTAL_SESSION_SECRET", "verificacao-local-com-64-caracteres-de-sobra-aqui")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from cryptography.hazmat.primitives.serialization import pkcs12  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def _pfx(cn: str, dias: int, senha: bytes) -> bytes:
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    agora = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - timedelta(days=1))
            .not_valid_after(agora + timedelta(days=dias)).sign(chave, hashes.SHA256()))
    return pkcs12.serialize_key_and_certificates(b"teste", chave, cert, None,
                                                 serialization.BestAvailableEncryption(senha))


RENNER = Path(_TEMP) / "renner.pfx"
CAMICADO = Path(_TEMP) / "camicado.pfx"
RENNER.write_bytes(_pfx("LOJAS RENNER SA:92754738000162", 365, b"segredo"))
CAMICADO.write_bytes(_pfx("CAMICADO:12345678000199", -1, b"outra"))
os.environ["NFE_CERT_RENNER_PFX"] = str(RENNER)
os.environ["NFE_CERT_RENNER_SENHA"] = "segredo"
os.environ["NFE_CERT_CAMICADO_PFX"] = str(CAMICADO)
os.environ["NFE_CERT_CAMICADO_SENHA"] = "outra"
os.environ["NFE_AMBIENTE"] = "homologacao"
os.environ["NFE_PROXY"] = ""
os.environ["VERIFY_SSL"] = "true"

import requests  # noqa: E402
from integracoes import nfe_sefaz  # noqa: E402

falhas: list[str] = []
feitos = 0


def checar(cond, d) -> None:
    global feitos
    feitos += 1
    print(("  ok    " if cond else "  FALHA ") + d)
    if not cond:
        falhas.append(d)


CHAVE = "35250912345678000199550010000020010000002010"
XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<nfeProc xmlns="http://www.portalfiscal.inf.br/nfe" versao="4.00"><NFe><infNFe Id="NFe{CHAVE}" versao="4.00">
<ide><cUF>35</cUF><natOp>VENDA</natOp><mod>55</mod><serie>1</serie><nNF>200153</nNF><dhEmi>2026-09-10T10:00:00-03:00</dhEmi><tpNF>1</tpNF><idDest>2</idDest></ide>
<emit><CNPJ>12345678000199</CNPJ><xNome>ZEBRA DO BRASIL</xNome><enderEmit><xLgr>Rua A</xLgr><nro>1</nro><xBairro>Centro</xBairro><cMun>3550308</cMun><xMun>Sao Paulo</xMun><UF>SP</UF><CEP>01000000</CEP></enderEmit><IE>123</IE></emit>
<dest><CNPJ>92754738000162</CNPJ><xNome>LOJAS RENNER</xNome><enderDest><xLgr>Av B</xLgr><nro>2</nro><xBairro>Centro</xBairro><cMun>4314902</cMun><xMun>Porto Alegre</xMun><UF>RS</UF><CEP>90000000</CEP></enderDest><indIEDest>1</indIEDest><IE>456</IE></dest>
<det nItem="1"><prod><cProd>347191</cProd><cEAN>SEM GTIN</cEAN><xProd>ZEBRA IMPRESSORA INDUSTRIAL ZT231</xProd><NCM>84433290</NCM><CFOP>6102</CFOP><uCom>UN</uCom><qCom>2.0000</qCom><vUnCom>7500.00</vUnCom><vProd>15000.00</vProd><cEANTrib>SEM GTIN</cEANTrib><uTrib>UN</uTrib><qTrib>2.0000</qTrib><vUnTrib>7500.00</vUnTrib><indTot>1</indTot><xPed>4512345</xPed></prod><imposto><ICMS><ICMS00><orig>0</orig><CST>00</CST><modBC>3</modBC><vBC>15000.00</vBC><pICMS>12.00</pICMS><vICMS>1800.00</vICMS></ICMS00></ICMS></imposto></det>
<total><ICMSTot><vBC>15000.00</vBC><vICMS>1800.00</vICMS><vICMSDeson>0</vICMSDeson><vFCP>0</vFCP><vBCST>0</vBCST><vST>0</vST><vFCPST>0</vFCPST><vFCPSTRet>0</vFCPSTRet><vProd>15000.00</vProd><vFrete>0</vFrete><vSeg>0</vSeg><vDesc>0</vDesc><vII>0</vII><vIPI>0</vIPI><vIPIDevol>0</vIPIDevol><vPIS>0</vPIS><vCOFINS>0</vCOFINS><vOutro>0</vOutro><vNF>15000.00</vNF></ICMSTot></total>
<transp><modFrete>1</modFrete></transp>
<cobr><fat><nFat>001</nFat><vOrig>15000.00</vOrig><vDesc>0</vDesc><vLiq>15000.00</vLiq></fat><dup><nDup>001</nDup><dVenc>2026-10-30</dVenc><vDup>15000.00</vDup></dup></cobr>
<infAdic><infCpl>Pedido 4512345</infCpl></infAdic>
</infNFe></NFe><protNFe versao="4.00"><infProt><tpAmb>1</tpAmb><verAplic>SP</verAplic><chNFe>{CHAVE}</chNFe><dhRecbto>2026-09-10T10:01:00-03:00</dhRecbto><nProt>135260000000001</nProt><digVal>abc=</digVal><cStat>100</cStat><xMotivo>Autorizado o uso da NF-e</xMotivo></infProt></protNFe></nfeProc>"""
RESUMO = f'<resNFe xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01"><chNFe>{CHAVE}</chNFe><CNPJ>12345678000199</CNPJ><xNome>ZEBRA DO BRASIL</xNome><dhEmi>2026-09-10T10:00:00-03:00</dhEmi><vNF>15000.00</vNF></resNFe>'


def _zip(texto: str) -> str:
    return base64.b64encode(gzip.compress(texto.encode("utf-8"))).decode()


def _resposta(cstat, docs=()):
    corpo = "".join(f'<docZip NSU="0000000000000{i + 1}" schema="{s}">{_zip(x)}</docZip>' for i, (s, x) in enumerate(docs))
    return (f'<?xml version="1.0"?><soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"><soap:Body>'
            f'<nfeDistDFeInteresseResponse xmlns="http://www.portalfiscal.inf.br/nfe/wsdl/NFeDistribuicaoDFe"><nfeDistDFeInteresseResult>'
            f'<retDistDFeInt xmlns="http://www.portalfiscal.inf.br/nfe" versao="1.01"><tpAmb>2</tpAmb><verAplic>AN</verAplic><cStat>{cstat}</cStat><xMotivo>x</xMotivo>'
            f'<dhResp>2026-09-19T10:00:00-03:00</dhResp><ultNSU>1</ultNSU><maxNSU>1</maxNSU>'
            + (f'<loteDistDFeInt>{corpo}</loteDistDFeInt>' if docs else '') +
            '</retDistDFeInt></nfeDistDFeInteresseResult></nfeDistDFeInteresseResponse></soap:Body></soap:Envelope>').encode()


class _Resp:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


CAPTURA: dict = {}
PLANO: dict = {"status": 200, "corpo": b""}


def _post_falso(self, url, data=None, headers=None, timeout=None, **kw):
    CAPTURA.update({"url": url, "data": data, "headers": headers, "timeout": timeout, "verify": self.verify,
                    "adapter": type(self.get_adapter("https://x")).__name__})
    return _Resp(PLANO["status"], PLANO["corpo"])


requests.Session.post = _post_falso

print("[1] Chave de acesso")
checar(nfe_sefaz.validar_chave(" ".join(CHAVE[i:i + 4] for i in range(0, 44, 4))) == CHAVE, "aceita com espaços e devolve limpa")
for ruim, o_que in ((CHAVE[:-1] + "9", "dígito verificador errado"), (CHAVE[:-1] + "A", "letra"), ("123", "curta"), ("", "vazia")):
    try:
        nfe_sefaz.validar_chave(ruim)
        checar(False, f"{o_que} → ChaveInvalida")
    except nfe_sefaz.ChaveInvalida:
        checar(True, f"{o_que} → ChaveInvalida")

print("\n[2] Certificado por BU")
c = nfe_sefaz.certificado_da_bu("Renner")
checar(c is not None and c.cnpj == "92754738000162" and not c.vencido and c.bu == "Renner", f"Renner: CNPJ do CN e validade ({c})")
c2 = nfe_sefaz.certificado_da_bu("Camicado")
checar(c2 is not None and c2.cnpj == "12345678000199" and c2.vencido, "Camicado: lido e marcado como vencido")
checar(nfe_sefaz.certificado_da_bu("Youcom") is None, "Youcom não tem certificado")
os.environ["NFE_CERT_CAMICADO_SENHA"] = "errada"
import config as _config  # noqa: E402
_config.get_settings().NFE_CERT_CAMICADO_SENHA = "errada"
try:
    nfe_sefaz.certificado_da_bu("Camicado")
    checar(False, "senha errada → NfeSemCertificado")
except nfe_sefaz.NfeSemCertificado as exc:
    checar("senha" in str(exc), f"senha errada → NfeSemCertificado ({str(exc)[:60]})")
_config.get_settings().NFE_CERT_CAMICADO_SENHA = "outra"
_config.get_settings().NFE_CERT_RENNER_PFX = str(RENNER)

print("\n[3] Envelope e chamada")
env = nfe_sefaz.montar_envelope(CHAVE, "92754738000162")
checar(b"<consChNFe><chNFe>" + CHAVE.encode() + b"</chNFe></consChNFe>" in env and b"<tpAmb>2</tpAmb>" in env
       and b"<CNPJ>92754738000162</CNPJ>" in env and b'versao="1.01"' in env, "distDFeInt com consChNFe, CNPJ e ambiente de homologação")
PLANO.update({"status": 200, "corpo": _resposta("138", [("procNFe_v4.00.xsd", XML)])})
r = nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
checar(r.ok and r.completa and r.xml and b"<nNF>200153</nNF>" in r.xml and r.cstat == "138", "138 com procNFe: XML completo")
checar("hom1.nfe.fazenda.gov.br" in CAPTURA["url"] and CAPTURA["adapter"] == "Pkcs12Adapter"
       and "soap+xml" in CAPTURA["headers"]["Content-Type"] and CAPTURA["timeout"] == 40,
       f"POST na URL de homologação, mTLS pelo Pkcs12Adapter, SOAP 1.2, timeout ({CAPTURA['url']})")
checar(CAPTURA["verify"] is not False, f"TLS verificado na sessão ({CAPTURA['verify']!r})")
PLANO.update({"corpo": _resposta("138", [("resNFe_v1.01.xsd", RESUMO)])})
r = nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
checar(not r.completa and r.resumo and "Ciência" in r.erro, "138 só com resNFe: resumo e explicação da ciência")
PLANO.update({"corpo": _resposta("137")})
r = nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
checar(not r.ok and r.cstat == "137" and "Nenhum documento" in r.erro, "137: nenhum documento")
PLANO.update({"corpo": _resposta("656")})
r = nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
checar(not r.ok and "1 hora" in r.erro, "656: consumo indevido, esperar 1 hora")
PLANO.update({"status": 500, "corpo": b"<html>erro</html>"})
try:
    nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
    checar(False, "HTTP 500 → SefazIndisponivel")
except nfe_sefaz.SefazIndisponivel as exc:
    checar("500" in str(exc), "HTTP 500 → SefazIndisponivel")
PLANO.update({"status": 200, "corpo": b"pagina do proxy"})
try:
    nfe_sefaz.buscar_por_chave(CHAVE, "Renner")
    checar(False, "resposta que não é XML → SefazIndisponivel")
except nfe_sefaz.SefazIndisponivel:
    checar(True, "resposta que não é XML → SefazIndisponivel")
try:
    nfe_sefaz.buscar_por_chave(CHAVE, "Youcom")
    checar(False, "Youcom → NfeSemCertificado")
except nfe_sefaz.NfeSemCertificado:
    checar(True, "Youcom → NfeSemCertificado")
try:
    nfe_sefaz.buscar_por_chave(CHAVE, "Camicado")
    checar(False, "certificado vencido → NfeSemCertificado")
except nfe_sefaz.NfeSemCertificado as exc:
    checar("venceu" in str(exc), "certificado vencido → NfeSemCertificado dizendo quando")

print("\n[4] Campos e DANFE")
cp = nfe_sefaz.campos(XML.encode())
checar(cp["nf"] == "200153" and cp["chave"] == CHAVE and cp["vencimento"] == "2026-10-30" and cp["dest_cnpj"] == "92754738000162"
       and cp["emit_nome"] == "ZEBRA DO BRASIL" and cp["valor_total"] == 15000.0 and cp["data_emissao"] == "2026-09-10"
       and cp["itens"] == [{"codigo": "347191", "descricao": "ZEBRA IMPRESSORA INDUSTRIAL ZT231", "quantidade": 2}] and cp["po"] == "4512345",
       f"campos do XML: nNF, chave, vencimento, destinatário, emitente, total, itens, PO ({cp})")
cr = nfe_sefaz.campos(RESUMO.encode())
checar(cr["formato"] == "resumo" and cr["nf"] == "2001" and cr["emit_nome"] == "ZEBRA DO BRASIL", f"campos do resumo vêm da chave ({cr['nf']})")
try:
    pdf = nfe_sefaz.danfe_pdf(XML.encode())
    checar(pdf[:5] == b"%PDF-" and len(pdf) > 5000, f"DANFE gerada em PDF ({len(pdf)} bytes)")
except nfe_sefaz.SemBibliotecaDanfe as exc:
    print("  pulado DANFE:", exc)
try:
    nfe_sefaz.danfe_pdf(RESUMO.encode())
    checar(False, "DANFE do resumo → ValueError")
except (ValueError, nfe_sefaz.SemBibliotecaDanfe):
    checar(True, "DANFE do resumo → ValueError")

print("\n[5] Contraprovas")
fonte = (RAIZ / "integracoes/nfe_sefaz.py").read_text(encoding="utf-8")
checar("verify=False" not in fonte and "verify = False" not in fonte, "sem verify=False")
checar("os.environ" not in fonte and "getenv" not in fonte, "sem leitura direta do ambiente (config apenas)")
checar("http_saida.sessao(" in fonte, "sessão HTTP pela integracoes/http.py")
req = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
checar("requests-pkcs12" in req and "brazilfiscalreport" in req, "dependências declaradas")

print(f"\n{feitos - len(falhas)} de {feitos} verificações passaram.")
if falhas:
    print("Falhou:\n  - " + "\n  - ".join(falhas))
    sys.exit(1)
print("Cliente NF-e: chave, certificado por BU, envelope, respostas da SEFAZ e DANFE.")
