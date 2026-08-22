/* ============================================================================
   PHASE 2 — BRONZE EXIT TESTS

   The authoritative check is scripts/validate_phase2.py, which compares all
   1,864 rows against the source CSVs cell by cell. This file is the SQL-native
   equivalent so the repo can be verified from a Snowsight worksheet alone.

   Expected row counts are hard-coded from the Phase 1 ground truth
   (docs/ground_truth.json). If the generator is re-run with a different seed,
   these must be updated.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    BRONZE;

/* ---- TEST 1 -----------------------------------------------------------------
   Row counts match the source files exactly.
   Expect: 6 rows, all PASS.
   ---------------------------------------------------------------------------- */
WITH actual AS (
    SELECT 'GL_ACCOUNTS'     AS table_name, COUNT(*) AS rows_loaded FROM BRONZE.GL_ACCOUNTS
    UNION ALL SELECT 'VENDORS',         COUNT(*) FROM BRONZE.VENDORS
    UNION ALL SELECT 'VENDORS_UPDATE',  COUNT(*) FROM BRONZE.VENDORS_UPDATE
    UNION ALL SELECT 'PURCHASE_ORDERS', COUNT(*) FROM BRONZE.PURCHASE_ORDERS
    UNION ALL SELECT 'INVOICES',        COUNT(*) FROM BRONZE.INVOICES
    UNION ALL SELECT 'PAYMENTS',        COUNT(*) FROM BRONZE.PAYMENTS
),
expected AS (
    SELECT 'GL_ACCOUNTS' AS table_name, 20 AS csv_rows
    UNION ALL SELECT 'VENDORS',          50
    UNION ALL SELECT 'VENDORS_UPDATE',   52
    UNION ALL SELECT 'PURCHASE_ORDERS', 480
    UNION ALL SELECT 'INVOICES',        675
    UNION ALL SELECT 'PAYMENTS',        587
)
SELECT
    a.table_name,
    e.csv_rows,
    a.rows_loaded,
    CASE WHEN a.rows_loaded = e.csv_rows THEN 'PASS' ELSE 'FAIL' END AS result
FROM   actual a
JOIN   expected e ON a.table_name = e.table_name
ORDER  BY a.table_name;

/* ---- TEST 2 -----------------------------------------------------------------
   Spot-check 5 random rows per table against the CSV by eye.
   Note the unparsed dates, mixed casing and untrimmed spaces -- that is the
   point. Bronze shows you exactly what the source system sent.
   ---------------------------------------------------------------------------- */
SELECT * FROM BRONZE.VENDORS          SAMPLE (5 ROWS);
SELECT * FROM BRONZE.PURCHASE_ORDERS  SAMPLE (5 ROWS);
SELECT * FROM BRONZE.INVOICES         SAMPLE (5 ROWS);
SELECT * FROM BRONZE.PAYMENTS         SAMPLE (5 ROWS);
SELECT * FROM BRONZE.GL_ACCOUNTS      SAMPLE (5 ROWS);

/* ---- TEST 3 -----------------------------------------------------------------
   Nothing was cleaned on the way in.

   CATEGORY should show MORE raw variants than real values -- if these two
   numbers were equal, something standardized the data during load, which is a
   medallion violation.
   Expect: 15 raw variants, 5 canonical, PASS.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT(DISTINCT CATEGORY)              AS raw_variants,
    COUNT(DISTINCT UPPER(TRIM(CATEGORY))) AS canonical_values,
    CASE WHEN COUNT(DISTINCT CATEGORY) > COUNT(DISTINCT UPPER(TRIM(CATEGORY)))
         THEN 'PASS - noise preserved' ELSE 'FAIL - data was standardized' END AS result
FROM BRONZE.VENDORS;

/* The raw variants themselves. */
SELECT CATEGORY, COUNT(*) AS n
FROM   BRONZE.VENDORS
GROUP  BY CATEGORY
ORDER  BY UPPER(TRIM(CATEGORY)), CATEGORY;

/* Untrimmed whitespace survived. Expect: a non-zero count. */
SELECT
    COUNT_IF(PO_STATUS != TRIM(PO_STATUS)) AS untrimmed_values,
    CASE WHEN COUNT_IF(PO_STATUS != TRIM(PO_STATUS)) > 0
         THEN 'PASS' ELSE 'FAIL' END AS result
FROM BRONZE.PURCHASE_ORDERS;

/* All three date formats still present as unparsed strings.
   Expect: all three columns non-zero. */
SELECT
    COUNT_IF(INVOICE_DATE LIKE '____-__-__')  AS iso_yyyy_mm_dd,
    COUNT_IF(INVOICE_DATE LIKE '__/__/____')  AS us_mm_dd_yyyy,
    COUNT_IF(INVOICE_DATE LIKE '__-___-____') AS oracle_dd_mon_yyyy
FROM BRONZE.INVOICES;

/* ---- TEST 4 -----------------------------------------------------------------
   Blank source fields became real NULLs, not empty strings.
   The distinction matters: NULL means "the source sent nothing", '' means "the
   source sent a blank". Conflating them makes Silver's null handling untestable.
   Expect: 4, 3, 18, 0.
   ---------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM BRONZE.VENDORS  WHERE PAYMENT_TERMS IS NULL) AS null_payment_terms,
    (SELECT COUNT(*) FROM BRONZE.VENDORS  WHERE REGION        IS NULL) AS null_region,
    (SELECT COUNT(*) FROM BRONZE.INVOICES WHERE PO_ID         IS NULL) AS null_po_id_non_po_spend,
    (SELECT COUNT(*) FROM BRONZE.VENDORS  WHERE PAYMENT_TERMS = '' OR REGION = '') AS empty_strings;

/* ---- TEST 5 -----------------------------------------------------------------
   Deliberate duplicates survived. Bronze must NOT deduplicate -- that is Silver's
   decision to make, and only for exact whole-row duplicates.
   Expect: 3 invoice ids and 3 payment ids, each appearing twice.
   ---------------------------------------------------------------------------- */
SELECT INVOICE_ID, COUNT(*) AS occurrences,
       MIN(_FILE_ROW_NUMBER) AS first_file_line,
       MAX(_FILE_ROW_NUMBER) AS second_file_line
FROM   BRONZE.INVOICES
GROUP  BY INVOICE_ID
HAVING COUNT(*) > 1
ORDER  BY INVOICE_ID;

SELECT PAYMENT_ID, COUNT(*) AS occurrences
FROM   BRONZE.PAYMENTS
GROUP  BY PAYMENT_ID
HAVING COUNT(*) > 1
ORDER  BY PAYMENT_ID;

/* ---- TEST 6 -----------------------------------------------------------------
   No transformation logic exists in this schema.

   (a) No views -- a view here would be derived logic.
   (b) Every business column is still TEXT. A DATE or NUMBER column would mean a
       parse decision was made at load time, which is exactly what Bronze defers.
   Expect: 0 rows from both.
   ---------------------------------------------------------------------------- */
SELECT table_name, 'view in BRONZE' AS violation
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.VIEWS
WHERE  table_schema = 'BRONZE';

SELECT table_name, column_name, data_type, 'business column is not TEXT' AS violation
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
WHERE  table_schema = 'BRONZE'
  AND  LEFT(column_name, 1) != '_'
  AND  data_type != 'TEXT'
ORDER  BY table_name, column_name;

/* ---- TEST 7 -----------------------------------------------------------------
   Load lineage populated on every row.
   Expect: 0 rows missing metadata.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT(*) AS rows_missing_lineage,
    CASE WHEN COUNT(*) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   BRONZE.INVOICES
WHERE  _SOURCE_FILE IS NULL OR _FILE_ROW_NUMBER IS NULL OR _LOAD_TS IS NULL;

/* Where each table came from, and when. */
SELECT 'GL_ACCOUNTS' AS table_name, ANY_VALUE(_SOURCE_FILE) AS source_file, MAX(_LOAD_TS) AS loaded_at FROM BRONZE.GL_ACCOUNTS
UNION ALL SELECT 'VENDORS',         ANY_VALUE(_SOURCE_FILE), MAX(_LOAD_TS) FROM BRONZE.VENDORS
UNION ALL SELECT 'VENDORS_UPDATE',  ANY_VALUE(_SOURCE_FILE), MAX(_LOAD_TS) FROM BRONZE.VENDORS_UPDATE
UNION ALL SELECT 'PURCHASE_ORDERS', ANY_VALUE(_SOURCE_FILE), MAX(_LOAD_TS) FROM BRONZE.PURCHASE_ORDERS
UNION ALL SELECT 'INVOICES',        ANY_VALUE(_SOURCE_FILE), MAX(_LOAD_TS) FROM BRONZE.INVOICES
UNION ALL SELECT 'PAYMENTS',        ANY_VALUE(_SOURCE_FILE), MAX(_LOAD_TS) FROM BRONZE.PAYMENTS
ORDER BY table_name;
