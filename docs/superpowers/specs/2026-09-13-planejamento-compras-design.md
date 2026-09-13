# Planejamento de compras — Orçamento Spare

## Objetivo

Aba **Planejamento** dentro do Orçamento Spare com, por item configurado:
histórico mensal de consumo do estoque de reposição, previsão de consumo
para os próximos 12 meses e necessidade de compra (quantidade, valor e
data-limite do pedido).

Só o estoque de **reposição** entra. Inauguração e reforma ficam de fora.

## Fonte dos dados

- **Consumo real**: Separação. Solicitações **enviadas** cujo tipo de
  atendimento consome o estoque de reposição (hoje Frente e Retaguarda e
  Mobilidade, conforme `estoque_<tipo>` na configuração da Separação),
  agrupadas por mês de envio e por modelo. A quantidade é o número de
  unidades bipadas; sem unidade, a quantidade pedida do item.
- **Consumo imputado**: meses anteriores à **data de início do sistema**
  (configurável). Digitado na grade mês × item ou importado por planilha
  (CSV/XLSX com colunas item, mês, quantidade). A partir da data de
  início, o valor é o real e não se edita.
- **Estoque atual**: campo do item, com botão que conta no ServiceNow os
  ativos em estoque de reposição dos modelos do item (mesma consulta da
  Separação, como o usuário logado).

## Item

Um item agrupa um ou mais modelos da Separação (equivalentes). Campos:
nome, modelos, estoque atual (e quando foi atualizado), pedidos em
aberto, lead time em dias, estoque de segurança em dias de consumo, custo
unitário, ativo.

## Previsão

Sem caixa-preta. Método escolhido pelo tamanho da série:

| Meses de histórico | Método |
|---|---|
| < 3 | média simples |
| 3 a 23 | média móvel ponderada (6 meses) + tendência linear amortecida |
| ≥ 24 | mesmo, multiplicado pelo índice sazonal do mês (anos completos) |

P50 é a previsão; P90 = P50 + 1,28 × desvio dos erros de um passo
(backtest sobre a própria série). A tela diz o método e quantos meses
usou. Horizonte padrão: 12 meses.

## Necessidade de compra

Por item:

- estoque de segurança (unidades) = consumo médio previsto por mês ×
  dias de segurança / 30;
- projeção mês a mês: estoque − P50 (+ pedidos em aberto no primeiro mês);
- **mês de ruptura**: primeiro mês em que a projeção cai abaixo da
  segurança; **data-limite do pedido** = primeiro dia desse mês − lead time;
- **necessidade** = máx(0, Σ P50 no horizonte + segurança − estoque −
  pedidos em aberto), também em P90, e em reais pelo custo unitário.

## Arquitetura

- `db/planejamento.py` (banco próprio `data/planejamento_spare.db`):
  `pln_item`, `pln_historico` (item, mês, quantidade imputada),
  `pln_config` (data de início, horizonte).
- `core/previsao.py`: funções puras de previsão e necessidade, testáveis
  sem banco.
- `routers/planejamento.py` (`/api/planejamento`): itens, modelos
  disponíveis, histórico (leitura consolidada, gravação e importação),
  previsão, configuração, contagem no ServiceNow. Permissão: os níveis do
  próprio Orçamento Spare (view lê; edit/admin altera).
- Front: `frontend/controle-orcamento-exec/src/Planejamento.jsx`, aba
  visível só na instância Spare (literal trocado ao servir o bundle).
- Verificação: `scripts/verificar_planejamento.py`.

## Fora de escopo

Estoque de inauguração, integração com pedidos de compra do EBS,
previsão por loja.
