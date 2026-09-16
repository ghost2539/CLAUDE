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
| Controle de Orçamento (/controle-orcamento) | **React** (build gerado com esbuild, versionado) |
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

**Padrão de UI SPARE** (contrato em `docs/PADRAO_UI_SPARE.md`) — todas as
telas seguem a mesma biblioteca de `static/app.css`:
- Paleta LRSA 2025: `#000000 #0C0C0C #FFFFFF #AB4807 #C79105 #E9D39B #246F68`
  (mais os derivados de hover/contraste). Nenhum hex fora da tabela nos módulos:
  as cores entram por token `--sp-*` (os nomes antigos `--color-*`/`--bg-*`
  são apelidos para os tokens).
- Tipografia: **Arial** em todo o sistema — títulos, texto, botões, rótulos e
  números. É a fonte institucional e não depende de CDN nenhum; o alinhamento
  de dígito vem de `font-variant-numeric: tabular-nums`.
- Forma: estrutura reta (cartões, tabelas, KPIs e shell sem raio) com
  **controles arredondados** — botões 7px, campos 6px, chips 6px, badges 5px.
  A única sombra do sistema é a do botão primário. Separação é borda de 1px;
  no KPI, cada célula leva `outline` e o divisor se forma no `gap`.
- Shell: sidebar fixa de 236px, **preta nos dois temas**, itens numerados
  01…N, rodapé com versão/ambiente e "CSC TI - Spare"; header de 62px com
  trilha (grupo / tela), busca global, **toggle de tema** (`role="switch"`),
  pílula de status do ServiceNow e avatar quadrado com iniciais + matrícula.
- Tema: o login é sempre escuro; nos módulos o usuário escolhe. A preferência
  fica no perfil (`settings`, chave `pref:<login>`, via
  `PUT /api/auth/preferencias`) e o `localStorage['spare-tema']` é só cache.
  As páginas fora do SPA (obsolescência, EBS Forms) leem o mesmo cache.
- *Parâmetros → Visual* guarda só nome da aplicação e rodapé (tabela
  `settings`, chave `visual`); cores não se configuram.

**Indicadores (/indicadores)** — painel executivo sempre escuro, na mesma
paleta (fundo `#0C0C0C`, superfície `#141414`, acento `#AB4807`; séries
`#C05B12 #2F8079 #C79105 #E9D39B #5FB8AC #6E6E6E`). Abas no topo, KPIs,
gráficos SVG inline (colunas, linha, ranking), auto-refresh 2 min.

**Controle de Orçamento (/controle-orcamento)** — React + Tailwind, tema
claro; KPIs, donut, barras, curva S; tabela editável.

**⚠️ Regra de CSP (importante para novas telas):** o portal envia
`Content-Security-Policy: script-src 'self'`. Isso **bloqueia `<script>` inline** —
todo JS de página tem que estar em **arquivo externo** (`/static/.../app.js`).
CSS inline é permitido (`style-src 'unsafe-inline'`). **Nenhum recurso
externo**: fonte, script e folha de estilo vêm todos do próprio portal.

---

## 3. Acesso e autenticação

Login **só por Logon AD** (`SSO`): a senha de rede é validada no **loginsso**
(Oracle Access Manager) por uma conexão com **TLS verificado**
(`integracoes/http.py`; `PORTAL_CA_BUNDLE` quando há proxy interceptador) e
não é guardada em lugar nenhum. **Só entra quem um admin liberou** (`allowed`).

Regras:
- **Sessão** por cookie assinado (`itsdangerous`), `HttpOnly`, `Secure` em HTTPS
  (`SESSION_COOKIE_SECURE`). A sessão cai por **ociosidade** —
  `SESSION_TTL_MINUTES` (480) contados do último pedido, não do login — com
  teto absoluto em `SESSION_MAX_HOURS` (24). Sessões em memória do processo,
  indexadas por login: mudança de permissão vale na hora; desativar,
  tirar a liberação ou excluir derruba as sessões vivas.
- **Bloqueado no SSO fica salvo como pendente** (Permitido=Não) e aparece em *Parâmetros → Usuários e Permissões* para o admin liberar (marcar "Acesso permitido"), ou criar antes via **Novo usuário → Rede/SSO** (entra já liberado; senha é a do AD).
- **Rate limit** do login por IP **e por login tentado** (5/min); API 120/min.
  `X-Forwarded-For` só vale vindo de `TRUSTED_PROXIES`.
- **Permissões por módulo** com as ações que o módulo tem (`config.MODULE_ACTIONS`):
  `view`, e conforme o módulo `create/edit/export/admin`. `admin` de módulo administra
  aquele módulo. **Usuários, liberações e `is_admin` são só de administrador do portal.**
  O administrador inicial não pode ser rebaixado; ninguém rebaixa a si mesmo.
- **Ações de escrita no ServiceNow ocorrem como o usuário logado** (cookies SSO da sessão),
  nunca com conta de serviço. `sys_id` vindo do cliente é validado (32 hexadecimais).

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
| **Configuração** | *Visual* (admin: nome e rodapé) · *Locais* · *Classificações* · *Usuários e Permissões* (admin: cria usuário SSO, libera, define admin, permissões por módulo/ação) · *Sequências* (admin) · *Configuração Módulos* (admin) · *Ciclo do ativo* (admin) · *Automações* · *Monitoramento* (admin) · *Acessos & Alertas* (admin) · *Dashboards* (admin) · *Minha conta*. |

O campo **Corredor/Espaço** (`aisle_space_location`) foi adicionado à saída
(formulário e upload). **A automação de encerramento de chamados não é afetada.**

---

## 5. Páginas autônomas (fora do menu)

### 5.1 e 5.2 — removidos em setembro/2026
O painel `/tv` (e `/api/tv/dashboard`) e o Controle de Orçamento de portfólio
`/tv2` foram descontinuados. O `/tv2` foi substituído pelo
`/controle-orcamento`.

### 5.3 `/controle-orcamento` — Execução de CAPEX
Banco próprio (`db/orcamento_exec.py`, tabela `budget_projects` com as colunas
`a_realizar` e `locked`). **Acesso controlado**: exige login do portal e o
módulo `orcamento` liberado para o usuário; toda abertura e toda gravação
ficam na trilha `budget_acessos`.
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


---

## 7. Modelo de dados

**Banco do portal (`database.py`):** `users`, `permissions`, `access_logs`,
`settings`, `classifications`, `storage_locations`, `assets`, `receipt_cycles`,
`movements`, `lot_sequences`, `lots`, `repairs`, `local_assets`, `load_history`,
`printers`.

**Bancos isolados (separados do portal):**
- `db/orcamento_exec.py` (/controle-orcamento): `budget_projects` (+`a_realizar`,`locked`,`synced_at`), `budget_categories`.
- `database_indicadores.py` (/indicadores): `indicador_snapshot`.

---

## 8. Parâmetros (variáveis de ambiente)

Arquivo de ambiente do serviço: **`/etc/portal_operacoes_spare/environment`**.

### Núcleo
| Variável | Padrão | Uso |
|---|---|---|
| `DATABASE_URL` | — (obrigatório) | Banco do portal. PostgreSQL hoje / MySQL (`mysql+pymysql://...`) no servidor novo. |
| `PORTAL_SESSION_SECRET` | — (obrigatório) | Segredo de assinatura da sessão. |
| `SESSION_TTL_MINUTES` | 480 | Ociosidade: a sessão cai depois deste tempo **sem uso**; cada pedido reinicia a contagem. |
| `SESSION_MAX_HOURS` | 24 | Teto absoluto da sessão, por mais que se use. |
| `SESSION_COOKIE_SECURE` | auto | Cookie só em HTTPS: `auto` (liga quando o pedido chega por https, direto ou via proxy confiável), `true`, `false`. |
| `TRUSTED_PROXIES` | 127.0.0.1,::1 | Proxies cujos `X-Forwarded-For`/`X-Forwarded-Proto` são aceitos (IP do IP real e do esquema). |
| `PORTAL_CA_BUNDLE` | "" | PEM com a CA corporativa (+ CA do proxy interceptador) para verificar o TLS de saída. |
| `PUBLIC_ASSETS_TOKEN` | "" (cofre) | Token do cabeçalho `X-Api-Key` da conversão EBS → ServiceNow. Sem ele, só sessão com `consulta:view`. |
| `INITIAL_ADMIN_LOGIN` | "" | Login de rede do administrador inicial (a senha é a do AD). |
| `HOST` / `PORT` / `WORKERS` | 0.0.0.0 / **8901** / 1 | Servidor. |
| `UPLOAD_MAX_MB` | 50 | Upload máximo. |
| `RATE_LIMIT_LOGIN` / `RATE_LIMIT_API` | 5/minute / 120/minute | Limites. |
| `SSL_CERTFILE` / `SSL_KEYFILE` | "" | HTTPS direto (opcional). |
| `DEFAULT_VALOR_HORA` | 150 | Valor-hora do saving. |

### EBS / consulta pública
| Variável | Uso |
|---|---|
| `EBS_LOGIN_URL` / `EBS_SEARCH_URL` | Endpoints do EBS. |
| `VERIFY_SSL` | **true** | Verificação TLS de toda saída (OAM, ServiceNow, EBS, MDM, Correios). `false` só para diagnóstico; fica em log. |
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

**Páginas:** `/` · `/controle-orcamento` · `/indicadores`

**API (prefixos):**
- `/api/auth/*` — login (SSO), logout, sessão (`/me`), preferências (tema), sessão ServiceNow.
- `/api/consulta*` — consulta de ativos.
- `/api/recebimento* · /recebimentos* · /lotes*` — recebimento e lotes.
- `/api/identificacao/*` — etiquetas e impressoras.
- `/api/servicenow/*` — entrada, saída (search/search_lote/move), incidentes, relatórios, Correios, encerramento.

- `/api/parametros/*` — administração.
- `/api/status · /dashboard/summary` — status e resumo.
- `/api/controle-orcamento-exec/*` (/controle-orcamento) · `/api/indicadores/*` — módulos isolados.
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

