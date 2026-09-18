SELECT * FROM (
    SELECT LTRIM(msib.segment1, '0') AS item_ebs, msib.description AS descricao,
        CASE msib.item_type WHEN 'ATIVO FIXO' THEN 'HARDWARE' WHEN 'SERVICO' THEN 'SERVICOS'
            WHEN 'SERVICO ATIVO FIXO' THEN 'SERVICOS' WHEN 'USO CONSUMO' THEN 'HARDWARE' ELSE 'OUTROS' END AS tipo_item,
        s.vendor_name AS fornecedor, pl.unit_price AS valor_unitario, pd.expenditure_type,
        pat.task_number AS tarefa, ph.creation_date AS po_date,
        ROW_NUMBER() OVER (PARTITION BY msib.inventory_item_id ORDER BY ph.creation_date DESC) AS rn
    FROM APPS.PO_HEADERS_ALL ph
    JOIN APPS.PO_LINES_ALL pl ON pl.po_header_id = ph.po_header_id
    JOIN APPS.PO_DISTRIBUTIONS_ALL pd ON pd.po_line_id = pl.po_line_id
    JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = ph.vendor_id
    LEFT JOIN APPS.PA_TASKS pat ON pat.task_id = pd.task_id
    LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = pl.item_id AND msib.organization_id = 101
    WHERE ph.authorization_status IN ('APPROVED','CLOSED')
      AND pd.expenditure_type IN ('Computadores e Perifericos','Sistemas de Informatica')
      AND msib.segment1 IS NOT NULL AND ph.creation_date >= ADD_MONTHS(SYSDATE, -36)
) WHERE rn = 1 ORDER BY descricao
