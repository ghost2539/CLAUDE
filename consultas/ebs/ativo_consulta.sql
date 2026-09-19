-- Ativo por número de série, etiqueta ou imobilizado (tela Consulta).
-- Os termos entram como binds (:t0, :t1...) no marcador de termos do arquivo.
--
-- Os blocos marcados com --<opcional OBJETO> saem da consulta quando a conta
-- não enxerga aquela tabela ou coluna: instalação que não concede FA_RETIREMENTS
-- ou FA_ASSET_INVOICES continua consultando, só sem aquela coluna. Quem monta
-- isso é integracoes/ebs_ativos.py, olhando o catálogo da própria base.
SELECT fa.asset_id                                   AS asset_id,
       fa.asset_number                               AS imobilizado,
       fa.tag_number                                 AS etiqueta,
       fa.serial_number                              AS numero_serie,
       fa.description                                AS descricao,
--<opcional FA_ADDITIONS_B.MANUFACTURER_NAME>
       fa.manufacturer_name                          AS fabricante,
--</opcional>
--<opcional FA_ADDITIONS_B.MODEL_NUMBER>
       fa.model_number                               AS modelo_ebs,
--</opcional>
       fb.book_type_code                             AS livro,
--<opcional FA_BOOKS.COST>
       fb.cost                                       AS custo,
--</opcional>
--<opcional FA_BOOKS.DATE_PLACED_IN_SERVICE>
       fb.date_placed_in_service                     AS dpis,
--</opcional>
--<opcional FA_BOOKS.PERIOD_COUNTER_FULLY_RETIRED>
       -- Baixa TOTAL do ativo. FA_BOOKS não guarda data de baixa: o período em
       -- que o ativo foi totalmente retirado é o que marca que ele saiu.
       CASE WHEN fb.period_counter_fully_retired IS NOT NULL
            THEN 'S' ELSE 'N' END                    AS baixado,
--</opcional>
--<opcional FA_RETIREMENTS>
       -- A data da baixa mora na retirada, não no livro.
       (SELECT MAX(ret.date_retired)
          FROM APPS.FA_RETIREMENTS ret
         WHERE ret.asset_id = fa.asset_id
           AND ret.book_type_code = fb.book_type_code
           AND ret.status IN ('PROCESSED', 'PENDING')) AS data_baixa,
--</opcional>
--<opcional FA_DISTRIBUTION_HISTORY+FA_LOCATIONS>
       (SELECT REGEXP_REPLACE(TRIM('.' FROM loc.segment1 || '.' || loc.segment2 || '.' || loc.segment3 || '.' ||
                                          loc.segment4 || '.' || loc.segment5 || '.' || loc.segment6 || '.' || loc.segment7),
                              '\.{2,}', '.')
          FROM APPS.FA_DISTRIBUTION_HISTORY dh
          JOIN APPS.FA_LOCATIONS loc ON loc.location_id = dh.location_id
         WHERE dh.asset_id = fa.asset_id
           AND dh.date_ineffective IS NULL
           AND ROWNUM = 1)                           AS local_atribuido,
--</opcional>
--<opcional FA_ASSET_INVOICES>
       (SELECT MAX(ai.po_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS po,
       (SELECT MAX(ai.invoice_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS nf
--</opcional>
FROM APPS.FA_ADDITIONS_B fa
LEFT JOIN APPS.FA_BOOKS fb
       ON fb.asset_id = fa.asset_id
      AND fb.date_ineffective IS NULL
--<opcional FA_BOOK_CONTROLS>
      AND fb.book_type_code IN (SELECT bc.book_type_code
                                  FROM APPS.FA_BOOK_CONTROLS bc
                                 WHERE bc.book_class = 'CORPORATE')
--</opcional>
WHERE fa.asset_number  IN (/*TERMOS*/)
   OR fa.tag_number    IN (/*TERMOS*/)
   OR fa.serial_number IN (/*TERMOS*/)
