"""
Phase 3 exit tests — Silver layer.

Two opposing things must both be true, and testing only one of them is the classic
way this phase goes wrong:

    Silver DID clean    - types are real, text is standardized, exact duplicate
                          rows are gone, missing values are imputed and flagged.

    Silver did NOT      - the 14 near-duplicate invoice pairs, 18 non-PO invoices,
    over-clean            36 PO variances and 157 late payments all survive intact.

A Silver layer that scores well on the first and badly on the second looks
immaculate and has quietly destroyed everything Phase 5 exists to find.

Run:
    .venv\\Scripts\\python.exe scripts\\validate_phase3.py
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
    print("PHASE 3 EXIT TESTS — SILVER LAYER")
    print("=" * 78)

    gt = GROUND_TRUTH
    conn = sf.connect()
    try:
        cur = conn.cursor()

        # ===============================================================
        print("\n1. Types are real (the roadmap's headline exit test)")
        # ===============================================================
        cur.execute("""
            SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
            FROM FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'SILVER'
              AND (COLUMN_NAME LIKE '%DATE%')
              AND LEFT(COLUMN_NAME, 1) != '_'
            ORDER BY TABLE_NAME, COLUMN_NAME
        """)
        date_cols = cur.fetchall()
        not_date = [c for c in date_cols if c[2] != "DATE"]
        check(
            f"all {len(date_cols)} date columns are true DATE type",
            not not_date,
            "" if not_date else "\n".join(f"{t}.{c} -> {d}" for t, c, d in date_cols[:4])
            + f"\n... and {len(date_cols) - 4} more" if len(date_cols) > 4 else "",
        )

        cur.execute("""
            SELECT COUNT(*) FROM FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'SILVER'
              AND COLUMN_NAME IN ('AMOUNT','AMOUNT_USD','PO_AMOUNT','PO_AMOUNT_USD',
                                  'AMOUNT_PAID','AMOUNT_PAID_USD')
              AND DATA_TYPE != 'NUMBER'
        """)
        check("all amount columns are NUMBER, not text", cur.fetchone()[0] == 0)

        # ===============================================================
        print("\n2. Referential integrity")
        # ===============================================================
        # NOT EXISTS rather than NOT IN: if the subquery ever returned a NULL,
        # NOT IN evaluates to UNKNOWN for every row and silently reports zero
        # orphans -- passing the test by accident. NOT EXISTS has no such trap.
        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES i
            WHERE NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = i.VENDOR_ID)
        """)
        check(f"{n} invoices with an unresolvable vendor_id", n == 0)

        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES i
            WHERE i.PO_ID IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM SILVER.PURCHASE_ORDERS p WHERE p.PO_ID = i.PO_ID)
        """)
        check(f"{n} invoices with an unresolvable po_id (nulls excluded)", n == 0)

        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES i
            WHERE NOT EXISTS (SELECT 1 FROM SILVER.GL_ACCOUNTS g WHERE g.ACCOUNT_ID = i.GL_ACCOUNT_ID)
        """)
        check(f"{n} invoices with an unresolvable gl_account_id", n == 0)

        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.PAYMENTS p
            WHERE NOT EXISTS (SELECT 1 FROM SILVER.INVOICES i WHERE i.INVOICE_ID = p.INVOICE_ID)
        """)
        check(f"{n} payments with an unresolvable invoice_id", n == 0)

        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.PURCHASE_ORDERS p
            WHERE NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = p.VENDOR_ID)
        """)
        check(f"{n} POs with an unresolvable vendor_id", n == 0)

        # ===============================================================
        print("\n3. Silver DID clean — exact duplicates removed")
        # ===============================================================
        inv_n = scalar(cur, "SELECT COUNT(*) FROM SILVER.INVOICES")
        pay_n = scalar(cur, "SELECT COUNT(*) FROM SILVER.PAYMENTS")
        check(f"invoices 675 -> {inv_n} (3 exact duplicate rows removed)", inv_n == 672)
        check(f"payments 587 -> {pay_n} (3 exact duplicate rows removed)", pay_n == 584)

        for table, key in [("INVOICES", "INVOICE_ID"), ("PAYMENTS", "PAYMENT_ID"),
                           ("PURCHASE_ORDERS", "PO_ID"), ("VENDORS", "VENDOR_ID"),
                           ("GL_ACCOUNTS", "ACCOUNT_ID")]:
            n = scalar(cur, f"""
                SELECT COUNT(*) FROM (
                    SELECT {key} FROM SILVER.{table} GROUP BY {key} HAVING COUNT(*) > 1
                )
            """)
            check(f"{table:<16} {key} is unique", n == 0)

        # ===============================================================
        print("\n4. Silver DID clean — standardization")
        # ===============================================================
        cur.execute("SELECT DISTINCT CATEGORY FROM SILVER.VENDORS ORDER BY 1")
        cats = [r[0] for r in cur.fetchall()]
        check(
            f"CATEGORY collapsed from 15 raw variants to {len(cats)} canonical",
            len(cats) == 5 and "Unknown" not in cats,
            ", ".join(cats),
        )

        # Whitespace must be gone from every standardized text column.
        untrimmed = 0
        for table, cols in [
            ("VENDORS", ["VENDOR_NAME", "CATEGORY", "REGION", "PAYMENT_TERMS", "CURRENCY"]),
            ("PURCHASE_ORDERS", ["PO_STATUS", "CURRENCY"]),
            ("INVOICES", ["STATUS", "CURRENCY"]),
            ("PAYMENTS", ["PAYMENT_METHOD", "CURRENCY"]),
        ]:
            cond = " OR ".join(f"{c} != TRIM({c})" for c in cols)
            untrimmed += scalar(cur, f"SELECT COUNT(*) FROM SILVER.{table} WHERE {cond}")
        check(f"{untrimmed} values still carry untrimmed whitespace", untrimmed == 0,
              "Bronze had 51 in PO_STATUS alone")

        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES WHERE STATUS NOT IN ('Paid','Unpaid')
        """)
        check(f"{n} invoices with a non-canonical status", n == 0)

        # ===============================================================
        print("\n5. Silver DID clean — nulls imputed AND flagged")
        # ===============================================================
        n_terms = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS
            WHERE PAYMENT_TERMS = 'Unknown'
              AND DATA_QUALITY_FLAGS LIKE '%MISSING_PAYMENT_TERMS%'
        """)
        n_region = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS
            WHERE REGION = 'Unknown' AND DATA_QUALITY_FLAGS LIKE '%MISSING_REGION%'
        """)
        exp_terms = len(gt["defect_5_missing_fields"]["vendors_missing_payment_terms"])
        exp_region = len(gt["defect_5_missing_fields"]["vendors_missing_region"])
        check(f"{n_terms} vendors: terms imputed to 'Unknown' AND flagged", n_terms == exp_terms)
        check(f"{n_region} vendors: region imputed to 'Unknown' AND flagged", n_region == exp_region)

        # Imputation without flagging would destroy the evidence; this asserts
        # no value was quietly replaced without leaving a trace.
        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS
            WHERE (PAYMENT_TERMS = 'Unknown' OR REGION = 'Unknown')
              AND NOT IS_DQ_FLAGGED
        """)
        check(f"{n} imputed values with no audit flag", n == 0,
              "every imputation is traceable")

        n = scalar(cur, "SELECT COUNT(*) FROM SILVER.VENDORS WHERE PAYMENT_TERMS IS NULL OR REGION IS NULL")
        check(f"{n} NULLs left in standardized vendor columns", n == 0,
              "nulls here would break GROUP BY and Power BI slicers")

        # ===============================================================
        print("\n6. Silver did NOT over-clean — every injected defect survives")
        # ===============================================================
        gt_pairs = gt["defect_1_duplicate_invoice_pairs"]["count"]
        n_pairs = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES a
            JOIN SILVER.INVOICES b
              ON a.VENDOR_ID = b.VENDOR_ID
             AND a.AMOUNT    = b.AMOUNT
             AND a.INVOICE_ID < b.INVOICE_ID
             AND ABS(DATEDIFF('day', a.INVOICE_DATE, b.INVOICE_DATE)) <= 3
        """)
        check(
            f"{n_pairs} near-duplicate pairs still detectable (injected {gt_pairs})",
            n_pairs >= gt_pairs,
            "the whole subject of query 5.4 — collapsing these would leave it nothing to find",
        )

        n_orphan = scalar(cur, "SELECT COUNT(*) FROM SILVER.INVOICES WHERE PO_ID IS NULL")
        check(f"{n_orphan} non-PO invoices still have NULL po_id",
              n_orphan == gt["defect_2_orphan_invoices"]["count"],
              "not backfilled with a placeholder PO")

        n_var = scalar(cur, """
            WITH billed AS (
                SELECT PO_ID, SUM(AMOUNT) AS invoiced
                FROM SILVER.INVOICES WHERE PO_ID IS NOT NULL GROUP BY PO_ID
            )
            SELECT COUNT(*) FROM billed b
            JOIN SILVER.PURCHASE_ORDERS p ON p.PO_ID = b.PO_ID
            WHERE ABS(b.invoiced - p.PO_AMOUNT) / NULLIF(p.PO_AMOUNT, 0) > 0.05
        """)
        exp_var = gt["defect_3_price_mismatch_pos"]["expected_query_5_5_exceptions"]
        check(f"{n_var} POs still breach the 5% tolerance (expected {exp_var})",
              n_var == exp_var, "amounts were not 'corrected' toward the PO")

        n_late = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.PAYMENTS p
            JOIN SILVER.INVOICES i ON i.INVOICE_ID = p.INVOICE_ID
            WHERE p.PAYMENT_DATE > i.DUE_DATE
        """)
        check(f"{n_late} payments still settled after the due date",
              n_late == gt["defect_4_late_payments"]["count"])

        n_unpaid = scalar(cur, "SELECT COUNT(*) FROM SILVER.INVOICES WHERE STATUS = 'Unpaid'")
        check(f"{n_unpaid} invoices still unpaid",
              n_unpaid == gt["kpi_cross_check"]["unpaid_invoice_count"])

        # ===============================================================
        print("\n7. FX conversion reproduces the ground-truth totals exactly")
        # ===============================================================
        kpi = gt["kpi_cross_check"]

        total_inv = float(scalar(cur, "SELECT SUM(AMOUNT_USD) FROM SILVER.INVOICES"))
        check(f"total invoiced USD {total_inv:,.2f}",
              abs(total_inv - kpi["total_invoiced_usd"]) < 0.01,
              f"ground truth {kpi['total_invoiced_usd']:,.2f}")

        total_out = float(scalar(cur,
            "SELECT SUM(AMOUNT_USD) FROM SILVER.INVOICES WHERE STATUS = 'Unpaid'"))
        check(f"total outstanding USD {total_out:,.2f}",
              abs(total_out - kpi["total_outstanding_usd"]) < 0.01,
              f"ground truth {kpi['total_outstanding_usd']:,.2f}")

        avg_dpo = float(scalar(cur, """
            SELECT AVG(DATEDIFF('day', i.INVOICE_DATE, p.PAYMENT_DATE))
            FROM SILVER.INVOICES i
            JOIN SILVER.PAYMENTS p ON p.INVOICE_ID = i.INVOICE_ID
        """))
        check(f"average DPO {avg_dpo:.2f} days",
              abs(avg_dpo - kpi["avg_dpo_days"]) < 0.01,
              f"ground truth {kpi['avg_dpo_days']} days")

        # A non-USD row proves the conversion actually applied rather than
        # AMOUNT_USD being a copy of AMOUNT.
        n = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.INVOICES
            WHERE CURRENCY != 'USD' AND AMOUNT_USD = AMOUNT
        """)
        check(f"{n} non-USD invoices where AMOUNT_USD was left unconverted", n == 0)

        # ===============================================================
        print("\n8. SCD Type 2 source is ready for Phase 4")
        # ===============================================================
        n_changed = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u
            JOIN SILVER.VENDORS v ON v.VENDOR_ID = u.VENDOR_ID
            WHERE u.CATEGORY != v.CATEGORY OR u.PAYMENT_TERMS != v.PAYMENT_TERMS
        """)
        check(f"{n_changed} vendors differ between the cleaned extracts",
              n_changed == gt["scd2_vendor_changes"]["count"])

        # The critical one: after standardization, cosmetic differences must be
        # gone, so the MERGE only ever sees real changes.
        n_spurious = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u
            JOIN SILVER.VENDORS v ON v.VENDOR_ID = u.VENDOR_ID
            WHERE u.VENDOR_NAME != v.VENDOR_NAME
        """)
        check(f"{n_spurious} vendors differ only by name after standardization",
              n_spurious == 0,
              "casing noise can no longer trigger a false SCD2 version")

        n_new = scalar(cur, """
            SELECT COUNT(*) FROM SILVER.VENDORS_UPDATE u
            WHERE NOT EXISTS (SELECT 1 FROM SILVER.VENDORS v WHERE v.VENDOR_ID = u.VENDOR_ID)
        """)
        check(f"{n_new} vendors new in extract 2 (exercises MERGE insert path)",
              n_new == len(gt["new_vendors_in_extract_2"]))

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
