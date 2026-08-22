# Phase 0 — Exit Test Report

**Date:** 2026-08-15
**Machine:** Windows 11 Home Single Language 10.0.26200
**Phase goal:** every tool account/access ready so the build sprint isn't derailed by setup friction.

---

## Automated tests (run and verified)

### Test 4 — Python data-generation environment

```
> .venv\Scripts\python.exe -c "import faker, pandas; print('PASS')"
PASS - faker + pandas import cleanly
```

Full installed set, pinned in [`requirements.txt`](../requirements.txt):

| Package | Version |
|---|---|
| faker | 40.36.0 |
| pandas | 3.0.5 |
| numpy | 2.5.2 |
| openpyxl | 3.1.5 |
| XlsxWriter | 3.2.9 |
| snowflake-connector-python | 4.7.2 |
| python-dotenv | (latest) |

**Result: ✅ PASS**

---

### Test 5 — Repository skeleton

```
> 14 required paths checked
PASS - all 14 required paths present
```

All folders carry a `.gitkeep` so the skeleton survives a fresh clone (git does not track
empty directories — without these the structure would silently vanish for anyone cloning
the repo).

Git repository initialized on branch `main`, two commits:

```
04ebba6  Phase 0: add Snowflake automation harness
a6e6aa3  Phase 0: environment, repo skeleton, and design decisions
```

**Remote:** intentionally not created. Decision taken to keep the repo local until Phase 8,
when it is actually presentable.

**Result: ✅ PASS**

---

### Extra — Snowflake runner harness

Not in the original exit-test list; added because Snowflake execution is being automated
rather than driven through the Snowsight UI.

```
> .venv\Scripts\python.exe scripts\sf.py test
Missing required environment variable(s): SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER
Create D:\sanjay\DataPlatter\.env using .env.example as a template.
```

Correct behaviour with no credentials present: fails fast with an actionable message rather
than a stack trace. Argument parsing verified for all four subcommands (`test`, `run`,
`query`, `put`).

**Result: ✅ PASS** (harness ready; awaiting credentials)

---

## Snowflake

Trial account created (Standard edition, AWS). Snowflake version **10.28.101**.
Environment built by running the Phase 0 setup script through the harness:

```
> .venv\Scripts\python.exe scripts\sf.py run sql\00_setup\01_create_database_and_schemas.sql
[ 2/13] Warehouse FIN_PROC_WH successfully created.
[ 3/13] Database FIN_PROC_DB successfully created.
[ 5/13] Schema BRONZE successfully created.
[ 6/13] Schema SILVER successfully created.
[ 7/13] Schema GOLD successfully created.
[ 9/13] File format CSV_STANDARD successfully created.
[10/13] Stage area RAW_FILES successfully created.
DONE — 13 statement(s) executed across 1 file(s).
```

### Test 1 — session context resolves

```
CURRENT_WAREHOUSE | CURRENT_DATABASE | CURRENT_SCHEMA | CURRENT_ROLE | SNOWFLAKE_VERSION
------------------+------------------+----------------+--------------+------------------
FIN_PROC_WH       | FIN_PROC_DB      | BRONZE         | ACCOUNTADMIN | 10.28.101
```

**Result: ✅ PASS**

### Test 2 — three schemas exist, and are empty

```
SCHEMA_NAME | COMMENT
------------+------------------------------------------------------------
BRONZE      | Raw landing zone. Source extracts loaded verbatim, all VARCH
GOLD        | Business-ready star schema. Facts + dimensions, DIM_VENDOR i
SILVER      | Cleaned + conformed. Typed columns, standardized text/dates,
(3 rows)

SCHEMAS_FOUND | RESULT
--------------+-------
3             | PASS
```

Emptiness check (Phase 0 creates structure, never data):

```
TABLE_SCHEMA | TABLE_NAME | ROW_COUNT
-------------+------------+----------
(0 rows)
```

Supporting objects for the Phase 2 load also confirmed present:

| Object | Type | Location |
|---|---|---|
| `CSV_STANDARD` | FILE FORMAT | `FIN_PROC_DB.BRONZE` |
| `RAW_FILES` | STAGE (internal) | `FIN_PROC_DB.BRONZE` |

**Result: ✅ PASS**

---

## Power BI Desktop

### Test 3 — Power BI Desktop opens and can create a blank report

The `winget install --id Microsoft.PowerBI --silent` attempt **stalled**: winget resolved the
package (v2.156.951.0) and began downloading, but wrote 0 bytes and hung indefinitely.

Diagnosed by inspecting the partial download:

```
Length LastWriteTime       FullName
------ -------------       --------
     0 15-08-2026 22:15:30 ...\Temp\WinGet\Microsoft.PowerBI.2.156.951.0\DOEFDC...
```

Falling back to a direct download of the same installer, resumable and retrying:

```
> curl.exe -L -C - --retry 20 --retry-all-errors --output PBIDesktopSetup_x64.exe <url>
```

Download completed with an exact byte match, and the installer was verified as authentic
Microsoft-signed code before being executed:

```
bytes  : 731450544  (expected 731450544)  -> match
status : Valid
signer : CN=Microsoft Corporation, O=Microsoft Corporation, L=Redmond, S=Washington, C=US
```

Installed silently (`-quiet -norestart ACCEPT_EULA=1`, elevated), exit code 0:

```
product : 2.156.951.0 (26.07)+c9381f8e5efc99c8de04425f1572e841914690d8
signed  : Valid

DisplayName    : Microsoft Power BI Desktop (x64)
DisplayVersion : 2.156.951.0
```

Launch test — the exit test requires the app to open and create a blank report:

```
launched pid 10004
MAIN WINDOW OPENED after 4.2s
APPLICATION WINDOW READY
title  : Untitled - Power BI Desktop
memory : 350 MB
```

`Untitled - Power BI Desktop` is the blank-report state, so the test is satisfied.

**Result: ✅ PASS**

### Bonus check — Snowflake connectivity for Phase 6

Confirmed the Snowflake drivers ship with this build, so Phase 6 can connect Power BI directly
to Snowflake rather than falling back to CSV exports:

```
Simba Snowflake ODBC Driver
SnowflakeODBC_sb64.dll
libadbc_driver_snowflake.dll
```

---

## Summary

| # | Test | Owner | Result |
|---|---|---|---|
| 1 | `CURRENT_WAREHOUSE()` / `CURRENT_DATABASE()` succeed | Snowflake | ✅ PASS |
| 2 | `BRONZE`, `SILVER`, `GOLD` schemas exist and are empty | Snowflake | ✅ PASS |
| 3 | Power BI Desktop opens, can create a blank report | Local | ✅ PASS |
| 4 | `import faker, pandas` runs clean | Local | ✅ PASS |
| 5 | Repo skeleton + README exist | Local | ✅ PASS |

**All 5 exit tests pass. Phase 0 is complete.** The environment is fully provisioned: a live
Snowflake account with the medallion schemas in place, Power BI Desktop with working Snowflake
drivers, a pinned Python data-generation environment, and a committed repo skeleton.

Phase 1 (synthetic data generation) is unblocked.

---

## Credential handling

Snowflake credentials live in `.env` at the repo root, which is git-ignored via the `*.env`
rule in [`.gitignore`](../.gitignore):

```
> git check-ignore -v .env
.gitignore:9:*.env      .env

> git grep -n "<password>"
clean - no password in tracked/committed content
```

[`.env.example`](../.env.example) is the committed template and holds only blank placeholders.
Verified that no credential has ever entered git history.
