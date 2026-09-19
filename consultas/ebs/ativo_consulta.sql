SELECT fa.asset_id                                   AS asset_id,
       fa.asset_number                               AS imobilizado,
       fa.tag_number                                 AS etiqueta,
       fa.serial_number                              AS numero_serie,
       fa.description                                AS descricao,
       fa.manufacturer_name                          AS fabricante,
       fa.model_number                               AS modelo_ebs,
       fb.book_type_code                             AS livro,
       fb.cost                                       AS custo,
       fb.date_placed_in_service                     AS dpis,
       CASE WHEN fb.date_retired IS NOT NULL
              OR fb.period_counter_fully_retired IS NOT NULL THEN 'S' ELSE 'N' END AS baixado,
       fb.date_retired                               AS data_baixa,
       (SELECT REGEXP_REPLACE(TRIM('.' FROM loc.segment1 || '.' || loc.segment2 || '.' || loc.segment3 || '.' ||
                                          loc.segment4 || '.' || loc.segment5 || '.' || loc.segment6 || '.' || loc.segment7),
                              '\.{2,}', '.')
          FROM APPS.FA_DISTRIBUTION_HISTORY dh
          JOIN APPS.FA_LOCATIONS loc ON loc.location_id = dh.location_id
         WHERE dh.asset_id = fa.asset_id
           AND dh.date_ineffective IS NULL
           AND ROWNUM = 1)                           AS local_atribuido,
       (SELECT MAX(ai.po_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS po,
       (SELECT MAX(ai.invoice_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS nf
FROM APPS.FA_ADDITIONS_B fa
LEFT JOIN APPS.FA_BOOKS fb
       ON fb.asset_id = fa.asset_id
      AND fb.date_ineffective IS NULL
      AND fb.book_type_code IN (SELECT bc.book_type_code
                                  FROM APPS.FA_BOOK_CONTROLS bc
                                 WHERE bc.book_class = 'CORPORATE')
WHERE fa.asset_number  IN (/*TERMOS*/)
   OR fa.tag_number    IN (/*TERMOS*/)
   OR fa.serial_number IN (/*TERMOS*/)
