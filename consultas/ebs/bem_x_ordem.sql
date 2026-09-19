-- As ordens de venda a que um BEM (patrimônio) está vinculado.
--
-- Mesma finalidade da "OM/FA - Consulta Bem x Ordem" do Funcional de
-- Consultas Dinâmicas, e com os mesmos cinco parâmetros BEM_1..BEM_5. Os
-- que ficarem em branco não atrapalham: NULL nunca casa no IN, então
-- preencher só o BEM_1 consulta um bem só.
--
-- COMO O BEM CHEGA NA ORDEM
-- -------------------------
-- O ativo fixo não guarda número de ordem em lugar nenhum. Quem liga os dois
-- é o NÚMERO DE SÉRIE, que é o mesmo dos dois lados:
--
--   FA_ADDITIONS_B            o bem, com serial_number
--     → MTL_UNIT_TRANSACTIONS      cada movimento daquele serial
--       → MTL_MATERIAL_TRANSACTIONS  o movimento em si
--         → OE_ORDER_LINES_ALL        trx_source_line_id = line_id
--           → OE_ORDER_HEADERS_ALL    a ordem
--
-- `transaction_source_type_id = 2` é "Ordem de Venda" no padrão do EBS. Sem
-- esse filtro entram transferência, ajuste e inventário, que não têm ordem
-- nenhuma — e aí o `trx_source_line_id` aponta para OUTRA tabela, trazendo
-- linha de ordem que não tem a ver com o bem. Filtrar é o que impede a
-- consulta de mentir com cara de certeza.
--
-- Um bem costuma aparecer em MAIS DE UMA ordem (saiu, voltou, foi remanejado).
-- Isso não é duplicidade: são movimentos diferentes, e por isso `data_ordem`
-- ordena do mais recente para o mais antigo.
--
-- A NF
-- ----
-- Vem pelo AutoInvoice, que é o caminho padrão do EBS da ordem para a fatura:
-- a linha de AR guarda o número da ordem em `interface_line_attribute1` e o
-- `line_id` em `interface_line_attribute6`, com
-- `interface_line_context = 'ORDER ENTRY'`.
--
-- ⚠ CONFIRA ANTES DE ENTREGAR. Duas coisas dependem desta instalação e não
-- dá para afirmar daqui:
--
--   1. Se a ligação bem→ordem de vocês é mesmo pelo serial. Se a consulta
--      atual do DNQ usa outro caminho (tabela própria, referência na ordem),
--      o resultado vai divergir. `bem_x_ordem_conferir.sql` mostra em qual
--      degrau a corrente arrebenta.
--   2. Se o número da NF é o `trx_number` do AR. Na localização brasileira,
--      série e número às vezes ficam em GLOBAL_ATTRIBUTE da
--      RA_CUSTOMER_TRX_ALL, com índice que muda por instalação — o mesmo
--      problema que `nf_onde_esta_a_chave.sql` resolve para a chave.

WITH bens AS (
    SELECT fa.asset_id,
           fa.asset_number,
           fa.serial_number,
           fa.description AS bem_descricao
    FROM APPS.FA_ADDITIONS_B fa
    WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)),
                                           UPPER(TRIM(:BEM_2)),
                                           UPPER(TRIM(:BEM_3)),
                                           UPPER(TRIM(:BEM_4)),
                                           UPPER(TRIM(:BEM_5)))
      AND fa.serial_number IS NOT NULL
),
movimentos AS (
    -- DISTINCT porque o mesmo serial pode ter vários movimentos apontando
    -- para a MESMA linha de ordem (separação, embarque, ajuste de embarque).
    -- Sem ele, a mesma ordem apareceria três vezes por motivo nenhum.
    SELECT DISTINCT b.asset_number,
                    b.serial_number,
                    b.bem_descricao,
                    mmt.trx_source_line_id AS line_id
    FROM bens b
    JOIN APPS.MTL_UNIT_TRANSACTIONS mut
      ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(b.serial_number))
    JOIN APPS.MTL_MATERIAL_TRANSACTIONS mmt
      ON mmt.transaction_id = mut.transaction_id
     AND mmt.transaction_source_type_id = 2
    WHERE mmt.trx_source_line_id IS NOT NULL
),
nf AS (
    -- Uma linha de ordem pode virar mais de uma linha de fatura (entrega
    -- parcial). DISTINCT no cabeçalho da fatura evita repetir a ordem por
    -- causa disso.
    SELECT DISTINCT TO_NUMBER(ctl.interface_line_attribute6) AS line_id,
                    ct.trx_number                            AS nf_numero,
                    ct.trx_date                              AS nf_data
    FROM APPS.RA_CUSTOMER_TRX_LINES_ALL ctl
    JOIN APPS.RA_CUSTOMER_TRX_ALL ct ON ct.customer_trx_id = ctl.customer_trx_id
    WHERE ctl.interface_line_context = 'ORDER ENTRY'
      AND ctl.interface_line_attribute6 IS NOT NULL
      -- REGEXP porque interface_line_attribute6 é VARCHAR2: um valor não
      -- numérico ali faz o TO_NUMBER estourar ORA-01722 e derruba a consulta
      -- inteira, não só aquela linha.
      AND REGEXP_LIKE(ctl.interface_line_attribute6, '^[0-9]+$')
)
SELECT h.order_number                        AS ordem,
       NVL(fs.meaning, h.flow_status_code)   AS status_ordem,
       h.order_type_id                       AS id_tipo_ordem,
       tt.name                               AS tipo_ordem,
       h.ordered_date                        AS data_ordem,
       msi.segment1                          AS cod_item,
       msi.description                       AS descricao_item,
       m.asset_number                        AS bem,
       m.serial_number                       AS numero_serie,
       m.bem_descricao                       AS descricao_bem,
       l.line_number                         AS linha_ordem,
       l.ordered_quantity                    AS quantidade,
       nf.nf_numero                          AS numero_nf,
       nf.nf_data                            AS data_nf
FROM movimentos m
JOIN APPS.OE_ORDER_LINES_ALL l   ON l.line_id = m.line_id
JOIN APPS.OE_ORDER_HEADERS_ALL h ON h.header_id = l.header_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msi
       ON msi.inventory_item_id = l.inventory_item_id
      AND msi.organization_id  = l.ship_from_org_id
LEFT JOIN APPS.OE_TRANSACTION_TYPES_TL tt
       ON tt.transaction_type_id = h.order_type_id
      AND tt.language = USERENV('LANG')
LEFT JOIN APPS.OE_LOOKUPS fs
       ON fs.lookup_type = 'FLOW_STATUS'
      AND fs.lookup_code = h.flow_status_code
LEFT JOIN nf ON nf.line_id = l.line_id
ORDER BY m.asset_number, h.ordered_date DESC, h.order_number, l.line_number
