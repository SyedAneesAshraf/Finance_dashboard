/* ============================================================================
   PHASE 2 — BRONZE LAYER DDL
   Six tables mirroring the six source extracts exactly.

   Two rules govern this schema:

     1. EVERY business column is VARCHAR, unsized.
        Not laziness — deliberate. A date written 30-SEP-2024 and one written
        2024-09-30 are both valid raw values; typing them as DATE here would
        force a parse decision at load time and lose whatever failed to parse.
        Bronze's job is to accept the source verbatim so that the raw feed is
        always reproducible. Typing belongs in Silver, where a failed cast is a
        finding rather than silent data loss.

     2. No transformation. No CASE, no TRIM, no COALESCE, no dedup.
        If a value arrived with trailing spaces or in the wrong case, it is
        stored with trailing spaces in the wrong case.

   The three underscore-prefixed columns are load lineage, not transformation.
   They record where a row came from and when, which is what makes it possible
   to answer "why does this number look wrong" months later, and to prove the
   exact-duplicate rows are genuinely two rows in the file rather than a bug in
   the loader.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    BRONZE;

/* ---------------------------------------------------------------------------
   1. GL ACCOUNTS  <- gl_accounts.csv
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.GL_ACCOUNTS (
    ACCOUNT_ID        VARCHAR,
    ACCOUNT_NAME      VARCHAR,
    COST_CENTER       VARCHAR,
    DEPARTMENT        VARCHAR,
    _SOURCE_FILE      VARCHAR,
    _FILE_ROW_NUMBER  NUMBER,
    _LOAD_TS          TIMESTAMP_NTZ
)
COMMENT = 'Raw GL account master. Loaded verbatim from gl_accounts.csv.';

/* ---------------------------------------------------------------------------
   2. VENDORS  <- vendors.csv          (vendor master, extract #1, 2024-09-01)
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.VENDORS (
    VENDOR_ID         VARCHAR,
    VENDOR_NAME       VARCHAR,
    CATEGORY          VARCHAR,
    REGION            VARCHAR,
    PAYMENT_TERMS     VARCHAR,
    CURRENCY          VARCHAR,
    CREATION_DATE     VARCHAR,
    LAST_UPDATE_DATE  VARCHAR,
    _SOURCE_FILE      VARCHAR,
    _FILE_ROW_NUMBER  NUMBER,
    _LOAD_TS          TIMESTAMP_NTZ
)
COMMENT = 'Raw vendor master, first extract. CREATION_DATE/LAST_UPDATE_DATE are Oracle EBS WHO columns.';

/* ---------------------------------------------------------------------------
   3. VENDORS_UPDATE  <- vendors_update.csv   (extract #2, 2026-08-15)
   Same table in the source system, extracted 24 months later. Phase 4's SCD
   Type 2 MERGE compares this against DIM_VENDOR to detect what changed.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.VENDORS_UPDATE (
    VENDOR_ID         VARCHAR,
    VENDOR_NAME       VARCHAR,
    CATEGORY          VARCHAR,
    REGION            VARCHAR,
    PAYMENT_TERMS     VARCHAR,
    CURRENCY          VARCHAR,
    CREATION_DATE     VARCHAR,
    LAST_UPDATE_DATE  VARCHAR,
    _SOURCE_FILE      VARCHAR,
    _FILE_ROW_NUMBER  NUMBER,
    _LOAD_TS          TIMESTAMP_NTZ
)
COMMENT = 'Raw vendor master, second extract. Source for the Phase 4 SCD Type 2 MERGE.';

/* ---------------------------------------------------------------------------
   4. PURCHASE ORDERS  <- purchase_orders.csv
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.PURCHASE_ORDERS (
    PO_ID               VARCHAR,
    VENDOR_ID           VARCHAR,
    PO_DATE             VARCHAR,
    PO_AMOUNT           VARCHAR,
    CURRENCY            VARCHAR,
    GOODS_RECEIPT_DATE  VARCHAR,
    PO_STATUS           VARCHAR,
    _SOURCE_FILE        VARCHAR,
    _FILE_ROW_NUMBER    NUMBER,
    _LOAD_TS            TIMESTAMP_NTZ
)
COMMENT = 'Raw purchase orders. PO_AMOUNT stays VARCHAR here; numeric casting is a Silver concern.';

/* ---------------------------------------------------------------------------
   5. INVOICES  <- invoices.csv
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.INVOICES (
    INVOICE_ID        VARCHAR,
    VENDOR_ID         VARCHAR,
    PO_ID             VARCHAR,
    GL_ACCOUNT_ID     VARCHAR,
    INVOICE_DATE      VARCHAR,
    DUE_DATE          VARCHAR,
    AMOUNT            VARCHAR,
    CURRENCY          VARCHAR,
    STATUS            VARCHAR,
    _SOURCE_FILE      VARCHAR,
    _FILE_ROW_NUMBER  NUMBER,
    _LOAD_TS          TIMESTAMP_NTZ
)
COMMENT = 'Raw AP invoices. A null PO_ID is non-PO spend, not a defect. Contains deliberate duplicates.';

/* ---------------------------------------------------------------------------
   6. PAYMENTS  <- payments.csv
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE BRONZE.PAYMENTS (
    PAYMENT_ID        VARCHAR,
    INVOICE_ID        VARCHAR,
    PAYMENT_DATE      VARCHAR,
    AMOUNT_PAID       VARCHAR,
    CURRENCY          VARCHAR,
    PAYMENT_METHOD    VARCHAR,
    _SOURCE_FILE      VARCHAR,
    _FILE_ROW_NUMBER  NUMBER,
    _LOAD_TS          TIMESTAMP_NTZ
)
COMMENT = 'Raw payment transactions. One payment per invoice.';

/* ---------------------------------------------------------------------------
   Confirm all six exist and are empty.
   --------------------------------------------------------------------------- */
SELECT table_name, row_count
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.TABLES
WHERE  table_schema = 'BRONZE'
ORDER  BY table_name;
