/* ============================================================================
   PHASE 4 — SCD TYPE 2 MERGE FOR DIM_VENDOR
   The technical centrepiece of the Gold layer.

   Applies the second vendor extract (SILVER.VENDORS_UPDATE, 2026-08-15) to a
   DIM_VENDOR currently holding version 1 of all 50 vendors. Three outcomes:

     UNCHANGED  do nothing
     CHANGED    close the current version, insert a new one
     NEW        insert as version 1

   ---------------------------------------------------------------------------
   THE PROBLEM THIS PATTERN SOLVES
   ---------------------------------------------------------------------------
   A changed vendor needs TWO actions against the target: UPDATE the old row and
   INSERT a new one. A MERGE gives each source row exactly one action, so a
   naive single-statement MERGE cannot do both.

   The standard fix is to make each changed vendor appear TWICE in the source:

     row A  merge_key = <existing VENDOR_KEY>  -> matches   -> UPDATE (close out)
     row B  merge_key = NULL                   -> no match  -> INSERT (new version)

   Because VENDOR_KEY is never NULL in the target, row B can never match
   anything, which is what makes the trick reliable rather than merely clever.

   ---------------------------------------------------------------------------
   WHY THE CHANGE DATE COMES FROM LAST_UPDATE_DATE
   ---------------------------------------------------------------------------
   The new version starts on the source system's LAST_UPDATE_DATE -- an Oracle
   EBS WHO column -- not on the extract date.

   This is the difference between SCD2 that works and SCD2 that looks like it
   works. V0026 changed terms on 2025-05-10 but was extracted on 2026-08-15.
   Using the extract date would attribute the NEW terms to all 15 months of
   invoices in between, and query 5.6 would return confidently wrong answers.

   ---------------------------------------------------------------------------
   WHY THIS RUNS AGAINST SILVER, NEVER BRONZE
   ---------------------------------------------------------------------------
   The change detection compares CATEGORY and PAYMENT_TERMS as strings. In
   Bronze those still carry casing and whitespace noise, so 'Consulting' vs
   'CONSULTING' would register as a real change and spawn a spurious version for
   a vendor that never changed. Phase 3 proved 0 vendors differ by name alone
   after standardization, which is what makes this comparison safe.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    GOLD;

/* ---------------------------------------------------------------------------
   PREVIEW: what the MERGE is about to do. Run this first -- an SCD2 load that
   silently does nothing looks identical to one that silently does everything.
   Expect: 6 CHANGED, 2 NEW, 44 UNCHANGED.
   --------------------------------------------------------------------------- */
WITH current_version AS (
    SELECT VENDOR_KEY, VENDOR_ID, CATEGORY, PAYMENT_TERMS, VERSION_NUMBER
    FROM   GOLD.DIM_VENDOR
    WHERE  IS_CURRENT
)
SELECT
    CASE
        WHEN c.VENDOR_KEY IS NULL THEN 'NEW'
        WHEN u.CATEGORY <> c.CATEGORY OR u.PAYMENT_TERMS <> c.PAYMENT_TERMS THEN 'CHANGED'
        ELSE 'UNCHANGED'
    END AS change_type,
    COUNT(*) AS vendors
FROM      SILVER.VENDORS_UPDATE u
LEFT JOIN current_version c ON c.VENDOR_ID = u.VENDOR_ID
GROUP BY 1
ORDER BY 1;


/* ===========================================================================
   THE MERGE
   =========================================================================== */
MERGE INTO GOLD.DIM_VENDOR AS tgt
USING (
    WITH current_version AS (
        SELECT VENDOR_KEY, VENDOR_ID, CATEGORY, PAYMENT_TERMS, VERSION_NUMBER
        FROM   GOLD.DIM_VENDOR
        WHERE  IS_CURRENT
    ),
    delta AS (
        SELECT
            u.VENDOR_ID,
            u.VENDOR_NAME,
            u.CATEGORY,
            u.REGION,
            u.PAYMENT_TERMS,
            u.PAYMENT_TERMS_DAYS,
            u.CURRENCY,
            u.CREATION_DATE,
            u.LAST_UPDATE_DATE,
            u.DATA_QUALITY_FLAGS,
            c.VENDOR_KEY     AS current_key,
            c.VERSION_NUMBER AS current_version,
            CASE
                WHEN c.VENDOR_KEY IS NULL THEN 'NEW'
                WHEN u.CATEGORY <> c.CATEGORY
                  OR u.PAYMENT_TERMS <> c.PAYMENT_TERMS THEN 'CHANGED'
                ELSE 'UNCHANGED'
            END AS change_type
        FROM      SILVER.VENDORS_UPDATE u
        LEFT JOIN current_version c ON c.VENDOR_ID = u.VENDOR_ID
    )

    /* row A -- close out the superseded version (CHANGED only) */
    SELECT
        current_key            AS merge_key,
        VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS,
        PAYMENT_TERMS_DAYS, CURRENCY, CREATION_DATE, LAST_UPDATE_DATE,
        DATA_QUALITY_FLAGS,
        LAST_UPDATE_DATE       AS new_effective_start,
        current_version + 1    AS new_version,
        'CLOSE'                AS action
    FROM delta
    WHERE change_type = 'CHANGED'

    UNION ALL

    /* row B -- insert the new version (CHANGED and NEW) */
    SELECT
        NULL                   AS merge_key,
        VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS,
        PAYMENT_TERMS_DAYS, CURRENCY, CREATION_DATE, LAST_UPDATE_DATE,
        DATA_QUALITY_FLAGS,
        /* a brand-new vendor's history starts when it was created; a changed
           vendor's new version starts the day the source recorded the change */
        CASE WHEN change_type = 'NEW' THEN CREATION_DATE ELSE LAST_UPDATE_DATE END
                               AS new_effective_start,
        CASE WHEN change_type = 'NEW' THEN 1 ELSE current_version + 1 END
                               AS new_version,
        'INSERT'               AS action
    FROM delta
    WHERE change_type IN ('CHANGED', 'NEW')
) AS src
ON tgt.VENDOR_KEY = src.merge_key

/* Close out: the old version ends the day before the new one begins, so the
   two ranges abut without overlapping. An overlap would make the range join in
   query 5.6 return two rows for one invoice and double-count it. */
WHEN MATCHED THEN UPDATE SET
    tgt.EFFECTIVE_END_DATE = DATEADD('day', -1, src.new_effective_start),
    tgt.IS_CURRENT         = FALSE,
    tgt._GOLD_LOAD_TS      = CURRENT_TIMESTAMP()

WHEN NOT MATCHED THEN INSERT
    (VENDOR_ID, VENDOR_NAME, CATEGORY, REGION, PAYMENT_TERMS, PAYMENT_TERMS_DAYS,
     CURRENCY, EFFECTIVE_START_DATE, EFFECTIVE_END_DATE, IS_CURRENT, VERSION_NUMBER,
     SOURCE_CREATION_DATE, SOURCE_LAST_UPDATE, DATA_QUALITY_FLAGS, _GOLD_LOAD_TS)
VALUES
    (src.VENDOR_ID, src.VENDOR_NAME, src.CATEGORY, src.REGION, src.PAYMENT_TERMS,
     src.PAYMENT_TERMS_DAYS, src.CURRENCY,
     src.new_effective_start, '9999-12-31'::DATE, TRUE, src.new_version,
     src.CREATION_DATE, src.LAST_UPDATE_DATE, src.DATA_QUALITY_FLAGS,
     CURRENT_TIMESTAMP());


/* ===========================================================================
   VERIFICATION
   =========================================================================== */

/* The headline exit test: MORE rows than distinct vendors.
   Expect 58 rows for 52 vendors -- 6 vendors carry 2 versions each. */
SELECT
    COUNT(*)                                    AS total_rows,
    COUNT(DISTINCT VENDOR_ID)                   AS distinct_vendors,
    COUNT(*) - COUNT(DISTINCT VENDOR_ID)        AS extra_version_rows,
    COUNT_IF(IS_CURRENT)                        AS current_rows,
    COUNT_IF(NOT IS_CURRENT)                    AS historical_rows,
    CASE WHEN COUNT(*) > COUNT(DISTINCT VENDOR_ID)
         THEN 'PASS - SCD2 is real' ELSE 'FAIL - decorative only' END AS result
FROM GOLD.DIM_VENDOR;

/* Every vendor must have exactly one open version -- no more, no fewer. */
SELECT
    COUNT_IF(current_versions <> 1) AS vendors_without_exactly_one_current,
    CASE WHEN COUNT_IF(current_versions <> 1) = 0 THEN 'PASS' ELSE 'FAIL' END AS result
FROM (
    SELECT VENDOR_ID, COUNT_IF(IS_CURRENT) AS current_versions
    FROM   GOLD.DIM_VENDOR
    GROUP  BY VENDOR_ID
);

/* No overlapping validity ranges for the same vendor -- otherwise a single
   invoice would match two dimension rows and be counted twice. Expect 0. */
SELECT COUNT(*) AS overlapping_ranges
FROM   GOLD.DIM_VENDOR a
JOIN   GOLD.DIM_VENDOR b
  ON   a.VENDOR_ID  = b.VENDOR_ID
 AND   a.VENDOR_KEY < b.VENDOR_KEY
 AND   a.EFFECTIVE_START_DATE <= b.EFFECTIVE_END_DATE
 AND   b.EFFECTIVE_START_DATE <= a.EFFECTIVE_END_DATE;

/* The six versioned vendors, both versions side by side. */
SELECT VENDOR_KEY, VENDOR_ID, VENDOR_NAME, CATEGORY, PAYMENT_TERMS,
       EFFECTIVE_START_DATE, EFFECTIVE_END_DATE, IS_CURRENT, VERSION_NUMBER
FROM   GOLD.DIM_VENDOR
WHERE  VENDOR_ID IN (
    SELECT VENDOR_ID FROM GOLD.DIM_VENDOR GROUP BY VENDOR_ID HAVING COUNT(*) > 1
)
ORDER  BY VENDOR_ID, VERSION_NUMBER;
