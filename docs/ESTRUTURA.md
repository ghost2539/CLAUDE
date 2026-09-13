# Estrutura do repositório

Regra que organiza tudo o que está aqui: **cada pasta tem um papel só**.
Rota não conhece driver de banco, banco não conhece HTTP, e integração
externa não conhece nem um nem outro. A raiz guarda apenas o ponto de
entrada e a configuração.

**Tudo roda num processo só**, na porta 8901: o servidor novo não tem root,
então manter vários serviços não é opção. Telas que antes eram aplicativos
separados (Consulta de Ativos dos times, na 8502) viraram router do portal.
Para não quebrar os endereços salvos pelos times, o mesmo processo abre um
segundo *listener* na 8502 servindo **só** essa tela (`CONSULTA_TIMES_PORTA`;
0 desliga). Continua sendo um processo, um serviço e um deploy.

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
  orcamento_spare.py     Orçamento do SPARE (CAPEX da área)

routers/                 As APIs do portal — uma por área funcional
  auth · consulta · recebimento · reparos · status · parametros
  identificacao · servicenow · correios · encerramento · rastreio (tv)
  indicadores · automacoes · monitoramento
  controle_orcamento_exec · orcamento_spare · public_assets · helpers
  consulta_times · cockpit (telas de TV)

integracoes/             Clientes de sistemas externos (sem rota, sem banco)
  ebs_service.py         API REST do EBS
  ebs_oracle.py          Consultas diretas na base Oracle do EBS
  ebs_logged.py          Raspagem autenticada do EBS

static/                  Front-end servido ao navegador (público por definição)
  index.html · app.js · app.css · modules/*.js
  controle-orcamento-exec/ · indicadores/ · identificacao/ · cockpit/
  consulta-times/

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
| Orçamento do SPARE | `data/db/orcamento_spare.db` | `ORCAMENTO_SPARE_DATABASE_URL` |
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
| `/api/lotes` `/api/dashboard` | helpers | `/api/controle-orcamento-exec` | controle_orcamento_exec |
| `/api/orcamento-spare` | orcamento_spare | `/api/cockpit` | cockpit (público) |
| `/api/orcamento-manutencao` | orcamento_manutencao | `/api/obsolescencia` | obsolescencia |
| `/api/trilha` | trilha (núcleo) | `/api/torre` | torre |
| `/api/separacao` | separacao | `/api/atendimento` | atendimento |
| `/api/bancada` | bancada | `/api/preparacao` | preparacao |
| `/api/destinacao` | destinacao | `/api/externo` | externo |
| `/api/projetos` | projetos | `/api/reversa` | reversa |
| `/api/inventario` | inventario | `/api/regularizacao` | regularizacao |
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
