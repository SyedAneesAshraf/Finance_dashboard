/* ============================================================================
   QUERY 5.5 — THREE-WAY MATCH VARIANCE
   SQL concepts: subquery/aggregate join + CASE + NULLIF
   Answers business question 5: do POs, goods receipts and invoices agree?

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   The three-way match is the core procurement control: before paying, the
   invoice must agree with what was ORDERED (the purchase order) and with what
   was RECEIVED (the goods receipt). All three must line up. If they do not,
   the company is about to pay for something it did not order, did not receive,
   or was overcharged for.

   A 5% tolerance is applied because small differences are legitimate -- freight,
   rounding, minor quantity variation on bulk materials. Beyond 5% it stops
   being noise and becomes a price discrepancy someone has to explain.

   ---------------------------------------------------------------------------
   WHY THIS AGGREGATES INVOICES PER PO
   ---------------------------------------------------------------------------
   A naive version compares each invoice to its PO one-to-one. That is wrong
   here, and wrong in most real systems, because a PO is frequently billed
   across several invoices as deliveries are staged. Comparing a single partial
   invoice against the full PO value would flag every legitimate staged delivery
   as a massive under-billing -- the report would be almost entirely false
   positives and would be ignored within a week.

   The control that matters is CUMULATIVE: has the total billed against this PO
   exceeded what was ordered? That is what this computes.

   ---------------------------------------------------------------------------
   WHY NULLIF
   ---------------------------------------------------------------------------
   NULLIF(po_amount, 0) turns a zero PO value into NULL, so the division yields
   NULL rather than raising a divide-by-zero. A cancelled or zero-value PO then
   reports "cannot assess" instead of aborting the whole query -- one bad row
   should not take down a control report that Finance runs monthly.

   ---------------------------------------------------------------------------
   EXPECTED RESULT AND ITS OVERLAP WITH QUERY 5.4
   ---------------------------------------------------------------------------
   36 exceptions, not the 22 POs that were deliberately mis-priced. The other 14
   are POs pushed past tolerance by a DUPLICATE invoice quoting the same PO
   number -- the defect that query 5.4 detects.

   That overlap is real, not a flaw. A duplicated bill genuinely IS an
   over-billing against the PO, so two independent controls catch the same event
   from different angles. In practice you investigate the duplicate first,
   because resolving it also clears the variance.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

WITH invoiced_per_po AS (
    /* Total billed against each PO. Aggregating first is what makes staged
       deliveries compare correctly. */
    SELECT
        f.PO_ID,
        COUNT(*)             AS invoice_count,
        SUM(f.AMOUNT)        AS total_invoiced,
        SUM(f.AMOUNT_USD)    AS total_invoiced_usd,
        MIN(f.INVOICE_DATE)  AS first_invoice_date,
        MAX(f.INVOICE_DATE)  AS last_invoice_date,
        LISTAGG(f.INVOICE_ID, ', ') WITHIN GROUP (ORDER BY f.INVOICE_ID) AS invoice_ids
    FROM     GOLD.FACT_INVOICE f
    WHERE    f.PO_ID IS NOT NULL          -- non-PO spend has nothing to match against
    GROUP BY f.PO_ID
),
matched AS (
    SELECT
        po.PO_ID,
        po.VENDOR_ID,
        po.PO_DATE,
        po.GOODS_RECEIPT_DATE,
        po.PO_STATUS,
        po.PO_AMOUNT,
        po.PO_AMOUNT_USD,
        po.CURRENCY,
        i.invoice_count,
        i.total_invoiced,
        i.total_invoiced_usd,
        i.invoice_ids,
        i.first_invoice_date,
        i.last_invoice_date,
        (i.total_invoiced - po.PO_AMOUNT)                        AS variance_amount,
        ROUND(100.0 * (i.total_invoiced - po.PO_AMOUNT)
              / NULLIF(po.PO_AMOUNT, 0), 2)                      AS pct_variance
    FROM      invoiced_per_po i
    JOIN      GOLD.FACT_PURCHASE_ORDER po ON po.PO_ID = i.PO_ID
)
SELECT
    m.PO_ID,
    m.VENDOR_ID,
    v.VENDOR_NAME,
    m.PO_DATE,
    m.PO_STATUS,
    m.PO_AMOUNT,
    m.CURRENCY,
    m.invoice_count,
    m.total_invoiced,
    m.variance_amount,
    m.pct_variance,
    m.invoice_ids,

    /* Leg 1 of the match: was anything actually received? */
    CASE WHEN m.GOODS_RECEIPT_DATE IS NULL
         THEN 'NO GOODS RECEIPT' ELSE 'Received' END              AS receipt_status,

    /* Legs 2 and 3: does the invoiced value agree with the ordered value? */
    CASE
        WHEN m.PO_AMOUNT IS NULL OR m.PO_AMOUNT = 0
            THEN 'Cannot assess - zero or missing PO value'
        WHEN ABS(m.total_invoiced - m.PO_AMOUNT) / NULLIF(m.PO_AMOUNT, 0) > 0.05
            THEN 'Exception - Review'
        ELSE 'Matched'
    END                                                            AS match_status,

    /* Over- and under-billing are different problems needing different people:
       over-billing is money at risk now, under-billing is an unrecorded
       liability that will land in a future period. */
    CASE
        WHEN m.PO_AMOUNT IS NULL OR m.PO_AMOUNT = 0 THEN 'Unassessable'
        WHEN m.pct_variance >  5  THEN 'Over-billed - do not pay difference'
        WHEN m.pct_variance < -5  THEN 'Under-billed - expect further invoices'
        ELSE 'Within tolerance'
    END                                                            AS exception_type
FROM      matched m
JOIN      GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = m.VENDOR_ID
      AND v.IS_CURRENT
/* Exceptions first, largest absolute variance at the top -- the report should
   open on the biggest problem, not on PO100001. */
ORDER BY
    CASE WHEN ABS(COALESCE(m.pct_variance, 0)) > 5 THEN 0 ELSE 1 END,
    ABS(COALESCE(m.pct_variance, 0)) DESC,
    m.PO_ID;
