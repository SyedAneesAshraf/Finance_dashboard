/* ============================================================================
   PHASE 2 — BRONZE LOAD
   Loads the six staged CSVs into the Bronze tables.

   Prerequisite: the files must already be in the internal stage. Upload with
       .venv\Scripts\python.exe scripts\sf.py put "data_raw/*.csv" @BRONZE.RAW_FILES
   which PUTs each file and gzips it, so the staged names end in .csv.gz.

   Idempotency: each table is truncated first and every COPY uses FORCE = TRUE.
   Without FORCE, Snowflake's load-history metadata silently skips files it has
   already ingested, so a re-run after a TRUNCATE would leave the table empty --
   a genuinely confusing failure. Phase 8 requires the whole SQL pipeline to be
   re-runnable end to end, so every script here must be safe to execute twice.

   ON_ERROR = 'ABORT_STATEMENT' is deliberate. Bronze must be all-or-nothing:
   a partially loaded table is worse than a failed load, because the row-count
   check would pass on a later re-run while the data is quietly wrong.

   Each COPY selects from the stage rather than loading directly, because the
   METADATA$ pseudo-columns carrying load lineage are only available that way.
   Column selection is not transformation -- no value is altered.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    BRONZE;

/* ---------------------------------------------------------------------------
   Confirm what is actually staged before loading anything.
   --------------------------------------------------------------------------- */
LIST @BRONZE.RAW_FILES;

/* ---------------------------------------------------------------------------
   1. GL ACCOUNTS
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.GL_ACCOUNTS;

COPY INTO BRONZE.GL_ACCOUNTS
    (ACCOUNT_ID, ACCOUNT_NAME, COST_CENTER, DEPARTMENT,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/gl_accounts.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   2. VENDORS  (extract #1)
   The explicit filename matters: a pattern like 'vendors.*' would also match
   vendors_update.csv.gz and silently load both extracts into one table.
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.VENDORS;

COPY INTO BRONZE.VENDORS
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, CURRENCY,
     CREATION_DATE, LAST_UPDATE_DATE,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4, $5, $6, $7, $8,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/vendors.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   3. VENDORS_UPDATE  (extract #2)
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.VENDORS_UPDATE;

COPY INTO BRONZE.VENDORS_UPDATE
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, CURRENCY,
     CREATION_DATE, LAST_UPDATE_DATE,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4, $5, $6, $7, $8,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/vendors_update.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   4. PURCHASE ORDERS
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.PURCHASE_ORDERS;

COPY INTO BRONZE.PURCHASE_ORDERS
    (PO_ID, VENDOR_ID, PO_DATE, PO_AMOUNT, CURRENCY,
     GOODS_RECEIPT_DATE, PO_STATUS,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4, $5, $6, $7,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/purchase_orders.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   5. INVOICES
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.INVOICES;

COPY INTO BRONZE.INVOICES
    (INVOICE_ID, VENDOR_ID, PO_ID, GL_ACCOUNT_ID, INVOICE_DATE, DUE_DATE,
     AMOUNT, CURRENCY, STATUS,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4, $5, $6, $7, $8, $9,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/invoices.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   6. PAYMENTS
   --------------------------------------------------------------------------- */
TRUNCATE TABLE BRONZE.PAYMENTS;

COPY INTO BRONZE.PAYMENTS
    (PAYMENT_ID, INVOICE_ID, PAYMENT_DATE, AMOUNT_PAID, CURRENCY, PAYMENT_METHOD,
     _SOURCE_FILE, _FILE_ROW_NUMBER, _LOAD_TS)
FROM (
    SELECT $1, $2, $3, $4, $5, $6,
           METADATA$FILENAME, METADATA$FILE_ROW_NUMBER, CURRENT_TIMESTAMP()
    FROM @BRONZE.RAW_FILES/payments.csv.gz
         (FILE_FORMAT => 'BRONZE.CSV_STANDARD')
)
FORCE = TRUE
ON_ERROR = 'ABORT_STATEMENT';

/* ---------------------------------------------------------------------------
   Load summary
   --------------------------------------------------------------------------- */
SELECT 'GL_ACCOUNTS'     AS table_name, COUNT(*) AS rows_loaded FROM BRONZE.GL_ACCOUNTS
UNION ALL SELECT 'VENDORS',         COUNT(*) FROM BRONZE.VENDORS
UNION ALL SELECT 'VENDORS_UPDATE',  COUNT(*) FROM BRONZE.VENDORS_UPDATE
UNION ALL SELECT 'PURCHASE_ORDERS', COUNT(*) FROM BRONZE.PURCHASE_ORDERS
UNION ALL SELECT 'INVOICES',        COUNT(*) FROM BRONZE.INVOICES
UNION ALL SELECT 'PAYMENTS',        COUNT(*) FROM BRONZE.PAYMENTS
ORDER BY table_name;
