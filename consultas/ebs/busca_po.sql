SELECT h.segment1 AS po_numero, ppa.segment1 AS projeto_numero, ppa.name AS projeto_nome,
    s.vendor_name AS fornecedor, h.authorization_status AS status_po, l.line_num AS linha,
    NVL(l.item_description, msib.description) AS descricao_item,
    NVL(l.unit_meas_lookup_code, msib.primary_uom_code) AS uom,
    l.unit_price AS preco_unitario, l.closed_code AS status_linha,
    ll.shipment_num AS entrega, ll.quantity AS quantidade_pedida,
    ll.promised_date AS data_prometida, ll.need_by_date AS data_necessidade,
    ll.closed_code AS status_entrega, pd.distribution_num AS distribuicao,
    pat.task_number AS tarefa_numero, pat.task_name AS tarefa_nome,
    pd.expenditure_type AS tipo_despesa, pd.destination_type_code AS destino,
    COALESCE(pah_ll.note, pah_hdr.note) AS motivo_rejeicao,
    COALESCE(pah_ll.action_date, pah_hdr.action_date) AS data_rejeicao,
    COALESCE(fu_ll.user_name, fu_hdr.user_name) AS rejeitado_por
FROM APPS.PO_HEADERS_ALL h
JOIN APPS.PO_LINES_ALL l ON l.po_header_id = h.po_header_id
JOIN APPS.PO_LINE_LOCATIONS_ALL ll ON ll.po_line_id = l.po_line_id
JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = h.vendor_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = l.item_id AND msib.organization_id = ll.ship_to_organization_id
JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.line_location_id = ll.line_location_id
LEFT JOIN APPS.PA_PROJECTS_ALL ppa ON ppa.project_id = pd.project_id
LEFT JOIN APPS.PA_TASKS pat ON pat.task_id = pd.task_id
LEFT JOIN (SELECT pah1.* FROM APPS.PO_ACTION_HISTORY pah1 WHERE pah1.object_type_code='PO' AND pah1.action_code='REJECT'
  AND pah1.sequence_num=(SELECT MAX(pah2.sequence_num) FROM APPS.PO_ACTION_HISTORY pah2
  WHERE pah2.object_type_code=pah1.object_type_code AND pah2.object_id=pah1.object_id AND pah2.action_code='REJECT')
) pah_hdr ON pah_hdr.object_id = h.po_header_id
LEFT JOIN APPS.FND_USER fu_hdr ON fu_hdr.user_id = pah_hdr.last_updated_by
LEFT JOIN (SELECT pah1.* FROM APPS.PO_ACTION_HISTORY pah1 WHERE pah1.object_type_code='PO_LINE_LOCATION' AND pah1.action_code='REJECT'
  AND pah1.sequence_num=(SELECT MAX(pah2.sequence_num) FROM APPS.PO_ACTION_HISTORY pah2
  WHERE pah2.object_type_code=pah1.object_type_code AND pah2.object_id=pah1.object_id AND pah2.action_code='REJECT')
) pah_ll ON pah_ll.object_id = ll.line_location_id
LEFT JOIN APPS.FND_USER fu_ll ON fu_ll.user_id = pah_ll.last_updated_by
WHERE h.segment1 = :numero_po AND (:p_line_num IS NULL OR l.line_num = :p_line_num)
ORDER BY l.line_num, ll.shipment_num, pd.distribution_num
