# Estrutura do repositório

Regra que organiza tudo o que está aqui: **cada pasta tem um papel só**.
Rota não conhece driver de banco, banco não conhece HTTP, integração externa
não conhece nem um nem outro, e o que roda como serviço separado mora em
`apps/`. A raiz guarda apenas o ponto de entrada e a configuração.

```
main.py                  Ponto de entrada do portal (uvicorn main:app)
config.py                Configuração central — lida por tudo, inclusive apps/ e scripts/
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
  orcamento.py           Controle de Orçamento de portfólio (/tv2)
  orcamento_exec.py      Controle de Orçamento — execução CAPEX

routers/                 As APIs do portal — uma por área funcional
  auth · consulta · recebimento · reparos · status · parametros
  identificacao · servicenow · correios · encerramento · rastreio (tv)
  indicadores · automacoes · monitoramento
  controle_orcamento · controle_orcamento_exec · public_assets · helpers

integracoes/             Clientes de sistemas externos (sem rota, sem banco)
  ebs_service.py         API REST do EBS
  ebs_oracle.py          Consultas diretas na base Oracle do EBS
  ebs_logged.py          Raspagem autenticada do EBS

apps/                    Aplicativos com serviço PRÓPRIO, fora do portal
  controle_orcamento.py  Controle de Orçamento isolado (porta 8902)
  consulta_times/        Consulta de Ativos para os times (porta 8502)

static/                  Front-end servido ao navegador (público por definição)
  index.html · app.js · app.css · modules/*.js
  controle-orcamento/ · controle-orcamento-exec/ · indicadores/ · identificacao/

frontend/                Fontes React dos painéis; o build sai em static/

data/                    TUDO que é gravado em disco (fora do repositório)
  db/                    Bancos SQLite dos módulos isolados
  uploads/               Arquivos enviados pelos usuários
  referencias/           Cadastros de apoio versionados (ex.: locations_sn.json)

deploy/                  Instalação e serviços systemd
  portal_spare.service · controle_orcamento.service · consulta_times.service
  install.sh · controle_orcamento_instalar.sh · generate-cert.sh
  requirements-controle-orcamento.txt

scripts/                 Utilitários de operação
  backup.sh              Backup total (Postgres + SQLite + env + uploads)
  controle_orcamento_dados.py · migrar_pg_para_mysql.py

docs/                    Documentação
```

## Onde ficam os bancos

| Banco | Onde | Definido em |
|---|---|---|
| Portal (principal) | PostgreSQL | `DATABASE_URL` |
| Indicadores | `data/db/indicadores.db` | `INDICADORES_DATABASE_URL` |
| Automações | `data/db/automacoes.db` | `AUTOMACOES_DATABASE_URL` |
| Monitoramento | `data/db/monitoramento.db` | `MONITORAMENTO_DATABASE_URL` |
| Controle de Orçamento (/tv2) | `data/db/controle_orcamento.db` | `CONTROLE_ORCAMENTO_DATABASE_URL` |
| Controle de Orçamento — CAPEX | `data/db/controle_orcamento_exec.db` | `ORCAMENTO_EXEC_DATABASE_URL` |

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
| `/api/reparos` | reparos | `/api/automacoes` | automacoes |
| `/api/status` | status | `/api/monitor` | monitoramento |
| `/api/parametros` | parametros | `/api/public-assets` | public_assets |
| `/api/lotes` `/api/dashboard` `/api/tv` | tv / helpers | `/api/controle-orcamento[-exec]` | controle_orcamento[_exec] |

As páginas que não são API (`/`, `/tv`, `/tv2`, `/indicadores`,
`/controle-orcamento`) são servidas pelos seus próprios routers, com o
HTML em `static/`.

## Regras para não voltar a misturar

1. Arquivo novo de banco entra em `db/`, com URL declarada em `config.py`.
2. Rota nova entra em `routers/`, nunca na raiz.
3. Cliente de sistema externo entra em `integracoes/`.
4. Nada gravado em disco fora de `data/`.
5. Nada que precise de login pode morar em `static/` — ali é público.
6. Serviço systemd e instalador entram em `deploy/`.
