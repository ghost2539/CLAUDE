SELECT hou.name AS operating_unit, NVL(msib.description, pl.item_description) AS item_description,
    pl.unit_meas_lookup_code AS uom, pl.list_price_per_unit AS unit_price
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
LEFT JOIN APPS.HR_OPERATING_UNITS hou ON hou.organization_id = pha.org_id
JOIN APPS.PO_LINES_ALL pl ON pl.po_header_id = pha.po_header_id
LEFT JOIN APPS.MTL_SYSTEM_ITEMS_B msib ON msib.inventory_item_id = pl.item_id AND msib.organization_id = 0
WHERE pha.type_lookup_code IN ('BLANKET','CONTRACT') AND pha.authorization_status = 'APPROVED'
  AND UPPER(pv.vendor_name) = UPPER(:p_vendor_name)
  AND TRUNC(SYSDATE) >= TRUNC(NVL(pha.start_date, SYSDATE))
  AND (pha.end_date IS NULL OR TRUNC(SYSDATE) <= TRUNC(pha.end_date))
ORDER BY hou.name, pha.segment1, pl.line_num
