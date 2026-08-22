/* ============================================================================
   QUERY 5.2 — AP AGING BUCKETS
   SQL concepts: CASE + DATEDIFF conditional logic
   Answers business question 3: how much payable is overdue, and by how long?

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   This is the single most common report in accounts payable. It takes every
   unpaid invoice and sorts it by how far past its due date it has drifted,
   into the standard 0-30 / 31-60 / 61-90 / 90+ day buckets.

   The buckets are not arbitrary. Each maps to a different action: 0-30 is
   normal payment lag, 31-60 warrants a chase, 61-90 usually means something is
   stuck in approval, and 90+ is where late-payment penalties, supply
   interruption and audit findings live. Money sitting in the 90+ bucket is
   almost never an oversight -- it is a dispute, a missing goods receipt, or a
   process failure.

   Two things make this query correct rather than merely plausible:

   1. It buckets on DUE_DATE, not INVOICE_DATE. An invoice issued 90 days ago on
      NET90 terms is not overdue at all. Ageing from the invoice date is a
      common and expensive mistake -- it manufactures overdue balances that do
      not exist and destroys the report's credibility with Finance.

   2. 'Not Due' is a bucket in its own right, kept separate from 0-30. Lumping
      them together overstates overdue exposure, which is the number the whole
      report exists to communicate.

   NOTE ON CURRENT_DATE: bucket membership is relative to today, so it shifts as
   real time passes. That is correct behaviour for an ageing report -- an
   invoice genuinely does move from 31-60 into 61-90 by doing nothing. It also
   means this query's output is not reproducible across dates by design.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

WITH unpaid AS (
    SELECT
        f.INVOICE_ID,
        f.VENDOR_ID,
        f.PO_ID,
        f.INVOICE_DATE,
        f.DUE_DATE,
        f.AMOUNT_USD,
        f.IS_NON_PO_SPEND,
        DATEDIFF('day', f.DUE_DATE, CURRENT_DATE) AS days_overdue
    FROM GOLD.FACT_INVOICE f
    WHERE f.STATUS = 'Unpaid'
),
bucketed AS (
    SELECT
        u.*,
        CASE
            WHEN days_overdue <= 0             THEN 'Not Due'
            WHEN days_overdue BETWEEN 1  AND 30 THEN '0-30 Days'
            WHEN days_overdue BETWEEN 31 AND 60 THEN '31-60 Days'
            WHEN days_overdue BETWEEN 61 AND 90 THEN '61-90 Days'
            ELSE '90+ Days'
        END AS aging_bucket,
        /* An explicit sort key. Ordering by the bucket label alphabetically
           would render the chart as 0-30, 31-60, 61-90, 90+, Not Due -- which
           reads as though 90+ were less severe than 'Not Due'. */
        CASE
            WHEN days_overdue <= 0             THEN 1
            WHEN days_overdue BETWEEN 1  AND 30 THEN 2
            WHEN days_overdue BETWEEN 31 AND 60 THEN 3
            WHEN days_overdue BETWEEN 61 AND 90 THEN 4
            ELSE 5
        END AS bucket_sort_order
    FROM unpaid u
)
SELECT
    b.aging_bucket,
    b.bucket_sort_order,
    COUNT(*)                                            AS invoice_count,
    ROUND(SUM(b.AMOUNT_USD), 2)                         AS outstanding_usd,
    ROUND(100.0 * SUM(b.AMOUNT_USD) / SUM(SUM(b.AMOUNT_USD)) OVER (), 2) AS pct_of_outstanding,
    ROUND(AVG(b.days_overdue), 0)                       AS avg_days_overdue,
    MAX(b.days_overdue)                                 AS max_days_overdue,
    COUNT_IF(b.IS_NON_PO_SPEND)                         AS non_po_invoices
FROM     bucketed b
GROUP BY b.aging_bucket, b.bucket_sort_order
ORDER BY b.bucket_sort_order;
