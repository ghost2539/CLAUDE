# Estrutura do repositório

Regra que organiza tudo o que está aqui: **cada pasta tem um papel só**.
Rota não conhece driver de banco, banco não conhece HTTP, e integração
externa não conhece nem um nem outro. A raiz guarda apenas o ponto de
entrada e a configuração.

**Tudo roda num processo só**, na porta 8901: o servidor novo não tem root,
então manter vários serviços não é opção. Telas que antes eram aplicativos
separados (Consulta de Ativos dos times) viraram router do portal. É um
processo, um serviço e um deploy.

```
main.py                  Ponto de entrada do portal (uvicorn main:app)
config.py                Configuração central — lida por tudo, inclusive scripts/
requirements.txt         Dependências do portal
.env.example             Modelo das variáveis de ambiente

core/                    Núcleo: o que não é rota, banco nem integração
  security.py            Sessão, permissões, rate limit, middlewares de segurança
  notificador.py         Canal de alertas por e-mail (SMTP)

db/                      Camada de dados — um módulo por banco, todos isolados
  portal.py              Postgres principal (usuários, recebimentos, reparos…)
  indicadores.py         Snapshots do painel de indicadores
  automacoes.py          Regras, logs e configuração das automações
  monitoramento.py       Eventos de saúde/falha e configuração de alertas
  orcamento_exec.py      Controle de Orçamento — execução CAPEX


routers/                 As APIs do portal — uma por área funcional
  auth · consulta · recebimento · reparos · status · parametros
  identificacao · servicenow · correios · encerramento · rastreio (tv)
  indicadores · automacoes · monitoramento
  controle_orcamento_exec · orcamento_spare · public_assets · helpers
  consulta_times · cockpit (telas de TV)

integracoes/             Clientes de sistemas externos (sem rota, sem banco)
  ebs_service.py         API REST do EBS

  ebs_logged.py          Raspagem autenticada do EBS

static/                  Front-end servido ao navegador (público por definição)
  index.html · app.js · app.css (tokens + componentes do padrão de UI) · modules/*.js
  modules-times/ (espaço Consulta Times) · obsolescencia/ · ebs-forms/
  controle-orcamento-exec/ · indicadores/ · identificacao/ · cockpit/

frontend/                Fontes React dos painéis; o build sai em static/

data/                    TUDO que é gravado em disco (fora do repositório)
  db/                    Bancos SQLite dos módulos isolados
  uploads/               Arquivos enviados pelos usuários
  referencias/           Cadastros de apoio versionados (ex.: locations_sn.json)

deploy/                  Instalação e serviços systemd
  portal_spare.service        serviço de sistema (com root)
  portal_spare.user.service   serviço de usuário (systemctl --user)
  portal.sh                   controle sem root: start/stop/status/atualizar
  instalar_usuario.sh         instalação sem root, direto do GitHub
  install.sh · generate-cert.sh

scripts/                 Utilitários de operação
  backup.sh              Backup total (Postgres + SQLite + env + uploads)
  restaurar.sh           Restauração do pacote no servidor novo
  migrar_pg_para_mysql.py

docs/                    Documentação
```

## Onde ficam os bancos

| Banco | Onde | Definido em |
|---|---|---|
| Portal (principal) | PostgreSQL | `DATABASE_URL` |
| Indicadores | `data/db/indicadores.db` | `INDICADORES_DATABASE_URL` |
| Automações | `data/db/automacoes.db` | `AUTOMACOES_DATABASE_URL` |
| Monitoramento | `data/db/monitoramento.db` | `MONITORAMENTO_DATABASE_URL` |
| Controle de Orçamento — CAPEX | `data/db/controle_orcamento_exec.db` | `ORCAMENTO_EXEC_DATABASE_URL` |

| Orçamento de Manutenção | `data/db/orcamento_manutencao.db` | `ORCAMENTO_MANUTENCAO_DATABASE_URL` |
| Obsolescência do parque (MDM) | `data/db/obsolescencia.db` | `OBSOLESCENCIA_DATABASE_URL` |
| EBS Forms | `data/db/ebs_forms.db` | `EBS_FORMS_DATABASE_URL` |
| **Ciclo do ativo** — núcleo (Trilha) | `data/db/trilha.db` | `TRILHA_DATABASE_URL` |
| Separação (A15) | `data/db/separacao.db` | `SEPARACAO_DATABASE_URL` |
| Atendimento (A20) | `data/db/atendimento.db` | `ATENDIMENTO_DATABASE_URL` |
| Bancada (A02–A04) | `data/db/bancada.db` | `BANCADA_DATABASE_URL` |
| Preparação (A06–A08) | `data/db/preparacao.db` | `PREPARACAO_DATABASE_URL` |
| Destinação (A09–A13) | `data/db/destinacao.db` | `DESTINACAO_DATABASE_URL` |
| Assistência externa (A05/A14) | `data/db/externo.db` | `EXTERNO_DATABASE_URL` |
| Projetos de loja (A16) | `data/db/projetos.db` | `PROJETOS_DATABASE_URL` |
| Logística reversa (A17) | `data/db/reversa.db` | `REVERSA_DATABASE_URL` |
| Inventário (A18) | `data/db/inventario.db` | `INVENTARIO_DATABASE_URL` |
| Regularização (A19) | `data/db/regularizacao.db` | `REGULARIZACAO_DATABASE_URL` |
| Consulta Times (liberações e acessos) | `data/db/consulta_times.db` | `CONSULTA_TIMES_DATABASE_URL` |
| Orçamento Spare (clone do Infra CSC) | `data/db/orcamento_spare_exec.db` | `ORCAMENTO_SPARE_EXEC_DATABASE_URL` |

Nenhum módulo isolado escreve no banco do portal, e vice-versa. Toda URL sai
de `config.py`; nenhum módulo monta caminho por conta própria.

**Compatibilidade:** instalações anteriores guardavam os SQLite direto em
`data/`. Se o arquivo antigo existir e o novo ainda não, ele continua sendo
usado — uma atualização nunca aponta o serviço para um banco vazio. Para
adotar o lugar novo, com o serviço parado:

```bash
sudo systemctl stop portal_spare.service
mkdir -p data/db && mv data/*.db data/*.db-wal data/*.db-shm data/db/ 2>/dev/null
sudo systemctl start portal_spare.service
```

## Onde ficam as APIs

Todas sob o prefixo `/api`, uma por área, definidas em `routers/`:

| Prefixo | Router | Prefixo | Router |
|---|---|---|---|
| `/api/auth` | auth | `/api/servicenow` | servicenow |
| `/api/consulta` | consulta | `/api/identificacao` | identificacao |
| `/api/recebimento(s)` | recebimento | `/api/indicadores` | indicadores |
| `/api/automacoes` | automacoes | | |
| `/api/status` | status | `/api/monitor` | monitoramento |
| `/api/parametros` | parametros | `/api/public-assets` | public_assets |
| `/api/lotes` `/api/dashboard` | helpers | `/api/controle-orcamento-exec` | controle_orcamento_exec |
| `/api/orcamento-spare` | orcamento_spare | `/api/cockpit` | cockpit (público) |
| `/api/orcamento-manutencao` | orcamento_manutencao | `/api/obsolescencia` | obsolescencia |
| `/api/trilha` | trilha (núcleo) | `/api/torre` | torre |
| `/api/separacao` | separacao | `/api/atendimento` | atendimento |
| `/api/bancada` | bancada | `/api/preparacao` | preparacao |
| `/api/destinacao` | destinacao | `/api/externo` | externo |
| `/api/projetos` | projetos | `/api/reversa` | reversa |
| `/api/inventario` | inventario | `/api/regularizacao` | regularizacao |
| `/consulta-times` `/api/consulta-times` | consulta_times (espaço Times) | `/orcamento-spare` `/api/orcamento-spare-exec` | orcamento_spare_exec (clone do Infra CSC) |
| `/api/ebs-forms` | ebs_forms | `/api/servicenow/correios` | correios |

O contrato dos módulos do ciclo do ativo (estados, o que cada um escreve
no ServiceNow, verificação) está em `docs/TRILHA_E_SEPARACAO.md`. Esquema:
`create_all` cria o que falta e `db/_esquema.py` acrescenta coluna nova em
tabela existente, no `init_db` de cada módulo.

As páginas que não são API (`/`, `/indicadores`, `/controle-orcamento`) são
servidas pelos seus próprios routers, com o HTML em `static/`.

`/controle-orcamento` tem **permissão exclusiva**: exige sessão do portal e
o módulo `orcamento` liberado para o usuário (`view` para ler, `create` para
incluir, `edit` para alterar/excluir/sincronizar, `admin` para a trilha de
acesso). Cada abertura de tela e cada gravação ficam registradas na tabela
`budget_acessos`, consultável em Parâmetros → Acessos & Alertas.

`/api/orcamento-spare` segue o mesmo desenho, com o módulo `orcamento_spare`.

## Telas de TV (públicas)

`/cockpit-spare`, `/dash-recebimento`, `/dash-centralreparos` e
`/dash-estoques` ficam em painel na parede e **não pedem login**. Em troca,
vale a regra inversa das demais telas: os endpoints `/api/cockpit/*` só
podem devolver **agregado** — nada de nome de colaborador, número de
chamado, série, imobilizado, loja isolada ou valor por projeto.

As quatro páginas usam o mesmo renderizador (`static/cockpit/cockpit.js`):
o servidor devolve uma lista de blocos (`kpis`, `barras`, `tabela`, `texto`)
e a página desenha. Indicador novo = consulta nova no router; a página não
muda.

## Regras para não voltar a misturar

1. Arquivo novo de banco entra em `db/`, com URL declarada em `config.py`.
2. Rota nova entra em `routers/`, nunca na raiz.
3. Cliente de sistema externo entra em `integracoes/`.
4. Nada gravado em disco fora de `data/`.
5. Nada que precise de login pode morar em `static/` — ali é público.
6. Serviço systemd e instalador entram em `deploy/`.

## Espaços e clones

- **Consulta Times** (`/consulta-times`) é o mesmo shell do portal servido
  com `<body data-espaco="times">`: o `app.js` mostra só Consulta, Entrada /
  Saída / Movimentação interna e Acessos, e cai na Consulta. Entra quem tem
  liberação por login (dada na tela Acessos por quem administra) ou a
  permissão `consulta_times`. A liberação vira permissão de sessão no login
  (`routers/auth.py`) para `consulta_times`, `consulta` e `servicenow`.
- **Orçamento Spare** (`/orcamento-spare`) carrega o fonte do Controle de
  Orçamento uma segunda vez (`routers/orcamento_spare_exec.py`) trocando só
  banco, permissão (`orcamento_spare`) e caminhos. Correção no original vale
  para os dois.
- **Central de Reparos**: a tela é `static/modules/reparos.js` (bancadas +
  dashboard medido pelo núcleo); a API continua em `/api/bancada`, com a
  permissão `reparos`.

### Ícone do portal (favicon)

`/favicon.ico` serve o ícone enviado pelo admin geral em `data/branding/`
(SVG, PNG ou ICO, até 256 KB, SVG sem script) e, na falta dele, o padrão de
`static/favicon.svg`. Todas as páginas apontam para `/favicon.ico`, então a
troca vale para o portal, obsolescência, orçamentos, cockpit e formulários
sem reiniciar nada. Só o **admin geral** altera: o login em
`ADMIN_GERAL_LOGIN` (em branco, o `INITIAL_ADMIN_LOGIN`). A tela fica em
Configuração → Visual.

### Planejamento de compras (Orçamento Spare)

Aba **Planejamento** do Orçamento Spare (só nessa instância). Banco
próprio `data/db/planejamento_spare.db` (`pln_item`, `pln_historico`,
`pln_config`), API em `/api/planejamento`, permissão pelos níveis do
próprio Orçamento Spare (view lê; edit e admin alteram).

- **Consumo real** vem da Separação: solicitações enviadas cujo tipo de
  atendimento consome o estoque de reposição (inauguração e reforma não
  entram), por mês de envio e modelo; a quantidade é o número de unidades
  bipadas, ou a quantidade pedida quando não há unidade.
- **Consumo imputado**: meses anteriores à data de início do sistema
  (configurável; em branco, o primeiro mês com envio), digitados na grade
  ou importados por CSV/XLSX (item, mês, quantidade).
- **Previsão** (`core/previsao.py`, funções puras): média simples com
  menos de 3 meses; média móvel ponderada + tendência amortecida de 3 a
  23; com 24 ou mais, vezes o índice sazonal do mês. P90 = P50 + 1,28 ×
  desvio do backtest. A tela informa o método.
- **Necessidade**: consumo previsto + segurança (dias × consumo médio) −
  estoque − pedidos em aberto; mês de ruptura e data-limite do pedido
  (ruptura − lead time); valor pelo custo unitário.
- **Estoque atual**: manual, ou contagem no ServiceNow dos modelos do
  item no estoque de reposição (consulta da Separação, como o usuário
  logado).

- **Obsolescência** (sub-aba): aparelhos obsoletos da última coleta do
  MDM por modelo × BU (o CD entra como BU própria), com plano de
  substituição por modelo (`pln_substituicao`: item substituto ou custo,
  percentual, mês alvo). Dá valor por BU e calendário de compra por mês.
- **Acordos de compra** (Configuração): item EBS, descrição, valor,
  código e nome do fornecedor ("3729 - AIDC" é aceito num campo só),
  vencimento; incluídos na tela ou por planilha CSV/XLSX
  (`pln_acordo`). Situação vigente / vence em breve / vencido, com o
  prazo do alerta configurável. Um item de planejamento com Item EBS e
  sem custo digitado usa o valor do acordo vigente.

Verificação: `python3 scripts/verificar_planejamento.py`.

### Consulta Times: espaço com módulos próprios

O espaço em `/consulta-times` é independente do portal, por decisão da
área: mexer num lado não muda o outro.

- **Telas**: `static/modules-times/` (consulta, gestao_ativos, servicenow,
  consulta_times). São cópias independentes das de `static/modules/`, não
  wrappers. `app.js` carrega dessa pasta quando `body[data-espaco=times]`.
  O preço da independência é que uma correção válida para os dois espaços
  precisa ser aplicada nos dois arquivos.
- **Configuração**: estoques, corredores e anotações ficam em `ct_config`,
  no banco do próprio espaço. A chave `gestao_ativos` do portal não é
  lida nem escrita aqui.
- **Estoques**: escolhidos numa lista vinda do ServiceNow
  (`GET /api/consulta-times/stockrooms`, tabela `alm_stockroom`), sem os
  que têm "Spare" no nome — esses são da área SPARE.
- **Sem Obsolescência**: a aba não existe em Gestão de Ativos no espaço.

### Entrada de Ativos: origem por planilha

Além da base por status e da lista digitada, a Entrada aceita planilha.
`GET /api/servicenow/entrada/planilha-modelo` devolve o XLSX modelo com as
colunas que sobem para o ServiceNow (Asset Tag, Número de Série e Modelo
são obrigatórios) e uma aba de instruções;
`POST /api/servicenow/entrada/planilha` recebe o arquivo preenchido
(XLSX ou CSV) e devolve as linhas na mesma pré-visualização das outras
origens, marcando as incompletas com o motivo.

Verificação: `python3 scripts/verificar_consulta_times.py`.

### Recebimento espelhado no ServiceNow

Ao confirmar um recebimento, o portal reflete no ServiceNow o que chegou:

- **Espaço e Corredor é obrigatório** e é pedido na tela antes de gravar.
  Recusa-se o lote inteiro antes de escrever, para não deixar recebimento
  gravado e ServiceNow pela metade.
- **Ativo que já existe** é atualizado com os dados do recebimento
  (modelo, categoria, identificador que faltava) e passa a estar em
  estoque, no `SPARE - CD324`, no espaço informado.
- **Ativo que não existe é criado** ali mesmo, com os mesmos campos.
  Antes ficava invisível até alguém lembrar da Entrada de Estoque
  (`criar_ausentes = false` volta ao comportamento antigo).
- Modelo e categoria só entram quando o ServiceNow os reconhece: mandar
  texto livre num campo de referência apagaria o valor atual.

Verificação: `python3 scripts/verificar_recebimento_sn.py`.
