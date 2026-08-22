/* ============================================================================
   PHASE 0 — EXIT TESTS
   Run every block. All four must pass before Phase 1 is considered started.
   ============================================================================ */

USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    BRONZE;

/* ---- TEST 1 -----------------------------------------------------------------
   Expect: FIN_PROC_WH | FIN_PROC_DB | BRONZE  (and a non-null role/user)
   ---------------------------------------------------------------------------- */
SELECT
    CURRENT_WAREHOUSE() AS current_warehouse,
    CURRENT_DATABASE()  AS current_database,
    CURRENT_SCHEMA()    AS current_schema,
    CURRENT_ROLE()      AS current_role,
    CURRENT_VERSION()   AS snowflake_version;

/* ---- TEST 2 -----------------------------------------------------------------
   Expect: exactly 3 rows — BRONZE, GOLD, SILVER (PUBLIC/INFORMATION_SCHEMA excluded)
   ---------------------------------------------------------------------------- */
SELECT schema_name, comment
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.SCHEMATA
WHERE  schema_name IN ('BRONZE', 'SILVER', 'GOLD')
ORDER  BY schema_name;

/* ---- TEST 3 -----------------------------------------------------------------
   Expect: schemas_found = 3
   A single-row PASS/FAIL so there is nothing to eyeball.
   ---------------------------------------------------------------------------- */
SELECT
    COUNT(*) AS schemas_found,
    CASE WHEN COUNT(*) = 3 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.SCHEMATA
WHERE  schema_name IN ('BRONZE', 'SILVER', 'GOLD');

/* ---- TEST 4 -----------------------------------------------------------------
   Expect: the CSV_STANDARD file format and the RAW_FILES stage both listed.
   ---------------------------------------------------------------------------- */
SHOW FILE FORMATS IN SCHEMA FIN_PROC_DB.BRONZE;
SHOW STAGES       IN SCHEMA FIN_PROC_DB.BRONZE;

/* ---- TEST 5 (housekeeping) --------------------------------------------------
   Confirms all three schemas are genuinely empty — Phase 0 creates structure,
   never data. Expect: 0 rows.
   ---------------------------------------------------------------------------- */
SELECT table_schema, table_name, row_count
FROM   FIN_PROC_DB.INFORMATION_SCHEMA.TABLES
WHERE  table_schema IN ('BRONZE', 'SILVER', 'GOLD');
