"""
Report definition for the Power BI project, in PBIR (enhanced report) format.

Imported by build_pbip.py.

FORMAT NOTE
-----------
Power BI has two on-disk report formats. The legacy one is a single report.json
at the Report root, referenced by "version": "1.0" in definition.pbir. Current
Power BI Desktop expects PBIR instead: a definition/ folder holding one JSON file
per page and per visual, referenced by "version": "4.0".

The legacy format was tried first here and produced a silent failure -- Desktop
opened a blank report rather than reporting anything -- which is why this is
written as PBIR.

Each visual carries a query.queryState mapping a visual ROLE (Values, Category,
Y, Rows...) to projections. Every projection needs three things that must agree:

    field          the semantic expression (Column or Measure)
    queryRef       "Table.Field" -- how the visual refers to it internally
    nativeQueryRef the display name shown in the field well

The helpers below derive all three from one spec so they cannot drift apart.
"""

from __future__ import annotations

import json
from pathlib import Path

PAGE_W, PAGE_H = 1280, 720

SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"


# ===========================================================================
# FIELD / PROJECTION HELPERS
# ===========================================================================
def field(table: str, prop: str, kind: str) -> dict:
    node = {"Expression": {"SourceRef": {"Entity": table}}, "Property": prop}
    return {("Measure" if kind == "measure" else "Column"): node}


def projection(table: str, prop: str, kind: str) -> dict:
    return {
        "field": field(table, prop, kind),
        "queryRef": f"{table}.{prop}",
        "nativeQueryRef": prop,
    }


def title_object(text: str) -> dict:
    return {
        "title": [{
            "properties": {
                "show": {"expr": {"Literal": {"Value": "true"}}},
                "text": {"expr": {"Literal": {"Value": f"'{text}'"}}},
                "fontSize": {"expr": {"Literal": {"Value": "11D"}}},
                "bold": {"expr": {"Literal": {"Value": "true"}}},
            }
        }]
    }


def visual(name: str, vtype: str, x: int, y: int, w: int, h: int,
           roles: dict[str, list[tuple[str, str, str]]], *,
           title: str | None = None,
           sort: tuple[str, str, str, str] | None = None) -> dict:
    """roles: {visual role -> [(table, property, kind), ...]}"""
    query_state = {
        role: {"projections": [projection(*f) for f in fields]}
        for role, fields in roles.items()
    }

    query: dict = {"queryState": query_state}
    if sort:
        s_table, s_prop, s_kind, s_dir = sort
        query["sortDefinition"] = {
            "sort": [{"field": field(s_table, s_prop, s_kind), "direction": s_dir}],
            "isDefaultSort": True,
        }

    v: dict = {
        "$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0},
        "visual": {
            "visualType": vtype,
            "query": query,
            "drillFilterOtherVisuals": True,
        },
    }
    if title:
        v["visual"]["visualContainerObjects"] = title_object(title)
    return v


def textbox(name: str, x: int, y: int, w: int, h: int,
            text: str, size: int = 20, bold: bool = True) -> dict:
    return {
        "$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json",
        "name": name,
        "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0},
        "visual": {
            "visualType": "textbox",
            "objects": {
                "general": [{
                    "properties": {
                        "paragraphs": [{
                            "textRuns": [{
                                "value": text,
                                "textStyle": {
                                    "fontSize": f"{size}pt",
                                    "fontWeight": "bold" if bold else "normal",
                                },
                            }]
                        }]
                    }
                }]
            },
            "drillFilterOtherVisuals": True,
        },
    }


# ===========================================================================
# PAGE 1 — AP AGING & CASH FLOW
# ===========================================================================
def page_one() -> list[dict]:
    FI, DV, DD = "FACT_INVOICE", "DIM_VENDOR", "DIM_DATE"
    return [
        textbox("p1Header", 16, 12, 700, 40, "AP Aging & Cash Flow Overview"),
        textbox("p1Sub", 16, 52, 700, 24,
                "Meridian Manufacturing Group  |  Accounts Payable", size=10, bold=False),

        # KPI cards
        visual("p1CardOutstanding", "card", 16, 88, 226, 108,
               {"Values": [(FI, "Total Outstanding", "measure")]},
               title="Total Outstanding Payables"),
        visual("p1CardOverdue", "card", 254, 88, 226, 108,
               {"Values": [(FI, "Total Overdue", "measure")]},
               title="Total Overdue"),
        visual("p1CardPctOverdue", "card", 492, 88, 210, 108,
               {"Values": [(FI, "% Overdue", "measure")]},
               title="% Overdue"),
        visual("p1CardDpo", "card", 714, 88, 210, 108,
               {"Values": [(FI, "Avg DPO", "measure")]},
               title="Average DPO (days)"),

        # Slicers
        visual("p1SlicerDate", "slicer", 940, 88, 324, 108,
               {"Values": [(DD, "FULL_DATE", "column")]},
               title="Date range"),
        visual("p1SlicerCategory", "slicer", 940, 208, 324, 148,
               {"Values": [(DV, "CATEGORY", "column")]},
               title="Vendor category"),
        visual("p1SlicerRegion", "slicer", 940, 368, 324, 148,
               {"Values": [(DV, "REGION", "column")]},
               title="Region"),

        # Aging bar
        visual("p1Aging", "clusteredColumnChart", 16, 208, 448, 308,
               {"Category": [(FI, "Aging Bucket", "column")],
                "Y": [(FI, "Total Outstanding", "measure")]},
               title="Outstanding by aging bucket"),

        # Monthly trend
        visual("p1Trend", "lineChart", 476, 208, 448, 308,
               {"Category": [(DD, "MONTH_YEAR", "column")],
                "Y": [(FI, "Total Invoiced", "measure"), (FI, "Total Paid", "measure")]},
               title="Monthly invoiced vs paid"),

        # Overdue detail table
        visual("p1Table", "tableEx", 16, 528, 908, 176,
               {"Values": [(FI, "INVOICE_ID", "column"),
                           (DV, "VENDOR_NAME", "column"),
                           (FI, "DUE_DATE", "column"),
                           (FI, "Days Overdue", "column"),
                           (FI, "Aging Bucket", "column"),
                           (FI, "Total Outstanding", "measure")]},
               title="Overdue invoices",
               sort=(FI, "Days Overdue", "column", "Descending")),

        visual("p1CardOverdueCount", "card", 940, 528, 324, 176,
               {"Values": [(FI, "Overdue Invoice Count", "measure")]},
               title="Overdue invoice count"),
    ]


# ===========================================================================
# PAGE 2 — VENDOR SPEND & RISK
# ===========================================================================
def page_two() -> list[dict]:
    FI, DV = "FACT_INVOICE", "DIM_VENDOR"
    DUP, TWM = "VW_DUPLICATE_INVOICE_PAIRS", "VW_THREE_WAY_MATCH"
    return [
        textbox("p2Header", 16, 12, 700, 40, "Vendor Spend & Risk Analysis"),
        textbox("p2Sub", 16, 52, 700, 24,
                "Concentration, duplicate invoices, and 3-way match exceptions",
                size=10, bold=False),

        # Risk cards
        visual("p2CardDupes", "card", 940, 88, 158, 108,
               {"Values": [(FI, "Duplicate Invoice Pairs", "measure")]},
               title="Duplicate pairs"),
        visual("p2CardExposure", "card", 1106, 88, 158, 108,
               {"Values": [(FI, "Duplicate Exposure", "measure")]},
               title="Exposure"),
        visual("p2CardExceptions", "card", 940, 208, 324, 108,
               {"Values": [(FI, "Match Exceptions", "measure")]},
               title="3-way match exceptions"),

        # Slicers
        visual("p2SlicerCategory", "slicer", 940, 328, 324, 188,
               {"Values": [(DV, "CATEGORY", "column")]},
               title="Vendor category"),
        visual("p2SlicerRegion", "slicer", 940, 528, 324, 176,
               {"Values": [(DV, "REGION", "column")]},
               title="Region"),

        # Pareto: bars = spend, line = cumulative %
        visual("p2Pareto", "lineClusteredColumnComboChart", 16, 88, 604, 340,
               {"Category": [(DV, "VENDOR_NAME", "column")],
                "Y": [(FI, "Total Invoiced", "measure")],
                "Y2": [(FI, "Cumulative Spend %", "measure")]},
               title="Vendor spend concentration (Pareto)",
               sort=(FI, "Total Invoiced", "measure", "Descending")),

        # Donut
        visual("p2Donut", "donutChart", 632, 88, 292, 340,
               {"Category": [(DV, "CATEGORY", "column")],
                "Y": [(FI, "Total Invoiced", "measure")]},
               title="Spend by category"),

        # 3-way match matrix
        visual("p2Matrix", "pivotTable", 16, 438, 604, 266,
               {"Rows": [(TWM, "PO_ID", "column"), (TWM, "VENDOR_NAME", "column")],
                "Columns": [(TWM, "EXCEPTION_TYPE", "column")],
                "Values": [(TWM, "PCT_VARIANCE", "column")]},
               title="3-way match exceptions by PO"),

        # Duplicate detail
        visual("p2DupTable", "tableEx", 632, 438, 292, 266,
               {"Values": [(DUP, "INVOICE_1", "column"),
                           (DUP, "INVOICE_2", "column"),
                           (DUP, "VENDOR_NAME", "column"),
                           (DUP, "EXPOSURE_USD", "column")]},
               title="Flagged duplicate invoices"),
    ]


PAGES = [
    ("page1", "AP Aging & Cash Flow", page_one),
    # page_two() (Vendor Spend & Risk -- Pareto, category donut, 3-way match
    # matrix, duplicate table) is deliberately not wired into PAGES yet.
    # Scope decision: ship Page 1 now, build Page 2 in a follow-up pass.
    # The function is left complete and ready to re-enable below when that
    # happens: ("page2", "Vendor Spend & Risk", page_two),
]


# ===========================================================================
# WRITE
# ===========================================================================
def write_report(report_dir: Path, guid_fn) -> None:
    def dump(path: Path, obj: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # write_bytes, not write_text: text mode would translate "\n" to
        # os.linesep and turn this CRLF content into CR CR LF. No BOM.
        text = json.dumps(obj, indent=2).replace("\n", "\r\n")
        path.write_bytes(text.encode("utf-8"))

    definition = report_dir / "definition"

    # PBIR requires this file. Without it Desktop reports "version.json not
    # found" and falls back to a blank report -- it is not implied by the
    # "version": "4.0" in definition.pbir.
    dump(definition / "version.json", {
        "$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json",
        "version": "4.0",
    })

    # No themeCollection / resourcePackages. Declaring a base theme requires the
    # matching StaticResources/SharedResources/BaseThemes/<name>.json to exist in
    # the project; referencing one that is absent makes Desktop reject the whole
    # report definition and load the model with no pages at all. Omitting it lets
    # Desktop apply its built-in default.
    dump(definition / "report.json", {
        "$schema": f"{SCHEMA}/report/1.0.0/schema.json",
        "layoutOptimization": "None",
        "settings": {
            "useStylableVisualContainerHeader": True,
            "defaultDrillFilterOtherVisuals": True,
        },
    })

    dump(definition / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p[0] for p in PAGES],
        "activePageName": PAGES[0][0],
    })

    total_visuals = 0
    for page_name, display, builder in PAGES:
        page_dir = definition / "pages" / page_name
        dump(page_dir / "page.json", {
            "$schema": f"{SCHEMA}/page/1.0.0/schema.json",
            "name": page_name,
            "displayName": display,
            "displayOption": "FitToPage",
            "height": PAGE_H,
            "width": PAGE_W,
        })
        for v in builder():
            dump(page_dir / "visuals" / v["name"] / "visual.json", v)
            total_visuals += 1

    print(f"Report         : {len(PAGES)} pages, {total_visuals} visuals (PBIR format)")
