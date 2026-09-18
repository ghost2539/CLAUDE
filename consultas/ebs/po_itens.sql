-- Os itens de uma PO, para o Agendamento de Fornecedores.
--
-- A `busca_po` veio do módulo do outro time e devolve uma linha por
-- DISTRIBUIÇÃO: o mesmo item repete quando há várias entregas ou vários
-- projetos rateando. Para a tela de Agendamento isso é ruído — ela precisa
-- de "10 desktops, 20 leitores", uma linha por item.
--
-- Então aqui agrega por linha do pedido e devolve o que a tela usa:
-- descrição, unidade, quantidade pedida, já recebida e o que falta. Linha
-- cancelada fica de fora; quantidade_pendente é o que se espera receber de
-- verdade.
SELECT ph.segment1                                   AS po_numero,
       pr.release_num                                AS liberacao,
       s.vendor_name                                 AS fornecedor,
       ph.authorization_status                       AS status_po,
       ph.currency_code                              AS moeda,
       pl.line_num                                   AS linha,
       LTRIM(msib.segment1, '0')                     AS item_ebs,
       NVL(pl.item_description, msib.description)    AS descricao,
       NVL(pl.unit_meas_lookup_code, msib.primary_uom_code) AS unidade,
       SUM(NVL(pll.quantity, 0))                     AS quantidade_pedida,
       SUM(NVL(pll.quantity_received, 0))            AS quantidade_recebida,
       SUM(NVL(pll.quantity, 0) - NVL(pll.quantity_received, 0)) AS quantidade_pendente,
       MAX(NVL(pll.price_override, pl.unit_price))   AS preco_unitario
FROM APPS.PO_HEADERS_ALL ph
JOIN APPS.PO_LINES_ALL          pl  ON pl.po_header_id = ph.po_header_id
JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.po_line_id  = pl.po_line_id
LEFT JOIN APPS.PO_RELEASES_ALL  pr  ON pr.po_release_id = pll.po_release_id
LEFT JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = ph.vendor_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib
       ON msib.inventory_item_id = pl.item_id
      AND msib.organization_id   = pll.ship_to_organization_id
WHERE ph.segment1 = :numero_po
  -- Acordo de compras tem UM número de PO e várias liberações: o que muda de
  -- um pedido para outro é o número depois do hífen (2570313-25 → liberação
  -- 25). Sem este filtro a consulta somaria as quantidades de TODAS as
  -- liberações do acordo, e o agendamento nasceria pedindo o total do ano.
  -- `:liberacao` nulo traz a PO inteira, que é o caso da compra avulsa.
  AND (:liberacao IS NULL OR pr.release_num = :liberacao)
  AND NVL(pl.cancel_flag, 'N') = 'N'
  AND NVL(pll.cancel_flag, 'N') = 'N'
GROUP BY ph.segment1, pr.release_num, s.vendor_name, ph.authorization_status,
         ph.currency_code, pl.line_num, msib.segment1, pl.item_description,
         msib.description, pl.unit_meas_lookup_code, msib.primary_uom_code
ORDER BY pl.line_num
