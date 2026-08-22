/* ============================================================================
   QUERY 5.3 — VENDOR SPEND CONCENTRATION (PARETO)
   SQL concepts: window functions -- RANK, SUM() OVER (), running SUM OVER (ORDER BY)
   Answers business question 1: is our spend too concentrated in a few vendors?

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   Supplier concentration is a risk nobody notices until it bites. If 80% of
   spend flows through five vendors, then one of those five going insolvent,
   getting acquired, or simply raising prices is not a procurement inconvenience
   -- it is a production stoppage. Procurement uses this analysis to decide
   where a second source is worth the cost of qualifying one.

   The mechanic is a Pareto curve: rank vendors by spend, then track the running
   cumulative percentage. Reading down the CUMULATIVE_PCT column to where it
   crosses 80% tells you exactly how few vendors carry most of the exposure.

   Three window functions do the work, and each answers a different question:

     RANK() OVER (ORDER BY spend DESC)     -- where does this vendor sit?
     SUM(SUM(x)) OVER ()                   -- what is total spend? (grand total)
     SUM(SUM(x)) OVER (ORDER BY spend DESC)-- what is the running total to here?

   The nested SUM(SUM(x)) is not a typo. The inner SUM aggregates within the
   GROUP BY; the outer SUM is the window function operating over those already
   grouped rows. This is the standard way to compute a share-of-total without a
   self-join or a second pass over the data.

   ---------------------------------------------------------------------------
   TWO CORRECTNESS DETAILS
   ---------------------------------------------------------------------------
   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW is stated explicitly. The
   default frame for a window with ORDER BY is RANGE, which includes all PEER
   rows -- so two vendors with identical spend would each get the same running
   total, and the cumulative curve would step rather than climb. With money this
   rarely happens, but "rarely" is not "never" and the failure is silent.

   ORDER BY ... , VENDOR_ID breaks ties deterministically, so the same data
   always produces the same ranking rather than an arbitrary one.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

WITH vendor_spend AS (
    SELECT
        f.VENDOR_ID,
        SUM(f.AMOUNT_USD) AS total_spend_usd,
        COUNT(*)          AS invoice_count
    FROM     GOLD.FACT_INVOICE f
    GROUP BY f.VENDOR_ID
)
SELECT
    vs.VENDOR_ID,
    v.VENDOR_NAME,
    v.CATEGORY,
    v.REGION,
    vs.invoice_count,
    ROUND(vs.total_spend_usd, 2)                                     AS total_spend_usd,

    RANK() OVER (ORDER BY vs.total_spend_usd DESC, vs.VENDOR_ID)     AS spend_rank,

    ROUND(100.0 * vs.total_spend_usd
          / SUM(vs.total_spend_usd) OVER (), 2)                      AS pct_of_total,

    ROUND(100.0 * SUM(vs.total_spend_usd) OVER (
              ORDER BY vs.total_spend_usd DESC, vs.VENDOR_ID
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
          / SUM(vs.total_spend_usd) OVER (), 2)                      AS cumulative_pct,

    /* The business read-out: which vendors sit inside the 80% band. */
    CASE WHEN ROUND(100.0 * SUM(vs.total_spend_usd) OVER (
                        ORDER BY vs.total_spend_usd DESC, vs.VENDOR_ID
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                    / SUM(vs.total_spend_usd) OVER (), 2) <= 80.0
         THEN 'Core 80% of spend'
         ELSE 'Long tail'
    END AS pareto_segment
FROM      vendor_spend vs
JOIN      GOLD.DIM_VENDOR v
       ON v.VENDOR_ID = vs.VENDOR_ID
      AND v.IS_CURRENT
ORDER BY  vs.total_spend_usd DESC, vs.VENDOR_ID;
