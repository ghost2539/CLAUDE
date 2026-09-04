# Documentação do Sistema — Portal de Operações SPARE

> Documento de referência completo: arquitetura, telas, funcionalidades,
> integrações, modelo de dados e operação. Gerado a partir do código-fonte
> (branch `main`).

---

## 1. Visão geral

O **Portal de Operações SPARE** é a plataforma web da operação SPARE (gestão do
ciclo de vida de ativos de TI de loja: recebimento, triagem, reparo,
identificação/etiquetagem, saída de estoque e chamados). Ele concentra em um só
lugar as integrações com **EBS (Oracle E-Business Suite)**, **ServiceNow** e
**Correios**, além de painéis de TV e indicadores gerenciais.

| Item | Valor |
|---|---|
| Backend | Python 3.11+ / **FastAPI** + Uvicorn |
| Frontend principal | SPA em **JavaScript puro** (sem framework), CSS próprio |
| Módulo Controle de Orçamento | **React** (build gerado e versionado) |
| ORM / Banco | **SQLAlchemy 2** — PostgreSQL (atual) / **MySQL–MariaDB** (servidor novo) |
| Porta padrão | **8901** |
| Serviço | systemd `portal_spare.service` |
| Caminho (servidor novo) | `/var/www/vcreports/portal-spare` |

### Arquitetura em uma frase

Um único serviço FastAPI serve: (a) a **SPA do portal** (com login e menu), (b) três
**páginas autônomas** fora do menu (`/tv`, `/tv2`, `/indicadores`) e (c) toda a
**API REST** (`/api/...`). Dois módulos rodam no mesmo processo mas com **banco de
dados próprio e isolado**: Controle de Orçamento e Indicadores.

```
Navegador
   │
   ├── /                 → SPA (login + módulos)            ── banco PORTAL
   ├── /tv               → Painel de TV (operação)          ── banco PORTAL
   ├── /tv2, /controle-orcamento → Controle de Orçamento    ── banco ORCAMENTO (isolado)
   ├── /indicadores      → Indicadores RMR                  ── banco INDICADORES (isolado)
   └── /api/...          → API REST
            │
            ├── EBS (Oracle E-Business Suite)   — consulta de ativos + login AD
            ├── ServiceNow (renner.service-now.com) — entrada/saída/encerramento/relatórios
            └── Correios (API oficial)          — rastreio e comprovantes
```

---

## 2. Acesso e autenticação

Tela de login com **três tipos de acesso** (botões no topo do formulário):

| Botão | `auth_type` | O que faz |
|---|---|---|
| **Logon AD** | `AD` | Valida no **EBS/AD** corporativo (`ebs_service.login`). Cria o usuário no primeiro acesso. |
| **Logon Rede** | `SSO` | Valida no **loginsso** corporativo (Oracle Access Manager). **Só entra quem um administrador liberou** (lista de permissão por usuário de rede). |
| **Logon Local** | `LOCAL` | Usuário/senha guardados no próprio portal (hash **bcrypt**). Permite troca de senha pela tela "Minha conta". |

Existe ainda o tipo interno `SN` (autenticação direta ServiceNow), usado por
fluxos que precisam de sessão ServiceNow.

### Regras de segurança
- **Sessão** por cookie assinado (`itsdangerous`), `HttpOnly`, expira em **480 min**
  (configurável). As sessões ficam em memória do processo (`SESSIONS`).
- **Bloqueio por tentativas** (Local): 5 senhas erradas ⇒ 15 min bloqueado.
- **Rate limit**: login `5/min`, API `120/min` (por IP).
- **Liberação SSO (allow-list)**: usuários de rede nascem *sem* liberação
  (`allowed=false`) e só entram após um admin liberar em **Parâmetros → Usuários
  e Permissões**. O admin inicial (`INITIAL_ADMIN_LOGIN`) é sempre liberado.
- **Sessão ServiceNow**: ao logar por AD/SSO, o portal guarda os cookies do
  ServiceNow do usuário. Ações de **escrita** (entrada, saída, encerramento)
  ocorrem **como o usuário logado** — nunca com conta de serviço. Há endpoints
  para checar (`/api/auth/sn-session`) e refazer (`/api/auth/sn-relogin`) essa
  sessão sem deslogar do portal.

### Permissões
Cada usuário tem permissões **por módulo**: `can_view`, `can_create`, `can_edit`,
`can_export`, `can_admin`. **Administradores** têm acesso total a todos os módulos.
O menu lateral só mostra os módulos que o usuário pode ver.

---

## 3. Módulos do portal (menu lateral)

A SPA tem 9 entradas no menu. Módulos com várias telas usam **sub-abas**.

### 3.1 Bem-vindo
Página inicial com boas-vindas e atalhos. Sem escrita.

### 3.2 Consulta
Busca de ativos por qualquer identificador (imobilizado, ativo, etiqueta, série).
- Busca **em lote** (cola vários identificadores; deduplica; até 1000).
- Busca **individual**.
- Cruza com a base local, com o EBS e aplica as **regras de classificação**
  (categoria/modelo) cadastradas.
- **Exporta** o resultado em **Excel (.xlsx)**.

Endpoints: `POST /api/consulta`, `GET /api/consulta/single`, `POST /api/consulta/export`.

### 3.3 Recebimento
O coração do fluxo de entrada de equipamentos. Sub-abas:

| Sub-aba | Função |
|---|---|
| **Novo Recebimento** | Escaneia/insere identificadores, faz *preview* (busca dados no EBS/base), classifica e grava o ciclo de recebimento. Detecta duplicidade (prefixos CM/YC). |
| **Base de Recebimentos** | Lista, filtra, edita o ativo do ciclo, atualiza status/local e exclui recebimentos. |
| **Dashboard** | Indicadores de recebimento (por período, status, categoria). |
| **Lotes** | Geração e gestão de lotes (numeração automática por prefixo). |
| **Cadastro de modelos** | Regras de classificação (descrição → categoria/modelo). |
| **Importar base histórica** | Importa histórico de recebimentos por planilha. |
| **Base local EBS** | Sobe/atualiza a base local de ativos (fallback do EBS) por upload. |

Cada mudança de status/local gera um registro em **Movimentos** (auditoria).
Endpoints principais: `POST /api/recebimento/scan|preview|bulk-submit`,
`GET/PUT/DELETE /api/recebimentos...`, `GET /api/recebimentos/dashboard`,
`POST /api/lotes`, `GET/PUT /api/lotes/sequencias`.

### 3.4 Identificação (etiquetagem)
Impressão de etiquetas e caixas. Sub-abas:

| Sub-aba | Função |
|---|---|
| **Gerar Lote** | Gera etiquetas em lote a partir de ativos/recebimentos, com sequência automática. |
| **Identificação A4** | Gera PDF A4 para impressão comum (`/api/identificacao/a4.pdf`) ou envio direto. |
| **Impressão Zebra Livre** | Impressão livre em impressora **Zebra** (ZPL), com *preview*. |
| **Impressoras** | Cadastro/gestão de impressoras de rede (host/porta) e teste de conexão. |

Endpoints: `.../gerar-lote`, `.../preview-lote`, `.../reimprimir-caixa`,
`.../zebra-livre`, `.../a4.pdf`, `.../a4.print`, `.../printers`, `.../test-printer`.

### 3.5 ServiceNow
Integração operacional com o ServiceNow. Sub-abas:

| Sub-aba | Função |
|---|---|
| **Entrada de estoque** | Envia os ativos recebidos para o **`alm_hardware`** do ServiceNow (via SSO + JSONv2), com status de instalação, depreciação e demais campos. Processamento assíncrono (jobs com barra de progresso). |
| **Saída de estoque** | **Fluxo em lote**: cola-se uma lista de identificadores (vírgula/quebra de linha, sem limite), o sistema busca todos, e cada linha é editável na própria lista (destino/local, status, **corredor/espaço**, observações). Há um *flag* "aplicar a todos" para pré-preencher todas as linhas com o mesmo destino/status/corredor/observação. Grava no ServiceNow (`aisle_space_location`, etc.). |
| **Rastreio - Chamados** | Lista chamados de Correios e correlaciona com código de rastreio. |
| **Relatórios** | Relatórios operacionais (tickets por período/prioridade, SLA, TMA) e atualização do painel de TV. |

O campo **Corredor/Espaço** (`aisle_space_location`) foi adicionado à saída — tanto
no formulário quanto no fluxo automático de upload. **A automação de encerramento
de chamados não é afetada por esse fluxo.**

Endpoints: `POST /api/servicenow/upload` + `GET /jobs/{id}` (entrada),
`POST /saida/search|search_lote|move`, `GET /saida/locations`,
`GET /incidents|incidents/count`, `GET /relatorios/tickets|sla|tma`,
`POST /relatorios/refresh-tv`, `POST /test-login`, `GET /proxy-check`.

### 3.6 Correios
Rastreamento e encerramento. Sub-abas:

| Sub-aba | Função |
|---|---|
| **Rastreios** | Rastreia objetos (individual e **em lote**), mostra eventos e baixa **comprovante de entrega**. |
| **Encerramento** | **Encerramento automático** de chamados de Correios **entregues**: lista candidatos (chamados On Hold/In Progress com código de rastreio) e, confirmando a entrega pelos Correios, encerra no ServiceNow seguindo a transição exigida (On Hold → In Progress → Resolved) com código e nota-padrão de encerramento. |

Endpoints: `GET /correios/rastrear/{codigo}`, `GET /correios/comprovante/{codigo}`,
`POST /correios/rastrear-lote`, `POST /correios/test`,
`GET /encerramento/candidatos`, `POST /encerramento/executar`.

> As **credenciais dos Correios** vêm do **cofre `vcreports_secret`** (servidor
> novo); há *fallback* para variável de ambiente só na transição.

### 3.7 Central de Reparos
Registro de reparos e produtividade. Sub-abas:

| Sub-aba | Função |
|---|---|
| **Registro de Reparo** | Registra tempos (triagem, reparo, pesquisa, higienização), técnico, resultado e calcula o **saving** (valor-hora × tempo). Atualiza o status do ciclo conforme o resultado. |
| **Tratativa de saldos** | Tratamento de saldos/pendências. |
| **Dashboard Reparos** | Indicadores de reparo e saving. |

Endpoints: `POST /api/reparos`, `GET /api/reparos/dashboard`.

### 3.8 Status
**Status das Integrações**: verifica a saúde das conexões (EBS, ServiceNow,
Correios, banco) e um resumo geral. Endpoints: `GET /api/status`,
`GET /api/dashboard/summary`.

### 3.9 Parâmetros
Administração do sistema. Sub-abas (algumas só para admin):

| Sub-aba | Admin? | Função |
|---|---|---|
| **Visual** | sim | Nome do app, cores, fonte, logo, textos de login/rodapé. |
| **Locais** | não | Cadastro de locais de armazenagem. |
| **Classificações** | não | Regras descrição → categoria/modelo. |
| **Valor-hora** | não | Valor-hora usado no cálculo de saving. |
| **Usuários e Permissões** | sim | Cria usuários (Local/SSO), define permissões por módulo, **libera acesso SSO** (allow-list), ativa/desativa, define admin. |
| **Sequências** | sim | Sequências de numeração de lotes. |
| **TV** | não | Configura o painel de TV (título, intervalo, widgets). |
| **Minha conta** | — | Troca de senha (apenas Logon Local). |

Endpoints: `GET/POST/PUT/DELETE /api/parametros/...` (locais, classificacoes,
valor-hora, config/{key}, visual/logo, permissoes, controle-acesso, usuarios,
sequencias, base-local/upload).

---

## 4. Páginas autônomas (fora do menu)

Acessadas direto pela URL, sem passar pelo menu do portal.

### 4.1 `/tv` — Painel de Operações (TV)
Dashboard leve para exibir em TV: estatísticas, gráficos (categoria, status) e
últimos recebimentos. Atualiza sozinho em intervalo configurável.
- `/tv?demo=1` → preenche com **dados aleatórios** para validação visual.
- Fonte de dados: banco do portal (`GET /api/tv/dashboard`, `GET /api/tv/dashboard`).

### 4.2 `/tv2` e `/controle-orcamento` — Controle de Orçamento (CAPEX/OPEX)
Dashboard **React** de acompanhamento de orçamento de portfólio.
- **Módulo oculto**: não aparece no menu; acesso direto pela URL.
- **Acesso livre** (sem login), como consulta pública; grava quem alterou
  (usuário logado ou IP). Escritas passam por rate limit de API.
- **Banco de dados exclusivo** (`database_orcamento.py`, por padrão SQLite
  `data/controle_orcamento.db`; MySQL no servidor novo).
- Tabelas: `budget_projects` (projeto, CAPEX/OPEX, categoria, área, estágio,
  prioridade, orçamento aprovado/comprometido/realizado, prazo) e
  `budget_categories`.
- Assets (`static/controle-orcamento/app.js`) são o **build** de
  `frontend/controle-orcamento` — versionados no repositório porque o servidor
  não tem Node.js.

Endpoints: `GET/POST/PATCH/DELETE /api/controle-orcamento/projetos...`,
`.../categorias...`, `GET /api/controle-orcamento/sessao`.

### 4.3 `/indicadores` — Indicadores RMR
Painel dos indicadores mensais da apresentação **RMR** da operação SPARE.
- **Módulo isolado**: código próprio; **banco próprio** (`database_indicadores.py`,
  tabela `indicador_snapshot` com o JSON de cada mês `YYYY-MM`).
- **Leitura no ServiceNow pela API REST com a conta de serviço**
  (`SIS.ZABBIXDCSN`) — somente leitura; usa a **Aggregate API** para contar no
  servidor. Escrita nunca ocorre aqui.
- Indicadores calculados:
  1. **Tickets resolvidos (12 meses) + SLA** — grupo `TI_N2_FLD_RNR_LOJAS_SPARE`,
     fechados no ano, estado ≠ Cancelado; SLA por `task_sla` (`has_breached`).
  2. **Top 20 lojas** e **Top 10 subcategorias**.
  3. **TMA** (Coletor / SLED / PDV) — incidentes dos últimos 12 meses,
     subcategoria Sled RFID/Sled/Coletor, abertos e fechados no mesmo mês; TMA =
     dias entre "Data Bouncing" (`u_data_bouncing`, configurável) e a resolução,
     média por subcategoria.
- Se qualquer erro ocorrer ao carregar este módulo, **o portal sobe normalmente
  sem ele** (carregamento isolado, apenas registra no log).

Endpoints: `GET /api/indicadores/dados`, `POST /api/indicadores/atualizar`.

---

## 5. Integrações externas

### EBS (Oracle E-Business Suite)
- `ebs_service.py`: **login AD** e **consulta de ativos** no ERP corporativo.
- Toda configuração (URLs, timeout, SSL, workers) vem de `config.py` — nada é lido
  direto do ambiente dentro do serviço.
- Fallback: **base local** de ativos (tabela `local_assets`) para quando o EBS
  não responde.

### ServiceNow (`renner.service-now.com`)
- **Escrita como usuário logado**: login por **SSO/OAM** (`_login_sso`) e reuso dos
  cookies da sessão do usuário (`_sn_session_from_portal`). Usado em entrada,
  saída e encerramento.
- **Leitura por conta de serviço**: API REST (`/api/now/table/...` e Aggregate
  `/api/now/stats/...`) com `SIS.ZABBIXDCSN`, usada pelos **Indicadores**.
- **Proxy de saída** `SN_PROXY` (ex.: `http://10.115.30.135:8888`) — faz
  interceptação TLS (certificado self-signed ⇒ `verify=False`).
- Tabelas usadas: `alm_hardware` (ativos) e `incident` (chamados), `task_sla`.

### Correios (API oficial)
- Autenticação com cartão/contrato e **rastreio** de objetos + **comprovante**.
- Credenciais no **cofre `vcreports_secret`** (`CORREIOS_USUARIO`, `CORREIOS_CHAVE`,
  `CORREIOS_CARTOES`, `CORREIOS_DR`, `CORREIOS_CONTRATO`), com *fallback* para env.

---

## 6. Modelo de dados

### Banco do portal (`database.py`)
| Tabela | Conteúdo |
|---|---|
| `users` | Usuários (login, origem AD/SSO/LOCAL, admin, `allowed`, bloqueio, senha hash). |
| `permissions` | Permissões por usuário e módulo. |
| `access_logs` | Log de acessos (sucesso/falha, IP, origem). |
| `settings` | Configurações chave/valor (visual, TV, valor-hora, controle de acesso). |
| `classifications` | Regras de classificação (descrição → categoria/modelo). |
| `storage_locations` | Locais de armazenagem. |
| `assets` | Ativos (imobilizado, ativo, etiqueta, série, descrição, custo, DPIS…). |
| `receipt_cycles` | Ciclos de recebimento (status, local, lote, semana ISO, aberto). |
| `movements` | Auditoria de mudanças de status/local. |
| `lot_sequences`, `lots` | Numeração e registro de lotes. |
| `repairs` | Reparos (tempos, técnico, resultado, saving). |
| `local_assets`, `load_history` | Base local de ativos e histórico de cargas. |
| `printers` | Impressoras cadastradas (criada pelo módulo Identificação). |

### Banco Controle de Orçamento (`database_orcamento.py`, isolado)
`budget_projects`, `budget_categories`.

### Banco Indicadores (`database_indicadores.py`, isolado)
`indicador_snapshot` (`referencia` `YYYY-MM`, `payload` JSON, criado_em/por).

---

## 7. Configuração e operação

### Variáveis de ambiente principais (`config.py`)
| Variável | Uso |
|---|---|
| `DATABASE_URL` | Banco do portal (PostgreSQL hoje / MySQL no servidor novo). |
| `PORTAL_SESSION_SECRET` | Segredo de assinatura de sessão. |
| `INITIAL_ADMIN_LOGIN` / `..._PASSWORD` | Admin inicial. |
| `PORT` (8901), `HOST`, `WORKERS` | Servidor. |
| `SN_PROXY` | Proxy de saída para ServiceNow/Correios. |
| `SN_API_USER` / `SN_API_PASS` / `SN_API_PROXY` | Conta de serviço p/ Indicadores. |
| `SN_INDIC_QUEUE`, `SN_TMA_START_FIELD` | Parâmetros dos Indicadores. |
| `INDICADORES_DATABASE_URL` | Banco isolado dos Indicadores. |
| `CONTROLE_ORCAMENTO_DATABASE_URL` | Banco isolado do Controle de Orçamento. |
| `SSL_CERTFILE` / `SSL_KEYFILE` | HTTPS direto (opcional). |
| `CREDENTIALS_DIRECTORY` | Diretório de credenciais protegidas (EBS público). |

> **Correios**: no servidor novo **não** use env — as credenciais vêm do cofre
> `vcreports_secret`.

### Subir o serviço
```bash
cd /var/www/vcreports/portal-spare
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 main.py            # ou via systemd (portal_spare.service)
```
Na subida, `init_db()` cria as tabelas e grava as configurações padrão.

### Migração para o servidor novo
Ver **`docs/MIGRACAO_SERVIDOR_NOVO.md`** (MySQL/MariaDB, cofre de segredos, cópia
de dados dos três bancos, proteção do servidor compartilhado).

---

## 8. Mapa rápido de rotas

**Páginas:** `/` (portal) · `/tv` · `/tv2` · `/controle-orcamento` · `/indicadores`

**API (prefixos):**
- `/api/auth/*` — login, logout, sessão, troca de senha, sessão ServiceNow.
- `/api/consulta*` — consulta de ativos.
- `/api/recebimento*`, `/api/recebimentos*`, `/api/lotes*` — recebimento e lotes.
- `/api/identificacao/*` — etiquetas e impressoras.
- `/api/servicenow/*` — entrada, saída, incidentes, relatórios, Correios,
  encerramento.
- `/api/reparos*` — reparos e dashboard.
- `/api/parametros/*` — administração.
- `/api/status`, `/api/dashboard/summary`, `/api/tv/dashboard` — status e TV.
- `/api/controle-orcamento/*`, `/api/indicadores/*` — módulos isolados.
- `/api/public-assets/*` — consulta pública de ativos EBS (sem login).

---

## 9. Documentos relacionados

| Arquivo | Assunto |
|---|---|
| `docs/MIGRACAO_SERVIDOR_NOVO.md` | Migração para o servidor novo (MySQL + cofre). |
| `docs/CORREIOS_SERVICENOW.md` | Detalhes da integração Correios/ServiceNow. |
| `docs/CONTROLE_ORCAMENTO.md` | Módulo Controle de Orçamento. |
| `docs/MIGRACAO_CONTROLE_ORCAMENTO.md` | Migração do Controle de Orçamento. |
| `docs/MIGRACAO.md` | Notas gerais de migração. |
