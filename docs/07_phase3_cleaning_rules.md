# Phase 3 — Silver Cleaning Rules

Every rule applied in [`sql/02_silver/02_load_silver.sql`](../sql/02_silver/02_load_silver.sql),
and the reasoning behind it.

The organising principle: **Silver makes the data trustworthy without making it dishonest.**
Anything that is genuinely wrong gets fixed. Anything that is genuinely *true but unwelcome* —
a duplicate bill, a non-PO invoice, an over-billed PO, a late payment — survives untouched,
because those are findings the business needs, not defects to be tidied away.

---

## R1 — Date parsing

**Rule:** every date column is resolved through a `TRY_TO_DATE` chain:

```sql
COALESCE(TRY_TO_DATE(col, 'YYYY-MM-DD'),
         TRY_TO_DATE(col, 'MM/DD/YYYY'),
         TRY_TO_DATE(col, 'DD-MON-YYYY'))
```

**Why:** the source carries three formats, because extracts from EBS instances with different
`NLS_DATE_FORMAT` settings were concatenated. `TRY_TO_DATE` returns `NULL` instead of raising,
so the `COALESCE` tries each mask in turn and the first that fits wins.

**Why not `TO_DATE` with auto-detection:** Snowflake's automatic format detection is lenient
and would resolve ambiguous strings by guessing. Explicit masks mean each value parses under
exactly one format or not at all — verified: all 3,062 date values parse, none under two masks.

**Why the order is safe:** the three formats are mutually exclusive by shape. `2024-09-30` cannot
parse as `MM/DD/YYYY`, and `30-SEP-2024` cannot parse as either of the other two. There is no
`03/04/2025`-style day/month ambiguity because only one slash-format is used.

**Failure handling:** the transaction date columns are `NOT NULL`, so an unparseable date would
abort the load rather than silently insert a row with a missing date. A pre-flight query at the
top of the load script counts unparseable values first, so the problem surfaces *before* the
load while the original string is still readable in Bronze.

## R2 — Whitespace

**Rule:** `TRIM()` on every text column.

**Why:** Bronze deliberately preserved 51 `PO_STATUS` values with trailing spaces (`TRIM_SPACE =
FALSE` on the file format). `'Open'` and `'Open  '` are the same status, but they group as two
rows, render as two slicer entries in Power BI, and break equality joins.

## R3 — Vendor name casing

**Rule:** normalize only names that are *entirely* upper or *entirely* lower case:

```sql
CASE WHEN name = UPPER(name) OR name = LOWER(name)
     THEN INITCAP(TRIM(name))
     ELSE TRIM(name)
END
```

**Why not a blanket `INITCAP`:** it would rewrite correctly-cased names too.
`'Arnold, Mitchell and Jones'` would become `'Arnold, Mitchell And Jones'` — damaging good data
to fix bad data. Names that are uniformly one case are unambiguously noise; mixed-case names are
probably deliberate and are left alone.

**Known limitation:** a genuinely all-caps trading name (`IBM`, `BASF`) would be title-cased to
`Ibm`. No such vendor exists in this dataset. A production system would carry an exceptions list.

## R4 — Controlled vocabularies

**Rule:** `category`, `region`, `status`, `po_status`, and `payment_method` are mapped through an
explicit `CASE` on `UPPER(TRIM(x))` to a canonical value. Anything unrecognised becomes
`'Unknown'` **and** is flagged.

**Why not `INITCAP`:** `INITCAP('IT SERVICES')` gives `'It Services'`. Title-casing is a
formatting operation; mapping to a controlled vocabulary is a *validation* operation, and only
the second one can reject a value it does not recognise.

**Why the `ELSE 'Unknown'` branch matters:** if a seventh vendor category ever appears in the
source, it surfaces as a flagged row rather than silently entering the warehouse as a new
dimension member. Verified: 15 raw `CATEGORY` variants collapsed to exactly 5 canonical values,
with zero landing in `'Unknown'`.

## R5 — Deduplication (the consequential one)

**Rule:** remove exact whole-row duplicates only.

```sql
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY <every business column>
    ORDER BY _FILE_ROW_NUMBER
) = 1
```

**Why partition on every column:** it collapses only byte-identical rows — defect 8, the
double-load artefact. Result: invoices 675 → 672, payments 587 → 584. Exactly 3 each, matching
the ground truth.

**What it deliberately does not do:** partitioning on `(vendor_id, amount, invoice_date)` would
also collapse the 14 near-duplicate pairs. Those have *different* invoice IDs and are the entire
subject of query 5.4. Removing them here would make Silver look cleaner while leaving Phase 5
nothing to detect — the single most likely way to break this project.

| | Near-duplicate (defect 1) | Exact duplicate (defect 8) |
|---|---|---|
| Invoice ID | different | identical |
| Cause | vendor double-billed, or AP keyed it twice | extract ran twice |
| Silver | **preserved** — 14 pairs still detectable | **removed** — 3 rows |
| Meaning | real money at risk | a loading artefact worth nothing |

**Why `ORDER BY _FILE_ROW_NUMBER`:** it makes the choice deterministic. Without an explicit
order, which copy survives is arbitrary and the load stops being reproducible.

## R6 — Nulls: impute *and* flag

**Rule:** missing `payment_terms` and `region` become `'Unknown'`, and the row records
`MISSING_PAYMENT_TERMS` / `MISSING_REGION` in `DATA_QUALITY_FLAGS` with `IS_DQ_FLAGGED = TRUE`.

**Why both:** the roadmap says pick one approach. Doing both is the reasoned choice, because each
alone fails in a specific way:

- **Impute only** — the value becomes indistinguishable from a vendor genuinely categorised as
  Unknown. The evidence that anything was ever missing is destroyed, and no one can audit or
  fix the source.
- **Flag only** — `NULL`s survive into reporting, where they silently drop out of `GROUP BY`
  results and produce a blank entry in every Power BI slicer.

Imputing gives clean reporting; flagging preserves the audit trail. Verified: 4 vendors with
imputed terms and 3 with imputed region, **zero** imputations without a flag.

**Why `PAYMENT_TERMS_DAYS` stays `NULL` when terms are unknown:** imputing a default of 30 would
invent a due-date obligation the contract does not support. `'Unknown'` is a legitimate label
for a category; 30 days is a false fact.

## R7 — `PO_ID` stays nullable

**Rule:** a null `po_id` is preserved as `NULL` and flagged `NON_PO_SPEND`.

**Why:** this is the one FK deliberately left unresolvable. Non-PO spend is a real audit red flag
— money leaving the company without a purchase order — and it is what business question 5 asks
about. Backfilling a placeholder PO would erase the finding. All 18 survive.

## R8 — Currency conversion

**Rule:** `AMOUNT_USD = ROUND(amount × rate, 2)`, joined from the `SILVER.FX_RATES` reference
table. The entered amount and currency are both retained alongside it.

**Why convert:** vendors bill in USD, EUR, GBP, and CAD. `SUM(amount)` across them adds unlike
units and produces a number that means nothing. Every Gold fact and every dashboard figure is in
USD.

**Why keep the original:** Oracle EBS distinguishes *entered* currency from *functional*
currency, and auditors reconcile against what the vendor actually invoiced. Discarding the
entered amount would make the warehouse unreconcilable against the source.

**Why a table rather than an inline `CASE`:** the rate would otherwise be repeated in three
places (POs, invoices, payments) and could drift between them. One join, one source of truth.

**Why rounded per row:** the per-row `AMOUNT_USD` is the value that reaches the fact table, so
rounding must happen there. Summing unrounded products and rounding at the end would leave every
total a few cents adrift from the sum of its own rows — a discrepancy small enough to look like a
bug worth hunting rather than a definition difference.

The rates are hard-coded snapshots and **must** match `FX_TO_USD` in
[`scripts/generate_data.py`](../scripts/generate_data.py). Verified: Silver reproduces the
ground-truth total of **6,973,028.93 USD** exactly.

## R9 — Rules deliberately NOT applied

| Not done | Why |
|---|---|
| Correct invoice amounts toward their PO | The variance *is* the finding — query 5.5 |
| Adjust late payment dates | Lateness *is* the finding — DPO and aging |
| Collapse near-duplicate invoices | Query 5.4 |
| Backfill missing `goods_receipt_date` | Flagged `NO_GOODS_RECEIPT`; a fabricated receipt date would defeat the 3-way match |
| Recompute `status` from payments | Silver preserves source semantics; derivation belongs in Gold |

---

## Data quality flags emitted

| Table | Flag | Meaning |
|---|---|---|
| `VENDORS` | `MISSING_PAYMENT_TERMS` | Terms blank in source, imputed |
| | `MISSING_REGION` | Region blank in source, imputed |
| | `UNMAPPED_CATEGORY` | Value outside the controlled vocabulary |
| `PURCHASE_ORDERS` | `NO_GOODS_RECEIPT` | Ordered, not yet received |
| | `GR_BEFORE_PO_DATE` | Receipt predates the order — impossible sequence |
| `INVOICES` | `NON_PO_SPEND` | No purchase order — audit red flag |
| | `DUE_BEFORE_INVOICE` | Due date precedes the invoice date |
| `PAYMENTS` | `UNMAPPED_PAYMENT_METHOD` | Value outside the controlled vocabulary |
| all | `NON_POSITIVE_AMOUNT` | Zero or negative transaction value |

Flags are stored comma-separated, built with `ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(...))` so
that a row with no issues gets `NULL` rather than an awkward empty list. `IS_DQ_FLAGGED` gives
Power BI a clean boolean to slice on without string matching.
