# Documentação do Sistema — Portal de Operações SPARE

> Documento único para uma pessoa (ou uma IA) entender **toda** a aplicação:
> arquitetura, catálogo de telas, integrações, bancos, segurança, design,
> parâmetros e operação.
>
> Levantado a partir do código-fonte da branch `migracao-oficial`. O catálogo
> da seção 4 foi extraído do menu (`static/index.html`), das abas de cada
> módulo e dos routers — não de memória.

---

## 1. Visão geral

Plataforma web da operação **SPARE**: o ciclo de vida do ativo de TI de loja,
do recebimento à destinação. Recebimento e triagem, reparo em bancada,
identificação e etiquetagem, separação e saída, logística reversa, inventário,
venda e descarte — mais o controle de orçamento e a gestão de fornecedores em
volta disso.

| Item | Valor |
|---|---|
| Backend | Python 3.11+ · **FastAPI** + Uvicorn |
| Frontend do portal | **SPA em JavaScript puro** (sem framework), CSS próprio |
| Orçamento Infra CSC (`/controle-orcamento`) | **React**, build com esbuild, versionado em `static/` |
| Indicadores (`/indicadores`) | HTML + JS externo + SVG inline, sem bibliotecas |
| ORM / banco | **SQLAlchemy 2** — PostgreSQL no principal, SQLite nos módulos isolados |
| Porta | **8901**, um processo só |
| Publicação | `suporte.lojasrenner.com.br/portal-spare` (subcaminho do proxy) |
| Repositório | `github.com/ghost2539/CLAUDE` |

### Um processo, uma porta

O servidor não tem root, então cada serviço a mais é um problema a mais.
Tela nova é **router do portal**, nunca aplicativo com systemd próprio. As
telas que já foram aplicações separadas (Consulta de Ativos dos times,
Controle de Orçamento) hoje são routers.

```
Navegador
 └── suporte.lojasrenner.com.br/portal-spare
      ├── /                     SPA do portal (login SSO + menu)
      ├── /obsolescencia        Painel de obsolescência
      ├── /orcamento-spare      Orçamento Spare
      ├── /controle-orcamento   Orçamento Infra CSC (React)
      ├── /indicadores          Indicadores RMR
      ├── /consulta-times       Espaço dos times
      ├── /cockpit-spare        Cockpit (TV)
      ├── /dash-*               Painéis de TV
      └── /api/...              API REST
```

**Prefixo do proxy.** O portal é publicado num subcaminho. `core/prefixo.py`
resolve isso: `APP_BASE_PATH` (ou o `--root-path` do uvicorn) é injetado no
HTML como `<meta name="app-base">`, e o front monta `/static` e `/api` a
partir dele. Sem isso, o navegador buscaria tudo na raiz do domínio e a tela
apareceria crua.

**Isolamento.** Cada módulo carrega em `main.py` dentro do próprio
`try/except`: erro em um **nunca** derruba o portal. Cada módulo isolado tem
banco próprio.

---

## 2. Acesso e autenticação

- **Login só por SSO** (Logon AD / loginsso). Não existe senha no portal:
  nenhum campo, nenhum hash, nenhuma troca de senha.
- **Liberação prévia.** SSO válido não basta — o usuário precisa estar
  liberado (`allowed`) por um administrador.
- **Sessão deslizante.** `SESSION_TTL_MINUTES` (padrão 480) é a janela de
  **ociosidade**: cada requisição reinicia a contagem. `SESSION_MAX_HOURS`
  (padrão 24) é o teto absoluto, assinado no cookie. Antes o prazo corria
  desde o login e derrubava quem estava no meio do trabalho.
- **Permissão por módulo e ação.** `require_permission(req, "<módulo>",
  "<ação>")` em toda rota. As ações de cada módulo estão em
  `config.py: MODULE_ACTIONS` — a tela de permissões só oferece essas.
- **`admin` de módulo administra o módulo.** Usuários, liberações e o flag
  `is_admin` são só de administrador do portal (`require_admin`). Mudança de
  permissão reflete na sessão viva.
- **Escrita no ServiceNow sempre como o usuário logado** (cookies da sessão).
  A conta de serviço só **lê**.

---

## 3. Design

**Padrão de UI SPARE** — contrato completo em `docs/PADRAO_UI_SPARE.md`.

- Paleta LRSA 2025, aplicada por tokens `--sp-*`. Nenhum hex fora da paleta
  em módulo nenhum.
- **Arial em todo o sistema**; dígitos alinhados com `font-variant-numeric:
  tabular-nums`. Monoespaçada (`--sp-mono`, classes `.om-mono` / `.om-pre`)
  só onde alinhar caractere a caractere importa: SQL, chave de cofre,
  identificador.
- **Estrutura reta, controles arredondados**: card, tabela, KPI e shell com
  `border-radius: 0`; botão 7px, campo 6px, chip 6px, selo 5px.
- **Uma sombra só** no sistema inteiro: a do botão primário.
- Sidebar 236px preta nos dois temas, sem numeração. Header 62px.
- **Tema claro e escuro**, com a escolha guardada no perfil
  (`PUT /api/auth/preferencias`) — sobrevive a sair e entrar de novo.
  `localStorage` só como cache, para não piscar na abertura.
- Nada de fonte, script ou folha externa: a política de conteúdo (CSP) só
  libera o próprio domínio, e `<script>` inline é bloqueado.

**Exceção combinada:** o Orçamento Infra CSC (React) fica fora do padrão de
design, por decisão de quem pediu a migração.

---

## 4. Catálogo de telas

Toda tela do menu lateral, na ordem em que aparece. A coluna *Permissão* é a
chave de `MODULE_ACTIONS`; sem ela, o item nem aparece na barra.

### Início

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Torre de Controle** | `#torre` | `torre` | Área · Tendência · Trilha do ativo · Minha produção · **Equipe** (só admin) |
| **Consulta** | `#consulta` | `consulta` | — (busca em lote até 1000 e individual; cruza base local + EBS + classificação; exporta `.xlsx`) |

### Entrada

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Recebimento** | `#recebimento` | `recebimento` | Novo Recebimento · Base de Recebimentos · Dashboard · Lotes · Cadastro de modelos |
| **Agendamentos Forn.** | `#agendamentos_forn` | `agendamentos_forn` | — |
| **Internalização** | `#internalizacao` | `internalizacao` | — |
| **Identificação** | `#identificacao` | `identificacao` | Gerar Lote · Identificação A4 · Impressão Zebra Livre · Impressoras |

**Recebimento — Novo Recebimento** começa pela **origem da entrada**, e a
escolha muda o fluxo inteiro:

- **Reversa** — devolução da loja. O ativo já existe no EBS e entra pela
  leitura: fluxo de sempre, com detecção de duplicidade CM/YC.
- **Fornecedores** — compra nova, que não existe no EBS. O operador digita
  **descrição do item, serial number, PO e NF** de cada item, e nenhum dos
  quatro é opcional. PO e NF ficam entre os itens da mesma sessão.

Origem, PO e NF ficam no ciclo (`receipt_cycles.origem_entrada/po/nf`) e
aparecem na Base. **Uma sessão é de uma origem só** — compra e devolução não
se misturam no mesmo lote. Cada mudança gera Movimento (auditoria).

**Agendamentos Forn.** registra a entrega combinada com o fornecedor: BU, NF,
PO, volumes, estoque de destino e a lista de equipamentos. **Internalização**
é o que entrou por agendamento, pronto para conferência e exportação.

### Atendimento

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Atendimento** | `#atendimento` | `atendimento` | Frente e Retaguarda · Mobilidade |
| **Logística Reversa** | `#reversa` | `reversa` | Em aberto · Encerradas |
| **Preparação** | `#preparacao` | `preparacao` | Configuração · Montagem de sled · Internalização |

### Central de Reparos

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Frente e Retaguarda** | `#reparos/loja` | `reparos` | (a bancada escolhida) + Dashboard |
| **Mobilidade** | `#reparos/frota` | `reparos` | idem |
| **Conectividade** | `#reparos/conectividade` | `reparos` | idem |
| **Assistência Externa** | `#externo` | `externo` | Assistência externa · Devolução a terceiros |

As três bancadas são a mesma tela com a fila filtrada. Registro de reparo
grava tempos, técnico, resultado e **saving** (valor-hora × tempo). Destinos
de saída: Internalização, Aguardando peças, Assistência externa, Reparo
inviável.

### Saída

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Separação** | `#separacao` | `separacao` | Frente e Retaguarda · Mobilidade · Inauguração e Reforma |
| **Projetos de Loja** | `#projetos` | `projetos` | Fila por etapa · Projetos |
| **Venda de Ativos** | `#venda` | `venda` | Fila de venda · Ciclos · Painel |
| **Destinação** | `#destinacao` | `destinacao` | Descaracterização · Lotes |
| **Correios** | `#rastreio` | `rastreio` | — (rastreio individual e em lote, com comprovante) |

### Gestão de Ativos

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Inventário** | `#inventario` | `inventario` | — |
| **Regularização** | `#regularizacao` | `regularizacao` | Em aberto · Minhas · Encerradas |
| **Entrada de Ativos** | `#gestao_ativos/entrada` | `servicenow` | Entrada de estoque |
| **Saída de Ativos** | `#gestao_ativos/saida` | `servicenow` | Saída de estoque |
| **Movimentação Interna** | `#gestao_ativos/movimentacao` | `servicenow` | Movimentação Interna |
| **Obsolescência** | `/obsolescencia` | `obsolescencia` | — (página própria; a aba homônima em Gestão de Ativos abre esta mesma página em outra janela) |

As três telas de Gestão de Ativos são o módulo `gestao_ativos`, que carrega
`servicenow.js` sob demanda. Abas do conjunto: Entrada de estoque · Saída de
estoque · Movimentação Interna · Obsolescência. A **Saída** trabalha em lote:
cola-se a lista de identificadores e edita-se por linha o destino, o status, o
**corredor/espaço** (`aisle_space_location`) e a observação, com a opção
"aplicar a todos".

### Gestão de Fornecedores

| Tela | Rota | Permissão | Abas |
|---|---|---|---|
| **Brasil — Manutenção** | `#orcamento_manutencao` | `orcamento_manutencao` | Painel · Reparos |

### Controle

| Tela | Rota | Permissão | Observação |
|---|---|---|---|
| **Orçamento Spare** | `/orcamento-spare` | `orcamento_spare` | Página própria. Aba Planejamento de compras. |
| **CAPEX Spare** | `#capex_spare` | `capex_spare` | Projetos de investimento e linhas de item: catálogo, acordo de compra, NCM/TIPI e leitura da NF em PDF. |
| **Orçamento Infra CSC** | `/controle-orcamento` | `orcamento` | Página própria, em React. **Fora do padrão de design**, por decisão. |

> **Orçamento Spare e CAPEX Spare são produtos diferentes** que vieram de
> branches diferentes e disputavam a mesma chave de permissão. Cada um tem
> hoje chave, rota e banco próprios. Se as duas telas devem continuar
> existindo é decisão de quem usa — veja `docs/MIGRACAO_OFICIAL.md`.

### Rodapé da barra lateral

| Tela | Rota | Permissão |
|---|---|---|
| **Status** | `#status` | `status` |
| **Configuração** | `#parametros` | `parametros` |
| **Acesso Consulta Times** | `#consulta_times` | `consulta_times` |
| **Minha conta** | `#parametros/conta` | `parametros` |
| **Sair** | — | — |

### Configuração — as 15 abas

| Aba | Rota | Só admin | O que faz |
|---|---|:-:|---|
| Visual | `#parametros/visual` | ✓ | Nome do portal e rodapé |
| Locais | `#parametros/locais` | | Cadastro de locais |
| Classificações | `#parametros/classificacoes` | | Modelos e classificação de ativo |
| Usuários e Permissões | `#parametros/permissoes` | ✓ | Cria usuário SSO, libera, define admin, permissão por módulo/ação |
| Sequências | `#parametros/sequencias` | ✓ | Numeração automática |
| Configuração Módulos | `#parametros/config-modulos` | ✓ | Liga/desliga e parametriza módulos |
| Ciclo do ativo | `#parametros/separacao` | ✓ | Estados e transições do ativo |
| Automações | `#parametros/automacoes` | | Situação, logs e o botão de execução |
| Cofre de segredos | `#parametros/cofre` | ✓ | De onde vem cada chave, o dono e a permissão do arquivo do cofre — **nunca o valor** |
| Gestão de Compras | `#parametros/compras` | ✓ | Ponte HTTP com o módulo `/gestao_compras` |
| Base EBS | `#parametros/base-ebs` | ✓ | Leitura direta da base do EBS: consultas nomeadas, só-leitura, catálogo |
| Monitoramento | `#parametros/monitoramento` | ✓ | Saúde, checagem de integrações, falhas registradas |
| Acessos & Alertas | `#parametros/acessos` | ✓ | Trilha de acesso e alertas por e-mail |
| Dashboards | `#parametros/dashboards` | ✓ | Painéis de TV |
| Minha conta | `#parametros/conta` | | Tema e dados do próprio usuário |

**Automações** é a única aba fora da regra: quem não é admin vê a situação,
os logs e o botão de execução; a configuração (credencial, cofre, horários)
segue só do admin. **A automação roda apenas pelo botão**, com o login de
quem clicou — não há agendador.

### Páginas fora do menu

| Página | Acesso | O que é |
|---|---|---|
| `/consulta-times` | `consulta_times` | Espaço dos times: Entrada, Saída e Movimentação com listas próprias de estoque e corredor |
| `/indicadores` | `parametros` | Indicadores RMR — dashboard executivo |
| `/cockpit-spare` | pública | Cockpit para TV |
| `/dash-recebimento`, `/dash-estoques`, `/dash-centralreparos` | públicas | Painéis de TV |

As telas de TV são **públicas de propósito**, e por isso os endpoints
`/api/cockpit/*` só devolvem agregado — nunca dado de colaborador, chamado,
série ou loja isolada.

**Indicadores RMR** tem banco próprio (`indicador_snapshot`) e lê do
ServiceNow pela conta de serviço (REST, só leitura, Aggregate API — sempre
GET). Mostra: tickets resolvidos por mês e SLA (das ANS `task_sla` cujo nome
contém SPARE), abertos por mês, Top 20 lojas, Top 10 subcategorias, e o TMA
de Coletor e SLED (média de dias entre o campo de início e a resolução).

---

## 5. Integrações externas

Todas em `integracoes/`, sem rota e sem banco. **Toda saída HTTP passa por
`integracoes/http.py`**, com TLS verificado.

| Integração | Arquivo | O que faz |
|---|---|---|
| **EBS — API REST** | `ebs_service.py` | Login AD e consulta de ativos. Sem resposta, cai para a base local (`local_assets`). |
| **EBS — raspagem autenticada** | `ebs_logged.py` | O que a API REST não expõe. |
| **EBS — RPA no Forms** | `ebs_forms.py` | Cliente Oracle Forms dirigido por robô, para o que só existe na tela. |
| **EBS — base de dados** | `ebs_oracle.py` | Leitura direta, só-leitura, com consultas nomeadas. Credencial no cofre. |
| **Gestão de Compras** | `gestao_compras.py` | PO, projetos e acordos, por HTTP, pelo módulo `/gestao_compras`. |
| **ServiceNow** | `routers/servicenow.py` | Entrada, saída, movimentação, rastreio e encerramento. |
| **Correios** | `routers/correios.py` | Rastreio e comprovante de postagem. |
| **MDM de Coletores** | `mdm_airwatch.py` | Console Workspace ONE / AirWatch, para a obsolescência. |

### Dois caminhos para PO e projetos

O mesmo dado chega por dois caminhos, e a escolha é **operacional**:

- **Direto** — `integracoes/ebs_oracle.py`, quando o serviço alcança a base;
- **Por HTTP** — `integracoes/gestao_compras.py`, quando não alcança: o
  módulo do outro time consulta e devolve JSON.

As consultas são **as mesmas**, com os mesmos nomes e os mesmos binds, de
propósito: `saldo`, `po`, `rc`, `acordos`, `vendor_lookup`, `vendor_items`,
`busca_po`, `catalogo`.

### ServiceNow — quem escreve e quem lê

- **Escrita sempre como o usuário logado**, com os cookies da sessão SSO.
  Entrada, saída, movimentação e encerramento.
- **Leitura pode usar a conta de serviço** (`SN_API_USER` / `SN_API_PASS`,
  resolvidas pelo cofre), na Table e na Aggregate API. Essas APIs só aceitam
  **GET**.
- `sys_id` vindo do cliente passa por `sys_id_valido` (32 hexadecimais);
  `_sn_update` recusa o resto.
- Valor de usuário em consulta passa por `termo_sn()`: `^ = , !` mudam o
  significado de uma encoded query, e uma reserva no `sys_id` errado é o
  custo de não sanitizar.
- Tabelas usadas: `alm_hardware`, `alm_stockroom`, `incident`, `task_sla`.
- **Keep-alive** da sessão do ServiceNow, com retomada ao voltar de aba
  suspensa (`visibilitychange` + `online`).

---

## 6. Bancos de dados

O principal é PostgreSQL. Cada módulo isolado tem **banco próprio**, por
padrão um arquivo SQLite em `data/db/<nome>.db`, declarado em `config.py` com
`_sqlite()`. Nenhum módulo monta caminho por conta própria, e nenhum escreve
no banco de outro.

| Banco | Módulo |
|---|---|
| `portal` | Principal: usuários, recebimentos, ciclos, movimentos |
| `agendamentos_forn` · `internalizacao` · `preparacao` | Entrada e preparação |
| `atendimento` · `bancada` · `externo` · `reversa` | Atendimento e reparos |
| `separacao` · `projetos` · `venda` · `destinacao` | Saída |
| `inventario` · `regularizacao` · `trilha` | Gestão de ativos |
| `orcamento_manutencao` · `orcamento_exec` · `capex_spare` · `planejamento` | Orçamento |
| `obsolescencia` · `indicadores` · `consulta_times` · `ebs_forms` | Painéis e espaços |
| `automacoes` · `monitoramento` | Operação |

`db/_esquema.py: migrar_colunas()` acrescenta coluna nova a tabela existente
sem perder dado — é como `origem_entrada`, `po` e `nf` entraram no ciclo de
recebimento.

---

## 7. Segredos e segurança

**Ordem de busca de todo segredo**: cofre corporativo → cofre local cifrado →
variável de ambiente. `core/cofre.py`.

- **Segredo nunca no código nem no git.** No repositório fica o **nome** da
  chave; o valor vem do cofre.
- **Dado de acesso a banco é segredo** — host, porta, instância, SID, esquema
  e usuário, não só a senha. Mensagem de erro de driver costuma trazer o
  endereço dentro: `routers/ebs_oracle.py::_limpo` apaga antes de mostrar.
- **SQL não vem da tela.** Consulta ao banco é nomeada, mora no código e usa
  bind variables. "Começa com SELECT" não é proteção.
- **TLS verificado em toda saída.** `verify=False` no código é **proibido**;
  desligar é `VERIFY_SSL=false` no ambiente, e fica em log. Proxy que
  intercepta o TLS pede `PORTAL_CA_BUNDLE` — nunca desligar a conferência.
- **Proxy declarado vazio é decisão** e encerra a busca (`config._proxy`).
  Encadear com `or` faria o `https_proxy` do perfil do servidor entrar sem
  ninguém configurar.
- **Nada gravado fora de `data/`.**
- **Nada que exija login mora em `static/`** — ali é público.
- **CSP** é `script-src 'self'`: `<script>` inline é bloqueado; JS de página
  sempre em arquivo externo.
- Cookie de sessão **Secure** quando o pedido chega por HTTPS (direto ou por
  `X-Forwarded-Proto` de proxy confiável), com **HSTS**.
- **Rate limit** no login (5/min) e na API (120/min).

A aba **Cofre de segredos** mostra, para cada chave: se resolveu, de qual
fonte veio, o tamanho, e se está **sombreada** (existe em mais de uma fonte —
a falha silenciosa clássica, em que um valor velho no cofre local derruba o
certo). Nenhum valor de segredo aparece. Mostra também dono, grupo e
permissão do arquivo do cofre, que é o que se pede ao time quando o serviço
não alcança.

---

## 8. Parâmetros de ambiente

Modelo completo em `.env.example`. No servidor, o arquivo é lido pelo systemd
(`EnvironmentFile=`) — comentário **só em linha própria**, senão o texto
entra no valor.

| Chave | Padrão | Para quê |
|---|---|---|
| `DATABASE_URL` | — | Banco principal. Aceita `@cofre:NOME@` no lugar da senha. |
| `PORTAL_SESSION_SECRET` | — | Assinatura do cookie. |
| `SESSION_TTL_MINUTES` | 480 | Janela de ociosidade. |
| `SESSION_MAX_HOURS` | 24 | Teto absoluto da sessão. |
| `SESSION_COOKIE_SECURE` | auto | `auto` liga quando o pedido chega por HTTPS. |
| `APP_BASE_PATH` | "" | Subcaminho do proxy (ex.: `/portal-spare`). |
| `API_BARRA_FINAL` | "" | Remendo para proxy que responde 301 ao `/api/...` sem barra. |
| `HOST` / `PORT` | `0.0.0.0` / 8901 | |
| `VERIFY_SSL` | true | Desligar é decisão explícita, e fica em log. |
| `PORTAL_CA_BUNDLE` | — | CA corporativa, para proxy que intercepta TLS. |
| `COFRE_CORPORATIVO` | sim | Liga a busca no cofre corporativo. |
| `PORTAL_COFRE_DIR` | `~/.config/portal-spare` | Cofre local cifrado. |
| `SN_API_BASE` | `renner.service-now.com` | |
| `SN_PROXY` / `SN_API_PROXY` | — | Proxy de saída. **Declarar vazio é decisão.** |
| `SN_INDIC_REFRESH_MIN` | 0 | Recálculo dos indicadores, em minutos. |
| `GESTAO_COMPRAS_URL` / `_TIMEOUT` / `_VERIFY` / `_PROXY` | — | Ponte com o módulo de compras. |
| `<MODULO>_DATABASE_URL` | `data/db/<modulo>.db` | Banco de cada módulo isolado. |
| `RATE_LIMIT_LOGIN` / `RATE_LIMIT_API` | 5/min · 120/min | |

**Credenciais não vão aqui** — vão no cofre:
`CORREIOS_USUARIO` · `CORREIOS_CHAVE` · `CORREIOS_CARTOES` ·
`SN_API_USER` · `SN_API_PASS` · `GESTAO_COMPRAS_USER` · `GESTAO_COMPRAS_PASS` ·
`ORACLE_EBS_DSN` · `ORACLE_EBS_USER` · `ORACLE_EBS_PASS` · `MDM_USUARIO` ·
`MDM_SENHA` · `SMTP_SENHA` · `PUBLIC_ASSETS_TOKEN`.

```bash
python3 scripts/cofre.py definir <CHAVE>
```

---

## 9. Mapa da API

| Prefixo | Assunto |
|---|---|
| `/api/auth` | Login SSO, sessão, preferências |
| `/api` | Consulta, recebimento, status |
| `/api/servicenow` · `/api/servicenow/encerramento` | ServiceNow e Correios |
| `/api/identificacao` | Etiquetas e impressoras |
| `/api/atendimento` · `/api/bancada` · `/api/externo` · `/api/reversa` | Atendimento e reparos |
| `/api/preparacao` · `/api/agendamentos-forn` · `/api/internalizacao` | Entrada e preparação |
| `/api/separacao` · `/api/projetos` · `/api/venda` · `/api/destinacao` | Saída |
| `/api/inventario` · `/api/regularizacao` · `/api/trilha` · `/api/torre` | Gestão de ativos |
| `/api/orcamento-manutencao` · `/api/capex-spare` · `/api/planejamento` | Orçamento |
| `/api/gestao-compras` · `/api/ebs-oracle` · `/api/ebs-forms` | EBS |
| `/api/cofre` | Diagnóstico do cofre (nunca devolve valor) |
| `/api/parametros` · `/api/automacoes` · `/api/monitor` | Administração |
| `/api/public-assets` | Consulta para sistema externo: exige sessão **ou** o token `PUBLIC_ASSETS_TOKEN` no cabeçalho `X-Api-Key`. Sem um dos dois, 401. |

---

## 10. Operação

```bash
# subir local
uvicorn main:app --host 0.0.0.0 --port 8901

# serviço
deploy/portal.sh start|stop|status|atualizar

# backup e restauração
scripts/backup.sh          # bancos + ambiente + uploads
scripts/restaurar.sh
```

- Serviço systemd em `deploy/` — com root (`portal_spare.service`) ou sem
  root (`portal_spare.user.service`).
- Ambiente do servidor novo: `deploy/environment.servidor-novo`, permissão
  600, **sem senha dentro** (marcador `@cofre:NOME@`).
- Saída de rede necessária: ServiceNow (443), loginsso (443), Correios (443),
  EBS corporativo (80 e 443), `suporte.lojasrenner.com.br` (443), base de
  dados do EBS (1521) e o relay SMTP interno (25 ou 587).

---

## 11. Verificação

Cada assunto tem um script que o prova. Todos imprimem o placar e saem com
código diferente de zero se algo falhar.

```bash
for f in scripts/verificar_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```

| Script | Guarda |
|---|---|
| `verificar_seguranca.py` | TLS, sessão, permissões, sem senha local, `sys_id` |
| `verificar_migracao.py` | O que foi removido de propósito não volta num merge |
| `verificar_ui.py` | Padrão de design: fonte, raio, tokens, sem recurso externo |
| `verificar_cofre_diagnostico.py` | A tela do cofre diz a verdade e não vaza valor |
| `verificar_ebs_oracle.py` | Credencial pelo cofre, só consulta nomeada, sem dado de acesso na tela |
| `verificar_gestao_compras.py` | O protocolo da ponte HTTP, contra um stub do módulo real |
| `verificar_prefixo.py` | O portal servido num subcaminho do proxy |
| demais `verificar_*.py` | Um por módulo funcional |

---

## 12. Documentos relacionados

| Arquivo | Assunto |
|---|---|
| `Diretrizes do Projeto.md` | As regras que não se negociam |
| `docs/ESTRUTURA.md` | O papel de cada pasta |
| `docs/PADRAO_UI_SPARE.md` | O padrão de design, em detalhe |
| `docs/MIGRACAO_OFICIAL.md` | A junção das quatro branches: o que entrou, o que não, e por quê |
| `docs/MIGRACAO_SERVIDOR_NOVO.md` | Migração para o servidor novo |
| `docs/SERVICO_SERVIDOR_NOVO.md` | Serviço, ambiente e saída de rede |
| `docs/FLUXO_DO_ATIVO.md` | O ciclo do ativo, estado a estado |
| `docs/TRILHA_E_SEPARACAO.md` | Trilha e separação |
| `docs/ORCAMENTO_MANUTENCAO.md` | Orçamento de manutenção |
| `docs/CORREIOS_SERVICENOW.md` | Correios e ServiceNow |
| `docs/MDM_OBSOLESCENCIA.md` | MDM de coletores e obsolescência |
| `docs/EBS_FORMS.md` | RPA sobre o Oracle Forms |
| `docs/AMBIENTE_TESTES.md` | Ambiente de testes |
