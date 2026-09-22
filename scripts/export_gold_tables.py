"""
Export the Gold star schema to CSV.

Two consumers:
  - Phase 7's Excel companion, which must pull actuals from a Gold export rather
    than have numbers typed in by hand
  - Power BI, as a fallback source if the direct Snowflake connection proves
    awkward on the day

Run:
    .venv\\Scripts\\python.exe scripts\\export_gold_tables.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import sf

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "exports" / "gold"

TABLES = [
    "DIM_DATE",
    "FACT_INVOICE",
    "FACT_PAYMENT",
    "FACT_PURCHASE_ORDER",
]

# Deterministic ordering so re-running produces byte-identical files and git
# diffs stay meaningful.
ORDER_BY = {
    "DIM_DATE": "DATE_KEY",
    "DIM_VENDOR": "VENDOR_KEY",
    "DIM_GL_ACCOUNT": "ACCOUNT_KEY",
    "FACT_INVOICE": "INVOICE_ID",
    "FACT_PAYMENT": "PAYMENT_ID",
    "FACT_PURCHASE_ORDER": "PO_ID",
}


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sf.connect()
    try:
        cur = conn.cursor()
        print(f"{'table':<24} {'rows':>7}  {'cols':>4}  output")
        print("-" * 72)
        for t in TABLES:
            # The Gold load-timestamp column changes on every reload and would
            # make every export differ; excluding it keeps diffs meaningful.
            cur.execute(f"""
                SELECT COLUMN_NAME FROM FIN_PROC_DB.INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = 'GOLD' AND TABLE_NAME = '{t}'
                  AND LEFT(COLUMN_NAME, 1) != '_'
                ORDER BY ORDINAL_POSITION
            """)
            cols = [r[0] for r in cur.fetchall()]

            cur.execute(
                f"SELECT {', '.join(cols)} FROM GOLD.{t} ORDER BY {ORDER_BY[t]}"
            )
            rows = cur.fetchall()

            out = OUT_DIR / f"{t.lower()}.csv"
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(cols)
                w.writerows(rows)
            print(f"{t:<24} {len(rows):>7}  {len(cols):>4}  exports/gold/{out.name}")

        cur.close()
    finally:
        conn.close()
    print("\nGold star schema exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
