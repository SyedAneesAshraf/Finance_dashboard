# Phase 5 — The Six Analytical Queries

Each query, the business question it answers, the SQL concept it demonstrates, and what it
actually returned. Full commentary lives in the header of each `.sql` file; results are
exported to [`exports/`](../exports/).

**All 26 exit tests pass.** Run with `.venv\Scripts\python.exe scripts\validate_phase5.py`.

| # | Query | SQL concept | Business question |
|---|---|---|---|
| 5.1 | [DPO by vendor](../sql/04_analytics/51_dpo_by_vendor.sql) | CTE + aggregate | How long do we take to pay? |
| 5.2 | [AP aging buckets](../sql/04_analytics/52_ap_aging_buckets.sql) | CASE + DATEDIFF | How much is overdue, and by how long? |
| 5.3 | [Spend concentration](../sql/04_analytics/53_vendor_spend_concentration.sql) | Window functions | Are we over-reliant on a few vendors? |
| 5.4 | [Duplicate detection](../sql/04_analytics/54_duplicate_invoice_detection.sql) | Self-join + tolerance | Are we about to pay twice? |
| 5.5 | [3-way match](../sql/04_analytics/55_three_way_match_variance.sql) | Aggregate + CASE + NULLIF | Do PO, receipt and invoice agree? |
| 5.6 | [Vendor history](../sql/04_analytics/56_vendor_history_scd2.sql) | SCD2 range join | What terms applied *at the time*? |

---

## 5.1 — Days Payable Outstanding by vendor

**Question:** On average, how many days pass between an invoice arriving and being paid?

DPO is watched from two directions at once. A rising figure can mean healthy working-capital
management or it can mean cash-flow strain and a supplier about to stop shipping — the number
alone does not distinguish them.

**What makes it actionable is comparing DPO against the terms actually agreed.** Paying a NET60
vendor in 55 days is good treasury practice; paying a NET15 vendor in 55 days is a penalty and a
damaged relationship. Raw DPO would rank the well-managed NET60 vendor as *worse*.

The agreed terms come from the vendor version in force **when each invoice was issued**, not
today's terms — which is why this query depends on Phase 4's SCD2 work.

**Result — 48 vendors, portfolio DPO 41.83 days.** Worst offenders by days beyond terms:

| Vendor | Category | Invoices | Avg DPO | Agreed | **Beyond terms** | % late |
|---|---|---|---|---|---|---|
| Campbell Ltd | Consulting | 1 | 142.0 | 45 | **+97** | 100% |
| Bonilla, Jefferson and Brown | Logistics | 1 | 72.0 | 15 | **+57** | 100% |
| Martinez, Wright and Reynolds | Logistics | 4 | 87.0 | 60 | **+27** | 50% |
| Medina LLC | IT Services | 18 | 50.9 | 30 | **+20.9** | 50% |

34 of 48 vendors are paid beyond their agreed terms. Medina LLC is the one to act on — 18
invoices and $103k of spend, unlike the top two which are single-invoice outliers.

---

## 5.2 — AP aging buckets

**Question:** How much of our payables is overdue, split into 30/60/90+ day bands?

The most common report in accounts payable. Each bucket maps to a different action: 0-30 is
normal lag, 31-60 warrants a chase, 61-90 means something is stuck in approval, and 90+ is where
penalties, supply interruption and audit findings live.

Two details make it correct rather than merely plausible:

1. **It ages from `DUE_DATE`, not `INVOICE_DATE`.** An invoice issued 90 days ago on NET90 terms
   is not overdue at all. Ageing from the invoice date manufactures overdue balances that do not
   exist — a common and credibility-destroying mistake.
2. **'Not Due' is its own bucket**, kept out of 0-30. Merging them overstates the exposure that
   the entire report exists to communicate.

**Result — all 88 unpaid invoices bucketed, $849,948.62 total:**

| Bucket | Invoices | Outstanding USD | % | Avg days | Max days |
|---|---|---|---|---|---|
| Not Due | 19 | 237,352.58 | 27.9% | −21 | −2 |
| 0-30 Days | 16 | 185,370.12 | 21.8% | 19 | 30 |
| 31-60 Days | 13 | 71,171.92 | 8.4% | 43 | 55 |
| 61-90 Days | 11 | 90,480.53 | 10.7% | 73 | 88 |
| **90+ Days** | **29** | **265,573.47** | **31.3%** | **384** | **616** |

**The finding: the largest single bucket is 90+ days.** $265k — nearly a third of all outstanding
payables — is more than three months overdue, averaging 384 days and reaching 616. Money sitting
there is almost never an oversight; it is a dispute, a missing goods receipt, or a broken
process. This is the headline number for the Page 1 dashboard.

---

## 5.3 — Vendor spend concentration (Pareto)

**Question:** Is spend too concentrated in a handful of vendors?

Supplier concentration is a risk nobody notices until it bites. If most spend flows through a few
vendors, one of them failing is not a procurement inconvenience — it is a production stoppage.

Three window functions, each answering a different question:

```sql
RANK() OVER (ORDER BY spend DESC)          -- where does this vendor sit?
SUM(SUM(x)) OVER ()                        -- grand total
SUM(SUM(x)) OVER (ORDER BY spend DESC ...) -- running total to here
```

The nested `SUM(SUM(x))` is not a typo: the inner `SUM` aggregates within the `GROUP BY`, the
outer one is the window function operating over those grouped rows.

**Two correctness details.** `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` is stated
explicitly — the default `RANGE` frame includes *peer* rows, so two vendors with identical spend
would share a running total and the curve would step rather than climb. And `ORDER BY ...,
VENDOR_ID` breaks ties deterministically.

**Result — 49 vendors, $6,973,028.93 total, cumulative reaches exactly 100.00%:**

| Rank | Vendor | Category | Spend USD | % | Cumulative % |
|---|---|---|---|---|---|
| 1 | Woodard, Harrell and Bell | Consulting | 841,226.95 | 12.06% | 12.06% |
| 2 | Williams, Hodges And Lee | Raw Materials | 771,580.52 | 11.07% | 23.13% |
| 3 | Phillips-Henry | Raw Materials | 567,236.37 | 8.13% | 31.26% |
| 4 | Johnson Ltd | IT Services | 500,106.01 | 7.17% | 38.44% |
| 5 | Arroyo Ltd | Raw Materials | 429,162.09 | 6.15% | 44.59% |

**The finding: 15 of 49 vendors — 31% — carry the first 80% of spend.** The top two alone are
23%. A textbook Pareto distribution, and a concrete case for dual-sourcing the top five.

**A second finding falls out of it:** the largest vendor by spend, Woodard Harrell and Bell at
$841k, has `REGION = 'Unknown'` — one of the three vendors whose region was blank in the source
and imputed in Silver. The single biggest supplier relationship has incomplete master data. That
is exactly the kind of issue the `DATA_QUALITY_FLAGS` column exists to surface.

---

## 5.4 — Duplicate invoice detection

**Question:** Are there duplicate or suspicious invoices?

Duplicate payment is one of the most common and most expensive AP control failures, and it rarely
looks like fraud: a supplier re-sends an invoice already in the queue, or the same PDF gets keyed
twice. Auditors ask for this check by name.

**Rule:** same vendor, same amount, invoice dates within 3 days, different invoice IDs.

**Why `a.INVOICE_ID < b.INVOICE_ID` matters.** Without it a self-join returns each pair *twice*
(A-B and B-A) plus every row matched against itself. The strict inequality does all three jobs
at once: no self-matches, no mirrored duplicates, and it fixes which invoice is the "original".
Reporting 28 duplicates when there are 14 destroys the credibility of the control.

**Result — 14 pairs, exactly matching the 14 injected in Phase 1.** Not by count alone: the
validator compares the recovered pairs against the ground-truth list **identity by identity**, and
all 14 match. Maximum gap 3 days, no mirrored pairs, no self-matches.

Each pair carries a recommended action, because the response differs by status:

- **BOTH PAID** — the money is gone; recover the overpayment
- **ONE PAID** — hold the second before it runs
- **NEITHER PAID** — block both before the next payment run

The rule is deliberately slightly over-inclusive. A vendor on a fixed monthly retainer can trip
it legitimately. That is the right trade-off for a fraud control: a false positive costs an
analyst five minutes, a false negative costs the invoice amount.

---

## 5.5 — Three-way match variance

**Question:** Do the purchase order, the goods receipt, and the invoice agree?

The core procurement control. Before paying, the invoice must agree with what was **ordered** and
what was **received**. A 5% tolerance absorbs legitimate freight and rounding differences; beyond
that it is a discrepancy someone has to explain.

**Why this aggregates invoices per PO.** A naive version compares each invoice to its PO
one-to-one. That is wrong here and in most real systems, because a PO is frequently billed across
several staged deliveries — comparing one partial invoice against the full PO value would flag
every legitimate staged delivery as a massive under-billing, and the report would be ignored
within a week. The control that matters is cumulative.

**Why `NULLIF`.** `NULLIF(po_amount, 0)` turns a zero PO value into `NULL`, so the division
returns `NULL` instead of raising. A cancelled PO reports "cannot assess" rather than aborting a
control report Finance runs monthly.

**Result — 420 POs assessed (480 minus 60 never invoiced), 36 exceptions:**

| Outcome | Count |
|---|---|
| Over-billed — do not pay difference | 29 |
| Under-billed — expect further invoices | 7 |
| **Total exceptions** | **36** |
| Invoiced POs with **no goods receipt** | 32 |

**36, not the 22 POs deliberately mis-priced.** The other 14 are POs pushed past tolerance by a
**duplicate invoice** quoting the same PO number — the defect query 5.4 detects. Worst case was
+104%, a PO billed roughly twice.

That overlap is real, not a flaw: a duplicated bill genuinely *is* an over-billing against the
PO, so two independent controls catch the same event from different angles. In practice you
investigate the duplicate first, because resolving it clears the variance too.

The 32 POs invoiced with **no goods receipt recorded** are leg 1 of the match failing outright —
billed for goods the system has no record of receiving.

---

## 5.6 — Vendor history snapshot (SCD Type 2)

**Question:** What payment terms did this vendor have **when this invoice was issued** — not
today?

In finance this is not a philosophical distinction. If a vendor moved from NET30 to NET45 in
September 2025, an invoice issued that June was due in 30 days. Judging it against today's NET45
scores a late payment as on-time, understates the DPO problem, and produces an audit finding when
someone reconciles against the original contract.

**A flat, current-only vendor table cannot answer this at all.** It has one row per vendor and no
memory that anything changed. That is the entire reason `DIM_VENDOR` is Type 2.

```sql
JOIN DIM_VENDOR v
  ON  f.VENDOR_ID = v.VENDOR_ID
 AND  f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
```

The equality alone would match every version and multiply the invoice by its version count. The
`BETWEEN` narrows it to the one version whose window contains the invoice date — and it returns
exactly one row only because Phase 4 guarantees the ranges abut without overlapping.

**Result — 78 invoices across the 6 versioned vendors, 5 showing terms that differ across their
own invoice history:**

| Vendor | Terms then vs now |
|---|---|
| V0001 Johnson Ltd | NET30 → NET45 |
| V0002 Arnold, Mitchell and Jones | NET45 → 2/10 NET30 |
| V0008 Winters, Campbell and Carr | NET45 → NET15 |
| V0024 Ramos-Hall | NET15 → NET45 |
| V0043 Morrow, Miller And Brooks | NET30 → NET15 |

78 rows for 78 invoices — **no fan-out**, confirming one dimension row per invoice.

V0026 has two versions in the dimension but appears with only one set of terms here, because all
of its invoices fall before its change date. Correct behaviour: the history exists and is
queryable, there is simply no post-change activity yet.

---

## The five business questions, answered

| # | Question | Query | Answer |
|---|---|---|---|
| 1 | Vendor concentration risk | 5.3 | **Yes** — 15 of 49 vendors (31%) carry 80% of spend |
| 2 | DPO / late payment risk | 5.1 | **41.83 days** average; 34 of 48 vendors paid beyond terms |
| 3 | Overdue payables by bucket | 5.2 | **$265,573 (31%)** is 90+ days overdue, worst at 616 days |
| 4 | Duplicate / fraud risk | 5.4 | **14 duplicate pairs** flagged for review |
| 5 | 3-way match compliance | 5.5 | **36 exceptions**, plus 32 POs invoiced with no goods receipt |
