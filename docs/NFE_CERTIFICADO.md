# Certificado A1 da NF-e no servidor

O Recebimento → Fornecedores busca o XML da NF-e na SEFAZ pela chave de
acesso, usando o certificado A1 (e-CNPJ, arquivo `.pfx`) da BU. Há um por
BU: Renner e Camicado. Youcom não tem certificado — lá a nota entra por
arquivo (XML ou PDF).

## 1. Onde fica o arquivo

Fora do repositório e fora de `data/`:

```
sudo mkdir -p /etc/vcreports/nfe
sudo cp renner.pfx camicado.pfx /etc/vcreports/nfe/
sudo chown root:portalspare /etc/vcreports/nfe/*.pfx
sudo chmod 640 /etc/vcreports/nfe/*.pfx
```

## 2. Conferir o certificado

```
openssl pkcs12 -in /etc/vcreports/nfe/renner.pfx -nokeys -clcerts -legacy | openssl x509 -noout -subject -enddate
```

O `subject` tem de trazer o CNPJ (`CN = LOJAS RENNER S.A.:92754738000162`) e
`notAfter` tem de estar no futuro. Sem `-legacy` em OpenSSL 3 alguns `.pfx`
antigos não abrem.

## 3. Chaves no environment

Em `/var/www/vcreports/portal-spare/data/environment` (chaves exclusivas do
certificado, sem passar pelo `.secrets.env` — decisão do dono do portal):

```
NFE_CERT_RENNER_PFX=/etc/vcreports/nfe/renner.pfx
NFE_CERT_RENNER_SENHA=...
NFE_CERT_CAMICADO_PFX=/etc/vcreports/nfe/camicado.pfx
NFE_CERT_CAMICADO_SENHA=...
NFE_AMBIENTE=producao
NFE_TIMEOUT=40
NFE_PROXY=
```

`NFE_PROXY` declarado e vazio significa "sem proxy"; se a saída para a
SEFAZ passar por proxy, informe-o aqui.

## 4. Dependências e reinício

```
sudo -u portalspare /var/www/vcreports/portal-spare/.venv/bin/pip install requests-pkcs12 brazilfiscalreport
sudo systemctl restart portal_spare
```

## 5. Conferir no portal

Internalização → Lançamento mostra, no alto da lista, o estado de cada
certificado (CNPJ e vencimento). A API é `GET /api/internalizacao/nfe/certificados`.

## 6. Regras da SEFAZ que aparecem na tela

- A NF-e completa só é distribuída ao **CNPJ destinatário** da nota (mesma
  raiz de CNPJ do certificado). Nota emitida contra outro CNPJ volta como
  "nenhum documento" (cStat 137).
- Antes da **Ciência da Operação** (feita pelo fiscal), a SEFAZ entrega só
  o resumo (`resNFe`): sem itens e sem DANFE. A tela pede a ciência e a
  nova consulta, ou o envio do arquivo.
- cStat **656** (consumo indevido): esperar 1 hora.
- Consulta com chave de dígito errado é recusada antes de ir à SEFAZ.

## 7. Renovação

Ao vencer, a tela marca "vencido" e a busca para com a mensagem. Substitua
o `.pfx`, ajuste a senha no environment e reinicie o serviço.
