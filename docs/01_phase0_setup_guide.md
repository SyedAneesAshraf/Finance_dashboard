# Phase 0 — Environment Setup Guide

Goal: every tool ready before any build work starts, so the sprint isn't derailed by setup
friction later.

Legend: **[auto]** = already done for you · **[you]** = needs a human (account signup, UI click)

---

## 1. Repository skeleton — **[auto]** ✅

```
DataPlatter/
├── data_raw/          5 synthetic source CSVs            (Phase 1)
├── sql/
│   ├── 00_setup/      warehouse + database + schemas     (Phase 0)
│   ├── 01_bronze/     raw landing DDL + COPY INTO        (Phase 2)
│   ├── 02_silver/     cleaning + standardization         (Phase 3)
│   ├── 03_gold/       star schema DDL + SCD2 MERGE       (Phase 4)
│   └── 04_analytics/  the 6 analytical queries           (Phase 5)
├── powerbi/           .pbix dashboard                    (Phase 6)
├── excel/             budget-vs-actual workbook          (Phase 7)
├── exports/           Gold CSV extracts for BI/Excel     (Phase 6/7)
├── scripts/           Python generator + helpers         (Phase 1)
└── docs/              design decisions, notes, diagrams, screenshots
```

## 2. Python environment — **[auto]** ✅

A local virtual environment at `.venv/` (git-ignored) with:

| Package | Version | Used for |
|---|---|---|
| faker | 40.36.0 | realistic vendor names, addresses |
| pandas | 3.0.5 | CSV assembly and validation |
| numpy | 2.5.2 | seeded random distributions |
| openpyxl | 3.1.5 | Excel workbook (Phase 7) |
| XlsxWriter | 3.2.9 | Excel charts + conditional formatting (Phase 7) |

Reproduce on another machine with `pip install -r requirements.txt`.

## 3. Power BI Desktop — **[auto]** ✅

Version **2.156.951.0 (26.07)**, installed to `C:\Program Files\Microsoft Power BI Desktop\`.

`winget install Microsoft.PowerBI` was tried first and **stalled at 0 bytes downloaded**. The
working route was a direct, resumable download of the same Microsoft installer:

```powershell
curl.exe -L -C - --retry 20 --retry-all-errors --output PBIDesktopSetup_x64.exe `
  https://download.microsoft.com/download/8/8/0/880BCA75-79DD-466A-927D-1ABF1F5454B0/PBIDesktopSetup-2026-07_x64.exe

# verify before executing a 700 MB binary
Get-AuthenticodeSignature .\PBIDesktopSetup_x64.exe      # expect Status = Valid, Microsoft Corporation

# silent install (needs elevation)
Start-Process .\PBIDesktopSetup_x64.exe -ArgumentList '-quiet','-norestart','ACCEPT_EULA=1' -Verb RunAs -Wait
```

The installer is ~698 MB, so allow time on a slow connection. `-C -` makes the download
resumable, which matters at that size.

> Power BI Desktop is Windows-only, which is why this project is built on Windows. If both
> routes fail, the fallback is the Microsoft Store version (`9NTXR16HNW1T`) — it auto-updates
> and needs no admin rights.

**Phase 6 note:** this build ships the Simba Snowflake ODBC driver and
`libadbc_driver_snowflake.dll`, so Power BI can connect straight to Snowflake — no CSV-export
workaround needed.

## 4. Snowflake account — **[you]** ⬅ *this is the one step nobody can do for you*

A trial account needs a real email and a phone verification, so it has to be your hands.

1. Go to **https://signup.snowflake.com**
2. Fill in name / email / role — any values are fine.
3. Choose:
   - **Edition:** Standard (Enterprise also fine — this project uses nothing edition-specific)
   - **Cloud provider:** AWS
   - **Region:** whichever is nearest you (e.g. *AWS Asia Pacific (Mumbai)*) — lower latency,
     and region choice has no effect on anything built here.
4. Confirm via the activation email, then set your username and password.
5. **Write down your account identifier.** It's in the browser URL of Snowsight:
   `https://app.snowflake.com/<org_name>/<account_name>/` → your identifier is
   `<org_name>-<account_name>`. Power BI will ask for this in Phase 6 as
   `<org_name>-<account_name>.snowflakecomputing.com`.

You get **$400 of credits over 30 days**. This project uses a fraction of one credit — the
X-Small warehouse auto-suspends after 60 seconds of idle time, so leaving it running between
phases costs nothing.

### Then run the setup script

In Snowsight: **Projects → Worksheets → `+` → SQL Worksheet**, paste the contents of
[`sql/00_setup/01_create_database_and_schemas.sql`](../sql/00_setup/01_create_database_and_schemas.sql),
and run it all (`Ctrl+Shift+Enter` runs every statement).

That creates:

| Object | Name |
|---|---|
| Warehouse | `FIN_PROC_WH` (X-Small, auto-suspend 60s) |
| Database | `FIN_PROC_DB` |
| Schemas | `BRONZE`, `SILVER`, `GOLD` |
| File format | `BRONZE.CSV_STANDARD` |
| Stage | `BRONZE.RAW_FILES` |

### Then verify

Run [`sql/00_setup/02_verify_setup.sql`](../sql/00_setup/02_verify_setup.sql). Five test blocks;
the ones that matter:

- **Test 1** returns `FIN_PROC_WH | FIN_PROC_DB | BRONZE`
- **Test 3** returns a single row reading `3 | PASS`
- **Test 5** returns **0 rows** — Phase 0 creates structure, never data

## 5. Git — **[auto]** ✅ local · **[you]** remote

The local repository is initialized and committed. Pushing to GitHub is a separate decision —
see the Phase 0 test report.

---

## Phase 0 exit tests

| # | Test | Who |
|---|---|---|
| 1 | `SELECT CURRENT_WAREHOUSE(), CURRENT_DATABASE();` succeeds in Snowsight | **[you]** |
| 2 | Three empty schemas exist: `BRONZE`, `SILVER`, `GOLD` | **[you]** |
| 3 | Power BI Desktop opens and can create a blank report | **[you]** |
| 4 | `python -c "import faker, pandas"` runs with no error | **[auto]** |
| 5 | Repo exists with the folder skeleton and a README | **[auto]** |

Results are recorded in [`docs/02_phase0_test_report.md`](02_phase0_test_report.md).
