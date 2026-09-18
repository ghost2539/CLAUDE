SELECT pha.segment1 AS agreement_num, pv.vendor_name, pha.start_date, pha.end_date,
    ROUND(pha.end_date - SYSDATE) AS days_to_expire, pha.authorization_status,
    hou.name AS operating_unit
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
LEFT JOIN APPS.HR_OPERATING_UNITS hou ON hou.organization_id = pha.org_id
WHERE pha.type_lookup_code = 'BLANKET' AND pha.authorization_status = 'APPROVED'
  AND pha.end_date IS NOT NULL AND TRUNC(pha.end_date) BETWEEN TRUNC(SYSDATE) AND TRUNC(SYSDATE) + :p_days
ORDER BY days_to_expire ASC
