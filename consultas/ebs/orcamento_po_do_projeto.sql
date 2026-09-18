-- As POs de um projeto, uma linha por PO, com a NF atrelada (se houver).
--
-- É a lista que o Controle de Orçamento pede: "todo ID de projeto está
-- atrelado a uma PO" — então, dado o projeto, quais POs existem, quanto
-- valem, quantos itens têm, e se já viraram nota fiscal.
--
-- STATUS DE EXECUÇÃO
--   Executada    → existe pelo menos uma NF lançada contra alguma
--                  distribuição desta PO no projeto.
--   Em andamento → a PO existe e nenhuma NF chegou nela ainda.
-- A conta é essa e só essa. Não se olha status de aprovação nem
-- recebimento: o pedido foi "se tem NF atrelada".
--
-- A CHAVE DA NF (44 dígitos)
-- Na localização brasileira do EBS a chave de acesso da NF-e não tem coluna
-- própria: ela mora num dos GLOBAL_ATTRIBUTE da AP_INVOICES_ALL, e QUAL
-- deles varia de instalação para instalação. Em vez de chutar um número e
-- devolver lixo de outra coluna calado, varremos os vinte e aceitamos
-- apenas o que TEM CARA de chave: 44 dígitos, nada mais. Se nesta
-- instalação a chave estiver em outro lugar (tabela da localização, por
-- exemplo), a coluna vem vazia — e vazio aqui quer dizer "não encontrei",
-- não "não existe". Rode consultas/ebs/nf_onde_esta_a_chave.sql para achar
-- onde ela está de verdade e fixar aqui.
--
-- Uma PO pode ter várias NFs (entregas parciais). Os números e as chaves
-- vêm concatenados na mesma célula, e nf_qtd diz quantas são.
WITH proj AS (
    SELECT project_id, segment1 AS projeto_numero, name AS projeto_nome
    FROM APPS.PA_PROJECTS_ALL
    WHERE segment1 = :p_project_number
),
-- É a DISTRIBUIÇÃO que amarra a PO ao projeto: uma mesma PO pode ratear
-- entre projetos, e só as distribuições deste projeto interessam.
dist AS (
    SELECT DISTINCT pd.po_header_id, pd.po_distribution_id,
           pd.line_location_id, pd.project_id
    FROM APPS.PO_DISTRIBUTIONS_ALL pd
    JOIN proj p ON p.project_id = pd.project_id
),
nf AS (
    SELECT DISTINCT
           d.po_header_id,
           ai.invoice_id,
           ai.invoice_num                                AS nf_numero,
           ai.invoice_date                               AS nf_data,
           NVL(ai.invoice_amount, 0)                     AS nf_valor,
           COALESCE(
               CASE WHEN REGEXP_LIKE(ai.global_attribute1,  '^[0-9]{44}$') THEN ai.global_attribute1  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute2,  '^[0-9]{44}$') THEN ai.global_attribute2  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute3,  '^[0-9]{44}$') THEN ai.global_attribute3  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute4,  '^[0-9]{44}$') THEN ai.global_attribute4  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute5,  '^[0-9]{44}$') THEN ai.global_attribute5  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute6,  '^[0-9]{44}$') THEN ai.global_attribute6  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute7,  '^[0-9]{44}$') THEN ai.global_attribute7  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute8,  '^[0-9]{44}$') THEN ai.global_attribute8  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute9,  '^[0-9]{44}$') THEN ai.global_attribute9  END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute10, '^[0-9]{44}$') THEN ai.global_attribute10 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute11, '^[0-9]{44}$') THEN ai.global_attribute11 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute12, '^[0-9]{44}$') THEN ai.global_attribute12 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute13, '^[0-9]{44}$') THEN ai.global_attribute13 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute14, '^[0-9]{44}$') THEN ai.global_attribute14 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute15, '^[0-9]{44}$') THEN ai.global_attribute15 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute16, '^[0-9]{44}$') THEN ai.global_attribute16 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute17, '^[0-9]{44}$') THEN ai.global_attribute17 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute18, '^[0-9]{44}$') THEN ai.global_attribute18 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute19, '^[0-9]{44}$') THEN ai.global_attribute19 END,
               CASE WHEN REGEXP_LIKE(ai.global_attribute20, '^[0-9]{44}$') THEN ai.global_attribute20 END
           )                                             AS nf_chave
    FROM dist d
    JOIN APPS.AP_INVOICE_DISTRIBUTIONS_ALL aid
         ON aid.po_distribution_id = d.po_distribution_id
    JOIN APPS.AP_INVOICES_ALL ai ON ai.invoice_id = aid.invoice_id
),
-- Valor e itens contam SÓ as linhas que este projeto paga. Somar a PO
-- inteira num rateio faria o projeto parecer ter gasto o dobro.
val AS (
    SELECT d.po_header_id,
           d.project_id,
           COUNT(DISTINCT pl.po_line_id)               AS qtd_itens,
           SUM(NVL(pll.quantity, 0))                   AS qtd_total,
           SUM(NVL(pll.quantity_received, 0))          AS qtd_recebida,
           SUM(NVL(pll.amount,
                   NVL(pll.quantity, 0)
                   * NVL(pll.price_override, NVL(pl.unit_price, 0)))) AS valor_total
    FROM dist d
    JOIN APPS.PO_LINE_LOCATIONS_ALL pll ON pll.line_location_id = d.line_location_id
    JOIN APPS.PO_LINES_ALL          pl  ON pl.po_line_id = pll.po_line_id
    WHERE NVL(pl.cancel_flag, 'N')  = 'N'
      AND NVL(pll.cancel_flag, 'N') = 'N'
    GROUP BY d.po_header_id, d.project_id
)
SELECT p.projeto_numero,
       p.projeto_nome,
       ph.segment1                                     AS po_numero,
       s.vendor_name                                   AS fornecedor,
       ph.authorization_status                         AS status_po,
       ph.currency_code                                AS moeda,
       ph.creation_date                                AS po_criada_em,
       ph.approved_date                                AS po_aprovada_em,
       v.qtd_itens,
       v.qtd_total,
       v.qtd_recebida,
       v.valor_total,
       (SELECT COUNT(DISTINCT n.invoice_id) FROM nf n
         WHERE n.po_header_id = ph.po_header_id)       AS nf_qtd,
       (SELECT LISTAGG(n.nf_numero, ', ') WITHIN GROUP (ORDER BY n.nf_numero)
          FROM nf n WHERE n.po_header_id = ph.po_header_id) AS nf_numero,
       (SELECT LISTAGG(n.nf_chave, ', ') WITHIN GROUP (ORDER BY n.nf_chave)
          FROM nf n WHERE n.po_header_id = ph.po_header_id
            AND n.nf_chave IS NOT NULL)                AS nf_chave,
       (SELECT MIN(n.nf_data) FROM nf n
         WHERE n.po_header_id = ph.po_header_id)       AS nf_data,
       (SELECT SUM(n.nf_valor) FROM nf n
         WHERE n.po_header_id = ph.po_header_id)       AS nf_valor,
       CASE WHEN EXISTS (SELECT 1 FROM nf n WHERE n.po_header_id = ph.po_header_id)
            THEN 'Executada' ELSE 'Em andamento' END   AS status_execucao
FROM val v
JOIN APPS.PO_HEADERS_ALL ph ON ph.po_header_id = v.po_header_id
JOIN proj p ON p.project_id = v.project_id
LEFT JOIN APPS.AP_SUPPLIERS s ON s.vendor_id = ph.vendor_id
ORDER BY ph.segment1
