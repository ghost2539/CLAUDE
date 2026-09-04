# EBS Oracle — base de acesso e catálogo padrão

Referência da **camada de acesso** ao Oracle E-Business Suite (produção `EBSPRD`)
e do **dicionário de objetos padrão** (não customizados) sobre os quais montamos
as consultas. As consultas de negócio são configuradas por nós; aqui fica a base.

> Módulo de código: `ebs_oracle.py` (só-leitura, timeout e teto de linhas).

---

## 1. Dados de acesso (o que importa)

Tudo vem do **cofre do EBS** (separado do cofre dos Correios), via
`from vcreports_secrets import s`.

| Segredo | Default | Papel |
|---|---|---|
| `ORACLE_EBS_USER` | `inframon` | Usuário de leitura/monitoração no banco. |
| `ORACLE_EBS_PASS` | — (obrigatório) | Senha do usuário. Só no cofre. |
| `ORACLE_EBS_DSN` | `rac04-scan:1521/EBSPRD` | Destino: **SCAN do RAC : porta / service name**. `EBSPRD` = **produção**. |
| `ORACLE_CLIENT_LIB_DIR` | `/usr/lib/oracle/21/client64/lib` | Oracle **Instant Client 21** (modo *thick*). |

**Anatomia do DSN** `rac04-scan:1521/EBSPRD`:
- `rac04-scan` — endereço **SCAN** do cluster RAC (balanceia entre os nós).
- `1521` — porta do listener.
- `EBSPRD` — **service name** do banco de produção.

Formato alternativo (Easy Connect com failover já é resolvido pelo SCAN). Se um
dia precisarem apontar para outro ambiente, troca-se só o service name no cofre.

---

## 2. Modelo de segurança (produção)

`ebs_oracle.py` aplica, por padrão, em toda consulta:

- **`SET TRANSACTION READ ONLY`** — a sessão não aceita gravação; qualquer DML falha.
- **Sem commit** — `rollback()` + `close()` sempre no `finally`.
- **`call_timeout` = 60 s** — o banco corta a chamada se passar disso.
- **`max_rows` = 5000** — teto de linhas ao explorar (passe `0` para sem teto,
  com consciência do volume).
- **Bind variables** (`:param`) — nunca concatenar valor em SQL.

> As consultas continuam podendo ser **pesadas** em produção. Ao explorar, filtre
> por um projeto/PO conhecido, use `p_days` pequeno e evite horário de pico.

---

## 3. Fluxo para "ir montando" (exploração segura)

Três passos, todos só-leitura:

```bash
# 1) Validar o acesso (quem sou, qual banco, hora do servidor)
python3 ebs_oracle.py check

# 2) Listar objetos padrão acessíveis por prefixo (ex.: Projetos)
python3 ebs_oracle.py list PA_PROJECTS

# 3) Descrever as colunas de um objeto
python3 ebs_oracle.py describe PA_PROJECTS_ALL
```

No código:
```python
import ebs_oracle
ebs_oracle.check_access()                 # dict com session_user, db_name, ...
ebs_oracle.list_objects("PO")             # objetos de Compras acessíveis
ebs_oracle.describe("PO_HEADERS_ALL")     # colunas do objeto
ebs_oracle.query("SELECT ... :p", {"p": 123})   # sua consulta, com binds
```

Descoberta pelo dicionário de dados (padrão Oracle, sempre disponível):
`ALL_OBJECTS`, `ALL_TAB_COLUMNS`, `ALL_TABLES`, `ALL_VIEWS`, `ALL_CONSTRAINTS`,
`ALL_CONS_COLUMNS` (para achar PK/FK e as chaves de join).

---

## 4. Catálogo de objetos padrão do EBS

Objetos *standard* (schema **`APPS`**, sufixo `_ALL` = particionado por
`ORG_ID`/operating unit). Colunas-chave e joins mais usados. **Valide os grants**
da conta com `list_objects`/`describe` antes de assumir acesso a cada um.

### 4.1 Projetos e Orçamento (PA — Project Accounting)
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `PA_PROJECTS_ALL` | Projetos. `segment1` = número do projeto; `name`. | PK `project_id` |
| `PA_TASKS` | Tarefas do projeto. | `project_id` → projeto; PK `task_id` |
| `PA_RESOURCE_ASSIGNMENTS` | Alocações de recurso (ligam tarefa ao orçamento). | `task_id`; PK `resource_assignment_id` |
| `PA_BUDGET_VERSIONS` | Versões de orçamento. `current_flag`, `budget_status_code` (`B`=baseline). | PK `budget_version_id`; `project_id` |
| `PA_BUDGET_LINES` | Linhas de orçamento. `burdened_cost`, `raw_cost`. | `budget_version_id`, `resource_assignment_id` |
| `PA_EXPENDITURE_ITEMS_ALL` | Custos realizados lançados no projeto. | `project_id`, `task_id` |

> **Saldo/orçamento aprovado** = `PA_BUDGET_LINES` da versão *current/baseline*.

### 4.2 Compras (PO — Purchasing)
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `PO_HEADERS_ALL` | Cabeçalho do pedido. `segment1` = nº do PO; `authorization_status`; `type_lookup_code` (`STANDARD`/`BLANKET`/`CONTRACT`). | PK `po_header_id`; `vendor_id`, `org_id` |
| `PO_LINES_ALL` | Linhas do pedido. `line_num`, `item_id`, `unit_price`, `item_description`. | PK `po_line_id`; `po_header_id` |
| `PO_LINE_LOCATIONS_ALL` | Entregas/remessas (shipments). `quantity`, `quantity_received/billed/cancelled`, `need_by_date`, `amount_billed`. | PK `line_location_id`; `po_line_id` |
| `PO_DISTRIBUTIONS_ALL` | Rateio contábil/projeto. `project_id`, `task_id`, `expenditure_type`, `destination_type_code`. | PK `po_distribution_id`; `line_location_id`, `po_line_id` |
| `PO_ACTION_HISTORY` | Histórico de ações (aprovação/**rejeição**). `action_code`, `note`, `action_date`. | `object_id` (= header ou line_location), `object_type_code` |
| `PO_REQUISITION_HEADERS_ALL` | Requisições (RC) — cabeçalho. `segment1`, `authorization_status`. | PK `requisition_header_id` |
| `PO_REQUISITION_LINES_ALL` | RC — linhas. `item_description`, `quantity`, `unit_price`. | PK `requisition_line_id`; `requisition_header_id` |
| `PO_REQ_DISTRIBUTIONS_ALL` | RC — rateio (liga ao projeto). | `requisition_line_id`, `project_id` |

> **Comprometido vs Realizado**: use `PO_LINE_LOCATIONS_ALL.amount_billed`
> (0 = comprometido; > 0 = realizado/faturado).

### 4.3 Fornecedores (AP — Payables)
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `AP_SUPPLIERS` | Fornecedores (nome atual). `vendor_name`. | PK `vendor_id` |
| `PO_VENDORS` | View clássica de fornecedores (ainda usada em PO). `vendor_name`. | `vendor_id` |
| `AP_SUPPLIER_SITES_ALL` | Sites/filiais do fornecedor. | `vendor_id`, `org_id` |

### 4.4 Itens e Inventário (INV)
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `MTL_SYSTEM_ITEMS_B` | Cadastro de itens. `segment1` = código do item; `description`, `item_type`, `primary_uom_code`. | PK (`inventory_item_id`, `organization_id`) |
| `MTL_ITEM_CATEGORIES` / `MTL_CATEGORIES_B` | Categorias dos itens. | `inventory_item_id`, `category_id` |
| `MTL_PARAMETERS` | Organizações de estoque. | PK `organization_id` |

> `organization_id = 0` costuma ser o item mestre; `101` uma org operacional
> (o valor correto varia por instância — confirme com `describe`/dados).

### 4.5 Organização e Contabilidade (HR / GL)
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `HR_OPERATING_UNITS` | Unidades operacionais (OU). `name`, `organization_id`. | join por `org_id` das `_ALL` |
| `HR_ALL_ORGANIZATION_UNITS` | Todas as organizações. | `organization_id` |
| `GL_CODE_COMBINATIONS` | Combinações contábeis (conta). `segment1..n`. | PK `code_combination_id` |
| `GL_JE_HEADERS` / `GL_JE_LINES` | Lançamentos contábeis. | `je_header_id` |
| `FND_USER` | Usuários da aplicação. `user_name`, `user_id`. | `user_id` |

### 4.6 Ativo Fixo (FA — Fixed Assets) — para o lado de ativos
| Objeto | Conteúdo | Chaves / join |
|---|---|---|
| `FA_ADDITIONS_B` | Cadastro do ativo. `asset_number`, `description`, `tag_number`, `serial_number`. | PK `asset_id` |
| `FA_BOOKS` | Livro do ativo. `cost`, `date_placed_in_service`, `book_type_code`. | `asset_id` |
| `FA_CATEGORIES_B` | Categorias de ativo. | `category_id` |
| `FA_DEPRN_SUMMARY` | Depreciação acumulada. | `asset_id`, `book_type_code` |

> É a fonte natural do que hoje o portal chama de imobilizado/ativo/etiqueta/série
> (o módulo Consulta/Recebimento).

---

## 5. Convenções úteis do EBS

- **Sufixo `_ALL`** → tabela multi-org, filtrada por `ORG_ID`. Se precisar filtrar
  por empresa/OU, junte com `HR_OPERATING_UNITS` por `org_id`.
- **`segment1`** → quase sempre o "número" de negócio (projeto, PO, RC, item).
- **`authorization_status`** → `INCOMPLETE`/`IN PROCESS`/`APPROVED`/`REJECTED`/`CLOSED`.
- **Datas** → o driver devolve em ISO; no SQL use `TRUNC`, `SYSDATE`, `ADD_MONTHS`.
- **Números com zero à esquerda** → passe como **texto** no bind (o CLI já preserva).
- **Views `APPS.*`** → o schema `APPS` expõe synonyms para as tabelas dos módulos;
  por isso quase tudo é acessível como `APPS.<OBJETO>`.

---

## 6. Próximos passos sugeridos

1. Rodar `ebs_oracle.py check` no servidor novo para confirmar credenciais/DSN.
2. Rodar `list`/`describe` nos objetos que interessam e conferir os grants da conta.
3. Preencher `QUERIES` em `ebs_oracle.py` com as consultas de negócio.
4. (Opcional) Ligar `saldo`/`po`/`rc` ao módulo Controle de Orçamento para
   preencher aprovado/comprometido/realizado a partir do EBS.
