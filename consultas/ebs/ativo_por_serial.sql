-- Onde o patrimônio aparece depois de capitalizado. É o que a tela
-- Internalização → Patrimônio consulta para saber se o equipamento já virou
-- ativo fixo no EBS: enquanto não aparece, ele fica esperando.
--
-- A busca é pelo SERIAL porque é o que o portal tem na mão — a plaqueta é
-- atribuída pelo próprio EBS e por isso não serve para procurar. UPPER nos
-- dois lados: serial digitado por pessoa vem em qualquer caixa, e o que
-- está gravado no EBS nem sempre segue a mesma.
SELECT fa.asset_number                               AS ativo,
       fa.serial_number                              AS numero_serie,
       fa.tag_number                                 AS plaqueta,
       fa.description                                AS descricao,
       fa.manufacturer_name                          AS fabricante,
       fa.model_number                               AS modelo,
       fc.segment1                                   AS categoria,
       fb.date_placed_in_service                     AS data_capitalizacao,
       fb.book_type_code                             AS empresa
FROM APPS.FA_ADDITIONS_B fa
LEFT JOIN APPS.FA_BOOKS fb
       ON fb.asset_id = fa.asset_id
      AND NVL(fb.date_ineffective, SYSDATE + 1) > SYSDATE
LEFT JOIN APPS.FA_CATEGORIES_B fc ON fc.category_id = fa.asset_category_id
WHERE UPPER(fa.serial_number) = UPPER(:numero_serie)
ORDER BY fb.date_placed_in_service DESC
