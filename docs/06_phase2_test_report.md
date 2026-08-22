# Phase 2 — Exit Test Report (Bronze Layer)

**Date:** 2026-08-16
**Goal:** land the raw CSVs in Snowflake untouched.

```
.venv\Scripts\python.exe scripts\sf.py put "data_raw/*.csv" @BRONZE.RAW_FILES
.venv\Scripts\python.exe scripts\sf.py run sql\01_bronze\01_create_bronze_tables.sql
.venv\Scripts\python.exe scripts\sf.py run sql\01_bronze\02_load_bronze.sql
.venv\Scripts\python.exe scripts\validate_phase2.py        # 23 automated tests
.venv\Scripts\python.exe scripts\sf.py run sql\01_bronze\03_verify_bronze.sql
```

---

## Result: 23 / 23 passed

| # | Exit test (from the roadmap) | Result |
|---|---|---|
| 1 | `SELECT COUNT(*)` on each Bronze table matches its source CSV exactly | ✅ 6/6 tables |
| 2 | Spot-check 5 random rows per table — values match verbatim, nulls included | ✅ **all 1,864 rows** checked, not 5 |
| 3 | No transformation logic anywhere in the schema | ✅ 0 views, all columns still TEXT |

### On exit test 2

The roadmap asks for 5 random rows per table. This project compares **every row, every
column** — 1,864 rows across 6 tables — because at this data size the exhaustive check costs
the same as the sample, and sampling 5 of 675 rows would miss a single corrupted row **99.3%**
of the time.

The comparison is a **multiset** comparison over sorted rows, not a key join. That is
deliberate: `invoices` and `payments` contain intentional whole-row duplicates whose primary
key is not unique, and a join would either fan out or silently collapse them.

---

## Load results

Every file loaded with `errors_seen = 0` and `rows_parsed = rows_loaded`:

| Table | CSV rows | Loaded | |
|---|---|---|---|
| `GL_ACCOUNTS` | 20 | 20 | ✅ |
| `VENDORS` | 50 | 50 | ✅ |
| `VENDORS_UPDATE` | 52 | 52 | ✅ |
| `PURCHASE_ORDERS` | 480 | 480 | ✅ |
| `INVOICES` | 675 | 675 | ✅ |
| `PAYMENTS` | 587 | 587 | ✅ |

## Proof that nothing was cleaned

A Bronze layer that silently tidies data is worse than no Bronze layer, because it destroys the
audit trail while looking correct. These checks prove the mess survived:

```
CATEGORY has 15 raw variants for 5 real values        <- casing noise intact
51 PO_STATUS values still carry untrimmed whitespace  <- TRIM_SPACE = FALSE honoured
INVOICE_DATE: ISO 484, US 113, Oracle 78              <- still unparsed strings
3 invoice_ids and 3 payment_ids appear twice          <- defect 8 not deduplicated
0 views in BRONZE                                     <- no derived logic
every business column is still VARCHAR/TEXT           <- no parse decisions made at load
```

If `raw_variants` had equalled `canonical_values`, something would have standardized the data
during load — a medallion violation. The test asserts the inequality rather than just reporting
the counts.

## Blank vs null

```
4 vendors NULL payment_terms, 3 NULL region
18 invoices NULL po_id (non-PO spend)
0 empty strings masquerading as nulls
```

`EMPTY_FIELD_AS_NULL = TRUE` with `NULL_IF = ('')` means a blank source field lands as a real
SQL `NULL`. The distinction matters: `NULL` means "the source sent nothing", `''` means "the
source sent a blank". Conflating them would make Silver's null handling untestable, so the last
check asserts no empty strings exist at all.

## Load lineage

Every Bronze row carries `_SOURCE_FILE`, `_FILE_ROW_NUMBER`, and `_LOAD_TS`. These are lineage,
not transformation — they record *where a row came from*, they do not alter it.

They also earn their place immediately, by proving the exact duplicates are real:

```
INV200401: 2 rows at file lines 529 and 673
INV200597: 2 rows at file lines 151 and 674
INV200625: 2 rows at file lines 342 and 675
```

Two distinct physical lines in the source file — so these are genuinely duplicated rows in the
extract, not an artefact of the loader running twice. Without `_FILE_ROW_NUMBER` that would be
an assumption rather than a demonstrated fact.

---

## Design notes

### Why every business column is `VARCHAR`

A date written `30-SEP-2024` and one written `2024-09-30` are both valid raw values. Typing the
column as `DATE` at load time forces a parse decision, and anything that fails to parse is
silently lost or aborts the load. Keeping Bronze as text means the raw feed is always
reproducible, and a failed cast in Silver becomes a *finding* rather than data loss.

### Why the loads are idempotent

Each table is truncated and every `COPY` uses `FORCE = TRUE`. Snowflake's load-history metadata
skips files it has already ingested, so without `FORCE` a re-run after a `TRUNCATE` would leave
the table **empty** — a genuinely confusing failure mode. Phase 8 requires the SQL pipeline to
re-run end to end on a fresh schema, so every script must be safe to execute twice.

### Why `ON_ERROR = 'ABORT_STATEMENT'`

Bronze must be all-or-nothing. A partially loaded table is worse than a failed load, because a
later re-run would make the row-count check pass while the data is quietly wrong.

### Why explicit filenames in each `COPY`

`@BRONZE.RAW_FILES/vendors.csv.gz` rather than a `vendors.*` pattern — the pattern would also
match `vendors_update.csv.gz` and silently merge both vendor extracts into one table, which
would destroy the Phase 4 SCD Type 2 comparison before it ever ran.

---

## One bug found and fixed

The validator's "no typed columns" check failed with a Snowflake syntax error:

```
001003 (42000): SQL compilation error: syntax error line 5 at position 32 unexpected 'TEXT'.
```

Cause: `NOT LIKE '\_%' ESCAPE '\\'` in a Python string renders as `ESCAPE '\'`, and that
backslash escapes the closing quote rather than acting as the escape character. Replaced with
`LEFT(column_name, 1) != '_'`, which needs no escaping at all.

---

**Phase 2 complete. Phase 3 (Silver) is unblocked.**
