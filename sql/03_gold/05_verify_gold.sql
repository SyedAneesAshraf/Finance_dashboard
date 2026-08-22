/* ============================================================================
   PHASE 4 — GOLD EXIT TESTS

   Authoritative check is scripts/validate_phase4.py (30 tests). This is the
   SQL-native equivalent for a Snowsight worksheet.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ---- TEST 1 -----------------------------------------------------------------
   Every fact FK resolves to its dimension. Expect 0 across the board.
   ---------------------------------------------------------------------------- */
SELECT 'FACT_INVOICE.VENDOR_KEY' AS foreign_key, COUNT(*) AS broken
FROM   GOLD.FACT_INVOICE f
WHERE  NOT EXISTS (SELECT 1 FROM GOLD.DIM_VENDOR d WHERE d.VENDOR_KEY = f.VENDOR_KEY)
UNION ALL SELECT 'FACT_INVOICE.ACCOUNT_KEY', COUNT(*)
FROM   GOLD.FACT_INVOICE f
WHERE  NOT EXISTS (SELECT 1 FROM GOLD.DIM_GL_ACCOUNT d WHERE d.ACCOUNT_KEY = f.ACCOUNT_KEY)
UNION ALL SELECT 'FACT_INVOICE.INVOICE_DATE_KEY', COUNT(*)
FROM   GOLD.FACT_INVOICE f
WHERE  NOT EXISTS (SELECT 1 FROM GOLD.DIM_DATE d WHERE d.DATE_KEY = f.INVOICE_DATE_KEY)
UNION ALL SELECT 'FACT_PAYMENT.VENDOR_KEY', COUNT(*)
FROM   GOLD.FACT_PAYMENT f
WHERE  NOT EXISTS (SELECT 1 FROM GOLD.DIM_VENDOR d WHERE d.VENDOR_KEY = f.VENDOR_KEY)
UNION ALL SELECT 'FACT_PURCHASE_ORDER.VENDOR_KEY', COUNT(*)
FROM   GOLD.FACT_PURCHASE_ORDER f
WHERE  NOT EXISTS (SELECT 1 FROM GOLD.DIM_VENDOR d WHERE d.VENDOR_KEY = f.VENDOR_KEY)
ORDER  BY foreign_key;

/* The one deliberately-unresolvable relationship: non-PO spend.
   Expect 18 null po_id, 0 pointing at a PO that does not exist. */
SELECT
    COUNT_IF(PO_ID IS NULL) AS non_po_spend_invoices,
    (SELECT COUNT(*) FROM GOLD.FACT_INVOICE f
      WHERE f.PO_ID IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM GOLD.FACT_PURCHASE_ORDER p WHERE p.PO_ID = f.PO_ID)
    ) AS invoices_pointing_at_missing_po
FROM GOLD.FACT_INVOICE;

/* ---- TEST 2 -----------------------------------------------------------------
   DIM_VENDOR has MORE rows than distinct vendors -- the structural proof that
   SCD Type 2 is real rather than decorative.
   Expect: 58 rows / 52 vendors / PASS.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT(*)                              AS total_rows,
    COUNT(DISTINCT VENDOR_ID)             AS distinct_vendors,
    COUNT_IF(NOT IS_CURRENT)              AS historical_rows,
    CASE WHEN COUNT(*) > COUNT(DISTINCT VENDOR_ID)
         THEN 'PASS - SCD2 is real' ELSE 'FAIL - decorative only' END AS result
FROM GOLD.DIM_VENDOR;

/* Structural integrity of the version chain. Expect 0, 0, 0. */
SELECT
    (SELECT COUNT(*) FROM (
        SELECT VENDOR_ID FROM GOLD.DIM_VENDOR
        GROUP BY VENDOR_ID HAVING COUNT_IF(IS_CURRENT) <> 1)
    ) AS vendors_not_exactly_one_current,
    (SELECT COUNT(*) FROM GOLD.DIM_VENDOR a
      JOIN GOLD.DIM_VENDOR b ON a.VENDOR_ID = b.VENDOR_ID AND a.VENDOR_KEY < b.VENDOR_KEY
       AND a.EFFECTIVE_START_DATE <= b.EFFECTIVE_END_DATE
       AND b.EFFECTIVE_START_DATE <= a.EFFECTIVE_END_DATE
    ) AS overlapping_ranges,
    (SELECT COUNT(*) FROM (
        SELECT EFFECTIVE_END_DATE,
               LEAD(EFFECTIVE_START_DATE) OVER (PARTITION BY VENDOR_ID ORDER BY EFFECTIVE_START_DATE) AS next_start
        FROM GOLD.DIM_VENDOR)
      WHERE next_start IS NOT NULL AND next_start <> DATEADD('day', 1, EFFECTIVE_END_DATE)
    ) AS gaps_between_versions;

/* ---- TEST 3 -----------------------------------------------------------------
   THE DEFINITIVE SCD2 TEST (query 5.6 in preview form).

   Same vendor, different invoice dates, DIFFERENT payment terms. A flat or
   current-only vendor table cannot produce this result at all.
   Expect: 5 vendors, each showing two different terms.
   ---------------------------------------------------------------------------- */
SELECT
    f.VENDOR_ID,
    v.VENDOR_NAME,
    v.PAYMENT_TERMS,
    v.EFFECTIVE_START_DATE,
    v.EFFECTIVE_END_DATE,
    COUNT(*)          AS invoices,
    MIN(f.INVOICE_DATE) AS first_invoice,
    MAX(f.INVOICE_DATE) AS last_invoice
FROM      GOLD.FACT_INVOICE f
JOIN      GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = f.VENDOR_ID
      AND f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
WHERE f.VENDOR_ID IN (
    SELECT VENDOR_ID FROM GOLD.DIM_VENDOR GROUP BY VENDOR_ID HAVING COUNT(*) > 1
)
GROUP BY f.VENDOR_ID, v.VENDOR_NAME, v.PAYMENT_TERMS,
         v.EFFECTIVE_START_DATE, v.EFFECTIVE_END_DATE
ORDER BY f.VENDOR_ID, v.EFFECTIVE_START_DATE;

/* The range join must return exactly one dimension row per invoice.
   More than one = overlapping ranges; fewer = a gap. Expect 0. */
SELECT COUNT(*) AS invoices_not_matching_exactly_one_version
FROM (
    SELECT f.INVOICE_ID
    FROM   GOLD.FACT_INVOICE f
    JOIN   GOLD.DIM_VENDOR v
      ON   v.VENDOR_ID = f.VENDOR_ID
     AND   f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
    GROUP  BY f.INVOICE_ID
    HAVING COUNT(*) <> 1
);

/* ---- TEST 4 -----------------------------------------------------------------
   The two join routes must agree.

   Route A: range join on the business key (what query 5.6 does)
   Route B: equality join on the surrogate key (what Power BI does)

   If these ever disagree, the surrogate keys were resolved to the wrong version
   at load time -- and every Power BI figure would be quietly wrong while the SQL
   stayed right, which is the hardest kind of bug to notice. Expect 0.
   ---------------------------------------------------------------------------- */
SELECT COUNT(*) AS rows_where_routes_disagree
FROM GOLD.FACT_INVOICE f
JOIN GOLD.DIM_VENDOR rng
  ON rng.VENDOR_ID = f.VENDOR_ID
 AND f.INVOICE_DATE BETWEEN rng.EFFECTIVE_START_DATE AND rng.EFFECTIVE_END_DATE
JOIN GOLD.DIM_VENDOR sk ON sk.VENDOR_KEY = f.VENDOR_KEY
WHERE rng.VENDOR_KEY <> sk.VENDOR_KEY;

/* How many facts actually use the history? Expect a non-zero historical count. */
SELECT
    COUNT_IF(NOT v.IS_CURRENT) AS invoices_on_historical_versions,
    COUNT_IF(v.IS_CURRENT)     AS invoices_on_current_versions
FROM GOLD.FACT_INVOICE f
JOIN GOLD.DIM_VENDOR v ON v.VENDOR_KEY = f.VENDOR_KEY;

/* ---- TEST 5 -----------------------------------------------------------------
   DIM_DATE covers the full fact range with no gaps.
   Expect: 1461 rows, 0 gaps, dimension range enclosing the fact range.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT(*)                  AS date_rows,
    COUNT(DISTINCT FULL_DATE) AS distinct_dates,
    MIN(FULL_DATE)            AS dim_min,
    MAX(FULL_DATE)            AS dim_max
FROM GOLD.DIM_DATE;

SELECT COUNT(*) AS gaps_in_date_spine
FROM ( SELECT FULL_DATE, LAG(FULL_DATE) OVER (ORDER BY FULL_DATE) AS prev FROM GOLD.DIM_DATE )
WHERE prev IS NOT NULL AND DATEDIFF('day', prev, FULL_DATE) <> 1;

SELECT MIN(d) AS fact_min_date, MAX(d) AS fact_max_date
FROM (
    SELECT INVOICE_DATE AS d FROM GOLD.FACT_INVOICE
    UNION ALL SELECT DUE_DATE     FROM GOLD.FACT_INVOICE
    UNION ALL SELECT PAYMENT_DATE FROM GOLD.FACT_PAYMENT
    UNION ALL SELECT PO_DATE      FROM GOLD.FACT_PURCHASE_ORDER
);

/* ---- TEST 6 -----------------------------------------------------------------
   Row and amount reconciliation, Silver to Gold. Expect 0 lost everywhere.
   ---------------------------------------------------------------------------- */
SELECT 'FACT_INVOICE' AS fact_table,
       (SELECT COUNT(*) FROM SILVER.INVOICES) AS silver_rows,
       (SELECT COUNT(*) FROM GOLD.FACT_INVOICE) AS gold_rows,
       ROUND((SELECT SUM(AMOUNT_USD) FROM SILVER.INVOICES), 2)   AS silver_usd,
       ROUND((SELECT SUM(AMOUNT_USD) FROM GOLD.FACT_INVOICE), 2) AS gold_usd
UNION ALL
SELECT 'FACT_PAYMENT',
       (SELECT COUNT(*) FROM SILVER.PAYMENTS),
       (SELECT COUNT(*) FROM GOLD.FACT_PAYMENT),
       ROUND((SELECT SUM(AMOUNT_PAID_USD) FROM SILVER.PAYMENTS), 2),
       ROUND((SELECT SUM(AMOUNT_PAID_USD) FROM GOLD.FACT_PAYMENT), 2)
UNION ALL
SELECT 'FACT_PURCHASE_ORDER',
       (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS),
       (SELECT COUNT(*) FROM GOLD.FACT_PURCHASE_ORDER),
       ROUND((SELECT SUM(PO_AMOUNT_USD) FROM SILVER.PURCHASE_ORDERS), 2),
       ROUND((SELECT SUM(PO_AMOUNT_USD) FROM GOLD.FACT_PURCHASE_ORDER), 2)
ORDER BY fact_table;

/* ---- TEST 7 -----------------------------------------------------------------
   Stored measures recompute correctly. Expect 0 mismatches, DPO = 41.83.
   ---------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM GOLD.FACT_PAYMENT p
      JOIN GOLD.FACT_INVOICE f ON f.INVOICE_ID = p.INVOICE_ID
      WHERE p.DAYS_TO_PAY <> DATEDIFF('day', f.INVOICE_DATE, p.PAYMENT_DATE)
         OR p.DAYS_LATE   <> GREATEST(0, DATEDIFF('day', f.DUE_DATE, p.PAYMENT_DATE))
    ) AS miscomputed_measures,
    (SELECT ROUND(AVG(DAYS_TO_PAY), 2) FROM GOLD.FACT_PAYMENT) AS avg_dpo_days,
    (SELECT COUNT_IF(IS_LATE) FROM GOLD.FACT_PAYMENT)          AS late_payments;
