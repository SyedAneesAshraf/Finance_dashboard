/* ============================================================================
   PHASE 3 — SILVER LOAD (cleaning & standardization)

   Every rule applied here is documented with its reasoning in
   docs/07_phase3_cleaning_rules.md. The short version:

     PARSE   three date formats via a TRY_TO_DATE chain
     TYPE    amounts to NUMBER(15,2), dates to DATE
     TRIM    leading/trailing whitespace everywhere
     MAP     controlled vocabularies to canonical values via explicit CASE
     DEDUP   exact whole-row duplicates only
     IMPUTE  missing values to 'Unknown', AND flag them
     CONVERT entered amounts to USD alongside the original

   What Silver must NOT do, because Phase 5 needs these to still exist:
     - collapse near-duplicate invoices (different IDs, same vendor/amount/date)
     - fill in a placeholder PO for non-PO spend
     - correct invoice-vs-PO amount variances
     - adjust late payment dates

   Load order respects the foreign keys: vendors -> GL -> POs -> invoices -> payments.
   Every statement is re-runnable (TRUNCATE then INSERT).
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    SILVER;

/* ---------------------------------------------------------------------------
   PRE-FLIGHT: would any date fail to parse?

   Silver declares the transaction date columns NOT NULL, so an unparseable date
   would abort the load rather than land a row with a silently missing date. This
   check surfaces that BEFORE the load, while the raw string is still in Bronze
   and can be inspected. Expect 0 across the board.
   --------------------------------------------------------------------------- */
SELECT 'invoices.invoice_date' AS column_checked,
       COUNT_IF(COALESCE(TRY_TO_DATE(INVOICE_DATE,'YYYY-MM-DD'),
                         TRY_TO_DATE(INVOICE_DATE,'MM/DD/YYYY'),
                         TRY_TO_DATE(INVOICE_DATE,'DD-MON-YYYY')) IS NULL) AS unparseable
FROM BRONZE.INVOICES
UNION ALL
SELECT 'invoices.due_date',
       COUNT_IF(COALESCE(TRY_TO_DATE(DUE_DATE,'YYYY-MM-DD'),
                         TRY_TO_DATE(DUE_DATE,'MM/DD/YYYY'),
                         TRY_TO_DATE(DUE_DATE,'DD-MON-YYYY')) IS NULL)
FROM BRONZE.INVOICES
UNION ALL
SELECT 'purchase_orders.po_date',
       COUNT_IF(COALESCE(TRY_TO_DATE(PO_DATE,'YYYY-MM-DD'),
                         TRY_TO_DATE(PO_DATE,'MM/DD/YYYY'),
                         TRY_TO_DATE(PO_DATE,'DD-MON-YYYY')) IS NULL)
FROM BRONZE.PURCHASE_ORDERS
UNION ALL
SELECT 'payments.payment_date',
       COUNT_IF(COALESCE(TRY_TO_DATE(PAYMENT_DATE,'YYYY-MM-DD'),
                         TRY_TO_DATE(PAYMENT_DATE,'MM/DD/YYYY'),
                         TRY_TO_DATE(PAYMENT_DATE,'DD-MON-YYYY')) IS NULL)
FROM BRONZE.PAYMENTS;


/* ===========================================================================
   1. GL ACCOUNTS
   =========================================================================== */
TRUNCATE TABLE SILVER.GL_ACCOUNTS;

INSERT INTO SILVER.GL_ACCOUNTS
    (ACCOUNT_ID, ACCOUNT_NAME, COST_CENTER, DEPARTMENT,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.GL_ACCOUNTS
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY ACCOUNT_ID, ACCOUNT_NAME, COST_CENTER, DEPARTMENT
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
)
SELECT
    TRIM(ACCOUNT_ID),
    TRIM(ACCOUNT_NAME),
    UPPER(TRIM(COST_CENTER)),
    TRIM(DEPARTMENT),
    NULL,
    FALSE,
    CURRENT_TIMESTAMP()
FROM deduped;


/* ===========================================================================
   2. VENDORS  (extract #1)

   Casing rule: only normalize names that are entirely upper or entirely lower
   case. A blanket INITCAP would rewrite correctly-cased names too --
   'Arnold, Mitchell and Jones' would become '... And Jones'. Normalizing only
   the clearly-noisy cases leaves good data alone.

   Controlled vocabularies use an explicit CASE rather than INITCAP because
   INITCAP('IT SERVICES') yields 'It Services'. An unrecognised value falls to
   'Unknown' and is flagged, so a new source value shows up as a finding instead
   of silently entering the warehouse.
   =========================================================================== */
TRUNCATE TABLE SILVER.VENDORS;

INSERT INTO SILVER.VENDORS
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, PAYMENT_TERMS_DAYS,
     CURRENCY, CREATION_DATE, LAST_UPDATE_DATE,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.VENDORS
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY VENDOR_ID, VENDOR_NAME, CATEGORY, REGION,
                     PAYMENT_TERMS, CURRENCY, CREATION_DATE, LAST_UPDATE_DATE
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
),
cleaned AS (
    SELECT
        TRIM(VENDOR_ID) AS vendor_id,
        CASE
            WHEN TRIM(VENDOR_NAME) = UPPER(TRIM(VENDOR_NAME))
              OR TRIM(VENDOR_NAME) = LOWER(TRIM(VENDOR_NAME))
            THEN INITCAP(TRIM(VENDOR_NAME))
            ELSE TRIM(VENDOR_NAME)
        END AS vendor_name,
        CASE UPPER(TRIM(CATEGORY))
            WHEN 'RAW MATERIALS'   THEN 'Raw Materials'
            WHEN 'IT SERVICES'     THEN 'IT Services'
            WHEN 'LOGISTICS'       THEN 'Logistics'
            WHEN 'OFFICE SUPPLIES' THEN 'Office Supplies'
            WHEN 'CONSULTING'      THEN 'Consulting'
            ELSE 'Unknown'
        END AS category,
        CASE UPPER(TRIM(REGION))
            WHEN 'NORTH AMERICA' THEN 'North America'
            WHEN 'EMEA'          THEN 'EMEA'
            WHEN 'APAC'          THEN 'APAC'
            WHEN 'LATAM'         THEN 'LATAM'
            ELSE 'Unknown'
        END AS region,
        COALESCE(NULLIF(UPPER(TRIM(PAYMENT_TERMS)), ''), 'Unknown') AS payment_terms,
        CASE UPPER(TRIM(PAYMENT_TERMS))
            WHEN 'NET15'      THEN 15
            WHEN 'NET30'      THEN 30
            WHEN 'NET45'      THEN 45
            WHEN 'NET60'      THEN 60
            WHEN '2/10 NET30' THEN 30   -- net due in 30; the 2% discount is not modelled
            ELSE NULL
        END AS payment_terms_days,
        UPPER(TRIM(CURRENCY)) AS currency,
        COALESCE(TRY_TO_DATE(CREATION_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(CREATION_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(CREATION_DATE,'DD-MON-YYYY')) AS creation_date,
        COALESCE(TRY_TO_DATE(LAST_UPDATE_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(LAST_UPDATE_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(LAST_UPDATE_DATE,'DD-MON-YYYY')) AS last_update_date,
        PAYMENT_TERMS AS raw_terms,
        REGION        AS raw_region,
        CATEGORY      AS raw_category
    FROM deduped
)
SELECT
    vendor_id, vendor_name, category, region, payment_terms, payment_terms_days,
    currency, creation_date, last_update_date,
    NULLIF(ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
        CASE WHEN raw_terms    IS NULL THEN 'MISSING_PAYMENT_TERMS' END,
        CASE WHEN raw_region   IS NULL THEN 'MISSING_REGION'        END,
        CASE WHEN category = 'Unknown' AND raw_category IS NOT NULL THEN 'UNMAPPED_CATEGORY' END,
        CASE WHEN creation_date    IS NULL THEN 'UNPARSEABLE_CREATION_DATE'    END,
        CASE WHEN last_update_date IS NULL THEN 'UNPARSEABLE_LAST_UPDATE_DATE' END
    ), ','), '') AS data_quality_flags,
    (raw_terms IS NULL OR raw_region IS NULL
     OR (category = 'Unknown' AND raw_category IS NOT NULL)
     OR creation_date IS NULL OR last_update_date IS NULL) AS is_dq_flagged,
    CURRENT_TIMESTAMP()
FROM cleaned;


/* ===========================================================================
   3. VENDORS_UPDATE  (extract #2)
   Identical rules to extract #1. That is the point: both extracts must pass
   through exactly the same standardization, or the Phase 4 SCD Type 2 MERGE
   would read a casing difference as a genuine attribute change and emit a
   spurious version row.
   =========================================================================== */
TRUNCATE TABLE SILVER.VENDORS_UPDATE;

INSERT INTO SILVER.VENDORS_UPDATE
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, PAYMENT_TERMS_DAYS,
     CURRENCY, CREATION_DATE, LAST_UPDATE_DATE,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.VENDORS_UPDATE
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY VENDOR_ID, VENDOR_NAME, CATEGORY, REGION,
                     PAYMENT_TERMS, CURRENCY, CREATION_DATE, LAST_UPDATE_DATE
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
),
cleaned AS (
    SELECT
        TRIM(VENDOR_ID) AS vendor_id,
        CASE
            WHEN TRIM(VENDOR_NAME) = UPPER(TRIM(VENDOR_NAME))
              OR TRIM(VENDOR_NAME) = LOWER(TRIM(VENDOR_NAME))
            THEN INITCAP(TRIM(VENDOR_NAME))
            ELSE TRIM(VENDOR_NAME)
        END AS vendor_name,
        CASE UPPER(TRIM(CATEGORY))
            WHEN 'RAW MATERIALS'   THEN 'Raw Materials'
            WHEN 'IT SERVICES'     THEN 'IT Services'
            WHEN 'LOGISTICS'       THEN 'Logistics'
            WHEN 'OFFICE SUPPLIES' THEN 'Office Supplies'
            WHEN 'CONSULTING'      THEN 'Consulting'
            ELSE 'Unknown'
        END AS category,
        CASE UPPER(TRIM(REGION))
            WHEN 'NORTH AMERICA' THEN 'North America'
            WHEN 'EMEA'          THEN 'EMEA'
            WHEN 'APAC'          THEN 'APAC'
            WHEN 'LATAM'         THEN 'LATAM'
            ELSE 'Unknown'
        END AS region,
        COALESCE(NULLIF(UPPER(TRIM(PAYMENT_TERMS)), ''), 'Unknown') AS payment_terms,
        CASE UPPER(TRIM(PAYMENT_TERMS))
            WHEN 'NET15'      THEN 15
            WHEN 'NET30'      THEN 30
            WHEN 'NET45'      THEN 45
            WHEN 'NET60'      THEN 60
            WHEN '2/10 NET30' THEN 30
            ELSE NULL
        END AS payment_terms_days,
        UPPER(TRIM(CURRENCY)) AS currency,
        COALESCE(TRY_TO_DATE(CREATION_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(CREATION_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(CREATION_DATE,'DD-MON-YYYY')) AS creation_date,
        COALESCE(TRY_TO_DATE(LAST_UPDATE_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(LAST_UPDATE_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(LAST_UPDATE_DATE,'DD-MON-YYYY')) AS last_update_date,
        PAYMENT_TERMS AS raw_terms,
        REGION        AS raw_region,
        CATEGORY      AS raw_category
    FROM deduped
)
SELECT
    vendor_id, vendor_name, category, region, payment_terms, payment_terms_days,
    currency, creation_date, last_update_date,
    NULLIF(ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
        CASE WHEN raw_terms    IS NULL THEN 'MISSING_PAYMENT_TERMS' END,
        CASE WHEN raw_region   IS NULL THEN 'MISSING_REGION'        END,
        CASE WHEN category = 'Unknown' AND raw_category IS NOT NULL THEN 'UNMAPPED_CATEGORY' END,
        CASE WHEN creation_date    IS NULL THEN 'UNPARSEABLE_CREATION_DATE'    END,
        CASE WHEN last_update_date IS NULL THEN 'UNPARSEABLE_LAST_UPDATE_DATE' END
    ), ','), '') AS data_quality_flags,
    (raw_terms IS NULL OR raw_region IS NULL
     OR (category = 'Unknown' AND raw_category IS NOT NULL)
     OR creation_date IS NULL OR last_update_date IS NULL) AS is_dq_flagged,
    CURRENT_TIMESTAMP()
FROM cleaned;


/* ===========================================================================
   4. PURCHASE ORDERS
   =========================================================================== */
TRUNCATE TABLE SILVER.PURCHASE_ORDERS;

INSERT INTO SILVER.PURCHASE_ORDERS
    (PO_ID, VENDOR_ID, PO_DATE, PO_AMOUNT, CURRENCY, PO_AMOUNT_USD,
     GOODS_RECEIPT_DATE, PO_STATUS,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.PURCHASE_ORDERS
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY PO_ID, VENDOR_ID, PO_DATE, PO_AMOUNT, CURRENCY,
                     GOODS_RECEIPT_DATE, PO_STATUS
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
),
cleaned AS (
    SELECT
        TRIM(PO_ID)     AS po_id,
        TRIM(VENDOR_ID) AS vendor_id,
        COALESCE(TRY_TO_DATE(PO_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(PO_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(PO_DATE,'DD-MON-YYYY')) AS po_date,
        TRY_TO_DECIMAL(TRIM(PO_AMOUNT), 15, 2) AS po_amount,
        UPPER(TRIM(CURRENCY)) AS currency,
        COALESCE(TRY_TO_DATE(GOODS_RECEIPT_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(GOODS_RECEIPT_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(GOODS_RECEIPT_DATE,'DD-MON-YYYY')) AS goods_receipt_date,
        CASE UPPER(TRIM(PO_STATUS))
            WHEN 'OPEN'               THEN 'Open'
            WHEN 'CLOSED'             THEN 'Closed'
            WHEN 'CANCELLED'          THEN 'Cancelled'
            WHEN 'PARTIALLY RECEIVED' THEN 'Partially Received'
            ELSE 'Unknown'
        END AS po_status,
        GOODS_RECEIPT_DATE AS raw_gr
    FROM deduped
)
SELECT
    c.po_id, c.vendor_id, c.po_date, c.po_amount, c.currency,
    ROUND(c.po_amount * f.RATE_TO_USD, 2) AS po_amount_usd,
    c.goods_receipt_date, c.po_status,
    NULLIF(ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
        CASE WHEN c.raw_gr IS NULL                       THEN 'NO_GOODS_RECEIPT'    END,
        CASE WHEN c.goods_receipt_date < c.po_date        THEN 'GR_BEFORE_PO_DATE'   END,
        CASE WHEN c.po_status = 'Unknown'                 THEN 'UNMAPPED_PO_STATUS'  END,
        CASE WHEN c.po_amount <= 0                        THEN 'NON_POSITIVE_AMOUNT' END
    ), ','), '') AS data_quality_flags,
    (c.raw_gr IS NULL OR c.goods_receipt_date < c.po_date
     OR c.po_status = 'Unknown' OR c.po_amount <= 0) AS is_dq_flagged,
    CURRENT_TIMESTAMP()
FROM cleaned c
JOIN SILVER.FX_RATES f ON f.CURRENCY_CODE = c.currency;


/* ===========================================================================
   5. INVOICES

   The deduplication here is the single most consequential statement in the
   Silver layer. It partitions on EVERY business column, so it collapses only
   byte-identical rows (defect 8, a double-load artefact).

   It deliberately does NOT partition on (vendor_id, amount, invoice_date),
   which would also collapse the 14 near-duplicate pairs -- and those are the
   entire subject of query 5.4. Removing them here would leave Phase 5 with
   nothing to detect while making Silver look cleaner.
   =========================================================================== */
TRUNCATE TABLE SILVER.INVOICES;

INSERT INTO SILVER.INVOICES
    (INVOICE_ID, VENDOR_ID, PO_ID, GL_ACCOUNT_ID, INVOICE_DATE, DUE_DATE,
     AMOUNT, CURRENCY, AMOUNT_USD, STATUS,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.INVOICES
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY INVOICE_ID, VENDOR_ID, PO_ID, GL_ACCOUNT_ID,
                     INVOICE_DATE, DUE_DATE, AMOUNT, CURRENCY, STATUS
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
),
cleaned AS (
    SELECT
        TRIM(INVOICE_ID)    AS invoice_id,
        TRIM(VENDOR_ID)     AS vendor_id,
        NULLIF(TRIM(COALESCE(PO_ID, '')), '') AS po_id,   -- stays NULL: non-PO spend
        TRIM(GL_ACCOUNT_ID) AS gl_account_id,
        COALESCE(TRY_TO_DATE(INVOICE_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(INVOICE_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(INVOICE_DATE,'DD-MON-YYYY')) AS invoice_date,
        COALESCE(TRY_TO_DATE(DUE_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(DUE_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(DUE_DATE,'DD-MON-YYYY')) AS due_date,
        TRY_TO_DECIMAL(TRIM(AMOUNT), 15, 2) AS amount,
        UPPER(TRIM(CURRENCY)) AS currency,
        CASE UPPER(TRIM(STATUS))
            WHEN 'PAID'   THEN 'Paid'
            WHEN 'UNPAID' THEN 'Unpaid'
            ELSE 'Unknown'
        END AS status
    FROM deduped
)
SELECT
    c.invoice_id, c.vendor_id, c.po_id, c.gl_account_id,
    c.invoice_date, c.due_date, c.amount, c.currency,
    ROUND(c.amount * f.RATE_TO_USD, 2) AS amount_usd,
    c.status,
    NULLIF(ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
        CASE WHEN c.po_id IS NULL         THEN 'NON_PO_SPEND'         END,
        CASE WHEN c.due_date < c.invoice_date THEN 'DUE_BEFORE_INVOICE' END,
        CASE WHEN c.status = 'Unknown'    THEN 'UNMAPPED_STATUS'      END,
        CASE WHEN c.amount <= 0           THEN 'NON_POSITIVE_AMOUNT'  END
    ), ','), '') AS data_quality_flags,
    (c.po_id IS NULL OR c.due_date < c.invoice_date
     OR c.status = 'Unknown' OR c.amount <= 0) AS is_dq_flagged,
    CURRENT_TIMESTAMP()
FROM cleaned c
JOIN SILVER.FX_RATES f ON f.CURRENCY_CODE = c.currency;


/* ===========================================================================
   6. PAYMENTS
   =========================================================================== */
TRUNCATE TABLE SILVER.PAYMENTS;

INSERT INTO SILVER.PAYMENTS
    (PAYMENT_ID, INVOICE_ID, PAYMENT_DATE, AMOUNT_PAID, CURRENCY,
     AMOUNT_PAID_USD, PAYMENT_METHOD,
     DATA_QUALITY_FLAGS, IS_DQ_FLAGGED, _SILVER_LOAD_TS)
WITH deduped AS (
    SELECT *
    FROM   BRONZE.PAYMENTS
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY PAYMENT_ID, INVOICE_ID, PAYMENT_DATE, AMOUNT_PAID,
                     CURRENCY, PAYMENT_METHOD
        ORDER BY _FILE_ROW_NUMBER
    ) = 1
),
cleaned AS (
    SELECT
        TRIM(PAYMENT_ID) AS payment_id,
        TRIM(INVOICE_ID) AS invoice_id,
        COALESCE(TRY_TO_DATE(PAYMENT_DATE,'YYYY-MM-DD'),
                 TRY_TO_DATE(PAYMENT_DATE,'MM/DD/YYYY'),
                 TRY_TO_DATE(PAYMENT_DATE,'DD-MON-YYYY')) AS payment_date,
        TRY_TO_DECIMAL(TRIM(AMOUNT_PAID), 15, 2) AS amount_paid,
        UPPER(TRIM(CURRENCY)) AS currency,
        CASE UPPER(TRIM(PAYMENT_METHOD))
            WHEN 'ACH'            THEN 'ACH'
            WHEN 'WIRE TRANSFER'  THEN 'Wire Transfer'
            WHEN 'CHECK'          THEN 'Check'
            WHEN 'CORPORATE CARD' THEN 'Corporate Card'
            ELSE 'Unknown'
        END AS payment_method
    FROM deduped
)
SELECT
    c.payment_id, c.invoice_id, c.payment_date, c.amount_paid, c.currency,
    ROUND(c.amount_paid * f.RATE_TO_USD, 2) AS amount_paid_usd,
    c.payment_method,
    NULLIF(ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
        CASE WHEN c.payment_method = 'Unknown' THEN 'UNMAPPED_PAYMENT_METHOD' END,
        CASE WHEN c.amount_paid <= 0           THEN 'NON_POSITIVE_AMOUNT'     END
    ), ','), '') AS data_quality_flags,
    (c.payment_method = 'Unknown' OR c.amount_paid <= 0) AS is_dq_flagged,
    CURRENT_TIMESTAMP()
FROM cleaned c
JOIN SILVER.FX_RATES f ON f.CURRENCY_CODE = c.currency;


/* ---------------------------------------------------------------------------
   Load summary: Bronze rows in, Silver rows out, and the difference.
   Only INVOICES and PAYMENTS should shrink, by exactly 3 each.
   --------------------------------------------------------------------------- */
SELECT 'GL_ACCOUNTS' AS table_name,
       (SELECT COUNT(*) FROM BRONZE.GL_ACCOUNTS) AS bronze_rows,
       (SELECT COUNT(*) FROM SILVER.GL_ACCOUNTS) AS silver_rows,
       (SELECT COUNT(*) FROM BRONZE.GL_ACCOUNTS) - (SELECT COUNT(*) FROM SILVER.GL_ACCOUNTS) AS removed
UNION ALL SELECT 'VENDORS',
       (SELECT COUNT(*) FROM BRONZE.VENDORS), (SELECT COUNT(*) FROM SILVER.VENDORS),
       (SELECT COUNT(*) FROM BRONZE.VENDORS) - (SELECT COUNT(*) FROM SILVER.VENDORS)
UNION ALL SELECT 'VENDORS_UPDATE',
       (SELECT COUNT(*) FROM BRONZE.VENDORS_UPDATE), (SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE),
       (SELECT COUNT(*) FROM BRONZE.VENDORS_UPDATE) - (SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE)
UNION ALL SELECT 'PURCHASE_ORDERS',
       (SELECT COUNT(*) FROM BRONZE.PURCHASE_ORDERS), (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS),
       (SELECT COUNT(*) FROM BRONZE.PURCHASE_ORDERS) - (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS)
UNION ALL SELECT 'INVOICES',
       (SELECT COUNT(*) FROM BRONZE.INVOICES), (SELECT COUNT(*) FROM SILVER.INVOICES),
       (SELECT COUNT(*) FROM BRONZE.INVOICES) - (SELECT COUNT(*) FROM SILVER.INVOICES)
UNION ALL SELECT 'PAYMENTS',
       (SELECT COUNT(*) FROM BRONZE.PAYMENTS), (SELECT COUNT(*) FROM SILVER.PAYMENTS),
       (SELECT COUNT(*) FROM BRONZE.PAYMENTS) - (SELECT COUNT(*) FROM SILVER.PAYMENTS)
ORDER BY table_name;
