SELECT DISTINCT pv.vendor_name
FROM APPS.PO_HEADERS_ALL pha
JOIN APPS.PO_VENDORS pv ON pv.vendor_id = pha.vendor_id
WHERE pha.type_lookup_code IN ('BLANKET','CONTRACT') AND pha.authorization_status = 'APPROVED'
  AND TRUNC(SYSDATE) >= TRUNC(NVL(pha.start_date, SYSDATE))
  AND (pha.end_date IS NULL OR TRUNC(SYSDATE) <= TRUNC(pha.end_date))
ORDER BY pv.vendor_name
