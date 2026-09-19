# Chamado de lançamento de NF no ServiceNow

O OK do Lançamento (Internalização) abre o pedido do item de catálogo
`SN_CATALOGO_LANCAMENTO_NF_ITEM` (padrão `66831e771b37b5901e870e9fe54bcb11`)
**como o usuário logado**, com os cookies SSO da sessão do portal. A conta
de serviço não participa: ela só lê, e o pedido precisa nascer no nome de
quem operou.

## Sequência

1. `g_ck` (X-UserToken) lido de uma página de UI.
2. `GET /api/now/ui/user/current_user` → `requested_for`.
3. `GET /api/now/table/sys_user` pelo e-mail de `SN_CATALOGO_LANCAMENTO_NF_IMPACTO` → `u_impact_employee`.
4. `GET /api/sn_sc/servicecatalog/items/{sys_id}` → variáveis, opções e o bloco multilinha.
5. Variáveis montadas pela tabela abaixo; opções resolvidas pelo **rótulo**.
6. `POST .../order_now` → REQ; `GET sc_req_item?request=` → RITM.
7. Anexos no RITM: planilha Cadastro de Ativos e PDF(s) da NF.

Falha em qualquer passo não desfaz o lançamento: o processo fica
"chamado pendente" com o motivo e o botão **Reenviar ao ServiceNow**.

## Campos

| Variável | Valor |
|---|---|
| `requested_for` | usuário logado |
| `u_phone` | `SN_CATALOGO_LANCAMENTO_NF_TELEFONE` só se editável |
| `u_impact_employee` | `SN_CATALOGO_LANCAMENTO_NF_IMPACTO` |
| `question_brand` | Renner → `Renner Brasil`; Camicado → `Camicado`; Youcom → `Youcom` |
| `question_document_type` | `Material` |
| `question_demand_type` | `Others` |
| `question_has_a_purchase_order` | `Yes`/`Sim` |
| `question_supplier` | fornecedor do agendamento |
| `question_nature_of_the_transaction` | `Fixed assets (CAPEX)` |
| `question_the_document_is_being_sent_after_the_deadline` | `No`/`Não` |
| `question_number_of_documents_being_sent` | quantidade de NFs |
| bloco multilinha (o que tem `question_document_number`) | uma linha por NF: `question_order_number_po`, `question_document_number`, `question_due_date` |
| `question_description` | `Prezados, gentileza enviar pra lançamento e cadastro de patrimonio a {NF} referente à PO {PO}.` |

## Se o formulário mudar

O botão **Conferir formulário do ServiceNow** (Lançamento) descreve o item
como o usuário logado: nome, tipo, obrigatoriedade e opções de cada
variável, mais o que seria enviado. Variável que sumiu aparece em
"faltantes"; o mapa está em `integracoes/sn_catalogo.py::montar_variaveis_lancamento_nf`.
