-- Por que `bem_x_ordem.sql` veio vazia (ou veio demais).
--
-- Não é consulta de tela. É para rodar com os MESMOS BEM_1..BEM_5 e olhar
-- onde o número cai para zero: aquele é o degrau que arrebentou.
--
-- Existe porque "veio vazio" é a resposta menos útil que um SELECT pode dar.
-- Com sete contagens em sequência, a diferença entre "o bem não existe",
-- "o bem não tem série", "a série nunca se moveu por ordem de venda" e "moveu,
-- mas a NF não chegou" aparece na hora — e cada uma dessas pede uma ação
-- diferente. Sem isso, o caminho é chutar em cima de uma tela em branco.

SELECT 1 AS passo,
       'bens encontrados pelo número informado' AS degrau,
       COUNT(*)                                 AS quantos
FROM APPS.FA_ADDITIONS_B fa
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))

UNION ALL
SELECT 2, '...destes, com número de série preenchido', COUNT(*)
FROM APPS.FA_ADDITIONS_B fa
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))
  AND fa.serial_number IS NOT NULL

UNION ALL
-- Zero aqui com o passo 2 cheio quer dizer que a série do ativo fixo não é a
-- mesma do estoque. Acontece quando o bem foi cadastrado à mão: a ligação
-- existe no mundo real e não existe no banco.
SELECT 3, 'movimentos de estoque com essa série', COUNT(*)
FROM APPS.FA_ADDITIONS_B fa
JOIN APPS.MTL_UNIT_TRANSACTIONS mut
  ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(fa.serial_number))
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))

UNION ALL
-- Zero aqui com o passo 3 cheio: a série se moveu, mas nunca por ORDEM DE
-- VENDA (só transferência, ajuste, inventário). Aí a ligação bem→ordem desta
-- instalação não passa pelo estoque, e a consulta precisa de outro caminho.
SELECT 4, '...destes, originados de ordem de venda', COUNT(*)
FROM APPS.FA_ADDITIONS_B fa
JOIN APPS.MTL_UNIT_TRANSACTIONS mut
  ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(fa.serial_number))
JOIN APPS.MTL_MATERIAL_TRANSACTIONS mmt
  ON mmt.transaction_id = mut.transaction_id
 AND mmt.transaction_source_type_id = 2
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))
 AND mmt.trx_source_line_id IS NOT NULL

UNION ALL
-- Zero aqui com o passo 4 cheio é o caso mais escondido: o movimento aponta
-- para uma linha que não está mais na OE_ORDER_LINES_ALL (ordem expurgada).
SELECT 5, 'linhas de ordem alcançadas', COUNT(DISTINCT l.line_id)
FROM APPS.FA_ADDITIONS_B fa
JOIN APPS.MTL_UNIT_TRANSACTIONS mut
  ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(fa.serial_number))
JOIN APPS.MTL_MATERIAL_TRANSACTIONS mmt
  ON mmt.transaction_id = mut.transaction_id
 AND mmt.transaction_source_type_id = 2
JOIN APPS.OE_ORDER_LINES_ALL l ON l.line_id = mmt.trx_source_line_id
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))

UNION ALL
SELECT 6, 'ordens distintas (é o que a tela mostra)', COUNT(DISTINCT h.order_number)
FROM APPS.FA_ADDITIONS_B fa
JOIN APPS.MTL_UNIT_TRANSACTIONS mut
  ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(fa.serial_number))
JOIN APPS.MTL_MATERIAL_TRANSACTIONS mmt
  ON mmt.transaction_id = mut.transaction_id
 AND mmt.transaction_source_type_id = 2
JOIN APPS.OE_ORDER_LINES_ALL l   ON l.line_id = mmt.trx_source_line_id
JOIN APPS.OE_ORDER_HEADERS_ALL h ON h.header_id = l.header_id
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))

UNION ALL
-- Zero aqui NÃO é defeito: quer dizer que a ordem ainda não virou nota. Só
-- vira problema se as ordens do passo 6 já estiverem faturadas — e aí o que
-- está errado é a coluna da NF, não a ligação.
SELECT 7, '...destas, com NF pelo AutoInvoice', COUNT(DISTINCT ct.trx_number)
FROM APPS.FA_ADDITIONS_B fa
JOIN APPS.MTL_UNIT_TRANSACTIONS mut
  ON UPPER(TRIM(mut.serial_number)) = UPPER(TRIM(fa.serial_number))
JOIN APPS.MTL_MATERIAL_TRANSACTIONS mmt
  ON mmt.transaction_id = mut.transaction_id
 AND mmt.transaction_source_type_id = 2
JOIN APPS.OE_ORDER_LINES_ALL l ON l.line_id = mmt.trx_source_line_id
JOIN APPS.RA_CUSTOMER_TRX_LINES_ALL ctl
  ON ctl.interface_line_context = 'ORDER ENTRY'
 AND REGEXP_LIKE(ctl.interface_line_attribute6, '^[0-9]+$')
 AND TO_NUMBER(ctl.interface_line_attribute6) = l.line_id
JOIN APPS.RA_CUSTOMER_TRX_ALL ct ON ct.customer_trx_id = ctl.customer_trx_id
WHERE UPPER(TRIM(fa.asset_number)) IN (UPPER(TRIM(:BEM_1)), UPPER(TRIM(:BEM_2)),
                                       UPPER(TRIM(:BEM_3)), UPPER(TRIM(:BEM_4)),
                                       UPPER(TRIM(:BEM_5)))

ORDER BY 1
