-- Ativo por numero de serie, etiqueta ou imobilizado (tela Consulta).
--
-- Este arquivo e um MOLDE: o portal o completa com o que a conta enxerga.
-- Cada marcador entre barra-asterisco vira a coluna real da base, ou NULL
-- quando ela nao existe; o marcador da busca vira um SELECT por coluna
-- indexada para cada identificador, unidos por UNION; e um bloco entre as
-- marcas de opcional sai inteiro quando a tabela ou coluna que ele exige nao
-- existe. Quem monta e integracoes/ebs_ativos.py, olhando o catalogo da base.
-- Sem acento de proposito: o texto vai inteiro para o banco.
WITH alvos AS (
/*BUSCA*/
)
SELECT fa.asset_id                                   AS asset_id,
       /*IMOBILIZADO*/                               AS imobilizado,
       /*ETIQUETA*/                                  AS etiqueta,
       /*SERIE*/                                     AS numero_serie,
       /*DESCRICAO*/                                 AS descricao,
       /*FABRICANTE*/                                AS fabricante,
       /*MODELO*/                                    AS modelo_ebs,
       fb.book_type_code                             AS livro,
       /*CUSTO*/                                     AS custo,
       /*DPIS*/                                      AS dpis,
       /*BAIXADO*/                                   AS baixado,
--<opcional FA_RETIREMENTS.DATE_RETIRED>
       (SELECT MAX(ret.date_retired)
          FROM APPS.FA_RETIREMENTS ret
         WHERE ret.asset_id = fa.asset_id
           AND ret.book_type_code = fb.book_type_code
           AND ret.status IN ('PROCESSED', 'PENDING')) AS data_baixa,
--</opcional>
--<opcional FA_DISTRIBUTION_HISTORY.LOCATION_ID+FA_LOCATIONS.SEGMENT1>
       (SELECT REGEXP_REPLACE(TRIM('.' FROM loc.segment1 || '.' || loc.segment2 || '.' || loc.segment3 || '.' ||
                                          loc.segment4 || '.' || loc.segment5 || '.' || loc.segment6 || '.' || loc.segment7),
                              '\.{2,}', '.')
          FROM APPS.FA_DISTRIBUTION_HISTORY dh
          JOIN APPS.FA_LOCATIONS loc ON loc.location_id = dh.location_id
         WHERE dh.asset_id = fa.asset_id
           AND dh.date_ineffective IS NULL
           AND ROWNUM = 1)                           AS local_atribuido,
--</opcional>
--<opcional FA_ASSET_INVOICES.PO_NUMBER>
       (SELECT MAX(ai.po_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS po,
--</opcional>
--<opcional FA_ASSET_INVOICES.INVOICE_NUMBER>
       (SELECT MAX(ai.invoice_number)
          FROM APPS.FA_ASSET_INVOICES ai
         WHERE ai.asset_id = fa.asset_id
           AND ai.date_ineffective IS NULL)          AS nf
--</opcional>
FROM APPS.FA_ADDITIONS_B fa
JOIN alvos alvo
  ON alvo.asset_id = fa.asset_id
--<opcional USA_TL>
-- Em R12 a descricao do ativo fica na tabela traduzida, nao na base.
LEFT JOIN APPS.FA_ADDITIONS_TL tl
       ON tl.asset_id = fa.asset_id
      AND tl.language = USERENV('LANG')
--</opcional>
LEFT JOIN APPS.FA_BOOKS fb
       ON fb.asset_id = fa.asset_id
      AND fb.date_ineffective IS NULL
--<opcional FA_BOOK_CONTROLS.BOOK_CLASS>
      AND fb.book_type_code IN (SELECT bc.book_type_code
                                  FROM APPS.FA_BOOK_CONTROLS bc
                                 WHERE bc.book_class = 'CORPORATE')
--</opcional>
