# Orçamento de Manutenção — contrato do módulo

Acompanhamento do orçamento de reparo de **coletores e SLEDs** do time SPARE.
Substitui a planilha `Manutenção.xlsx` e a tela "MANUTENÇÃO LOJAS — COLETOR |
SLED RFID" que o time usa hoje.

Este documento é a **fonte única** para backend e tela: nomes de campo,
regras de normalização, cálculo dos 60 %, endpoints e formato das respostas.
Quem discordar de algo aqui muda o documento primeiro.

---

## 1. Identidade

| | |
|---|---|
| Módulo de permissão | `orcamento_manutencao` (`view` lê, `create` inclui, `edit` altera, `export` exporta, `admin` importa planilha, exclui e configura) |
| Rota do menu | `orcamento_manutencao` — rótulo **Orçamento** na sidebar |
| Prefixo da API | `/api/orcamento-manutencao` |
| Banco | próprio, `ORCAMENTO_MANUTENCAO_DATABASE_URL`, padrão `_sqlite("orcamento_manutencao")` |
| Arquivos | `db/orcamento_manutencao.py` · `routers/orcamento_manutencao.py` · `static/modules/orcamento_manutencao.js` |

Carregado isolado em `main.py` (bloco `try/except` próprio). Nada dele
escreve no banco de outro módulo.

---

## 2. Modelo de dados

### 2.1 `manut_reparo` — uma linha por RMA (chamado de reparo do fornecedor)

| coluna | tipo | origem na planilha | regra |
|---|---|---|---|
| `id` | PK | — | |
| `rma` | str(40), **único**, índice | RMA | texto; remover espaços e `\xa0` (a planilha tem) |
| `serie` | str(60), índice | SÉRIE | maiúsculas, sem espaços nas pontas |
| `loja` | int, nulo | LOJA | loja que paga o reparo |
| `categoria` | str(40), índice | CATEGORIA | rótulo canônico, ver 3.1 |
| `familia` | str(10), índice | derivado | `COLETOR` ou `SLED`, ver 3.1 |
| `empresa` | str(20) | EMPRESA / EBS | `RENNER`, `CAMICADO`, `YOUCOM` ou `""` |
| `orcamento` | Numeric(12,2) | ORÇAMENTO | valor do reparo em R$; `"Garantia"` e `"R$ -"` viram `0` |
| `garantia` | bool | ORÇAMENTO = "Garantia" | reparo em garantia, sem custo |
| `valor_compra` | Numeric(12,2), nulo | derivado / EBS | valor de aquisição do ativo, ver 3.4 |
| `valor_compra_fonte` | str(12) | — | `EBS`, `PLANILHA`, `PADRAO`, `MANUAL` ou `""` |
| `percentual` | Numeric(8,4), nulo | 60% Orçamento | `orcamento / valor_compra`; nulo quando não dá para calcular |
| `avaliacao` | str(10) | AVALIAÇÃO ORÇAMENTO | `DENTRO`, `FORA` ou `""` (ver 3.5) |
| `status` | str(24), índice | STATUS ORÇAMENTO | canônico, ver 3.2 |
| `status_original` | str(60) | STATUS ORÇAMENTO | texto como veio, para auditoria |
| `tipo_manutencao` | str(10) | TIPO DE MANUTENÇAO | `CONTRATO` ou `AVULSA`, ver 3.3 |
| `tipo_original` | str(60) | TIPO DE MANUTENÇAO | texto como veio |
| `status_retorno` | str(14), índice | STATUS DE RETORNO | `DEVOLVIDO` ou `EM_MANUTENCAO` |
| `ano` | int, índice | ANO | ano do registro |
| `mes_referencia` | str(7), índice, nulo | MÊS CONTRATO | `AAAA-MM`; nulo quando a célula é texto ("Aguardando Orçamento") |
| `ano_devolucao` | int, nulo | ANO DEVOLUÇÃO | nulo quando "Em Manutenção" |
| `lote_prime` | str(200) | LOTE PRIME | texto livre |
| `qtde` | int | QTDE | padrão 1 |
| `observacao` | Text | — | livre |
| `ebs_consultado_em` | DateTime, nulo | — | última busca no EBS |
| `ebs_erro` | str(200) | — | motivo da última falha no EBS, se houve |
| `criado_em`, `atualizado_em` | DateTime | — | |
| `criado_por`, `atualizado_por` | str(80) | — | login do portal |
| `origem` | str(12) | — | `PLANILHA` (importado) ou `PORTAL` (digitado) |

### 2.2 `manut_config` — chave/valor JSON

| chave | conteúdo | padrão |
|---|---|---|
| `cota_mensal` | `{"2024": 110691.61, "2025": ..., "2026": ...}` — cota em R$ por ano | `{}` |
| `limiar_percentual` | número, fração | `0.60` |
| `valor_compra_padrao` | `{"Coletor": 4978.29, "Coletor HF550X": 4589.79, "Sled RFID": 3731.51, "Sled RFR901": 3735.95}` | os quatro valores ao lado, que são os que a planilha histórica usa |
| `atualizado_em`, `atualizado_por` | por chave | |

### 2.3 `manut_importacao` — log de cada planilha importada

`id`, `arquivo`, `usuario`, `quando`, `lidas`, `incluidas`, `atualizadas`,
`rejeitadas`, `detalhes` (JSON com as primeiras 200 rejeições: linha + motivo).

---

## 3. Regras de normalização — valem na importação **e** na digitação

A planilha de 3 anos tem 8.123 linhas e estas variações reais. Toda regra
abaixo foi tirada dela.

### 3.1 Categoria e família

Comparação sem acento, sem caixa, sem espaços duplicados.

| vem assim | `categoria` | `familia` |
|---|---|---|
| `Coletor`, `coletor` | `Coletor` | `COLETOR` |
| `Coletor HF550X` | `Coletor HF550X` | `COLETOR` |
| `Coletor S70` | `Coletor S70` | `COLETOR` |
| `Sled RFID` | `Sled RFID` | `SLED` |
| `SLED RFR901` | `Sled RFR901` | `SLED` |
| qualquer outra com "coletor" | texto original com caixa de título | `COLETOR` |
| qualquer outra com "sled" | texto original com caixa de título | `SLED` |
| nada disso | texto original | `OUTRO` |

### 3.2 Status do orçamento

| vem assim (sem acento, minúsculas) | `status` |
|---|---|
| começa com `aprovado` (inclui `Aprovado Via Contrato`, `Aprovado/camicado`) | `APROVADO` |
| começa com `reprovado` (inclui `Reprovado Via Contrato`) | `REPROVADO` |
| `aguardando aprovacao` | `AGUARDANDO_APROVACAO` |
| `aguardando orcamento` | `AGUARDANDO_ORCAMENTO` |
| `validando orcamento` | `VALIDANDO_ORCAMENTO` |
| vazio ou desconhecido | `AGUARDANDO_ORCAMENTO` e a linha entra no relatório de importação como "status não reconhecido: X" |

Rótulos de tela: Aprovado · Reprovado · Aguardando Aprovação · Aguardando
Orçamento · Validando Orçamento.

### 3.3 Tipo de manutenção

| vem assim | `tipo_manutencao` |
|---|---|
| contém `contrato` | `CONTRATO` |
| contém `avulsa` ou `po extra` | `AVULSA` |
| vazio/desconhecido | `CONTRATO` (é o caso de 98 % da base) + aviso no relatório |

### 3.4 Valor de compra — de onde vem, nesta ordem

1. **EBS**, pela série: `integracoes.ebs_service.search_one(auth, serie)` com
   `auth = routers.public_assets._auth()`. Campo `custo_asset`; empresa vem
   de `empresa`. Gravar `valor_compra_fonte = "EBS"` e `ebs_consultado_em`.
   Falhou ou não achou: gravar `ebs_erro` e seguir para o próximo passo —
   **nunca** deixar a inclusão falhar por causa do EBS.
2. **Planilha**, na importação: a coluna `60% Orçamento` é a razão
   `orcamento / valor_compra`. Quando `orcamento > 0` e `razão > 0`,
   `valor_compra = round(orcamento / razão, 2)` e fonte `PLANILHA`.
   (Confere: dá 4.978,29 para Coletor e 3.731,51 para Sled RFID em toda a
   base.)
3. **Padrão por categoria** (`manut_config.valor_compra_padrao`), fonte
   `PADRAO`.
4. Digitado pelo usuário: fonte `MANUAL`. Valor manual **não** é
   sobrescrito por consulta automática ao EBS; só por ação explícita
   (`POST /reparos/{id}/ebs`).

Na importação **não** se consulta o EBS linha a linha (8 mil chamadas):
usa-se 2 e 3. Há um endpoint para completar depois em lote (5.6).

### 3.5 Percentual, avaliação e a regra dos 60 %

```
percentual = orcamento / valor_compra     (nulo se valor_compra nulo ou 0)
avaliacao  = "DENTRO" se percentual <= limiar   (limiar padrão 0.60)
             "FORA"   se percentual >  limiar
             ""       se percentual nulo
```

**Reprovação automática**: quando `avaliacao == "FORA"` e o `status` está
em `AGUARDANDO_APROVACAO`, `AGUARDANDO_ORCAMENTO` ou `VALIDANDO_ORCAMENTO`,
o status vira `REPROVADO` e `status_original` recebe
`"Reprovado automaticamente (X % > 60 %)"`.

Status já decidido (`APROVADO` ou `REPROVADO`) **não é alterado** pela regra:
a planilha histórica tem 34 reparos aprovados acima de 65 % e são decisões
tomadas, não erro. A tela mostra `avaliacao` ao lado do status, então a
divergência fica visível sem reescrever o passado.

A planilha distingue "Entre 60% e 65%" de "Maior que 65%". O módulo
guarda o `percentual` exato, então a tela pode mostrar a faixa que quiser;
a regra de negócio, por ora, tem um limiar só (configurável).

### 3.6 Demais campos

- `orcamento`: número; texto `Garantia` → `0` e `garantia = true`; texto
  com `R$` e `-` → `0`; qualquer outro texto → rejeita a linha com motivo.
- `mes_referencia`: célula data → `AAAA-MM`; texto → nulo.
- `ano_devolucao`: inteiro → valor; texto (`Em Manutenção`) → nulo.
- `status_retorno`: contém `devolvid` → `DEVOLVIDO`; senão `EM_MANUTENCAO`.
- `empresa`: `renner`/`camicado`/`youcom` (sem caixa) → maiúsculas; vazio
  fica vazio até o EBS preencher.
- `rma`: `str(valor).strip()` removendo também `\xa0`; RMA vazio → rejeita.
- `serie`: vazia → rejeita.

---

## 4. Agregados do painel (o que a tela desenha)

Tudo filtrado por **ano** (parâmetro) e agrupado por `mes_referencia`.
Meses sem dado aparecem com zero — a tela sempre mostra jan..dez.

| bloco da imagem | regra |
|---|---|
| **Consumo mês a mês** por família + total | `SUM(orcamento)` onde `status = APROVADO`, por `mes_referencia` e `familia` |
| **Cota mensal** | `manut_config.cota_mensal[ano]` |
| **Cota em uso** | o mês mais recente com consumo > 0 no ano |
| **Consumo atual** | consumo do mês em "cota em uso" |
| **Residual** | `cota_mensal − consumo_atual` |
| **Total investido** | soma do consumo do ano |
| **Equipamentos reparados** por família | `COUNT` onde `status = APROVADO`, por mês e família |
| **Reprovados — valor de aquisição** por família | `SUM(valor_compra)` onde `status = REPROVADO`, por mês e família (a imagem chama de "valor de aquisição": é o custo de repor o equipamento que não valeu a pena consertar) |
| **Equipamentos reprovados** por família | `COUNT` onde `status = REPROVADO`, por mês e família |
| **Informações gerais** | consumo e reparados por família no ano; média = consumo ÷ reparados |
| **Aguardando aprovação** (card) | por `categoria`: `COUNT` e `SUM(orcamento)` onde `status = AGUARDANDO_APROVACAO` |
| **Aguardando devolução** (card) | por `categoria`, onde `status_retorno = EM_MANUTENCAO`: `total`, e a quebra por status: `ag_manutencao` (= `APROVADO`), `ag_orcamento` (= `AGUARDANDO_ORCAMENTO` + `VALIDANDO_ORCAMENTO`), `ag_aprovacao`, `reprovado` |

Os cards **não** filtram por ano — são o retrato de agora.

Linhas com `mes_referencia` nulo entram só nos cards, nunca nos meses.

---

## 5. Endpoints

Todos exigem sessão e passam por `require_permission(req, "orcamento_manutencao", ação)`.
Respostas em JSON; valores monetários como número (a tela formata).

### 5.1 `GET /resumo?ano=2026` — `view`

```json
{
  "ano": 2026,
  "anos_disponiveis": [2024, 2025, 2026],
  "cota_mensal": 110691.61,
  "cota_em_uso": "2026-10",
  "consumo_atual": 70324.02,
  "residual": 40367.59,
  "total_investido": 1039098.00,
  "limiar_percentual": 0.60,
  "meses": [
    {"mes": "2026-01",
     "consumo":    {"COLETOR": 105024.30, "SLED": 5667.31, "TOTAL": 110691.61},
     "reparados":  {"COLETOR": 84, "SLED": 13, "TOTAL": 97},
     "reprovados_valor": {"COLETOR": 212071.05, "SLED": 31598.64, "TOTAL": 243669.69},
     "reprovados_qtde":  {"COLETOR": 45, "SLED": 8, "TOTAL": 53}}
    /* ... sempre 12 entradas, jan..dez */
  ],
  "gerais": {
    "COLETOR": {"consumo": 1010658.08, "reparados": 898, "media": 1125.45},
    "SLED":    {"consumo": 28439.92,   "reparados": 51,  "media": 557.65}
  },
  "aguardando_aprovacao": [
    {"categoria": "Sled RFID", "familia": "SLED", "qtde": 36, "valor": 19774.00}
  ],
  "aguardando_devolucao": [
    {"categoria": "Coletor", "familia": "COLETOR", "total": 54,
     "ag_manutencao": 18, "ag_orcamento": 0, "ag_aprovacao": 0, "reprovado": 36}
  ]
}
```

### 5.2 `GET /reparos` — `view`

Filtros (query): `ano`, `mes` (`AAAA-MM`), `familia`, `categoria`, `status`,
`status_retorno`, `empresa`, `tipo_manutencao`, `q` (busca em `rma`, `serie`,
`lote_prime`), `limit` (padrão 100, máx 1000), `offset`.

```json
{"total": 8123, "itens": [ { ...todas as colunas de 2.1... } ]}
```

### 5.3 `POST /reparos` — `create`

Corpo (só `rma`, `serie` e `categoria` são obrigatórios):

```json
{"rma": "202609124321", "serie": "RFR900NX5REUHBB899", "loja": 30,
 "categoria": "Sled RFID", "orcamento": 389.29, "status": "AGUARDANDO_APROVACAO",
 "tipo_manutencao": "CONTRATO", "status_retorno": "EM_MANUTENCAO",
 "mes_referencia": "2026-09", "lote_prime": "Manutenção Lojas",
 "empresa": "", "valor_compra": null, "observacao": ""}
```

Ao incluir: normaliza (seção 3), **consulta o EBS pela série** (3.4 passo 1)
para `valor_compra` e `empresa` — a menos que `valor_compra` tenha vindo no
corpo (fonte `MANUAL`) —, calcula percentual/avaliação e aplica a
reprovação automática (3.5). `ano` sai de `mes_referencia` ou do ano atual.
Devolve `201` com a linha gravada, mais `"ebs": {"consultado": true|false, "erro": ""}`.

RMA repetido → `409` com `{"detail": "RMA já cadastrado", "id": 123}`.

### 5.4 `PUT /reparos/{id}` — `edit`

Mesmo corpo, todos opcionais. Recalcula percentual/avaliação/regra dos 60 %
sempre que `orcamento` ou `valor_compra` mudarem. Não consulta o EBS sozinho.

### 5.5 `DELETE /reparos/{id}` — `admin`

### 5.6 `POST /reparos/{id}/ebs` — `edit` · `POST /reparos/ebs-pendentes?limite=200` — `admin`

Reconsulta o EBS para uma linha, ou para até `limite` linhas sem
`valor_compra` ou sem `empresa` (as mais recentes primeiro). Sobrescreve
`valor_compra` só se a fonte atual **não** for `MANUAL`. Devolve
`{"consultados": n, "atualizados": n, "falhas": [{"id":..,"erro":..}]}`.

### 5.7 `POST /importar` — `admin` — `multipart/form-data`, campo `file`

Aceita `.xlsx`/`.csv` com as colunas da planilha (casamento por nome sem
acento e sem caixa; ver `routers/recebimento.py::import_historico` para o
padrão de aliases). **Upsert por RMA**: RMA que já existe é atualizado, o
resto é incluído. Não consulta o EBS (3.4). Devolve

```json
{"lidas": 8123, "incluidas": 8100, "atualizadas": 0, "rejeitadas": 23,
 "detalhes": [{"linha": 57, "motivo": "orçamento não numérico: 'abc'"}],
 "avisos": {"status_nao_reconhecido": 2, "tipo_nao_reconhecido": 0, "empresa_vazia": 1275}}
```

e grava em `manut_importacao`.

### 5.8 `GET /exportar.xlsx` — `export`

Mesmos filtros de 5.2, sem paginação. Use `routers.helpers.xlsx_response`.

### 5.9 `GET /config` — `view` · `PUT /config` — `admin`

```json
{"cota_mensal": {"2026": 110691.61}, "limiar_percentual": 0.60,
 "valor_compra_padrao": {"Coletor": 4978.29, "Sled RFID": 3731.51}}
```

`PUT` aceita o mesmo objeto, parcial. Mudar `limiar_percentual` **não**
recalcula o histórico sozinho — há `POST /recalcular` (`admin`) que refaz
`percentual`/`avaliacao` de todas as linhas e aplica a regra 3.5 nas
pendentes, devolvendo quantas mudaram.

### 5.10 `GET /opcoes` — `view`

Listas para os selects: categorias (das linhas + padrão), status, tipos,
empresas, anos, lotes mais usados. Evita hard-code na tela.

---

## 6. Tela — o que a imagem de referência pede

Uma rota `orcamento_manutencao` com sub-abas:

1. **Painel** (padrão) — reproduz a imagem: seletor de ano; à esquerda os
   blocos FINANCEIRO e INFORMAÇÕES GERAIS; à direita as quatro tabelas
   mensais (consumo, reparados, reprovados-valor, reprovados-qtde) com
   colunas jan..dez, linhas COLETOR / SLED / TOTAL, e o indicador de
   tendência (▲ ▼ ▬) comparando com o mês anterior; abaixo os dois cards,
   Aguardando aprovação e Aguardando devolução, por categoria.
2. **Reparos** — lista com filtros de 5.2, busca, paginação, botão Novo,
   edição em modal, exportar. Na linha: status com badge, avaliação
   (DENTRO/FORA com o percentual), origem do valor de compra.
3. **Importar** (`admin`) — upload da planilha, resultado com rejeitadas e
   avisos, botão "Completar valor de compra pelo EBS" (5.6 em lote).
4. **Configuração** (`admin`) — cota por ano, limiar, valores padrão por
   categoria, botão recalcular.

Cores e componentes: os do portal (`.card`, `.data-table`, `.badge-*`,
`.stats-grid`). Sem `<script>` inline (CSP). Números em `pt-BR`
(`S.money`, `toLocaleString('pt-BR')`).

---

## 7. Perguntas em aberto (não bloqueiam a primeira versão)

1. A faixa **60–65 %** precisa de tratamento próprio (ex.: "validar antes de
   reprovar") ou basta o limiar único? Hoje: limiar único, faixa visível
   pelo percentual.
2. "Equipamentos reparados" conta **aprovados** ou **devolvidos**? Hoje:
   aprovados por mês de referência.
3. A **cota mensal** é uma só por ano ou varia por mês? Hoje: uma por ano.
4. `LOJA` deve virar cadastro (nome da loja) ou fica só o número? Hoje: número.

---

## 8. Retorno de reparo, planilha do fornecedor e reincidência

### 8.1 Colunas novas em `manut_reparo`

| coluna | tipo | conteúdo |
|---|---|---|
| `origem_equipamento` | str(10) | `LOJA` ou `CD` — coluna ORIGEM da planilha do fornecedor |
| `disponibilizacao` | Date, nulo | DISPONIBILIZAÇÃO: data em que o fornecedor enviou o orçamento |
| `devolvido_em` | DateTime, nulo | quando o retorno foi confirmado no portal |
| `devolvido_por` | str(80) | login de quem confirmou |
| `po` | str(40) | PO da manutenção avulsa, quando houver |

O banco de produção já existe: `init_db()` precisa acrescentar as colunas que
faltarem (`ALTER TABLE ... ADD COLUMN`, uma por vez, ignorando as que já
existem). Nunca recriar a tabela.

### 8.2 Planilha do fornecedor (lote de reparo)

Layout, verificado no arquivo real (aba `CONTRATO 464`, 1.678 linhas úteis):

| coluna | destino |
|---|---|
| DISPONIBILIZAÇÃO | `disponibilizacao` (data) |
| CATEGORIA | `categoria`: `Coletor - BlueBird` → **Coletor**; `Sled RFID` → **Sled RFID** |
| SÉRIE | `serie` |
| ORIGEM | `origem_equipamento` (`LOJA`/`CD`) |
| RMA | `rma` (único) |
| ORÇAMENTO | `orcamento` |
| APROVADO VIA CONTRATO (ou STATUS) | `status` + `tipo_manutencao` + `mes_referencia` |

O texto do status carrega três informações. Regras (sem acento, sem caixa):

| texto | status | tipo | mês |
|---|---|---|---|
| `APROVADO VIA CONTRATO - MARÇO 2026` | `APROVADO` | `CONTRATO` | `2026-03` |
| `APROVADO VIA PO EXTRA - AGOSTO 2026` | `APROVADO` | `AVULSA` | `2026-08` |
| `APROVADO - PO EXTRA CAMICADO - SETEMBRO 2026` | `APROVADO` | `AVULSA` | `2026-09` (e `empresa = CAMICADO`) |
| `REPROVADO` | `REPROVADO` | — | — |
| `AGUARDANDO APROVAÇÃO` | `AGUARDANDO_APROVACAO` | — | — |
| `validando orçamento` | `VALIDANDO_ORCAMENTO` | — | — |
| `BONIFICADO` | `APROVADO` com `orcamento = 0` | — | — |
| `GARANTIA` | `APROVADO`, `garantia = true`, `orcamento = 0` | — | — |
| `FATURADO` (aba AVULSO) | `APROVADO` — a nota já saiu | `AVULSA` | do MÊS - CONTRATO |
| `... OUTUBRO / NOVEMBRO 2025` | vale o **primeiro** mês, com aviso | | |

Mês por extenso em português (janeiro..dezembro) seguido do ano. Sem mês no
status, `mes_referencia` sai do mês da DISPONIBILIZAÇÃO. `ano` sai do mês de
referência. `status_original` guarda o texto como veio.

Planilha sem coluna de tipo e reparo novo: assume `CONTRATO` e conta o
aviso `tipo_assumido_contrato`. Reparo que já existe mantém o tipo gravado.

A importação continua fazendo **upsert por RMA** e aceita a opção de
substituir a base. Aceita também o campo opcional `aba`: sem ele, usa a
primeira aba que tenha RMA, SÉRIE e CATEGORIA (ou RMA e S/N); a resposta
informa `aba` usada e `abas_disponiveis`.

Também é reconhecido o layout da aba `AVULSO` (RMA, S/N, VALOR,
MÊS - CONTRATO, STATUS, PO): tipo `AVULSA`, `po` preenchido, `serie` de S/N,
`orcamento` de VALOR, `mes_referencia` de MÊS - CONTRATO.

### 8.3 Retorno de reparo (nova sub-aba)

Tela para bipar RMAs em lote, no padrão da Consulta: uma caixa de texto que
aceita separação por vírgula, ponto e vírgula, espaço ou uma por linha.

- `POST /retorno/consultar` — `view`. Corpo `{"rmas": ["...", "..."]}`.
  Devolve, na ordem digitada, `{"rma", "encontrado", "ja_devolvido", ...dados
  do reparo}`. RMA fora da base vem com `encontrado: false`.
- `POST /retorno/confirmar` — `edit`. Mesmo corpo. Marca
  `status_retorno = DEVOLVIDO`, grava `devolvido_em`, `devolvido_por` e
  `ano_devolucao` (ano corrente) nos que existem e ainda não estavam
  devolvidos. Devolve `{"devolvidos": n, "ja_devolvidos": n,
  "nao_encontrados": [...], "itens": [...]}`.

Nada é criado por essa tela: RMA que não existe é apenas reportado.

### 8.4 Reincidência

Um equipamento é identificado pela **série**; o RMA é único por atendimento.
Série com dois ou mais RMAs é reincidente.

- `GET /reparos` passa a devolver, em cada item, `serie_reparos` (quantos
  atendimentos aquela série tem na base) e `serie_custo` (soma dos orçamentos
  aprovados da série). Novo filtro `min_reparos` (inteiro; 2 = só
  reincidentes).
- `GET /exportar.xlsx` aceita o mesmo filtro e traz as duas colunas.
- `GET /reincidencia` — `view` — resumo por série: `serie`, `categoria`,
  `familia`, `reparos`, `custo_total`, `custo_medio`, `primeiro`, `ultimo`,
  `reprovados`, `lojas`. Filtros `min_reparos` (padrão 2), `familia`,
  `categoria`, `ano`, `q`, `limit`, `offset`, ordenado por `reparos` e depois
  `custo_total`, ambos decrescentes.
- `GET /reincidencia.xlsx` — `export` — mesma consulta, sem paginação.
