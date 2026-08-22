# What is this project, actually?

## The one-line version
A fake company's messy finance data → cleaned and modeled in a cloud warehouse →
turned into a dashboard and SQL answers. Built as a portfolio piece for a
Dataplatr Data Analytics & BI internship application.

## The story behind it
Dataplatr's actual job is building pre-made data models on top of enterprise
systems (SAP, Oracle EBS, Workday, Salesforce) for clients. This project fakes
that whole workflow end-to-end so there's something real to talk about in an
interview instead of "I read about star schemas once."

The fake setup: **Meridian Manufacturing Group**, a mid-size manufacturer
running Oracle EBS, has a Finance team that currently does everything by hand —
exporting spreadsheets, reconciling them manually, redoing it every month. This
project is the replacement for that: a governed pipeline + dashboard.

## What it answers
Five questions a real Finance/Procurement team cares about:

1. **Which vendors do we spend the most with?** — are we too dependent on a few?
2. **How long do we take to pay invoices (DPO)?** — are we paying late?
3. **How much unpaid AP is overdue, and by how much?** — cash flow risk.
4. **Are there duplicate/suspicious invoices?** — fraud/error risk.
5. **Do POs, receipts, and invoices actually match?** — the "3-way match" audit control.

## How it's built (the pipeline)
```
5 synthetic CSVs (invoices, POs, vendors, payments, GL accounts)
     — generated with Python/Faker, deliberately messy on purpose
         (duplicates, missing fields, mismatched amounts, late payments)
                    │
                    ▼
        SNOWFLAKE (Bronze → Silver → Gold)
   Bronze = raw, untouched load
   Silver = cleaned, typed, standardized — but the deliberate "flaws" above
            are kept, because later queries need to find them
   Gold   = a proper star schema (fact tables + dimensions), including a
            vendor dimension with full history tracking (SCD Type 2 —
            i.e. it remembers what a vendor's payment terms *used to be*)
                    │
                    ▼
        6 analytical SQL queries answer the 5 questions above
                    │
                    ▼
        Power BI dashboard (2 pages) + an Excel budget-vs-actual sheet
```

The "messiness" is the point — a clean dataset wouldn't prove you can actually
clean data or catch fraud/errors. Every defect injected in step 1 is something
a later query is specifically built to catch.

## Where it stands right now
| Phase | What | Status |
|---|---|---|
| 0 | Environment + design decisions locked | ✅ Done |
| 1 | 5 synthetic CSVs w/ injected defects | ✅ Done, tested |
| 2 | Bronze layer loaded into Snowflake | ✅ Done, tested |
| 3 | Silver layer cleaned | ✅ Done, tested |
| 4 | Gold star schema + SCD Type 2 vendor history | ✅ Done, tested |
| 5 | 6 analytical SQL queries | ✅ Done, tested |
| 6 | Power BI dashboard | 🟡 Semantic model + both report pages already built (measures, relationships, visuals) — just never opened in Power BI Desktop to confirm it actually renders/refreshes, and no screenshots taken yet |
| 7 | Excel budget-vs-actual companion | ✅ Done, verified — live formulas checked by actually driving Excel via COM automation |
| 8 | Final README/docs packaging | 🟡 Mostly there — needs screenshots + a final pass once Phase 6 is confirmed |
| 9 | Interview dry-run / scope check | ⬜ Not started |

So: the hard data engineering part (Snowflake pipeline + SQL) is fully done and
tested, and the Excel companion is now done too. What's left is verifying the
Power BI dashboard actually opens/refreshes cleanly (needs a human at Power BI
Desktop — that's not something scriptable), plus a final documentation pass.
