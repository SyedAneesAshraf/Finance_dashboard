/* ============================================================================
   PHASE 6 — GOLD VIEWS FOR THE BI LAYER

   Power BI consumes tables, not queries. Two of the Phase 5 analyses cannot be
   expressed as a visual over the star schema:

     - Duplicate detection is a SELF-JOIN. There is no DAX or visual construct
       that pairs a table against itself on a tolerance window.
     - Three-way match requires aggregating invoices per PO BEFORE comparing to
       the PO value. A visual would compare row by row and flag every legitimate
       staged delivery.

   Both are therefore productionised as views. They mirror queries 5.4 and 5.5
   exactly -- if either query changes, the matching view must change with it.

   Views rather than tables: they evaluate at refresh time, so they never go
   stale, and they cost nothing to store.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ---------------------------------------------------------------------------
   VW_DUPLICATE_INVOICE_PAIRS  --  mirrors query 5.4
   One row per suspected duplicate PAIR. Feeds the Page 2 count card.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW GOLD.VW_DUPLICATE_INVOICE_PAIRS AS
SELECT
    a.INVOICE_ID                                          AS INVOICE_1,
    b.INVOICE_ID                                          AS INVOICE_2,
    a.VENDOR_ID,
    a.VENDOR_KEY,
    v.VENDOR_NAME,
    v.CATEGORY,
    v.REGION,
    a.AMOUNT,
    a.CURRENCY,
    a.AMOUNT_USD                                          AS EXPOSURE_USD,
    a.INVOICE_DATE                                        AS INVOICE_1_DATE,
    b.INVOICE_DATE                                        AS INVOICE_2_DATE,
    a.INVOICE_DATE_KEY,
    ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE))  AS DAYS_APART,
    a.STATUS                                              AS INVOICE_1_STATUS,
    b.STATUS                                              AS INVOICE_2_STATUS,
    CASE
        WHEN a.STATUS = 'Paid' AND b.STATUS = 'Paid' THEN 'Both paid - recover'
        WHEN a.STATUS = 'Paid' OR  b.STATUS = 'Paid' THEN 'One paid - hold second'
        ELSE 'Neither paid - block'
    END                                                   AS RECOMMENDED_ACTION
FROM GOLD.FACT_INVOICE a
JOIN GOLD.FACT_INVOICE b
  ON a.VENDOR_ID  = b.VENDOR_ID
 AND a.AMOUNT     = b.AMOUNT
 AND a.INVOICE_ID < b.INVOICE_ID
 AND ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE)) <= 3
JOIN GOLD.DIM_VENDOR v
  ON v.VENDOR_KEY = a.VENDOR_KEY;

COMMENT ON VIEW GOLD.VW_DUPLICATE_INVOICE_PAIRS IS
'Suspected duplicate invoice pairs (same vendor, same amount, <=3 days apart). Mirrors query 5.4.';


/* ---------------------------------------------------------------------------
   VW_THREE_WAY_MATCH  --  mirrors query 5.5
   One row per invoiced PO with its cumulative variance. Feeds the Page 2 matrix.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE VIEW GOLD.VW_THREE_WAY_MATCH AS
WITH invoiced_per_po AS (
    SELECT
        PO_ID,
        COUNT(*)          AS INVOICE_COUNT,
        SUM(AMOUNT)       AS TOTAL_INVOICED,
        SUM(AMOUNT_USD)   AS TOTAL_INVOICED_USD,
        LISTAGG(INVOICE_ID, ', ') WITHIN GROUP (ORDER BY INVOICE_ID) AS INVOICE_IDS
    FROM     GOLD.FACT_INVOICE
    WHERE    PO_ID IS NOT NULL
    GROUP BY PO_ID
)
SELECT
    po.PO_ID,
    po.VENDOR_KEY,
    po.VENDOR_ID,
    v.VENDOR_NAME,
    v.CATEGORY,
    v.REGION,
    po.PO_DATE,
    po.PO_DATE_KEY,
    po.PO_STATUS,
    po.PO_AMOUNT,
    po.PO_AMOUNT_USD,
    po.CURRENCY,
    po.GOODS_RECEIPT_DATE,
    i.INVOICE_COUNT,
    i.TOTAL_INVOICED,
    i.TOTAL_INVOICED_USD,
    i.INVOICE_IDS,
    (i.TOTAL_INVOICED - po.PO_AMOUNT)                                  AS VARIANCE_AMOUNT,
    ROUND(100.0 * (i.TOTAL_INVOICED - po.PO_AMOUNT)
          / NULLIF(po.PO_AMOUNT, 0), 2)                                AS PCT_VARIANCE,
    CASE WHEN po.GOODS_RECEIPT_DATE IS NULL
         THEN 'No goods receipt' ELSE 'Received' END                   AS RECEIPT_STATUS,
    CASE
        WHEN po.PO_AMOUNT IS NULL OR po.PO_AMOUNT = 0 THEN 'Cannot assess'
        WHEN ABS(i.TOTAL_INVOICED - po.PO_AMOUNT) / NULLIF(po.PO_AMOUNT, 0) > 0.05
            THEN 'Exception - Review'
        ELSE 'Matched'
    END                                                                AS MATCH_STATUS,
    CASE
        WHEN po.PO_AMOUNT IS NULL OR po.PO_AMOUNT = 0 THEN 'Unassessable'
        WHEN 100.0 * (i.TOTAL_INVOICED - po.PO_AMOUNT) / NULLIF(po.PO_AMOUNT, 0) >  5
            THEN 'Over-billed'
        WHEN 100.0 * (i.TOTAL_INVOICED - po.PO_AMOUNT) / NULLIF(po.PO_AMOUNT, 0) < -5
            THEN 'Under-billed'
        ELSE 'Within tolerance'
    END                                                                AS EXCEPTION_TYPE
FROM invoiced_per_po i
JOIN GOLD.FACT_PURCHASE_ORDER po ON po.PO_ID = i.PO_ID
JOIN GOLD.DIM_VENDOR v           ON v.VENDOR_KEY = po.VENDOR_KEY;

COMMENT ON VIEW GOLD.VW_THREE_WAY_MATCH IS
'Cumulative invoiced-vs-PO variance per purchase order, 5% tolerance. Mirrors query 5.5.';


/* ---------------------------------------------------------------------------
   Verify: 14 duplicate pairs, 420 POs assessed of which 36 are exceptions.
   --------------------------------------------------------------------------- */
SELECT 'VW_DUPLICATE_INVOICE_PAIRS' AS view_name, COUNT(*) AS rows_returned
FROM   GOLD.VW_DUPLICATE_INVOICE_PAIRS
UNION ALL
SELECT 'VW_THREE_WAY_MATCH', COUNT(*) FROM GOLD.VW_THREE_WAY_MATCH
UNION ALL
SELECT 'VW_THREE_WAY_MATCH (exceptions only)', COUNT(*)
FROM   GOLD.VW_THREE_WAY_MATCH WHERE MATCH_STATUS = 'Exception - Review';
