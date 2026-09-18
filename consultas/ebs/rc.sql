SELECT p.segment1 AS project_number, prh.segment1 AS rc_numero, prh.authorization_status AS rc_status,
    prh.description AS rc_descricao, prh.creation_date AS rc_data_criacao, prl.line_num AS rc_line_num,
    prl.item_description AS rc_item_desc, prl.quantity AS rc_qty, prl.unit_price AS rc_unit_price
FROM apps.pa_projects_all p
JOIN apps.po_req_distributions_all prd ON prd.project_id = p.project_id
JOIN apps.po_requisition_lines_all prl ON prl.requisition_line_id = prd.requisition_line_id
JOIN apps.po_requisition_headers_all prh ON prh.requisition_header_id = prl.requisition_header_id
WHERE p.segment1 = :p_project_number ORDER BY rc_data_criacao DESC
