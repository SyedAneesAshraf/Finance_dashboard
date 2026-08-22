# Phase 7 — Exit Test Report (Excel Budget-vs-Actual Companion)

**Date:** 2026-08-22
**Goal:** a budget-vs-actual workbook built from the Gold-layer export, not manual entry —
every actual figure a live formula, not a number pasted in from Snowsight.

```
.venv\Scripts\python.exe scripts\export_gold_tables.py       # refresh exports/gold/*.csv
.venv\Scripts\python.exe scripts\build_excel_companion.py    # writes excel\FinanceProcurement_BudgetVsActual.xlsx
```

---

## Result: 3 / 3 roadmap exit tests passed

| # | Exit test (from the roadmap) | Result |
|---|---|---|
| 1 | Actual-spend figures match the Gold-layer source exactly (spot-check 3–4 cost centers) | ✅ all 15 cost centers checked, exact match |
| 2 | Variance is a live formula; conditional formatting flips when budget changes | ✅ confirmed by mutating a budget cell and re-reading the cell color |
| 3 | Chart updates automatically when the underlying table changes | ✅ chart is bound to `Budget_vs_Actual!B4:C11` by reference, not a static image |

---

## What's in the workbook

`excel/FinanceProcurement_BudgetVsActual.xlsx`, 4 sheets:

| Sheet | Contents |
|---|---|
| `Budget_vs_Actual` | Department-level Budget / Actual / Variance / Variance % table + a clustered-column chart, "Budget vs Actual by Department" (matches the roadmap's chart spec literally) |
| `CostCenter_Detail` | Actual spend by all 15 cost centers (`SUMIFS`, two criteria) — the granularity the roadmap's pivot spec actually asks for ("actual spend by GL account/cost center"); the department rollup exists for the chart |
| `Invoices` | `exports/gold/fact_invoice.csv` imported verbatim (672 rows), plus two formula columns, `DEPARTMENT`/`COST_CENTER`, computed via `VLOOKUP` against `GL_Accounts` |
| `GL_Accounts` | `exports/gold/dim_gl_account.csv` imported verbatim |

Nothing is pasted in as a value except the CSV imports and the budget targets themselves —
every rollup (`SUMIF`/`SUMIFS`), lookup (`VLOOKUP`), and variance is a live formula that
recalculates if the Gold export changes.

## The one manual input, and why

`BUDGET_ASSUMPTIONS` in `scripts/build_excel_companion.py` — a round, independently-set
24-month departmental spend plan. Documented rather than hidden, per the project's existing
convention ([00_design_decisions.md](00_design_decisions.md)):

| Department | Budget | Actual | Variance |
|---|---:|---:|---:|
| Manufacturing | $2,900,000 | $3,059,924 | 🔴 +$159,924 |
| IT | $2,250,000 | $2,133,408 | 🟢 −$116,592 |
| HR | $600,000 | $638,057 | 🔴 +$38,057 |
| Corporate | $650,000 | $563,693 | 🟢 −$86,307 |
| Finance | $400,000 | $412,122 | 🔴 +$12,122 |
| Logistics | $175,000 | $156,528 | 🟢 −$18,472 |
| Facilities | $12,000 | $9,296 | 🟢 −$2,704 |
| **Total** | **$6,987,000** | **$6,973,029** | −$13,971 |

Budgets were picked independent of the actuals (not reverse-engineered to look tidy), so the
resulting mix of 3 over-budget and 4 under-budget departments is a real outcome, not a
demo artifact — which is what actually exercises the conditional formatting in both
directions.

## Two build issues worth recording (both silent-wrong-number failure modes)

1. **XLOOKUP isn't universal.** The first draft used `XLOOKUP`, which returned `#NAME?` in
   the Excel install actually available here (pre-365/2021). Switched to `VLOOKUP` — every
   Excel version supports it, and it needed no help from the workbook author to fail loudly
   instead of quietly: `#NAME?` cascaded into every downstream `SUMIF` silently returning 0,
   which would have shipped as a workbook that opens fine and shows a wrong number in every
   cell.
2. **openpyxl conditional-formatting fills are backwards from normal cell fills.** A regular
   `PatternFill` reads its visible color from `fgColor`; a differential-format fill used in
   `conditional_formatting.add(...)` reads it from `end_color`/`bgColor` instead. Built with
   only `fgColor` set, the rule fired correctly (confirmed via the formula and via row colors
   splitting by sign) but painted every matching cell black at `Color: 0`. Fixed by setting
   `start_color`/`end_color` to the same value so the distinction can't matter. Both bugs were
   caught by using Excel COM automation (`win32com`) to actually open the file, force a full
   recalculation, and read back real cell values and colors — not by eyeballing the generated
   XML, which would have looked correct in both cases.

## Verification method

No spreadsheet-formula evaluator ships with openpyxl — it only writes formula strings, it
doesn't compute them. Verified for real by driving the actual Excel install on this machine
via `win32com.client` (installed to the project venv for this purpose): open the workbook,
call `Application.CalculateFullRebuild()`, read back computed cell values and
`DisplayFormat.Interior.Color`, close without saving. This is the same class of "did it
actually compute, not just look plausible" check the project applied to Silver's counts and
Gold's SCD2 in earlier phases.
