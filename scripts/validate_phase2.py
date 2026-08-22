"""
Phase 2 exit tests — Bronze layer fidelity.

The roadmap asks for a 5-random-row spot check per table. This does a full
row-by-row verbatim comparison of all 1,864 rows instead: at this data size the
exhaustive check costs the same as the sample, and a sample of 5 out of 675 would
miss a single corrupted row 99.3% of the time.

Compares the local CSVs against what actually landed in Snowflake, cell by cell,
including whitespace, casing, date formatting, and blank-vs-null.

Run:
    .venv\\Scripts\\python.exe scripts\\validate_phase2.py

Exits 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import sf  # sibling module: connection handling

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data_raw"

# csv file -> (bronze table, business columns in CSV order)
TABLES = {
    "gl_accounts.csv": (
        "GL_ACCOUNTS",
        ["ACCOUNT_ID", "ACCOUNT_NAME", "COST_CENTER", "DEPARTMENT"],
    ),
    "vendors.csv": (
        "VENDORS",
        ["VENDOR_ID", "VENDOR_NAME", "CATEGORY", "REGION", "PAYMENT_TERMS",
         "CURRENCY", "CREATION_DATE", "LAST_UPDATE_DATE"],
    ),
    "vendors_update.csv": (
        "VENDORS_UPDATE",
        ["VENDOR_ID", "VENDOR_NAME", "CATEGORY", "REGION", "PAYMENT_TERMS",
         "CURRENCY", "CREATION_DATE", "LAST_UPDATE_DATE"],
    ),
    "purchase_orders.csv": (
        "PURCHASE_ORDERS",
        ["PO_ID", "VENDOR_ID", "PO_DATE", "PO_AMOUNT", "CURRENCY",
         "GOODS_RECEIPT_DATE", "PO_STATUS"],
    ),
    "invoices.csv": (
        "INVOICES",
        ["INVOICE_ID", "VENDOR_ID", "PO_ID", "GL_ACCOUNT_ID", "INVOICE_DATE",
         "DUE_DATE", "AMOUNT", "CURRENCY", "STATUS"],
    ),
    "payments.csv": (
        "PAYMENTS",
        ["PAYMENT_ID", "INVOICE_ID", "PAYMENT_DATE", "AMOUNT_PAID",
         "CURRENCY", "PAYMENT_METHOD"],
    ),
}

results: list[tuple[str, bool]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def read_csv_rows(path: Path) -> list[tuple]:
    """Read a CSV as tuples, mapping empty string to None to mirror the load."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        # EMPTY_FIELD_AS_NULL + NULL_IF=('') means a blank field lands as SQL NULL.
        # Mapping '' -> None here compares like for like.
        return [tuple(v if v != "" else None for v in row) for row in reader]


def fetch_rows(conn, table: str, columns: list[str]) -> list[tuple]:
    cur = conn.cursor()
    cur.execute(f"SELECT {', '.join(columns)} FROM BRONZE.{table}")
    rows = cur.fetchall()
    cur.close()
    # Every business column is VARCHAR, so anything non-str would itself be a defect.
    return [tuple(v if v is None else str(v) for v in r) for r in rows]


def main() -> int:
    print("=" * 78)
    print("PHASE 2 EXIT TESTS — BRONZE LAYER FIDELITY")
    print("=" * 78)

    conn = sf.connect()
    try:
        cur = conn.cursor()

        # ---------------------------------------------------------------
        print("\n1. Row counts match the source files exactly")
        # ---------------------------------------------------------------
        total_rows = 0
        for fname, (table, _cols) in TABLES.items():
            csv_n = len(read_csv_rows(DATA_DIR / fname))
            cur.execute(f"SELECT COUNT(*) FROM BRONZE.{table}")
            db_n = cur.fetchone()[0]
            total_rows += db_n
            check(f"{table:<16} CSV {csv_n:>4} = Snowflake {db_n:>4}", csv_n == db_n)

        # ---------------------------------------------------------------
        print(f"\n2. Every value matches verbatim ({total_rows} rows, all columns)")
        # ---------------------------------------------------------------
        for fname, (table, cols) in TABLES.items():
            csv_rows = sorted(read_csv_rows(DATA_DIR / fname), key=lambda r: tuple(
                "" if v is None else v for v in r))
            db_rows = sorted(fetch_rows(conn, table, cols), key=lambda r: tuple(
                "" if v is None else v for v in r))

            if len(csv_rows) != len(db_rows):
                check(f"{table:<16} verbatim", False, "row counts differ; skipping cell compare")
                continue

            # Multiset comparison via sorted order: this is deliberate rather than a
            # key-based join, because invoices and payments contain intentional
            # whole-row duplicates whose primary key is NOT unique. A join would
            # either fan out or silently collapse them.
            mismatches = []
            for i, (a, b) in enumerate(zip(csv_rows, db_rows)):
                if a != b:
                    for j, (av, bv) in enumerate(zip(a, b)):
                        if av != bv:
                            mismatches.append(
                                f"row {i} col {cols[j]}: CSV {av!r} != SF {bv!r}"
                            )
            check(
                f"{table:<16} {len(csv_rows):>4} rows x {len(cols)} cols identical",
                not mismatches,
                "" if not mismatches else "\n".join(mismatches[:5]),
            )

        # ---------------------------------------------------------------
        print("\n3. Raw messiness survived the load (Bronze cleaned nothing)")
        # ---------------------------------------------------------------
        cur.execute("SELECT COUNT(DISTINCT CATEGORY) FROM BRONZE.VENDORS")
        raw_cats = cur.fetchone()[0]
        cur.execute("SELECT COUNT(DISTINCT UPPER(TRIM(CATEGORY))) FROM BRONZE.VENDORS")
        norm_cats = cur.fetchone()[0]
        check(
            f"CATEGORY has {raw_cats} raw variants for {norm_cats} real values",
            raw_cats > norm_cats and norm_cats == 5,
            "casing/whitespace noise intact — nothing was standardized on the way in",
        )

        cur.execute("""
            SELECT COUNT(*) FROM BRONZE.PURCHASE_ORDERS
            WHERE PO_STATUS != TRIM(PO_STATUS)
        """)
        untrimmed = cur.fetchone()[0]
        check(f"{untrimmed} PO_STATUS values still carry untrimmed whitespace",
              untrimmed > 0, "TRIM_SPACE = FALSE preserved leading/trailing spaces")

        cur.execute("""
            SELECT
              COUNT_IF(INVOICE_DATE LIKE '____-__-__')         AS iso,
              COUNT_IF(INVOICE_DATE LIKE '__/__/____')         AS us,
              COUNT_IF(INVOICE_DATE LIKE '__-___-____')        AS oracle
            FROM BRONZE.INVOICES
        """)
        iso, us, oracle = cur.fetchone()
        check(
            f"all three date formats present in INVOICE_DATE",
            iso > 0 and us > 0 and oracle > 0,
            f"ISO {iso}, US {us}, Oracle {oracle} — still unparsed strings, as intended",
        )

        # ---------------------------------------------------------------
        print("\n4. Blank source fields landed as real NULLs")
        # ---------------------------------------------------------------
        cur.execute("SELECT COUNT(*) FROM BRONZE.VENDORS WHERE PAYMENT_TERMS IS NULL")
        n_terms = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM BRONZE.VENDORS WHERE REGION IS NULL")
        n_region = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM BRONZE.INVOICES WHERE PO_ID IS NULL")
        n_po = cur.fetchone()[0]
        check(f"{n_terms} vendors NULL payment_terms, {n_region} NULL region",
              n_terms == 4 and n_region == 3)
        check(f"{n_po} invoices NULL po_id (non-PO spend)", n_po == 18)

        # An empty string and a NULL are different claims: one says "the source sent
        # a blank", the other says "the source sent nothing". Mixing them would make
        # Silver's null handling untestable.
        cur.execute("""
            SELECT COUNT(*) FROM BRONZE.VENDORS
            WHERE PAYMENT_TERMS = '' OR REGION = ''
        """)
        empties = cur.fetchone()[0]
        check("no empty strings masquerading as nulls", empties == 0)

        # ---------------------------------------------------------------
        print("\n5. Deliberate duplicates survived (Silver's job, not Bronze's)")
        # ---------------------------------------------------------------
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT INVOICE_ID FROM BRONZE.INVOICES
                GROUP BY INVOICE_ID HAVING COUNT(*) > 1
            )
        """)
        dup_inv = cur.fetchone()[0]
        cur.execute("""
            SELECT COUNT(*) FROM (
                SELECT PAYMENT_ID FROM BRONZE.PAYMENTS
                GROUP BY PAYMENT_ID HAVING COUNT(*) > 1
            )
        """)
        dup_pay = cur.fetchone()[0]
        check(f"{dup_inv} invoice_ids and {dup_pay} payment_ids appear twice",
              dup_inv == 3 and dup_pay == 3,
              "defect 8 intact — Bronze must not deduplicate")

        # ---------------------------------------------------------------
        print("\n6. No transformation logic exists in the BRONZE schema")
        # ---------------------------------------------------------------
        cur.execute("""
            SELECT COUNT(*) FROM FIN_PROC_DB.INFORMATION_SCHEMA.VIEWS
            WHERE TABLE_SCHEMA = 'BRONZE'
        """)
        n_views = cur.fetchone()[0]
        check(f"{n_views} views in BRONZE", n_views == 0,
              "a view here would be derived logic; derivation belongs in Silver")

        # Business columns must all still be VARCHAR. A DATE or NUMBER column would
        # mean a parse decision was made at load time, which is precisely what
        # Bronze exists to defer.
        # LEFT(...) rather than NOT LIKE '\_%' ESCAPE '\': the backslash needed for
        # LIKE escaping also escapes the closing quote, which Snowflake rejects.
        cur.execute("""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'BRONZE'
              AND LEFT(COLUMN_NAME, 1) != '_'
              AND DATA_TYPE != 'TEXT'
        """)
        typed = cur.fetchall()
        check("every business column is still VARCHAR/TEXT", not typed,
              "" if not typed else f"typed columns found: {typed[:5]}")

        # ---------------------------------------------------------------
        print("\n7. Load lineage is populated")
        # ---------------------------------------------------------------
        cur.execute("""
            SELECT COUNT(*) FROM BRONZE.INVOICES
            WHERE _SOURCE_FILE IS NULL OR _FILE_ROW_NUMBER IS NULL OR _LOAD_TS IS NULL
        """)
        missing_meta = cur.fetchone()[0]
        check("every Bronze row carries source file, row number and load timestamp",
              missing_meta == 0)

        cur.execute("""
            SELECT INVOICE_ID, COUNT(*) AS n, MIN(_FILE_ROW_NUMBER), MAX(_FILE_ROW_NUMBER)
            FROM BRONZE.INVOICES
            GROUP BY INVOICE_ID HAVING COUNT(*) > 1
            ORDER BY INVOICE_ID
        """)
        dups = cur.fetchall()
        distinct_rownums = all(r[2] != r[3] for r in dups)
        check(
            "duplicate rows trace to different physical lines in the file",
            distinct_rownums and len(dups) == 3,
            "\n".join(f"{r[0]}: {r[1]} rows at file lines {r[2]} and {r[3]}" for r in dups)
            + "\nproves these are genuinely two rows in the source, not a loader bug",
        )

        cur.close()
    finally:
        conn.close()

    print("\n" + "=" * 78)
    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed
    print(f"RESULT: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 78)
    if failed:
        print("\nFAILURES:")
        for name, ok in results:
            if not ok:
                print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
