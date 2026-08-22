/* ============================================================================
   PHASE 4 — FACT LOADS

   ---------------------------------------------------------------------------
   THE ONE THING THAT MATTERS HERE: LEFT JOIN, NOT INNER JOIN
   ---------------------------------------------------------------------------
   Every dimension lookup below is a LEFT JOIN into a column declared NOT NULL.

   That combination is deliberate. The natural way to write these loads is an
   INNER JOIN -- but if a single invoice's date fell outside every version range
   of its vendor, an INNER JOIN would quietly DROP that invoice. The load would
   report success, the fact table would be short by one row, and the missing
   money would never surface. Silent row loss in a fact load is the worst class
   of data bug precisely because nothing looks wrong.

   A LEFT JOIN produces NULL for a miss, which violates NOT NULL and aborts the
   INSERT with an explicit error naming the column. The failure becomes loud
   instead of invisible. The row-count reconciliation at the end of this script
   is the second line of defence.

   ---------------------------------------------------------------------------
   HOW VENDOR_KEY IS RESOLVED
   ---------------------------------------------------------------------------
   Each fact binds to the vendor version that was in effect ON ITS OWN
   TRANSACTION DATE, via the range join. This is where the historical accuracy
   actually gets baked in: after this load, joining FACT_INVOICE to DIM_VENDOR on
   VENDOR_KEY alone gives the terms that applied when the invoice was issued --
   no date predicate required, which is what makes the model usable in Power BI.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ---------------------------------------------------------------------------
   PRE-FLIGHT: does DIM_DATE actually span the facts?
   A date key with no matching dimension row breaks time intelligence in ways
   that look like missing data rather than a modelling error. Expect 0 / 0.
   --------------------------------------------------------------------------- */
SELECT
    (SELECT COUNT(*) FROM SILVER.INVOICES i
      WHERE NOT EXISTS (SELECT 1 FROM GOLD.DIM_DATE d WHERE d.FULL_DATE = i.INVOICE_DATE)
         OR NOT EXISTS (SELECT 1 FROM GOLD.DIM_DATE d WHERE d.FULL_DATE = i.DUE_DATE)
    ) AS invoice_dates_outside_dim_date,
    (SELECT COUNT(*) FROM SILVER.PAYMENTS p
      WHERE NOT EXISTS (SELECT 1 FROM GOLD.DIM_DATE d WHERE d.FULL_DATE = p.PAYMENT_DATE)
    ) AS payment_dates_outside_dim_date;

/* Would any transaction fall outside every version range of its vendor?
   Expect 0 / 0. If not, the fact loads below will abort on NOT NULL. */
SELECT
    (SELECT COUNT(*) FROM SILVER.INVOICES i
      WHERE NOT EXISTS (
        SELECT 1 FROM GOLD.DIM_VENDOR v
        WHERE v.VENDOR_ID = i.VENDOR_ID
          AND i.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE)
    ) AS invoices_with_no_vendor_version,
    (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS p
      WHERE NOT EXISTS (
        SELECT 1 FROM GOLD.DIM_VENDOR v
        WHERE v.VENDOR_ID = p.VENDOR_ID
          AND p.PO_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE)
    ) AS pos_with_no_vendor_version;


/* ===========================================================================
   1. FACT_PURCHASE_ORDER
   =========================================================================== */
TRUNCATE TABLE GOLD.FACT_PURCHASE_ORDER;

INSERT INTO GOLD.FACT_PURCHASE_ORDER
    (PO_ID, VENDOR_KEY, VENDOR_ID, PO_DATE_KEY, PO_DATE,
     GOODS_RECEIPT_DATE_KEY, GOODS_RECEIPT_DATE,
     PO_AMOUNT, CURRENCY, PO_AMOUNT_USD, PO_STATUS, IS_GOODS_RECEIVED, _GOLD_LOAD_TS)
SELECT
    p.PO_ID,
    v.VENDOR_KEY,
    p.VENDOR_ID,
    TO_NUMBER(TO_CHAR(p.PO_DATE, 'YYYYMMDD')),
    p.PO_DATE,
    TO_NUMBER(TO_CHAR(p.GOODS_RECEIPT_DATE, 'YYYYMMDD')),   -- NULL-safe: NULL in, NULL out
    p.GOODS_RECEIPT_DATE,
    p.PO_AMOUNT,
    p.CURRENCY,
    p.PO_AMOUNT_USD,
    p.PO_STATUS,
    p.GOODS_RECEIPT_DATE IS NOT NULL,
    CURRENT_TIMESTAMP()
FROM      SILVER.PURCHASE_ORDERS p
LEFT JOIN GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = p.VENDOR_ID
      AND p.PO_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE;


/* ===========================================================================
   2. FACT_INVOICE

   TERM_DAYS is the gap between invoice and due date AS ISSUED. It is stored
   rather than read from the vendor dimension because the two can legitimately
   disagree: the invoice records what was actually granted, the dimension
   records the standing agreement. Keeping both makes that gap visible instead
   of assuming it away.
   =========================================================================== */
TRUNCATE TABLE GOLD.FACT_INVOICE;

INSERT INTO GOLD.FACT_INVOICE
    (INVOICE_ID, VENDOR_KEY, VENDOR_ID, ACCOUNT_KEY, GL_ACCOUNT_ID, PO_ID,
     INVOICE_DATE_KEY, INVOICE_DATE, DUE_DATE_KEY, DUE_DATE,
     AMOUNT, CURRENCY, AMOUNT_USD, STATUS, IS_NON_PO_SPEND, TERM_DAYS, _GOLD_LOAD_TS)
SELECT
    i.INVOICE_ID,
    v.VENDOR_KEY,
    i.VENDOR_ID,
    g.ACCOUNT_KEY,
    i.GL_ACCOUNT_ID,
    i.PO_ID,                                        -- stays NULL for non-PO spend
    TO_NUMBER(TO_CHAR(i.INVOICE_DATE, 'YYYYMMDD')),
    i.INVOICE_DATE,
    TO_NUMBER(TO_CHAR(i.DUE_DATE, 'YYYYMMDD')),
    i.DUE_DATE,
    i.AMOUNT,
    i.CURRENCY,
    i.AMOUNT_USD,
    i.STATUS,
    i.PO_ID IS NULL,
    DATEDIFF('day', i.INVOICE_DATE, i.DUE_DATE),
    CURRENT_TIMESTAMP()
FROM      SILVER.INVOICES i
LEFT JOIN GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = i.VENDOR_ID
      AND i.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
LEFT JOIN GOLD.DIM_GL_ACCOUNT g
       ON g.ACCOUNT_ID = i.GL_ACCOUNT_ID;


/* ===========================================================================
   3. FACT_PAYMENT

   VENDOR_KEY is inherited from the invoice rather than re-resolved at the
   payment date. A payment settles a specific invoice, so it belongs to the
   vendor version that invoice belongs to. Re-resolving at the payment date
   would put an invoice and its own payment under two different vendor versions
   whenever a term change fell between the two dates -- and vendor-level totals
   would then disagree between the two facts.
   =========================================================================== */
TRUNCATE TABLE GOLD.FACT_PAYMENT;

INSERT INTO GOLD.FACT_PAYMENT
    (PAYMENT_ID, INVOICE_ID, VENDOR_KEY, VENDOR_ID, PAYMENT_DATE_KEY, PAYMENT_DATE,
     AMOUNT_PAID, CURRENCY, AMOUNT_PAID_USD, PAYMENT_METHOD,
     DAYS_TO_PAY, DAYS_LATE, IS_LATE, _GOLD_LOAD_TS)
SELECT
    p.PAYMENT_ID,
    p.INVOICE_ID,
    f.VENDOR_KEY,
    f.VENDOR_ID,
    TO_NUMBER(TO_CHAR(p.PAYMENT_DATE, 'YYYYMMDD')),
    p.PAYMENT_DATE,
    p.AMOUNT_PAID,
    p.CURRENCY,
    p.AMOUNT_PAID_USD,
    p.PAYMENT_METHOD,
    DATEDIFF('day', f.INVOICE_DATE, p.PAYMENT_DATE)              AS days_to_pay,
    GREATEST(0, DATEDIFF('day', f.DUE_DATE, p.PAYMENT_DATE))     AS days_late,
    p.PAYMENT_DATE > f.DUE_DATE                                  AS is_late,
    CURRENT_TIMESTAMP()
FROM      SILVER.PAYMENTS p
LEFT JOIN GOLD.FACT_INVOICE f ON f.INVOICE_ID = p.INVOICE_ID;


/* ===========================================================================
   RECONCILIATION -- Silver in, Gold out. Every row must survive.
   Expect removed = 0 on all three.
   =========================================================================== */
SELECT 'FACT_PURCHASE_ORDER' AS fact_table,
       (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS)     AS silver_rows,
       (SELECT COUNT(*) FROM GOLD.FACT_PURCHASE_ORDER)   AS gold_rows,
       (SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS)
         - (SELECT COUNT(*) FROM GOLD.FACT_PURCHASE_ORDER) AS lost_rows
UNION ALL
SELECT 'FACT_INVOICE',
       (SELECT COUNT(*) FROM SILVER.INVOICES),
       (SELECT COUNT(*) FROM GOLD.FACT_INVOICE),
       (SELECT COUNT(*) FROM SILVER.INVOICES) - (SELECT COUNT(*) FROM GOLD.FACT_INVOICE)
UNION ALL
SELECT 'FACT_PAYMENT',
       (SELECT COUNT(*) FROM SILVER.PAYMENTS),
       (SELECT COUNT(*) FROM GOLD.FACT_PAYMENT),
       (SELECT COUNT(*) FROM SILVER.PAYMENTS) - (SELECT COUNT(*) FROM GOLD.FACT_PAYMENT)
ORDER BY fact_table;

/* Amounts must reconcile too -- a row count can match while values are wrong. */
SELECT
    ROUND((SELECT SUM(AMOUNT_USD) FROM SILVER.INVOICES), 2)     AS silver_invoiced_usd,
    ROUND((SELECT SUM(AMOUNT_USD) FROM GOLD.FACT_INVOICE), 2)   AS gold_invoiced_usd,
    ROUND((SELECT SUM(AMOUNT_PAID_USD) FROM SILVER.PAYMENTS), 2)   AS silver_paid_usd,
    ROUND((SELECT SUM(AMOUNT_PAID_USD) FROM GOLD.FACT_PAYMENT), 2) AS gold_paid_usd;

/* How many invoices bound to a NON-current vendor version? A non-zero number is
   the proof that historical binding actually happened -- if every fact pointed
   at the current version, SCD2 would be present but unused. */
SELECT
    COUNT_IF(NOT v.IS_CURRENT) AS invoices_on_historical_versions,
    COUNT_IF(v.IS_CURRENT)     AS invoices_on_current_versions,
    COUNT(*)                   AS total
FROM GOLD.FACT_INVOICE f
JOIN GOLD.DIM_VENDOR v ON v.VENDOR_KEY = f.VENDOR_KEY;
