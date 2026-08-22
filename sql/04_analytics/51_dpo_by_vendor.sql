/* ============================================================================
   QUERY 5.1 — DAYS PAYABLE OUTSTANDING (DPO) BY VENDOR
   SQL concepts: CTE + aggregate (AVG, GROUP BY)
   Answers business question 2: how long do we take to pay, and are we late?

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   DPO measures the average number of days between an invoice being issued and
   being paid. Finance watches it from two directions at once: a rising DPO can
   mean healthy working-capital management (holding cash longer) or it can mean
   cash-flow strain and a supplier relationship about to break. The number alone
   does not distinguish them.

   What makes it actionable is comparing DPO against the terms actually agreed
   with each vendor. Paying a NET60 vendor in 55 days is good treasury practice;
   paying a NET15 vendor in 55 days is a late-payment penalty and a strained
   relationship. This query therefore reports DAYS_BEYOND_TERMS alongside raw
   DPO -- the raw average on its own would rank a well-managed NET60 vendor as
   "worse" than a chronically-late NET15 one.

   The agreed terms come from the vendor version in effect WHEN EACH INVOICE WAS
   ISSUED (via VENDOR_KEY), not today's terms. For the six vendors whose terms
   changed mid-window, using current terms would misjudge every invoice issued
   before the change.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

WITH paid_invoices AS (
    /* One row per settled invoice, carrying both how long we took and what we
       had agreed to. DAYS_TO_PAY is read from the fact rather than recomputed
       so that SQL and the Power BI DAX measure cannot drift apart. */
    SELECT
        f.INVOICE_ID,
        f.VENDOR_ID,
        f.VENDOR_KEY,
        f.INVOICE_DATE,
        p.PAYMENT_DATE,
        p.DAYS_TO_PAY,
        p.DAYS_LATE,
        f.TERM_DAYS,
        f.AMOUNT_USD
    FROM GOLD.FACT_INVOICE  f
    JOIN GOLD.FACT_PAYMENT  p ON p.INVOICE_ID = f.INVOICE_ID
)
SELECT
    pi.VENDOR_ID,
    v.VENDOR_NAME,
    v.CATEGORY,
    v.REGION,
    COUNT(*)                                    AS invoices_paid,
    ROUND(SUM(pi.AMOUNT_USD), 2)                AS total_paid_usd,
    ROUND(AVG(pi.DAYS_TO_PAY), 1)               AS avg_dpo_days,
    ROUND(AVG(pi.TERM_DAYS), 1)                 AS avg_agreed_terms_days,
    ROUND(AVG(pi.DAYS_TO_PAY) - AVG(pi.TERM_DAYS), 1) AS avg_days_beyond_terms,
    COUNT_IF(pi.DAYS_LATE > 0)                  AS late_payments,
    ROUND(100.0 * COUNT_IF(pi.DAYS_LATE > 0) / COUNT(*), 1) AS pct_paid_late,
    MAX(pi.DAYS_LATE)                           AS worst_days_late
FROM      paid_invoices pi
/* Join to the CURRENT vendor row for naming only. Reporting labels should show
   who the vendor is today; the terms comparison above already used the
   historically-correct version via TERM_DAYS on the fact. */
JOIN      GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = pi.VENDOR_ID
      AND v.IS_CURRENT
GROUP BY  pi.VENDOR_ID, v.VENDOR_NAME, v.CATEGORY, v.REGION
ORDER BY  avg_days_beyond_terms DESC, avg_dpo_days DESC;
