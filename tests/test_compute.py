"""compute.py: the only source of numbers the user sees. No model, no I/O."""

import json
from pathlib import Path

import pytest

from clause import compute as C
from clause.types import Clause, Fee, LoanTerms, SourceSpan

ROOT = Path(__file__).resolve().parent.parent
SPAN = SourceSpan(page=1, text="x")


def fee(kind, basis, value, *, financed=False, confidence=0.9):
    return Fee(kind=kind, basis=basis, value=value, span=SPAN, confidence=confidence, financed=financed)


def terms(**kw):
    base = dict(principal=10000.0, stated_apr=0.05, term_months=12, payment_amount=856.07,
                payment_frequency="monthly", balloon_amount=None, fees=[])
    base.update(kw)
    return LoanTerms(**base)


def clause(id, category, risk="high"):
    return Clause(id=id, text=f"{category} clause", span=SPAN, category=category, risk=risk)


# ---- effective APR: five hand-checked cases (independent Newton solve) ------
# Reference values computed with a separate Newton's-method IRR, not the bisection in compute.py.

@pytest.mark.parametrize("net, payment, n, freq, balloon, expected", [
    (10000.00, 856.07, 12, "monthly", 0.00, 0.0500),      # no fees -> effective == stated (5%)
    (9800.00, 856.07, 12, "monthly", 0.00, 0.0879),       # $200 upfront fee on the same loan
    (8930.00, 229.04, 48, "monthly", 0.00, 0.1059),       # 5% origination fee deducted, 7.9% stated
    (9400.00, 194.46, 48, "monthly", 1944.60, 0.0790),    # 10x balloon, no fees -> still 7.9%
    (11850.00, 180.69, 78, "biweekly", 0.00, 0.1178),     # biweekly, $150 doc fee, 10.9% stated
])
def test_effective_apr_hand_cases(net, payment, n, freq, balloon, expected):
    assert C.effective_apr(net, payment, n, freq, balloon) == pytest.approx(expected, abs=5e-5)


def test_scheduled_payment_textbook():
    assert round(C.scheduled_payment(10000, 0.05, 12, "monthly"), 2) == 856.07


# ---- incomplete handling: never a confident wrong number --------------------

@pytest.mark.parametrize("missing", ["payment_amount", "term_months", "payment_frequency"])
def test_missing_required_term_means_no_total(missing):
    a = C.compute(terms(**{missing: None}), [])
    assert a.incomplete is True
    assert a.total_cost is None and a.effective_apr is None and a.cost_above_stated is None
    assert any(missing in w for w in a.warnings)


def test_missing_principal_gives_total_but_no_apr():
    a = C.compute(terms(principal=None), [])
    assert a.total_cost == pytest.approx(856.07 * 12)
    assert a.effective_apr is None
    assert a.incomplete is True


def test_percent_fee_with_unknown_principal_blocks_total():
    t = terms(principal=None, fees=[fee("origination", "percent_of_principal", 0.04)])
    a = C.compute(t, [])
    assert a.total_cost is None and a.incomplete
    assert any("origination fee is a percentage" in w for w in a.warnings)


def test_low_confidence_term_is_warned_but_used():
    t = terms(confidences={"payment_amount": 0.3})
    a = C.compute(t, [])
    assert a.total_cost is not None
    assert any("payment_amount is low confidence" in w for w in a.warnings)


def test_low_confidence_fee_warned():
    a = C.compute(terms(fees=[fee("doc", "flat", 150.0, confidence=0.2)]), [])
    assert any("doc fee is low confidence" in w for w in a.warnings)


# ---- totals ------------------------------------------------------------------

def test_clean_loan_totals():
    a = C.compute(terms(), [])
    assert a.incomplete is False
    assert a.total_cost == pytest.approx(10272.84)
    assert a.effective_apr == pytest.approx(0.05, abs=5e-5)
    assert a.cost_above_stated == pytest.approx(0.0, abs=0.01)
    assert a.grade == "A"


def test_upfront_fees_raise_total_and_apr():
    t = terms(fees=[fee("doc", "flat", 150.0), fee("origination", "percent_of_principal", 0.005)])
    a = C.compute(t, [])
    assert a.total_cost == pytest.approx(10272.84 + 150 + 50)
    assert a.effective_apr > 0.05
    assert a.cost_above_stated == pytest.approx(200.0)


def test_financed_fee_not_in_upfront():
    """A financed origination fee is already in the payment; must not be double counted."""
    t = terms(fees=[fee("origination", "percent_of_principal", 0.03, financed=True)])
    a = C.compute(t, [])
    assert a.total_cost == pytest.approx(856.07 * 12)


def test_balloon_added_to_total():
    a = C.compute(terms(payment_amount=194.46, stated_apr=0.079, term_months=48,
                        principal=9400.0, balloon_amount=1944.60), [])
    assert a.total_cost == pytest.approx(194.46 * 48 + 1944.60)
    assert a.effective_apr == pytest.approx(0.079, abs=5e-5)


def test_contingent_fees_excluded_from_total():
    t = terms(fees=[fee("late", "flat", 50.0), fee("prepayment", "percent_of_principal", 0.03)])
    a = C.compute(t, [])
    assert a.total_cost == pytest.approx(856.07 * 12)


def test_payment_mismatch_warning():
    """Stated installment much higher than stated rate implies -> hidden charges."""
    a = C.compute(terms(payment_amount=950.00), [])
    assert any("does not match the stated rate" in w for w in a.warnings)


def test_no_mismatch_warning_when_fee_financed():
    p = round(C.scheduled_payment(10300, 0.05, 12, "monthly"), 2)
    t = terms(payment_amount=p, fees=[fee("origination", "percent_of_principal", 0.03, financed=True)])
    assert not any("does not match" in w for w in C.compute(t, []).warnings)


# ---- clauses: impact, ranking, grade, questions -----------------------------

def test_clause_impacts_come_from_terms_not_text():
    t = terms(term_months=48, payment_amount=250.0, balloon_amount=2500.0,
              fees=[fee("origination", "percent_of_principal", 0.04),
                    fee("prepayment", "flat", 750.0),
                    fee("late", "percent_of_payment", 0.05)])
    cl = [clause("c1", "late_fee"), clause("c2", "balloon"), clause("c3", "auto_renewal"),
          clause("c4", "venue"), clause("c5", "origination_fee"), clause("c6", "prepayment_penalty"),
          clause("c7", "standard", risk="standard")]
    a = C.compute(t, cl)
    by = {c.id: c.dollar_impact for c in a.clauses}
    assert by == {"c1": 12.5, "c2": 2500.0, "c3": 12000.0, "c4": None, "c5": 400.0, "c6": 750.0, "c7": None}


def test_ranking_by_dollar_then_risk():
    t = terms(term_months=48, payment_amount=250.0, balloon_amount=2500.0, fees=[fee("late", "flat", 25.0)])
    cl = [clause("c1", "venue", "medium"), clause("c2", "late_fee", "low"), clause("c3", "balloon", "high"),
          clause("c4", "blanket_lien", "high"), clause("c5", "standard", "standard")]
    a = C.compute(t, cl)
    assert [c.id for c in a.clauses] == ["c3", "c2", "c4", "c1", "c5"]


def test_does_not_mutate_inputs():
    cl = [clause("c1", "balloon")]
    t = terms(balloon_amount=1000.0)
    C.compute(t, cl)
    assert cl[0].dollar_impact is None


@pytest.mark.parametrize("highs, mediums, gap, expected", [
    (0, 0, 0.0, "A"), (1, 0, 0.0, "B"), (0, 0, 0.02, "B"), (2, 0, 0.0, "C"),
    (2, 1, 0.02, "C"), (2, 2, 0.02, "D"), (3, 0, 0.04, "D"), (4, 0, 0.06, "F"),
])
def test_grade_scale(highs, mediums, gap, expected):
    cl = [clause(f"h{i}", "venue", "high") for i in range(highs)] + \
         [clause(f"m{i}", "venue", "medium") for i in range(mediums)]
    assert C._grade(0.05 + gap, 0.05, cl) == expected


def test_grade_without_apr_uses_clauses_only():
    a = C.compute(terms(principal=None), [clause("c1", "blanket_lien")])
    assert a.grade == "B"


def test_questions_deduped_and_only_for_risky_clauses():
    cl = [clause("c1", "balloon"), clause("c2", "balloon"), clause("c3", "venue", "medium"),
          clause("c4", "late_fee", "low"), clause("c5", "standard", "standard")]
    q = C.compute(terms(balloon_amount=100.0), cl).questions_to_ask
    assert q == [C.QUESTIONS["balloon"], C.QUESTIONS["venue"]]


# ---- golden.json: reproduce every generated document exactly ----------------

def _golden():
    return json.loads((ROOT / "data" / "golden.json").read_text())


@pytest.mark.parametrize("entry", _golden(), ids=lambda e: e["doc_id"])
def test_matches_golden(entry):
    t, g = entry["terms"], entry["computed"]
    lt = LoanTerms(
        principal=t["principal"], stated_apr=t["stated_apr"], term_months=t["term_months"],
        payment_amount=t["payment_amount"], payment_frequency=t["payment_frequency"],
        balloon_amount=t["balloon_amount"],
        fees=[fee(f["kind"], f["basis"], f["value"], financed=f["financed"]) for f in t["fees"]],
    )
    a = C.compute(lt, [])
    assert a.incomplete is False
    assert a.total_cost == pytest.approx(g["total_cost"], abs=0.01)
    assert a.effective_apr == pytest.approx(g["effective_apr"], abs=5e-5)
    assert not any("does not match" in w for w in a.warnings), a.warnings
