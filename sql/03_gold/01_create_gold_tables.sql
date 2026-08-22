/* ============================================================================
   PHASE 4 — GOLD LAYER DDL (star schema)

   Three facts, three dimensions.

   ---------------------------------------------------------------------------
   THE KEY DESIGN DECISION: facts carry BOTH a surrogate key and a natural key.
   ---------------------------------------------------------------------------
   DIM_VENDOR is SCD Type 2, so VENDOR_ID is no longer unique in it -- a vendor
   that changed terms has two rows. That breaks the obvious join, and there are
   two established ways to deal with it:

     (a) RANGE JOIN on the natural key
         JOIN DIM_VENDOR v ON f.VENDOR_ID = v.VENDOR_ID
                          AND f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE
                                                 AND v.EFFECTIVE_END_DATE
         This is the pattern the blueprint's query 5.6 demonstrates, and it is
         the clearest way to SHOW how SCD2 works.

     (b) SURROGATE KEY resolved at load time
         JOIN DIM_VENDOR v ON f.VENDOR_KEY = v.VENDOR_KEY
         The ETL binds each fact row to the dimension version that was in
         effect on its transaction date, once, at load.

   This model does both, because each is needed for a different reason:

     - (a) is kept because query 5.6 must demonstrate the technique, and because
       a natural key is what a human writing ad-hoc SQL will reach for.

     - (b) is REQUIRED for Power BI. Power BI relationships are equality-only --
       there is no way to express BETWEEN in the model. Relating on VENDOR_ID
       against a dimension with repeated values yields a many-to-many
       relationship, which is ambiguous and silently produces wrong numbers.
       VENDOR_KEY makes it a clean many-to-one.

   Phase 5 verifies the two approaches return identical results. If they ever
   diverge, the surrogate keys were resolved incorrectly at load.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ===========================================================================
   DIMENSION: DATE
   A smart key (YYYYMMDD) rather than a meaningless sequence -- it makes fact
   rows readable during debugging and sorts correctly without a join.
   FULL_DATE is kept alongside it because Power BI's "Mark as Date Table" and
   every DAX time-intelligence function need a real DATE column.
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.DIM_DATE (
    DATE_KEY          NUMBER(8)    NOT NULL,   -- 20260815
    FULL_DATE         DATE         NOT NULL,
    DAY_OF_MONTH      NUMBER(2)    NOT NULL,
    DAY_OF_WEEK       NUMBER(1)    NOT NULL,
    DAY_NAME          VARCHAR(10)  NOT NULL,
    IS_WEEKEND        BOOLEAN      NOT NULL,
    MONTH_NUMBER      NUMBER(2)    NOT NULL,
    MONTH_NAME        VARCHAR(10)  NOT NULL,
    MONTH_YEAR        VARCHAR(8)   NOT NULL,   -- 'Aug 2026', for chart axes
    QUARTER_NUMBER    NUMBER(1)    NOT NULL,
    QUARTER_NAME      VARCHAR(7)   NOT NULL,   -- '2026-Q3'
    YEAR_NUMBER       NUMBER(4)    NOT NULL,
    FISCAL_YEAR       NUMBER(4)    NOT NULL,
    FISCAL_QUARTER    NUMBER(1)    NOT NULL,
    FISCAL_PERIOD     NUMBER(2)    NOT NULL,   -- 1-12, April = 1
    CONSTRAINT PK_DIM_DATE PRIMARY KEY (DATE_KEY)
)
COMMENT = 'Calendar dimension. Fiscal year starts 1 April, so FY2026 = Apr 2025 - Mar 2026.';

/* ===========================================================================
   DIMENSION: GL ACCOUNT
   Type 1 (overwrite). Cost-centre reassignments are corrections rather than
   history worth preserving, and no business question asks what an account's
   cost centre used to be.
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.DIM_GL_ACCOUNT (
    ACCOUNT_KEY       NUMBER        AUTOINCREMENT START 1 INCREMENT 1,
    ACCOUNT_ID        VARCHAR(10)   NOT NULL,
    ACCOUNT_NAME      VARCHAR(100)  NOT NULL,
    COST_CENTER       VARCHAR(20)   NOT NULL,
    DEPARTMENT        VARCHAR(50)   NOT NULL,
    CONSTRAINT PK_DIM_GL_ACCOUNT PRIMARY KEY (ACCOUNT_KEY)
)
COMMENT = 'Expense account dimension, SCD Type 1.';

/* ===========================================================================
   DIMENSION: VENDOR  --  SCD TYPE 2
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.DIM_VENDOR (
    VENDOR_KEY            NUMBER        AUTOINCREMENT START 1 INCREMENT 1,
    VENDOR_ID             VARCHAR(10)   NOT NULL,   -- business key, repeats across versions
    VENDOR_NAME           VARCHAR(200)  NOT NULL,
    CATEGORY              VARCHAR(50)   NOT NULL,
    REGION                VARCHAR(50)   NOT NULL,
    PAYMENT_TERMS         VARCHAR(20)   NOT NULL,
    PAYMENT_TERMS_DAYS    NUMBER(3),
    CURRENCY              VARCHAR(3)    NOT NULL,
    -- SCD Type 2 control columns
    EFFECTIVE_START_DATE  DATE          NOT NULL,
    EFFECTIVE_END_DATE    DATE          NOT NULL,   -- 9999-12-31 while open
    IS_CURRENT            BOOLEAN       NOT NULL,
    VERSION_NUMBER        NUMBER(3)     NOT NULL,
    -- provenance
    SOURCE_CREATION_DATE  DATE,
    SOURCE_LAST_UPDATE    DATE,
    DATA_QUALITY_FLAGS    VARCHAR(300),
    _GOLD_LOAD_TS         TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_DIM_VENDOR PRIMARY KEY (VENDOR_KEY)
)
COMMENT = 'Vendor dimension, SCD Type 2. One row per vendor per version. EFFECTIVE_END_DATE 9999-12-31 = open.';

/*
   Why EFFECTIVE_END_DATE is 9999-12-31 rather than NULL on the open row:
   the range join in query 5.6 is written as
       f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
   and BETWEEN against a NULL upper bound evaluates to UNKNOWN, so every current
   vendor would silently vanish from the result. A sentinel keeps the predicate
   total. The cost is that 9999-12-31 shows up in the data, which is why the
   IS_CURRENT flag exists as the readable way to filter.
*/

/* ===========================================================================
   FACT: PURCHASE ORDER
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.FACT_PURCHASE_ORDER (
    PO_ID                    VARCHAR(20)   NOT NULL,
    VENDOR_KEY               NUMBER        NOT NULL,
    VENDOR_ID                VARCHAR(10)   NOT NULL,
    PO_DATE_KEY              NUMBER(8)     NOT NULL,
    PO_DATE                  DATE          NOT NULL,
    GOODS_RECEIPT_DATE_KEY   NUMBER(8),
    GOODS_RECEIPT_DATE       DATE,
    PO_AMOUNT                NUMBER(15,2)  NOT NULL,
    CURRENCY                 VARCHAR(3)    NOT NULL,
    PO_AMOUNT_USD            NUMBER(15,2)  NOT NULL,
    PO_STATUS                VARCHAR(30)   NOT NULL,
    IS_GOODS_RECEIVED        BOOLEAN       NOT NULL,
    _GOLD_LOAD_TS            TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_FACT_PO      PRIMARY KEY (PO_ID),
    CONSTRAINT FK_FPO_VENDOR   FOREIGN KEY (VENDOR_KEY)  REFERENCES GOLD.DIM_VENDOR (VENDOR_KEY),
    CONSTRAINT FK_FPO_DATE     FOREIGN KEY (PO_DATE_KEY) REFERENCES GOLD.DIM_DATE (DATE_KEY)
)
COMMENT = 'Procurement fact. The PO side of the 3-way match.';

/* ===========================================================================
   FACT: INVOICE
   PO_ID is a degenerate dimension, deliberately nullable: NULL is non-PO spend.
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.FACT_INVOICE (
    INVOICE_ID          VARCHAR(20)   NOT NULL,
    VENDOR_KEY          NUMBER        NOT NULL,
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    ACCOUNT_KEY         NUMBER        NOT NULL,
    GL_ACCOUNT_ID       VARCHAR(10)   NOT NULL,
    PO_ID               VARCHAR(20),
    INVOICE_DATE_KEY    NUMBER(8)     NOT NULL,
    INVOICE_DATE        DATE          NOT NULL,
    DUE_DATE_KEY        NUMBER(8)     NOT NULL,
    DUE_DATE            DATE          NOT NULL,
    AMOUNT              NUMBER(15,2)  NOT NULL,
    CURRENCY            VARCHAR(3)    NOT NULL,
    AMOUNT_USD          NUMBER(15,2)  NOT NULL,
    STATUS              VARCHAR(20)   NOT NULL,
    IS_NON_PO_SPEND     BOOLEAN       NOT NULL,
    TERM_DAYS           NUMBER(4)     NOT NULL,   -- DUE_DATE - INVOICE_DATE, as issued
    _GOLD_LOAD_TS       TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_FACT_INVOICE  PRIMARY KEY (INVOICE_ID),
    CONSTRAINT FK_FI_VENDOR     FOREIGN KEY (VENDOR_KEY)       REFERENCES GOLD.DIM_VENDOR (VENDOR_KEY),
    CONSTRAINT FK_FI_ACCOUNT    FOREIGN KEY (ACCOUNT_KEY)      REFERENCES GOLD.DIM_GL_ACCOUNT (ACCOUNT_KEY),
    CONSTRAINT FK_FI_INV_DATE   FOREIGN KEY (INVOICE_DATE_KEY) REFERENCES GOLD.DIM_DATE (DATE_KEY),
    CONSTRAINT FK_FI_DUE_DATE   FOREIGN KEY (DUE_DATE_KEY)     REFERENCES GOLD.DIM_DATE (DATE_KEY)
)
COMMENT = 'Central AP fact. Retains near-duplicate invoices for fraud detection.';

/* ===========================================================================
   FACT: PAYMENT

   DAYS_TO_PAY and DAYS_LATE are stored rather than computed at query time.
   Both are functions only of dates already on the row, so they never go stale
   -- unlike days-overdue on an unpaid invoice, which depends on CURRENT_DATE
   and is therefore deliberately NOT stored anywhere.

   Storing them also guarantees SQL and DAX agree on DPO: both read one column
   instead of each re-implementing the same DATEDIFF.
   =========================================================================== */
CREATE OR REPLACE TABLE GOLD.FACT_PAYMENT (
    PAYMENT_ID          VARCHAR(20)   NOT NULL,
    INVOICE_ID          VARCHAR(20)   NOT NULL,
    VENDOR_KEY          NUMBER        NOT NULL,
    VENDOR_ID           VARCHAR(10)   NOT NULL,
    PAYMENT_DATE_KEY    NUMBER(8)     NOT NULL,
    PAYMENT_DATE        DATE          NOT NULL,
    AMOUNT_PAID         NUMBER(15,2)  NOT NULL,
    CURRENCY            VARCHAR(3)    NOT NULL,
    AMOUNT_PAID_USD     NUMBER(15,2)  NOT NULL,
    PAYMENT_METHOD      VARCHAR(30)   NOT NULL,
    DAYS_TO_PAY         NUMBER(5)     NOT NULL,   -- payment_date - invoice_date
    DAYS_LATE           NUMBER(5)     NOT NULL,   -- payment_date - due_date, floored at 0
    IS_LATE             BOOLEAN       NOT NULL,
    _GOLD_LOAD_TS       TIMESTAMP_NTZ NOT NULL,
    CONSTRAINT PK_FACT_PAYMENT  PRIMARY KEY (PAYMENT_ID),
    CONSTRAINT FK_FP_INVOICE    FOREIGN KEY (INVOICE_ID)       REFERENCES GOLD.FACT_INVOICE (INVOICE_ID),
    CONSTRAINT FK_FP_VENDOR     FOREIGN KEY (VENDOR_KEY)       REFERENCES GOLD.DIM_VENDOR (VENDOR_KEY),
    CONSTRAINT FK_FP_DATE       FOREIGN KEY (PAYMENT_DATE_KEY) REFERENCES GOLD.DIM_DATE (DATE_KEY)
)
COMMENT = 'Payment fact. Carries its own vendor and date keys so it can be sliced without traversing FACT_INVOICE.';

/* --------------------------------------------------------------------------- */
SELECT table_name, row_count
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.TABLES
WHERE  table_schema = 'GOLD'
ORDER  BY table_name;
