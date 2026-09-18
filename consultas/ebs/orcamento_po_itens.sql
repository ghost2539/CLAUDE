-- Os itens de UMA PO dentro de UM projeto — o detalhe da lista que
-- orcamento_po_do_projeto.sql resume.
--
-- Por que não reusar `po_itens`: aquela é do Agendamento de Fornecedores e
-- filtra por número de PO e liberação, sem saber de projeto. Numa PO que
-- rateia entre projetos ela somaria os itens de todos, e o orçamento do
-- projeto apareceria maior do que é. Aqui o recorte é a DISTRIBUIÇÃO do
-- projeto, igual ao da consulta-resumo — para que a soma dos itens bata com
-- o valor_total que a linha da PO mostra.
WITH proj AS (
    SELECT project_id
    FROM APPS.PA_PROJECTS_ALL
    WHERE segment1 = :p_project_number
),
dist AS (
    SELECT DISTINCT pd.po_header_id, pd.line_location_id
    FROM APPS.PO_DISTRIBUTIONS_ALL pd
    JOIN proj p ON p.project_id = pd.project_id
)
SELECT ph.segment1                                     AS po_numero,
       pr.release_num                                  AS liberacao,
       pl.line_num                                     AS linha,
       LTRIM(msib.segment1, '0')                       AS item_ebs,
       NVL(pl.item_description, msib.description)      AS descricao,
       NVL(pl.unit_meas_lookup_code, msib.primary_uom_code) AS unidade,
       SUM(NVL(pll.quantity, 0))                       AS quantidade_pedida,
       SUM(NVL(pll.quantity_received, 0))              AS quantidade_recebida,
       SUM(NVL(pll.quantity_billed, 0))                AS quantidade_faturada,
       SUM(NVL(pll.quantity, 0) - NVL(pll.quantity_received, 0)) AS quantidade_pendente,
       MAX(NVL(pll.price_override, pl.unit_price))     AS preco_unitario,
       SUM(NVL(pll.amount,
               NVL(pll.quantity, 0)
               * NVL(pll.price_override, NVL(pl.unit_price, 0)))) AS valor_total
FROM dist d
JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.line_location_id = d.line_location_id
JOIN APPS.PO_LINES_ALL          pl  ON pl.po_line_id   = pll.po_line_id
JOIN APPS.PO_HEADERS_ALL        ph  ON ph.po_header_id = pl.po_header_id
LEFT JOIN APPS.PO_RELEASES_ALL  pr  ON pr.po_release_id = pll.po_release_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib
       ON msib.inventory_item_id = pl.item_id
      AND msib.organization_id   = pll.ship_to_organization_id
WHERE ph.segment1 = :numero_po
  AND NVL(pl.cancel_flag, 'N')  = 'N'
  AND NVL(pll.cancel_flag, 'N') = 'N'
GROUP BY ph.segment1, pr.release_num, pl.line_num, msib.segment1,
         pl.item_description, msib.description,
         pl.unit_meas_lookup_code, msib.primary_uom_code
ORDER BY pl.line_num
