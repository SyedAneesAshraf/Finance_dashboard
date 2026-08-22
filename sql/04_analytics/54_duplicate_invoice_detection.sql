/* ============================================================================
   QUERY 5.4 — DUPLICATE INVOICE DETECTION
   SQL concepts: self-join with a tolerance window
   Answers business question 4: are there duplicate or suspicious invoices?

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   Duplicate payment is one of the most common and most expensive AP control
   failures. It rarely looks like fraud: a supplier re-sends an invoice that was
   already in the queue, or the same PDF gets keyed twice by two people, and the
   company simply pays twice. Every AP team runs a version of this check, and
   external auditors ask for it by name.

   The rule: same vendor, same amount, invoice dates within three days of each
   other, different invoice IDs. Three days is the tolerance window -- wide
   enough to catch a re-send that arrived the next morning, narrow enough not to
   flag a vendor who legitimately bills the same round amount every month.

   ---------------------------------------------------------------------------
   WHY a.INVOICE_ID < b.INVOICE_ID
   ---------------------------------------------------------------------------
   Without it, a self-join returns each pair TWICE (A-B and B-A) plus every row
   matched against itself. The strict inequality does all three jobs at once: it
   eliminates self-matches, deduplicates the mirrored pairs, and fixes which
   invoice is reported as the "original". Reporting 28 duplicates when there are
   14 destroys the credibility of the whole control.

   ---------------------------------------------------------------------------
   WHY THIS IS A CANDIDATE LIST, NOT A VERDICT
   ---------------------------------------------------------------------------
   The rule is intentionally slightly over-inclusive. A vendor on a fixed
   monthly retainer can trip it legitimately. That is the correct trade-off for
   a fraud control: a false positive costs an analyst five minutes, a false
   negative costs the invoice amount. The output is a work queue for AP to
   review, not an accusation -- which is why POTENTIAL_DUPLICATE_AMOUNT_USD is
   labelled as exposure at risk rather than as a loss.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

SELECT
    a.INVOICE_ID                                   AS invoice_1,
    b.INVOICE_ID                                   AS invoice_2,
    a.VENDOR_ID,
    v.VENDOR_NAME,
    a.AMOUNT                                       AS amount,
    a.CURRENCY,
    a.AMOUNT_USD                                   AS potential_duplicate_amount_usd,
    a.INVOICE_DATE                                 AS invoice_1_date,
    b.INVOICE_DATE                                 AS invoice_2_date,
    ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE)) AS days_apart,
    a.PO_ID                                        AS invoice_1_po,
    b.PO_ID                                        AS invoice_2_po,
    a.STATUS                                       AS invoice_1_status,
    b.STATUS                                       AS invoice_2_status,
    /* Both already paid is the expensive case -- the money is gone and the
       action is to recover it, not to stop it. */
    CASE
        WHEN a.STATUS = 'Paid' AND b.STATUS = 'Paid'
            THEN 'BOTH PAID - recover overpayment'
        WHEN a.STATUS = 'Paid' OR  b.STATUS = 'Paid'
            THEN 'ONE PAID - hold the second'
        ELSE 'NEITHER PAID - block before payment run'
    END AS recommended_action
FROM      GOLD.FACT_INVOICE a
JOIN      GOLD.FACT_INVOICE b
       ON a.VENDOR_ID  = b.VENDOR_ID          -- same supplier
      AND a.AMOUNT     = b.AMOUNT             -- identical value
      AND a.INVOICE_ID < b.INVOICE_ID         -- each pair once, never self-matched
      AND ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE)) <= 3   -- tolerance window
JOIN      GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = a.VENDOR_ID
      AND v.IS_CURRENT
ORDER BY  a.AMOUNT_USD DESC, a.INVOICE_ID;
