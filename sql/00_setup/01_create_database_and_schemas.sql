/* ============================================================================
   PHASE 0 — SNOWFLAKE ENVIRONMENT SETUP
   Project : Finance & Procurement Analytics (Oracle EBS-style)
   Run as  : ACCOUNTADMIN (or any role with CREATE DATABASE / CREATE WAREHOUSE)
   Where   : Snowsight  →  Projects  →  Worksheets  →  new SQL worksheet

   Creates the warehouse, database, and the three medallion schemas.
   Safe to re-run: every statement is IF NOT EXISTS / OR REPLACE-free.
   ============================================================================ */

USE ROLE ACCOUNTADMIN;

/* ---------------------------------------------------------------------------
   1. Compute
   X-Small is plenty for this dataset (~2,000 rows total). AUTO_SUSPEND at 60s
   and INITIALLY_SUSPENDED keep a 30-day trial's credits from draining while
   the warehouse sits idle between phases.
   --------------------------------------------------------------------------- */
CREATE WAREHOUSE IF NOT EXISTS FIN_PROC_WH
    WAREHOUSE_SIZE      = 'XSMALL'
    AUTO_SUSPEND        = 60
    AUTO_RESUME         = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT             = 'Compute for Finance & Procurement analytics project';

/* ---------------------------------------------------------------------------
   2. Database + medallion schemas
   --------------------------------------------------------------------------- */
CREATE DATABASE IF NOT EXISTS FIN_PROC_DB
    COMMENT = 'Oracle EBS-style Finance & Procurement analytics platform';

USE DATABASE FIN_PROC_DB;

CREATE SCHEMA IF NOT EXISTS BRONZE
    COMMENT = 'Raw landing zone. Source extracts loaded verbatim, all VARCHAR, no transformation.';

CREATE SCHEMA IF NOT EXISTS SILVER
    COMMENT = 'Cleaned + conformed. Typed columns, standardized text/dates, FX-converted, DQ-flagged.';

CREATE SCHEMA IF NOT EXISTS GOLD
    COMMENT = 'Business-ready star schema. Facts + dimensions, DIM_VENDOR is SCD Type 2.';

/* Snowflake creates a PUBLIC schema with every database; we do not use it. */

/* ---------------------------------------------------------------------------
   3. File format + internal stage for the Bronze CSV loads (Phase 2)
   Created here so Phase 2 is a pure "load" step with no setup friction.

   Note on SKIP_HEADER / NULL_IF: the generator writes a header row and writes
   genuinely-missing values as empty strings. EMPTY_FIELD_AS_NULL = TRUE plus
   NULL_IF = ('') means those land as real SQL NULLs in Bronze rather than as
   the two-character text 'NA' — Bronze preserves the *absence*, faithfully.
   --------------------------------------------------------------------------- */
USE SCHEMA BRONZE;

CREATE FILE FORMAT IF NOT EXISTS BRONZE.CSV_STANDARD
    TYPE                         = 'CSV'
    FIELD_DELIMITER              = ','
    SKIP_HEADER                  = 1
    FIELD_OPTIONALLY_ENCLOSED_BY = '"'
    TRIM_SPACE                   = FALSE
    EMPTY_FIELD_AS_NULL          = TRUE
    NULL_IF                      = ('')
    ENCODING                     = 'UTF8'
    COMMENT                      = 'Standard CSV format for raw EBS-style extracts';

CREATE STAGE IF NOT EXISTS BRONZE.RAW_FILES
    FILE_FORMAT = BRONZE.CSV_STANDARD
    COMMENT     = 'Internal stage holding the 5 raw source CSVs';

/* ---------------------------------------------------------------------------
   4. Session defaults
   --------------------------------------------------------------------------- */
USE WAREHOUSE FIN_PROC_WH;
USE DATABASE  FIN_PROC_DB;
USE SCHEMA    BRONZE;
