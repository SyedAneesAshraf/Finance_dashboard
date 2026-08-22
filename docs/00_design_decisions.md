# Design Decisions (locked in Phase 0)

These are the project-wide constants. Every later phase — data generation, Snowflake SQL,
Power BI, Excel — must stay consistent with this file. If a decision changes, change it
here first, then propagate.

---

## D1. The fictional company

**Meridian Manufacturing Group (MMG)** — a mid-size industrial manufacturer running Oracle
E-Business Suite R12 for Financials and Procurement.

Scope of the dataset: **~$7.0M of AP spend across 50 suppliers over 24 months** — one division's
direct and indirect procurement, not the whole enterprise. Quote that figure rather than a
company-wide revenue number; the generated data is what it is, and a claim the data cannot
support is exactly the kind of thing an interviewer checks.

Why a manufacturer: it makes all five vendor categories in the blueprint (Raw Materials, IT
Services, Logistics, Office Supplies, Consulting) natural rather than arbitrary. A retailer or
SaaS company would not plausibly buy raw materials.

## D2. Time window and the "as-of" date

| Item | Value |
|---|---|
| Data window | **2024-09-01 → 2026-08-15** (~24 months) |
| Project as-of date (`AS_OF_DATE`) | **2026-08-15** |
| `DIM_DATE` range | **2024-01-01 → 2027-12-31** (padded either side of the fact range) |

24 months is deliberate: the Power BI "last 12 months invoiced vs paid" line chart needs a
full 12-month window *plus* a prior-year comparison period for the `SAMEPERIODLASTYEAR` DAX
measure. A 12-month dataset would make month-over-month DAX return blanks for the first year.

**Aging buckets are computed against `CURRENT_DATE`**, per the blueprint's query 5.2. That
means bucket membership drifts as real time passes. The generator therefore spreads unpaid
invoice due dates so that all five buckets (Not Due / 0-30 / 31-60 / 61-90 / 90+) stay
populated for months after generation. `AS_OF_DATE` is recorded in the generation log so any
future discrepancy is explainable rather than mysterious.

## D3. Currency — one currency per vendor

Each vendor bills in exactly **one** currency, fixed for the life of the vendor. A vendor's
POs, invoices, and payments are all in that vendor's currency.

- ~85% USD, plus a minority of EUR, GBP, CAD.
- Bronze/Silver keep the **entered** amount and its `currency` code, exactly as an ERP would.
- Silver adds `amount_usd` using a small static FX rate table, documented in the Silver notes.
- **All Gold facts and all reporting are in USD.**

Why one currency per vendor rather than random per invoice: the 3-way match query (5.5)
compares invoice amount to PO amount, and the duplicate query (5.4) compares amount to amount.
If currency varied per document, both comparisons would silently compare unlike units and
produce garbage exceptions. Fixing currency at the vendor level keeps every like-for-like
comparison valid while still exercising real multi-currency handling.

This mirrors Oracle EBS's *entered currency* vs *functional currency* distinction, which is a
genuine talking point rather than invented complexity.

## D4. Payment terms

`NET15`, `NET30`, `NET45`, `NET60`, `2/10 NET30`. `due_date = invoice_date + N days`, where
`2/10 NET30` is treated as net 30 for the due date (the 2% early-payment discount is not
modeled — noted here so the omission is deliberate, not an oversight).

## D5. Document chain

```
PURCHASE ORDER  →  GOODS RECEIPT  →  INVOICE  →  PAYMENT
   po_id            (date on PO)      invoice_id   payment_id
   vendor_id                          po_id (FK, nullable)
                                      vendor_id (FK)
```

`invoice.po_id` is **nullable by design** — a null is non-PO spend, which is business question
4/5 territory, not a data error. Every other FK must resolve.

## D6. Determinism

The generator is seeded (`SEED = 20260815`). Re-running it reproduces byte-identical CSVs.
This matters because the Phase 1 ground-truth flaw log must keep matching the data through
Phases 3 and 5; a fresh random dataset each run would invalidate every downstream exit test.

## D7. Null strategy in Silver (decided now, applied in Phase 3)

Missing `payment_terms` and `region` are **imputed to `'Unknown'` AND flagged** via a
`data_quality_flag` column. The roadmap says "pick one approach and document why" — the reason
for doing both is that imputing alone silently destroys the evidence that a field was ever
missing, while flagging alone leaves nulls that break `GROUP BY` and Power BI slicers. Keeping
both gives clean reporting *and* an auditable trail of what was repaired.

## D8. What Silver must NOT clean

Silver removes **exact whole-row duplicates only** (an accidental double-load artifact). It
must preserve, untouched:

- near-duplicate invoices (different `invoice_id`, same vendor/amount, dates within 3 days)
- orphan invoices (`po_id IS NULL`)
- invoice-vs-PO amount variances
- late payments

Those four are the *findings* of Phase 5's queries. Cleaning them away in Phase 3 would leave
Phase 5 with nothing to detect — the single most likely way to break this project.
