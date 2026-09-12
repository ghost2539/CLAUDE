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

### `GET /AirWatch/Device/List` — a casca (506 bytes)
Só dispara a grade por AJAX, sem token nem cabeçalho especial:

    var url = '/AirWatch/Device/List/Search';
    $.ajax({ url: url, method: 'get' }).then(view => $('#device-list').html(view));

Ou seja: basta a sessão para chamar `/Device/List/Search` direto.

### `GET /AirWatch/Device/List/Search` — HTML, a grade
Colunas: Last Seen, General Info, Platform, User, Tags, Enrollment,
Compliance Status. Filtros confirmados pelas URLs de drill-down:

    ?Platform=Android
    ?OsVersion=14.0&Platform=Android
    ?Ownership=1|2|3            (Dedicated | Employee | Shared)
    ?EnrollmentStatus=4         (Enrolled)
    ?LastSeenMin=0&LastSeenMax=3
    ?Passcode=N  ?Encryption=N  ?Compromised=...

As linhas **estão** nesta resposta (684 KB): 74 coletores por página. A
primeira leitura deu "0 linhas" porque o contador olhava só o primeiro
`<tbody>` da página, que é um template vazio — corrigido.

Cada linha traz um `aria-label` com usuário, modelo e versão juntos, e o id do
aparelho no link de detalhe:

    aria-label="ljr417_coletor_2 Bluebird EF501R Android 9.0 I985"
    href="#/AirWatch/Device/Details/Summary/751057"
    <td class="time_stamp" data-property="LastPingDate">

Os `data-property` das células são a chave para extrair campo a campo, em vez
de depender da posição da coluna.

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

## Identidade da loja e da BU

O usuário do coletor carrega a loja: `<sigla><numero>_coletor`.

| Sigla | BU | País | Prefixo numérico |
|---|---|---|---|
| LJR | Renner | BR | — |
| CM | Camicado | BR | — |
| LAS | Ashua | BR | — |
| YC | Youcom | BR | — |
| CD | Centro de Distribuição | BR | — |
| LJRAR | Renner Argentina | AR | 13 |
| LJRUY | Renner Uruguai | UY | 11 |

Argentina e Uruguai trazem um prefixo numérico fixo que **não** faz parte do
número da loja: `ljrar13001_coletor` é a loja 001, não a 13001.

A separação letras/dígitos é feita por regex, então `ljrar13001` nunca é lido
como a BU `LJR` seguida de `ar13001` — essa era a armadilha do parsing.

Dois achados que só os dados reais revelaram:

- **`CD`** (Centro de Distribuição) existe no parque (`cd504_coletor`) e não
  estava na lista original de identificadores. **Confirmar com a área** se o
  CD entra nos números do painel ou fica de fora.
- **Sufixo de sequência**: `ljr417_coletor_2` é o segundo coletor da mesma
  loja. O sufixo é guardado à parte; a loja continua sendo a 417.
O que não bate no padrão vira "Não identificado" em vez de ser descartado:
sumir com o registro esconderia coletor do painel.

## Tags acompanhadas

Só três, contadas separadamente: **Inatividade**, **Manutenção**,
**Movimentação**. A comparação é por conteúdo, sem acento e sem caixa, então
"Em Manutenção - CD" conta como Manutenção. Qualquer outra tag é ignorada.

## Recortes que o painel deve entregar

1. Coletores por Loja / BU
2. Quantos dentro dos critérios de obsolescência
3. Os mais antigos do parque
4. Sem comunicar há mais de 30 dias
5. Por tag (Inatividade, Manutenção, Movimentação), separadamente

## Em aberto

- **Regra E ou OU?** Implementado como os três critérios juntos (E), conforme
  o enunciado. Muda muito o número — com OU, o total dispara.
- **Data de aquisição**: o MDM só tem a inscrição. Falta a origem real.
- **Lista de modelos EOL**: não vem do MDM, precisa ser mantida no portal.
