"""
Phase 1 exit tests.

Reads the CSVs from disk with pandas and re-derives every claim independently of the
generator's in-memory state. That independence is the point: if the generator had a bug
that made its own summary wrong, validating against its variables would repeat the bug.
Everything here comes from parsing the actual files a downstream consumer would read.

Run:
    .venv\\Scripts\\python.exe scripts\\validate_phase1.py

Exits 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data_raw"
DOCS_DIR = REPO_ROOT / "docs"

AS_OF_DATE = dt.date(2026, 8, 15)
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d-%b-%Y"]

EXPECTED_HEADERS = {
    "gl_accounts.csv": ["account_id", "account_name", "cost_center", "department"],
    "vendors.csv": ["vendor_id", "vendor_name", "category", "region",
                    "payment_terms", "currency", "creation_date", "last_update_date"],
    "vendors_update.csv": ["vendor_id", "vendor_name", "category", "region",
                           "payment_terms", "currency", "creation_date", "last_update_date"],
    "purchase_orders.csv": ["po_id", "vendor_id", "po_date", "po_amount", "currency",
                            "goods_receipt_date", "po_status"],
    "invoices.csv": ["invoice_id", "vendor_id", "po_id", "gl_account_id", "invoice_date",
                     "due_date", "amount", "currency", "status"],
    "payments.csv": ["payment_id", "invoice_id", "payment_date", "amount_paid",
                     "currency", "payment_method"],
}

ROW_COUNT_TARGETS = {
    "vendors.csv": (40, 60),
    "purchase_orders.csv": (300, 500),
    "invoices.csv": (600, 900),
    "payments.csv": (500, 800),
    "gl_accounts.csv": (15, 25),
}

DATE_COLUMNS = {
    "vendors.csv": ["creation_date", "last_update_date"],
    "vendors_update.csv": ["creation_date", "last_update_date"],
    "purchase_orders.csv": ["po_date", "goods_receipt_date"],
    "invoices.csv": ["invoice_date", "due_date"],
    "payments.csv": ["payment_date"],
}

results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append((name, passed, detail))
    print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    if detail:
        for line in detail.splitlines():
            print(f"         {line}")


def parse_date(value) -> dt.date | None:
    """Parse a date written in any of the three injected formats."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def norm(s) -> str:
    """Normalize a text value the way the Silver layer will."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return " ".join(str(s).split()).strip().upper()


def main() -> int:
    print("=" * 78)
    print("PHASE 1 EXIT TESTS")
    print("=" * 78)

    ground_truth = json.loads((DOCS_DIR / "ground_truth.json").read_text(encoding="utf-8"))

    # ---------------------------------------------------------------
    print("\n1. Files load cleanly with expected headers")
    # ---------------------------------------------------------------
    frames: dict[str, pd.DataFrame] = {}
    all_loaded = True
    for fname, expected in EXPECTED_HEADERS.items():
        path = DATA_DIR / fname
        if not path.exists():
            check(f"{fname} exists", False, "file not found")
            all_loaded = False
            continue
        try:
            # dtype=str keeps raw fidelity: no silent type coercion, and a leading
            # zero in an account_id survives instead of becoming an int.
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
        except Exception as exc:
            check(f"{fname} parses as UTF-8 CSV", False, str(exc))
            all_loaded = False
            continue
        frames[fname] = df
        ok = list(df.columns) == expected
        check(
            f"{fname:<22} loads, headers match, {len(df)} rows",
            ok,
            "" if ok else f"expected {expected}\ngot      {list(df.columns)}",
        )
        if not ok:
            all_loaded = False

    if not all_loaded:
        print("\nAborting: files did not load cleanly.")
        return 1

    # ---------------------------------------------------------------
    print("\n2. Row counts within target ranges")
    # ---------------------------------------------------------------
    for fname, (lo, hi) in ROW_COUNT_TARGETS.items():
        n = len(frames[fname])
        check(f"{fname:<22} {n} rows in [{lo}, {hi}]", lo <= n <= hi)

    # ---------------------------------------------------------------
    print("\n3. Referential integrity")
    # ---------------------------------------------------------------
    vendors = frames["vendors.csv"]
    pos = frames["purchase_orders.csv"]
    invoices = frames["invoices.csv"]
    payments = frames["payments.csv"]
    gl = frames["gl_accounts.csv"]

    vendor_ids = set(vendors["vendor_id"])
    po_ids = set(pos["po_id"])
    invoice_ids = set(invoices["invoice_id"])
    account_ids = set(gl["account_id"])

    bad = set(pos["vendor_id"]) - vendor_ids
    check(f"every PO vendor_id resolves ({len(pos)} POs)", not bad,
          "" if not bad else f"orphans: {sorted(bad)[:10]}")

    bad = set(invoices["vendor_id"]) - vendor_ids
    check(f"every invoice vendor_id resolves ({len(invoices)} invoices)", not bad,
          "" if not bad else f"orphans: {sorted(bad)[:10]}")

    inv_po = set(invoices.loc[invoices["po_id"] != "", "po_id"])
    bad = inv_po - po_ids
    check("every non-null invoice po_id resolves", not bad,
          "" if not bad else f"orphans: {sorted(bad)[:10]}")

    bad = set(invoices["gl_account_id"]) - account_ids
    check("every invoice gl_account_id resolves", not bad,
          "" if not bad else f"orphans: {sorted(bad)[:10]}")

    bad = set(payments["invoice_id"]) - invoice_ids
    check("every payment invoice_id resolves", not bad,
          "" if not bad else f"orphans: {sorted(bad)[:10]}")

    # ---------------------------------------------------------------
    print("\n4. Dates parse under exactly one of the three injected formats")
    # ---------------------------------------------------------------
    fmt_usage: dict[str, int] = {"%Y-%m-%d": 0, "%m/%d/%Y": 0, "%d-%b-%Y": 0}
    unparseable: list[str] = []
    for fname, cols in DATE_COLUMNS.items():
        df = frames[fname]
        for col in cols:
            for raw in df[col]:
                s = str(raw).strip()
                if not s:
                    continue      # blanks are legitimate (e.g. no goods receipt)
                hit = None
                for fmt in DATE_FORMATS:
                    try:
                        dt.datetime.strptime(s, fmt)
                        hit = fmt
                        break
                    except ValueError:
                        continue
                if hit:
                    fmt_usage[hit] += 1
                else:
                    unparseable.append(f"{fname}.{col} = {s!r}")

    total_dates = sum(fmt_usage.values())
    check(
        f"all {total_dates} date values parse",
        not unparseable,
        "" if not unparseable else "examples: " + ", ".join(unparseable[:5]),
    )
    mix = ", ".join(
        f"{f} {c} ({100*c/total_dates:.0f}%)" for f, c in fmt_usage.items()
    )
    check("all three date formats present (defect 6)",
          all(c > 0 for c in fmt_usage.values()), mix)

    # ---------------------------------------------------------------
    print("\n5. Defect 1 — near-duplicate invoices")
    # ---------------------------------------------------------------
    # Re-derive using the same rule query 5.4 will use, from the file only.
    inv = invoices.copy()
    inv["_date"] = inv["invoice_date"].map(parse_date)
    inv["_amt"] = inv["amount"].astype(float)
    inv = inv.drop_duplicates(subset=["invoice_id"])     # ignore defect-8 rows here

    pairs = []
    for (vid, amt), grp in inv.groupby(["vendor_id", "_amt"]):
        if len(grp) < 2:
            continue
        recs = grp.sort_values("invoice_id")[["invoice_id", "_date"]].values.tolist()
        for a in range(len(recs)):
            for b in range(a + 1, len(recs)):
                if abs((recs[a][1] - recs[b][1]).days) <= 3:
                    pairs.append((recs[a][0], recs[b][0], vid, amt))

    gt_pairs = ground_truth["defect_1_duplicate_invoice_pairs"]["count"]
    check(
        f"detected {len(pairs)} duplicate pairs, ground truth injected {gt_pairs}",
        len(pairs) >= gt_pairs and 10 <= len(pairs) <= 25,
        "detection finds at least the injected set; a small excess is expected\n"
        "(random invoices can coincidentally satisfy the same rule)",
    )
    check("ground-truth pair list saved",
          len(ground_truth["defect_1_duplicate_invoice_pairs"]["pairs"]) == gt_pairs)

    # ---------------------------------------------------------------
    print("\n6. Defect 2 — orphan invoices (non-PO spend)")
    # ---------------------------------------------------------------
    n_orphan = int((invoices["po_id"] == "").sum())
    check(f"{n_orphan} invoices have a blank po_id, target 15-20", 15 <= n_orphan <= 20)

    # ---------------------------------------------------------------
    print("\n7. Defect 3 — invoiced vs PO variance beyond 5%")
    # ---------------------------------------------------------------
    inv_no_dup = invoices.drop_duplicates(subset=["invoice_id"])
    linked = inv_no_dup[inv_no_dup["po_id"] != ""].copy()
    linked["_amt"] = linked["amount"].astype(float)
    totals = linked.groupby("po_id")["_amt"].sum()

    po_amt = pos.drop_duplicates(subset=["po_id"]).set_index("po_id")["po_amount"].astype(float)
    joined = pd.DataFrame({"invoiced": totals}).join(po_amt.rename("po_amount"), how="inner")
    joined["pct_var"] = 100 * (joined["invoiced"] - joined["po_amount"]) / joined["po_amount"]
    exceptions = joined[joined["pct_var"].abs() > 5]

    d3 = ground_truth["defect_3_price_mismatch_pos"]
    gt_injected = d3["count"]
    gt_expected = d3["expected_query_5_5_exceptions"]
    check(
        f"{len(exceptions)} POs exceed 5% tolerance (expected {gt_expected})",
        len(exceptions) == gt_expected and len(exceptions) >= 10,
        f"{gt_injected} deliberately mis-priced + "
        f"{d3['attributable_to_duplicate_invoices']} inflated by a duplicate invoice\n"
        f"largest overage {joined['pct_var'].max():.1f}%, "
        f"largest shortfall {joined['pct_var'].min():.1f}%",
    )

    # ---------------------------------------------------------------
    print("\n8. Defect 4 — late payments populate the aging story")
    # ---------------------------------------------------------------
    due = inv_no_dup.set_index("invoice_id")["due_date"].map(parse_date)
    pay = payments.drop_duplicates(subset=["payment_id"]).copy()
    pay["_pdate"] = pay["payment_date"].map(parse_date)
    pay["_due"] = pay["invoice_id"].map(due)
    # These columns hold Python date objects, not datetime64, so the subtraction
    # yields an object-dtype Series of timedeltas — .dt is unavailable on it.
    pay["_late"] = [
        (p - d).days if p is not None and d is not None else None
        for p, d in zip(pay["_pdate"], pay["_due"])
    ]
    n_late = int((pay["_late"] > 0).sum())
    check(f"{n_late} payments settled after the due date",
          n_late >= 50,
          f"average {pay.loc[pay['_late'] > 0, '_late'].mean():.1f} days late, "
          f"max {int(pay['_late'].max())}")

    # unpaid aging spread
    paid_ids = set(pay["invoice_id"])
    unpaid = inv_no_dup[~inv_no_dup["invoice_id"].isin(paid_ids)].copy()
    unpaid["_due"] = unpaid["due_date"].map(parse_date)
    unpaid["_overdue"] = unpaid["_due"].map(lambda d: (AS_OF_DATE - d).days)

    def bucket(d: int) -> str:
        if d <= 0:
            return "Not Due"
        if d <= 30:
            return "0-30 Days"
        if d <= 60:
            return "31-60 Days"
        if d <= 90:
            return "61-90 Days"
        return "90+ Days"

    counts = unpaid["_overdue"].map(bucket).value_counts().to_dict()
    all_buckets = ["Not Due", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days"]
    nonzero = sum(1 for b in all_buckets if counts.get(b, 0) > 0)
    check(
        f"{nonzero}/5 aging buckets populated ({len(unpaid)} unpaid invoices)",
        nonzero >= 3,
        "  ".join(f"{b}={counts.get(b, 0)}" for b in all_buckets),
    )

    # ---------------------------------------------------------------
    print("\n9. Defect 5 — genuinely blank fields")
    # ---------------------------------------------------------------
    n_terms = int((vendors["payment_terms"].str.strip() == "").sum())
    n_region = int((vendors["region"].str.strip() == "").sum())
    check(f"{n_terms} vendors have blank payment_terms", n_terms >= 3)
    check(f"{n_region} vendors have blank region", n_region >= 3)

    # ---------------------------------------------------------------
    print("\n10. Defect 7 — text casing / whitespace noise")
    # ---------------------------------------------------------------
    cats = vendors["category"]
    raw_distinct = cats.nunique()
    norm_distinct = cats.map(norm).nunique()
    check(
        f"category has {raw_distinct} raw variants collapsing to {norm_distinct} canonical",
        raw_distinct > norm_distinct and norm_distinct == 5,
        "Silver has real standardization work to do",
    )

    # ---------------------------------------------------------------
    print("\n11. Defect 8 — exact whole-row duplicates")
    # ---------------------------------------------------------------
    inv_dupes = len(invoices) - len(invoices.drop_duplicates())
    pay_dupes = len(payments) - len(payments.drop_duplicates())
    gt8 = ground_truth["defect_8_exact_duplicate_rows"]["total"]
    check(
        f"{inv_dupes} duplicate invoice rows + {pay_dupes} duplicate payment rows = {inv_dupes + pay_dupes}",
        inv_dupes + pay_dupes == gt8,
        f"ground truth expects {gt8}",
    )
    check("duplicated invoice_ids are NOT extra invoices",
          len(set(invoices['invoice_id'])) == len(invoices) - inv_dupes,
          "they repeat an existing PK, so Silver must collapse them")

    # ---------------------------------------------------------------
    print("\n12. SCD Type 2 source data")
    # ---------------------------------------------------------------
    v2 = frames["vendors_update.csv"]
    v1_idx = vendors.set_index("vendor_id")
    v2_idx = v2.set_index("vendor_id")

    new_ids = set(v2_idx.index) - set(v1_idx.index)
    check(f"{len(new_ids)} vendors appear only in extract 2", len(new_ids) == 2,
          f"exercises the MERGE insert path: {sorted(new_ids)}")

    changed = []
    for vid in set(v1_idx.index) & set(v2_idx.index):
        a, b = v1_idx.loc[vid], v2_idx.loc[vid]
        if norm(a["category"]) != norm(b["category"]) or norm(a["payment_terms"]) != norm(b["payment_terms"]):
            changed.append(vid)
    check(f"{len(changed)} vendors changed category or payment_terms between extracts",
          3 <= len(changed) <= 8, f"vendors: {sorted(changed)}")

    # The cosmetic-noise trap: unchanged vendors must be byte-identical on the
    # attributes the MERGE compares, or SCD2 fires spurious versions.
    spurious = []
    for vid in set(v1_idx.index) & set(v2_idx.index):
        if vid in changed:
            continue
        a, b = v1_idx.loc[vid], v2_idx.loc[vid]
        if a["category"] != b["category"] or a["vendor_name"] != b["vendor_name"]:
            spurious.append(vid)
    check("unchanged vendors carry identical casing across both extracts",
          not spurious,
          "prevents the MERGE reading cosmetic noise as a real change"
          if not spurious else f"would fire spuriously for: {spurious[:5]}")

    changed_with_both = 0
    inv_dates = inv_no_dup.copy()
    inv_dates["_d"] = inv_dates["invoice_date"].map(parse_date)
    for chg in ground_truth["scd2_vendor_changes"]["changes"]:
        cd = dt.date.fromisoformat(chg["change_date"])
        sub = inv_dates[inv_dates["vendor_id"] == chg["vendor_id"]]
        if (sub["_d"] < cd).any() and (sub["_d"] >= cd).any():
            changed_with_both += 1
    check(
        f"{changed_with_both}/6 changed vendors have invoices both before and after the change",
        changed_with_both >= 1,
        "required for query 5.6 to return differing terms for one vendor",
    )

    # ---------------------------------------------------------------
    print("\n" + "=" * 78)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"RESULT: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 78)
    if failed:
        print("\nFAILURES:")
        for name, ok, _ in results:
            if not ok:
                print(f"  - {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
