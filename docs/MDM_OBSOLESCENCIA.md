# MDM de Coletores — contrato descoberto e plano da Obsolescência

Levantado a partir de captura real do console (Workspace ONE / AirWatch,
`cn258.awmdm.com`) em 12/09/2026, com `scripts/mdm_airwatch_captura.js` e
`scripts/mdm_analisar_har.py`.

## O que o console é

Aplicação ASP.NET: a grade de coletores é **HTML montado no servidor**, não
JSON. Só os filtros e o dashboard voltam em JSON. Qualquer coleta que dependa
de raspar a grade quebra quando o MDM é atualizado — é dívida conhecida.

## Tamanho do parque

| Medida | Valor |
|---|---|
| Android | 15.819 |
| Linux | 1 |
| Enrolled / Registered / Unenrolled | 15.803 / 85 / 3 |
| Shared Corp / Dedicated Corp | 15.499 / 321 |
| Comunicando (Up) / sem comunicar (Down) | 10.456 / 5.364 |

## Endpoints úteis

### `GET /AirWatch/Device/Dashboard/Data` — JSON, agregados
15 blocos (`LastSeen`, `Platforms`, `Ownership`, `Enrollment`,
`AndroidOsVersion`, `Compromised`, `Encrypted`, …). Cada item traz
`Label`, `Value` e `Url` de drill-down.

**Cuidado:** `AndroidOsVersion` só lista as versões mais novas — 16.0 (129),
15.0 (26) e 14.0 (110), somando 265 de 15.819. Os outros ~98% estão em
versões antigas que o widget não plota. Ou seja: o dashboard NÃO serve para
medir obsolescência, justamente porque omite a parte obsoleta.

### `GET /AirWatch/Device/List/Search` — HTML, a grade
Colunas: Last Seen, General Info, Platform, User, Tags, Enrollment,
Compliance Status. Filtros confirmados pelas URLs de drill-down:

    ?Platform=Android
    ?OsVersion=14.0&Platform=Android
    ?Ownership=1|2|3            (Dedicated | Employee | Shared)
    ?EnrollmentStatus=4         (Enrolled)
    ?LastSeenMin=0&LastSeenMax=3
    ?Passcode=N  ?Encryption=N  ?Compromised=...

Na captura o `<tbody>` veio vazio (684 KB eram a casca da página): as linhas
chegam em outra requisição, ainda não capturada.

## Regra de obsolescência (definida pela área)

Coletor obsoleto quando:
1. **5 anos de uso**, e
2. **Android travado**, sem possibilidade de atualizar de versão, e
3. **EOL atingido** para o modelo.

Pendência: o MDM tem a data de **inscrição** (enrollment), que não é a data de
**aquisição**. Se os 5 anos contam da compra, a idade precisa vir do EBS ou da
base de recebimento.

## Acesso

Mesma credencial do ServiceNow, com usuário no formato `renner\<usuario>`,
sessão mantida por keep-alive. O portal **não guarda a senha do usuário** (só
`sn_cookies`), então a coleta em segundo plano usa credencial de serviço no
**cofre**, como já faz `routers/automacoes.py` (`_creds_para_login()`).

O coletor é **somente leitura**. Nunca chamar `Devices/DeleteDevice`,
`Devices/ManageTagsBulkDevices` nem qualquer rota de escrita.

## Próximo passo

Capturar a ação de **Export** da lista: é o caminho de 1 requisição para as
15.8 mil linhas, em vez de paginar ~316 telas de HTML.
