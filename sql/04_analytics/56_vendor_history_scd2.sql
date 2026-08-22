/* ============================================================================
   QUERY 5.6 — VENDOR HISTORY SNAPSHOT (SCD TYPE 2 RANGE JOIN)
   SQL concepts: range join on BETWEEN effective_start AND effective_end
   Demonstrates: historically accurate reporting

   ---------------------------------------------------------------------------
   BUSINESS EXPLANATION
   ---------------------------------------------------------------------------
   "What payment terms did this vendor have WHEN this invoice was issued?" --
   not what they have today.

   This is the difference between "what is true now" and "what was true then",
   and in finance it is not a philosophical distinction. If a vendor moved from
   NET30 to NET45 in September 2025, then an invoice issued in June 2025 was due
   in 30 days. Judging it against today's NET45 would score a late payment as
   on-time, understate the DPO problem, and produce an audit finding when
   someone reconciles the report against the original contract.

   A flat, current-only vendor table CANNOT answer this question at all. It has
   exactly one row per vendor and no memory that anything ever changed. That is
   the entire reason DIM_VENDOR is modelled as Type 2.

   ---------------------------------------------------------------------------
   HOW THE RANGE JOIN WORKS
   ---------------------------------------------------------------------------
       JOIN DIM_VENDOR v
         ON  f.VENDOR_ID = v.VENDOR_ID
         AND f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE
                                AND v.EFFECTIVE_END_DATE

   The equality alone would match every version of the vendor and multiply the
   invoice by its version count. The BETWEEN narrows it to the single version
   whose validity window contains the invoice date.

   This only produces one row because Phase 4 guarantees the version ranges
   ABUT WITHOUT OVERLAPPING -- each version ends the day before the next begins,
   verified at 0 overlaps and 0 gaps. An overlap would double-count the invoice;
   a gap would make it disappear from the report entirely. Both failures are
   silent, which is why they are tested rather than assumed.

   EFFECTIVE_END_DATE is 9999-12-31 on the open version rather than NULL,
   because BETWEEN against a NULL upper bound evaluates to UNKNOWN and every
   current vendor would silently vanish from these results.

   ---------------------------------------------------------------------------
   WHAT TO LOOK FOR IN THE OUTPUT
   ---------------------------------------------------------------------------
   TERMS_AT_INVOICE_DATE differs from TERMS_TODAY on the rows where it matters.
   The TERMS_CHANGED_SINCE column marks exactly those. Same vendor, same query,
   different answer depending on when the invoice was issued -- which is the
   proof that the history is real and usable, not decorative columns on a table.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

SELECT
    f.INVOICE_ID,
    f.INVOICE_DATE,
    f.VENDOR_ID,
    hist.VENDOR_NAME,

    /* --- the historically accurate view: what applied at the time --- */
    hist.CATEGORY           AS category_at_invoice_date,
    hist.PAYMENT_TERMS      AS terms_at_invoice_date,
    hist.VERSION_NUMBER     AS vendor_version,
    hist.EFFECTIVE_START_DATE,
    hist.EFFECTIVE_END_DATE,

    /* --- what a current-only vendor table would have told us --- */
    curr.CATEGORY           AS category_today,
    curr.PAYMENT_TERMS      AS terms_today,

    /* --- the contrast, made explicit --- */
    CASE WHEN hist.PAYMENT_TERMS <> curr.PAYMENT_TERMS
         THEN 'YES - a current-only table would report ' || curr.PAYMENT_TERMS
         ELSE 'no'
    END AS terms_changed_since,

    f.AMOUNT_USD,
    f.DUE_DATE,
    f.TERM_DAYS             AS actual_terms_granted_on_invoice,
    hist.PAYMENT_TERMS_DAYS AS standard_terms_at_the_time,
    f.STATUS

FROM GOLD.FACT_INVOICE f

/* The version that was in force when the invoice was issued. */
JOIN GOLD.DIM_VENDOR hist
  ON  hist.VENDOR_ID = f.VENDOR_ID
 AND  f.INVOICE_DATE BETWEEN hist.EFFECTIVE_START_DATE AND hist.EFFECTIVE_END_DATE

/* The version in force today, purely for the side-by-side comparison. */
JOIN GOLD.DIM_VENDOR curr
  ON  curr.VENDOR_ID = f.VENDOR_ID
 AND  curr.IS_CURRENT

/* Restrict to the vendors that actually have history, so the contrast is
   visible rather than buried under 600 rows where nothing changed. */
WHERE f.VENDOR_ID IN (
    SELECT VENDOR_ID FROM GOLD.DIM_VENDOR GROUP BY VENDOR_ID HAVING COUNT(*) > 1
)
ORDER BY f.VENDOR_ID, f.INVOICE_DATE;
