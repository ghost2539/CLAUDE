SELECT ph.authorization_status AS status, NVL(pll.amount_billed, 0) AS amount_billed_ship,
    CASE WHEN NVL(pll.amount_billed, 0)=0 THEN 'Comprometida' ELSE 'Realizado' END AS status_faturado,
    ph.segment1 || ' / ' || pll.shipment_num AS numero_po, ph.creation_date AS data_criacao,
    ph.approved_date AS data_aprovacao, pl.line_num AS po_line_num, pll.shipment_num,
    pl.item_description AS desc_po, pll.need_by_date AS necessario_em,
    NVL(pll.quantity, 0) AS qty_pedida, NVL(pll.quantity_received, 0) AS qty_recebida,
    NVL(pll.quantity_billed, 0) AS qty_faturada, NVL(pll.quantity_cancelled, 0) AS qty_cancelada,
    NVL(pll.price_override, NVL(pl.unit_price, 0)) AS price_override,
    NVL(pll.amount, NVL(pll.quantity, 0) * NVL(pll.price_override, NVL(pl.unit_price, 0))) AS amount_ship
FROM APPS.PA_PROJECTS_ALL p
JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.project_id = p.project_id
JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.line_location_id = pd.line_location_id
JOIN APPS.PO_LINES_ALL pl ON pl.po_line_id = pd.po_line_id
JOIN APPS.PO_HEADERS_ALL ph ON ph.po_header_id = pl.po_header_id
WHERE p.segment1 = :p_project_number ORDER BY ph.segment1, pll.shipment_num
