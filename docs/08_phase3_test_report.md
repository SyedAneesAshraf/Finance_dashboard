# Phase 3 — Exit Test Report (Silver Layer)

**Date:** 2026-08-16
**Goal:** turn raw Bronze data into a trustworthy, standardized dataset — without destroying
the defects Phase 5 exists to find.

```
.venv\Scripts\python.exe scripts\sf.py run sql\02_silver\01_create_silver_tables.sql
.venv\Scripts\python.exe scripts\sf.py run sql\02_silver\02_load_silver.sql
.venv\Scripts\python.exe scripts\validate_phase3.py          # 33 automated tests
.venv\Scripts\python.exe scripts\sf.py run sql\02_silver\03_verify_silver.sql
```

---

## Result: 33 / 33 passed

| # | Exit test (from the roadmap) | Result |
|---|---|---|
| 1 | All date columns are true `DATE` type (no strings) | ✅ 9/9 columns |
| 2 | Invoices with unresolvable `vendor_id` returns 0 | ✅ 0, and 4 more FK checks |
| 3 | Intentional Phase 1 flaws **still present** in Silver | ✅ all five, exact counts |
| 4 | Markdown note describing each cleaning rule and why | ✅ [07_phase3_cleaning_rules.md](07_phase3_cleaning_rules.md) |

---

## The two-sided test

The failure mode this phase invites is a Silver layer that scores beautifully on cleanliness
*because* it destroyed the evidence. So the suite asserts both directions.

### Silver DID clean

```
invoices 675 -> 672      3 exact duplicate rows removed
payments 587 -> 584      3 exact duplicate rows removed
CATEGORY: 15 raw variants -> 5 canonical, none 'Unknown'
0 values still carry untrimmed whitespace          (Bronze had 51 in PO_STATUS alone)
0 invoices with a non-canonical status
0 NULLs left in standardized vendor columns
all 9 date columns are true DATE
```

### Silver did NOT over-clean

```
defect                       found   expected
near-duplicate invoice pairs    14      14
non-PO invoices (po_id NULL)    18      18
POs breaching 5% tolerance      36      36
payments after due date        157     157
unpaid invoices                 88      88
```

Every count matches [`ground_truth.json`](ground_truth.json) exactly. Had the dedup partitioned
on `(vendor_id, amount, invoice_date)` instead of on every column, the first row would read 0
and query 5.4 would have nothing to detect — while Silver looked *cleaner* for it.

---

## Imputation with an audit trail

```
imputed_terms | imputed_region | imputed_without_flag | nulls_remaining
      4       |       3        |          0           |        0
```

The `imputed_without_flag = 0` column is the one that matters: no value was quietly replaced
without leaving a trace.

| Vendor | Category | Region | Terms | Flag |
|---|---|---|---|---|
| V0004 | Raw Materials | North America | **Unknown** | `MISSING_PAYMENT_TERMS` |
| V0012 | Logistics | LATAM | **Unknown** | `MISSING_PAYMENT_TERMS` |
| V0028 | Consulting | **Unknown** | NET60 | `MISSING_REGION` |
| V0032 | IT Services | North America | **Unknown** | `MISSING_PAYMENT_TERMS` |
| V0038 | IT Services | North America | **Unknown** | `MISSING_PAYMENT_TERMS` |
| V0039 | Consulting | **Unknown** | NET60 | `MISSING_REGION` |
| V0042 | Consulting | **Unknown** | NET45 | `MISSING_REGION` |

Imputing alone would make these indistinguishable from vendors genuinely categorised Unknown.
Flagging alone would leave `NULL`s that drop out of `GROUP BY` and appear as blank slicer
entries in Power BI. Doing both gives clean reporting *and* an auditable trail.

---

## FX conversion matches the ground truth to the cent

```
TOTAL_INVOICED_USD | TOTAL_OUTSTANDING_USD | UNCONVERTED_NON_USD
    6,973,028.93   |       849,948.62      |          0
```

| Currency | Invoices | Entered | USD |
|---|---|---|---|
| USD | 589 | 6,238,517.63 | 6,238,517.63 |
| GBP | 51 | 504,116.71 | 640,228.26 |
| CAD | 10 | 111,421.63 | 82,452.01 |
| EUR | 22 | 10,954.65 | 11,831.03 |

Average DPO also reproduces exactly: **41.83 days**, matching the generator.

**A precision issue found here.** The generator originally summed unrounded FX products and
rounded once at the end, while Silver rounds each row then sums. That left the totals a few
cents apart. Per-row rounding is the correct definition — `AMOUNT_USD` is a concrete stored
value per invoice, so that *is* the number in the fact table. The generator was corrected to
match, and because the generator is deterministic, re-running rewrote only `ground_truth.json`;
all six CSVs stayed byte-identical, so Bronze and Silver needed no reload.

A few cents of drift is worse than a large discrepancy: it looks like a rounding bug worth
hunting rather than a definition difference.

---

## Casing rule, working as designed

```
johnson ltd                  ->  Johnson Ltd                   normalized
STEVENSON, WALSH AND PIERCE  ->  Stevenson, Walsh And Pierce   normalized
Arnold, Mitchell and Jones   ->  Arnold, Mitchell and Jones    left alone
```

A blanket `INITCAP` would have rewritten the third one to `... And Jones` — damaging good data
to fix bad data. Only names that are *entirely* upper or lower case are treated as noise.

The known limitation is visible in the SCD2 output: `MORROW, MILLER AND BROOKS` became
`Morrow, Miller And Brooks`. A genuinely all-caps trading name would be title-cased the same
way. No such vendor exists here; a production system would carry an exceptions list.

---

## SCD Type 2 source is ready

```
changed_vendors | name_only_differences | new_vendors
       6        |          0            |      2
```

| Vendor | Category | Terms | Change date |
|---|---|---|---|
| V0001 | Raw Materials → IT Services | NET30 → NET45 | 2025-09-10 |
| V0002 | *unchanged* | NET45 → 2/10 NET30 | 2025-11-09 |
| V0008 | Office Supplies → Raw Materials | NET45 → NET15 | 2025-10-23 |
| V0024 | Consulting → Logistics | NET15 → NET45 | 2025-06-19 |
| V0026 | *unchanged* | NET45 → NET30 | 2025-05-10 |
| V0043 | *unchanged* | NET30 → NET15 | 2025-09-25 |

**`name_only_differences = 0` is the load-bearing check.** Before standardization, the two
extracts carried identical cosmetic noise; after it, no vendor differs by name alone. The Phase 4
`MERGE` therefore cannot mistake `"ACME CORP"` vs `"Acme Corp"` for a real attribute change and
spawn a spurious version row.

This is precisely why the `MERGE` runs against **Silver**, never Bronze.

---

## One testing decision worth noting

The roadmap's exit test is written as:

```sql
SELECT COUNT(*) FROM SILVER.INVOICES
WHERE vendor_id NOT IN (SELECT vendor_id FROM SILVER.VENDORS)
```

Both the SQL and Python suites use `NOT EXISTS` instead. If the subquery ever returned a single
`NULL`, `NOT IN` evaluates to `UNKNOWN` for *every* row and reports zero orphans — the test
passes by accident, which is the worst way for a test to pass. `NOT EXISTS` has no such trap.

---

**Phase 3 complete. Phase 4 (Gold star schema + SCD Type 2) is unblocked.**
