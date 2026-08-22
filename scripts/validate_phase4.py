"""
Phase 4 exit tests — Gold star schema and SCD Type 2.

The roadmap warns that the most common way people fake SCD Type 2 is to build the
*columns* (effective_start / effective_end / is_current) and never actually
simulate a change, leaving a dimension that is structurally correct and
behaviourally inert. Several tests here exist specifically to catch that:

  - DIM_VENDOR must have MORE rows than distinct vendors
  - some facts must bind to NON-current versions
  - query 5.6 must return DIFFERENT terms for the SAME vendor at different dates

The last one is the definitive proof. A dimension can pass the first two by
accident; it can only pass the third if the history is genuinely usable.

Run:
    .venv\\Scripts\\python.exe scripts\\validate_phase4.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import sf

REPO_ROOT = Path(__file__).resolve().parent.parent
GROUND_TRUTH = json.loads(
    (REPO_ROOT / "docs" / "ground_truth.json").read_text(encoding="utf-8")
)

results: list[tuple[str, bool]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def scalar(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


def main() -> int:
    print("=" * 78)
    print("PHASE 4 EXIT TESTS — GOLD STAR SCHEMA + SCD TYPE 2")
    print("=" * 78)

    gt = GROUND_TRUTH
    conn = sf.connect()
    try:
        cur = conn.cursor()

        # ===============================================================
        print("\n1. Every fact FK joins cleanly to its dimension")
        # ===============================================================
        for fact, key, dim, dimkey in [
            ("FACT_INVOICE", "VENDOR_KEY", "DIM_VENDOR", "VENDOR_KEY"),
            ("FACT_INVOICE", "ACCOUNT_KEY", "DIM_GL_ACCOUNT", "ACCOUNT_KEY"),
            ("FACT_INVOICE", "INVOICE_DATE_KEY", "DIM_DATE", "DATE_KEY"),
            ("FACT_INVOICE", "DUE_DATE_KEY", "DIM_DATE", "DATE_KEY"),
            ("FACT_PAYMENT", "VENDOR_KEY", "DIM_VENDOR", "VENDOR_KEY"),
            ("FACT_PAYMENT", "PAYMENT_DATE_KEY", "DIM_DATE", "DATE_KEY"),
            ("FACT_PURCHASE_ORDER", "VENDOR_KEY", "DIM_VENDOR", "VENDOR_KEY"),
            ("FACT_PURCHASE_ORDER", "PO_DATE_KEY", "DIM_DATE", "DATE_KEY"),
        ]:
            n = scalar(cur, f"""
                SELECT COUNT(*) FROM GOLD.{fact} f
                WHERE f.{key} IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM GOLD.{dim} d WHERE d.{dimkey} = f.{key})
            """)
            check(f"{fact}.{key:<18} -> {dim}", n == 0)

        # The one deliberately-broken join, documented as non-PO spend.
        n_orphan = scalar(cur, """
            SELECT COUNT(*) FROM GOLD.FACT_INVOICE f
            WHERE f.PO_ID IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM GOLD.FACT_PURCHASE_ORDER p WHERE p.PO_ID = f.PO_ID)
        """)
        check(f"{n_orphan} invoices reference a non-existent PO", n_orphan == 0)

        n_nonpo = scalar(cur, "SELECT COUNT(*) FROM GOLD.FACT_INVOICE WHERE PO_ID IS NULL")
        check(f"{n_nonpo} invoices have NULL po_id — the documented exception",
              n_nonpo == gt["defect_2_orphan_invoices"]["count"],
              "non-PO spend, deliberately preserved")

        # ===============================================================
        print("\n2. No rows or amounts lost between Silver and Gold")
        # ===============================================================
        for silver, gold in [("INVOICES", "FACT_INVOICE"),
                             ("PAYMENTS", "FACT_PAYMENT"),
                             ("PURCHASE_ORDERS", "FACT_PURCHASE_ORDER")]:
            s = scalar(cur, f"SELECT COUNT(*) FROM SILVER.{silver}")
            g = scalar(cur, f"SELECT COUNT(*) FROM GOLD.{gold}")
            check(f"{gold:<20} {s} -> {g}", s == g)

        gold_inv = float(scalar(cur, "SELECT SUM(AMOUNT_USD) FROM GOLD.FACT_INVOICE"))
        check(f"total invoiced USD {gold_inv:,.2f}",
              abs(gold_inv - gt["kpi_cross_check"]["total_invoiced_usd"]) < 0.01,
              f"ground truth {gt['kpi_cross_check']['total_invoiced_usd']:,.2f}")

        # ===============================================================
        print("\n3. DIM_VENDOR proves SCD Type 2 is real, not decorative")
        # ===============================================================
        rows = scalar(cur, "SELECT COUNT(*) FROM GOLD.DIM_VENDOR")
        distinct = scalar(cur, "SELECT COUNT(DISTINCT VENDOR_ID) FROM GOLD.DIM_VENDOR")
        check(f"{rows} rows for {distinct} distinct vendors", rows > distinct,
              "more rows than vendors is the structural signature of SCD2")

        n_versioned = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT VENDOR_ID FROM GOLD.DIM_VENDOR GROUP BY VENDOR_ID HAVING COUNT(*) >= 2
            )
        """)
        check(f"{n_versioned} vendors carry 2+ historical rows (roadmap wants 3-5)",
              n_versioned >= 3)

        n = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT VENDOR_ID FROM GOLD.DIM_VENDOR
                GROUP BY VENDOR_ID HAVING COUNT_IF(IS_CURRENT) <> 1
            )
        """)
        check(f"{n} vendors without exactly one current version", n == 0)

        # Overlapping ranges would make one invoice match two dimension rows and
        # be silently double-counted by the range join.
        n = scalar(cur, """
            SELECT COUNT(*) FROM GOLD.DIM_VENDOR a
            JOIN GOLD.DIM_VENDOR b
              ON a.VENDOR_ID = b.VENDOR_ID AND a.VENDOR_KEY < b.VENDOR_KEY
             AND a.EFFECTIVE_START_DATE <= b.EFFECTIVE_END_DATE
             AND b.EFFECTIVE_START_DATE <= a.EFFECTIVE_END_DATE
        """)
        check(f"{n} overlapping validity ranges", n == 0,
              "an overlap would double-count every invoice in the overlap window")

        # Gaps are the opposite failure: an invoice falling in the gap matches
        # nothing and vanishes from the report entirely.
        n = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT VENDOR_ID, EFFECTIVE_END_DATE,
                       LEAD(EFFECTIVE_START_DATE) OVER (
                           PARTITION BY VENDOR_ID ORDER BY EFFECTIVE_START_DATE) AS next_start
                FROM GOLD.DIM_VENDOR
            )
            WHERE next_start IS NOT NULL
              AND next_start <> DATEADD('day', 1, EFFECTIVE_END_DATE)
        """)
        check(f"{n} gaps between consecutive versions", n == 0,
              "each version ends the day before the next begins")

        n_hist = scalar(cur, """
            SELECT COUNT(*) FROM GOLD.FACT_INVOICE f
            JOIN GOLD.DIM_VENDOR v ON v.VENDOR_KEY = f.VENDOR_KEY
            WHERE NOT v.IS_CURRENT
        """)
        check(f"{n_hist} invoices bound to a NON-current vendor version", n_hist > 0,
              "if every fact pointed at the current row, SCD2 would be unused")

        # ===============================================================
        print("\n4. Query 5.6 — the definitive SCD2 proof")
        # ===============================================================
        cur.execute("""
            SELECT f.VENDOR_ID,
                   COUNT(DISTINCT v.PAYMENT_TERMS) AS distinct_terms,
                   MIN(v.PAYMENT_TERMS) AS terms_a,
                   MAX(v.PAYMENT_TERMS) AS terms_b,
                   COUNT(*) AS invoices
            FROM GOLD.FACT_INVOICE f
            JOIN GOLD.DIM_VENDOR v
              ON v.VENDOR_ID = f.VENDOR_ID
             AND f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
            GROUP BY f.VENDOR_ID
            HAVING COUNT(DISTINCT v.PAYMENT_TERMS) > 1
            ORDER BY f.VENDOR_ID
        """)
        multi = cur.fetchall()
        check(
            f"{len(multi)} vendors show DIFFERENT terms at different invoice dates",
            len(multi) >= 1,
            "\n".join(f"{r[0]}: {r[2]} vs {r[3]} across {r[4]} invoices" for r in multi),
        )

        # The range join must produce exactly one dimension row per invoice.
        # More than one means overlapping ranges; fewer means a gap.
        n = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT f.INVOICE_ID, COUNT(*) AS matches
                FROM GOLD.FACT_INVOICE f
                JOIN GOLD.DIM_VENDOR v
                  ON v.VENDOR_ID = f.VENDOR_ID
                 AND f.INVOICE_DATE BETWEEN v.EFFECTIVE_START_DATE AND v.EFFECTIVE_END_DATE
                GROUP BY f.INVOICE_ID
                HAVING COUNT(*) <> 1
            )
        """)
        check(f"{n} invoices match other than exactly one vendor version", n == 0)

        # ===============================================================
        print("\n5. Range join and surrogate key agree")
        # ===============================================================
        # Two independent routes to the same answer. If they disagree, the
        # surrogate keys were resolved wrongly at load time and every Power BI
        # figure would be quietly wrong while the SQL stayed right.
        n = scalar(cur, """
            SELECT COUNT(*)
            FROM GOLD.FACT_INVOICE f
            JOIN GOLD.DIM_VENDOR rng
              ON rng.VENDOR_ID = f.VENDOR_ID
             AND f.INVOICE_DATE BETWEEN rng.EFFECTIVE_START_DATE AND rng.EFFECTIVE_END_DATE
            JOIN GOLD.DIM_VENDOR sk ON sk.VENDOR_KEY = f.VENDOR_KEY
            WHERE rng.VENDOR_KEY <> sk.VENDOR_KEY
        """)
        check(f"{n} invoices where the two join routes disagree", n == 0,
              "surrogate keys were resolved to the historically correct version")

        # ===============================================================
        print("\n6. DIM_DATE covers the facts with no gaps")
        # ===============================================================
        n_dates = scalar(cur, "SELECT COUNT(*) FROM GOLD.DIM_DATE")
        n_distinct = scalar(cur, "SELECT COUNT(DISTINCT FULL_DATE) FROM GOLD.DIM_DATE")
        check(f"{n_dates} date rows, all distinct", n_dates == n_distinct == 1461)

        n = scalar(cur, """
            SELECT COUNT(*) FROM (
                SELECT FULL_DATE,
                       LAG(FULL_DATE) OVER (ORDER BY FULL_DATE) AS prev
                FROM GOLD.DIM_DATE
            )
            WHERE prev IS NOT NULL AND DATEDIFF('day', prev, FULL_DATE) <> 1
        """)
        check(f"{n} gaps in the date spine", n == 0)

        cur.execute("""
            SELECT MIN(d), MAX(d) FROM (
                SELECT INVOICE_DATE AS d FROM GOLD.FACT_INVOICE
                UNION ALL SELECT DUE_DATE FROM GOLD.FACT_INVOICE
                UNION ALL SELECT PAYMENT_DATE FROM GOLD.FACT_PAYMENT
                UNION ALL SELECT PO_DATE FROM GOLD.FACT_PURCHASE_ORDER
            )
        """)
        fmin, fmax = cur.fetchone()
        cur.execute("SELECT MIN(FULL_DATE), MAX(FULL_DATE) FROM GOLD.DIM_DATE")
        dmin, dmax = cur.fetchone()
        check(f"DIM_DATE {dmin}..{dmax} spans facts {fmin}..{fmax}",
              dmin <= fmin and dmax >= fmax,
              "padded either side so SAMEPERIODLASTYEAR has somewhere to land")

        # ===============================================================
        print("\n7. Stored measures are correct")
        # ===============================================================
        n = scalar(cur, """
            SELECT COUNT(*) FROM GOLD.FACT_PAYMENT p
            JOIN GOLD.FACT_INVOICE f ON f.INVOICE_ID = p.INVOICE_ID
            WHERE p.DAYS_TO_PAY <> DATEDIFF('day', f.INVOICE_DATE, p.PAYMENT_DATE)
               OR p.DAYS_LATE   <> GREATEST(0, DATEDIFF('day', f.DUE_DATE, p.PAYMENT_DATE))
        """)
        check(f"{n} payments with a miscomputed DAYS_TO_PAY or DAYS_LATE", n == 0)

        avg_dpo = float(scalar(cur, "SELECT AVG(DAYS_TO_PAY) FROM GOLD.FACT_PAYMENT"))
        check(f"average DPO from the stored column: {avg_dpo:.2f} days",
              abs(avg_dpo - gt["kpi_cross_check"]["avg_dpo_days"]) < 0.01,
              f"ground truth {gt['kpi_cross_check']['avg_dpo_days']}")

        n_late = scalar(cur, "SELECT COUNT(*) FROM GOLD.FACT_PAYMENT WHERE IS_LATE")
        check(f"{n_late} payments flagged late",
              n_late == gt["defect_4_late_payments"]["count"])

        # ===============================================================
        print("\n8. ER diagram exists")
        # ===============================================================
        er = REPO_ROOT / "docs" / "09_phase4_er_diagram.md"
        check(f"{er.name} present", er.exists())

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
