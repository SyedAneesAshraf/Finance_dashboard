# Phase 1 — Exit Test Report

**Date:** 2026-08-16
**Goal:** produce 5 source extracts that are realistic *and* carry the required data-quality
defects on purpose.

Run with:

```
.venv\Scripts\python.exe scripts\generate_data.py      # produces the CSVs + ground truth
.venv\Scripts\python.exe scripts\validate_phase1.py    # runs the exit tests
```

`validate_phase1.py` re-derives every claim by parsing the CSVs from disk. It never reads the
generator's in-memory state — if the generator had a bug that made its own summary wrong,
checking against its own variables would just repeat the bug.

---

## Result: 33 / 33 passed

| # | Exit test (from the roadmap) | Result |
|---|---|---|
| 1 | Row counts fall within each target range | ✅ 5/5 files |
| 2 | Every `vendor_id` in POs/Invoices exists in `vendors.csv` | ✅ 0 orphans |
| 3 | 10–15 duplicate-invoice pairs, ground truth saved separately | ✅ 14 pairs, all listed |
| 4 | 15–20 orphan invoices (`po_id` null) | ✅ 18 |
| 5 | ≥10 invoices with >5% variance from PO amount | ✅ 36 POs |
| 6 | A few rows with genuinely blank `payment_terms`/`region` | ✅ 4 and 3 |
| 7 | CSVs open cleanly, expected headers, no encoding errors | ✅ all UTF-8, headers exact |

Plus additional checks for the defects added in Phase 1's design (date formats, casing,
exact-duplicate rows, SCD2 source changes).

---

## Row counts

| File | Rows | Distinct keys | Target | |
|---|---|---|---|---|
| `gl_accounts.csv` | 20 | 20 | 15–25 | ✅ |
| `vendors.csv` | 50 | 50 | 40–60 | ✅ |
| `vendors_update.csv` | 52 | 52 | — 2nd extract | ✅ |
| `purchase_orders.csv` | 480 | 480 | 300–500 | ✅ |
| `invoices.csv` | 675 | 672 | 600–900 | ✅ |
| `payments.csv` | 587 | 584 | 500–800 | ✅ |

Rows exceed distinct keys in invoices and payments by exactly 3 each — that is defect 8, the
double-load artifact Silver must collapse.

## Referential integrity

```
[PASS] every PO vendor_id resolves (480 POs)
[PASS] every invoice vendor_id resolves (675 invoices)
[PASS] every non-null invoice po_id resolves
[PASS] every invoice gl_account_id resolves
[PASS] every payment invoice_id resolves
```

The only deliberately-unresolvable FK is `invoice.po_id IS NULL` — non-PO spend, which is a
finding rather than a defect.

## Date formats (defect 6)

```
all 3062 date values parse
%Y-%m-%d 2174 (71%)   %m/%d/%Y 518 (17%)   %d-%b-%Y 370 (12%)
```

Every value parses under exactly one of the three formats, so Silver's `TRY_TO_DATE` chain has
a well-defined job with no ambiguous cases.

## Aging spread (defect 4)

```
Not Due=19   0-30 Days=16   31-60 Days=13   61-90 Days=11   90+ Days=29
```

All five buckets populated — the roadmap only requires 2–3. 157 payments settled after their
due date, averaging 40.1 days late, worst 107 days.

## SCD Type 2 source (Phase 4 depends on this)

```
[PASS] 2 vendors appear only in extract 2 — V0051, V0052 (exercises MERGE insert path)
[PASS] 6 vendors changed category or payment_terms — V0001, V0002, V0008, V0024, V0026, V0043
[PASS] unchanged vendors carry identical casing across both extracts
[PASS] 5/6 changed vendors have invoices both before and after their change date
```

That last check is the one that matters: without invoices straddling the change date, query 5.6
cannot demonstrate differing terms for the same vendor and SCD2 stays theoretical.

---

## Two defects found and fixed during testing

### 1. Query 5.5 will return 36 exceptions, not 22

The validator reported 36 POs breaching the 5% tolerance against a ground truth of 22. Not a
generator bug — a genuine interaction between two defects.

A near-duplicate invoice quotes the **same `po_id`** as the invoice it duplicates, so it inflates
the total billed against that PO — often far past tolerance. The largest was **+104.3%**, i.e. a
PO billed roughly twice.

This is realistic and worth keeping: a duplicated bill genuinely *is* an over-billing against the
PO. But it means two questions have two answers, so the ground truth now records both:

```json
"count": 22,                              // deliberately mis-priced
"attributable_to_duplicate_invoices": 14,
"expected_query_5_5_exceptions": 36       // what Phase 5 will actually return
```

Recording only the 22 would have made Phase 5 look like it was over-reporting by 64%.

### 2. The generator was not actually deterministic

Design decision D6 promises byte-identical output across runs. It didn't hold:

```
gl_accounts.csv     stable
invoices.csv        stable
payments.csv        stable
purchase_orders.csv stable
vendors.csv         DIFFERS
vendors_update.csv  DIFFERS
```

Cause: the per-vendor noise RNG was seeded with `hash(vendor_id)`. **Python salts string hashing
per process** (`PYTHONHASHSEED`), so every run produced different casing noise.

This was worse than cosmetic. The two vendor extracts are compared attribute-by-attribute by the
Phase 4 SCD2 `MERGE`; drifting noise would make it detect `"Acme Corp"` vs `"ACME CORP"` as a real
change and emit spurious version rows — the exact failure the design was built to avoid.

Fixed by seeding from `zlib.crc32(vendor_id)`, which is stable across processes. Verified across
three independent runs:

```
DETERMINISM: PASS across 3 independent runs
```

---

## Artifacts

| Path | Contents |
|---|---|
| `data_raw/*.csv` | The 6 extract files (5 tables; vendor master extracted twice) |
| `docs/ground_truth.json` | Machine-readable defect counts + KPI cross-check values |
| `docs/04_phase1_ground_truth.md` | Readable defect log — **generated**, never hand-edited |
| `scripts/generate_data.py` | The generator |
| `scripts/validate_phase1.py` | These 33 exit tests |

## KPI cross-check values

Phase 5's SQL and Phase 6's DAX must reproduce these. Recording them now converts "the dashboard
number looks about right" into a pass/fail test.

| Metric | Value |
|---|---|
| Average DPO | 41.83 days |
| Total invoiced (USD) | 6,973,028.88 |
| Total outstanding (USD) | 849,948.61 |
| Total overdue (USD) | 612,596.04 |
| % overdue of outstanding | 72.07% |

FX rates to USD: `USD=1.0`, `EUR=1.08`, `GBP=1.27`, `CAD=0.74`. The Silver layer must hard-code
the same values or every spend figure downstream drifts.

**Phase 1 complete. Phase 2 (Bronze load) is unblocked.**
