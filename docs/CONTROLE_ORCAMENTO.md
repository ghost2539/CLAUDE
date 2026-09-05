# Controle de Orçamento de Portfólio (CAPEX/OPEX)

Módulo **oculto** do Portal de Operações SPARE: faz parte do sistema, mas
não aparece no menu lateral nem na lista de módulos (`Settings.MODULES`).
O acesso é feito diretamente pela URL:

```
http://<ip-do-servidor>:8901/tv2
```

(`/controle-orcamento` e `/controle-orçamento` redirecionam para `/tv2`.)

## Acesso

- **Livre, sem login** — como a consulta pública. Qualquer pessoa com acesso
  à rede pode ver **e editar** os projetos.
- Se o usuário estiver logado no portal, o nome dele é registrado em
  `updated_by`; caso contrário registra-se `publico@<ip>`.
- As gravações passam pelo rate limit de API por IP (`RATE_LIMIT_API`).
- Para exigir login no futuro, basta chamar `get_session(req)` (ou
  `require_permission`) nos endpoints de `routers/controle_orcamento.py`.

## Modos de execução

| Modo | Como | URL |
|---|---|---|
| Embutido no portal | `main.py` carrega o router em bloco protegido | `http://<ip>:8901/tv2` |
| **Serviço isolado** | `apps/controle_orcamento.py` + `deploy/controle_orcamento.service` (instalador em `deploy/controle_orcamento_instalar.sh`) | `http://<ip>:8902/tv2` |

No modo isolado nada do portal é carregado: atualizações do portal não
afetam a tela, e ela pode ser instalada sozinha em outro servidor. Os dois
modos podem apontar para o mesmo banco. Migração e backup: ver
`docs/MIGRACAO_CONTROLE_ORCAMENTO.md` e `scripts/controle_orcamento_dados.py`
(exporta/importa JSON).

## Isolamento do restante do portal

- `main.py` carrega o router do módulo dentro de `try/except`: se o import
  falhar (arquivo ausente, dependência, erro de código), o erro vai para o
  log e o portal sobe normalmente sem o `/tv2`.
- Nada é executado em tempo de import: engine, conexão e criação da tabela
  acontecem só na primeira requisição à API do módulo.
- Erros do banco do módulo viram `503` apenas nas rotas do módulo.

## Banco de dados separado

Os projetos ficam em um banco **exclusivo** do módulo, definido em
`database_orcamento.py` (engine, sessão e modelo próprios). Nada é criado no
banco do portal (`database.py`), e uma falha nesse banco não derruba as demais
telas: a inicialização no startup é protegida e o erro só aparece ao usar o
módulo.

| Configuração | Valor |
|---|---|
| Padrão | arquivo SQLite em `data/controle_orcamento.db` (pasta já gravável pelo serviço, `ReadWritePaths` do systemd) |
| Outro banco | variável `CONTROLE_ORCAMENTO_DATABASE_URL`, ex.: `postgresql+psycopg://usuario:senha@127.0.0.1:5432/controle_orcamento_db` |
| Tabela | `budget_projects` |

Se a URL for Postgres, o módulo usa **o mesmo driver** da `DATABASE_URL` do
portal (ex.: `psycopg2`), mesmo que a variável venha com outro. Lembre-se de
liberar o banco novo no `pg_hba.conf` com as mesmas regras do banco do portal.

No SQLite o módulo ativa `journal_mode=WAL` e `busy_timeout` para suportar
vários usuários editando ao mesmo tempo. Na primeira execução, com a tabela
vazia, são carregados 8 projetos de exemplo (`database_orcamento.SEED`); eles
podem ser excluídos pela própria tela.

Backup: copie `data/controle_orcamento.db` (e os arquivos `-wal`/`-shm`, se
existirem) ou use o backup do banco dedicado.

## O que a tela faz

- 6 cards de KPI (Demandas, Valor Total, CAPEX Aprovado, Realizado,
  Comprometido, A Realizar) e 4 gráficos (categoria, estágio, prioridade e
  evolução mensal em curva "S") calculados em tempo real a partir da tabela.
- Tabela de projetos com **edição inline**: ID (código), projeto, tipo
  (CAPEX/OPEX), categoria, área, estágio, prioridade, orçamento, comprometido,
  realizado e vencimento. Colunas "A Realizar", "% Realizado" e "Status" são
  calculadas.
- **Gravação automática**: cada alteração atualiza a tela na hora e é enviada
  ao servidor ~0,6 s depois (agrupando a digitação). O cabeçalho mostra
  "Salvando…", "Salvo HH:MM:SS" ou "Erro ao salvar" com botão de nova
  tentativa. Alterações pendentes são enviadas ao fechar/ocultar a aba.
- Filtros (ano, tipo, área, categoria, prioridade, estágio, status) aplicados
  a KPIs, gráficos e tabela; incluir, duplicar e excluir projetos; botão
  "Atualizar" para recarregar do banco.

## Categorias editáveis

As categorias de projeto são cadastradas pelo usuário (tabela
`budget_categories`, com nome e cor) pelo botão **Categorias** acima da
tabela. As cinco iniciais (Manutenção, Expansão, Estratégico,
Legal/Compliance, Outros) são criadas só quando a tabela está vazia.

- Renomear uma categoria atualiza automaticamente os projetos que a usam.
- A exclusão é bloqueada enquanto houver projeto vinculado (a tela informa
  quantos). Troque a categoria desses projetos antes.
- A cor escolhida é usada no gráfico "Distribuição por Categoria".

## API

Todas as rotas são públicas e retornam JSON.

| Método | Rota | Descrição |
|---|---|---|
| `GET` | `/api/controle-orcamento/projetos` | Lista projetos (ordem de cadastro) e opções dos selects. |
| `POST` | `/api/controle-orcamento/projetos` | Cria projeto (campos ausentes recebem padrão). |
| `PATCH` | `/api/controle-orcamento/projetos/{id}` | Atualiza apenas os campos enviados. |
| `DELETE` | `/api/controle-orcamento/projetos/{id}` | Exclui. |
| `POST` | `/api/controle-orcamento/projetos/{id}/duplicar` | Cria cópia logo após o original. |
| `GET` | `/api/controle-orcamento/categorias` | Lista categorias (`id`, `nome`, `cor`). |
| `POST` | `/api/controle-orcamento/categorias` | Cria categoria (`nome`, `cor` opcional `#rrggbb`). `409` se já existir. |
| `PATCH` | `/api/controle-orcamento/categorias/{id}` | Renomeia e/ou recolore; o rename propaga aos projetos. |
| `DELETE` | `/api/controle-orcamento/categorias/{id}` | Exclui; `409` se houver projeto usando. |

Campos: `codigo`, `nome`, `tipo` (CAPEX/OPEX), `categoria` (precisa existir no cadastro), `area`, `estagio`,
`prioridade`, `orcamento`, `comprometido`, `realizado` (≥ 0), `vencimento`
(`AAAA-MM-DD` ou vazio). Valores fora das opções retornam `422`.

## Arquivos

| Caminho | Função |
|---|---|
| `routers/controle_orcamento.py` | Página `/tv2` e API REST. |
| `database_orcamento.py` | Engine/sessão/modelo do banco separado, seed e inicialização protegida. |
| `config.py` | `ORCAMENTO_DATABASE_URL` (variável `CONTROLE_ORCAMENTO_DATABASE_URL`). |
| `static/controle-orcamento/index.html` | Casca HTML que carrega o bundle. |
| `static/controle-orcamento/app.js` / `app.css` | **Bundle compilado** (React + Recharts + Tailwind), versionado no repositório. |
| `frontend/controle-orcamento/` | Código-fonte (React/JSX, Tailwind) e script de build. |

O servidor não possui Node.js e a CSP do portal permite scripts apenas da
própria origem (`script-src 'self'`); por isso as dependências são
empacotadas em um único arquivo e commitadas, sem uso de CDN.

## Como alterar e recompilar

```bash
cd frontend/controle-orcamento
npm install          # uma vez (requer Node 18+)
npm run build        # gera static/controle-orcamento/app.js e app.css
# ou: npm run watch  # recompila o JS a cada alteração
```

Depois do build, faça commit dos arquivos em `static/controle-orcamento/` e
reinicie o serviço (`systemctl restart portal_spare`). O hash dos assets muda
automaticamente na URL (`?v=...`), então não é preciso limpar cache.
