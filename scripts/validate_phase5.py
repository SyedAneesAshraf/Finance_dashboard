"""
Phase 5 exit tests — the six analytical queries.

Each query has its own pass criteria from the roadmap. Several are cross-checked
against docs/ground_truth.json, which converts "the number looks plausible" into
a real pass/fail: a duplicate-detection query returning 9 pairs and one returning
14 both look reasonable, and only one of them is right.

Run:
    .venv\\Scripts\\python.exe scripts\\validate_phase5.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import sf
from snowflake.connector.util_text import split_statements

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "sql" / "04_analytics"
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


def run_query_file(cur, filename: str):
    """Execute a query file and return (columns, rows) of its final result set."""
    sql = (SQL_DIR / filename).read_text(encoding="utf-8")
    statements = [
        s for s, _ in split_statements(io.StringIO(sql), remove_comments=False)
        if s and s.strip()
    ]
    columns, rows = None, None
    for stmt in statements:
        cur.execute(stmt)
        if cur.description and len(cur.description) > 1:
            columns = [d[0] for d in cur.description]
            rows = cur.fetchall()
    return columns, rows


def col(columns, rows, name):
    i = columns.index(name)
    return [r[i] for r in rows]


def main() -> int:
    print("=" * 78)
    print("PHASE 5 EXIT TESTS — THE SIX ANALYTICAL QUERIES")
    print("=" * 78)

    gt = GROUND_TRUTH
    kpi = gt["kpi_cross_check"]
    conn = sf.connect()
    try:
        cur = conn.cursor()

        # ===============================================================
        print("\n5.1  DPO by vendor — CTE + aggregate")
        # ===============================================================
        c, r = run_query_file(cur, "51_dpo_by_vendor.sql")
        vendors = col(c, r, "VENDOR_ID")
        dpo = [float(x) for x in col(c, r, "AVG_DPO_DAYS")]

        check(f"{len(r)} rows, one per vendor with paid invoices",
              len(vendors) == len(set(vendors)))
        check(f"DPO range {min(dpo):.1f} to {max(dpo):.1f} days is plausible",
              all(0 < d < 365 for d in dpo),
              "sanity check: no negatives (payment before invoice) and nothing absurd")

        # The per-vendor averages must reconcile with the portfolio-wide figure.
        overall = float(next(
            cur.execute("SELECT AVG(DAYS_TO_PAY) FROM GOLD.FACT_PAYMENT").fetchone()
        )[0]) if False else None
        cur.execute("SELECT AVG(DAYS_TO_PAY) FROM GOLD.FACT_PAYMENT")
        overall = float(cur.fetchone()[0])
        check(f"portfolio-wide DPO {overall:.2f} days matches ground truth",
              abs(overall - kpi["avg_dpo_days"]) < 0.01,
              f"ground truth {kpi['avg_dpo_days']}")

        beyond = [float(x) for x in col(c, r, "AVG_DAYS_BEYOND_TERMS")]
        check(f"{sum(1 for b in beyond if b > 0)} vendors paid beyond agreed terms",
              any(b > 0 for b in beyond),
              "raw DPO alone cannot distinguish good treasury from chronic lateness")

        # ===============================================================
        print("\n5.2  AP aging buckets — CASE + DATEDIFF")
        # ===============================================================
        c, r = run_query_file(cur, "52_ap_aging_buckets.sql")
        counts = col(c, r, "INVOICE_COUNT")
        amounts = [float(x) for x in col(c, r, "OUTSTANDING_USD")]
        buckets = col(c, r, "AGING_BUCKET")

        cur.execute("""
            SELECT COUNT(*), SUM(AMOUNT_USD) FROM GOLD.FACT_INVOICE WHERE STATUS = 'Unpaid'
        """)
        total_n, total_amt = cur.fetchone()

        check(f"every unpaid invoice is bucketed ({sum(counts)} of {total_n})",
              sum(counts) == total_n)
        check(f"bucket totals sum to total unpaid ({sum(amounts):,.2f})",
              abs(sum(amounts) - float(total_amt)) < 0.01,
              f"total outstanding {float(total_amt):,.2f}")
        check(f"{len(buckets)} buckets populated (roadmap wants 2-3+)",
              len(buckets) >= 3,
              "  ".join(f"{b}={n}" for b, n in zip(buckets, counts)))

        pct = [float(x) for x in col(c, r, "PCT_OF_OUTSTANDING")]
        check(f"bucket percentages sum to {sum(pct):.2f}%", abs(sum(pct) - 100) < 0.05)

        # ===============================================================
        print("\n5.3  Vendor spend concentration — window functions")
        # ===============================================================
        c, r = run_query_file(cur, "53_vendor_spend_concentration.sql")
        cum = [float(x) for x in col(c, r, "CUMULATIVE_PCT")]
        ranks = [int(x) for x in col(c, r, "SPEND_RANK")]
        spend = [float(x) for x in col(c, r, "TOTAL_SPEND_USD")]
        pct_total = [float(x) for x in col(c, r, "PCT_OF_TOTAL")]

        check(f"cumulative_pct reaches {cum[-1]:.2f}% on the last ranked row",
              abs(cum[-1] - 100.0) < 0.05)
        check("cumulative_pct increases monotonically",
              all(cum[i] <= cum[i + 1] + 1e-9 for i in range(len(cum) - 1)),
              "an explicit ROWS frame prevents tied vendors sharing a running total")
        check(f"RANK runs 1..{len(ranks)} with no gaps or ties",
              ranks == list(range(1, len(ranks) + 1)),
              "the VENDOR_ID tiebreaker makes the ranking deterministic")
        check(f"pct_of_total sums to {sum(pct_total):.2f}%", abs(sum(pct_total) - 100) < 0.1)
        check(f"total spend {sum(spend):,.2f} matches ground truth",
              abs(sum(spend) - kpi["total_invoiced_usd"]) < 0.5,
              f"ground truth {kpi['total_invoiced_usd']:,.2f}")

        core = sum(1 for x in col(c, r, "PARETO_SEGMENT") if x == "Core 80% of spend")
        check(f"{core} of {len(r)} vendors carry the first 80% of spend",
              0 < core < len(r),
              f"{100*core/len(r):.0f}% of vendors — the concentration finding")

        # ===============================================================
        print("\n5.4  Duplicate invoice detection — self-join")
        # ===============================================================
        c, r = run_query_file(cur, "54_duplicate_invoice_detection.sql")
        gt_pairs = gt["defect_1_duplicate_invoice_pairs"]["count"]
        check(f"{len(r)} duplicate pairs found, ground truth injected {gt_pairs}",
              len(r) == gt_pairs,
              "exact match: not zero, not inflated by mirrored pairs")

        pairs = list(zip(col(c, r, "INVOICE_1"), col(c, r, "INVOICE_2")))
        check("no pair is reported twice and no invoice matches itself",
              len(set(pairs)) == len(pairs)
              and all(a < b for a, b in pairs)
              and not any(a == b for a, b in pairs),
              "a.INVOICE_ID < b.INVOICE_ID does all three jobs")

        gt_set = {
            (p["original"], p["duplicate"])
            for p in gt["defect_1_duplicate_invoice_pairs"]["pairs"]
        }
        found_set = {(a, b) if a < b else (b, a) for a, b in pairs}
        gt_norm = {(a, b) if a < b else (b, a) for a, b in gt_set}
        check(f"{len(gt_norm & found_set)}/{len(gt_norm)} injected pairs recovered exactly",
              gt_norm == found_set,
              "matches the ground-truth list identity by identity, not just by count")

        days = [int(x) for x in col(c, r, "DAYS_APART")]
        check(f"all pairs fall within the 3-day tolerance (max {max(days)})",
              all(0 <= d <= 3 for d in days))

        # ===============================================================
        print("\n5.5  Three-way match variance — aggregate + CASE + NULLIF")
        # ===============================================================
        c, r = run_query_file(cur, "55_three_way_match_variance.sql")
        status = col(c, r, "MATCH_STATUS")
        exceptions = sum(1 for s in status if s == "Exception - Review")
        expected = gt["defect_3_price_mismatch_pos"]["expected_query_5_5_exceptions"]

        check(f"{exceptions} exceptions found, expected {expected}",
              exceptions == expected,
              f"{gt['defect_3_price_mismatch_pos']['count']} deliberately mis-priced + "
              f"{gt['defect_3_price_mismatch_pos']['attributable_to_duplicate_invoices']} "
              "inflated by a duplicate invoice sharing the PO")

        check(f"{len(r)} POs assessed (480 total minus 60 never invoiced)",
              len(r) == 420)

        # NULLIF's whole job is that this query returns at all.
        variances = col(c, r, "PCT_VARIANCE")
        check("no divide-by-zero — the query completed and returned rows",
              len(r) > 0,
              "NULLIF(po_amount, 0) yields NULL rather than raising")

        etypes = col(c, r, "EXCEPTION_TYPE")
        over = sum(1 for e in etypes if e.startswith("Over-billed"))
        under = sum(1 for e in etypes if e.startswith("Under-billed"))
        check(f"{over} over-billed and {under} under-billed exceptions",
              over + under == exceptions,
              "over-billing is money at risk now; under-billing is an unrecorded liability")

        no_receipt = sum(1 for s in col(c, r, "RECEIPT_STATUS") if s == "NO GOODS RECEIPT")
        check(f"{no_receipt} invoiced POs have no goods receipt", no_receipt > 0,
              "leg 1 of the three-way match: billed for goods never recorded as received")

        # ===============================================================
        print("\n5.6  Vendor history snapshot — SCD2 range join")
        # ===============================================================
        c, r = run_query_file(cur, "56_vendor_history_scd2.sql")
        vids = col(c, r, "VENDOR_ID")
        then = col(c, r, "TERMS_AT_INVOICE_DATE")
        now = col(c, r, "TERMS_TODAY")

        differing_vendors = {v for v, t, n in zip(vids, then, now) if t != n}
        check(
            f"{len(differing_vendors)} vendors report terms differing from today's",
            len(differing_vendors) >= 1,
            "the definitive SCD2 proof — a current-only table cannot produce this",
        )

        by_vendor: dict[str, set] = {}
        for v, t in zip(vids, then):
            by_vendor.setdefault(v, set()).add(t)
        multi = {v: ts for v, ts in by_vendor.items() if len(ts) > 1}
        check(
            f"{len(multi)} vendors show DIFFERENT terms across their own invoices",
            len(multi) >= 1,
            "\n".join(f"{v}: {' vs '.join(sorted(ts))}" for v, ts in sorted(multi.items())),
        )

        invoice_ids = col(c, r, "INVOICE_ID")
        check(f"{len(invoice_ids)} rows for {len(set(invoice_ids))} invoices — no fan-out",
              len(invoice_ids) == len(set(invoice_ids)),
              "abutting non-overlapping ranges give exactly one dimension row per invoice")

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
