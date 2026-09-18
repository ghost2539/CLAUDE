# Documentação do Sistema — Portal de Operações SPARE
### Referência completa para quem chega agora (pessoa ou IA)

> Documento único para entender **toda** a aplicação: arquitetura, telas,
> módulos, integrações, design, parâmetros e operação.
> Conferido contra o código da branch `migracao` em 18/09/2026.
>
> **Este documento é verificado.** `scripts/verificar_migracao.py` compara a
> seção 4 com o menu de verdade (`static/index.html`): tela que entra no menu
> sem entrada aqui quebra a verificação. Foi assim que se descobriu que ele
> tinha ficado 21 módulos para trás.

---

## 1. Visão geral

Plataforma web da operação **SPARE** — o ciclo de vida do ativo de TI de loja,
da chegada à destinação: recebimento, internalização, atendimento, reparo,
separação, saída e devolução. Integra **EBS (Oracle E-Business Suite)**,
**ServiceNow**, **Correios** e o **MDM** (obsolescência), e tem painéis
próprios (Torre de Controle, Indicadores RMR, Orçamento).

| Item | Valor |
|---|---|
| Backend | Python 3.11+ · **FastAPI** + Uvicorn |
| Frontend do portal | **SPA em JavaScript puro** (sem framework), CSS próprio |
| Orçamento Infra CSC (`/controle-orcamento`) | **React** (build esbuild, versionado no repositório) |
| Indicadores (`/indicadores`) | HTML + **JS externo** + SVG inline (sem libs) |
| ORM | **SQLAlchemy 2** |
| Banco — servidor antigo | **PostgreSQL** |
| Banco — servidor novo | **SQLite** (arquivos em `data/db/`) |
| Porta padrão | **8901** |
| Serviço | systemd `portal_spare.service` |
| Caminho servidor antigo | `/opt/portal-spare-v2` (env em `/etc/portal_operacoes_spare/environment`) |
| Caminho servidor novo | `/var/www/vcreports/portal-spare` (env em `data/environment`) |
| Publicação | atrás de proxy, em subcaminho (`/portal-spare`) — ver `APP_BASE_PATH` |
| Repositório | `github.com/ghost2539/CLAUDE` (branch `migracao`) |

> ⚠️ **O SQLite do servidor é 3.26** (RHEL, 2018). Construção que só entrou
> depois — `NULLS LAST` (3.30), `RETURNING` (3.35), `FILTER (WHERE)` (3.30) —
> passa na máquina de desenvolvimento e quebra lá. `SELECT ... FOR UPDATE` não
> existe no SQLite em versão nenhuma. `scripts/verificar_sql_portavel.py`
> varre o código atrás disso.

### Arquitetura (um serviço só)
```
Navegador
 ├── /                       SPA do portal (login + menu)       → banco PORTAL
 ├── /modulos/<nome>.js      tela do módulo, servida COM permissão
 ├── /obsolescencia          parque de coletores (MDM)          → banco próprio
 ├── /controle-orcamento     Orçamento Infra CSC (React)        → banco próprio
 ├── /indicadores            Indicadores RMR                    → banco próprio
 ├── /consulta-times         espaço Times (liberação por login) → banco próprio
 ├── /cockpit-spare, /dash-* painéis de parede                  → banco PORTAL
 └── /api/...                API REST
        ├── EBS (Oracle E-Business Suite) — ativos, login, CAPEX
        ├── ServiceNow (renner.service-now.com) — ativos, chamados, indicadores
        ├── Correios (API oficial) — rastreio e comprovante
        └── MDM — parque de coletores (obsolescência)
```

**Cada módulo registra-se no `main.py` dentro do próprio `try/except`**: erro
num módulo nunca derruba o portal. **Cada módulo tem banco próprio e
separado** — são 23 bancos isolados além do banco do portal.

**O JavaScript do módulo não mora em `static/`.** Ele é servido por
`routers/modulos.py`, que confere sessão e permissão antes de entregar o
arquivo. Em `static/` ele saía para qualquer um, sem login.

---

## 2. Design / identidade visual

O padrão é o **Padrão de UI SPARE** (`docs/PADRAO_UI_SPARE.md`): paleta LRSA
2025, **Arial** em todo o sistema, estrutura reta com controles arredondados,
uma sombra só. Cor fixa em hexadecimal não entra: tudo por token CSS, o que é
o que faz o tema claro/escuro funcionar.

- Sidebar 236px, agrupada por etapa do ciclo; header 62px.
- Tema claro/escuro por token, guardado no perfil do usuário.
- **CSP `script-src 'self'`** → JS de página **sempre em arquivo externo**,
  nunca inline.
- Os comentários são retirados de `.js`, `.css` e `.html` na entrega
  (`core/estatico.py`): o que explica o código fica no repositório, não no
  navegador de quem abre a tela.

**Exceção combinada:** o Orçamento Infra CSC (React) fica fora do padrão
visual, por decisão de quem pediu a migração.

---

## 3. Acesso e autenticação

**Só SSO.** O login por senha no portal (`LOCAL`, bcrypt) **foi removido** na
revisão de segurança: não há senha de portal, não há troca de senha, não há
bloqueio por tentativa local.

| Tipo | O que faz |
|---|---|
| **SSO / Rede** | Valida no **loginsso** (Oracle Access Manager). Só entra quem um admin liberou (allow-list por usuário de rede). |

Regras:
- **Sessão** por cookie assinado (`itsdangerous`), `HttpOnly`, `Secure` atrás
  de HTTPS. **Janela deslizante**: o uso renova; não expira 8h depois do login
  no meio do trabalho.
- **Bloqueado no SSO fica pendente** e aparece em *Configuração → Usuários e
  Permissões* para o admin liberar.
- **Permissão por módulo** em quatro níveis (`nenhum`, `consultar`, `operar`,
  `administrar`), que o servidor traduz para as flags
  `can_view/create/edit/export/admin`. Admin do portal tem acesso total; o
  menu só mostra o que a pessoa pode.
- **Escrita no ServiceNow ocorre como o usuário logado** (cookies SSO da
  sessão), nunca com conta de serviço. A conta de serviço **só lê** (Indicadores).
- **Rate limit**: login 5/min, API 120/min.

---

## 4. Módulos do portal (menu lateral)

São **30 módulos** em `config.MODULE_ACTIONS` e **32 entradas** no menu,
agrupadas pela etapa do ciclo do ativo. A rota entre crases é a que o menu usa
(`data-route`).

### Início
| Módulo | Rota | O que faz |
|---|---|---|
| **Torre de Controle** | `#torre` | Painel de acompanhamento do ciclo: onde está cada lote e o que está parado. |
| **Consulta** | `#consulta` | Busca de ativos (imobilizado/ativo/etiqueta/série), em lote e individual; cruza base local + EBS + classificação; exporta **.xlsx**. |

### Entrada
| Módulo | Rota | O que faz |
|---|---|---|
| **Recebimento** | `#recebimento` | *Novo Recebimento* (Fornecedores ou Reversa) · *Base de Recebimentos* · *Dashboard* · *Lotes* · *Cadastro de modelos* (classificação) · *Importar base histórica* · *Base local EBS*. Cada mudança gera **Movimento** (auditoria). |
| **Agendamentos Forn.** | `#agendamentos_forn` | Agendamento da entrega do fornecedor, com PO e liberação consultadas no EBS (a liberação é o que vem depois do hífen: `2570313-25`). |
| **Identificação** | `#identificacao` | *Gerar Lote* · *Identificação A4* (PDF) · *Impressão Zebra* (ZPL) · *Impressoras*. |

### Internalização
| Módulo | Rota | O que faz |
|---|---|---|
| **Lançamento** | `#internalizacao/lancamento` | Abre o processo a partir do agendamento recebido. |
| **Patrimônio** | `#internalizacao/patrimonio` | Varredura no EBS pela série (só pelo botão) e confirmação do número de patrimônio. BU que não tem EBS não passa por aqui. |
| **Entrada de Equipamento** | `#internalizacao/entrada` | Dá entrada no estoque. Grava no portal **antes** do ServiceNow: queda de lá não perde a entrada. |

### Atendimento
| Módulo | Rota | O que faz |
|---|---|---|
| **Atendimento** | `#atendimento` | Fila de atendimento ao chamado. |
| **Logística Reversa** | `#reversa` | O que volta da loja: coleta, esperado × recebido. |
| **Preparação** | `#preparacao` | Preparo do equipamento para devolver ao uso. |
| **ServiceNow** | `#servicenow_automacoes` | Automações do ServiceNow. Roda **só pelo botão**, com o usuário logado — não há agendador nem conta de serviço escrevendo. |
| **Consulta de chamados** | `#servicenow_automacoes/consulta` | Aba da tela acima. Extração em lote de **incidents** (`incident.do`) e **RITMs** (`sc_req_item`), com escolha de colunas e exportação em CSV. Passa de 5 mil chamados. Só **leitura**, pela conta de serviço. |

### Central de Reparos
| Módulo | Rota | O que faz |
|---|---|---|
| **Frente e Retaguarda** | `#reparos/loja` | Bancada de reparo de loja. |
| **Mobilidade** | `#reparos/frota` | Bancada de coletores e SLED. |
| **Conectividade** | `#reparos/conectividade` | Bancada de rede. |
| **Assistência Externa** | `#externo` | O que vai para assistência de terceiro. |

### Saída
| Módulo | Rota | O que faz |
|---|---|---|
| **Separação** | `#separacao` | Separação do que sai, por lote. |
| **Projetos de Loja** | `#projetos` | Itens reservados para projeto de loja. |
| **Venda de Ativos** | `#venda` | Ativos destinados a venda. |
| **Destinação** | `#destinacao` | Destinação final (descarte, doação, sucata). |
| **Correios** | `#rastreio` | *Rastreios* (individual/lote + comprovante) · *Encerramento* de chamados entregues. |

### Gestão de Ativos
| Módulo | Rota | O que faz |
|---|---|---|
| **Inventário** | `#inventario` | Contagem e conferência do estoque. |
| **Regularização** | `#regularizacao` | Acerto de divergência encontrada no inventário. |
| **Entrada de Ativos** | `#gestao_ativos/entrada` | Envia ativos para o `alm_hardware` do ServiceNow. *(espaço Times)* |
| **Saída de Ativos** | `#gestao_ativos/saida` | Baixa em lote, com destino/status/corredor-espaço por linha. *(espaço Times)* |
| **Movimentação Interna** | `#gestao_ativos/movimentacao` | Move ativo entre estoques. *(espaço Times)* |
| **Obsolescência** | `/obsolescencia` | Parque de coletores lido do **MDM**: idade, versão de Android, quem sumiu do console. Página autônoma. |

### Gestão de Fornecedores
| Módulo | Rota | O que faz |
|---|---|---|
| **Brasil — Manutenção** | `#orcamento_manutencao` | Orçamento de manutenção de coletores (Bluebird): reparos, regra dos 60 %, cota mensal, consumo por categoria e modelo. Doc: `docs/ORCAMENTO_MANUTENCAO.md`. |

### Controle
| Módulo | Rota | O que faz |
|---|---|---|
| **Orçamento Spare** | `#orcamento_spare` | Controle de orçamento do SPARE: projetos com linhas de item, aprovado puxado do EBS e a parcela destinada ao Spare. |
| **Orçamento Infra CSC** | `/controle-orcamento` | Execução de CAPEX e OPEX da Infra CSC (React). Página autônoma. |
| **Status** | `#status` | Saúde das integrações e do servidor. |
| **Configuração** | `#parametros` | Ver a seção 4.1. |
| **Acesso Consulta Times** | `#consulta_times` | Liberação de acesso ao espaço Times. *(espaço Times)* |
| **Minha conta** | `#parametros/conta` | Preferências do próprio usuário (tema). |

**Bem-vindo** (`bemvindo`) é a tela de entrada: não fica na barra lateral, mas
é para onde o portal vai quando não há rota no endereço.

### 4.1 Abas de Configuração

São **13 abas**. Todas menos *Minha conta* aparecem **só para o admin do
portal inteiro**.

| Aba | O que faz |
|---|---|
| **Visual** | Marca, favicon e identidade da instalação. |
| **Locais** | Estoques e locais de guarda. |
| **Classificações** | Regras que classificam o ativo pela descrição. |
| **Usuários e Permissões** | Libera login e define o nível por módulo. |
| **Sequências** | Numeração de lote por prefixo. |
| **Configuração Módulos** | Liga e desliga módulo. |
| **Ciclo do ativo** | Etapas do ciclo e o que cada uma exige. |
| **Cofre de segredos** | Diagnóstico do cofre (nunca mostra valor). |
| **Base EBS** | Conexão com a base do EBS e as consultas nomeadas. |
| **Monitoramento** | Saúde e falhas registradas. |
| **Acessos & Alertas** | Log de acesso e alertas. |
| **Dashboards** | Painéis publicados. |
| **Minha conta** | Preferências do usuário — a única que todos veem. |

---

## 5. Páginas autônomas (fora da SPA)

Cada uma é servida por rota Python, passa pelo prefixo de proxy
(`core/prefixo.py`) e tem o JavaScript em arquivo externo.

| Caminho | O que é |
|---|---|
| `/obsolescencia` | Parque de coletores pelo MDM. Doc: `docs/MDM_OBSOLESCENCIA.md`. |
| `/controle-orcamento` | Orçamento Infra CSC (React). Também em `/controle-orcamento-InfraCSC`. |
| `/indicadores` | Indicadores RMR — ver 5.1. |
| `/consulta-times` | Espaço Times: consulta liberada por login, fora do menu do portal. |
| `/cockpit-spare` · `/dash-recebimento` · `/dash-centralreparos` · `/dash-estoques` | Painéis de parede. |

### 5.1 `/indicadores` — Indicadores RMR

Painel executivo do ServiceNow: backlog, RITMs, aguardando atendimento,
priorizados, SLA mês a mês, tratados, backlog por mês, distribuição por
status/localidade/BU/subcategoria e as séries de SLED e coletores.

- Os números vêm de um **snapshot gravado**, não do ServiceNow ao vivo. O
  botão **Atualizar** é que vai ao ServiceNow e grava o snapshot novo.
- A leitura usa a **conta de serviço** (`SN_API_USER`/`SN_API_PASS`, no cofre),
  por **GET** na Table/Aggregate API.
- **Exportar** baixa o snapshot em planilha (abas Resumo, Mensal e as quatro
  distribuições). A aba Resumo diz **de quando é o snapshot** — sem isso é
  fácil apresentar número da semana passada sem perceber.

---

## 6. Integrações externas

**EBS (Oracle E-Business Suite)**
- `integracoes/ebs_service.py`: login e consulta de ativos. Sem resposta, a
  consulta cai para a base local (`local_assets`).
- `integracoes/ebs_oracle.py`: acesso **só-leitura** direto ao Oracle, com
  `SET TRANSACTION READ ONLY`, timeout e teto de linhas. **SQL não vem da
  tela**: são consultas **nomeadas** com bind variables, versionadas no
  código. Credenciais no cofre (`ORACLE_EBS_USER/PASS/DSN`).
- Erro do driver traz o endereço dentro da mensagem; `core/mascara.py` apaga
  endereço, partes do endereço e usuário antes de a mensagem chegar à tela.

**ServiceNow** (`renner.service-now.com`)
- **Escrita como o usuário logado** (cookies SSO da sessão) — entrada, saída,
  encerramento. Nunca com conta de serviço.
- **Leitura por conta de serviço** — Indicadores e a Consulta de chamados,
  por **GET** em `/api/now/table` e `/api/now/stats`. **POST não é
  suportado** por essa API.
- Tabelas: `alm_hardware`, `incident`, `task_sla`, `sc_req_item`, `task`,
  e — só na Consulta de chamados, para descobrir os campos — `sys_dictionary`
  e `sys_db_object`.
- **Os campos que a Consulta oferece são os que a conta de serviço lê de
  verdade**: depois de ler o dicionário, o portal pede um registro real com
  todos aqueles campos. O ServiceNow omite em silêncio o que a ACL nega, e é
  o que sobra que vira a lista da tela. Sem isso, um campo barrado viraria
  coluna vazia no arquivo e pareceria dado faltando no chamado.
- **A exportação pagina por `sys_id`, não por offset.** Chamados continuam
  sendo abertos enquanto a exportação roda; com `sysparm_offset` uma linha
  muda de página e sai duplicada ou some. A verificação tem contraprova
  disso (`scripts/verificar_sn_consulta.py`).
- A consulta nunca chega pronta da tela: ela manda linhas de
  (campo, operador, valor); o campo é conferido contra a lista lida do
  dicionário e o operador contra uma lista fechada, para que `^` e `=`
  digitados não virem estrutura da *encoded query*.

**Correios** — credenciais pelo **cofre** (`CORREIOS_USUARIO`, `CORREIOS_CHAVE`,
`CORREIOS_CARTOES`, `CORREIOS_DR`, `CORREIOS_CONTRATO`), com o ambiente como
última parada. Doc: `docs/CORREIOS_SERVICENOW.md`.

**MDM** — parque de coletores para a Obsolescência.

---

## 7. Modelo de dados

**Banco do portal** (`db/portal.py`): `users`, `permissions`, `access_profiles`,
`access_logs`, `settings`, `classifications`, `storage_locations`, `assets`,
`receipt_cycles`, `movements`, `lot_sequences`, `lots`, `repairs`,
`local_assets`, `load_history`, `printers`, `box_sequences`, `print_log`.

**23 bancos isolados**, um por módulo, em `data/db/<nome>.db`:
`agendamentos_forn`, `atendimento`, `automacoes`, `bancada`, `consulta_times`,
`controle_orcamento_exec`, `destinacao`, `ebs_forms`, `externo`, `indicadores`,
`internalizacao`, `inventario`, `monitoramento`, `obsolescencia`,
`orcamento_manutencao`, `orcamento_spare`, `preparacao`, `projetos`,
`regularizacao`, `reversa`, `separacao`, `trilha`, `venda`.

**Data e hora sempre em UTC.** O SQLite não guarda fuso e devolve a data
"pelada", o que fazia o navegador ler como hora local e mostrar 3 horas no
futuro. `db/_esquema.py` traz o `UtcDateTime`, que recoloca o fuso na leitura —
o mesmo código serve aos dois bancos.

**Coluna nova em banco que já existe** entra por `migrar_colunas`
(`db/_esquema.py`): compara o modelo com o banco e faz `ADD COLUMN` do que
falta, uma por transação, sem nunca apagar nem recriar.

---

## 8. Parâmetros (variáveis de ambiente)

Arquivo do serviço: `/etc/portal_operacoes_spare/environment` (servidor antigo)
ou `data/environment` (servidor novo). Modelo completo em
`deploy/environment.modelo`.

**Regra que vale para todas:** variável **declarada e vazia** encerra a busca.
`HTTPS_PROXY=` significa "aqui não tem proxy" e não cai para o perfil da
máquina — foi o que fazia chamada sair por um proxy que o destino não conhece.

### Núcleo
| Variável | Uso |
|---|---|
| `DATABASE_URL` | Banco do portal. `postgresql+psycopg://…` ou `sqlite:///…`. |
| `PORTAL_SESSION_SECRET` | Assinatura do cookie de sessão. Obrigatória. |
| `SESSION_TTL_MINUTES` | Janela da sessão (padrão 480). |
| `APP_BASE_PATH` | Subcaminho quando publicado atrás de proxy (`/portal-spare`). |
| `API_BARRA_FINAL` | Liga a barra final na API, para proxy que redireciona (o 301 quebraria o POST). |
| `VERIFY_SSL` | Verificação de TLS na saída. |
| `PORTAL_CA_BUNDLE` | PEM com a CA corporativa, para proxy que intercepta o TLS. |

### Bancos isolados
Cada módulo tem `<NOME>_DATABASE_URL`; sem ela, o padrão é
`data/db/<nome>.db` (`config._sqlite`). Instalação antiga que guardava em
`data/` continua sendo usada até o arquivo ser movido.

### Cofre — o que **não** vai no ambiente
Segredo vai para o cofre, na ordem **cofre corporativo → cofre local cifrado →
ambiente** (`core.cofre.obter`):
- **ServiceNow (leitura)**: `SN_API_USER`, `SN_API_PASS`.
- **Automação**: `SN_AUTOMACAO_USUARIO`, `SN_AUTOMACAO_SENHA`.
- **Correios**: `CORREIOS_USUARIO`, `CORREIOS_CHAVE`, `CORREIOS_CARTOES`.
- **Oracle EBS**: `ORACLE_EBS_USER`, `ORACLE_EBS_PASS`, `ORACLE_EBS_DSN`.

> **Dado de acesso a banco é segredo** — não só a senha: endereço, porta,
> instância, schema e usuário. No repositório fica só o **nome** da chave.

### EBS CAPEX API (Orçamento Infra CSC)
`EBS_CAPEX_URL`, `EBS_CAPEX_PROXY`, `EBS_CAPEX_TIMEOUT`, `EBS_CAPEX_VERIFY`,
`EBS_CAPEX_USER`/`PASS`, `EBS_CAPEX_TOKEN`/`_SCHEME`/`_AUTH_HEADER`.

**Câmbio (ARS/UYU → BRL):** a taxa é informada **na tela**
(*Orçamento Infra CSC → Configurações → Câmbio*) e tem prioridade sobre
`EBS_CAPEX_ARS_BRL`/`EBS_CAPEX_UYU_BRL` e sobre a cotação ao vivo
(`EBS_CAPEX_FX_URL`). É a única fonte que quem opera muda sem deploy.

---

## 9. Mapa de rotas

**Páginas:** `/` · `/obsolescencia` · `/controle-orcamento` · `/indicadores` ·
`/consulta-times` · `/cockpit-spare` · `/dash-recebimento` ·
`/dash-centralreparos` · `/dash-estoques`

**Entrega das telas:** `/modulos/<nome>.js` e `/modulos-times/<nome>.js` —
com sessão e permissão conferidas antes de entregar o arquivo.

**API — um prefixo por módulo**, todos sob `/api`:
`auth`, `consulta`, `consulta-times`, `recebimento`, `agendamentos-forn`,
`identificacao`, `internalizacao`, `atendimento`, `reversa`, `preparacao`,
`servicenow`, `automacoes`, `reparos`, `externo`, `separacao`, `projetos`,
`venda`, `destinacao`, `inventario`, `regularizacao`, `torre`, `trilha`,
`obsolescencia`, `orcamento-manutencao`, `orcamento-spare`,
`controle-orcamento-exec`, `indicadores`, `ebs-forms`, `ebs-oracle`,
`monitoramento`, `cofre`, `parametros`, `status`, `public-assets`.

---

## 10. Operação e deploy

**Rodar:**
```bash
cd /var/www/vcreports/portal-spare      # ou /opt/portal-spare-v2, no antigo
python3 -m venv venv && . venv/bin/activate
pip install -r requirements.txt
python3 main.py                          # ou: systemctl start portal_spare
```
`init_db` cria as tabelas na subida. **Módulo novo**: suba uma vez para o
esquema nascer antes de carregar dado.

**Atualizar:** `git pull origin migracao` e **reiniciar o serviço**. Mudança só
de arquivo estático dispensa restart; mudança de `.py` **não** — o processo já
tem o código antigo na memória.

**Migração de banco entre servidores:** ver `docs/MIGRACAO_SERVIDOR_NOVO.md`.
Dois pontos que já custaram caro:
- **Nunca copie um arquivo SQLite com `cp`** enquanto o serviço está no ar: o
  `-wal` fica para trás e o arquivo chega corrompido. Use
  `scripts/exportar_bancos.py`, que consolida e confere por contagem.
- **Postgres → SQLite** passa por `pg_dump --inserts` e
  `scripts/converter_pg_para_sqlite.py`, que **recusa gravar** se houver tabela
  no dump sem correspondente no destino.

**Verificação:** cada assunto tem o seu script em `scripts/verificar_*.py`.
```bash
for f in scripts/verificar_*.py; do python3 "$f" || echo "FALHOU: $f"; done
```

**Gotchas conhecidos:**
- **SQLite 3.26** no servidor — ver o aviso da seção 1.
- **API do ServiceNow**: só **GET** (POST → "Method not Supported").
- **CSP `script-src 'self'`**: JS de página sempre em arquivo externo.
- **Prefixo de proxy**: página autônoma precisa ler
  `<meta name="app-base">`; `fetch("/api/...")` absoluto não funciona quando o
  portal é publicado em `/portal-spare`.

---

## 11. Documentos relacionados

| Arquivo | Assunto |
|---|---|
| `docs/ESTRUTURA.md` | Estrutura de pastas do projeto. |
| `docs/PADRAO_UI_SPARE.md` | Padrão de UI (paleta, tipografia, componentes). |
| `docs/FLUXO_DO_ATIVO.md` | O ciclo do ativo, etapa por etapa. |
| `docs/TRILHA_E_SEPARACAO.md` | Trilha do ativo e separação. |
| `docs/ORCAMENTO_MANUTENCAO.md` | Orçamento de Manutenção (regras e cálculo). |
| `docs/MDM_OBSOLESCENCIA.md` | Obsolescência e integração com o MDM. |
| `docs/CORREIOS_SERVICENOW.md` | Integração Correios/ServiceNow. |
| `docs/EBS_ORACLE_BASE.md` | Camada de acesso ao Oracle EBS. |
| `docs/EBS_FORMS.md` | EBS Forms. |
| `docs/MIGRACAO_SERVIDOR_NOVO.md` | Migração para o servidor novo. |
| `docs/MIGRACAO_OFICIAL.md` | O que entrou na junção das branches, e o que não entrou. |
| `docs/SERVICO_SERVIDOR_NOVO.md` | systemd e publicação no servidor novo. |
| `docs/AMBIENTE_TESTES.md` | Ambiente de testes. |
