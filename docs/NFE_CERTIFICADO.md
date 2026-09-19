# Certificado A1 da NF-e

O Recebimento → Fornecedores busca o XML da NF-e na SEFAZ pela chave de
acesso, usando o certificado A1 (e-CNPJ, arquivo `.pfx`) da BU. Há um por
BU: Renner e Camicado. Youcom não tem certificado — lá a nota entra por
arquivo (XML ou PDF).

Tudo fica **dentro da pasta do portal**. Nada é gravado em `/etc` nem em
qualquer outro lugar do servidor.

## 1. Onde fica o arquivo

```
/var/www/vcreports/portal-spare/data/certificados/renner.pfx
/var/www/vcreports/portal-spare/data/certificados/camicado.pfx
```

```
cd /var/www/vcreports/portal-spare
mkdir -p data/certificados
cp /caminho/renner.pfx /caminho/camicado.pfx data/certificados/
chown portalspare:portalspare data/certificados data/certificados/*.pfx
chmod 700 data/certificados
chmod 600 data/certificados/*.pfx
```

A pasta `data/` já é ignorada pelo git; o certificado nunca entra no
repositório. Esses dois caminhos são o padrão do portal — só precisam ser
declarados no environment se o arquivo tiver outro nome.

## 2. Conferir o certificado

```
openssl pkcs12 -in data/certificados/renner.pfx -nokeys -clcerts -legacy | openssl x509 -noout -subject -enddate
```

O `subject` tem de trazer o CNPJ (`CN = LOJAS RENNER S.A.:92754738000162`) e
`notAfter` tem de estar no futuro. Sem `-legacy` em OpenSSL 3 alguns `.pfx`
antigos não abrem.

## 3. Chaves no environment

Em `/var/www/vcreports/portal-spare/data/environment` — chaves exclusivas
do certificado, separadas de Correios e EBS:

```
NFE_CERT_RENNER_SENHA=...
NFE_CERT_CAMICADO_SENHA=...
NFE_AMBIENTE=producao
NFE_TIMEOUT=40
NFE_PROXY=
```

Opcionais, só se o `.pfx` não estiver no caminho padrão acima:

```
NFE_CERT_RENNER_PFX=/var/www/vcreports/portal-spare/data/certificados/outro-nome.pfx
NFE_CERT_CAMICADO_PFX=...
```

`NFE_PROXY` declarado e vazio significa "sem proxy"; se a saída para a
SEFAZ passar por proxy, informe-o aqui.

## 4. Dependências e reinício

```
sudo -u portalspare /var/www/vcreports/portal-spare/.venv/bin/pip install "cryptography>=42" requests-pkcs12 brazilfiscalreport
sudo systemctl restart portal_spare
```

## 5. Conferir no portal

Internalização → Lançamento mostra, no alto da lista, o estado de cada
certificado (CNPJ e vencimento). A API é `GET /api/internalizacao/nfe/certificados`.
Arquivo ausente aparece como "não configurado"; senha errada aparece como erro.

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
o `.pfx` em `data/certificados/`, ajuste a senha no environment se mudou e
reinicie o serviço.
