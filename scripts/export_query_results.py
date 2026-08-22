"""
Run the six Phase 5 analytical queries and export each result set to CSV.

The exports serve three purposes: evidence in the README, the source for the
Excel companion in Phase 7, and a fallback data source for Power BI if a direct
Snowflake connection proves awkward.

Each .sql file contains USE statements followed by exactly one analytical query,
so the LAST result set produced by the file is the one worth keeping.

Run:
    .venv\\Scripts\\python.exe scripts\\export_query_results.py
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import sf
from snowflake.connector.util_text import split_statements

REPO_ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = REPO_ROOT / "sql" / "04_analytics"
OUT_DIR = REPO_ROOT / "exports"

QUERIES = [
    ("51_dpo_by_vendor.sql", "q51_dpo_by_vendor.csv"),
    ("52_ap_aging_buckets.sql", "q52_ap_aging_buckets.csv"),
    ("53_vendor_spend_concentration.sql", "q53_vendor_spend_concentration.csv"),
    ("54_duplicate_invoice_detection.sql", "q54_duplicate_invoices.csv"),
    ("55_three_way_match_variance.sql", "q55_three_way_match.csv"),
    ("56_vendor_history_scd2.sql", "q56_vendor_history_scd2.csv"),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sf.connect()
    try:
        cur = conn.cursor()
        print(f"{'query':<40} {'rows':>6}  {'cols':>4}  output")
        print("-" * 92)

        for sql_name, out_name in QUERIES:
            sql = (SQL_DIR / sql_name).read_text(encoding="utf-8")
            statements = [
                s for s, _ in split_statements(io.StringIO(sql), remove_comments=False)
                if s and s.strip()
            ]

            columns, rows = None, None
            for stmt in statements:
                cur.execute(stmt)
                # USE statements return a single 'status' column; a real result
                # set is anything wider, so keep the last one of those.
                if cur.description and len(cur.description) > 1:
                    columns = [d[0] for d in cur.description]
                    rows = cur.fetchall()

            if columns is None:
                print(f"{sql_name:<40} {'--':>6}        no result set found")
                continue

            out_path = OUT_DIR / out_name
            with out_path.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(columns)
                w.writerows(rows)

            print(f"{sql_name:<40} {len(rows):>6}  {len(columns):>4}  exports/{out_name}")

        cur.close()
    finally:
        conn.close()

    print("\nAll six analytical query results exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
