"""Synthetic equipment-financing agreements with planted traps. PLAN §5.

    python data/generate.py            # writes data/docs/*.pdf and data/golden.json
    python data/generate.py --seed 7   # different documents, same structure

Everything here is invented. No real lenders, borrowers, account numbers, or
financial records. Ground truth in golden.json is exact by construction: the
generator plants the terms, then computes total cost and effective APR from
those same terms using the formulas documented in `finance` below. compute.py
(T05) must reproduce these numbers.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "docs"
DEFAULT_GOLDEN = REPO_ROOT / "data" / "golden.json"

TRAP_TYPES = ["T_BALLOON", "T_APR_GAP", "T_ORIG_FEE", "T_PREPAY", "T_LATE_CASCADE",
              "T_AUTO_RENEW", "T_CROSS_DEFAULT", "T_CONFESSION", "T_INSURANCE",
              "T_VENUE", "T_UCC"]

N_TRAP_DOCS = 20
N_CLEAN_DOCS = 3
UGLY_STYLES = ["two_column", "rotated_table", "tiny_footnotes", "two_column", "rotated_table"]


# ---------------------------------------------------------------------------
# Finance — the formulas golden.json is built from. compute.py must match.
# ---------------------------------------------------------------------------

PERIODS_PER_YEAR = {"monthly": 12, "biweekly": 26, "weekly": 52}


def n_periods(term_months: int, freq: str) -> int:
    return round(term_months * PERIODS_PER_YEAR[freq] / 12)


def scheduled_payment(balance: float, apr: float, term_months: int, freq: str,
                      balloon: float = 0.0) -> float:
    """Level payment that amortizes `balance` at nominal `apr`, leaving `balloon` due at the end."""
    k = PERIODS_PER_YEAR[freq]
    n = n_periods(term_months, freq)
    i = apr / k
    if i == 0:
        return (balance - balloon) / n
    annuity = i / (1 - (1 + i) ** -n)
    return (balance - balloon / (1 + i) ** n) * annuity


def payment_with_balloon_multiple(balance: float, apr: float, term_months: int, freq: str,
                                  multiple: float) -> tuple[float, float]:
    """Solve payment p such that balloon = multiple * p. Returns (payment, balloon)."""
    k = PERIODS_PER_YEAR[freq]
    n = n_periods(term_months, freq)
    i = apr / k
    annuity = i / (1 - (1 + i) ** -n)
    p = balance * annuity / (1 + multiple * annuity / (1 + i) ** n)
    return p, multiple * p


def effective_apr(net_proceeds: float, payment: float, n: int, freq: str, balloon: float) -> float:
    """Nominal annual rate whose periodic IRR zeroes the borrower's cash flows."""
    k = PERIODS_PER_YEAR[freq]

    def npv(r: float) -> float:
        pv = sum(payment / (1 + r) ** t for t in range(1, n + 1))
        pv += balloon / (1 + r) ** n
        return net_proceeds - pv

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:   # pv too small -> rate too high
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2 * k, 6)


# ---------------------------------------------------------------------------
# Spec
# ---------------------------------------------------------------------------


@dataclass
class FeeSpec:
    kind: str
    basis: str
    value: float
    financed: bool = False      # added to the amount financed vs paid at closing


@dataclass
class DocSpec:
    doc_id: str
    lender: str
    borrower: str
    borrower_state: str
    equipment: str
    price: float
    down_payment: float
    principal: float
    stated_apr: float
    term_months: int
    freq: str
    traps: list[str]
    style: int
    ugly: str | None = None
    fees: list[FeeSpec] = field(default_factory=list)
    balloon: float = 0.0
    payment: float = 0.0
    n: int = 0
    financed_balance: float = 0.0
    upfront_fees: float = 0.0
    net_proceeds: float = 0.0
    total_cost: float = 0.0
    effective_apr: float = 0.0
    section_seed: int = 0

    def fee(self, kind: str) -> FeeSpec | None:
        return next((f for f in self.fees if f.kind == kind), None)


LENDERS = ["Keystone Equipment Capital, LLC", "Three Rivers Commercial Finance Corp.",
           "Allegheny Asset Funding, Inc.", "Northgate Leasing Partners, L.P.",
           "Summit Trade Credit Company", "Monongahela Capital Group, LLC",
           "Ironbridge Equipment Finance, Inc.", "Harborline Business Funding, LLC"]
BORROWERS = ["Ridgeview Lawn and Landscape", "J. Alvarez Media Services", "Copper Kettle Catering",
             "Steel City Mobile Welding", "Bluebird Courier Company", "Oak Hollow Tree Care",
             "Northside Pressure Washing", "Millvale Custom Cabinetry", "Two Forks Food Truck",
             "Bright Path Photography", "Hilltop Snow and Ice Services", "Greenfield Hauling"]
STATES = ["Pennsylvania", "Ohio", "West Virginia", "Maryland", "New York", "Virginia"]
FAR_VENUES = [("Clark County", "Nevada"), ("Miami-Dade County", "Florida"),
              ("Salt Lake County", "Utah"), ("Maricopa County", "Arizona")]
EQUIPMENT = [
    ("one (1) commercial zero-turn mower, 60-inch deck", 7000, 12000),
    ("one (1) cargo van, high roof, with shelving package", 26000, 44000),
    ("one (1) professional camera and lens kit with lighting", 8000, 15000),
    ("commercial kitchen equipment, including range, hood, and walk-in cooler", 16000, 38000),
    ("one (1) compact track loader with bucket attachment", 32000, 58000),
    ("one (1) enclosed utility trailer, 16-foot, tandem axle", 6000, 11000),
    ("one (1) commercial espresso machine and grinder", 9000, 17000),
    ("one (1) mobile welding rig, trailer mounted", 5500, 10000),
    ("one (1) hot water pressure washing unit, trailer mounted", 7500, 13000),
]
FILLER_SECTIONS = ["Representations and Warranties", "Use and Maintenance of Equipment", "Taxes",
                   "Indemnification", "Assignment", "Inspection and Reporting", "Notices",
                   "Miscellaneous", "Delivery and Acceptance", "Risk of Loss",
                   "Further Assurances", "Counterparts and Electronic Signatures"]


def _roman(n: int) -> str:
    out = ""
    for v, sym in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out += sym
            n -= v
    return out


def money(x: float) -> str:
    return f"${x:,.2f}"


def pct(x: float) -> str:
    return f"{x * 100:.2f}%"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def assign_traps(rng: random.Random) -> list[list[str]]:
    """20 lists of 2-4 trap IDs; every trap type appears at least twice."""
    docs: list[list[str]] = [[] for _ in range(N_TRAP_DOCS)]
    pool = TRAP_TYPES * 2
    rng.shuffle(pool)
    for i, t in enumerate(pool):
        docs[i % N_TRAP_DOCS].append(t)
    for d in docs:
        target = rng.choice([2, 3, 3, 4])
        while len(d) < target:
            t = rng.choice(TRAP_TYPES)
            if t not in d:
                d.append(t)
    for d in docs:
        rng.shuffle(d)
    rng.shuffle(docs)
    return docs


def plan_doc(rng: random.Random, doc_id: str, traps: list[str], ugly: str | None) -> DocSpec:
    equipment, lo, hi = rng.choice(EQUIPMENT)
    price = float(rng.randrange(lo, hi, 50))
    down_frac = rng.choice([0, 0, 0.05, 0.10, 0.15])
    down = round(price * down_frac, 2)
    principal = round(price - down, 2)
    spec = DocSpec(
        doc_id=doc_id, lender=rng.choice(LENDERS), borrower=rng.choice(BORROWERS),
        borrower_state=rng.choice(STATES), equipment=equipment, price=price,
        down_payment=down, principal=principal,
        stated_apr=round(rng.uniform(0.059, 0.149), 3),
        term_months=rng.choice([24, 36, 36, 48, 48, 60]),
        freq=rng.choice(["monthly"] * 4 + ["biweekly"]),
        traps=traps, style=rng.randrange(3), ugly=ugly, section_seed=rng.randrange(10**6),
    )

    # Fees ------------------------------------------------------------------
    if rng.random() < 0.7:
        spec.fees.append(FeeSpec("doc", "flat", float(rng.choice([75, 95, 125, 150, 195, 250]))))
    if "T_ORIG_FEE" in traps:
        spec.fees.append(FeeSpec("origination", "percent_of_principal",
                                 round(rng.uniform(0.03, 0.06), 3), financed=True))
    elif "T_APR_GAP" in traps:
        spec.fees.append(FeeSpec("origination", "percent_of_principal",
                                 round(rng.uniform(0.04, 0.07), 3), financed=False))
    if "T_LATE_CASCADE" in traps:
        spec.fees.append(FeeSpec("late", "percent_of_payment", rng.choice([0.05, 0.08, 0.10])))
    else:
        spec.fees.append(FeeSpec("late", "flat", float(rng.choice([25, 35, 50]))))
    if "T_PREPAY" in traps:
        if rng.random() < 0.6:
            spec.fees.append(FeeSpec("prepayment", "percent_of_principal", rng.choice([0.02, 0.03, 0.05])))
        else:
            spec.fees.append(FeeSpec("prepayment", "flat", float(rng.choice([500, 750, 1000]))))

    compute_spec(spec, rng)

    # T_APR_GAP promises a 4-9 point gap; raise the origination fee until we get there.
    if "T_APR_GAP" in traps:
        orig = spec.fee("origination")
        assert orig is not None
        while spec.effective_apr - spec.stated_apr < 0.04 and orig.value < 0.15:
            orig.value = round(orig.value + 0.005, 3)
            compute_spec(spec, rng)
    return spec


def compute_spec(spec: DocSpec, rng: random.Random) -> None:
    orig = spec.fee("origination")
    financed_fee = round(spec.principal * orig.value, 2) if orig and orig.financed else 0.0
    spec.financed_balance = round(spec.principal + financed_fee, 2)
    spec.n = n_periods(spec.term_months, spec.freq)

    if "T_BALLOON" in spec.traps:
        if spec.balloon == 0.0:
            spec._balloon_multiple = rng.uniform(8, 15)  # type: ignore[attr-defined]
        p, b = payment_with_balloon_multiple(spec.financed_balance, spec.stated_apr, spec.term_months,
                                             spec.freq, spec._balloon_multiple)  # type: ignore[attr-defined]
        spec.payment, spec.balloon = round(p, 2), round(b, 2)
    else:
        spec.payment = round(scheduled_payment(spec.financed_balance, spec.stated_apr,
                                               spec.term_months, spec.freq), 2)

    upfront = 0.0
    for f in spec.fees:
        if f.kind in ("doc", "origination") and not f.financed:
            upfront += f.value if f.basis == "flat" else round(spec.principal * f.value, 2)
    spec.upfront_fees = round(upfront, 2)
    spec.net_proceeds = round(spec.principal - spec.upfront_fees, 2)
    spec.total_cost = round(spec.n * spec.payment + spec.balloon + spec.upfront_fees, 2)
    spec.effective_apr = effective_apr(spec.net_proceeds, spec.payment, spec.n, spec.freq, spec.balloon)


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

FREQ_WORD = {"monthly": "monthly", "biweekly": "biweekly", "weekly": "weekly"}
FREQ_ADJ = {"monthly": "consecutive monthly", "biweekly": "consecutive biweekly (every fourteen days)",
            "weekly": "consecutive weekly"}

FILLER_PARAS = [
    "Borrower represents and warrants to Lender that Borrower is duly organized, validly existing, and in good standing under the laws of the state of its organization, and that Borrower has full power and authority to execute, deliver, and perform its obligations under this Agreement. The execution and delivery of this Agreement have been duly authorized by all necessary action on the part of Borrower and do not violate any provision of Borrower's organizational documents or any agreement to which Borrower is a party.",
    "Borrower shall use the Equipment solely in the ordinary course of Borrower's business, in a careful and proper manner, and in compliance with all applicable laws, ordinances, regulations, and manufacturer's instructions. Borrower shall not use or permit the Equipment to be used for any unlawful purpose or in any manner that would void any warranty or insurance coverage applicable to the Equipment.",
    "Borrower shall, at Borrower's sole expense, keep the Equipment in good repair, condition, and working order, and shall furnish all parts, mechanisms, and devices required to keep the Equipment in such condition. All replacement parts and additions shall become the property of Lender to the extent of Lender's interest and shall be subject to this Agreement.",
    "Borrower shall pay when due all taxes, assessments, fees, and other governmental charges, however designated, that are levied or assessed upon the Equipment, its use, or this Agreement, including sales, use, personal property, and excise taxes, excluding only taxes measured by Lender's net income. Borrower shall file all required returns and reports with respect to the Equipment.",
    "Borrower shall indemnify, defend, and hold harmless Lender and its officers, directors, employees, agents, successors, and assigns from and against any and all claims, losses, liabilities, damages, costs, and expenses, including reasonable attorneys' fees, arising out of or relating to the selection, delivery, possession, use, operation, condition, or return of the Equipment, except to the extent caused by the gross negligence or willful misconduct of Lender.",
    "Borrower shall not assign, transfer, pledge, or otherwise dispose of this Agreement or any interest herein, or sublet or lend the Equipment or permit it to be used by anyone other than Borrower's employees, without the prior written consent of Lender, which consent may be withheld in Lender's sole discretion. Any purported assignment in violation of this Section shall be void.",
    "Lender may assign, sell, or otherwise transfer this Agreement and its rights hereunder, in whole or in part, without notice to or consent of Borrower. Borrower agrees that any assignee of Lender shall have all of the rights but none of the obligations of Lender under this Agreement, and Borrower shall not assert against any assignee any defense, counterclaim, or setoff that Borrower may have against Lender.",
    "Lender and its designees shall have the right, at any reasonable time and upon reasonable notice, to enter upon the premises where the Equipment is located to inspect the Equipment and Borrower's records relating thereto. Borrower shall furnish to Lender, within one hundred twenty (120) days after the close of each fiscal year, financial statements of Borrower prepared in accordance with generally accepted accounting principles.",
    "All notices, requests, demands, and other communications required or permitted under this Agreement shall be in writing and shall be deemed to have been duly given when delivered personally, when sent by nationally recognized overnight courier, or three (3) business days after being mailed by certified mail, return receipt requested, postage prepaid, to the addresses set forth on the signature page hereof or to such other address as a party may designate by notice.",
    "This Agreement constitutes the entire agreement between the parties with respect to the subject matter hereof and supersedes all prior and contemporaneous agreements, representations, and understandings, whether written or oral. No amendment, modification, or waiver of any provision of this Agreement shall be effective unless in writing and signed by the party against whom enforcement is sought.",
    "If any provision of this Agreement is held to be invalid, illegal, or unenforceable in any respect, such invalidity, illegality, or unenforceability shall not affect any other provision of this Agreement, and this Agreement shall be construed as if such provision had never been contained herein. The headings in this Agreement are for convenience of reference only and shall not affect its interpretation.",
    "Borrower acknowledges that Borrower has selected the Equipment and the supplier thereof based upon Borrower's own judgment and expressly disclaims any reliance upon any statements or representations made by Lender. Lender makes no warranty, express or implied, as to the merchantability, fitness for a particular purpose, design, condition, capacity, quality, or durability of the Equipment.",
    "Upon delivery of the Equipment, Borrower shall inspect the Equipment and, unless Borrower gives written notice to Lender within five (5) business days specifying any defect or other objection, Borrower shall be conclusively deemed to have accepted the Equipment as satisfactory in all respects. Borrower shall execute and deliver to Lender a delivery and acceptance certificate in the form provided by Lender.",
    "Borrower shall bear the entire risk of loss, theft, damage, or destruction of the Equipment from any cause whatsoever from the date of delivery until all obligations of Borrower under this Agreement have been fully satisfied. No such loss, theft, damage, or destruction shall relieve Borrower of any obligation under this Agreement, including the obligation to make payments when due.",
    "In the event of any loss, theft, damage, or destruction of the Equipment, Borrower shall promptly notify Lender in writing and shall, at Lender's option, either repair the Equipment to good condition and working order, replace the Equipment with like equipment of equal or greater value acceptable to Lender, or pay to Lender the then outstanding balance of all amounts due under this Agreement.",
    "Borrower agrees to execute and deliver such further instruments and documents, and to take such further actions, as Lender may reasonably request in order to carry out the intent and purposes of this Agreement and to perfect and protect the interests of Lender in the Equipment, including without limitation financing statements, landlord waivers, and certificates of title.",
    "This Agreement may be executed in any number of counterparts, each of which shall be deemed an original and all of which together shall constitute one and the same instrument. Delivery of an executed counterpart by facsimile or electronic transmission shall be as effective as delivery of a manually executed counterpart. Only the counterpart marked Original by Lender shall constitute chattel paper.",
    "Time is of the essence with respect to the performance of Borrower's obligations under this Agreement. No delay or omission by Lender in exercising any right or remedy shall impair such right or remedy or be construed as a waiver thereof, and a waiver on one occasion shall not be construed as a waiver of any right or remedy on any future occasion.",
    "Borrower shall keep the Equipment free and clear of all liens, charges, and encumbrances other than those in favor of Lender, and shall not permit the Equipment to be removed from the location specified in Schedule A without the prior written consent of Lender. Borrower shall affix and maintain on the Equipment any labels, plates, or markings furnished by Lender identifying Lender's interest.",
    "Borrower shall furnish to Lender, promptly upon request, such information concerning the financial condition, business, and operations of Borrower and any guarantor as Lender may from time to time reasonably request, and Borrower authorizes Lender to obtain credit reports and other information concerning Borrower from third parties at any time during the term of this Agreement.",
    "The obligations of Borrower under this Agreement are absolute and unconditional and shall not be subject to any abatement, reduction, setoff, defense, counterclaim, or recoupment for any reason whatsoever, including any defect in the Equipment, any failure of the supplier to deliver or perform, or any interruption or cessation in the use or possession of the Equipment by Borrower for any reason.",
    "Borrower shall reimburse Lender on demand for all costs and expenses, including reasonable attorneys' fees and court costs, incurred by Lender in enforcing any of the terms of this Agreement, in protecting or realizing upon the Equipment, or in defending any claim brought by Borrower against Lender that is determined adversely to Borrower.",
    "Any provision of this Agreement that by its nature is intended to survive the expiration or termination of this Agreement, including without limitation the indemnification, payment, and confidentiality obligations of Borrower, shall survive such expiration or termination and remain in full force and effect.",
    "Borrower shall not, without the prior written consent of Lender, make any alterations, additions, or improvements to the Equipment that would impair its value or that cannot be removed without damage to the Equipment. Any alteration, addition, or improvement made to the Equipment shall be at Borrower's sole cost and shall become the property of Lender to the extent of Lender's interest.",
]


class Doc:
    """Accumulates flowables plus the exact strings that must appear in the PDF."""

    def __init__(self, spec: DocSpec, rng: random.Random):
        self.spec = spec
        self.rng = rng
        self.trap_spans: dict[str, str] = {}
        self._sections: list[tuple[str, list]] = []
        self.footnotes: list[str] = []
        self.style = self._styles(spec.style, spec.ugly)

    # ---- styles ------------------------------------------------------------

    @staticmethod
    def _styles(variant: int, ugly: str | None) -> dict:
        fonts = [("Helvetica", "Helvetica-Bold"), ("Times-Roman", "Times-Bold"), ("Courier", "Courier-Bold")][variant]
        size = [10, 10.5, 9.5][variant]
        lead = [14, 14.5, 13][variant]
        fn_size = 5 if ugly == "tiny_footnotes" else 7
        s = {
            "body": ParagraphStyle("b", fontName=fonts[0], fontSize=size, leading=lead,
                                   alignment=TA_JUSTIFY, spaceAfter=6),
            "h": ParagraphStyle("h", fontName=fonts[1], fontSize=size + 1.5, leading=lead + 2,
                                spaceBefore=10, spaceAfter=5),
            "title": ParagraphStyle("t", fontName=fonts[1], fontSize=15, leading=19,
                                    alignment=TA_CENTER, spaceAfter=14),
            "fn": ParagraphStyle("fn", fontName=fonts[0], fontSize=fn_size, leading=fn_size + 1.5,
                                 spaceBefore=2),
            "cell": ParagraphStyle("c", fontName=fonts[0], fontSize=size - 1, leading=lead - 2),
            "fonts": fonts,
            "numbering": variant,
        }
        return s

    def heading_label(self, n: int, title: str) -> str:
        v = self.style["numbering"]
        if v == 0:
            return f"{n}. {title}"
        if v == 1:
            return f"Section {n}. {title.upper()}"
        return f"ARTICLE {_roman(n)} - {title}"

    def add(self, title: str, paras: list) -> None:
        self._sections.append((title, paras))

    # ---- content -----------------------------------------------------------

    def build(self) -> list[tuple[str, list]]:
        s, r = self.spec, self.rng
        self.add("Parties and Recitals", [
            f"This Equipment Financing Agreement (this \"Agreement\") is entered into by and between {s.lender} (\"Lender\") and {s.borrower}, a business organized under the laws of the State of {s.borrower_state} (\"Borrower\"). Borrower desires to finance the acquisition of certain equipment described in Schedule A, and Lender is willing to provide such financing on the terms and subject to the conditions set forth herein.",
            "NOW, THEREFORE, in consideration of the mutual covenants contained herein and other good and valuable consideration, the receipt and sufficiency of which are hereby acknowledged, the parties agree as follows.",
        ])
        self.add("Definitions", [
            "\"Equipment\" means the property described in Schedule A, together with all attachments, accessories, replacements, substitutions, and additions thereto. \"Amount Financed\" means the principal amount stated in the Payment Terms section, together with any Fees expressly stated to be financed. \"Payment Date\" means each date on which an installment is due. \"Fees\" means the amounts set forth in the Schedule of Fees.",
            "\"Event of Default\" has the meaning given in the Default section of this Agreement. \"Obligations\" means all amounts payable by Borrower under this Agreement, whether for principal, interest, Fees, or otherwise. \"Business Day\" means any day other than a Saturday, Sunday, or day on which commercial banks in the State of the Lender's principal office are authorized to close.",
        ])

        middle: list[tuple[str, list]] = []
        middle.append(("Payment Terms", self._payment_terms()))
        middle.append(("Fees and Charges", self._fees()))
        middle.append(("Prepayment", self._prepayment()))
        middle.append(("Default", self._default()))
        middle.append(("Remedies", self._remedies()))
        middle.append(("Insurance", self._insurance()))
        middle.append(("Security Interest", self._security()))
        middle.append(("Term and Renewal", self._term()))
        fillers = r.sample(FILLER_SECTIONS, k=r.randint(9, len(FILLER_SECTIONS)))
        paras = FILLER_PARAS[:]
        r.shuffle(paras)
        extra = FILLER_PARAS[:]
        r.shuffle(extra)
        paras = extra + paras           # two passes through the pool; long docs reuse boilerplate
        for title in fillers:
            n = r.choice([2, 3, 3, 4])
            middle.append((title, [paras.pop() for _ in range(n)]))
        middle.append(("Governing Law and Disputes", self._venue()))

        # Keep payment terms near the top; shuffle everything else.
        head, rest = middle[:1], middle[1:]
        r.shuffle(rest)
        for title, paras in head + rest:
            self.add(title, paras)
        return self._sections

    def _payment_terms(self) -> list:
        s = self.spec
        out = []
        out.append(f"Amount Financed. The principal amount financed under this Agreement is {money(s.principal)}, representing the purchase price of the Equipment of {money(s.price)} less Borrower's down payment of {money(s.down_payment)}"
                   + (f", plus the Origination Fee described in the Fees and Charges section, which is added to the Amount Financed." if s.fee("origination") and s.fee("origination").financed else "."))
        out.append(f"Rate. Interest shall accrue on the outstanding Amount Financed at a fixed Annual Percentage Rate (APR) of {pct(s.stated_apr)}.")
        if "T_APR_GAP" in s.traps:
            v = self.rng.choice([
                f"The Annual Percentage Rate of {pct(s.stated_apr)} stated in this Agreement reflects the contract interest rate only and does not include the Origination Fee or any other Fees, which are payable in addition to interest.",
                f"Borrower acknowledges that the stated rate of {pct(s.stated_apr)} is exclusive of the Origination Fee and the other Fees set forth in the Schedule of Fees, and that the total cost of credit to Borrower is accordingly greater than the stated rate would indicate.",
            ])
            self.trap_spans["T_APR_GAP"] = v
            out.append(v)
        marker = "<super>1</super>" if "T_BALLOON" in s.traps else ""
        out.append(f"Installments. Borrower shall repay the Amount Financed together with interest in {s.n} {FREQ_ADJ[s.freq]} installments of {money(s.payment)} each{marker}, the first installment being due thirty (30) days after the date of this Agreement and each subsequent installment being due on the same day of each period thereafter.")
        if "T_BALLOON" in s.traps:
            v = self.rng.choice([
                f"A final balloon payment of {money(s.balloon)}, in addition to the regular installment, shall be due and payable together with the final installment.",
                f"In addition to the final regular installment, Borrower shall pay a balloon payment in the amount of {money(s.balloon)} on the final Payment Date, representing the remaining balance of the Amount Financed.",
                f"The installments described above do not fully amortize the Amount Financed; a balloon payment of {money(s.balloon)} shall be due on the final Payment Date.",
            ])
            self.trap_spans["T_BALLOON"] = v
            self.footnotes.append(f"<super>1</super> {v}")
        out.append("All payments shall be made in lawful money of the United States by automatic debit from the account designated by Borrower, without setoff or deduction. Any payment received after 5:00 p.m. Eastern time shall be credited on the next Business Day.")
        return out

    def _fees(self) -> list:
        s = self.spec
        out = [f"Borrower shall pay the Fees set forth in the Schedule of Fees attached to this Agreement as Schedule B. Fees not stated to be financed are due at closing."]
        orig = s.fee("origination")
        if orig:
            amt = money(round(s.principal * orig.value, 2))
            if orig.financed:
                v = self.rng.choice([
                    f"An Origination Fee equal to {pct(orig.value)} of the principal amount ({amt}) shall be charged and shall be added to and financed as part of the Amount Financed, and interest shall accrue on such fee at the rate stated herein.",
                    f"Borrower shall pay an Origination Fee of {pct(orig.value)} of the principal amount, which the parties agree shall be capitalized into the Amount Financed rather than paid at closing, such that the total Amount Financed is {money(s.financed_balance)}.",
                ])
                self.trap_spans["T_ORIG_FEE"] = v
            else:
                v = f"An Origination Fee equal to {pct(orig.value)} of the principal amount ({amt}) is due and payable at closing and shall be deducted from the proceeds disbursed to or on behalf of Borrower."
            out.append(v)
        doc = s.fee("doc")
        if doc:
            out.append(f"A Documentation Fee of {money(doc.value)} is due at closing to cover the preparation and filing of this Agreement and related documents.")
        return out

    def _prepayment(self) -> list:
        s = self.spec
        pp = s.fee("prepayment")
        if pp:
            amt = f"{pct(pp.value)} of the then outstanding principal balance" if pp.basis != "flat" else money(pp.value)
            v = self.rng.choice([
                f"Borrower may prepay the Obligations in full, but not in part, upon thirty (30) days' prior written notice, provided that Borrower shall pay, together with such prepayment, a Prepayment Premium equal to {amt}.",
                f"In the event Borrower elects to pay the Amount Financed prior to the final Payment Date for any reason, including refinancing or sale of the Equipment, Borrower shall pay a Prepayment Premium of {amt} in addition to all accrued interest and other amounts then due.",
            ])
            self.trap_spans["T_PREPAY"] = v
            return [v]
        return ["Borrower may prepay the Obligations in whole or in part at any time without premium or penalty. Any partial prepayment shall be applied to installments in inverse order of maturity and shall not relieve Borrower of the obligation to make the next scheduled installment when due."]

    def _default(self) -> list:
        s = self.spec
        out = ["Each of the following shall constitute an Event of Default: (a) Borrower fails to pay any installment or other amount within ten (10) days after the date due; (b) Borrower fails to perform any other covenant in this Agreement and such failure continues for thirty (30) days after written notice; (c) Borrower becomes insolvent, makes an assignment for the benefit of creditors, or becomes the subject of any bankruptcy or insolvency proceeding; or (d) any representation made by Borrower proves to have been false in any material respect when made."]
        if "T_CROSS_DEFAULT" in s.traps:
            v = self.rng.choice([
                "It shall also constitute an Event of Default hereunder if Borrower or any guarantor defaults under any other loan, lease, credit facility, or obligation owed to Lender, any affiliate of Lender, or any other creditor, regardless of whether such other creditor has declared a default or accelerated such obligation.",
                "Any default by Borrower under any other agreement for borrowed money or the lease of equipment, with Lender or with any third party, shall at Lender's option constitute an Event of Default under this Agreement, whether or not such default is cured or waived by the other creditor.",
            ])
            self.trap_spans["T_CROSS_DEFAULT"] = v
            out.append(v)
        late = s.fee("late")
        assert late is not None
        if "T_LATE_CASCADE" in s.traps:
            v = self.rng.choice([
                f"If any installment is not received within ten (10) days of its due date, Borrower shall pay a late charge equal to {pct(late.value)} of the overdue installment, and the interest rate applicable to the entire outstanding balance shall thereafter be increased by six (6) percentage points for the remainder of the term.",
                f"Borrower shall pay a late charge of {pct(late.value)} of any installment not paid when due, which late charge shall be added to the outstanding balance and shall itself bear interest, and upon any second late payment the rate applicable to all Obligations shall increase to the stated rate plus six (6) percentage points.",
            ])
            self.trap_spans["T_LATE_CASCADE"] = v
            out.append(v)
        else:
            out.append(f"If any installment is not received within ten (10) days of its due date, Borrower shall pay a late charge of {money(late.value)} to compensate Lender for its administrative costs. Such late charge shall not accrue interest.")
        return out

    def _remedies(self) -> list:
        s = self.spec
        out = ["Upon the occurrence of an Event of Default, Lender may, at its option and without notice or demand, exercise any one or more of the following remedies: declare all Obligations immediately due and payable; take possession of the Equipment wherever located; sell, lease, or otherwise dispose of the Equipment and apply the net proceeds to the Obligations; and exercise any other right or remedy available at law or in equity."]
        if "T_CONFESSION" in s.traps:
            v = self.rng.choice([
                "BORROWER HEREBY IRREVOCABLY AUTHORIZES AND EMPOWERS ANY ATTORNEY OF ANY COURT OF RECORD TO APPEAR FOR BORROWER AND CONFESS JUDGMENT AGAINST BORROWER, WITHOUT PRIOR NOTICE OR HEARING, FOR ALL OBLIGATIONS THEN DUE TOGETHER WITH COSTS AND ATTORNEYS' FEES OF FIFTEEN PERCENT (15%) OF THE AMOUNT DUE, AND BORROWER WAIVES ANY RIGHT TO NOTICE OR HEARING PRIOR TO THE ENTRY OF SUCH JUDGMENT.",
                "The undersigned principal of Borrower hereby personally, absolutely, and unconditionally guarantees the full and punctual payment of all Obligations, and agrees that Lender may proceed directly against the guarantor and the guarantor's personal assets, including any residence, without first proceeding against Borrower or the Equipment.",
            ])
            self.trap_spans["T_CONFESSION"] = v
            out.append(v)
        out.append("Borrower shall be liable for any deficiency remaining after disposition of the Equipment. The remedies of Lender are cumulative and may be exercised concurrently or separately.")
        return out

    def _insurance(self) -> list:
        s = self.spec
        if "T_INSURANCE" in s.traps:
            v = self.rng.choice([
                "Borrower shall obtain physical damage and liability insurance on the Equipment through the insurance program administered by Lender or its designated agent, the premiums for which shall be determined by Lender and added to each installment, and Borrower shall not substitute coverage from any other carrier without Lender's written consent.",
                "Lender shall procure insurance covering the Equipment on Borrower's behalf and shall bill Borrower for the cost of such coverage, together with an administrative charge, at rates established from time to time by Lender in its sole discretion. Borrower waives any right to select its own insurer.",
            ])
            self.trap_spans["T_INSURANCE"] = v
            return [v, "Any insurance proceeds received by Lender shall be applied, at Lender's option, to the repair or replacement of the Equipment or to the Obligations."]
        return ["Borrower shall, at Borrower's expense, maintain physical damage insurance covering the Equipment for its full replacement value and commercial general liability insurance in amounts reasonably acceptable to Lender, with insurers of Borrower's choosing rated A- or better. Lender shall be named as loss payee and additional insured, and Borrower shall deliver certificates of insurance upon request.",
                "Any insurance proceeds received by Lender shall be applied, at Lender's option, to the repair or replacement of the Equipment or to the Obligations."]

    def _security(self) -> list:
        s = self.spec
        if "T_UCC" in s.traps:
            v = self.rng.choice([
                "As security for the Obligations, Borrower hereby grants to Lender a continuing security interest in the Equipment and in all of Borrower's other assets, whether now owned or hereafter acquired, including all accounts, inventory, equipment, general intangibles, deposit accounts, and proceeds thereof, and authorizes Lender to file a UCC-1 financing statement describing the collateral as all assets of Borrower.",
                "Borrower grants Lender a first-priority security interest in all personal property of Borrower of every kind and description, wherever located and whether now existing or hereafter arising, and not merely the Equipment, and Borrower authorizes Lender to file financing statements in any jurisdiction covering all assets of the debtor.",
            ])
            self.trap_spans["T_UCC"] = v
            return [v]
        return ["As security for the Obligations, Borrower hereby grants to Lender a security interest in the Equipment and all proceeds thereof. Borrower authorizes Lender to file a financing statement describing the Equipment in any jurisdiction Lender deems appropriate. Lender's security interest is limited to the Equipment described in Schedule A."]

    def _term(self) -> list:
        s = self.spec
        if "T_AUTO_RENEW" in s.traps:
            v = self.rng.choice([
                f"Unless Borrower delivers written notice of termination to Lender not less than ninety (90) nor more than one hundred twenty (120) days prior to the final Payment Date, this Agreement shall automatically renew for an additional term of {s.term_months} months on the same terms, and installments shall continue to be due in the same amount for the renewal term.",
                f"This Agreement shall renew automatically for successive terms of {s.term_months} months each unless either party gives written notice of non-renewal within the thirty (30) day window beginning one hundred fifty (150) days before the end of the then current term; installments during any renewal term shall equal the installments due during the initial term.",
            ])
            self.trap_spans["T_AUTO_RENEW"] = v
            return [v]
        return ["This Agreement shall terminate upon Borrower's payment in full of all Obligations, whereupon Lender shall release its security interest in the Equipment and, upon Borrower's request, file a termination statement."]

    def _venue(self) -> list:
        s = self.spec
        if "T_VENUE" in s.traps:
            county, state = self.rng.choice(FAR_VENUES)
            v = self.rng.choice([
                f"This Agreement shall be governed by the laws of the State of {state}. Borrower irrevocably submits to the exclusive jurisdiction of the state and federal courts located in {county}, {state}, waives any objection to venue in such courts, and agrees that any dispute shall be resolved by binding arbitration in {county}, {state}, at Borrower's expense.",
                f"Any claim or controversy arising out of this Agreement shall be resolved exclusively by binding arbitration administered in {county}, {state}, before a single arbitrator selected by Lender, and Borrower waives any right to a jury trial or to participate in any class action. The laws of the State of {state} shall govern.",
            ])
            self.trap_spans["T_VENUE"] = v
            return [v]
        return [f"This Agreement shall be governed by and construed in accordance with the laws of the State of {s.borrower_state}, without regard to its conflict of laws principles. The parties consent to the jurisdiction of the state and federal courts located in the State of {s.borrower_state} for any action arising out of this Agreement."]

    def fee_table_rows(self) -> list[list[str]]:
        s = self.spec
        rows = [["Fee", "Amount", "When Payable"]]
        for f in s.fees:
            if f.kind == "origination":
                amt = f"{pct(f.value)} of principal ({money(round(s.principal * f.value, 2))})"
                when = "Financed into Amount Financed" if f.financed else "At closing"
            elif f.kind == "doc":
                amt, when = money(f.value), "At closing"
            elif f.kind == "late":
                amt = money(f.value) if f.basis == "flat" else f"{pct(f.value)} of overdue installment"
                when = "Upon late payment"
            elif f.kind == "prepayment":
                amt = money(f.value) if f.basis == "flat" else f"{pct(f.value)} of outstanding principal"
                when = "Upon prepayment"
            else:
                amt, when = money(f.value), "As incurred"
            label = {"origination": "Origination Fee", "doc": "Documentation Fee", "late": "Late Charge",
                     "prepayment": "Prepayment Premium"}.get(f.kind, f.kind.title())
            rows.append([label, amt, when])
        return rows


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class Rotated(Flowable):
    """Draw a flowable rotated 90 degrees. Used for the 'ugly' rotated fee table."""

    def __init__(self, inner: Flowable):
        super().__init__()
        self.inner = inner

    def wrap(self, aw, ah):
        w, h = self.inner.wrap(ah, aw)
        self._iw, self._ih = w, h
        return h, w

    def draw(self):
        c = self.canv
        c.saveState()
        c.rotate(90)
        c.translate(0, -self._ih)
        self.inner.drawOn(c, 0, 0)
        c.restoreState()


def _page_template(doc: BaseDocTemplate, two_column: bool) -> PageTemplate:
    w, h = letter
    m = doc.leftMargin
    if two_column:
        gutter = 0.3 * inch
        cw = (w - 2 * m - gutter) / 2
        frames = [Frame(m, m, cw, h - 2 * m, id="L"), Frame(m + cw + gutter, m, cw, h - 2 * m, id="R")]
    else:
        frames = [Frame(m, m, w - 2 * m, h - 2 * m, id="F")]

    def on_page(canv, d):
        canv.saveState()
        canv.setFont("Helvetica", 7)
        canv.drawRightString(w - m, m / 2, f"Page {d.page}")
        canv.drawString(m, m / 2, "CONFIDENTIAL - SYNTHETIC DOCUMENT FOR EVALUATION USE ONLY")
        canv.restoreState()

    return PageTemplate(id="main", frames=frames, onPage=on_page)


def render(spec: DocSpec, path: Path, rng: random.Random, *, doc_type: type[Doc] = Doc) -> dict[str, str]:
    """Render one spec, optionally with a Doc subclass used by focused evals."""
    d = doc_type(spec, rng)
    sections = d.build()
    st = d.style
    two_col = spec.ugly == "two_column"
    margin = [0.9 * inch, 1.0 * inch, 0.8 * inch][spec.style]

    doc = BaseDocTemplate(str(path), pagesize=letter, leftMargin=margin, rightMargin=margin,
                          topMargin=margin, bottomMargin=margin, title="Equipment Financing Agreement",
                          author=spec.lender)
    doc.addPageTemplates([_page_template(doc, two_col)])

    story: list = [Paragraph("EQUIPMENT FINANCING AGREEMENT", st["title"]),
                   Paragraph(f"Agreement No. EFA-{spec.doc_id[-3:]}-{rng.randrange(1000, 9999)}", st["cell"]),
                   Spacer(1, 10)]
    for n, (title, paras) in enumerate(sections, start=1):
        story.append(Paragraph(d.heading_label(n, title), st["h"]))
        for p in paras:
            story.append(Paragraph(p, st["body"]))
        if title == "Payment Terms" and d.footnotes:
            story.append(Spacer(1, 4))
            story.append(Paragraph("_" * 30, st["fn"]))
            for fn in d.footnotes:
                story.append(Paragraph(fn, st["fn"]))
            story.append(Spacer(1, 6))

    # Signature block
    story.append(Spacer(1, 18))
    story.append(Paragraph("IN WITNESS WHEREOF, the parties have executed this Agreement as of the date first written below.", st["body"]))
    sig = Table([[f"LENDER: {spec.lender}", f"BORROWER: {spec.borrower}"],
                 ["By: ______________________________", "By: ______________________________"],
                 ["Name:", "Name:"], ["Title:", "Title:"], ["Date:", "Date:"]],
                colWidths=[doc.width / 2 - 6] * 2 if not two_col else [doc.width / 4 - 6] * 2)
    sig.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), st["fonts"][0], 9), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story.append(sig)

    # Schedule A — equipment
    story.append(PageBreak())
    story.append(Paragraph("SCHEDULE A - EQUIPMENT", st["h"]))
    story.append(Paragraph(f"Description: {spec.equipment}.", st["body"]))
    story.append(Paragraph(f"Purchase price: {money(spec.price)}. Down payment: {money(spec.down_payment)}. Location: Borrower's principal place of business in the State of {spec.borrower_state}.", st["body"]))

    # Schedule B — fees
    story.append(Spacer(1, 14))
    story.append(Paragraph("SCHEDULE B - SCHEDULE OF FEES", st["h"]))
    rows = [[Paragraph(c, st["cell"]) for c in r] for r in d.fee_table_rows()]
    tw = (doc.width if not two_col else doc.width / 2 - 10)
    ft = Table(rows, colWidths=[tw * 0.3, tw * 0.4, tw * 0.3])
    ft.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                            ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    if spec.ugly == "rotated_table":
        story.append(PageBreak())
        story.append(Paragraph("SCHEDULE B - SCHEDULE OF FEES (continued)", st["h"]))
        story.append(Rotated(ft))
    else:
        story.append(ft)

    # Schedule C — payment schedule (omitted for balloon docs so the balloon is stated once)
    if "T_BALLOON" not in spec.traps and rng.random() < 0.6:
        story.append(PageBreak())
        story.append(Paragraph("SCHEDULE C - PAYMENT SCHEDULE", st["h"]))
        prow = [["No.", "Installment"]] + [[str(i), money(spec.payment)] for i in range(1, spec.n + 1)]
        # split into 3 column groups for compactness
        per = -(-len(prow[1:]) // 3)
        cols = [prow[1:][i:i + per] for i in range(0, len(prow[1:]), per)]
        merged = []
        for i in range(per):
            row = []
            for c in cols:
                row += c[i] if i < len(c) else ["", ""]
            merged.append(row)
        pt = Table([["No.", "Installment"] * len(cols)] + merged)
        pt.setStyle(TableStyle([("FONT", (0, 0), (-1, -1), st["fonts"][0], 8),
                                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey)]))
        story.append(pt)

    doc.build(story)
    return d.trap_spans


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def strip_markup(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


def golden_entry(spec: DocSpec, spans: dict[str, str], pdf_rel: str) -> dict:
    return {
        "doc_id": spec.doc_id,
        "pdf": pdf_rel,
        "ugly": spec.ugly,
        "terms": {
            "principal": spec.principal,
            "stated_apr": spec.stated_apr,
            "term_months": spec.term_months,
            "payment_amount": spec.payment,
            "payment_frequency": spec.freq,
            "balloon_amount": spec.balloon if spec.balloon else None,
            "fees": [{"kind": f.kind, "basis": f.basis, "value": f.value, "financed": f.financed}
                     for f in spec.fees],
        },
        "computed": {
            "amount_financed": spec.financed_balance,
            "n_payments": spec.n,
            "upfront_fees": spec.upfront_fees,
            "total_cost": spec.total_cost,
            "effective_apr": spec.effective_apr,
        },
        "traps": list(spec.traps),
        "trap_spans": {k: strip_markup(v) for k, v in spans.items()},
    }


def generate(out_dir: Path = DEFAULT_OUT, golden_path: Path = DEFAULT_GOLDEN, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    trap_lists = assign_traps(rng)
    ugly_slots = rng.sample(range(N_TRAP_DOCS), k=len(UGLY_STYLES))
    ugly_for = dict(zip(ugly_slots, UGLY_STYLES))

    specs: list[DocSpec] = []
    for i, traps in enumerate(trap_lists):
        specs.append(plan_doc(rng, "", traps, ugly_for.get(i)))
    for _ in range(N_CLEAN_DOCS):
        specs.append(plan_doc(rng, "", [], None))
    rng.shuffle(specs)

    golden = []
    for i, spec in enumerate(specs, start=1):
        spec.doc_id = f"eq_{i:03d}"
        pdf = out_dir / f"{spec.doc_id}.pdf"
        spans = render(spec, pdf, random.Random(spec.section_seed))
        assert set(spans) == set(spec.traps), (spec.doc_id, spans.keys(), spec.traps)
        try:
            rel = str(pdf.relative_to(REPO_ROOT))
        except ValueError:
            rel = str(pdf)
        golden.append(golden_entry(spec, spans, rel))

    golden_path.parent.mkdir(parents=True, exist_ok=True)
    golden_path.write_text(json.dumps(golden, indent=2) + "\n")
    return golden


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    golden = generate(a.out, a.golden, a.seed)
    n_traps = sum(len(g["traps"]) for g in golden)
    clean = sum(1 for g in golden if not g["traps"])
    print(f"wrote {len(golden)} PDFs to {a.out} ({clean} clean, {n_traps} traps planted); golden -> {a.golden}")


if __name__ == "__main__":
    main()
