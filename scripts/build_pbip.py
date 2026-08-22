"""
Generate the Power BI project (.pbip) as source files.

A .pbip is a Power BI report stored as text rather than as a binary .pbix: a TMDL
semantic model plus a JSON report definition. Authoring it here means the whole
dashboard is reproducible, reviewable in a diff, and version-controlled -- none of
which is true of a .pbix.

Run:
    .venv\\Scripts\\python.exe scripts\\build_pbip.py

Then open  powerbi/FinanceProcurement.pbip  in Power BI Desktop.

GUIDs are derived with uuid5 from stable names, so re-running produces identical
files instead of churning every lineage tag on every build.
"""

from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PBI_DIR = REPO_ROOT / "powerbi"
PROJECT = "FinanceProcurement"

MODEL_DIR = PBI_DIR / f"{PROJECT}.SemanticModel"
REPORT_DIR = PBI_DIR / f"{PROJECT}.Report"

# Snowflake server for the M queries. Account identifier from .env.
SNOWFLAKE_SERVER = "CEMNFKN-TT95210.snowflakecomputing.com"
SNOWFLAKE_WAREHOUSE = "FIN_PROC_WH"
SNOWFLAKE_DATABASE = "FIN_PROC_DB"
SNOWFLAKE_SCHEMA = "GOLD"

NS = uuid.UUID("6f1d2c3b-4a59-4e77-9b1a-0c8e5d7f2a41")


def guid(name: str) -> str:
    """Stable GUID from a name, so rebuilds do not churn every lineage tag."""
    return str(uuid.uuid5(NS, name))


# ===========================================================================
# TMDL — column definitions
# ===========================================================================
def column(name: str, dtype: str, table: str, *, fmt: str | None = None,
           summarize: str = "none", is_key: bool = False,
           sort_by: str | None = None, hidden: bool = False) -> str:
    """One TMDL column. TMDL is tab-indented and whitespace-significant."""
    lines = [f"\tcolumn {name}"]
    lines.append(f"\t\tdataType: {dtype}")
    if is_key:
        lines.append("\t\tisKey")
    if hidden:
        lines.append("\t\tisHidden")
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    lines.append(f"\t\tlineageTag: {guid(f'{table}.{name}')}")
    lines.append(f"\t\tsummarizeBy: {summarize}")
    lines.append(f"\t\tsourceColumn: {name}")
    if sort_by:
        lines.append(f"\t\tsortByColumn: {sort_by}")
    lines.append("")
    lines.append("\t\tannotation SummarizationSetBy = Automatic")
    lines.append("")
    return "\n".join(lines)


def calc_column(name: str, dtype: str, table: str, expression: str, *,
                sort_by: str | None = None, fmt: str | None = None,
                hidden: bool = False) -> str:
    """A DAX calculated column."""
    expr_lines = "\n".join(f"\t\t\t{ln}" for ln in expression.strip().splitlines())
    lines = [f"\tcolumn '{name}' =", expr_lines, ""]
    lines.append(f"\t\tdataType: {dtype}")
    if hidden:
        lines.append("\t\tisHidden")
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    lines.append(f"\t\tlineageTag: {guid(f'{table}.{name}')}")
    lines.append("\t\tsummarizeBy: none")
    if sort_by:
        lines.append(f"\t\tsortByColumn: '{sort_by}'")
    lines.append("")
    lines.append("\t\tannotation SummarizationSetBy = Automatic")
    lines.append("")
    return "\n".join(lines)


def measure(name: str, expression: str, table: str, *,
            fmt: str | None = None, description: str | None = None) -> str:
    expr_lines = "\n".join(f"\t\t\t{ln}" for ln in expression.strip().splitlines())
    lines: list[str] = []
    if description:
        # Prepend in order. Repeated insert(0, ...) in a loop would reverse a
        # multi-line description, which is easy to miss because each individual
        # line still looks right.
        lines.extend(f"\t/// {ln.strip()}" for ln in description.strip().splitlines())
    lines.extend([f"\tmeasure '{name}' =", expr_lines, ""])
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    lines.append(f"\t\tlineageTag: {guid(f'{table}.measure.{name}')}")
    lines.append("")
    return "\n".join(lines)


def m_partition(table: str, source_object: str, kind: str = "Table") -> str:
    """
    Import-mode M partition reading a Gold table or view over the Snowflake
    connector. Snowflake.Databases is the native connector, so credentials are
    managed by Power BI rather than embedded here.
    """
    m = f"""let
    Source = Snowflake.Databases("{SNOWFLAKE_SERVER}", "{SNOWFLAKE_WAREHOUSE}"),
    Db = Source{{[Name="{SNOWFLAKE_DATABASE}",Kind="Database"]}}[Data],
    Sch = Db{{[Name="{SNOWFLAKE_SCHEMA}",Kind="Schema"]}}[Data],
    Data = Sch{{[Name="{source_object}",Kind="{kind}"]}}[Data]
in
    Data"""
    body = "\n".join(f"\t\t\t\t{ln}" for ln in m.splitlines())
    return f"\tpartition {table} = m\n\t\tmode: import\n\t\tsource =\n{body}\n"


def table_tmdl(name: str, columns: list[str], partition: str, *,
               measures: list[str] | None = None,
               data_category: str | None = None,
               description: str | None = None) -> str:
    parts = []
    if description:
        for ln in description.strip().splitlines():
            parts.append(f"/// {ln.strip()}")
    parts.append(f"table {name}")
    parts.append(f"\tlineageTag: {guid(f'table.{name}')}")
    if data_category:
        parts.append(f"\tdataCategory: {data_category}")
    parts.append("")
    if measures:
        parts.extend(measures)
    parts.extend(columns)
    parts.append(partition)
    parts.append("\tannotation PBI_ResultType = Table")
    parts.append("")
    return "\n".join(parts)


# ===========================================================================
# THE MEASURES
# ===========================================================================
def build_measures() -> list[str]:
    T = "FACT_INVOICE"
    return [
        measure(
            "Total Invoiced", "SUM(FACT_INVOICE[AMOUNT_USD])", T,
            fmt='"$#,0.00;($#,0.00);$#,0.00"',
            description="Total invoiced value in USD across the selected context.",
        ),
        measure(
            "Total Outstanding",
            'CALCULATE(\n    SUM(FACT_INVOICE[AMOUNT_USD]),\n'
            '    FACT_INVOICE[STATUS] = "Unpaid"\n)',
            T, fmt='"$#,0.00;($#,0.00);$#,0.00"',
            description="Unpaid payables. The AP balance the business owes right now.",
        ),
        measure(
            "Total Overdue",
            'CALCULATE(\n    SUM(FACT_INVOICE[AMOUNT_USD]),\n'
            '    FACT_INVOICE[STATUS] = "Unpaid",\n'
            "    FACT_INVOICE[DUE_DATE] < TODAY()\n)",
            T, fmt='"$#,0.00;($#,0.00);$#,0.00"',
            description="Unpaid invoices already past their due date.",
        ),
        measure(
            "% Overdue",
            "DIVIDE(\n    [Total Overdue],\n    [Total Outstanding]\n)",
            T, fmt='"0.0%;-0.0%;0.0%"',
            description=(
                "DIVIDE rather than the / operator: when nothing is outstanding the "
                "denominator is zero, and / would raise an error that renders as "
                "Infinity on the card. DIVIDE returns BLANK instead."
            ),
        ),
        measure(
            "Avg DPO", "AVERAGE(FACT_PAYMENT[DAYS_TO_PAY])", T,
            fmt='"#,0.0"',
            description=(
                "Average days between invoice and payment. Reads the stored "
                "DAYS_TO_PAY column so DAX and SQL cannot drift apart."
            ),
        ),
        measure(
            "Total Paid", "SUM(FACT_PAYMENT[AMOUNT_PAID_USD])", T,
            fmt='"$#,0.00;($#,0.00);$#,0.00"',
        ),
        measure(
            "Invoice Count", "COUNTROWS(FACT_INVOICE)", T, fmt="#,0",
        ),
        measure(
            "Overdue Invoice Count",
            'CALCULATE(\n    COUNTROWS(FACT_INVOICE),\n'
            '    FACT_INVOICE[STATUS] = "Unpaid",\n'
            "    FACT_INVOICE[DUE_DATE] < TODAY()\n)",
            T, fmt="#,0",
        ),
        measure(
            "Total Paid LY",
            "CALCULATE(\n    [Total Paid],\n"
            "    SAMEPERIODLASTYEAR(DIM_DATE[FULL_DATE])\n)",
            T, fmt='"$#,0.00;($#,0.00);$#,0.00"',
            description=(
                "Same period last year. Requires DIM_DATE to be marked as a date "
                "table with a contiguous date column, which is why the dimension "
                "spans 2024-2027 rather than only the fact range."
            ),
        ),
        measure(
            "Paid MoM %",
            "VAR current_month = [Total Paid]\n"
            "VAR prior_month =\n"
            "    CALCULATE(\n        [Total Paid],\n"
            "        DATEADD(DIM_DATE[FULL_DATE], -1, MONTH)\n    )\n"
            "RETURN\n    DIVIDE(current_month - prior_month, prior_month)",
            T, fmt='"0.0%;-0.0%;0.0%"',
            description="Month-over-month change in payments, via CALCULATE + DATEADD.",
        ),
        measure(
            "Cumulative Spend %",
            "VAR this_vendor = [Total Invoiced]\n"
            "VAR all_spend =\n"
            "    CALCULATE([Total Invoiced], ALLSELECTED(DIM_VENDOR))\n"
            "VAR spend_at_or_above =\n"
            "    CALCULATE(\n"
            "        [Total Invoiced],\n"
            "        FILTER(\n"
            "            ALLSELECTED(DIM_VENDOR[VENDOR_NAME]),\n"
            "            [Total Invoiced] >= this_vendor\n"
            "        )\n"
            "    )\n"
            "RETURN\n"
            "    DIVIDE(spend_at_or_above, all_spend)",
            T, fmt='"0.0%;-0.0%;0.0%"',
            description=(
                "Running share of total spend for the Pareto line. FILTER over "
                "ALLSELECTED sums every vendor spending at least as much as this "
                "one, which is what makes the curve cumulative without needing a "
                "stored rank column."
            ),
        ),
        measure(
            "Duplicate Invoice Pairs", "COUNTROWS(VW_DUPLICATE_INVOICE_PAIRS)", T,
            fmt="#,0",
            description="Suspected duplicate invoice pairs flagged for AP review.",
        ),
        measure(
            "Duplicate Exposure",
            "SUM(VW_DUPLICATE_INVOICE_PAIRS[EXPOSURE_USD])", T,
            fmt='"$#,0.00;($#,0.00);$#,0.00"',
            description="Value at risk if every flagged duplicate were paid twice.",
        ),
        measure(
            "Match Exceptions",
            'CALCULATE(\n    COUNTROWS(VW_THREE_WAY_MATCH),\n'
            '    VW_THREE_WAY_MATCH[MATCH_STATUS] = "Exception - Review"\n)',
            T, fmt="#,0",
            description="POs where cumulative invoiced value breaches the 5% tolerance.",
        ),
    ]


# ===========================================================================
# TABLE DEFINITIONS
# ===========================================================================
def build_tables() -> dict[str, str]:
    tables: dict[str, str] = {}

    # ---- DIM_DATE ---------------------------------------------------------
    t = "DIM_DATE"
    cols = [
        column("DATE_KEY", "int64", t, fmt="0"),
        column("FULL_DATE", "dateTime", t, fmt="Long Date", is_key=True),
        column("DAY_OF_MONTH", "int64", t, fmt="0"),
        column("DAY_OF_WEEK", "int64", t, fmt="0"),
        column("DAY_NAME", "string", t),
        column("IS_WEEKEND", "boolean", t, fmt='"""TRUE"";""TRUE"";""FALSE"""'),
        column("MONTH_NUMBER", "int64", t, fmt="0"),
        column("MONTH_NAME", "string", t, sort_by="MONTH_NUMBER"),
        column("MONTH_YEAR", "string", t, sort_by="DATE_KEY"),
        column("QUARTER_NUMBER", "int64", t, fmt="0"),
        column("QUARTER_NAME", "string", t),
        column("YEAR_NUMBER", "int64", t, fmt="0"),
        column("FISCAL_YEAR", "int64", t, fmt="0"),
        column("FISCAL_QUARTER", "int64", t, fmt="0"),
        column("FISCAL_PERIOD", "int64", t, fmt="0"),
    ]
    tables[t] = table_tmdl(
        t, cols, m_partition(t, t), data_category="Time",
        description="Calendar dimension marked as a date table so DAX time intelligence works.",
    )

    # ---- DIM_VENDOR -------------------------------------------------------
    t = "DIM_VENDOR"
    cols = [
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_ID", "string", t),
        column("VENDOR_NAME", "string", t),
        column("CATEGORY", "string", t),
        column("REGION", "string", t),
        column("PAYMENT_TERMS", "string", t),
        column("PAYMENT_TERMS_DAYS", "int64", t, fmt="0"),
        column("CURRENCY", "string", t),
        column("EFFECTIVE_START_DATE", "dateTime", t, fmt="Short Date"),
        column("EFFECTIVE_END_DATE", "dateTime", t, fmt="Short Date"),
        column("IS_CURRENT", "boolean", t, fmt='"""TRUE"";""TRUE"";""FALSE"""'),
        column("VERSION_NUMBER", "int64", t, fmt="0"),
        column("SOURCE_CREATION_DATE", "dateTime", t, fmt="Short Date"),
        column("SOURCE_LAST_UPDATE", "dateTime", t, fmt="Short Date"),
        column("DATA_QUALITY_FLAGS", "string", t),
    ]
    tables[t] = table_tmdl(
        t, cols, m_partition(t, t),
        description=(
            "Vendor dimension, SCD Type 2. One row per vendor per version. "
            "Facts join on VENDOR_KEY, already resolved to the version in effect "
            "on each transaction date."
        ),
    )

    # ---- DIM_GL_ACCOUNT ---------------------------------------------------
    t = "DIM_GL_ACCOUNT"
    cols = [
        column("ACCOUNT_KEY", "int64", t, fmt="0", hidden=True),
        column("ACCOUNT_ID", "string", t),
        column("ACCOUNT_NAME", "string", t),
        column("COST_CENTER", "string", t),
        column("DEPARTMENT", "string", t),
    ]
    tables[t] = table_tmdl(t, cols, m_partition(t, t))

    # ---- FACT_INVOICE (carries the measures) ------------------------------
    t = "FACT_INVOICE"
    cols = [
        column("INVOICE_ID", "string", t),
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_ID", "string", t),
        column("ACCOUNT_KEY", "int64", t, fmt="0", hidden=True),
        column("GL_ACCOUNT_ID", "string", t),
        column("PO_ID", "string", t),
        column("INVOICE_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("INVOICE_DATE", "dateTime", t, fmt="Short Date"),
        column("DUE_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("DUE_DATE", "dateTime", t, fmt="Short Date"),
        column("AMOUNT", "decimal", t, fmt="#,0.00"),
        column("CURRENCY", "string", t),
        column("AMOUNT_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("STATUS", "string", t),
        column("IS_NON_PO_SPEND", "boolean", t, fmt='"""TRUE"";""TRUE"";""FALSE"""'),
        column("TERM_DAYS", "int64", t, fmt="0"),
        # Days overdue depends on TODAY(), so it is computed here at refresh
        # rather than stored in Gold where it would be stale within a day.
        calc_column(
            "Days Overdue", "int64", t,
            'IF(\n    FACT_INVOICE[STATUS] = "Unpaid",\n'
            "    DATEDIFF(FACT_INVOICE[DUE_DATE], TODAY(), DAY),\n"
            "    BLANK()\n)",
            fmt="0",
        ),
        calc_column(
            "Aging Sort", "int64", t,
            'VAR d = DATEDIFF(FACT_INVOICE[DUE_DATE], TODAY(), DAY)\n'
            "RETURN\n"
            'IF(\n    FACT_INVOICE[STATUS] <> "Unpaid",\n    BLANK(),\n'
            "    SWITCH(\n        TRUE(),\n"
            "        d <= 0, 1,\n        d <= 30, 2,\n        d <= 60, 3,\n"
            "        d <= 90, 4,\n        5\n    )\n)",
            fmt="0", hidden=True,
        ),
        calc_column(
            "Aging Bucket", "string", t,
            'VAR d = DATEDIFF(FACT_INVOICE[DUE_DATE], TODAY(), DAY)\n'
            "RETURN\n"
            'IF(\n    FACT_INVOICE[STATUS] <> "Unpaid",\n    BLANK(),\n'
            "    SWITCH(\n        TRUE(),\n"
            '        d <= 0, "Not Due",\n        d <= 30, "0-30 Days",\n'
            '        d <= 60, "31-60 Days",\n        d <= 90, "61-90 Days",\n'
            '        "90+ Days"\n    )\n)',
            sort_by="Aging Sort",
        ),
    ]
    tables[t] = table_tmdl(
        t, cols, m_partition(t, t), measures=build_measures(),
        description="Central AP fact. Hosts the model's measures.",
    )

    # ---- FACT_PAYMENT -----------------------------------------------------
    t = "FACT_PAYMENT"
    cols = [
        column("PAYMENT_ID", "string", t),
        column("INVOICE_ID", "string", t),
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_ID", "string", t),
        column("PAYMENT_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("PAYMENT_DATE", "dateTime", t, fmt="Short Date"),
        column("AMOUNT_PAID", "decimal", t, fmt="#,0.00"),
        column("CURRENCY", "string", t),
        column("AMOUNT_PAID_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("PAYMENT_METHOD", "string", t),
        column("DAYS_TO_PAY", "int64", t, fmt="0"),
        column("DAYS_LATE", "int64", t, fmt="0"),
        column("IS_LATE", "boolean", t, fmt='"""TRUE"";""TRUE"";""FALSE"""'),
    ]
    tables[t] = table_tmdl(t, cols, m_partition(t, t))

    # ---- FACT_PURCHASE_ORDER ----------------------------------------------
    t = "FACT_PURCHASE_ORDER"
    cols = [
        column("PO_ID", "string", t),
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_ID", "string", t),
        column("PO_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("PO_DATE", "dateTime", t, fmt="Short Date"),
        column("GOODS_RECEIPT_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("GOODS_RECEIPT_DATE", "dateTime", t, fmt="Short Date"),
        column("PO_AMOUNT", "decimal", t, fmt="#,0.00"),
        column("CURRENCY", "string", t),
        column("PO_AMOUNT_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("PO_STATUS", "string", t),
        column("IS_GOODS_RECEIVED", "boolean", t, fmt='"""TRUE"";""TRUE"";""FALSE"""'),
    ]
    tables[t] = table_tmdl(t, cols, m_partition(t, t))

    # ---- VW_DUPLICATE_INVOICE_PAIRS ---------------------------------------
    t = "VW_DUPLICATE_INVOICE_PAIRS"
    cols = [
        column("INVOICE_1", "string", t),
        column("INVOICE_2", "string", t),
        column("VENDOR_ID", "string", t),
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_NAME", "string", t),
        column("CATEGORY", "string", t),
        column("REGION", "string", t),
        column("AMOUNT", "decimal", t, fmt="#,0.00"),
        column("CURRENCY", "string", t),
        column("EXPOSURE_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("INVOICE_1_DATE", "dateTime", t, fmt="Short Date"),
        column("INVOICE_2_DATE", "dateTime", t, fmt="Short Date"),
        column("INVOICE_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("DAYS_APART", "int64", t, fmt="0"),
        column("INVOICE_1_STATUS", "string", t),
        column("INVOICE_2_STATUS", "string", t),
        column("RECOMMENDED_ACTION", "string", t),
    ]
    tables[t] = table_tmdl(
        t, cols, m_partition(t, t, kind="View"),
        description="Suspected duplicate invoice pairs. Mirrors query 5.4.",
    )

    # ---- VW_THREE_WAY_MATCH -----------------------------------------------
    t = "VW_THREE_WAY_MATCH"
    cols = [
        column("PO_ID", "string", t),
        column("VENDOR_KEY", "int64", t, fmt="0", hidden=True),
        column("VENDOR_ID", "string", t),
        column("VENDOR_NAME", "string", t),
        column("CATEGORY", "string", t),
        column("REGION", "string", t),
        column("PO_DATE", "dateTime", t, fmt="Short Date"),
        column("PO_DATE_KEY", "int64", t, fmt="0", hidden=True),
        column("PO_STATUS", "string", t),
        column("PO_AMOUNT", "decimal", t, fmt="#,0.00"),
        column("PO_AMOUNT_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("CURRENCY", "string", t),
        column("GOODS_RECEIPT_DATE", "dateTime", t, fmt="Short Date"),
        column("INVOICE_COUNT", "int64", t, fmt="0"),
        column("TOTAL_INVOICED", "decimal", t, fmt="#,0.00"),
        column("TOTAL_INVOICED_USD", "decimal", t, fmt='"$#,0.00;($#,0.00);$#,0.00"', summarize="sum"),
        column("INVOICE_IDS", "string", t),
        column("VARIANCE_AMOUNT", "decimal", t, fmt="#,0.00"),
        column("PCT_VARIANCE", "decimal", t, fmt="#,0.00"),
        column("RECEIPT_STATUS", "string", t),
        column("MATCH_STATUS", "string", t),
        column("EXCEPTION_TYPE", "string", t),
    ]
    tables[t] = table_tmdl(
        t, cols, m_partition(t, t, kind="View"),
        description="Cumulative invoiced-vs-PO variance per PO. Mirrors query 5.5.",
    )

    return tables


# ===========================================================================
# RELATIONSHIPS
# ===========================================================================
def build_relationships() -> str:
    """
    Every relationship is many-to-one, single-direction -- the star schema default.

    FACT_PAYMENT is deliberately NOT related to FACT_INVOICE. Doing so would create
    two filter paths from DIM_VENDOR to payments (direct, and via invoices), which
    Power BI treats as ambiguous. That is exactly why FACT_PAYMENT carries its own
    conformed VENDOR_KEY and date key.

    DIM_DATE -> FACT_INVOICE appears twice: invoice date is active, due date is
    inactive. Only one relationship per table pair may be active; the due-date one
    is activated on demand with USERELATIONSHIP.
    """
    rels = [
        ("FACT_INVOICE.VENDOR_KEY", "DIM_VENDOR.VENDOR_KEY", True),
        ("FACT_INVOICE.ACCOUNT_KEY", "DIM_GL_ACCOUNT.ACCOUNT_KEY", True),
        ("FACT_INVOICE.INVOICE_DATE_KEY", "DIM_DATE.DATE_KEY", True),
        ("FACT_INVOICE.DUE_DATE_KEY", "DIM_DATE.DATE_KEY", False),
        ("FACT_PAYMENT.VENDOR_KEY", "DIM_VENDOR.VENDOR_KEY", True),
        ("FACT_PAYMENT.PAYMENT_DATE_KEY", "DIM_DATE.DATE_KEY", True),
        ("FACT_PURCHASE_ORDER.VENDOR_KEY", "DIM_VENDOR.VENDOR_KEY", True),
        ("FACT_PURCHASE_ORDER.PO_DATE_KEY", "DIM_DATE.DATE_KEY", True),
        ("VW_DUPLICATE_INVOICE_PAIRS.VENDOR_KEY", "DIM_VENDOR.VENDOR_KEY", True),
        ("VW_THREE_WAY_MATCH.VENDOR_KEY", "DIM_VENDOR.VENDOR_KEY", True),
    ]
    out = []
    for frm, to, active in rels:
        rid = guid(f"rel.{frm}->{to}")
        block = [f"relationship {rid}"]
        if not active:
            block.append("\tisActive: false")
        block.append(f"\tfromColumn: {frm}")
        block.append(f"\ttoColumn: {to}")
        out.append("\n".join(block))
    return "\n\n".join(out) + "\n"


# ===========================================================================
# PROJECT SCAFFOLDING
# ===========================================================================
def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Two hard requirements from the TMDL/PBIR parser, both learned the hard way:
    #
    #  1. UTF-8 with NO BOM. Desktop rejects a BOM outright:
    #     "Only text with UTF8 encoding without BOM is supported."
    #
    #  2. Exactly CRLF line endings, written as bytes.
    #     Path.write_text() opens in text mode, where Python translates "\n" to
    #     os.linesep. On Windows that turns already-normalized CRLF content into
    #     CR CR LF, which the parser reports as "Unexpected line type: Empty!"
    #     on line 2 of the first file it reads. Writing bytes bypasses the
    #     translation entirely.
    normalized = content.replace("\r\n", "\n").replace("\n", "\r\n")
    path.write_bytes(normalized.encode("utf-8"))


def build() -> None:
    if MODEL_DIR.exists():
        shutil.rmtree(MODEL_DIR)
    if REPORT_DIR.exists():
        shutil.rmtree(REPORT_DIR)

    tables = build_tables()

    # ---- .pbip ------------------------------------------------------------
    write(PBI_DIR / f"{PROJECT}.pbip", json.dumps({
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{PROJECT}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    }, indent=2))

    # ---- semantic model ---------------------------------------------------
    write(MODEL_DIR / ".platform", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": guid("model.logical")},
    }, indent=2))

    write(MODEL_DIR / "definition.pbism", json.dumps({
        "version": "4.2", "settings": {},
    }, indent=2))

    write(MODEL_DIR / "definition" / "database.tmdl",
          f"database {PROJECT}\n\tcompatibilityLevel: 1567\n")

    order = json.dumps(list(tables.keys()))
    model_lines = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        f"annotation PBI_QueryOrder = {order}",
        "",
        "annotation PBI_ProTooling = [\"DevMode\"]",
        "",
    ]
    for name in tables:
        model_lines.append(f"ref table {name}")
    model_lines.append("")
    write(MODEL_DIR / "definition" / "model.tmdl", "\n".join(model_lines))

    write(MODEL_DIR / "definition" / "relationships.tmdl", build_relationships())

    for name, content in tables.items():
        write(MODEL_DIR / "definition" / "tables" / f"{name}.tmdl", content)

    # ---- report shell (visuals added by build_pbip_report.py) -------------
    write(REPORT_DIR / ".platform", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": PROJECT},
        "config": {"version": "2.0", "logicalId": guid("report.logical")},
    }, indent=2))

    # version 4.0 selects the PBIR (enhanced) report format -- a definition/
    # folder of per-page and per-visual JSON. Version 1.0 selects the legacy
    # single report.json, which current Desktop opens as a blank report.
    write(REPORT_DIR / "definition.pbir", json.dumps({
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{PROJECT}.SemanticModel"}},
    }, indent=2))

    import build_pbip_report
    build_pbip_report.write_report(REPORT_DIR, guid)

    print(f"Semantic model : {len(tables)} tables")
    for name in tables:
        n_measures = tables[name].count("\tmeasure ")
        extra = f", {n_measures} measures" if n_measures else ""
        print(f"    {name}{extra}")
    print(f"Relationships  : {build_relationships().count('relationship ')}")
    print(f"\nProject written to {PBI_DIR / (PROJECT + '.pbip')}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    build()
