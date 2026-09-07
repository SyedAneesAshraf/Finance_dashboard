# Finance & Procurement Analytics — Oracle EBS–Style Data Platform

**Snowflake (Medallion) → Advanced SQL → Power BI (DAX) → Excel**

An end-to-end analytics pipeline over a simulated Oracle E-Business Suite Financials &
Procurement dataset: Accounts Payable, Purchase Orders, and Vendor Management.

> Built as a portfolio project for the Dataplatr Data Analytics & BI internship.
> Dataplatr builds pre-built data models on top of enterprise applications (SAP, Oracle EBS,
> Workday, Salesforce). This project simulates that work end to end on the Oracle EBS
> Financials/Procurement domain.

---

## Problem statement

Meridian Manufacturing Group is a mid-size industrial manufacturer running Oracle EBS R12.
Its Finance team answers questions about payables by hand — exporting EBS extracts into
spreadsheets, reconciling them manually, and rebuilding the same analysis every month. That
process is slow, error-prone, and gives no one a current view of cash exposure.

This project replaces it with a governed cloud pipeline. Raw EBS-style extracts land in
Snowflake, are cleaned and conformed through a Bronze → Silver → Gold medallion architecture,
and are modeled as a star schema with a Type 2 slowly-changing vendor dimension. A Power BI
dashboard and a set of analytical SQL queries then answer, on demand, five questions the
Finance team currently cannot answer without a week of spreadsheet work:

| # | Business question | Risk it addresses |
|---|---|---|
| 1 | Which vendors do we spend the most with, and is that spend too concentrated? | Supplier concentration risk |
| 2 | How long do we take to pay invoices (DPO), and are we paying late? | Penalty and vendor-relationship risk |
| 3 | How much of our payables is overdue, by aging bucket (30/60/90+)? | Cash-flow visibility |
| 4 | Are there duplicate or suspicious invoices? | Fraud / error control |
| 5 | Do POs, goods receipts, and invoices match (3-way match)? | Procurement control, audit compliance |

**Every artifact in this repo traces back to one of those five questions.** Anything that
doesn't is scope creep and was deliberately left out.

---

## Architecture

```
  Synthetic Oracle EBS extracts (5 CSVs, with realistic injected data-quality defects)
                                    │
                                    ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  SNOWFLAKE  —  FIN_PROC_DB                                           │
  │                                                                      │
  │   BRONZE          raw landing, loaded verbatim, no transformation    │
  │      │            all columns VARCHAR — preserves source fidelity    │
  │      ▼                                                               │
  │   SILVER          typed, standardized, deduplicated, FX-converted    │
  │      │            data_quality_flag audit trail; defects preserved   │
  │      ▼                                                               │
  │   GOLD            star schema, business-ready                        │
  │                   FACT_INVOICE · FACT_PAYMENT · FACT_PURCHASE_ORDER  │
  │                   DIM_VENDOR (SCD Type 2) · DIM_DATE · DIM_GL_ACCOUNT│
  └──────────────────────────────────────────────────────────────────────┘
                        │                                │
                        ▼                                ▼
              6 analytical SQL queries          Power BI (1 page + DAX;
                                                  Page 2 planned next)
                                                        │
                                                        ▼
                                          Excel budget-vs-actual companion
```

Why layered: Bronze preserves exactly what the source system sent, so any number on the
dashboard can be traced back to a raw row. Silver is where trust is established once, rather
than re-litigated in every downstream query. Gold is shaped for consumption — a Finance user
joins nothing and writes no SQL.

---

## Repository layout

| Path | Contents |
|---|---|
| `data_raw/` | The 5 synthetic source CSVs (Bronze inputs) |
| `sql/00_setup/` | Snowflake warehouse, database, schema creation |
| `sql/01_bronze/` | Bronze DDL + load scripts |
| `sql/02_silver/` | Silver DDL + cleaning / standardization logic |
| `sql/03_gold/` | Star schema DDL + SCD Type 2 `MERGE` for `DIM_VENDOR` |
| `sql/04_analytics/` | The 6 analytical queries, each with its business write-up |
| `powerbi/` | Power BI project (`.pbip` — TMDL semantic model + PBIR report definition) |
| `excel/` | Budget-vs-actual companion workbook |
| `exports/` | Gold-layer CSV extracts feeding Power BI / Excel |
| `scripts/` | Python data generator and helpers |
| `docs/` | Design decisions, per-phase notes, ER diagram, screenshots |

---

## Tech stack

| Layer | Tool |
|---|---|
| Data generation | Python 3.13 — `Faker`, `pandas`, `numpy` |
| Warehouse | Snowflake (Bronze / Silver / Gold schemas) |
| Transformation | Snowflake SQL — CTEs, window functions, `MERGE`, `QUALIFY` |
| BI | Power BI Desktop + DAX |
| Companion analysis | Excel — pivot, `SUMIFS`/`XLOOKUP`, conditional formatting |
| Version control | Git / GitHub |

---

## Build status

| Phase | Output | Status |
|---|---|---|
| 0 | Environment, repo skeleton, design decisions | ✅ Complete |
| 1 | 5 synthetic CSVs with injected defects | ✅ Complete — 33/33 tests |
| 2 | Bronze layer loaded | ✅ Complete — 23/23 tests |
| 3 | Silver layer cleaned | ✅ Complete — 33/33 tests |
| 4 | Gold star schema + SCD Type 2 | ✅ Complete — 30/30 tests |
| 5 | 6 validated analytical queries | ✅ Complete — 26/26 tests |
| 6 | Power BI dashboard — Page 1 (AP Aging & Cash Flow) | ✅ Complete — Page 2 (Vendor Spend & Risk) scoped for a follow-up pass |
| 7 | Excel budget-vs-actual workbook | ✅ Complete — verified via Excel COM automation |
| 8 | Documentation & GitHub packaging | 🟡 In progress |
| 9 | Scope verification / interview dry-run | ⬜ Not started |

---

## Documentation

- [Design decisions](docs/00_design_decisions.md) — project-wide constants every phase honours
- [Phase 0 setup guide](docs/01_phase0_setup_guide.md) — environment reproduction steps
- [Phase 0 test report](docs/02_phase0_test_report.md) — 5/5 exit tests
- [Phase 1 data design](docs/03_phase1_data_design.md) — table specs and three deviations from the blueprint
- [Phase 1 ground truth](docs/04_phase1_ground_truth.md) — every injected defect, counted
- [Phase 1 test report](docs/05_phase1_test_report.md) — 33/33 exit tests
- [Phase 2 test report](docs/06_phase2_test_report.md) — 23/23 exit tests, all 1,864 rows verified verbatim
- [Phase 3 cleaning rules](docs/07_phase3_cleaning_rules.md) — every Silver rule and its reasoning
- [Phase 3 test report](docs/08_phase3_test_report.md) — 33/33 exit tests
- [Phase 4 ER diagram](docs/09_phase4_er_diagram.md) — star schema and the SCD2 history
- [Phase 4 test report](docs/10_phase4_test_report.md) — 30/30 exit tests
- [Phase 5 queries and results](docs/11_phase5_queries_and_results.md) — the 6 queries, their reasoning, and what they found
- [Phase 6 Power BI test report](docs/12_phase6_powerbi_test_report.md) — semantic model, Page 1, and what's still deferred
- [Phase 7 Excel test report](docs/13_phase7_excel_test_report.md) — 3/3 exit tests, verified via COM automation
#
