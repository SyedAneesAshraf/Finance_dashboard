"""
Snowflake runner for the Finance & Procurement analytics project.

A thin, dependency-light wrapper so every phase's SQL can be executed, verified, and
captured reproducibly instead of being pasted into Snowsight by hand.

Usage
-----
    python scripts/sf.py test
        Connect and print account context. Use this to confirm .env is right.

    python scripts/sf.py run sql/00_setup/01_create_database_and_schemas.sql
        Execute every statement in a file, in order, stopping at the first error
        and reporting exactly which statement failed.

    python scripts/sf.py query "SELECT COUNT(*) FROM BRONZE.INVOICES"
        Run one ad-hoc statement.

    python scripts/sf.py put data_raw/*.csv @BRONZE.RAW_FILES
        Upload local files to an internal stage (auto-compresses to .gz).

Options
-------
    --quiet     Suppress per-statement result tables; print only failures + a summary.
    --max-rows  Cap rows printed per result set (default 50).

Credentials come from a git-ignored .env in the repo root. See .env.example.
"""

from __future__ import annotations

import argparse
import glob
import io
import os
import pathlib
import sys

# ---------------------------------------------------------------------------
# Make the repo root importable/locatable regardless of where this is invoked from
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    import snowflake.connector
    from snowflake.connector.util_text import split_statements
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"Missing dependency: {exc}\n"
        "Install with:  .venv\\Scripts\\python.exe -m pip install "
        "snowflake-connector-python python-dotenv"
    )


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
REQUIRED_VARS = ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER")


def connect():
    """Open a Snowflake connection from .env, failing loudly on missing config."""
    load_dotenv(REPO_ROOT / ".env")

    missing = [v for v in REQUIRED_VARS if not os.getenv(v)]
    if missing:
        sys.exit(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f"\nCreate {REPO_ROOT / '.env'} using .env.example as a template."
        )

    password = os.getenv("SNOWFLAKE_PASSWORD")
    authenticator = os.getenv("SNOWFLAKE_AUTHENTICATOR")
    if not password and not authenticator:
        sys.exit(
            "Set either SNOWFLAKE_PASSWORD or SNOWFLAKE_AUTHENTICATOR "
            "(e.g. externalbrowser) in .env."
        )

    kwargs = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", "FIN_PROC_WH"),
        "database": os.getenv("SNOWFLAKE_DATABASE", "FIN_PROC_DB"),
        "role": os.getenv("SNOWFLAKE_ROLE", "ACCOUNTADMIN"),
        # Long-running DDL is not expected here; fail fast rather than hang a phase.
        "login_timeout": 30,
        "client_session_keep_alive": True,
    }
    schema = os.getenv("SNOWFLAKE_SCHEMA")
    if schema:
        kwargs["schema"] = schema
    if password:
        kwargs["password"] = password
    if authenticator:
        kwargs["authenticator"] = authenticator

    return snowflake.connector.connect(**kwargs)


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------
def fmt_table(columns, rows, max_rows: int = 50) -> str:
    """Render a result set as a fixed-width table."""
    if not columns:
        return ""
    shown = rows[:max_rows]
    header = [str(c) for c in columns]

    def cell(v):
        return "NULL" if v is None else str(v)

    widths = [len(h) for h in header]
    for r in shown:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(cell(v)))
    widths = [min(w, 60) for w in widths]

    def line(vals):
        return " | ".join(cell(v)[:60].ljust(widths[i]) for i, v in enumerate(vals))

    out = [line(header), "-+-".join("-" * w for w in widths)]
    out.extend(line(r) for r in shown)
    if len(rows) > max_rows:
        out.append(f"... {len(rows) - max_rows} more row(s) not shown")
    out.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(out)


def label_of(statement: str, width: int = 88) -> str:
    """A one-line summary of a statement, for progress output."""
    lines = [
        ln.strip()
        for ln in statement.splitlines()
        if ln.strip() and not ln.strip().startswith(("--", "/*", "*"))
    ]
    flat = " ".join(lines)
    return flat[:width] + ("..." if len(flat) > width else "")


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------
def execute_sql(conn, sql: str, quiet: bool, max_rows: int, source: str = "<inline>") -> int:
    """
    Execute statements one at a time so a failure names the exact statement.

    Returns the number of statements executed successfully.
    """
    statements = [
        (s, is_put_get)
        for s, is_put_get in split_statements(io.StringIO(sql), remove_comments=False)
        if s and s.strip()
    ]

    cur = conn.cursor()
    executed = 0
    for idx, (stmt, _is_put_get) in enumerate(statements, 1):
        tag = f"[{idx}/{len(statements)}]"
        try:
            cur.execute(stmt)
        except Exception as exc:
            print(f"\n{tag} FAILED in {source}")
            print("-" * 78)
            print(stmt.strip()[:1500])
            print("-" * 78)
            print(f"ERROR: {exc}")
            cur.close()
            raise SystemExit(1)

        executed += 1
        if quiet:
            continue

        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            print(f"\n{tag} {label_of(stmt)}")
            print(fmt_table(cols, rows, max_rows))
        else:
            print(f"{tag} OK   {label_of(stmt)}")

    cur.close()
    return executed


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_test(args) -> None:
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), "
            "CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA(), CURRENT_VERSION()"
        )
        row = cur.fetchone()
        labels = [
            "account", "user", "role", "warehouse", "database", "schema", "version",
        ]
        print("Connected to Snowflake\n")
        for k, v in zip(labels, row):
            print(f"  {k:<10} {v if v is not None else 'NULL'}")
        cur.close()
    finally:
        conn.close()


def cmd_run(args) -> None:
    paths: list[pathlib.Path] = []
    for pattern in args.paths:
        matched = sorted(glob.glob(pattern))
        if not matched:
            sys.exit(f"No file matched: {pattern}")
        paths.extend(pathlib.Path(m) for m in matched)

    conn = connect()
    try:
        total = 0
        for path in paths:
            print(f"\n{'=' * 78}\n>>> {path}\n{'=' * 78}")
            sql = path.read_text(encoding="utf-8")
            total += execute_sql(conn, sql, args.quiet, args.max_rows, source=str(path))
        print(f"\n{'=' * 78}")
        print(f"DONE — {total} statement(s) executed across {len(paths)} file(s).")
    finally:
        conn.close()


def cmd_query(args) -> None:
    conn = connect()
    try:
        execute_sql(conn, args.sql, quiet=False, max_rows=args.max_rows, source="<cli>")
    finally:
        conn.close()


def cmd_put(args) -> None:
    files = sorted(glob.glob(args.pattern))
    if not files:
        sys.exit(f"No file matched: {args.pattern}")

    conn = connect()
    try:
        cur = conn.cursor()
        for f in files:
            # Snowflake's file:// URI needs forward slashes even on Windows.
            uri = pathlib.Path(f).resolve().as_posix()
            stmt = f"PUT 'file://{uri}' {args.stage} OVERWRITE = TRUE AUTO_COMPRESS = TRUE"
            cur.execute(stmt)
            cols = [d[0] for d in cur.description]
            print(fmt_table(cols, cur.fetchall(), args.max_rows))
        cur.close()
        print(f"\nUploaded {len(files)} file(s) to {args.stage}.")
    finally:
        conn.close()


def main() -> None:
    p = argparse.ArgumentParser(
        prog="sf.py", description="Snowflake runner for the Finance & Procurement project."
    )
    p.add_argument("--quiet", action="store_true", help="suppress per-statement output")
    p.add_argument("--max-rows", type=int, default=50, help="max rows printed per result set")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("test", help="connect and print session context")

    pr = sub.add_parser("run", help="execute one or more .sql files")
    pr.add_argument("paths", nargs="+")

    pq = sub.add_parser("query", help="execute an ad-hoc statement")
    pq.add_argument("sql")

    pp = sub.add_parser("put", help="upload local files to an internal stage")
    pp.add_argument("pattern")
    pp.add_argument("stage")

    args = p.parse_args()
    {"test": cmd_test, "run": cmd_run, "query": cmd_query, "put": cmd_put}[args.command](args)


if __name__ == "__main__":
    main()
