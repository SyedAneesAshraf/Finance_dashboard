# Phase 1 — Ground Truth (injected defect log)

> **Generated file — do not edit by hand.** Written by `scripts/generate_data.py`.
> Seed `20260815`, as-of date `2026-08-15`, window `2024-09-01` to `2026-08-15`.

This is the reference Phases 3 and 5 validate against. Phase 3 must prove it did not
clean these defects away; Phase 5 must prove its queries actually find them.

---

## Row counts

| File | Rows | Distinct keys | Target |
|---|---|---|---|
| `gl_accounts.csv` | 20 | 20 | 15–25 |
| `vendors.csv` | 50 | 50 | 40–60 |
| `vendors_update.csv` | 52 | 52 | — (2nd extract) |
| `purchase_orders.csv` | 480 | 480 | 300–500 |
| `invoices.csv` | 675 | 672 | 600–900 |
| `payments.csv` | 587 | 584 | 500–800 |

Invoice and payment file rows exceed distinct keys because of defect 8 below.

---

## Injected defects

### 1. Near-duplicate invoices — 14 pairs

Same vendor, same amount, invoice dates within 3 days, different `invoice_id`.
**Silver must preserve these.** Query 5.4 detects them.

| Original | Duplicate | Vendor | Amount | Duplicate date |
|---|---|---|---|---|
| `INV200628` | `INV200672` | V0024 | 8,928.64 | 2024-11-27 |
| `INV200250` | `INV200663` | V0040 | 1,552.99 | 2025-01-04 |
| `INV200081` | `INV200669` | V0009 | 5,523.49 | 2025-01-29 |
| `INV200290` | `INV200660` | V0048 | 3,873.59 | 2025-05-13 |
| `INV200160` | `INV200661` | V0048 | 3,074.08 | 2025-05-26 |
| `INV200259` | `INV200671` | V0016 | 23,938.61 | 2025-08-08 |
| `INV200079` | `INV200666` | V0042 | 22,823.14 | 2025-08-12 |
| `INV200158` | `INV200670` | V0018 | 1,471.28 | 2025-08-23 |
| `INV200217` | `INV200664` | V0009 | 3,103.72 | 2025-09-23 |
| `INV200084` | `INV200659` | V0007 | 3,831.27 | 2025-10-20 |
| `INV200495` | `INV200665` | V0035 | 2,335.37 | 2026-02-14 |
| `INV200053` | `INV200668` | V0040 | 1,415.49 | 2026-02-24 |
| `INV200363` | `INV200667` | V0016 | 4,914.88 | 2026-06-03 |
| `INV200126` | `INV200662` | V0001 | 7,731.38 | 2026-08-13 |

### 2. Orphan invoices — 18

Invoices with no `po_id` — non-PO spend, a genuine audit red flag rather than a data error.

```
  INV200657, INV200645, INV200642, INV200653, INV200648, INV200643, INV200651, INV200646, INV200647, INV200655, INV200654, INV200649, INV200658, INV200641, INV200644, INV200652, INV200650, INV200656
```

### 3. Invoice-vs-PO variance beyond 5% — 22 deliberately mis-priced

- **22** POs were deliberately invoiced outside the ±5% tolerance.
- **14** more are pushed past tolerance by a defect-1 duplicate quoting the same `po_id`.
- **Query 5.5 should therefore return 36 exceptions**, not 22.

That overlap is intentional and worth being able to explain: a duplicated bill really does
show up as over-billing against the PO. The two controls catch the same event from
different angles.

### 4. Late payments — 157 (26.9% of payments)

- 133 are more than 10 days late
- average 40.1 days late, worst 107 days

### 5. Missing fields

- `payment_terms` blank for **4** vendors: `V0004`, `V0012`, `V0032`, `V0038`
- `region` blank for **3** vendors: `V0028`, `V0039`, `V0042`

### 6. Mixed date formats

`YYYY-MM-DD`, `MM/DD/YYYY`, and Oracle's default `DD-MON-YYYY`, consistent within a row
and varying between rows — as happens when extracts from EBS instances with different
`NLS_DATE_FORMAT` settings are concatenated.

### 7. Text casing and whitespace noise

Roughly 30% of text values are lower-cased, upper-cased, or carry leading/trailing spaces.

### 8. Exact whole-row duplicates — 6

Byte-identical rows repeating an existing primary key — a double-load artifact.
**Silver must remove these**, unlike defect 1.

- invoices: `INV200401`, `INV200597`, `INV200625`
- payments: `PAY300143`, `PAY300480`, `PAY300016`

---

## Unpaid AP aging profile

As of 2026-08-15, 88 invoices are unpaid.

| Bucket | Invoices | Amount (entered currency) |
|---|---|---|
| Not Due | 19 | 234,651.19 |
| 0-30 Days | 16 | 181,436.17 |
| 31-60 Days | 13 | 69,315.79 |
| 61-90 Days | 11 | 86,643.83 |
| 90+ Days | 29 | 264,943.79 |

> Buckets are computed against `CURRENT_DATE`, so membership shifts as real time passes.
> The spread is wide enough that all five stay populated for months after generation.

---

## SCD Type 2 source changes

6 vendors changed between the two extracts. 2 vendors are new in extract 2 (V0051, V0052), exercising the MERGE insert path.

| Vendor | Change date | Category | Payment terms |
|---|---|---|---|
| `V0001` | 2025-09-10 | Raw Materials → IT Services | NET30 → NET45 |
| `V0002` | 2025-11-09 | unchanged | NET45 → 2/10 NET30 |
| `V0008` | 2025-10-23 | Office Supplies → Raw Materials | NET45 → NET15 |
| `V0024` | 2025-06-19 | Consulting → Logistics | NET15 → NET45 |
| `V0026` | 2025-05-10 | unchanged | NET45 → NET30 |
| `V0043` | 2025-09-25 | unchanged | NET30 → NET15 |

`DIM_VENDOR` should therefore end up with **58 rows for 52 vendors** — the proof SCD2 is real rather than decorative.

---

## KPI cross-check

Phase 5's SQL and Phase 6's DAX must reproduce these. Recording them turns "the number looks plausible" into a pass/fail check.

| Metric | Value |
|---|---|
| Average DPO | 41.83 days |
| Total invoiced (USD) | 6,973,028.93 |
| Total outstanding (USD) | 849,948.62 |
| Total overdue (USD) | 612,596.04 |
| % overdue of outstanding | 72.07% |
| Paid invoices | 584 |
| Unpaid invoices | 88 |

FX rates to USD: `USD=1.0`, `EUR=1.08`, `GBP=1.27`, `CAD=0.74`. The Silver layer hard-codes the same values.

---

## Purchase orders with no invoice — 60

Open commitments. These are the PO-side exceptions: goods ordered and in some cases
received, but never billed.
