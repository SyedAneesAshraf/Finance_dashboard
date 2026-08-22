/* ============================================================================
   PHASE 3 — SILVER EXIT TESTS

   The authoritative check is scripts/validate_phase3.py (33 tests, including
   cross-checks against docs/ground_truth.json). This is the SQL-native
   equivalent, runnable from a Snowsight worksheet.

   Two opposing things must both hold. Testing only the first is how this phase
   usually goes wrong -- a Silver layer can look immaculate precisely because it
   destroyed the evidence Phase 5 needs.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    SILVER;

/* ---- TEST 1 -----------------------------------------------------------------
   All date columns are true DATE type -- the roadmap's headline exit test.
   Expect: every row reads DATE.
   ---------------------------------------------------------------------------- */
SELECT table_name, column_name, data_type,
       CASE WHEN data_type = 'DATE' THEN 'PASS' ELSE 'FAIL' END AS result
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
WHERE  table_schema = 'SILVER'
  AND  column_name LIKE '%DATE%'
  AND  LEFT(column_name, 1) != '_'
ORDER  BY table_name, column_name;

/* ---- TEST 2 -----------------------------------------------------------------
   Referential integrity.

   NOT EXISTS is used rather than the roadmap's NOT IN. If the subquery ever
   returned a NULL, NOT IN evaluates to UNKNOWN for every row and reports zero
   orphans -- passing by accident, which is the worst way for a test to pass.
   Expect: 0 for all five.
   ---------------------------------------------------------------------------- */
SELECT 'invoices -> vendors' AS relationship,
       COUNT(*) AS orphans,
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   SILVER.INVOICES i
WHERE  NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = i.VENDOR_ID)
UNION ALL
SELECT 'invoices -> purchase_orders (non-null only)', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   SILVER.INVOICES i
WHERE  i.PO_ID IS NOT NULL
  AND  NOT EXISTS (SELECT 1 FROM SILVER.PURCHASE_ORDERS p WHERE p.PO_ID = i.PO_ID)
UNION ALL
SELECT 'invoices -> gl_accounts', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   SILVER.INVOICES i
WHERE  NOT EXISTS (SELECT 1 FROM SILVER.GL_ACCOUNTS g WHERE g.ACCOUNT_ID = i.GL_ACCOUNT_ID)
UNION ALL
SELECT 'payments -> invoices', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   SILVER.PAYMENTS p
WHERE  NOT EXISTS (SELECT 1 FROM SILVER.INVOICES i WHERE i.INVOICE_ID = p.INVOICE_ID)
UNION ALL
SELECT 'purchase_orders -> vendors', COUNT(*),
       CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END
FROM   SILVER.PURCHASE_ORDERS p
WHERE  NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = p.VENDOR_ID);

/* ---- TEST 3 -----------------------------------------------------------------
   Silver DID clean: exact duplicates gone, primary keys unique.
   Expect: 672 invoices (from 675), 584 payments (from 587), 0 duplicate keys.
   ---------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM BRONZE.INVOICES) AS bronze_invoices,
    (SELECT COUNT(*) FROM SILVER.INVOICES) AS silver_invoices,
    (SELECT COUNT(*) FROM BRONZE.PAYMENTS) AS bronze_payments,
    (SELECT COUNT(*) FROM SILVER.PAYMENTS) AS silver_payments,
    CASE WHEN (SELECT COUNT(*) FROM BRONZE.INVOICES) - (SELECT COUNT(*) FROM SILVER.INVOICES) = 3
          AND (SELECT COUNT(*) FROM BRONZE.PAYMENTS) - (SELECT COUNT(*) FROM SILVER.PAYMENTS) = 3
         THEN 'PASS' ELSE 'FAIL' END AS result;

SELECT 'INVOICES' AS table_name, COUNT(*) AS duplicate_keys FROM (
    SELECT INVOICE_ID FROM SILVER.INVOICES GROUP BY INVOICE_ID HAVING COUNT(*) > 1)
UNION ALL SELECT 'PAYMENTS', COUNT(*) FROM (
    SELECT PAYMENT_ID FROM SILVER.PAYMENTS GROUP BY PAYMENT_ID HAVING COUNT(*) > 1)
UNION ALL SELECT 'PURCHASE_ORDERS', COUNT(*) FROM (
    SELECT PO_ID FROM SILVER.PURCHASE_ORDERS GROUP BY PO_ID HAVING COUNT(*) > 1)
UNION ALL SELECT 'VENDORS', COUNT(*) FROM (
    SELECT VENDOR_ID FROM SILVER.VENDORS GROUP BY VENDOR_ID HAVING COUNT(*) > 1);

/* ---- TEST 4 -----------------------------------------------------------------
   Silver DID clean: standardization.
   Expect: 5 categories, none 'Unknown'; 0 untrimmed values (Bronze had 51).
   ---------------------------------------------------------------------------- */
SELECT CATEGORY, COUNT(*) AS vendors FROM SILVER.VENDORS GROUP BY CATEGORY ORDER BY CATEGORY;

SELECT
    (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS WHERE PO_STATUS != TRIM(PO_STATUS)) AS untrimmed_po_status,
    (SELECT COUNT(*) FROM SILVER.VENDORS WHERE VENDOR_NAME != TRIM(VENDOR_NAME))     AS untrimmed_vendor_name,
    (SELECT COUNT(*) FROM SILVER.INVOICES WHERE STATUS NOT IN ('Paid','Unpaid'))     AS non_canonical_status;

/* ---- TEST 5 -----------------------------------------------------------------
   Silver DID clean: nulls imputed AND flagged.
   Expect: 4 terms, 3 region, and 0 imputations lacking an audit flag.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT_IF(PAYMENT_TERMS = 'Unknown')                                AS imputed_terms,
    COUNT_IF(REGION        = 'Unknown')                                AS imputed_region,
    COUNT_IF((PAYMENT_TERMS = 'Unknown' OR REGION = 'Unknown')
             AND NOT IS_DQ_FLAGGED)                                    AS imputed_without_flag,
    COUNT_IF(PAYMENT_TERMS IS NULL OR REGION IS NULL)                  AS nulls_remaining
FROM SILVER.VENDORS;

/* The flagged rows themselves -- the audit trail imputation would otherwise erase. */
SELECT VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, DATA_QUALITY_FLAGS
FROM   SILVER.VENDORS
WHERE  IS_DQ_FLAGGED
ORDER  BY VENDOR_ID;

/* ---- TEST 6 -----------------------------------------------------------------
   Silver did NOT over-clean. Every injected defect must still be present.
   Expect: 14 / 18 / 36 / 157 / 88 -- all matching docs/ground_truth.json.
   ---------------------------------------------------------------------------- */
WITH near_dupes AS (
    SELECT COUNT(*) AS n
    FROM   SILVER.INVOICES a
    JOIN   SILVER.INVOICES b
      ON   a.VENDOR_ID = b.VENDOR_ID
     AND   a.AMOUNT    = b.AMOUNT
     AND   a.INVOICE_ID < b.INVOICE_ID
     AND   ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE)) <= 3
),
po_variance AS (
    SELECT COUNT(*) AS n
    FROM ( SELECT PO_ID, SUM(AMOUNT) AS invoiced
           FROM SILVER.INVOICES WHERE PO_ID IS NOT NULL GROUP BY PO_ID ) b
    JOIN SILVER.PURCHASE_ORDERS p ON p.PO_ID = b.PO_ID
    WHERE ABS(b.invoiced - p.PO_AMOUNT) / NULLIF(p.PO_AMOUNT, 0) > 0.05
),
late AS (
    SELECT COUNT(*) AS n
    FROM   SILVER.PAYMENTS p
    JOIN   SILVER.INVOICES i ON i.INVOICE_ID = p.INVOICE_ID
    WHERE  p.PAYMENT_DATE > i.DUE_DATE
)
SELECT 'near-duplicate invoice pairs' AS defect, (SELECT n FROM near_dupes) AS found, 14 AS expected
UNION ALL SELECT 'non-PO invoices (po_id NULL)', (SELECT COUNT(*) FROM SILVER.INVOICES WHERE PO_ID IS NULL), 18
UNION ALL SELECT 'POs breaching 5% tolerance',   (SELECT n FROM po_variance), 36
UNION ALL SELECT 'payments after due date',      (SELECT n FROM late), 157
UNION ALL SELECT 'unpaid invoices',              (SELECT COUNT(*) FROM SILVER.INVOICES WHERE STATUS = 'Unpaid'), 88;

/* ---- TEST 7 -----------------------------------------------------------------
   FX conversion reproduces the ground-truth totals.
   Expect: 6,973,028.93 invoiced and 849,948.62 outstanding.
   ---------------------------------------------------------------------------- */
SELECT
    ROUND(SUM(AMOUNT_USD), 2)                                        AS total_invoiced_usd,
    ROUND(SUM(CASE WHEN STATUS = 'Unpaid' THEN AMOUNT_USD END), 2)   AS total_outstanding_usd,
    COUNT_IF(CURRENCY != 'USD' AND AMOUNT_USD = AMOUNT)              AS unconverted_non_usd
FROM SILVER.INVOICES;

SELECT CURRENCY, COUNT(*) AS invoices,
       ROUND(SUM(AMOUNT), 2)     AS total_entered,
       ROUND(SUM(AMOUNT_USD), 2) AS total_usd
FROM   SILVER.INVOICES
GROUP  BY CURRENCY
ORDER  BY total_usd DESC;

/* ---- TEST 8 -----------------------------------------------------------------
   SCD Type 2 source ready for Phase 4.
   Expect: 6 changed, 0 differing only by name, 2 new.
   ---------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u JOIN SILVER.VENDORS v ON v.VENDOR_ID = u.VENDOR_ID
      WHERE u.CATEGORY != v.CATEGORY OR u.PAYMENT_TERMS != v.PAYMENT_TERMS)  AS changed_vendors,
    (SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u JOIN SILVER.VENDORS v ON v.VENDOR_ID = u.VENDOR_ID
      WHERE u.VENDOR_NAME != v.VENDOR_NAME)                                  AS name_only_differences,
    (SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u
      WHERE NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = u.VENDOR_ID)) AS new_vendors;

/* The six real changes, side by side. These become SCD2 version rows in Phase 4. */
SELECT v.VENDOR_ID, v.VENDOR_NAME,
       v.CATEGORY       AS category_before,  u.CATEGORY      AS category_after,
       v.PAYMENT_TERMS  AS terms_before,     u.PAYMENT_TERMS AS terms_after,
       u.LAST_UPDATE_DATE AS change_date
FROM   SILVER.VENDORS v
JOIN   SILVER.VENDORS_UPDATE u ON u.VENDOR_ID = v.VENDOR_ID
WHERE  u.CATEGORY != v.CATEGORY OR u.PAYMENT_TERMS != v.PAYMENT_TERMS
ORDER  BY v.VENDOR_ID;
