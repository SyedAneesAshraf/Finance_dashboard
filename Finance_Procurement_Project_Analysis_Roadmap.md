# Dataplatr Finance & Procurement Analytics — Deep Analysis & Build Roadmap

---

## PART 1: DEEP ANALYSIS — WHAT THIS PROJECT ACTUALLY IS

### 1.1 The Core Idea in One Paragraph

You are building a **miniature, self-contained enterprise data platform**. You will invent a fake but realistic company's Finance & Procurement data (the kind that normally lives inside Oracle EBS), pipe it through a **Bronze → Silver → Gold (medallion) architecture in Snowflake**, model it as a **star schema** (facts + dimensions), write **advanced SQL** to answer real finance questions, and visualize the answers in **Power BI**, with a small **Excel** sheet as a bonus. The entire point is to *simulate* what Dataplatr's consultants do for real clients (SAP/Oracle EBS/Workday/Salesforce → data model → BI), so that in an interview you can speak from direct hands-on experience instead of theory.

It is not a "dashboard project." It is an **end-to-end data engineering + analytics engineering + BI project**, and the grading criteria (implicit, from an interview panel's perspective) will be: *did you actually understand data modeling, not just SQL syntax?*

### 1.2 The Business Problem Being Simulated

A fictional mid-size company's Finance team currently does everything manually in spreadsheets pulled from Oracle EBS extracts. That's slow and error-prone. You are the analyst building the replacement: a cloud pipeline + dashboard that answers, on demand:

| # | Business Question | Why it matters |
|---|---|---|
| 1 | Which vendors do we spend the most with? Is spend too concentrated? | Supplier/concentration risk |
| 2 | How long do we take to pay invoices (DPO)? Are we late? | Penalty risk, vendor relationship risk |
| 3 | How much payable is overdue, by aging bucket (30/60/90+)? | Cash flow visibility |
| 4 | Are there duplicate/suspicious invoices? | Fraud/error risk |
| 5 | Do POs, goods receipts, and invoices match (3-way match)? | Procurement control / audit compliance |

Every technical thing you build in this project should be traceable back to one of these five questions. If a feature doesn't help answer one of them, it's scope creep.

### 1.3 Why "Oracle EBS-style" Specifically (and why it's not just flavor text)

This isn't cosmetic. Oracle EBS Financials/Procurement has real, well-known structural properties you are expected to replicate:
- Transactional documents (PO → Goods Receipt → Invoice → Payment) that **chain together via foreign keys**, not one flat table.
- **Master data that changes over time** (a vendor's payment terms or category can change), which is why the vendor dimension needs **SCD Type 2**, not a simple lookup table.
- **Messy, real-world data quality problems** (duplicates, orphan records, mismatches) that a fresher-level toy dataset never has.

This is the single biggest differentiator the blueprint keeps repeating: *domain realism + modeling depth*, not dashboard polish.

### 1.4 Full Feature-by-Feature Scope Breakdown

#### A. Data Architecture (Snowflake Medallion)
| Layer | What lives here | What you must do |
|---|---|---|
| **Bronze** | Raw, unmodified extracts | Load 5 raw CSVs exactly as generated (Invoices, POs, Vendors, Payments, GL Accounts) — no cleaning yet |
| **Silver** | Cleaned, validated, standardized | Remove/handle nulls, standardize date formats & casing, deduplicate exact dupes, enforce data types |
| **Gold** | Business-ready star schema | Build the fact & dimension tables listed below, ready for BI consumption |

**Scope boundary:** Bronze = "as-is copy." Silver = "trustworthy." Gold = "queryable and joinable for reporting." Don't skip straight to Gold — the *layering itself* is a scored skill.

#### B. Star Schema (Gold Layer) — 6 tables
| Table | Type | Key Columns | Purpose |
|---|---|---|---|
| `FACT_INVOICE` | Fact | invoice_id (PK), vendor_id (FK), po_id (FK), invoice_date, due_date, amount, currency, status | Central AP fact table |
| `FACT_PAYMENT` | Fact | payment_id (PK), invoice_id (FK), payment_date, amount_paid, payment_method | Tracks when/how invoices were settled |
| `FACT_PURCHASE_ORDER` | Fact | po_id (PK), vendor_id (FK), po_date, po_amount, goods_receipt_date, po_status | Procurement side of the 3-way match |
| `DIM_VENDOR` | Dimension — **SCD Type 2** | vendor_id, vendor_name, category, region, payment_terms, effective_start_date, effective_end_date, is_current | Vendor master data with history |
| `DIM_DATE` | Dimension | date_key, day, month, quarter, year, fiscal_period | Standard date dimension for time intelligence |
| `DIM_GL_ACCOUNT` | Dimension | account_id, account_name, cost_center, department | Expense categorization |

**Why SCD Type 2 matters (flagged in the blueprint as your strongest differentiator):** it lets you answer "what were this vendor's payment terms *at the time* this invoice was issued" — a flat/current-only vendor table structurally cannot answer this. This is the one modeling decision an interviewer is most likely to probe.

#### C. Synthetic Dataset Generation
| Table | Target rows | Special notes |
|---|---|---|
| Vendors | 40–60 | Categories: Raw Materials, IT Services, Logistics, Office Supplies, Consulting |
| Purchase Orders | 300–500 | Some deliberately left unmatched to any invoice |
| Invoices | 600–900 | Some non-PO invoices, some intentional duplicates |
| Payments | 500–800 | Some invoices left deliberately unpaid/overdue |
| GL Accounts | 15–25 | Cost centers / departments |

**Deliberately injected data quality issues (this is what makes it "complex," not a clean Kaggle dataset):**
1. Duplicate invoices — same vendor, same amount, invoice dates within 3 days
2. Orphan invoices — no matching PO (non-PO spend)
3. Price mismatches — invoice vs PO amount differing >5% (3-way match failures)
4. Late payments — payment_date far past due_date (populates aging buckets meaningfully)
5. Missing/null fields — a few missing `payment_terms` / `vendor_region` values to force real Silver-layer cleaning logic

#### D. Core SQL Analysis — 6 queries, each demonstrating a distinct SQL concept
| # | Query | SQL Concept | Business Question |
|---|---|---|---|
| 5.1 | DPO by vendor | CTE + aggregate (AVG, GROUP BY) | How long do we take to pay, on average? |
| 5.2 | Aging buckets | CASE + DATEDIFF conditional logic | How much unpaid AP is overdue, by bucket? |
| 5.3 | Vendor spend concentration (Pareto) | Window functions (RANK, SUM OVER) | Are we over-reliant on a few vendors? |
| 5.4 | Duplicate invoice detection | Self-join with tolerance window | Which invoices look duplicated? |
| 5.5 | 3-way match variance | Subquery/join + CASE + NULLIF | Do invoice amounts match PO amounts within tolerance? |
| 5.6 | Vendor history snapshot | SCD Type 2 range join (`BETWEEN effective_start AND effective_end`) | What terms applied *at the time* of a given invoice? |

These six queries are explicitly called the "technical centerpiece" — you should be able to explain the *business reasoning*, not just recite syntax, since this is what gets probed in a SQL interview round.

#### E. Power BI Dashboard — 2 pages + DAX layer
**Page 1 — AP Aging & Cash Flow Overview**
- KPI cards: Total Outstanding Payables, Total Overdue Amount, Average DPO
- Stacked bar: outstanding amount by aging bucket
- Line chart: monthly invoiced vs paid trend (12 months)
- Table: top 10 overdue invoices
- Slicers: date range, vendor category, region

**Page 2 — Vendor Spend & Risk Analysis**
- Pareto chart: vendor spend concentration (bar + cumulative % line) — visual of query 5.3
- Donut: spend by vendor category
- Matrix: 3-way match exceptions with drill-through
- Card: count of flagged duplicate invoices

**DAX measures needed:** Total Outstanding, Avg DPO, % Overdue (using `DIVIDE` for safe zero-handling), MoM Payment Trend (`CALCULATE` + `DATEADD`/`SAMEPERIODLASTYEAR`).

#### F. Excel Companion (the "Good to Have: Excel" JD line)
- Pivot table: actual spend by GL account/cost center vs a budget figure you define
- Variance column with conditional formatting (red/green)
- SUMIFS/XLOOKUP pulling from the Gold-layer export (not manual entry)
- One chart: Budget vs Actual by department

#### G. Final Deliverables Checklist
- [ ] GitHub repo with README (architecture diagram, screenshots, key SQL snippets)
- [ ] 5 synthetic CSVs (Bronze layer)
- [ ] Silver-layer cleaning SQL scripts
- [ ] Gold-layer star schema DDL + SCD Type 2 MERGE logic for `DIM_VENDOR`
- [ ] 6 analytical SQL queries with a short write-up of the business question each answers
- [ ] Power BI `.pbix` — 2 pages, slicers, 4–5 DAX measures
- [ ] Excel budget-vs-actual companion workbook

### 1.5 Full Tech Stack

| Layer | Tool | Role |
|---|---|---|
| Data generation | Python (`Faker` library) or structured Excel | Create realistic synthetic CSVs with intentional flaws |
| Data warehouse | Snowflake (free trial account + warehouse) | Bronze/Silver/Gold schemas, SQL engine |
| Transformation | SQL (Snowflake SQL dialect) | Cleaning, star schema build, SCD2 MERGE, analytics queries |
| BI/Visualization | Power BI Desktop | Data model, relationships, DAX, 2-page dashboard |
| Companion analysis | Excel | Pivot tables, formulas, conditional formatting, 1 chart |
| Version control / presentation | GitHub | README, screenshots, SQL snippets, story-telling artifact |

### 1.6 What This Project Is Actually Testing (Interview Lens)
- Can you reason about **fact vs dimension** modeling, not just write flat SQL?
- Do you understand **why layered architecture (medallion) exists**, not just that it's a buzzword?
- Can you explain **SCD Type 2** and when "current state" isn't good enough?
- Can you translate **business questions into SQL** (and back — SQL result into a business sentence)?
- Can you build a **BI layer** that a Finance stakeholder could actually use?
- Can you speak about **data quality and audit/fraud controls** convincingly?

---

## PART 2: PHASED BUILD ROADMAP (with mandatory checkpoints)

Each phase below has: **Goal → Tasks → Tools → Exit Tests (must all pass before moving on) → Common Pitfalls**. The exit tests exist specifically so you don't drift from the original scope — treat them as hard gates, not suggestions.

---

### Phase 0 — Environment & Planning Setup
**Goal:** Have every tool account/access ready so the "1 focused day" sprint doesn't get derailed by setup friction.

**Tasks:**
1. Create Snowflake trial account; create a warehouse (X-Small is enough), a database (`FIN_PROC_DB`), and three schemas: `BRONZE`, `SILVER`, `GOLD`.
2. Install Power BI Desktop.
3. Set up Python environment (or Excel) for data generation; install `faker`, `pandas`, `numpy`.
4. Create a GitHub repo with folders: `/data_raw`, `/sql`, `/powerbi`, `/excel`, `/docs`.
5. Write a one-paragraph "problem statement" (copy/adapt Section 1.2 above) into `README.md` as the anchor — reread it before starting every phase.

**Exit Tests:**
- [ ] Can run `SELECT CURRENT_WAREHOUSE(), CURRENT_DATABASE();` successfully in Snowsight.
- [ ] Three empty schemas exist: BRONZE, SILVER, GOLD.
- [ ] Power BI opens and can create a blank report.
- [ ] `python -c "import faker, pandas"` runs with no error.
- [ ] GitHub repo exists with the folder skeleton and README stub.

**Pitfall to avoid:** Skipping this and improvising structure mid-build — you will lose time later reorganizing rather than building.

---

### Phase 1 — Synthetic Data Generation (Bronze source files)
**Goal:** Produce 5 CSVs that are realistic AND contain the required data quality flaws on purpose.

**Tasks:**
1. Generate `vendors.csv` (40–60 rows): vendor_id, vendor_name, category, region, payment_terms — leave a few `payment_terms`/`region` blank intentionally.
2. Generate `purchase_orders.csv` (300–500 rows) linked to vendor_id — leave some POs with no invoice reference (unmatched).
3. Generate `invoices.csv` (600–900 rows) linked to vendor_id and po_id — include:
   - Some invoices with `po_id = NULL` (orphan/non-PO invoices)
   - A batch of intentional near-duplicates (same vendor, same amount, invoice_date within 3 days)
   - Some invoice amounts deliberately >5% off their PO's amount
4. Generate `payments.csv` (500–800 rows) linked to invoice_id — leave a subset of invoices with no payment row (to populate "unpaid"/aging), and make some payment_dates far after due_date (late payments).
5. Generate `gl_accounts.csv` (15–25 rows): account_id, account_name, cost_center, department.
6. Log exactly how many of each intentional flaw you inserted (write this down — you'll validate against it in Phase 2/3).

**Exit Tests:**
- [ ] Row counts fall within each target range above.
- [ ] Every `vendor_id` in POs/Invoices exists in `vendors.csv` (except where nulls are intentional).
- [ ] At least 10–15 duplicate-invoice pairs exist and are identifiable by you (ground truth list saved separately).
- [ ] At least 15–20 orphan invoices (`po_id` null) exist.
- [ ] At least 10 invoices have >5% variance from their PO amount.
- [ ] At least a few rows have genuinely blank `payment_terms`/`region`.
- [ ] CSVs open cleanly in Excel/pandas with expected column headers and no encoding errors.

**Pitfall to avoid:** Making the data *too* clean (defeats the whole "complexity" purpose) or *too* random (breaks referential integrity so joins fail later). You need controlled messiness, not chaos.

---

### Phase 2 — Snowflake Bronze Layer
**Goal:** Land the raw CSVs untouched into Snowflake.

**Tasks:**
1. Create 5 Bronze tables mirroring the CSV structure exactly (all columns as-is, mostly VARCHAR/loose typing is fine here).
2. Load each CSV via Snowsight's "Load Data" wizard or `COPY INTO`.
3. Do **not** clean anything at this stage — that's Silver's job.

**Exit Tests:**
- [ ] `SELECT COUNT(*)` on each Bronze table matches the row count of its source CSV exactly.
- [ ] Spot-check 5 random rows per table against the CSV — values match verbatim, including any nulls/blank fields.
- [ ] No transformation logic exists anywhere in this schema (if you find yourself writing a CASE statement here, it belongs in Silver).

**Pitfall to avoid:** "Cleaning while loading" — resist the urge; the medallion separation is itself part of what's being evaluated.

---

### Phase 3 — Silver Layer (Cleaning & Standardization)
**Goal:** Turn raw Bronze data into a trustworthy, standardized dataset.

**Tasks:**
1. Create Silver tables with proper data types (DATE, NUMBER, VARCHAR with defined lengths).
2. Write SQL to:
   - Standardize date formats
   - Standardize text casing (vendor names, categories)
   - Deduplicate exact full-row duplicates (not the *intentional* near-duplicates — those stay, they're for Phase 5's detection query)
   - Handle nulls: either impute a clear "Unknown" placeholder or flag via a `data_quality_flag` column — pick one approach and document why
3. Keep referential integrity: every FK in Silver invoices/POs/payments should resolve to a Silver vendor/PO/invoice.

**Exit Tests:**
- [ ] All date columns are true DATE type (no strings).
- [ ] `SELECT COUNT(*) FROM SILVER.INVOICES WHERE vendor_id NOT IN (SELECT vendor_id FROM SILVER.VENDORS)` returns 0 (excluding any intentionally-orphaned FK you're tracking separately, e.g. po_id).
- [ ] Your intentional data-quality flaws from Phase 1 (duplicates, orphans, mismatches) are **still present** — Silver should not accidentally clean away the very issues you need to detect in Phase 5. Only *exact accidental* duplicates/nulls should be resolved.
- [ ] A short markdown note exists describing each cleaning rule applied and why.

**Pitfall to avoid:** Over-cleaning and accidentally removing the deliberate anomalies you need for the duplicate-detection and 3-way-match queries later. Cross-check your Phase 1 flaw log against what survives into Silver.

---

### Phase 4 — Gold Layer: Star Schema + SCD Type 2
**Goal:** Build the reporting-ready model exactly as specified in Section 2.1.

**Tasks:**
1. Create `DIM_DATE` (generate via a date-spine SQL script covering your full data date range).
2. Create `DIM_GL_ACCOUNT` from Silver GL accounts.
3. Create `DIM_VENDOR` as **SCD Type 2**:
   - Add `effective_start_date`, `effective_end_date`, `is_current` columns
   - Write a `MERGE` statement pattern that would insert a new row (and close out the old one) if a vendor's `category` or `payment_terms` changed — simulate at least 3–5 vendors having a historical change so the SCD2 logic is actually exercised, not just structurally present with no real history
4. Build `FACT_INVOICE`, `FACT_PAYMENT`, `FACT_PURCHASE_ORDER` from Silver, with FKs pointing to the dimensions.
5. Draw a simple star-schema ER diagram (even hand-drawn/draw.io) for the README.

**Exit Tests:**
- [ ] Every fact table FK joins cleanly to its dimension (no broken joins except your deliberately-orphaned invoice→PO case, which you can explicitly document as "non-PO spend").
- [ ] `DIM_VENDOR` has **more rows than distinct vendors** (proof SCD2 is real, not decorative) — at least 3–5 vendors have 2+ historical rows.
- [ ] Query 5.6 (vendor history snapshot) returns **different `payment_terms` for the same vendor** across different invoice dates for at least one vendor — this is the definitive proof SCD2 works.
- [ ] `DIM_DATE` covers the full min/max date range present in your fact tables with no gaps.
- [ ] ER diagram exists and matches the actual tables built.

**Pitfall to avoid:** Building `DIM_VENDOR` with the SCD2 *columns* but never actually simulating a historical change — this is the most common way people fake this feature without it actually working, and it's exactly what an interviewer will test by asking you to run query 5.6 live.

---

### Phase 5 — Core SQL Analytics (the 6 queries)
**Goal:** Implement and validate all 6 queries from Section 5, and be able to explain each one's business logic in plain English.

**Tasks:**
1. Implement 5.1–5.6 exactly as designed (adapt syntax if needed, e.g., Snowflake-specific functions).
2. For each query, write a 2–3 sentence "business explanation" doc entry (what question it answers, why it matters).
3. Save each query's actual output as a small results screenshot or CSV for your README/portfolio.

**Exit Tests (per query):**
- [ ] **5.1 DPO:** Output has one row per vendor with a plausible average days-to-pay (sanity check: not negative, not absurdly large e.g. 10,000 days).
- [ ] **5.2 Aging buckets:** Every unpaid invoice is bucketed; bucket totals sum to the total unpaid amount; you can see nonzero rows in at least 2–3 different buckets (proves your injected late payments worked).
- [ ] **5.3 Vendor concentration:** `cumulative_pct` reaches ~100% at the last ranked row; RANK has no gaps/ties issues you can't explain.
- [ ] **5.4 Duplicate detection:** The count of returned pairs is in the same ballpark as your Phase 1 ground-truth log of intentionally inserted duplicates (not zero, not wildly more).
- [ ] **5.5 3-way match:** At least the number of "Exception – Review" rows you deliberately created in Phase 1 shows up; `NULLIF` prevents any divide-by-zero errors.
- [ ] **5.6 SCD2 snapshot:** Confirmed different `payment_terms` for the same vendor at different invoice dates (carried over from Phase 4's exit test).
- [ ] You can verbally explain, without looking at notes, what business problem each query solves.

**Pitfall to avoid:** Treating this as "just run the query once and move on." The whole point is being able to *defend* the logic in a live SQL round — rehearse explaining each one out loud.

---

### Phase 6 — Power BI: Data Model + Dashboard
**Goal:** Turn the Gold layer into an interactive, stakeholder-usable 2-page dashboard.

**Tasks:**
1. Connect Power BI to Snowflake (or import Gold CSV exports if direct connection has friction).
2. Build the data model: relationships between facts and dimensions (star schema, single-direction filters where possible).
3. Build DAX measures: Total Outstanding, Avg DPO, % Overdue (using `DIVIDE`), MoM Payment Trend.
4. Build Page 1 (AP Aging & Cash Flow) and Page 2 (Vendor Spend & Risk) exactly per Section 6 spec.
5. Add slicers (date range, vendor category, region) and confirm they filter both visuals and KPI cards correctly.
6. Add drill-through from the 3-way match matrix to invoice detail.

**Exit Tests:**
- [ ] Model view shows a clean star schema (no unintended many-to-many relationships, no circular joins).
- [ ] All 4–5 DAX measures return numbers matching (or very close to) your Phase 5 SQL query outputs — this cross-check is critical; if Power BI's "Avg DPO" doesn't match SQL's, something is wrong in the model.
- [ ] `% Overdue` measure does not error when Total Outstanding is 0 (proves `DIVIDE` used correctly).
- [ ] Slicers, when changed, visibly update every visual and KPI card on the page.
- [ ] Both pages exist with all specified visuals present (KPI cards, stacked bar, line chart, table, Pareto chart, donut, matrix, card).
- [ ] `.pbix` file saved and opens cleanly on a fresh load (no broken data source prompts left unresolved).

**Pitfall to avoid:** Building visuals before validating the data model relationships — broken relationships silently produce wrong numbers that look plausible, which is worse than an obvious error.

---

### Phase 7 — Excel Companion
**Goal:** A lightweight but polished budget-vs-actual sheet, built from Gold-layer exports (not manual typing).

**Tasks:**
1. Export a Gold-layer table (e.g., invoice amounts by GL account/cost center) to CSV/Excel.
2. Build a pivot table: actual spend by GL account/cost center.
3. Add a budget column you define yourself (reasonable, documented assumption).
4. Add a variance column + conditional formatting (red = over budget, green = under).
5. Add one chart: Budget vs Actual by department.
6. Use SUMIFS or XLOOKUP to pull actuals rather than hardcoding numbers.

**Exit Tests:**
- [ ] Actual-spend figures in Excel match the Gold-layer source numbers exactly (spot-check 3–4 cost centers against Snowflake).
- [ ] Variance column formula is a live formula (not hardcoded), and conditional formatting correctly flips color when you change a budget number.
- [ ] Chart updates automatically when the underlying pivot/table changes.

**Pitfall to avoid:** Manually typing numbers instead of formula-linking to the exported data — defeats the stated purpose ("pulling from Gold-layer export rather than manual entry").

---

### Phase 8 — Documentation & GitHub Packaging
**Goal:** Package everything so it reads as a coherent portfolio piece, not a folder of scattered files.

**Tasks:**
1. Write the README with: problem statement, architecture diagram, tech stack, screenshots of both dashboard pages, key SQL snippets with 1–2 line explanations, and the deliverables checklist ticked off.
2. Upload all 5 Bronze CSVs, Silver cleaning scripts, Gold DDL + SCD2 MERGE script, the 6 annotated SQL queries, the `.pbix`, and the Excel workbook.
3. Add a short "Data Quality Issues Simulated" section listing the 5 intentional flaws and which query detects each — this directly showcases Section 3.1/5 alignment.

**Exit Tests:**
- [ ] A person with zero context can read the README top-to-bottom and understand what the project does, why, and how, without opening any other file.
- [ ] Every item in the Section 9 deliverables checklist is present in the repo.
- [ ] Screenshots actually match the current state of the `.pbix` (no stale/outdated images).
- [ ] All SQL files run cleanly end-to-end if someone re-executes them in order (Bronze → Silver → Gold → Analytics) on a fresh schema.

**Pitfall to avoid:** Treating documentation as an afterthought — for an interview-focused project, the README is often the *first* and sometimes *only* thing a recruiter actually opens before the interview.

---

### Phase 9 — Interview Dry-Run / Final Scope Check
**Goal:** Confirm the finished project actually satisfies every original business question from Section 1.2, and that you can defend it live.

**Tasks:**
1. Re-read Section 1.2's five business questions. For each, point to the exact query/visual that answers it.
2. Do a timed, spoken walkthrough (5–7 minutes) covering: architecture → star schema → SCD2 → one SQL query in depth → dashboard tour → business impact framing.
3. Have someone (or yourself, cold) try to break your duplicate/3-way-match logic by asking "what if X" — confirm your CASE/tolerance logic holds up.

**Exit Tests (final scope gate — the whole project passes or fails here):**
- [ ] Business Question 1 (vendor concentration/risk) → answered by query 5.3 + Page 2 Pareto chart. ✅/❌
- [ ] Business Question 2 (DPO/late payment risk) → answered by query 5.1 + Page 1 Avg DPO KPI. ✅/❌
- [ ] Business Question 3 (aging/overdue AP) → answered by query 5.2 + Page 1 stacked bar. ✅/❌
- [ ] Business Question 4 (duplicate/fraud risk) → answered by query 5.4 + Page 2 duplicate-count card. ✅/❌
- [ ] Business Question 5 (3-way match) → answered by query 5.5 + Page 2 exceptions matrix. ✅/❌
- [ ] You can explain SCD Type 2's purpose and demo query 5.6 without hesitation.
- [ ] You can name the medallion layer purpose for Bronze, Silver, and Gold without notes.

If any single item above is ❌, that is scope drift — go back to the relevant phase before considering the project "done," rather than patching it superficially at the end.

---

## Quick Reference: Phase Summary Table

| Phase | Output | Primary Tool |
|---|---|---|
| 0 | Environment ready | Snowflake, Power BI, GitHub |
| 1 | 5 flawed synthetic CSVs | Python/Faker or Excel |
| 2 | Bronze schema loaded | Snowflake |
| 3 | Silver schema cleaned | SQL |
| 4 | Gold star schema + SCD2 | SQL |
| 5 | 6 validated analytics queries | SQL |
| 6 | 2-page Power BI dashboard | Power BI + DAX |
| 7 | Budget vs Actual workbook | Excel |
| 8 | Packaged GitHub repo | GitHub/README |
| 9 | Scope-verified, interview-ready | Self-review |

Build in this order. Do not start Power BI before Gold is validated, and do not start Gold before Silver's cleaning is verified — each phase's exit tests exist precisely to stop errors from silently compounding downstream, which is the single biggest risk in a compressed one-day build.
