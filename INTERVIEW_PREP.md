# Interview Prep — Finance & Procurement Analytics Platform

Quick-reference for talking about this project in an interview. Read `README.md` /
`WHAT_IS_THIS.md` for the full writeup — this file is the condensed, talk-track version.

---

## 30-second pitch

"I built an end-to-end analytics platform that simulates what Dataplatr actually does for
clients — taking messy data out of an enterprise system like Oracle EBS and turning it into a
governed warehouse and a dashboard. I invented a fictional manufacturer, Meridian Manufacturing
Group, generated realistic-but-flawed AP/Procurement data for it, built a Snowflake medallion
pipeline (Bronze/Silver/Gold) with a star schema and a Type-2 slowly changing vendor dimension,
wrote six analytical SQL queries, and put a Power BI dashboard and an Excel companion on top.
Every artifact in the project traces back to one of five real Finance questions — nothing was
built just to look impressive."

## Elevator-pitch version (10 seconds)

"A simulated Oracle EBS finance dataset, cleaned through a Snowflake Bronze/Silver/Gold pipeline
into a star schema, then answered with SQL and Power BI — vendor spend concentration, days
payable outstanding, AP aging, duplicate invoices, and 3-way match."

---

## The business framing (memorize this — it's the "why")

**Fictional company:** Meridian Manufacturing Group (MMG), a mid-size manufacturer on Oracle EBS
R12. ~$7.0M of AP spend across 50 suppliers over 24 months (one division's procurement, not a
company-wide number — deliberately scoped so no claim outruns the data).

**The pain it replaces:** Finance currently answers payables questions by hand — exporting EBS
extracts, reconciling in spreadsheets, redoing it every month. Slow, error-prone, no live view of
cash exposure.

**The five questions everything traces back to:**
1. Which vendors do we spend the most with — too concentrated? (supplier risk)
2. How long do we take to pay (DPO) — paying late? (penalty / relationship risk)
3. How much AP is overdue, by aging bucket? (cash-flow visibility)
4. Are there duplicate/suspicious invoices? (fraud/error control)
5. Do POs, receipts, and invoices match (3-way match)? (procurement/audit control)

If an interviewer asks "why does X exist in your project," the answer should trace back to one
of these five. If it doesn't, say so honestly — the project deliberately avoided scope creep.

---

## Architecture (draw this if given a whiteboard)

```
5 synthetic CSVs (Python/Faker, seeded, deliberately messy)
        │
        ▼
SNOWFLAKE
  BRONZE  — raw, untouched, all VARCHAR (preserves source fidelity)
  SILVER  — typed, standardized, deduplicated, FX-converted, DQ-flagged
            (defects that are *findings* are deliberately NOT cleaned)
  GOLD    — star schema: 3 facts (Invoice, Payment, PO) + 3 dims
            (Vendor SCD2, Date, GL Account)
        │
        ├──► 6 analytical SQL queries
        └──► Power BI (2 pages, DAX) + Excel budget-vs-actual companion
```

**Why layered (Bronze/Silver/Gold):** Bronze preserves exactly what the source sent, so any
dashboard number traces back to a raw row. Silver establishes trust once instead of
re-litigating it in every downstream query. Gold is shaped for consumption — a Finance user
joins nothing and writes no SQL.

---

## Key numbers to have cold

| Metric | Value |
|---|---|
| Total invoiced (USD) | $6,973,028.93 |
| Total outstanding | $849,948.62 |
| Total overdue | $612,596 (query 5.1) / $612–615k range depending on cut |
| Average DPO | 41.83 days |
| Vendors | 50 sources → 52 in 2nd extract → 58 `DIM_VENDOR` rows (SCD2) |
| Invoices | 675 raw → 672 after dedup (3 exact dupes removed) |
| Payments | 587 raw → 584 after dedup |
| Purchase orders | 480 |
| Near-duplicate invoice pairs (fraud finding) | 14 |
| 3-way match exceptions | 36 (22 genuine mis-pricing + 14 caused by the duplicate invoices) |
| POs invoiced with no goods receipt | 32 |
| Orphan (non-PO) invoices | 18 |
| Vendor spend concentration | 15 of 49 vendors (31%) carry 80% of spend |
| Payments beyond agreed terms | 34 of 48 vendors |
| 90+ day overdue bucket | ~$265k, ~31% of all outstanding, worst case 616 days late |
| Test coverage | 100+ automated exit tests across phases 0–5, all passing |

You don't need to recite these — just don't be caught flat-footed if asked "so what did you
actually find?"

---

## The five questions, answered (the "so what")

| # | Question | Answer |
|---|---|---|
| 1 | Vendor concentration | **Yes** — top 15 of 49 vendors (31%) carry 80% of spend; top 2 alone are 23%. The single largest vendor ($841k) even has an `Unknown` region — a master-data gap on the company's most important relationship. |
| 2 | DPO / late payment | **41.83 days average**; 34 of 48 vendors paid beyond their agreed terms. |
| 3 | Aging / overdue | **$265k (31% of all outstanding) is 90+ days overdue**, averaging 384 days late, worst case 616 days. |
| 4 | Duplicate/fraud | **14 duplicate invoice pairs** found (same vendor, same amount, within 3 days) — a fraud control auditors ask for by name. |
| 5 | 3-way match | **36 exceptions** out of 420 invoiced POs, plus 32 invoiced with no goods receipt on file at all. |

---

## Design decisions worth explaining (these show judgment, not just execution)

**Why one currency per vendor, not random per invoice.** The duplicate-detection and 3-way-match
queries compare amount to amount. If currency varied per document those comparisons would
silently compare unlike units. Fixing currency at the vendor level (mirroring Oracle EBS's
*entered* vs *functional* currency) keeps every comparison valid while still exercising real
multi-currency handling (~85% USD, rest EUR/GBP/CAD, converted via a Silver FX table).

**Why Silver doesn't clean everything.** The organizing principle: *"Silver makes the data
trustworthy without making it dishonest."* Exact whole-row duplicates (a double-load artifact)
are removed. But near-duplicate invoices, orphan invoices, PO/invoice variances, and late
payments are deliberately **preserved** — those aren't data errors, they're the findings the
five business questions are built to catch. Cleaning them away would be the single most likely
way to break the whole project.

**Why nulls are imputed *and* flagged, not just one or the other.** Missing `payment_terms` /
`region` become `'Unknown'` *and* get a `data_quality_flag`. Impute-only silently destroys the
evidence anything was missing; flag-only leaves `NULL`s that break `GROUP BY` and Power BI
slicers. Doing both gives clean reporting and an auditable trail — verified zero imputations
without a flag.

**Why the vendor dimension is SCD Type 2.** Payment terms and category change over time (6 of 52
vendors changed between two source extracts). A flat current-only vendor table can't tell you
what terms applied *when an invoice was issued* — judging a June invoice against a September
rate change scores a late payment as on-time. `DIM_VENDOR` carries `EFFECTIVE_START/END_DATE`
and a range join (`invoice_date BETWEEN start AND end`) binds each fact to the version in force
at the time. 40 of 672 invoices bind to a non-current vendor version — that's the proof the
history is actually load-bearing, not decorative.

**Why `PO_ID` is nullable and never backfilled.** A null PO is non-PO spend — a genuine audit red
flag, not missing data. Backfilling a placeholder would erase the finding.

**Why 3-way match aggregates invoices per PO instead of comparing 1:1.** A PO is often billed
across several staged deliveries. Comparing one partial invoice to the full PO value would flag
every legitimate staged delivery as a massive under-billing and the report would be ignored
within a week. The control that matters is the cumulative invoiced total vs. the PO.

**Why the duplicate-detection self-join uses `a.invoice_id < b.invoice_id`.** Without the strict
inequality a self-join returns every pair twice (A-B and B-A) plus self-matches. One condition
does three jobs: no self-matches, no mirrored duplicate pairs, and it deterministically picks
which invoice is "the original." Getting this wrong would report 28 duplicates instead of 14 —
destroying the credibility of the control.

**Why dates are parsed with explicit `TRY_TO_DATE` masks instead of Snowflake's auto-detect.**
Source data has three date formats (mirroring EBS extracts from instances with different
`NLS_DATE_FORMAT` settings). Auto-detection is lenient and guesses; explicit masks in a
`COALESCE` chain mean each value parses under exactly one format or not at all — deterministic
and auditable.

**Why 24 months of data, not 12.** The Power BI "last 12 months invoiced vs. paid" trend needs a
full 12-month window *plus* a prior-year comparison period for a `SAMEPERIODLASTYEAR`-style DAX
measure. A 12-month dataset would return blanks for the whole first year.

**Why the data generator is seeded.** Reproducibility — the Phase 1 ground-truth defect log has
to keep matching the data through cleaning and querying. A fresh random dataset per run would
invalidate every downstream test.

---

## Anticipated questions & prepared answers

**"Walk me through the architecture."**
→ Use the Bronze/Silver/Gold diagram above. Emphasize *why* each layer exists, not just what's
in it.

**"What was the hardest part?"**
→ Good honest answer: getting Silver's cleaning rules right without accidentally cleaning away
the findings Phase 5 needed. It's tempting to "fix" a duplicate invoice or a late payment because
it looks like bad data — but those are the point. Also good: the SCD2 range join needing
non-overlapping, non-gapped effective-date ranges, verified with an explicit 0-overlap/0-gap
check.

**"How did you validate correctness?"**
→ Every phase has an automated exit-test script (`scripts/validate_phaseN.py`) checked against a
recorded ground truth (`docs/04_phase1_ground_truth.md`) generated alongside the synthetic data
itself — e.g. "14 near-duplicate pairs were injected, query 5.4 must recover exactly those 14
identities, not just the count." 100+ tests pass across phases 0–5.

**"Is this real company data?"**
→ No — be upfront. It's a synthetic dataset generated with Python/Faker, seeded for
reproducibility, with deliberately injected data-quality defects (duplicates, missing fields,
mixed date formats, late payments, mis-priced invoices) that mirror what a real Oracle EBS
extract looks like. The point was to build something with real engineering decisions to talk
about, not to fabricate a real client relationship.

**"Why Snowflake specifically?"**
→ Fits the "pre-built data models on top of enterprise apps" pattern Dataplatr's actual business
follows, and it makes Bronze/Silver/Gold and `MERGE`-based SCD2 natural to demonstrate.

**"What would you do differently / what's not done yet?"**
→ Be honest about current state (see below) — the Power BI dashboard's semantic model, DAX
measures, and both report pages are built, but hadn't been opened in Power BI Desktop to confirm
render/refresh at the time of writing, and screenshots weren't yet captured. If asked, say
exactly where it stands rather than overclaiming a finished dashboard demo.

**"What don't you like about the design / what's a known limitation?"**
→ Good honest answers already documented in the project itself:
- Vendor name casing normalization would mis-title-case a genuinely all-caps trading name (e.g.
  `IBM` → `Ibm`); no such vendor exists in this dataset, but production would need an exceptions
  list.
- The 2% early-payment discount implied by `2/10 NET30` terms isn't modeled — due dates are
  computed as straight NET30. Noted as a deliberate scope cut, not an oversight.
- FX rates are static hard-coded snapshots, not point-in-time historical rates.

---

## Current build status (know this so you don't overclaim)

| Phase | Status |
|---|---|
| Data generation (5 CSVs, injected defects) | ✅ Done, tested |
| Bronze layer | ✅ Done, tested |
| Silver layer (cleaning, FX, DQ flags) | ✅ Done, tested |
| Gold star schema + SCD2 | ✅ Done, tested |
| 6 analytical SQL queries | ✅ Done, tested (26/26 exit tests) |
| Power BI dashboard | 🟡 Semantic model + both pages built (measures, relationships, visuals) — not yet opened in Power BI Desktop to confirm render/refresh; no screenshots yet |
| Excel budget-vs-actual companion | ✅ Done, verified via COM automation |
| Final docs/README packaging | 🟡 Mostly done |

If asked to demo the dashboard live and it hasn't been opened/confirmed recently, say so rather
than guessing it works.

---

## Tech stack, precisely

Python 3.13 (Faker, pandas, numpy) → Snowflake SQL (CTEs, window functions, `MERGE`, `QUALIFY`,
`TRY_TO_DATE`) → Power BI Desktop + DAX → Excel (pivot tables, `SUMIFS`/`XLOOKUP`, conditional
formatting) → Git/GitHub for version control.
