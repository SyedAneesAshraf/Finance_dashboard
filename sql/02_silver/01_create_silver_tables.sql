/* ============================================================================
   PHASE 3 — SILVER LAYER DDL

   Where Bronze is text and verbatim, Silver is typed and trustworthy. Three
   things change:

     1. REAL TYPES. Dates are DATE, amounts are NUMBER(15,2), text columns are
        sized. A failed cast here is a finding, not silent data loss, because
        Bronze still holds the original string.

     2. AN AUDIT TRAIL. Every table carries DATA_QUALITY_FLAGS and
        IS_DQ_FLAGGED. Missing values are imputed to 'Unknown' AND flagged --
        imputing alone destroys the evidence a field was ever missing, while
        flagging alone leaves NULLs that break GROUP BY and Power BI slicers.

     3. A REPORTING CURRENCY. AMOUNT_USD sits beside the entered amount, so
        spend can be summed across vendors billing in four currencies without
        adding unlike units. Both are kept: Oracle EBS's entered-vs-functional
        currency distinction, which auditors care about.

   Constraints are declared but NOT enforced by Snowflake (only NOT NULL is).
   They are here because they document intent and because Power BI reads them
   when inferring relationships. Since they are advisory, integrity is tested
   explicitly in 03_verify_silver.sql rather than assumed.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    SILVER;

/* ---------------------------------------------------------------------------
   0. FX REFERENCE
   Static snapshot rates, not a time series. A rate history would be a second
   slowly-changing-dimension problem and answers none of the five business
   questions. These values MUST match FX_TO_USD in scripts/generate_data.py --
   if they drift, every spend figure downstream silently drifts with them.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.FX_RATES (
    CURRENCY_CODE  VARCHAR(3)     NOT NULL,
    RATE_TO_USD    NUMBER(12,6)   NOT NULL,
    RATE_BASIS     VARCHAR(50),
    CONSTRAINT PK_FX_RATES PRIMARY KEY (CURRENCY_CODE)
)
COMMENT = 'Static FX rates to USD, the reporting currency. Mirrors FX_TO_USD in the generator.';

INSERT INTO SILVER.FX_RATES (CURRENCY_CODE, RATE_TO_USD, RATE_BASIS) VALUES
    ('USD', 1.000000, 'reporting currency'),
    ('EUR', 1.080000, 'period-average snapshot'),
    ('GBP', 1.270000, 'period-average snapshot'),
    ('CAD', 0.740000, 'period-average snapshot');

/* ---------------------------------------------------------------------------
   1. GL ACCOUNTS
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.GL_ACCOUNTS (
    ACCOUNT_ID          VARCHAR(10)   NOT NULL,
    ACCOUNT_NAME        VARCHAR(100)  NOT NULL,
    COST_CENTER         VARCHAR(20)   NOT NULL,
    DEPARTMENT          VARCHAR(50)   NOT NULL,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_GL_ACCOUNTS PRIMARY KEY (ACCOUNT_ID)
)
COMMENT = 'Cleaned GL account master.';

/* ---------------------------------------------------------------------------
   2. VENDORS  (extract #1)
   PAYMENT_TERMS_DAYS is a decode of the terms code, not a new fact: NET30 -> 30.
   Standardizing a coded value is Silver's job, and it saves every downstream
   consumer from re-implementing the same mapping.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.VENDORS (
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    VENDOR_NAME         VARCHAR(200)  NOT NULL,
    CATEGORY            VARCHAR(50)   NOT NULL,
    REGION              VARCHAR(50)   NOT NULL,
    PAYMENT_TERMS       VARCHAR(20)   NOT NULL,
    PAYMENT_TERMS_DAYS  NUMBER(3),
    CURRENCY            VARCHAR(3)    NOT NULL,
    CREATION_DATE       DATE,
    LAST_UPDATE_DATE    DATE,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_VENDORS PRIMARY KEY (VENDOR_ID)
)
COMMENT = 'Cleaned vendor master, first extract (2024-09-01).';

/* ---------------------------------------------------------------------------
   3. VENDORS_UPDATE  (extract #2)
   Identical shape to SILVER.VENDORS by design -- the Phase 4 SCD Type 2 MERGE
   compares the two attribute-by-attribute, and differing shapes would make that
   comparison fragile.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.VENDORS_UPDATE (
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    VENDOR_NAME         VARCHAR(200)  NOT NULL,
    CATEGORY            VARCHAR(50)   NOT NULL,
    REGION              VARCHAR(50)   NOT NULL,
    PAYMENT_TERMS       VARCHAR(20)   NOT NULL,
    PAYMENT_TERMS_DAYS  NUMBER(3),
    CURRENCY            VARCHAR(3)    NOT NULL,
    CREATION_DATE       DATE,
    LAST_UPDATE_DATE    DATE,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_VENDORS_UPDATE PRIMARY KEY (VENDOR_ID)
)
COMMENT = 'Cleaned vendor master, second extract (2026-08-15). Source for the SCD2 MERGE.';

/* ---------------------------------------------------------------------------
   4. PURCHASE ORDERS
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.PURCHASE_ORDERS (
    PO_ID               VARCHAR(20)   NOT NULL,
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    PO_DATE             DATE          NOT NULL,
    PO_AMOUNT           NUMBER(15,2)  NOT NULL,
    CURRENCY            VARCHAR(3)    NOT NULL,
    PO_AMOUNT_USD       NUMBER(15,2)  NOT NULL,
    GOODS_RECEIPT_DATE  DATE,
    PO_STATUS           VARCHAR(30)   NOT NULL,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_PO       PRIMARY KEY (PO_ID),
    CONSTRAINT FK_SILVER_PO_VENDOR FOREIGN KEY (VENDOR_ID) REFERENCES SILVER.VENDORS (VENDOR_ID)
)
COMMENT = 'Cleaned purchase orders. GOODS_RECEIPT_DATE null = ordered but not yet received.';

/* ---------------------------------------------------------------------------
   5. INVOICES
   PO_ID stays nullable on purpose: a null is non-PO spend, a business finding
   rather than a defect, and forcing it to a placeholder would erase the very
   thing business question 5 asks about.
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.INVOICES (
    INVOICE_ID          VARCHAR(20)   NOT NULL,
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    PO_ID               VARCHAR(20),
    GL_ACCOUNT_ID       VARCHAR(10)   NOT NULL,
    INVOICE_DATE        DATE          NOT NULL,
    DUE_DATE            DATE          NOT NULL,
    AMOUNT              NUMBER(15,2)  NOT NULL,
    CURRENCY            VARCHAR(3)    NOT NULL,
    AMOUNT_USD          NUMBER(15,2)  NOT NULL,
    STATUS              VARCHAR(20)   NOT NULL,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_INVOICES        PRIMARY KEY (INVOICE_ID),
    CONSTRAINT FK_SILVER_INV_VENDOR      FOREIGN KEY (VENDOR_ID)     REFERENCES SILVER.VENDORS (VENDOR_ID),
    CONSTRAINT FK_SILVER_INV_PO          FOREIGN KEY (PO_ID)         REFERENCES SILVER.PURCHASE_ORDERS (PO_ID),
    CONSTRAINT FK_SILVER_INV_GL          FOREIGN KEY (GL_ACCOUNT_ID) REFERENCES SILVER.GL_ACCOUNTS (ACCOUNT_ID)
)
COMMENT = 'Cleaned AP invoices. Retains deliberate near-duplicates for query 5.4 to detect.';

/* ---------------------------------------------------------------------------
   6. PAYMENTS
   --------------------------------------------------------------------------- */
CREATE OR REPLACE TABLE SILVER.PAYMENTS (
    PAYMENT_ID          VARCHAR(20)   NOT NULL,
    INVOICE_ID          VARCHAR(20)   NOT NULL,
    PAYMENT_DATE        DATE          NOT NULL,
    AMOUNT_PAID         NUMBER(15,2)  NOT NULL,
    CURRENCY            VARCHAR(3)    NOT NULL,
    AMOUNT_PAID_USD     NUMBER(15,2)  NOT NULL,
    PAYMENT_METHOD      VARCHAR(30)   NOT NULL,
    DATA_QUALITY_FLAGS  VARCHAR(300),
    IS_DQ_FLAGGED       BOOLEAN       NOT NULL,
    _SILVER_LOAD_TS     TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_SILVER_PAYMENTS      PRIMARY KEY (PAYMENT_ID),
    CONSTRAINT FK_SILVER_PAY_INVOICE   FOREIGN KEY (INVOICE_ID) REFERENCES SILVER.INVOICES (INVOICE_ID)
)
COMMENT = 'Cleaned payments. One payment per invoice.';

/* --------------------------------------------------------------------------- */
SELECT table_name, row_count
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.TABLES
WHERE  table_schema = 'SILVER'
ORDER  BY table_name;
