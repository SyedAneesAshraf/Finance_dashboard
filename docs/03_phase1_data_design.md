# Phase 1 — Synthetic Data Design

How the five source extracts are constructed, and the three places this deviates from the
blueprint. Each deviation is a correction to a gap that would otherwise make a *later* phase
impossible — not a preference.

---

## Deviation 1 — invoices carry `gl_account_id`

**Blueprint says:** `FACT_INVOICE` = `invoice_id, vendor_id, po_id, invoice_date, due_date,
amount, currency, status`. It also specifies a `DIM_GL_ACCOUNT` dimension.

**Problem:** no fact in the blueprint references `account_id`. `DIM_GL_ACCOUNT` would join to
nothing — an orphan dimension floating in the star schema. Worse, Phase 7 requires "actual
spend by GL account/cost center", which is unanswerable if no transaction carries an account.

**Resolution:** `invoices.csv` includes `gl_account_id`, and `FACT_INVOICE` carries it as an
FK. This is also how Oracle EBS actually works — AP invoice distribution lines code to GL
accounts.

## Deviation 2 — the vendor master is extracted twice

**Blueprint says:** 5 CSVs, and `DIM_VENDOR` is SCD Type 2 with a `MERGE` that closes out old
rows when `category` or `payment_terms` change.

**Problem:** a single vendor snapshot contains no change to detect. A `MERGE` run against one
extract produces one row per vendor and SCD2 becomes decorative — exactly the failure mode the
roadmap calls "the most common way people fake this feature."

**Resolution:** the vendor master is extracted **twice**, as a full snapshot would be in
production:

| File | Extract date | Contents |
|---|---|---|
| `vendors.csv` | 2024-09-01 | 50 vendors, initial state |
| `vendors_update.csv` | 2026-08-15 | same 50 vendors — **6 changed** — plus **2 newly onboarded** |

Still five source *tables*; the vendor table simply has two snapshots. Phase 4's `MERGE`
consumes the second against `DIM_VENDOR` and exercises all three SCD2 paths: unchanged (no-op),
changed (close out + insert), and new member (insert).

The 2 new vendors deliberately have **no transactions** — newly onboarded suppliers with no
spend yet. This keeps the exit test "every `vendor_id` in POs/Invoices exists in `vendors.csv`"
true while still exercising the insert path.

### How the change date survives into the warehouse

Both extracts carry Oracle EBS **WHO columns** — `creation_date` and `last_update_date`. These
are standard on virtually every EBS table.

This matters mechanically: the SCD2 `MERGE` uses `last_update_date` as the `effective_start_date`
of the new version, and closes the prior version at `last_update_date - 1`. Without it, the
only available close-out date would be the extract date (2026-08-15), which would wrongly
attribute the *new* terms to every invoice issued between the real change and the extract —
silently breaking query 5.6.

## Deviation 3 — a PO may have more than one invoice

**Blueprint's query 5.5** joins `FACT_INVOICE` to `FACT_PURCHASE_ORDER` on `po_id` and compares
`f.amount` to `po.po_amount` one-to-one.

**Problem:** the row-count targets make strict 1:1 arithmetically impossible. POs cap at 500 and
some must be left unmatched, so 1:1 yields at most ~450 PO-backed invoices — but invoices must
reach 600–900.

**Resolution:** ~30% of POs are billed across 2–3 invoices, which is what actually happens with
staged deliveries. Query 5.5 therefore **aggregates invoiced amount per PO** before comparing
to the PO value. That is also the more correct control: real 3-way match flags *cumulative*
over-billing against a PO, not each partial invoice in isolation. A naive 1:1 comparison would
flag every legitimate partial billing as an exception.

---

## Table specifications

### `gl_accounts.csv` — 20 rows
`account_id, account_name, cost_center, department` — 6xxxx expense accounts across
Manufacturing, IT, Logistics, Finance, HR, Facilities.

### `vendors.csv` / `vendors_update.csv` — 50 / 52 rows
`vendor_id, vendor_name, category, region, payment_terms, currency, creation_date, last_update_date`

- Categories: Raw Materials, IT Services, Logistics, Office Supplies, Consulting
- Regions: North America, EMEA, APAC, LATAM
- Terms: NET15, NET30, NET45, NET60, 2/10 NET30
- Currency fixed per vendor (~85% USD) — see [design decision D3](00_design_decisions.md)

### `purchase_orders.csv` — ~480 rows
`po_id, vendor_id, po_date, po_amount, currency, goods_receipt_date, po_status`

- ~60 POs deliberately have no invoice (open commitments / exception reporting)
- Some `goods_receipt_date` null — ordered but not yet received

### `invoices.csv` — ~690 rows
`invoice_id, vendor_id, po_id, gl_account_id, invoice_date, due_date, amount, currency, status`

- `due_date` is derived from the payment terms **in effect on the invoice date**, not today's
  terms. This is what makes the SCD2 dimension load-bearing rather than ornamental.

### `payments.csv` — ~540 rows
`payment_id, invoice_id, payment_date, amount_paid, currency, payment_method`

One payment per invoice. Partial/installment payments were considered and rejected: they would
make query 5.1 count an invoice once per payment row and skew average DPO.

---

## Injected defects

The five the blueprint names, plus three that Phase 3 needs in order to have real work to do.

| # | Defect | Target | Detected by |
|---|---|---|---|
| 1 | Near-duplicate invoices (same vendor, same amount, ≤3 days apart) | 14 pairs | Query 5.4 |
| 2 | Orphan invoices (`po_id` null) | 18 | Query 5.5 / non-PO spend |
| 3 | Invoice total >5% off PO amount | 22 POs | Query 5.5 |
| 4 | Late payments (paid well past due date) | ~90 | Query 5.1, aging |
| 5 | Missing `payment_terms` / `region` | 4 / 3 vendors | Silver cleaning |
| 6 | **Mixed date formats** | ~30% of rows | Silver standardization |
| 7 | **Inconsistent casing / whitespace** | ~30% of text values | Silver standardization |
| 8 | **Exact whole-row duplicates** | 6 rows | Silver dedup |

### Why 6, 7, and 8 were added

Phase 3's task list includes "standardize date formats", "standardize text casing", and
"deduplicate exact full-row duplicates". If the raw files were already uniform, all three would
be no-ops and the Silver layer would be theatre — visibly present, provably useless.

**Defect 6 (date formats)** simulates extracts merged from EBS instances with different
`NLS_DATE_FORMAT` settings — the actual reason this happens in production. Three formats
appear: `YYYY-MM-DD`, `MM/DD/YYYY`, and Oracle's default `DD-MON-YYYY`. Format is consistent
*within* a row, varying *between* rows, as it would be if rows came from different extract runs.

**Defect 8** is distinct from defect 1 and the distinction is the whole point:

| | Defect 1 — near-duplicate | Defect 8 — exact duplicate |
|---|---|---|
| What | Different `invoice_id`, same vendor/amount, dates ≤3 days apart | Byte-identical row, same PK, appears twice |
| Cause | Vendor genuinely double-billed, or AP keyed it twice | Extract ran twice / double-load artifact |
| Silver | **Must preserve** | **Must remove** |
| Phase 5 | Reported as a fraud/error finding | Should not appear at all |

Collapsing defect 1 in Silver would leave query 5.4 with nothing to find. Leaving defect 8 in
would double-count real money. Handling both correctly is the point.

### A consequence worth noting

Casing noise on `vendor_name` and `category` is applied **identically in both vendor extracts**
for unchanged vendors. If it varied, the Phase 4 SCD2 `MERGE` would detect `"ACME Corp"` vs
`"Acme Corp"` as a genuine change and spawn a spurious new version.

That is not a hypothetical — it is a real and common SCD2 bug. Silver's casing standardization
is precisely what prevents it, which is why the `MERGE` in Phase 4 runs against **Silver**, never
Bronze.

---

## Ground truth

The generator writes an exact count of every injected defect to
[`ground_truth.json`](ground_truth.json) and a readable summary to
[`04_phase1_ground_truth.md`](04_phase1_ground_truth.md).

Phases 3 and 5 validate against that file. It is the only way to prove Silver did not
over-clean, and that the Phase 5 detection queries find what is actually there rather than a
plausible-looking number.
