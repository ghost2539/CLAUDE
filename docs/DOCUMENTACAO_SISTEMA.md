# Documentação do Sistema — Portal de Operações SPARE
### Referência completa para IA/handoff (atualizada)

> Documento único para outra pessoa (ou uma IA) entender **toda** a aplicação:
> arquitetura, telas, módulos, integrações, design, parâmetros e operação.
> Gerado a partir do código-fonte (branch `main`).

---

## 1. Visão geral

Plataforma web da operação **SPARE** (ciclo de vida de ativos de TI de loja:
recebimento, triagem, reparo, identificação/etiquetagem, saída de estoque e
chamados), integrando **EBS (Oracle E-Business Suite)**, **ServiceNow** e
**Correios**, além de painéis (TV, Controle de Orçamento, Indicadores RMR).

| Item | Valor |
|---|---|
| Backend | Python 3.11+ · **FastAPI** + Uvicorn |
| Frontend do portal | **SPA em JavaScript puro** (sem framework), CSS próprio |
| Controle de Orçamento (/tv2 e /controle-orcamento) | **React** (build gerado com esbuild, versionado) |
| Indicadores (/indicadores) | HTML + **JS externo** + SVG inline (sem libs) |
| ORM/Banco | **SQLAlchemy 2** — PostgreSQL (atual) / **MySQL–MariaDB** (servidor novo) |
| Porta padrão | **8901** |
| Serviço | systemd `portal_spare.service` (env em `/etc/portal_operacoes_spare/environment`) |
| Caminho servidor atual | `/opt/portal-spare-v2` |
| Caminho servidor novo | `/var/www/vcreports/portal-spare` |
| Repositório | `github.com/ghost2539/CLAUDE` (branch `main`) |

### Arquitetura (um único serviço)
```
Navegador
 ├── /                      SPA do portal (login + menu)        → banco PORTAL
 ├── /tv                    Painel de TV (operação)             → banco PORTAL
 ├── /tv2                   Controle de Orçamento (React)       → banco ORÇAMENTO (isolado)
 ├── /controle-orcamento    Execução CAPEX (clone + EBS)        → banco ORÇAMENTO-EXEC (isolado)
 ├── /indicadores           Indicadores RMR (dark dashboard)    → banco INDICADORES (isolado)
 └── /api/...               API REST
        ├── EBS (Oracle E-Business Suite)  — ativos + login AD
        ├── ServiceNow (renner.service-now.com) — entrada/saída/encerramento/relatórios/indicadores
        ├── Correios (API oficial)         — rastreio + comprovante
        └── EBS CAPEX API (suporte.lojasrenner.com.br/ebs/api/capex) — valores de projeto
```
Módulos isolados carregam com try/except no `main.py`: **um erro neles nunca
derruba o portal**. Cada um tem **banco próprio e separado**.

---

## 2. Design / identidade visual

**Portal (SPA)** — tema escuro configurável em *Parâmetros → Visual* (tabela
`settings`, chave `visual`). Padrões:
- Cor primária `#AB4807` (laranja queimado), destaque `#C79105` (âmbar),
  fundo `#090B0D`, painel `#111419`, texto `#E8E8E8`, fonte **Inter**.
- Layout: sidebar à esquerda + topbar + área de conteúdo; toasts; modais.

**Indicadores (/indicadores)** — dashboard executivo **dark** (navy):
- Fundo `#0A0F1A`, superfície `#121A2A`, texto `#E6EDF7`, acento **`#F97316`**.
- Paleta categórica validada: `#3B82F6 #0891B2 #22C55E #A855F7 #EC4899 #F59E0B`.
- Sidebar com **abas** (cada item mostra só a sua seção), KPIs com anel de %,
  gráficos SVG inline (colunas, linha, ranking), auto-refresh 2 min.

**Controle de Orçamento (/tv2, /controle-orcamento)** — React + Tailwind, tema
claro; KPIs, donut, barras, curva S; tabela editável.

**⚠️ Regra de CSP (importante para novas telas):** o portal envia
`Content-Security-Policy: script-src 'self'`. Isso **bloqueia `<script>` inline** —
todo JS de página tem que estar em **arquivo externo** (`/static/.../app.js`).
CSS inline é permitido (`style-src 'unsafe-inline'`). Recursos externos só de
`fonts.googleapis.com`/`fonts.gstatic.com`.

---

## 3. Acesso e autenticação

Login com **três tipos** (botões na tela):

| Botão | Tipo | O que faz |
|---|---|---|
| **Logon AD** | `AD` | Valida no EBS/AD. Cria o usuário no 1º acesso. |
| **Logon Rede** | `SSO` | Valida no **loginsso** (Oracle Access Manager). **Só entra quem um admin liberou** (allow-list por usuário de rede). |
| **Logon Local** | `LOCAL` | Usuário/senha no portal (hash **bcrypt**). |

Regras:
- **Sessão** por cookie assinado (`itsdangerous`), `HttpOnly`, TTL 480 min. Sessões em memória do processo.
- **Troca de senha obrigatória DESATIVADA** — Logon Local entra direto (troca voluntária em *Parâmetros → Minha conta*).
- **SSO exige liberação individual** (allow-list) — o "Controle de acesso externo" (block_external) só afeta AD/SN, **não** o SSO.
- **Bloqueado no SSO fica salvo como pendente** (Permitido=Não) e aparece em *Parâmetros → Usuários e Permissões* para o admin liberar (marcar "Acesso permitido"), ou criar antes via **Novo usuário → Rede/SSO** (entra já liberado; senha é a do AD).
- **Bloqueio local**: 5 senhas erradas ⇒ 15 min. **Rate limit**: login 5/min, API 120/min.
- **Permissões por módulo**: `can_view/create/edit/export/admin`. Admin = acesso total; o menu só mostra o permitido.
- **Ações de escrita no ServiceNow ocorrem como o usuário logado** (cookies SSO da sessão), nunca com conta de serviço.

---

## 4. Módulos do portal (menu lateral)

| Módulo | Sub-abas / função |
|---|---|
| **Bem-vindo** | Início. |
| **Consulta** | Busca de ativos (imobilizado/ativo/etiqueta/série), em lote (até 1000) e individual; cruza base local + EBS + classificação; exporta **.xlsx**. |
| **Recebimento** | *Novo Recebimento* (scan/preview/gravação, detecção de duplicidade CM/YC) · *Base de Recebimentos* · *Dashboard* · *Lotes* (numeração automática) · *Cadastro de modelos* (classificação) · *Importar base histórica* · *Base local EBS*. Cada mudança gera **Movimento** (auditoria). |
| **Identificação** | *Gerar Lote* · *Identificação A4* (PDF) · *Impressão Zebra Livre* (ZPL) · *Impressoras* (cadastro/teste). |
| **ServiceNow** | *Entrada de estoque* (envia ativos p/ `alm_hardware` via SSO+JSONv2, assíncrono) · *Saída de estoque* (**lote**: cola lista de identificadores, edita por linha destino/status/**corredor-espaço**/obs, flag "aplicar a todos") · *Rastreio - Chamados* · *Relatórios*. |
| **Correios** | *Rastreios* (individual/lote + comprovante) · *Encerramento* (encerra automaticamente chamados entregues: On Hold→In Progress→Resolved). |
| **Central de Reparos** | *Registro de Reparo* (tempos, técnico, resultado, **saving** = valor-hora × tempo) · *Tratativa de saldos* · *Dashboard*. |
| **Status** | Saúde das integrações. |
| **Parâmetros** | *Visual* (admin) · *Locais* · *Classificações* · *Valor-hora* · *Usuários e Permissões* (admin: cria Local/SSO, libera SSO, define admin) · *Sequências* (admin) · *TV* · *Minha conta*. |

O campo **Corredor/Espaço** (`aisle_space_location`) foi adicionado à saída
(formulário e upload). **A automação de encerramento de chamados não é afetada.**

---

## 5. Páginas autônomas (fora do menu)

### 5.1 `/tv` — Painel de Operações (TV)
Dashboard leve (estatísticas, gráficos, últimos recebimentos), auto-atualiza.
`/tv?demo=1` = dados aleatórios para validação.

### 5.2 `/tv2` — Controle de Orçamento de Portfólio (CAPEX/OPEX)
React, **acesso livre**, banco isolado (`database_orcamento.py`; tabelas
`budget_projects`, `budget_categories`). Edição manual dos valores.

### 5.3 `/controle-orcamento` — Execução de CAPEX (clone do /tv2, banco próprio)
Clone independente (`database_orcamento_exec.py`, tabela `budget_projects` com
coluna extra `a_realizar` e `locked`). **NÃO** compartilha dados com o /tv2.
- **Barra de inclusão** no topo: Número (ID que puxa do EBS), Tipo (CAPEX/OPEX),
  Projeto/Demanda (manual), Categoria, Área.
- **Puxa da API de CAPEX do EBS** (`/ebs/api/capex/?projetos=...`), mapeando:
  `saldo_inicial`→Orçamento Aprovado, `comprometido`+`reservados`→Comprometido,
  `realizado`→Realizado, `saldo_dia`→A Realizar. (`empresa`, `devoluções`,
  `pct_exec`, `nome_projeto` **não** são puxados.)
- **Conversão de moeda**: projetos com `empresa` Argentina (ARS) ou Uruguai (UYU)
  têm valores convertidos para BRL (cotação fixa por env ou ao vivo).
- **Cadeado por projeto** (`locked`): projeto travado **não** é alterado no
  "Atualizar (EBS)". Inclusão manual de projetos fora do EBS é permitida.
- **Autenticação da API**: reutiliza a do módulo *consulta-times* (login EBS →
  cookies/token), com re-login automático em 401; fallback Basic/token por env.
- API: `/api/controle-orcamento-exec/{projetos,categorias,incluir,sincronizar}`.

### 5.4 `/indicadores` — Indicadores RMR (dashboard executivo)
HTML + `app.js` externo (por causa da CSP). **Banco próprio**
(`database_indicadores.py`, tabela `indicador_snapshot`: `referencia` YYYY-MM,
`payload` JSON). **Leitura no ServiceNow pela conta de serviço** (REST, só
leitura, Aggregate API). **Auto-refresh a cada 2 min** puxando do ServiceNow.
Menu lateral = **abas** (cada uma mostra só a sua seção).

Indicadores:
1. **Tickets Resolvidos** (por mês) — vêm das **ANS (task_sla)** cujo nome contém **SPARE**, concluídas.
2. **SLA** (% por mês) — mesmas ANS SPARE de Resolução concluídas (`has_breached`=false = dentro do prazo).
3. **Abertos por mês** — incidentes por `opened_at` na fila SPARE.
4. **Top 20 Lojas** e **Top 10 Subcategorias** (incident, agregação por location/subcategory).
5. **TMA Coletor e SLED** — média de dias entre "Data Bouncing" (`u_data_bouncing`) e a resolução, para incidentes abertos e encerrados no mesmo mês.

Endpoints: `GET /api/indicadores/dados`, `POST /api/indicadores/atualizar`,
`GET /api/indicadores/diag-slas?like=SPARE` (lista os nomes de ANS + contagem —
diagnóstico para acertar o filtro).

---

## 6. Integrações externas

**EBS (Oracle E-Business Suite)** — `ebs_service.py`: login AD + consulta de
ativos. Fallback: base local (`local_assets`).

**ServiceNow** (`renner.service-now.com`):
- **Escrita como usuário logado** (SSO/OAM, cookies da sessão) — entrada, saída, encerramento.
- **Leitura por conta de serviço** (`SIS.ZABBIXDCSN`) — Indicadores, via **GET** na Table/Aggregate API (`/api/now/table` e `/api/now/stats`). **POST não é suportado** por essa API — sempre GET.
- **Proxy de saída** `SN_PROXY`/`SN_API_PROXY` = **`http://10.115.30.135:8888`** (faz interceptação TLS → `verify=False`). ⚠️ **Não** usar `cache.lojasrenner.com.br:3128` (inacessível do servidor).
- Tabelas: `alm_hardware`, `incident`, `task_sla`.

**Correios** — credenciais no **cofre** `vcreports_secret` (`CORREIOS_USUARIO`,
`CORREIOS_CHAVE`, `CORREIOS_CARTOES`, `CORREIOS_DR`, `CORREIOS_CONTRATO`), com
fallback para env na transição.

**EBS via Oracle direto** — `ebs_oracle.py` (camada de acesso só-leitura ao
Oracle EBS; `SET TRANSACTION READ ONLY`, timeout, teto de linhas). Credenciais
no cofre (`ORACLE_EBS_USER/PASS/DSN`, `ORACLE_CLIENT_LIB_DIR`). Consultas ainda
a configurar. Doc: `docs/EBS_ORACLE_BASE.md`.

---

## 7. Modelo de dados

**Banco do portal (`database.py`):** `users`, `permissions`, `access_logs`,
`settings`, `classifications`, `storage_locations`, `assets`, `receipt_cycles`,
`movements`, `lot_sequences`, `lots`, `repairs`, `local_assets`, `load_history`,
`printers`.

**Bancos isolados (separados do portal):**
- `database_orcamento.py` (/tv2): `budget_projects`, `budget_categories`.
- `database_orcamento_exec.py` (/controle-orcamento): `budget_projects` (+`a_realizar`,`locked`,`synced_at`), `budget_categories`.
- `database_indicadores.py` (/indicadores): `indicador_snapshot`.

---

## 8. Parâmetros (variáveis de ambiente)

Arquivo de ambiente do serviço: **`/etc/portal_operacoes_spare/environment`**.

### Núcleo
| Variável | Padrão | Uso |
|---|---|---|
| `DATABASE_URL` | — (obrigatório) | Banco do portal. PostgreSQL hoje / MySQL (`mysql+pymysql://...`) no servidor novo. |
| `PORTAL_SESSION_SECRET` | — (obrigatório) | Segredo de assinatura da sessão. |
| `SESSION_TTL_MINUTES` | 480 | Duração da sessão. |
| `INITIAL_ADMIN_LOGIN` / `INITIAL_ADMIN_PASSWORD` | "" | Admin inicial. |
| `HOST` / `PORT` / `WORKERS` | 0.0.0.0 / **8901** / 1 | Servidor. |
| `UPLOAD_MAX_MB` | 50 | Upload máximo. |
| `RATE_LIMIT_LOGIN` / `RATE_LIMIT_API` | 5/minute / 120/minute | Limites. |
| `SSL_CERTFILE` / `SSL_KEYFILE` | "" | HTTPS direto (opcional). |
| `DEFAULT_VALOR_HORA` | 150 | Valor-hora do saving. |

### EBS / consulta pública
| Variável | Uso |
|---|---|
| `EBS_LOGIN_URL` / `EBS_SEARCH_URL` | Endpoints do EBS. |
| `VERIFY_SSL` | false | Verificação TLS das chamadas. |
| `TIMEOUT_SECONDS` / `MAX_WORKERS` | Timeout e paralelismo das consultas. |
| `CREDENTIALS_DIRECTORY` | Diretório de credenciais protegidas (`ebs_public_username`/`password`) usadas por consulta pública/consulta-times/CAPEX. |

### ServiceNow (proxy + conta de serviço)
| Variável | Padrão | Uso |
|---|---|---|
| `SN_PROXY` | — | Proxy de saída (escrita como usuário). **`http://10.115.30.135:8888`** |
| `SN_API_PROXY` | (cai p/ SN_PROXY) | Proxy da leitura por conta de serviço. **Defina explicitamente** `http://10.115.30.135:8888` (senão pode cair no `https_proxy=cache...` e dar timeout). |
| `SN_API_BASE` | https://renner.service-now.com | Base REST. |
| `SN_API_USER` / `SN_API_PASS` | "" | Conta de serviço (`SIS.ZABBIXDCSN`) — sem isso os Indicadores não puxam. |
| `SN_INDIC_QUEUE` | TI_N2_FLD_RNR_LOJAS_SPARE | Grupo/fila do SPARE. |
| `SN_TMA_START_FIELD` | u_data_bouncing | Campo "Data Bouncing" do TMA. |
| `SN_SLA_NAME_LIKE` | SPARE | Só ANS cujo NOME contém isto (evita outras filas). Ajuste p/ o nome exato da ANS de Resolução se precisar. |
| `SN_SLA_STAGE` | completed | Só ANS concluídas (evita falso estouro). |
| `SN_SLA_EXTRA` | "" | Filtro extra opcional na task_sla. |
| `SN_SLA_DATE_FIELD` | task.closed_at | Campo de data para alocar a ANS no mês. |

### Bancos isolados
| Variável | Padrão | Uso |
|---|---|---|
| `INDICADORES_DATABASE_URL` | sqlite data/indicadores.db | Banco dos Indicadores. |
| `ORCAMENTO_DATABASE_URL` | sqlite data/controle_orcamento.db | Banco do /tv2. |
| `ORCAMENTO_EXEC_DATABASE_URL` | sqlite data/controle_orcamento_exec.db | Banco do /controle-orcamento. |

### EBS CAPEX API (/controle-orcamento)
| Variável | Padrão | Uso |
|---|---|---|
| `EBS_CAPEX_URL` | https://suporte.lojasrenner.com.br/ebs/api/capex/ | Endpoint. |
| `EBS_CAPEX_PROXY` | "" | Proxy (interno, normalmente vazio). |
| `EBS_CAPEX_TIMEOUT` / `EBS_CAPEX_VERIFY` | 30 / false | Timeout / TLS. |
| `EBS_CAPEX_USER` / `EBS_CAPEX_PASS` | "" | Basic auth (fallback). |
| `EBS_CAPEX_TOKEN` / `EBS_CAPEX_TOKEN_SCHEME` / `EBS_CAPEX_AUTH_HEADER` | "" / Bearer / Authorization | Token/header (fallback). |
| `EBS_CAPEX_ARS_BRL` / `EBS_CAPEX_UYU_BRL` | 0 | Cotação (R$ por 1 peso). 0 = tenta ao vivo. |
| `EBS_CAPEX_FX_URL` / `EBS_CAPEX_FX_PROXY` | awesomeapi / "" | Cotação ao vivo (best-effort). |

### Cofre (`vcreports_secrets`) — não vão em env no servidor novo
- **Correios**: `CORREIOS_USUARIO`, `CORREIOS_CHAVE`, `CORREIOS_CARTOES`, `CORREIOS_DR`, `CORREIOS_CONTRATO` (função `vcreports_secret`).
- **Oracle EBS**: `ORACLE_EBS_USER`, `ORACLE_EBS_PASS`, `ORACLE_EBS_DSN`, `ORACLE_CLIENT_LIB_DIR` (função `s`).

---

## 9. Mapa de rotas

**Páginas:** `/` · `/tv` · `/tv2` · `/controle-orcamento` · `/indicadores`

**API (prefixos):**
- `/api/auth/*` — login, logout, sessão, troca de senha, sessão ServiceNow.
- `/api/consulta*` — consulta de ativos.
- `/api/recebimento* · /recebimentos* · /lotes*` — recebimento e lotes.
- `/api/identificacao/*` — etiquetas e impressoras.
- `/api/servicenow/*` — entrada, saída (search/search_lote/move), incidentes, relatórios, Correios, encerramento.
- `/api/reparos*` — reparos e dashboard.
- `/api/parametros/*` — administração.
- `/api/status · /dashboard/summary · /tv/dashboard` — status e TV.
- `/api/controle-orcamento/*` (/tv2) · `/api/controle-orcamento-exec/*` (/controle-orcamento) · `/api/indicadores/*` — módulos isolados.
- `/api/public-assets/*` — consulta pública de ativos EBS (sem login).

---

## 10. Operação e deploy

**Rodar:**
```bash
cd <caminho>            # /opt/portal-spare-v2 (atual) ou /var/www/vcreports/portal-spare (novo)
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 main.py         # init_db cria as tabelas; ou via systemd portal_spare.service
```

**Deploy (servidor atual):** via **git bundle** do `main`
(`git fetch <bundle> main && git reset --hard FETCH_HEAD`) — o `git` pessoal e o
GitHub podem estar bloqueados; o **build do React já vem versionado** (não precisa
Node). Rodar o git **sem** `https_proxy` malformado (`env -u https_proxy ...`).
Mudança só de arquivo estático → basta substituir o arquivo (sem restart).
Mudança de `.py`/config → **reiniciar o serviço**.

**Migração para o servidor novo:** MySQL/MariaDB (3 schemas separados), cofre
`vcreports_secret`, cópia de dados via `scripts/migrar_pg_para_mysql.py`. Doc:
`docs/MIGRACAO_SERVIDOR_NOVO.md`.

**Gotchas conhecidos (importantes):**
- **Proxy ServiceNow**: use `http://10.115.30.135:8888` (defina `SN_API_PROXY` no env do serviço; o `cache.lojasrenner.com.br` dá timeout).
- **API do ServiceNow**: só **GET** (POST → "Method not Supported").
- **CSP**: `script-src 'self'` → JS de página **sempre em arquivo externo**.
- **Bundle/git**: senha do proxy corporativo tem `@` (quebra a URL do git) → rode git com `env -u https_proxy ...` (bundle local não precisa de proxy).

---

## 11. Documentos relacionados
| Arquivo | Assunto |
|---|---|
| `docs/MIGRACAO_SERVIDOR_NOVO.md` | Migração p/ o servidor novo (MySQL + cofre). |
| `docs/EBS_ORACLE_BASE.md` | Camada de acesso Oracle EBS + catálogo de tabelas padrão. |
| `docs/CORREIOS_SERVICENOW.md` | Integração Correios/ServiceNow. |
| `docs/CONTROLE_ORCAMENTO.md` | Módulo Controle de Orçamento. |
| `docs/DOCUMENTACAO_SISTEMA.html` | Versão HTML navegável desta documentação. |
