"""Every number the user sees comes from here. No I/O, no model. PLAN §2.3, §3.2.

Formulas match data/generate.py exactly (see DECISIONS.md, T04):

    n            = round(term_months * periods_per_year / 12)
    i            = stated_apr / periods_per_year
    L            = principal + financed origination fee
    payment      = (L - balloon/(1+i)^n) * i / (1 - (1+i)^-n)
    upfront_fees = doc fee + non-financed origination fee
    total_cost   = n * payment + balloon + upfront_fees
    effective_apr = periods_per_year * IRR such that
                    principal - upfront_fees == PV(payments) + PV(balloon)

A missing required term never defaults to zero. `compute` returns
`incomplete=True` and leaves `total_cost` / `effective_apr` as None instead.
"""

from __future__ import annotations

from dataclasses import replace

from clause import config
from clause.types import Analysis, Clause, Fee, LoanTerms

PERIODS_PER_YEAR = {"monthly": 12, "biweekly": 26, "weekly": 52}

REQUIRED_FOR_TOTAL = ("payment_amount", "term_months", "payment_frequency")
REQUIRED_FOR_APR = REQUIRED_FOR_TOTAL + ("principal",)

RISK_ORDER = {"high": 0, "medium": 1, "low": 2, "standard": 3, None: 4}

QUESTIONS = {
    "balloon": "Can the balloon payment be removed or spread across the regular installments?",
    "origination_fee": "Can the origination fee be waived, reduced, or paid up front instead of financed?",
    "prepayment_penalty": "Will you remove the prepayment premium so I can pay this off early without a penalty?",
    "late_fee": "Can the late charge be a flat amount, with no rate increase after a late payment?",
    "auto_renewal": "Will you strike the automatic renewal so the agreement simply ends when it's paid off?",
    "cross_default": "Can the cross-default clause be limited to obligations owed to you, not to any creditor?",
    "personal_guarantee": "Is the personal guarantee or confession of judgment negotiable? What is the cap?",
    "insurance": "Can I use my own insurer? What does your insurance program actually cost per month?",
    "venue": "Can disputes be handled in my home state instead of the venue named here?",
    "blanket_lien": "Will you limit the security interest to the equipment itself, not all of my business assets?",
}


# ---------------------------------------------------------------------------
# Finance primitives
# ---------------------------------------------------------------------------


def n_periods(term_months: int, freq: str) -> int:
    return round(term_months * PERIODS_PER_YEAR[freq] / 12)


def scheduled_payment(balance: float, apr: float, term_months: int, freq: str, balloon: float = 0.0) -> float:
    k = PERIODS_PER_YEAR[freq]
    n = n_periods(term_months, freq)
    i = apr / k
    if i == 0:
        return (balance - balloon) / n
    annuity = i / (1 - (1 + i) ** -n)
    return (balance - balloon / (1 + i) ** n) * annuity


def effective_apr(net_proceeds: float, payment: float, n: int, freq: str, balloon: float = 0.0) -> float:
    """Nominal annual rate whose periodic IRR discounts payments + balloon to net proceeds."""
    k = PERIODS_PER_YEAR[freq]

    def npv(r: float) -> float:
        pv = sum(payment / (1 + r) ** t for t in range(1, n + 1))
        pv += balloon / (1 + r) ** n
        return net_proceeds - pv

    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(mid) > 0:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2 * k, 6)


def fee_amount(fee: Fee, principal: float | None, payment: float | None) -> float | None:
    """Dollar value of one instance of a fee, or None if its basis can't be resolved yet."""
    if fee.basis == "flat":
        return round(fee.value, 2)
    if fee.basis == "percent_of_principal":
        return round(principal * fee.value, 2) if principal is not None else None
    if fee.basis == "percent_of_payment":
        return round(payment * fee.value, 2) if payment is not None else None
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute(terms: LoanTerms, clauses: list[Clause]) -> Analysis:
    """Pure function: LoanTerms + classified clauses -> Analysis. Does not mutate inputs."""
    warnings: list[str] = []
    missing = [f for f in REQUIRED_FOR_APR if getattr(terms, f) is None]
    for f in missing:
        warnings.append(f"{f} not stated in document")
    for f, c in terms.confidences.items():
        if c < config.LOW_CONFIDENCE and getattr(terms, f, None) is not None:
            warnings.append(f"{f} is low confidence ({c:.2f}); verify against the source quote")

    balloon = terms.balloon_amount or 0.0
    principal = terms.principal
    payment = terms.payment_amount

    # ---- fee buckets --------------------------------------------------------
    upfront = 0.0
    financed_fee = 0.0
    fee_incomplete = False
    for fee in terms.fees:
        if fee.confidence < config.LOW_CONFIDENCE:
            warnings.append(f"{fee.kind} fee is low confidence ({fee.confidence:.2f}); verify against the source quote")
        if fee.kind not in ("doc", "origination"):
            continue  # contingent fees (late, prepayment) aren't part of the scheduled cost
        amt = fee_amount(fee, principal, payment)
        if amt is None:
            fee_incomplete = True
            warnings.append(f"{fee.kind} fee is a percentage but principal is unknown; cost excluded")
            continue
        if fee.financed:
            financed_fee += amt
        else:
            upfront += amt
    upfront = round(upfront, 2)

    # ---- totals -------------------------------------------------------------
    total_cost: float | None = None
    eff: float | None = None
    cost_above_stated: float | None = None
    n: int | None = None
    can_total = all(getattr(terms, f) is not None for f in REQUIRED_FOR_TOTAL) and not fee_incomplete
    if can_total:
        n = n_periods(terms.term_months, terms.payment_frequency)  # type: ignore[arg-type]
        total_cost = round(n * payment + balloon + upfront, 2)     # type: ignore[operator]
        if principal is not None:
            eff = effective_apr(principal - upfront, payment, n, terms.payment_frequency, balloon)  # type: ignore[arg-type]
            if terms.stated_apr is not None:
                honest_payment = scheduled_payment(principal, terms.stated_apr, terms.term_months,  # type: ignore[arg-type]
                                                   terms.payment_frequency, balloon)          # type: ignore[arg-type]
                cost_above_stated = round(total_cost - (n * round(honest_payment, 2) + balloon), 2)

                # Cross-check: does the stated payment agree with the stated rate?
                expected = scheduled_payment(principal + financed_fee, terms.stated_apr,  # type: ignore[arg-type]
                                             terms.term_months, terms.payment_frequency, balloon)  # type: ignore[arg-type]
                if abs(expected - payment) / max(payment, 1) > 0.02:  # type: ignore[operator]
                    warnings.append(
                        f"stated installment {payment:,.2f} does not match the stated rate "
                        f"(expected ~{expected:,.2f}); there may be undisclosed financed charges")
    incomplete = not can_total or principal is None

    # ---- per-clause dollar impact ------------------------------------------
    out_clauses = [replace(c, dollar_impact=_clause_impact(c, terms, n, upfront)) for c in clauses]
    out_clauses.sort(key=lambda c: (-(c.dollar_impact or 0.0), c.dollar_impact is None, RISK_ORDER.get(c.risk, 4), c.id))

    grade = _grade(eff, terms.stated_apr, out_clauses)
    questions = _questions(out_clauses)

    return Analysis(
        terms=terms, clauses=out_clauses,
        total_cost=total_cost, effective_apr=eff, cost_above_stated=cost_above_stated,
        grade=grade, incomplete=incomplete, warnings=warnings,
        questions_to_ask=questions, timings={},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clause_impact(c: Clause, terms: LoanTerms, n: int | None, upfront: float) -> float | None:
    """Dollar figure for a clause, derived from extracted terms — never from the clause text."""
    p, pay = terms.principal, terms.payment_amount
    if c.category == "balloon":
        return terms.balloon_amount
    if c.category == "origination_fee":
        fee = _fee(terms, "origination")
        return fee_amount(fee, p, pay) if fee else None
    if c.category == "prepayment_penalty":
        fee = _fee(terms, "prepayment")
        return fee_amount(fee, p, pay) if fee else None
    if c.category == "late_fee":
        fee = _fee(terms, "late")
        return fee_amount(fee, p, pay) if fee else None
    if c.category == "auto_renewal":
        return round(n * pay, 2) if n is not None and pay is not None else None
    return None


def _fee(terms: LoanTerms, kind: str) -> Fee | None:
    return next((f for f in terms.fees if f.kind == kind), None)


def _grade(eff: float | None, stated: float | None, clauses: list[Clause]) -> str:
    """A-F from the APR gap plus the count of risky clauses. Deterministic, documented in DECISIONS."""
    points = 0.0
    if eff is not None and stated is not None:
        points += max(0.0, (eff - stated) * 100)          # 1 point per APR percentage point
    points += 1.5 * sum(1 for c in clauses if c.risk == "high")
    points += 0.5 * sum(1 for c in clauses if c.risk == "medium")
    for grade, limit in (("A", 1.0), ("B", 3.0), ("C", 6.0), ("D", 10.0)):
        if points < limit:
            return grade
    return "F"


def _questions(clauses: list[Clause]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for c in clauses:
        if c.category in QUESTIONS and c.category not in seen and c.risk in ("high", "medium"):
            seen.add(c.category)
            out.append(QUESTIONS[c.category])
    return out
