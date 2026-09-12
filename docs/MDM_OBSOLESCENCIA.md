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

### Leitura e escrita têm credenciais diferentes

Espelha a norma que já vale para o ServiceNow:

| | Credencial | Quem dispara |
|---|---|---|
| **Leitura** (coleta noturna) | conta de serviço, no cofre | rotina automática |
| **Escrita** (tag, deleção) | **sessão do usuário logado** | sempre uma pessoa |

Escrever como o usuário logado deixa o rastro certo no próprio MDM: quem
deletou um coletor aparece lá, não uma conta genérica.

### A rotina noturna NUNCA escreve

Separação dura: o processo que roda de madrugada só lê. Nenhum caminho de
código da coleta chama rota de escrita. Isso é o que impede um erro de
parsing virar deleção em massa às 3h da manhã, sem ninguém olhando.

## Arquitetura definida

### Coleta noturna
Roda de madrugada, lê o parque inteiro e grava no banco próprio do módulo.
A cada rodada, compara com o que já existe:

- **coletor novo** → entra na base;
- **coletor que sumiu** do MDM → não é apagado: vai para uma fila de
  **tratativa**, em menu separado, para alguém decidir. Sumir da base em
  silêncio esconderia justamente o caso que precisa de ação.

### Data de aquisição — vem do EBS
O MDM só tem a data de inscrição, que "rejuvenesce" quando o coletor é
reinscrito após reparo. A idade real vem do **EBS**, cruzada pela série.
Quando o EBS não tiver o ativo, a idade fica marcada como desconhecida em
vez de ser estimada pela inscrição: número inventado em tela de diretoria é
pior que número ausente.

### Regra de obsolescência configurável
Padrão **E** (os três critérios juntos), como a área definiu, com opção de
trocar para **OU** na configuração do módulo.

### Escritas no MDM (sob demanda, nunca automáticas)
Sempre por ação de uma pessoa, sempre num coletor só, resolvido pela série.

| Ação | Endpoint | Origem |
|---|---|---|
| Inserir/remover tag | `POST /AirWatch/Devices/ManageTagsBulkDevices` | tela ou abertura de chamado |
| Ler tags do aparelho | `POST /AirWatch/Device/Details/ReloadTags?deviceId=` | conferência |
| Deletar coletor | `POST /AirWatch/Devices/DeleteDevice/{id}` | Recebimento |
| Lista de tags (id) | `GET /AirWatch/Device/List/TagsListSearch` | 57 tags, label + value |

**Busca sempre pela série** — é o padrão da área.

### Processos que disparam escrita
- **Recebimento**: coletor recebido no CD é **deletado** do MDM, para
  desvincular da loja. Processo padrão da área.
- **Chamado**: ao iniciar o atendimento, o coletor recebe a tag
  **Manutenção**.

### Salvaguardas da deleção
Deleção não tem volta, e vai passar a ser disparada por um fluxo de rotina.
Por isso:

1. **Um por vez.** Nunca em lote, mesmo que o endpoint aceite.
2. **Série tem que resolver para exatamente um aparelho.** Zero ou mais de
   um → recusa e mostra o que achou, em vez de escolher sozinho.
3. **Registro local de toda escrita**: quem, quando, série, ação, resposta
   do MDM. Independente do log do próprio MDM.
4. **Confirmação explícita** na tela antes de deletar.
5. A rotina noturna não tem acesso a esse caminho.

## Próximo passo

Falta capturar, e a captura agora guarda corpo de requisição:

1. **Paginação** de `/Device/List/Search` (parâmetro de página / page size).
2. **Busca por série** — qual endpoint e qual parâmetro.
3. **Corpo do `ManageTagsBulkDevices`** ao inserir e ao remover uma tag,
   para saber como identificar aparelho e tag na chamada.

## Identidade da loja e da BU

O usuário do coletor carrega a loja: `<sigla><numero>_coletor`.

| Sigla | BU | País | Prefixo numérico | |
|---|---|---|---|---|
| LJR | Renner | BR | — |
| CM | Camicado | BR | — |
| LAS | Ashua | BR | — |
| YC | Youcom | BR | — |
| CD | Centro de Distribuição | BR | — | **fora do painel** |
| LJRAR | Renner Argentina | AR | 13 |
| LJRUY | Renner Uruguai | UY | 11 |

Argentina e Uruguai trazem um prefixo numérico fixo que **não** faz parte do
número da loja: `ljrar13001_coletor` é a loja 001, não a 13001.

A separação letras/dígitos é feita por regex, então `ljrar13001` nunca é lido
como a BU `LJR` seguida de `ar13001` — essa era a armadilha do parsing.

Dois achados que só os dados reais revelaram:

- **`CD`** (Centro de Distribuição) existe no parque (`cd504_coletor`) e não
  estava na lista original. Definido pela área: **não é loja, fica fora dos
  números do painel**. Ainda assim é reconhecido de propósito, com
  `e_loja = False`, para não cair em "Não identificado" — que é o sinal de
  dado sujo e precisa continuar significando só isso.
- **Sufixo de sequência**: `ljr417_coletor_2` é o segundo coletor da loja 417.
  A regra é direta: `<identificador da BU><número da loja>_coletor`, e o que
  vier depois não muda a loja.
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
