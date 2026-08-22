/* ============================================================================
   PHASE 4 — DIMENSION LOADS

   Loads DIM_DATE, DIM_GL_ACCOUNT, and the INITIAL version of DIM_VENDOR from
   the first vendor extract. The second extract is applied separately in
   03_scd2_merge_dim_vendor.sql, because the whole point of SCD Type 2 is that
   it is an incremental process -- collapsing both extracts into one load would
   produce the right rows for the wrong reason and prove nothing.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ===========================================================================
   1. DIM_DATE  --  2024-01-01 through 2027-12-31 (1,461 days)

   The range is padded well beyond the fact data (2024-09-01 to 2026-08-15) on
   both sides. A date dimension that merely covers the facts breaks the moment
   a DAX measure looks one year back from the earliest month, and
   SAMEPERIODLASTYEAR would return blank rather than an honest zero.

   Fiscal year starts 1 April: FY2026 = Apr 2025 - Mar 2026, FISCAL_PERIOD 1 = April.
   =========================================================================== */
TRUNCATE TABLE GOLD.DIM_DATE;

INSERT INTO GOLD.DIM_DATE
    (DATE_KEY, FULL_DATE, DAY_OF_MONTH, DAY_OF_WEEK, DAY_NAME, IS_WEEKEND,
     MONTH_NUMBER, MONTH_NAME, MONTH_YEAR, QUARTER_NUMBER, QUARTER_NAME,
     YEAR_NUMBER, FISCAL_YEAR, FISCAL_QUARTER, FISCAL_PERIOD)
WITH spine AS (
    SELECT DATEADD('day', SEQ4(), '2024-01-01'::DATE) AS d
    FROM   TABLE(GENERATOR(ROWCOUNT => 1461))
),
enriched AS (
    SELECT
        d,
        -- April = fiscal period 1, so months 4..12 map to 1..9 and 1..3 map to 10..12
        CASE WHEN MONTH(d) >= 4 THEN MONTH(d) - 3 ELSE MONTH(d) + 9 END AS fiscal_period
    FROM spine
)
SELECT
    TO_NUMBER(TO_CHAR(d, 'YYYYMMDD'))              AS date_key,
    d                                              AS full_date,
    DAY(d)                                         AS day_of_month,
    DAYOFWEEK(d)                                   AS day_of_week,
    DAYNAME(d)                                     AS day_name,
    DAYOFWEEK(d) IN (0, 6)                         AS is_weekend,
    MONTH(d)                                       AS month_number,
    MONTHNAME(d)                                   AS month_name,
    MONTHNAME(d) || ' ' || YEAR(d)                 AS month_year,
    QUARTER(d)                                     AS quarter_number,
    YEAR(d) || '-Q' || QUARTER(d)                  AS quarter_name,
    YEAR(d)                                        AS year_number,
    CASE WHEN MONTH(d) >= 4 THEN YEAR(d) + 1 ELSE YEAR(d) END AS fiscal_year,
    CEIL(fiscal_period / 3.0)                      AS fiscal_quarter,
    fiscal_period
FROM enriched;

/* ===========================================================================
   2. DIM_GL_ACCOUNT  --  straight Type 1 copy from Silver
   =========================================================================== */
TRUNCATE TABLE GOLD.DIM_GL_ACCOUNT;

INSERT INTO GOLD.DIM_GL_ACCOUNT (ACCOUNT_ID, ACCOUNT_NAME, COST_CENTER, DEPARTMENT)
SELECT ACCOUNT_ID, ACCOUNT_NAME, COST_CENTER, DEPARTMENT
FROM   SILVER.GL_ACCOUNTS
ORDER  BY ACCOUNT_ID;

/* ===========================================================================
   3. DIM_VENDOR  --  initial load (version 1 of every vendor)

   EFFECTIVE_START_DATE is the vendor's CREATION_DATE, not the extract date.

   That choice is load-bearing. Query 5.6 joins with
       invoice_date BETWEEN effective_start_date AND effective_end_date
   so if version 1 started on the extract date (2024-09-01), any invoice issued
   before it would match no row at all and silently disappear from the report.
   Anchoring to creation date means the first version covers the vendor's entire
   history up to its first change.
   =========================================================================== */
TRUNCATE TABLE GOLD.DIM_VENDOR;

INSERT INTO GOLD.DIM_VENDOR
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, PAYMENT_TERMS_DAYS,
     CURRENCY, EFFECTIVE_START_DATE, EFFECTIVE_END_DATE, IS_CURRENT, VERSION_NUMBER,
     SOURCE_CREATION_DATE, SOURCE_LAST_UPDATE, DATA_QUALITY_FLAGS, _GOLD_LOAD_TS)
SELECT
    VENDOR_ID,
    VENDOR_NAME,
    CATEGORY,
    REGION,
    PAYMENT_TERMS,
    PAYMENT_TERMS_DAYS,
    CURRENCY,
    CREATION_DATE          AS effective_start_date,
    '9999-12-31'::DATE     AS effective_end_date,
    TRUE                   AS is_current,
    1                      AS version_number,
    CREATION_DATE,
    LAST_UPDATE_DATE,
    DATA_QUALITY_FLAGS,
    CURRENT_TIMESTAMP()
FROM SILVER.VENDORS
ORDER BY VENDOR_ID;

/* ---------------------------------------------------------------------------
   State after the initial load: 50 vendors, 50 rows, all current, all version 1.
   DIM_VENDOR does not yet prove anything about SCD2 -- that is the next script.
   --------------------------------------------------------------------------- */
SELECT
    COUNT(*)                        AS total_rows,
    COUNT(DISTINCT VENDOR_ID)       AS distinct_vendors,
    COUNT_IF(IS_CURRENT)            AS current_rows,
    MAX(VERSION_NUMBER)             AS max_version
FROM GOLD.DIM_VENDOR;

SELECT
    COUNT(*)        AS date_rows,
    MIN(FULL_DATE)  AS first_date,
    MAX(FULL_DATE)  AS last_date,
    COUNT(DISTINCT FULL_DATE) AS distinct_dates
FROM GOLD.DIM_DATE;

SELECT COUNT(*) AS gl_accounts FROM GOLD.DIM_GL_ACCOUNT;
