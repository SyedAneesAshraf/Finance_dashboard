"""
Phase 1 — synthetic Oracle EBS-style Finance & Procurement extract generator.

Produces the raw source files for the Bronze layer, complete with the data-quality
defects that Phases 3 and 5 exist to clean and detect, plus a ground-truth log of
exactly how many of each defect was injected.

Run:
    .venv\\Scripts\\python.exe scripts\\generate_data.py

Outputs (data_raw/):
    gl_accounts.csv         20 rows
    vendors.csv             50 rows   vendor master, extract #1  (2024-09-01)
    vendors_update.csv      52 rows   vendor master, extract #2  (2026-08-15)
    purchase_orders.csv    ~480 rows
    invoices.csv           ~690 rows
    payments.csv           ~540 rows

Outputs (docs/):
    ground_truth.json               machine-readable defect counts
    04_phase1_ground_truth.md       human-readable summary

Design rationale lives in docs/03_phase1_data_design.md. Constants that bind other
phases live in docs/00_design_decisions.md.

Determinism: everything derives from SEED. Re-running reproduces byte-identical files,
which matters because the ground-truth log must keep describing the data through Phase 5.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import random
import zlib
from dataclasses import dataclass
from pathlib import Path

from faker import Faker

# ===========================================================================
# CONSTANTS  (see docs/00_design_decisions.md)
# ===========================================================================
SEED = 20260815

AS_OF_DATE = dt.date(2026, 8, 15)      # "today" for aging-bucket purposes
WINDOW_START = dt.date(2024, 9, 1)     # earliest transaction
WINDOW_END = AS_OF_DATE                # latest transaction

VENDOR_EXTRACT_1_DATE = dt.date(2024, 9, 1)
VENDOR_EXTRACT_2_DATE = AS_OF_DATE

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data_raw"
DOCS_DIR = REPO_ROOT / "docs"

# --- volumes ---------------------------------------------------------------
N_VENDORS = 50
N_NEW_VENDORS = 2            # onboarded between the two vendor extracts
N_GL_ACCOUNTS = 20
N_PURCHASE_ORDERS = 480
N_POS_WITHOUT_INVOICE = 60   # open commitments, deliberately unbilled

# --- injected defect targets ----------------------------------------------
N_DUPLICATE_PAIRS = 14       # defect 1 — near-duplicate invoices
N_ORPHAN_INVOICES = 18       # defect 2 — non-PO spend
N_PRICE_MISMATCH_POS = 22    # defect 3 — >5% invoiced-vs-PO variance
N_VENDORS_MISSING_TERMS = 4  # defect 5
N_VENDORS_MISSING_REGION = 3 # defect 5
N_EXACT_DUP_INVOICES = 3     # defect 8 — double-load artifact
N_EXACT_DUP_PAYMENTS = 3     # defect 8

P_NON_ISO_DATE = 0.30        # defect 6 — share of rows using a non-ISO date format
P_TEXT_NOISE = 0.30          # defect 7 — share of text values with casing/space noise

# --- vendor master ---------------------------------------------------------
CATEGORIES = ["Raw Materials", "IT Services", "Logistics", "Office Supplies", "Consulting"]
CATEGORY_WEIGHTS = [0.26, 0.22, 0.20, 0.16, 0.16]
REGIONS = ["North America", "EMEA", "APAC", "LATAM"]
REGION_WEIGHTS = [0.52, 0.26, 0.14, 0.08]
PAYMENT_TERMS = ["NET15", "NET30", "NET45", "NET60", "2/10 NET30"]
TERMS_WEIGHTS = [0.10, 0.44, 0.22, 0.14, 0.10]
TERMS_DAYS = {"NET15": 15, "NET30": 30, "NET45": 45, "NET60": 60, "2/10 NET30": 30}

CURRENCIES = ["USD", "EUR", "GBP", "CAD"]
CURRENCY_WEIGHTS = [0.85, 0.07, 0.05, 0.03]

# Static FX rates to USD, the reporting currency. Snapshot rates, not time-varying:
# a rate history would be a second SCD problem and answers none of the five business
# questions. The SAME numbers are hard-coded in the Silver layer — if they ever
# diverge, every spend figure downstream silently drifts.
FX_TO_USD = {"USD": 1.00, "EUR": 1.08, "GBP": 1.27, "CAD": 0.74}

# Spend profile per category, in the vendor's own currency.
CATEGORY_AMOUNT_RANGE = {
    "Raw Materials":  (5_000, 250_000),
    "IT Services":    (2_000, 80_000),
    "Logistics":      (500, 40_000),
    "Office Supplies": (200, 8_000),
    "Consulting":     (5_000, 120_000),
}

PO_STATUSES = ["Closed", "Open", "Partially Received", "Cancelled"]
PAYMENT_METHODS = ["ACH", "Wire Transfer", "Check", "Corporate Card"]
PAYMENT_METHOD_WEIGHTS = [0.46, 0.24, 0.22, 0.08]

GL_ACCOUNT_SPEC = [
    ("60100", "Raw Materials Purchases",      "CC-1010", "Manufacturing"),
    ("60110", "Direct Materials - Steel",     "CC-1010", "Manufacturing"),
    ("60120", "Direct Materials - Polymers",  "CC-1020", "Manufacturing"),
    ("60200", "Manufacturing Supplies",       "CC-1030", "Manufacturing"),
    ("60300", "Equipment Maintenance",        "CC-1040", "Manufacturing"),
    ("61100", "Software Licenses",            "CC-2010", "IT"),
    ("61200", "Cloud Infrastructure",         "CC-2010", "IT"),
    ("61300", "IT Consulting Services",       "CC-2020", "IT"),
    ("61400", "Hardware Purchases",           "CC-2030", "IT"),
    ("62100", "Freight - Inbound",            "CC-3010", "Logistics"),
    ("62200", "Freight - Outbound",           "CC-3020", "Logistics"),
    ("62300", "Warehousing Services",         "CC-3030", "Logistics"),
    ("62400", "Customs and Duties",           "CC-3010", "Logistics"),
    ("63100", "Office Supplies",              "CC-4010", "Finance"),
    ("63200", "Printing and Stationery",      "CC-4010", "Finance"),
    ("63300", "Professional Fees - Audit",    "CC-4020", "Finance"),
    ("64100", "Management Consulting",        "CC-5010", "Corporate"),
    ("64200", "Recruitment Fees",             "CC-6010", "HR"),
    ("64300", "Training and Development",     "CC-6010", "HR"),
    ("65100", "Facilities and Utilities",     "CC-7010", "Facilities"),
]

# Which GL accounts a category's spend plausibly codes to.
CATEGORY_TO_ACCOUNTS = {
    "Raw Materials":   ["60100", "60110", "60120", "60200"],
    "IT Services":     ["61100", "61200", "61300", "61400"],
    "Logistics":       ["62100", "62200", "62300", "62400"],
    "Office Supplies": ["63100", "63200", "65100"],
    "Consulting":      ["61300", "63300", "64100", "64200", "64300"],
}


# ===========================================================================
# SEEDED RANDOMNESS
# ===========================================================================
rng = random.Random(SEED)
fake = Faker("en_US")
Faker.seed(SEED)


def rand_date(start: dt.date, end: dt.date) -> dt.date:
    """Uniform random date in [start, end]."""
    return start + dt.timedelta(days=rng.randint(0, (end - start).days))


def lognormal_amount(low: float, high: float) -> float:
    """
    Draw a spend amount skewed toward the low end of [low, high].

    Real procurement spend is heavily right-skewed: many small invoices, few very large
    ones. A uniform draw would produce an unrealistically flat Pareto curve in query 5.3
    and make the concentration analysis meaningless.
    """
    mu = math.log(low * 3)
    sigma = 0.9
    for _ in range(50):
        v = rng.lognormvariate(mu, sigma)
        if low <= v <= high:
            return round(v, 2)
    return round(rng.uniform(low, min(high, low * 6)), 2)


# ===========================================================================
# TEXT / DATE NOISE  (defects 6 and 7)
# ===========================================================================
def noisy_text(value: str, r: random.Random) -> str:
    """Apply realistic casing / whitespace noise to a text value."""
    if value is None or value == "":
        return value
    roll = r.random()
    if roll < 1 - P_TEXT_NOISE:
        return value
    style = r.random()
    if style < 0.35:
        return value.lower()
    if style < 0.65:
        return value.upper()
    if style < 0.85:
        return f"  {value}"          # leading whitespace
    return f"{value}  "              # trailing whitespace


def fmt_date(d: dt.date | None, style: str) -> str:
    """
    Render a date in one of three formats.

    Simulates extracts merged from EBS instances with differing NLS_DATE_FORMAT.
    'oracle' is Oracle's own default display format (DD-MON-YYYY).
    """
    if d is None:
        return ""
    if style == "us":
        return d.strftime("%m/%d/%Y")
    if style == "oracle":
        return d.strftime("%d-%b-%Y").upper()
    return d.strftime("%Y-%m-%d")


def pick_date_style(r: random.Random) -> str:
    roll = r.random()
    if roll < 1 - P_NON_ISO_DATE:
        return "iso"
    return "us" if roll < 1 - P_NON_ISO_DATE + 0.18 else "oracle"


def vendor_noise_rng(vendor_id: str) -> random.Random:
    """
    A stable, per-vendor RNG so a vendor's cosmetic noise is identical in both extracts.

    Uses zlib.crc32 rather than the builtin hash(): Python salts string hashing per
    process via PYTHONHASHSEED, which made the two vendor extracts differ on every run
    and silently broke the determinism guarantee in design decision D6.
    """
    return random.Random(SEED + zlib.crc32(vendor_id.encode("utf-8")))


# ===========================================================================
# ENTITIES
# ===========================================================================
@dataclass
class Vendor:
    vendor_id: str
    vendor_name: str
    category: str
    region: str
    payment_terms: str
    currency: str
    creation_date: dt.date
    # SCD2 change, applied in extract #2
    changed: bool = False
    change_date: dt.date | None = None
    new_category: str | None = None
    new_payment_terms: str | None = None
    # deliberate nulls
    terms_missing: bool = False
    region_missing: bool = False
    is_new_in_extract2: bool = False

    def terms_on(self, when: dt.date) -> str:
        """Payment terms in effect on a given date — the heart of the SCD2 story."""
        if self.changed and self.change_date and when >= self.change_date:
            return self.new_payment_terms or self.payment_terms
        return self.payment_terms

    def terms_days_on(self, when: dt.date) -> int:
        return TERMS_DAYS.get(self.terms_on(when), 30)


@dataclass
class PurchaseOrder:
    po_id: str
    vendor_id: str
    po_date: dt.date
    po_amount: float
    currency: str
    goods_receipt_date: dt.date | None
    po_status: str
    has_invoice: bool = False
    is_price_mismatch: bool = False


@dataclass
class Invoice:
    invoice_id: str
    vendor_id: str
    po_id: str | None
    gl_account_id: str
    invoice_date: dt.date
    due_date: dt.date
    amount: float
    currency: str
    status: str
    is_orphan: bool = False
    is_duplicate_of: str | None = None


@dataclass
class Payment:
    payment_id: str
    invoice_id: str
    payment_date: dt.date
    amount_paid: float
    currency: str
    payment_method: str
    days_late: int = 0


# ===========================================================================
# BUILDERS
# ===========================================================================
def build_gl_accounts() -> list[dict]:
    return [
        {
            "account_id": aid,
            "account_name": name,
            "cost_center": cc,
            "department": dept,
        }
        for aid, name, cc, dept in GL_ACCOUNT_SPEC
    ]


def build_vendors() -> tuple[list[Vendor], list[Vendor]]:
    """
    Build the vendor master.

    Returns (extract1_vendors, new_vendors). Extract 2 is extract1 (with changes applied)
    plus the new vendors.
    """
    vendors: list[Vendor] = []
    used_names: set[str] = set()

    for i in range(1, N_VENDORS + 1):
        category = rng.choices(CATEGORIES, CATEGORY_WEIGHTS)[0]

        # Faker can repeat company names; a duplicate vendor_name would create a
        # false positive in any name-based analysis, so force uniqueness.
        for _ in range(60):
            name = fake.company()
            if name not in used_names:
                break
        used_names.add(name)

        vendors.append(
            Vendor(
                vendor_id=f"V{i:04d}",
                vendor_name=name,
                category=category,
                region=rng.choices(REGIONS, REGION_WEIGHTS)[0],
                payment_terms=rng.choices(PAYMENT_TERMS, TERMS_WEIGHTS)[0],
                currency=rng.choices(CURRENCIES, CURRENCY_WEIGHTS)[0],
                # Vendors predate the transaction window — they existed before we
                # started capturing spend.
                creation_date=rand_date(
                    WINDOW_START - dt.timedelta(days=900), WINDOW_START
                ),
            )
        )

    # --- SCD Type 2 changes (6 vendors) ------------------------------------
    # Changes are placed in the middle of the window so each changed vendor has
    # invoices both before and after — without that, query 5.6 cannot demonstrate
    # differing terms for the same vendor.
    changed = rng.sample(vendors, 6)
    for idx, v in enumerate(changed):
        v.changed = True
        v.change_date = rand_date(dt.date(2025, 5, 1), dt.date(2025, 11, 30))
        if idx % 2 == 0:
            # terms renegotiated
            v.new_payment_terms = rng.choice([t for t in PAYMENT_TERMS if t != v.payment_terms])
            v.new_category = v.category
        else:
            # re-classified, and terms move with it
            v.new_category = rng.choice([c for c in CATEGORIES if c != v.category])
            v.new_payment_terms = rng.choice([t for t in PAYMENT_TERMS if t != v.payment_terms])

    # --- deliberate nulls (defect 5) ---------------------------------------
    # Drawn from vendors that were NOT changed, so a missing value can never be
    # confused with an SCD2 transition during validation.
    unchanged = [v for v in vendors if not v.changed]
    for v in rng.sample(unchanged, N_VENDORS_MISSING_TERMS):
        v.terms_missing = True
    for v in rng.sample([x for x in unchanged if not x.terms_missing], N_VENDORS_MISSING_REGION):
        v.region_missing = True

    # --- newly onboarded vendors, appearing only in extract #2 -------------
    new_vendors: list[Vendor] = []
    for j in range(1, N_NEW_VENDORS + 1):
        for _ in range(60):
            name = fake.company()
            if name not in used_names:
                break
        used_names.add(name)
        new_vendors.append(
            Vendor(
                vendor_id=f"V{N_VENDORS + j:04d}",
                vendor_name=name,
                category=rng.choices(CATEGORIES, CATEGORY_WEIGHTS)[0],
                region=rng.choices(REGIONS, REGION_WEIGHTS)[0],
                payment_terms=rng.choices(PAYMENT_TERMS, TERMS_WEIGHTS)[0],
                currency=rng.choices(CURRENCIES, CURRENCY_WEIGHTS)[0],
                creation_date=rand_date(dt.date(2026, 3, 1), dt.date(2026, 7, 31)),
                is_new_in_extract2=True,
            )
        )

    return vendors, new_vendors


def build_purchase_orders(vendors: list[Vendor]) -> list[PurchaseOrder]:
    """
    POs are allocated to vendors with a skewed weighting so that spend concentrates —
    a flat allocation would make the Pareto analysis in query 5.3 show no concentration
    at all, which is the opposite of the business finding we need to demonstrate.
    """
    weights = [rng.lognormvariate(0, 0.85) for _ in vendors]

    pos: list[PurchaseOrder] = []
    for i in range(1, N_PURCHASE_ORDERS + 1):
        vendor = rng.choices(vendors, weights)[0]
        low, high = CATEGORY_AMOUNT_RANGE[vendor.category]
        # POs stop ~45 days before the window end so their invoices have room to land.
        po_date = rand_date(WINDOW_START, WINDOW_END - dt.timedelta(days=45))
        amount = lognormal_amount(low, high)

        # Goods receipt normally follows the PO by days-to-weeks. ~8% never received.
        if rng.random() < 0.08:
            gr_date = None
            status = rng.choices(["Open", "Cancelled"], [0.75, 0.25])[0]
        else:
            gr_date = po_date + dt.timedelta(days=rng.randint(3, 45))
            if gr_date > WINDOW_END:
                gr_date = WINDOW_END
            status = rng.choices(PO_STATUSES, [0.62, 0.18, 0.16, 0.04])[0]

        pos.append(
            PurchaseOrder(
                po_id=f"PO{100000 + i}",
                vendor_id=vendor.vendor_id,
                po_date=po_date,
                po_amount=amount,
                currency=vendor.currency,
                goods_receipt_date=gr_date,
                po_status=status,
            )
        )
    return pos


def build_invoices(
    vendors: list[Vendor],
    pos: list[PurchaseOrder],
    gl_accounts: list[dict],
) -> list[Invoice]:
    vendor_by_id = {v.vendor_id: v for v in vendors}
    invoices: list[Invoice] = []
    seq = 200000

    # ---- decide which POs get billed, and which are price mismatches -------
    billable = pos.copy()
    rng.shuffle(billable)
    unbilled = set(p.po_id for p in billable[:N_POS_WITHOUT_INVOICE])
    to_bill = [p for p in pos if p.po_id not in unbilled]

    mismatch_pos = set(p.po_id for p in rng.sample(to_bill, N_PRICE_MISMATCH_POS))

    for po in to_bill:
        vendor = vendor_by_id[po.vendor_id]
        po.has_invoice = True
        po.is_price_mismatch = po.po_id in mismatch_pos

        # ~38% of POs bill across multiple invoices (staged deliveries).
        n_inv = rng.choices([1, 2, 3], [0.62, 0.27, 0.11])[0]

        # Total to invoice against this PO.
        if po.is_price_mismatch:
            # Push clearly outside the ±5% tolerance so the exception is unambiguous.
            direction = rng.choice([-1, 1])
            variance = direction * rng.uniform(0.07, 0.28)
        else:
            # Within tolerance — small, realistic rounding/freight differences.
            variance = rng.uniform(-0.025, 0.025)
        total = round(po.po_amount * (1 + variance), 2)

        # Split the total across n_inv invoices.
        if n_inv == 1:
            splits = [total]
        else:
            cuts = sorted(rng.uniform(0.2, 0.8) for _ in range(n_inv - 1))
            bounds = [0.0] + cuts + [1.0]
            splits = [round(total * (bounds[k + 1] - bounds[k]), 2) for k in range(n_inv)]
            # Absorb rounding drift into the final split so the sum is exact.
            splits[-1] = round(total - sum(splits[:-1]), 2)

        # Invoices follow goods receipt where one exists, else the PO date.
        base = po.goods_receipt_date or po.po_date
        for k, amt in enumerate(splits):
            if amt <= 0:
                continue
            inv_date = base + dt.timedelta(days=rng.randint(0, 25) + k * rng.randint(10, 40))
            if inv_date > WINDOW_END:
                inv_date = WINDOW_END - dt.timedelta(days=rng.randint(0, 10))
            if inv_date < po.po_date:
                inv_date = po.po_date

            seq += 1
            invoices.append(
                Invoice(
                    invoice_id=f"INV{seq}",
                    vendor_id=vendor.vendor_id,
                    po_id=po.po_id,
                    gl_account_id=rng.choice(CATEGORY_TO_ACCOUNTS[vendor.category]),
                    invoice_date=inv_date,
                    due_date=inv_date + dt.timedelta(days=vendor.terms_days_on(inv_date)),
                    amount=amt,
                    currency=vendor.currency,
                    status="Unpaid",   # settled later in build_payments
                )
            )

    # ---- defect 2: orphan invoices (non-PO spend) -------------------------
    for _ in range(N_ORPHAN_INVOICES):
        vendor = rng.choice(vendors)
        low, high = CATEGORY_AMOUNT_RANGE[vendor.category]
        inv_date = rand_date(WINDOW_START, WINDOW_END)
        seq += 1
        invoices.append(
            Invoice(
                invoice_id=f"INV{seq}",
                vendor_id=vendor.vendor_id,
                po_id=None,
                gl_account_id=rng.choice(CATEGORY_TO_ACCOUNTS[vendor.category]),
                invoice_date=inv_date,
                due_date=inv_date + dt.timedelta(days=vendor.terms_days_on(inv_date)),
                amount=lognormal_amount(low, min(high, low * 8)),
                currency=vendor.currency,
                status="Unpaid",
                is_orphan=True,
            )
        )

    # ---- defect 1: near-duplicate invoices --------------------------------
    # Same vendor, same amount, invoice date within 3 days, different invoice_id.
    # This is what query 5.4's self-join must find.
    sources = rng.sample([i for i in invoices if not i.is_orphan], N_DUPLICATE_PAIRS)
    for src in sources:
        vendor = vendor_by_id[src.vendor_id]
        offset = rng.choice([1, 2, 3])
        dup_date = src.invoice_date + dt.timedelta(days=offset)
        if dup_date > WINDOW_END:
            dup_date = src.invoice_date - dt.timedelta(days=offset)
        seq += 1
        invoices.append(
            Invoice(
                invoice_id=f"INV{seq}",
                vendor_id=src.vendor_id,
                # A duplicated bill normally quotes the same PO; leaving it null
                # would make the duplicate detectable as an orphan instead, which
                # would confound the two defects.
                po_id=src.po_id,
                gl_account_id=src.gl_account_id,
                invoice_date=dup_date,
                due_date=dup_date + dt.timedelta(days=vendor.terms_days_on(dup_date)),
                amount=src.amount,          # identical amount is the giveaway
                currency=src.currency,
                status="Unpaid",
                is_duplicate_of=src.invoice_id,
            )
        )

    invoices.sort(key=lambda i: (i.invoice_date, i.invoice_id))
    return invoices


def build_payments(invoices: list[Invoice], vendors: list[Vendor]) -> list[Payment]:
    """
    Settle most invoices, leaving a deliberate unpaid tail spread across every aging
    bucket relative to AS_OF_DATE.
    """
    payments: list[Payment] = []
    seq = 300000

    # Bucket every invoice by how overdue it is as of AS_OF_DATE.
    def bucket(inv: Invoice) -> str:
        d = (AS_OF_DATE - inv.due_date).days
        if d <= 0:
            return "not_due"
        if d <= 30:
            return "0-30"
        if d <= 60:
            return "31-60"
        if d <= 90:
            return "61-90"
        return "90+"

    by_bucket: dict[str, list[Invoice]] = {
        "not_due": [], "0-30": [], "31-60": [], "61-90": [], "90+": []
    }
    for inv in invoices:
        by_bucket[bucket(inv)].append(inv)

    # How many invoices to deliberately leave unpaid in each bucket.
    #
    # Recent buckets keep a large share (normal AP lag). The 90+ rate is deliberately
    # low, but 90+ spans ~21 of the 24 months so it still yields a visible tail — that
    # tail is the "we have a real overdue problem" finding the dashboard exists to
    # surface. The middle buckets are held high because they are populated from only a
    # ~30-day slice of invoices each; a low rate there would leave them near-empty and
    # the aging chart would have nothing to show between the two ends.
    unpaid_targets = {"not_due": 0.85, "0-30": 0.65, "31-60": 0.55, "61-90": 0.45, "90+": 0.05}

    unpaid_ids: set[str] = set()
    for b, invs in by_bucket.items():
        want = int(round(len(invs) * unpaid_targets[b]))
        for inv in rng.sample(invs, min(want, len(invs))):
            unpaid_ids.add(inv.invoice_id)

    for inv in invoices:
        if inv.invoice_id in unpaid_ids:
            inv.status = "Unpaid"
            continue

        inv.status = "Paid"
        terms_days = (inv.due_date - inv.invoice_date).days

        # Payment timing relative to the due date.
        #
        # The "on time" band stops AT the due date rather than a few days past it.
        # Letting it run to due+4 would tag roughly half of on-time payments as late
        # and inflate the late-payment count to ~48% of all payments, which would
        # misrepresent the injected defect in the ground-truth log.
        roll = rng.random()
        if roll < 0.30:
            offset = rng.randint(max(3, terms_days - 18), max(4, terms_days - 5))   # early
        elif roll < 0.72:
            offset = rng.randint(max(4, terms_days - 4), terms_days)                # on time
        elif roll < 0.92:
            offset = terms_days + rng.randint(3, 35)                                # late
        else:
            offset = terms_days + rng.randint(36, 110)                              # very late

        pay_date = inv.invoice_date + dt.timedelta(days=max(1, offset))
        if pay_date > AS_OF_DATE:
            pay_date = AS_OF_DATE - dt.timedelta(days=rng.randint(0, 5))
        if pay_date < inv.invoice_date:
            pay_date = inv.invoice_date + dt.timedelta(days=1)

        seq += 1
        payments.append(
            Payment(
                payment_id=f"PAY{seq}",
                invoice_id=inv.invoice_id,
                payment_date=pay_date,
                amount_paid=inv.amount,
                currency=inv.currency,
                payment_method=rng.choices(PAYMENT_METHODS, PAYMENT_METHOD_WEIGHTS)[0],
                days_late=max(0, (pay_date - inv.due_date).days),
            )
        )

    payments.sort(key=lambda p: (p.payment_date, p.payment_id))
    return payments


# ===========================================================================
# CSV WRITING  (noise applied here, so the in-memory model stays clean)
# ===========================================================================
def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)


def vendor_row(v: Vendor, extract: int, noise: random.Random) -> list:
    """
    Render a vendor for a given extract.

    Casing noise is derived from a per-vendor seeded RNG so it is IDENTICAL across both
    extracts. If it varied, the Phase 4 SCD2 MERGE would read 'ACME Corp' vs 'Acme Corp'
    as a real change and emit a spurious version row.
    """
    if extract == 2 and v.changed:
        category = v.new_category or v.category
        terms = v.new_payment_terms or v.payment_terms
        last_update = v.change_date
    else:
        category = v.category
        terms = v.payment_terms
        last_update = v.creation_date

    if v.terms_missing:
        terms = ""
    region = "" if v.region_missing else v.region

    style = pick_date_style(noise)
    return [
        v.vendor_id,
        noisy_text(v.vendor_name, noise),
        noisy_text(category, noise),
        noisy_text(region, noise) if region else "",
        terms,
        v.currency,
        fmt_date(v.creation_date, style),
        fmt_date(last_update, style),
    ]


def write_ground_truth_md(gt: dict, aging_counts: dict, aging_amounts: dict) -> None:
    """
    Render the readable ground-truth log.

    Generated rather than hand-written so it cannot drift from ground_truth.json —
    a stale flaw log would silently invalidate every Phase 3 and Phase 5 exit test
    that validates against it.
    """
    rc = gt["row_counts"]
    d1, d3 = gt["defect_1_duplicate_invoice_pairs"], gt["defect_3_price_mismatch_pos"]
    d4, d5, d8 = gt["defect_4_late_payments"], gt["defect_5_missing_fields"], gt["defect_8_exact_duplicate_rows"]
    kpi = gt["kpi_cross_check"]

    lines = [
        "# Phase 1 — Ground Truth (injected defect log)",
        "",
        "> **Generated file — do not edit by hand.** Written by `scripts/generate_data.py`.",
        f"> Seed `{gt['generated_with_seed']}`, as-of date `{gt['as_of_date']}`, "
        f"window `{gt['window']['start']}` to `{gt['window']['end']}`.",
        "",
        "This is the reference Phases 3 and 5 validate against. Phase 3 must prove it did not",
        "clean these defects away; Phase 5 must prove its queries actually find them.",
        "",
        "---",
        "",
        "## Row counts",
        "",
        "| File | Rows | Distinct keys | Target |",
        "|---|---|---|---|",
        f"| `gl_accounts.csv` | {rc['gl_accounts']} | {rc['gl_accounts']} | 15–25 |",
        f"| `vendors.csv` | {rc['vendors']} | {rc['vendors']} | 40–60 |",
        f"| `vendors_update.csv` | {rc['vendors_update']} | {rc['vendors_update']} | — (2nd extract) |",
        f"| `purchase_orders.csv` | {rc['purchase_orders']} | {rc['purchase_orders']} | 300–500 |",
        f"| `invoices.csv` | {rc['invoices_file_rows']} | {rc['invoices_distinct']} | 600–900 |",
        f"| `payments.csv` | {rc['payments_file_rows']} | {rc['payments_distinct']} | 500–800 |",
        "",
        "Invoice and payment file rows exceed distinct keys because of defect 8 below.",
        "",
        "---",
        "",
        "## Injected defects",
        "",
        f"### 1. Near-duplicate invoices — {d1['count']} pairs",
        "",
        "Same vendor, same amount, invoice dates within 3 days, different `invoice_id`.",
        "**Silver must preserve these.** Query 5.4 detects them.",
        "",
        "| Original | Duplicate | Vendor | Amount | Duplicate date |",
        "|---|---|---|---|---|",
    ]
    for p in d1["pairs"]:
        lines.append(
            f"| `{p['original']}` | `{p['duplicate']}` | {p['vendor_id']} | "
            f"{p['amount']:,.2f} | {p['duplicate_date']} |"
        )

    lines += [
        "",
        f"### 2. Orphan invoices — {gt['defect_2_orphan_invoices']['count']}",
        "",
        "Invoices with no `po_id` — non-PO spend, a genuine audit red flag rather than a data error.",
        "",
        "```",
        "  " + ", ".join(gt["defect_2_orphan_invoices"]["invoice_ids"]),
        "```",
        "",
        f"### 3. Invoice-vs-PO variance beyond 5% — {d3['count']} deliberately mis-priced",
        "",
        f"- **{d3['count']}** POs were deliberately invoiced outside the ±5% tolerance.",
        f"- **{d3['attributable_to_duplicate_invoices']}** more are pushed past tolerance by a "
        "defect-1 duplicate quoting the same `po_id`.",
        f"- **Query 5.5 should therefore return {d3['expected_query_5_5_exceptions']} exceptions**, not "
        f"{d3['count']}.",
        "",
        "That overlap is intentional and worth being able to explain: a duplicated bill really does",
        "show up as over-billing against the PO. The two controls catch the same event from",
        "different angles.",
        "",
        f"### 4. Late payments — {d4['count']} ({d4['pct_of_payments_late']}% of payments)",
        "",
        f"- {d4['count_more_than_10_days_late']} are more than 10 days late",
        f"- average {d4['avg_days_late']} days late, worst {d4['max_days_late']} days",
        "",
        "### 5. Missing fields",
        "",
        f"- `payment_terms` blank for **{len(d5['vendors_missing_payment_terms'])}** vendors: "
        + ", ".join(f"`{v}`" for v in d5["vendors_missing_payment_terms"]),
        f"- `region` blank for **{len(d5['vendors_missing_region'])}** vendors: "
        + ", ".join(f"`{v}`" for v in d5["vendors_missing_region"]),
        "",
        "### 6. Mixed date formats",
        "",
        "`YYYY-MM-DD`, `MM/DD/YYYY`, and Oracle's default `DD-MON-YYYY`, consistent within a row",
        "and varying between rows — as happens when extracts from EBS instances with different",
        "`NLS_DATE_FORMAT` settings are concatenated.",
        "",
        "### 7. Text casing and whitespace noise",
        "",
        f"Roughly {int(gt['defect_7_text_noise']['approx_share'] * 100)}% of text values are lower-cased, "
        "upper-cased, or carry leading/trailing spaces.",
        "",
        f"### 8. Exact whole-row duplicates — {d8['total']}",
        "",
        "Byte-identical rows repeating an existing primary key — a double-load artifact.",
        "**Silver must remove these**, unlike defect 1.",
        "",
        f"- invoices: " + ", ".join(f"`{i}`" for i in d8["invoices"]),
        f"- payments: " + ", ".join(f"`{i}`" for i in d8["payments"]),
        "",
        "---",
        "",
        "## Unpaid AP aging profile",
        "",
        f"As of {gt['as_of_date']}, {gt['unpaid_aging_profile']['total_unpaid_invoices']} invoices are unpaid.",
        "",
        "| Bucket | Invoices | Amount (entered currency) |",
        "|---|---|---|",
    ]
    for b in ["Not Due", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days"]:
        lines.append(f"| {b} | {aging_counts.get(b, 0)} | {aging_amounts.get(b, 0.0):,.2f} |")

    lines += [
        "",
        "> Buckets are computed against `CURRENT_DATE`, so membership shifts as real time passes.",
        "> The spread is wide enough that all five stay populated for months after generation.",
        "",
        "---",
        "",
        "## SCD Type 2 source changes",
        "",
        f"{gt['scd2_vendor_changes']['count']} vendors changed between the two extracts. "
        f"{len(gt['new_vendors_in_extract_2'])} vendors are new in extract 2 "
        f"({', '.join(gt['new_vendors_in_extract_2'])}), exercising the MERGE insert path.",
        "",
        "| Vendor | Change date | Category | Payment terms |",
        "|---|---|---|---|",
    ]
    for c in gt["scd2_vendor_changes"]["changes"]:
        cat = (f"{c['category_before']} → {c['category_after']}"
               if c["category_before"] != c["category_after"] else "unchanged")
        lines.append(
            f"| `{c['vendor_id']}` | {c['change_date']} | {cat} | "
            f"{c['terms_before']} → {c['terms_after']} |"
        )

    lines += [
        "",
        "`DIM_VENDOR` should therefore end up with "
        f"**{gt['row_counts']['vendors_update'] + gt['scd2_vendor_changes']['count']} rows for "
        f"{gt['row_counts']['vendors_update']} vendors** — the proof SCD2 is real rather than decorative.",
        "",
        "---",
        "",
        "## KPI cross-check",
        "",
        "Phase 5's SQL and Phase 6's DAX must reproduce these. Recording them turns "
        "\"the number looks plausible\" into a pass/fail check.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Average DPO | {kpi['avg_dpo_days']} days |",
        f"| Total invoiced (USD) | {kpi['total_invoiced_usd']:,.2f} |",
        f"| Total outstanding (USD) | {kpi['total_outstanding_usd']:,.2f} |",
        f"| Total overdue (USD) | {kpi['total_overdue_usd']:,.2f} |",
        f"| % overdue of outstanding | {kpi['pct_overdue_of_outstanding']}% |",
        f"| Paid invoices | {kpi['paid_invoice_count']} |",
        f"| Unpaid invoices | {kpi['unpaid_invoice_count']} |",
        "",
        "FX rates to USD: " + ", ".join(f"`{k}={v}`" for k, v in gt["fx_rates_to_usd"].items())
        + ". The Silver layer hard-codes the same values.",
        "",
        "---",
        "",
        f"## Purchase orders with no invoice — {gt['pos_without_invoice']}",
        "",
        "Open commitments. These are the PO-side exceptions: goods ordered and in some cases",
        "received, but never billed.",
        "",
    ]

    (DOCS_DIR / "04_phase1_ground_truth.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating Oracle EBS-style Finance & Procurement extracts...")
    print(f"  seed={SEED}  window={WINDOW_START}..{WINDOW_END}  as_of={AS_OF_DATE}\n")

    gl_accounts = build_gl_accounts()
    vendors, new_vendors = build_vendors()
    pos = build_purchase_orders(vendors)
    invoices = build_invoices(vendors, pos, gl_accounts)
    payments = build_payments(invoices, vendors)

    # -------- gl_accounts.csv ---------------------------------------------
    write_csv(
        DATA_DIR / "gl_accounts.csv",
        ["account_id", "account_name", "cost_center", "department"],
        [[a["account_id"], a["account_name"], a["cost_center"], a["department"]] for a in gl_accounts],
    )

    # -------- vendors.csv / vendors_update.csv ----------------------------
    vhead = [
        "vendor_id", "vendor_name", "category", "region",
        "payment_terms", "currency", "creation_date", "last_update_date",
    ]
    # vendor_noise_rng() is derived purely from the vendor_id, so each extract
    # independently reproduces the same noise for the same vendor.
    write_csv(
        DATA_DIR / "vendors.csv", vhead,
        [vendor_row(v, 1, vendor_noise_rng(v.vendor_id)) for v in vendors],
    )
    write_csv(
        DATA_DIR / "vendors_update.csv", vhead,
        [vendor_row(v, 2, vendor_noise_rng(v.vendor_id)) for v in vendors + new_vendors],
    )

    # -------- purchase_orders.csv -----------------------------------------
    po_rows = []
    for p in pos:
        s = pick_date_style(rng)
        po_rows.append([
            p.po_id, p.vendor_id, fmt_date(p.po_date, s), f"{p.po_amount:.2f}",
            p.currency, fmt_date(p.goods_receipt_date, s), noisy_text(p.po_status, rng),
        ])
    write_csv(
        DATA_DIR / "purchase_orders.csv",
        ["po_id", "vendor_id", "po_date", "po_amount", "currency",
         "goods_receipt_date", "po_status"],
        po_rows,
    )

    # -------- invoices.csv -------------------------------------------------
    inv_rows = []
    for i in invoices:
        s = pick_date_style(rng)
        inv_rows.append([
            i.invoice_id, i.vendor_id, i.po_id or "", i.gl_account_id,
            fmt_date(i.invoice_date, s), fmt_date(i.due_date, s),
            f"{i.amount:.2f}", i.currency, noisy_text(i.status, rng),
        ])
    # defect 8: exact whole-row duplicates (double-load artifact)
    exact_dup_invoice_ids = []
    for row in rng.sample(inv_rows, N_EXACT_DUP_INVOICES):
        inv_rows.append(list(row))
        exact_dup_invoice_ids.append(row[0])
    write_csv(
        DATA_DIR / "invoices.csv",
        ["invoice_id", "vendor_id", "po_id", "gl_account_id", "invoice_date",
         "due_date", "amount", "currency", "status"],
        inv_rows,
    )

    # -------- payments.csv -------------------------------------------------
    pay_rows = []
    for p in payments:
        s = pick_date_style(rng)
        pay_rows.append([
            p.payment_id, p.invoice_id, fmt_date(p.payment_date, s),
            f"{p.amount_paid:.2f}", p.currency, noisy_text(p.payment_method, rng),
        ])
    exact_dup_payment_ids = []
    for row in rng.sample(pay_rows, N_EXACT_DUP_PAYMENTS):
        pay_rows.append(list(row))
        exact_dup_payment_ids.append(row[0])
    write_csv(
        DATA_DIR / "payments.csv",
        ["payment_id", "invoice_id", "payment_date", "amount_paid",
         "currency", "payment_method"],
        pay_rows,
    )

    # =======================================================================
    # GROUND TRUTH
    # =======================================================================
    dup_pairs = [
        {"original": i.is_duplicate_of, "duplicate": i.invoice_id,
         "vendor_id": i.vendor_id, "amount": i.amount,
         "duplicate_date": i.invoice_date.isoformat()}
        for i in invoices if i.is_duplicate_of
    ]
    orphans = [i.invoice_id for i in invoices if i.is_orphan]

    # Two different totals per PO, because defects 1 and 3 interact.
    #
    # A near-duplicate invoice quotes the SAME po_id as the invoice it duplicates, so it
    # inflates the amount billed against that PO — often past the 5% tolerance. That is
    # realistic and desirable (a duplicate really does show up as an over-billing), but it
    # means two different questions have two different answers:
    #
    #   excl_dup : how many POs did we deliberately mis-price?     -> the injected defect
    #   incl_dup : how many exceptions will query 5.5 actually return? -> what Phase 5 sees
    #
    # Recording only the first would make Phase 5 look like it over-reported.
    def totals_by_po(include_duplicates: bool) -> dict[str, float]:
        acc: dict[str, float] = {}
        for i in invoices:
            if not i.po_id:
                continue
            if i.is_duplicate_of and not include_duplicates:
                continue
            acc[i.po_id] = round(acc.get(i.po_id, 0.0) + i.amount, 2)
        return acc

    def variances(totals: dict[str, float]) -> list[dict]:
        out = []
        for p in pos:
            if p.po_id in totals and p.po_amount:
                var = (totals[p.po_id] - p.po_amount) / p.po_amount
                if abs(var) > 0.05:
                    out.append({
                        "po_id": p.po_id, "po_amount": p.po_amount,
                        "invoiced_amount": totals[p.po_id],
                        "pct_variance": round(var * 100, 2),
                    })
        return out

    mismatches = variances(totals_by_po(include_duplicates=False))
    mismatches_incl_dup = variances(totals_by_po(include_duplicates=True))

    late = [p for p in payments if p.days_late > 0]
    materially_late = [p for p in payments if p.days_late > 10]
    unpaid = [i for i in invoices if i.status == "Unpaid"]

    # ---- KPI cross-check values ------------------------------------------
    # Phase 5's SQL and Phase 6's DAX must reproduce these. Recording them here
    # turns "the dashboard number looks plausible" into a pass/fail check.
    inv_by_id = {i.invoice_id: i for i in invoices}
    dpo_days = [
        (p.payment_date - inv_by_id[p.invoice_id].invoice_date).days for p in payments
    ]
    avg_dpo = round(sum(dpo_days) / len(dpo_days), 2) if dpo_days else 0

    def usd(amount: float, currency: str) -> float:
        # Rounded per row, not at the end of the sum. The Silver layer stores a
        # concrete AMOUNT_USD per invoice, so the per-row rounded value IS the
        # figure that reaches the fact table. Summing unrounded products here
        # would leave these cross-check totals a few cents adrift from every
        # downstream query, and a mismatch that small is worse than a large one:
        # it looks like a rounding bug worth hunting rather than a definition
        # difference.
        return round(amount * FX_TO_USD[currency], 2)

    total_invoiced_usd = round(sum(usd(i.amount, i.currency) for i in invoices), 2)
    total_outstanding_usd = round(sum(usd(i.amount, i.currency) for i in unpaid), 2)
    total_overdue_usd = round(
        sum(usd(i.amount, i.currency) for i in unpaid if i.due_date < AS_OF_DATE), 2
    )

    def aging_bucket(inv: Invoice) -> str:
        d = (AS_OF_DATE - inv.due_date).days
        if d <= 0:
            return "Not Due"
        if d <= 30:
            return "0-30 Days"
        if d <= 60:
            return "31-60 Days"
        if d <= 90:
            return "61-90 Days"
        return "90+ Days"

    aging_counts: dict[str, int] = {}
    aging_amounts: dict[str, float] = {}
    for i in unpaid:
        b = aging_bucket(i)
        aging_counts[b] = aging_counts.get(b, 0) + 1
        aging_amounts[b] = round(aging_amounts.get(b, 0.0) + i.amount, 2)

    scd2 = [
        {"vendor_id": v.vendor_id, "change_date": v.change_date.isoformat(),
         "category_before": v.category, "category_after": v.new_category,
         "terms_before": v.payment_terms, "terms_after": v.new_payment_terms}
        for v in vendors if v.changed
    ]

    ground_truth = {
        "generated_with_seed": SEED,
        "as_of_date": AS_OF_DATE.isoformat(),
        "window": {"start": WINDOW_START.isoformat(), "end": WINDOW_END.isoformat()},
        "row_counts": {
            "gl_accounts": len(gl_accounts),
            "vendors": len(vendors),
            "vendors_update": len(vendors) + len(new_vendors),
            "purchase_orders": len(pos),
            "invoices_file_rows": len(inv_rows),
            "invoices_distinct": len(invoices),
            "payments_file_rows": len(pay_rows),
            "payments_distinct": len(payments),
        },
        "defect_1_duplicate_invoice_pairs": {
            "count": len(dup_pairs), "pairs": dup_pairs,
        },
        "defect_2_orphan_invoices": {
            "count": len(orphans), "invoice_ids": orphans,
        },
        "defect_3_price_mismatch_pos": {
            "_comment": (
                "count = POs deliberately mis-priced. expected_query_5_5_exceptions also "
                "counts POs pushed past tolerance by a defect-1 duplicate invoice sharing "
                "the same po_id. Query 5.5 runs over all invoices, so it will return the "
                "larger number - that is correct, not over-reporting."
            ),
            "count": len(mismatches),
            "tolerance_pct": 5.0,
            "expected_query_5_5_exceptions": len(mismatches_incl_dup),
            "attributable_to_duplicate_invoices": len(mismatches_incl_dup) - len(mismatches),
            "pos": mismatches,
        },
        "defect_4_late_payments": {
            "count": len(late),
            "count_more_than_10_days_late": len(materially_late),
            "pct_of_payments_late": round(100 * len(late) / len(payments), 1) if payments else 0,
            "max_days_late": max((p.days_late for p in late), default=0),
            "avg_days_late": round(sum(p.days_late for p in late) / len(late), 1) if late else 0,
        },
        "defect_5_missing_fields": {
            "vendors_missing_payment_terms": [v.vendor_id for v in vendors if v.terms_missing],
            "vendors_missing_region": [v.vendor_id for v in vendors if v.region_missing],
        },
        "defect_6_mixed_date_formats": {
            "formats": ["YYYY-MM-DD", "MM/DD/YYYY", "DD-MON-YYYY"],
            "approx_non_iso_share": P_NON_ISO_DATE,
        },
        "defect_7_text_noise": {"approx_share": P_TEXT_NOISE},
        "defect_8_exact_duplicate_rows": {
            "invoices": exact_dup_invoice_ids,
            "payments": exact_dup_payment_ids,
            "total": len(exact_dup_invoice_ids) + len(exact_dup_payment_ids),
        },
        "unpaid_aging_profile": {
            "total_unpaid_invoices": len(unpaid),
            "counts_by_bucket": aging_counts,
            "amounts_by_bucket": aging_amounts,
        },
        "scd2_vendor_changes": {"count": len(scd2), "changes": scd2},
        "new_vendors_in_extract_2": [v.vendor_id for v in new_vendors],
        "pos_without_invoice": sum(1 for p in pos if not p.has_invoice),
        "fx_rates_to_usd": FX_TO_USD,
        "kpi_cross_check": {
            "_comment": "Phase 5 SQL and Phase 6 DAX must reproduce these figures.",
            "avg_dpo_days": avg_dpo,
            "total_invoiced_usd": total_invoiced_usd,
            "total_outstanding_usd": total_outstanding_usd,
            "total_overdue_usd": total_overdue_usd,
            "pct_overdue_of_outstanding": (
                round(100 * total_overdue_usd / total_outstanding_usd, 2)
                if total_outstanding_usd else 0
            ),
            "paid_invoice_count": len(payments),
            "unpaid_invoice_count": len(unpaid),
        },
    }

    (DOCS_DIR / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2), encoding="utf-8"
    )
    write_ground_truth_md(ground_truth, aging_counts, aging_amounts)

    # -------- console summary ---------------------------------------------
    print("FILES WRITTEN")
    for name in ["gl_accounts.csv", "vendors.csv", "vendors_update.csv",
                 "purchase_orders.csv", "invoices.csv", "payments.csv"]:
        p = DATA_DIR / name
        n = sum(1 for _ in p.open(encoding="utf-8")) - 1
        print(f"  {name:<24} {n:>5} rows")

    print("\nINJECTED DEFECTS")
    print(f"  1. duplicate invoice pairs   {len(dup_pairs):>5}   (target 10-15)")
    print(f"  2. orphan invoices           {len(orphans):>5}   (target 15-20)")
    print(f"  3. price-mismatch POs        {len(mismatches):>5}   (target >=10)")
    print(f"     + inflated by duplicates  {len(mismatches_incl_dup) - len(mismatches):>5}   "
          f"= {len(mismatches_incl_dup)} exceptions query 5.5 will return")
    print(f"  4. late payments             {len(late):>5}   avg {ground_truth['defect_4_late_payments']['avg_days_late']} days late")
    print(f"  5. vendors missing terms     {len(ground_truth['defect_5_missing_fields']['vendors_missing_payment_terms']):>5}")
    print(f"     vendors missing region    {len(ground_truth['defect_5_missing_fields']['vendors_missing_region']):>5}")
    print(f"  8. exact duplicate rows      {ground_truth['defect_8_exact_duplicate_rows']['total']:>5}")

    print("\nUNPAID AGING PROFILE (as of {})".format(AS_OF_DATE))
    for b in ["Not Due", "0-30 Days", "31-60 Days", "61-90 Days", "90+ Days"]:
        print(f"  {b:<12} {aging_counts.get(b, 0):>4} invoices   "
              f"{aging_amounts.get(b, 0.0):>14,.2f}")

    print(f"\nSCD2 vendor changes: {len(scd2)}   new vendors in extract 2: {len(new_vendors)}")
    print(f"POs with no invoice: {ground_truth['pos_without_invoice']}")

    print("\nKPI CROSS-CHECK (Phase 5 SQL and Phase 6 DAX must reproduce these)")
    print(f"  average DPO             {avg_dpo:>16,.2f} days")
    print(f"  total invoiced (USD)    {total_invoiced_usd:>16,.2f}")
    print(f"  total outstanding (USD) {total_outstanding_usd:>16,.2f}")
    print(f"  total overdue (USD)     {total_overdue_usd:>16,.2f}")
    print("\nGround truth -> docs/ground_truth.json")


if __name__ == "__main__":
    main()
