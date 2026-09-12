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

### Coleta — sob demanda primeiro, agendada depois
**Decisão da área: nada de madrugada neste primeiro momento.** A coleta roda
por botão, com alguém acompanhando, até o comportamento estar conhecido —
volume real, tempo de execução, estabilidade da sessão. O agendamento entra
depois, e a rotina já nasce preparada para ser chamada por horário.

Quando rodar, lê o parque inteiro e grava no banco próprio do módulo.
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

## O console exige cabeçalho de AJAX

`/Device/List/Search` é ASP.NET MVC e olha o `X-Requested-With` para decidir
o que devolver:

| Requisição | Resposta |
|---|---|
| sem `X-Requested-With` | a **página inteira** do console (3,2 MB) — e **todo parâmetro é ignorado** |
| com `X-Requested-With: XMLHttpRequest` | o fragmento da grade, e aí os parâmetros valem |

Isso custou uma rodada inteira de investigação: sem o cabeçalho, 28
parâmetros diferentes devolveram exatamente o mesmo resultado, o que parecia
"a paginação é estado de sessão" quando na verdade a grade nem estava sendo
chamada. O coletor **tem que** mandar esse cabeçalho.

Sinal de que veio a coisa certa: o corpo contém `DeviceGrid` e **não** contém
`<html`.

## Modelos em EOL

Definido pela área: **EF500**.

⚠️ **A conferir antes de valer:** o parque tem `Bluebird EF501R`, e a string
"EF500" não está contida em "EF501R". Do jeito que a comparação é feita hoje
(por conteúdo), EF501R **não** seria marcado como EOL. Se a intenção é que a
família EF500 inclua o EF501R, a lista precisa dizer isso explicitamente —
senão o painel devolve zero obsoletos e ninguém percebe o motivo.

## Endpoints do detalhe de um coletor

Vieram de brinde numa captura, ao abrir um aparelho. Úteis para a busca por
série e para enriquecer o cadastro:

| Endpoint | O que traz |
|---|---|
| `GET /Device/Details/Summary/{id}` | página do aparelho (modelo, usuário, versão) |
| `POST /Devices/QueryAll/{id}` | força o aparelho a reportar |
| `GET /Devices/SearchCustomAttributesGrid` | atributos customizados (23) — **ver se guardam patrimônio/aquisição** |
| `GET /Devices/DeviceApplicationSearch` | apps instalados (43) |
| `GET /Device/Details/Content/{id}` | conteúdo (50) |
| `POST /Device/Details/ReloadTags?deviceId=` | tags do aparelho |

## Contrato confirmado (diagnóstico de 12/09/2026)

### Varredura do parque

    GET /AirWatch/Device/List/Search?Page=N&Sort=DeviceFriendlyName&Order=Ascending
    X-Requested-With: XMLHttpRequest

- **`Page` é BASE ZERO**: `Page=0` é a primeira página. Confirmado pelo
  próprio paginador do HTML, que marca `data-page="0"` no botão "1".
  `Page=2` devolveu os itens 201-300.
- 100 por página → **159 páginas** para os 15.819.
- **Ordenar por nome é obrigatório.** O padrão é `Sort=LastPingDate`
  decrescente, e a lista se reordena sozinha enquanto a varredura roda: sem
  ordem estável a coleta repete uns coletores e pula outros.
- O progresso vem do rodapé `Items X - Y of Z`, não da contagem de linhas.
- `Context.Size` é o campo de tamanho de página, mas rejeitou 500 e caiu
  para 50. Não é necessário: com `Page` e 100 por página a varredura fecha.

### Leitura de uma linha
Pelos atributos, nunca pela posição da coluna (o usuário pode reordenar):

| Campo | Onde |
|---|---|
| id do aparelho | `data-view-url="…/Summary/691477"` |
| nome | `data-property="FriendlyName"` → `id="FriendlyName"` |
| usuário / loja | `class="PartialPath"` → último trecho (`ljr234_coletor`) |
| caminho da organização | `data-ats-id="og-path"` |
| plataforma / modelo / versão | `data-ats-id="platform"` / `display-model"` / `os-version"` |
| último contato | `data-property="LastPingDate"` → `data-ats-id="device-last-seen"` |
| propriedade / gerenciamento | `data-ats-id="ownership"` / `"management"` |
| conformidade | `data-property="ComplianceStatusName"` |
| ações permitidas | `data-action-names` (traz `ManageTags`, `DeleteDevice`) |

Implementado e testado contra HTML real em `integracoes/mdm_airwatch.py`.

### Busca
`SearchText` filtra (15.819 → 53 buscando `ljr234_coletor`). **Busca por
série ainda não foi provada** — o diagnóstico não achou uma série de verdade
na grade para testar; o que ele pegou era o id do tenant.

### Escrita de tag — contrato completo, obtido sem escrever

    GET  /AirWatch/Devices/TagAssignment/{id}    → devolve o formulário
    POST /AirWatch/Devices/ManageTagsBulkDevices
         __RequestVerificationToken = <token do GET acima>
         SelectedDeviceIds          = 691477
         AddedAssignedTags          = <ids>
         RemovedAssignedTags        = <ids>

**Tem anti-CSRF.** Não dá para postar direto: é preciso buscar o formulário
antes e reaproveitar o token daquela resposta.

### Data de aquisição: confirmado que vem do EBS
Os atributos customizados do MDM são configuração do agente
(`androidAgent.logLevel`, `app-group`, `ca-value`) — não têm data de compra
nem patrimônio. A idade real do ativo só sai do EBS.

### As tags reais — decisão pendente
A comparação por conteúdo pega mais do que três. Existem no MDM:

| Tag | id |
|---|---|
| Manutenção | 11607 |
| Em manutenção | 11644 |
| Backlog de manutenção | 11643 |
| Manutenção de Bloqueio | 11640 |
| Bloqueio - Inatividade N1 | 11616 |
| Bloqueio - Inatividade +14 | 11765 |
| Bloqueio - Movimentação N1 | 11615 |
| Bloqueio - Movimentação N2 | 11635 |
| HML BLOQUEIO MOVIMENTAÇÃO | 11768 |

Decidido pela área: **conta as nove, mas o painel só mostra as que têm
coletor atribuído**. Cada tag é contada pelo **nome real** — "Backlog de
manutenção" não vira "Manutenção" — e o grupo (Inatividade / Manutenção /
Movimentação) fica ao lado, para quem quiser somar.

Tag sem nenhum coletor não aparece: o painel mostra o parque, não o catálogo
de tags do MDM. Um coletor com duas tags conta nas duas, porque as situações
são simultâneas.

## PDVs — o segundo parque, vindo do ServiceNow

O painel cobre dois parques de origens diferentes, na mesma base e na mesma
tela (coluna `tipo`: `coletor` | `pdv`):

| Parque | Origem | Como |
|---|---|---|
| Coletores | MDM (Workspace ONE) | grade HTML, paginada |
| PDVs | ServiceNow, `cmdb_ci_computer` | JSONv2, já integrado ao portal |

    discovery_source=ACC_VISIBILITY^install_status=1

Filtro definido pela área: origem da descoberta **ACC_VISIBILITY** e status
**Instalado**, agrupado por local. Tabela e query ficam na configuração do
módulo, porque o rótulo da tela ("Origem da descoberta") pode não bater com
o nome interno do campo — vale conferir na primeira execução.

Diferenças de tratamento:

- A loja do PDV vem do **Local** da CMDB, não do padrão
  `<sigla><numero>_coletor`, que só existe no MDM.
- Referências do JSONv2 chegam ora como texto, ora como
  `{value, display_value}` — a tradução resolve os dois casos.

Os PDVs são mais fáceis que os coletores: a integração com o ServiceNow já
existe no portal e pagina sozinha (`_sn_query_all`).

## Próximo passo

O painel, cobrindo os dois parques.

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

Interessam as que carregam um destes nomes: **Inatividade**, **Manutenção**,
**Movimentação** — comparados sem acento e sem caixa. Qualquer outra tag do
MDM é ignorada.

Cada tag é contada **pelo nome real**, não amassada no grupo, e só entra no
painel se tiver coletor atribuído.

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
