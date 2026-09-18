-- Descobre em QUAL coluna esta instalação guarda a chave de acesso da NF-e.
--
-- Não é consulta de tela: é para rodar uma vez, olhar o resultado e fixar a
-- coluna certa em orcamento_po_do_projeto.sql. Enquanto ninguém rodar, aquela
-- consulta varre os vinte GLOBAL_ATTRIBUTE e aceita só o que tem 44 dígitos —
-- funciona, mas a varredura custa e não cobre o caso de a chave estar numa
-- tabela da localização brasileira em vez de num atributo.
--
-- Conta, por coluna, quantas notas têm ali um valor de 44 dígitos. A coluna
-- com contagem alta é a resposta. Todas zeradas quer dizer que a chave não
-- está na AP_INVOICES_ALL nesta instalação — aí o caminho é a localização
-- (JL_BR_*), e vale perguntar ao time de Fiscal qual tabela usam.
--
-- :p_meses limita a janela para a consulta não varrer a tabela inteira.
SELECT 'GLOBAL_ATTRIBUTE1'  AS coluna, COUNT(*) AS notas_com_chave FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute1,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE2',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute2,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE3',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute3,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE4',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute4,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE5',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute5,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE6',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute6,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE7',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute7,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE8',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute8,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE9',  COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute9,  '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE10', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute10, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE11', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute11, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE12', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute12, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE13', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute13, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE14', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute14, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE15', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute15, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE16', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute16, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE17', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute17, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE18', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute18, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE19', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute19, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
UNION ALL SELECT 'GLOBAL_ATTRIBUTE20', COUNT(*) FROM APPS.AP_INVOICES_ALL WHERE REGEXP_LIKE(global_attribute20, '^[0-9]{44}$') AND invoice_date >= ADD_MONTHS(SYSDATE, -:p_meses)
ORDER BY notas_com_chave DESC
