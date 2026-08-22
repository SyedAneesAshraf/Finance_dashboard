# Phase 4 — Exit Test Report (Gold Star Schema + SCD Type 2)

**Date:** 2026-08-16
**Goal:** build the reporting-ready star schema, with `DIM_VENDOR` as a genuinely working
Type 2 slowly-changing dimension.

```
.venv\Scripts\python.exe scripts\sf.py run sql\03_gold\01_create_gold_tables.sql
.venv\Scripts\python.exe scripts\sf.py run sql\03_gold\02_load_dimensions.sql
.venv\Scripts\python.exe scripts\sf.py run sql\03_gold\03_scd2_merge_dim_vendor.sql
.venv\Scripts\python.exe scripts\sf.py run sql\03_gold\04_load_facts.sql
.venv\Scripts\python.exe scripts\validate_phase4.py          # 30 automated tests
.venv\Scripts\python.exe scripts\sf.py run sql\03_gold\05_verify_gold.sql
```

---

## Result: 30 / 30 passed

| # | Exit test (from the roadmap) | Result |
|---|---|---|
| 1 | Every fact FK joins cleanly to its dimension | ✅ 8/8 relationships, 0 broken |
| 2 | `DIM_VENDOR` has **more rows than distinct vendors** | ✅ **58 rows / 52 vendors** |
| 3 | 3–5 vendors have 2+ historical rows | ✅ 6 vendors |
| 4 | Query 5.6 returns **different terms for the same vendor** | ✅ 5 vendors |
| 5 | `DIM_DATE` covers the full fact range with no gaps | ✅ 1,461 days, 0 gaps |
| 6 | ER diagram exists and matches the tables built | ✅ [09_phase4_er_diagram.md](09_phase4_er_diagram.md) |

---

## What got built

| Table | Rows | |
|---|---|---|
| `DIM_DATE` | 1,461 | 2024-01-01 → 2027-12-31 |
| `DIM_GL_ACCOUNT` | 20 | SCD Type 1 |
| `DIM_VENDOR` | **58** | for **52** vendors |
| `FACT_PURCHASE_ORDER` | 480 | |
| `FACT_INVOICE` | 672 | 18 with `PO_ID IS NULL` |
| `FACT_PAYMENT` | 584 | |

Nothing was lost between layers, in rows or in money:

```
FACT_TABLE          | SILVER_ROWS | GOLD_ROWS | SILVER_USD | GOLD_USD
FACT_INVOICE        |     672     |    672    | 6973028.93 | 6973028.93
FACT_PAYMENT        |     584     |    584    | 6123080.31 | 6123080.31
FACT_PURCHASE_ORDER |     480     |    480    | 7509380.62 | 7509380.62
```

---

## The MERGE

```
CHANGE_TYPE | VENDORS          number of rows inserted | number of rows updated
CHANGED     |    6                       8             |           6
NEW         |    2
UNCHANGED   |   44
```

8 inserts = 6 new versions + 2 new vendors. 6 updates = the superseded versions being closed.

### The pattern, and why it is needed

A changed vendor requires **two** actions against the target — `UPDATE` the old row and `INSERT`
a new one — but a `MERGE` gives each source row exactly one action. The standard resolution is
to make each changed vendor appear **twice** in the source:

| | `merge_key` | Matches? | Action |
|---|---|---|---|
| row A | existing `VENDOR_KEY` | yes | `UPDATE` — close out |
| row B | `NULL` | no | `INSERT` — new version |

Because `VENDOR_KEY` is never `NULL` in the target, row B can never match anything. That is what
makes the trick reliable rather than merely clever.

### The detail that separates working SCD2 from decorative SCD2

The new version starts on the source system's **`LAST_UPDATE_DATE`** — an Oracle EBS WHO column
— not on the extract date.

V0026 changed terms on **2025-05-10** but was extracted on **2026-08-15**. Using the extract date
would have attributed the *new* terms to all 15 months of invoices in between, and query 5.6
would return confidently wrong answers while every structural test still passed.

---

## The definitive proof (exit test 4)

Same vendor, different invoice dates, **different payment terms**:

| Vendor | Terms | Effective from | to | Invoices | First invoice | Last invoice |
|---|---|---|---|---|---|---|
| V0001 | NET30 | 2022-05-24 | 2025-09-09 | 5 | 2024-11-28 | 2025-09-04 |
| V0001 | **NET45** | 2025-09-10 | 9999-12-31 | 15 | 2025-09-16 | 2026-08-13 |
| V0002 | NET45 | 2024-08-09 | 2025-11-08 | 13 | 2024-11-26 | 2025-11-08 |
| V0002 | **2/10 NET30** | 2025-11-09 | 9999-12-31 | 8 | 2025-11-13 | 2026-07-28 |
| V0008 | NET45 | 2024-08-16 | 2025-10-22 | 7 | 2024-11-07 | 2025-10-11 |
| V0008 | **NET15** | 2025-10-23 | 9999-12-31 | 7 | 2025-12-08 | 2026-06-06 |
| V0024 | NET15 | 2022-09-30 | 2025-06-18 | 8 | 2024-11-25 | 2025-05-23 |
| V0024 | **NET45** | 2025-06-19 | 9999-12-31 | 5 | 2025-11-03 | 2026-08-08 |
| V0043 | NET30 | 2024-01-16 | 2025-09-24 | 6 | 2024-11-21 | 2025-06-16 |
| V0043 | **NET15** | 2025-09-25 | 9999-12-31 | 3 | 2025-12-05 | 2026-05-06 |

A flat or current-only vendor table cannot produce this result at all — it would report NET45 for
every one of V0001's 20 invoices, including the 5 issued while NET30 was in force.

**V0026 appears in `DIM_VENDOR` with two versions but only one row here**, because all of its
invoices fall before its change date. That is correct behaviour, not a gap: the history exists
and is queryable, there is simply no post-change activity to report yet.

### Supporting structural checks

```
0  vendors without exactly one current version
0  overlapping validity ranges        <- an overlap double-counts every invoice in the window
0  gaps between consecutive versions  <- a gap makes invoices vanish from the report entirely
0  invoices matching other than exactly one vendor version
40 invoices bound to a NON-current vendor version
```

That last number is the one that matters most. **40 of 672 invoices report against the terms
that applied at the time**, not today's terms. A dimension carrying the SCD2 columns while every
fact pointed at the current row would look identical in a schema diagram and be worthless in
practice.

---

## Two design decisions worth defending

### Facts carry both a surrogate key and a natural key

`DIM_VENDOR` is SCD2, so `VENDOR_ID` is no longer unique in it. There are two ways to join
around that, and this model does both:

| | Route | Used by |
|---|---|---|
| **A** | Range join on `VENDOR_ID` + `BETWEEN` effective dates | Query 5.6 — shows *how* SCD2 works |
| **B** | Equality join on `VENDOR_KEY`, resolved at load time | Power BI — **required** |

Route B is not optional. **Power BI relationships are equality-only** — there is no way to
express `BETWEEN` in the model. Relating on `VENDOR_ID` against a dimension with repeated values
produces a many-to-many relationship, which is ambiguous and silently yields wrong numbers.

The two routes are cross-checked:

```
0 invoices where the two join routes disagree
```

If they ever diverged, the surrogate keys were resolved to the wrong version and every Power BI
figure would be quietly wrong while the SQL stayed right — the hardest class of bug to notice.

### `LEFT JOIN` into `NOT NULL`, not `INNER JOIN`

Every dimension lookup in the fact loads is a `LEFT JOIN` into a `NOT NULL` column.

The natural way to write these is an `INNER JOIN` — but if one invoice's date fell outside every
version range of its vendor, an `INNER JOIN` would silently **drop** it. The load would report
success, the fact table would be one row short, and the missing money would never surface.

A `LEFT JOIN` produces `NULL` on a miss, which violates `NOT NULL` and aborts the insert naming
the offending column. Silent row loss becomes a loud failure. The row-count reconciliation is
the second line of defence, and both came back clean.

---

## Other checks

**`DIM_DATE`** spans 2024-01-01 → 2027-12-31 against a fact range of 2024-09-01 → 2026-09-27,
with zero gaps in the spine. The padding is deliberate: a date dimension that merely covers the
facts makes `SAMEPERIODLASTYEAR` return blank rather than an honest zero at the edges.

The fact maximum of **2026-09-27** is later than the 2026-08-15 as-of date because it is a *due*
date — invoices issued in August on NET45 terms fall due in late September. Those are the
"Not Due" aging bucket.

**Stored measures** on `FACT_PAYMENT` recompute exactly: 0 mismatches on `DAYS_TO_PAY` and
`DAYS_LATE`, average DPO **41.83 days** matching the ground truth, 157 payments flagged late.

Days-overdue on an *unpaid* invoice is deliberately **not** stored anywhere, because it depends
on `CURRENT_DATE` and would be stale the day after loading. `DAYS_TO_PAY` and `DAYS_LATE` are
functions only of dates already on the row, so they never go stale.

---

**Phase 4 complete. Phase 5 (the 6 analytical queries) is unblocked.**
