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
  ebs_forms.py           EBS Forms (RPA): execuções, roteiros e ativos coletados

routers/                 As APIs do portal — uma por área funcional
  auth · consulta · recebimento · reparos · status · parametros
  identificacao · servicenow · correios · encerramento · rastreio (tv)
  indicadores · automacoes · monitoramento
  controle_orcamento_exec · orcamento_spare · public_assets · helpers
  consulta_times · cockpit (telas de TV) · ebs_forms (RPA)

integracoes/             Clientes de sistemas externos (sem rota, sem banco)
  ebs_service.py         API REST do EBS
  ebs_oracle.py          Consultas diretas na base Oracle do EBS
  ebs_logged.py          Raspagem autenticada do EBS
  ebs_forms.py           RPA sobre o cliente Oracle Forms do EBS (SSO → jnlp → JVM)
  ebs_forms_java/        LancadorForms.java — JVM própria que substitui o Java Web Start

static/                  Front-end servido ao navegador (público por definição)
  index.html · app.js · app.css · modules/*.js
  controle-orcamento-exec/ · indicadores/ · identificacao/ · cockpit/
  consulta-times/ · ebs-forms/ (tela de administração do RPA)

frontend/                Fontes React dos painéis; o build sai em static/

data/                    TUDO que é gravado em disco (fora do repositório)
  db/                    Bancos SQLite dos módulos isolados
  uploads/               Arquivos enviados pelos usuários
  referencias/           Cadastros de apoio versionados (ex.: locations_sn.json)
  ebs_forms/             RPA do Forms: bin/ (classes), jars/ (cache), capturas/,
                         logs/ (jvm-*.log, xvfb.log, javac.log), depuracao/ (HTML do SSO)

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
  cofre.py               Segredos: definir | conferir | listar
  ebs_forms_preparar.sh  Confere/instala/compila o RPA do Forms; `testar` abre o Forms

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
| EBS Forms (RPA) | `data/db/ebs_forms.db` | `EBS_FORMS_DATABASE_URL` |

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
| `/api/ebs-forms` | ebs_forms | | |

As páginas que não são API (`/`, `/indicadores`, `/controle-orcamento`) são
servidas pelos seus próprios routers, com o HTML em `static/`.

`/controle-orcamento` tem **permissão exclusiva**: exige sessão do portal e
o módulo `orcamento` liberado para o usuário (`view` para ler, `create` para
incluir, `edit` para alterar/excluir/sincronizar, `admin` para a trilha de
acesso). Cada abertura de tela e cada gravação ficam registradas na tabela
`budget_acessos`, consultável em Parâmetros → Acessos & Alertas.

`/api/orcamento-spare` segue o mesmo desenho, com o módulo `orcamento_spare`.

`/api/ebs-forms` usa o módulo `ebs_forms` (`view` acompanha execuções e
capturas, `create` dispara consultas, `admin` edita roteiros e roda o teste
de abertura). É o RPA sobre o cliente Oracle Forms do EBS: roda em segundo
plano numa tela virtual (Xvfb) dentro do mesmo processo do portal — nada de
serviço à parte. Guia operacional em `docs/EBS_FORMS.md`.

## Variáveis de ambiente do EBS Forms

Todas com prefixo `EBS_FORMS_`, declaradas em `config.py` e no
`deploy/environment.modelo`. Usuário e senha do robô (`EBS_FORMS_USER`,
`EBS_FORMS_PASS`) ficam **só no cofre** — nunca no environment.

| Variável | Padrão | Papel |
|---|---|---|
| `EBS_FORMS_DATABASE_URL` | `data/db/ebs_forms.db` | Banco isolado do módulo |
| `EBS_FORMS_HOME_URL` | `http://ebscorporativo…/OA_HTML/OA.jsp?OAFunc=OAHOMEPAGE` | Home do EBS (entrada do SSO) |
| `EBS_FORMS_FUNCAO_URL` | vazio | Link `RF.jsp?function_id=…` da função (recomendado) |
| `EBS_FORMS_FUNCAO` | `Informações Financeiras` | Nome da função procurado na home se a URL faltar |
| `EBS_FORMS_RESPONSABILIDADE` | `RENNER_FA_CONSULTA` | Responsabilidade (informativa, aparece no status) |
| `EBS_FORMS_LIVROS` | `FA_RENNER,FA_RENNER_FIS` | Livros tentados nessa ordem |
| `EBS_FORMS_PROXY` | vazio | Proxy HTTP do SSO; vazio ignora o proxy do ambiente |
| `EBS_FORMS_VERIFY` | `false` | Verificar TLS no SSO |
| `EBS_FORMS_TIMEOUT` | `40` | Timeout (s) das requisições HTTP |
| `EBS_FORMS_DISPLAY` | `:99` | Display do Xvfb |
| `EBS_FORMS_TELA` | `1280x900x24` | Tamanho da tela virtual |
| `EBS_FORMS_JAVA` / `EBS_FORMS_JAVAC` | vazio (`PATH`) | Binários do Java a usar |
| `EBS_FORMS_JAVA_OPCOES` | vazio | Opções extras para a JVM |
| `EBS_FORMS_CLASSE` | `oracle.forms.engine.Main` | Classe do applet Forms |
| `EBS_FORMS_ESPERA_JVM` | `180` | Segundos até o lançador responder "pronto" |

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
