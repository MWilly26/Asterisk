"""Round-trip serialization for every §3.1 type."""

import json

from clause.types import Analysis, Clause, Fee, LoanTerms, SourceSpan, TextBlock


def _span(page=1, text="the origination fee is 3.5%", bbox=(10.0, 20.0, 300.0, 32.0)):
    return SourceSpan(page=page, text=text, bbox=bbox)


def _terms():
    return LoanTerms(
        principal=9400.0,
        stated_apr=0.079,
        term_months=48,
        payment_amount=229.31,
        payment_frequency="monthly",
        balloon_amount=2820.0,
        fees=[
            Fee(kind="origination", basis="percent_of_principal", value=0.035,
                span=_span(), confidence=0.95),
            Fee(kind="late", basis="percent_of_payment", value=0.05,
                span=_span(page=7, text="5% of the payment", bbox=None), confidence=0.6),
        ],
        spans={"principal": _span(text="$9,400.00"), "balloon_amount": _span(page=12, text="$2,820.00")},
        confidences={"principal": 1.0, "balloon_amount": 0.8},
        missing_fields=["doc_fee"],
    )


def _analysis():
    clauses = [
        Clause(id="c001", text="A final balloon payment of $2,820.00 shall be due.",
               span=_span(page=12, text="A final balloon payment"),
               category="balloon", risk="high", plain_language="You owe $2,820 at the end.",
               confidence=0.9, dollar_impact=2820.0),
        Clause(id="c002", text="Governing law: Delaware.", span=_span(page=13, text="Governing law", bbox=None)),
    ]
    return Analysis(
        terms=_terms(), clauses=clauses,
        total_cost=14827.88, effective_apr=0.1361, cost_above_stated=1204.12,
        grade="D", incomplete=False,
        warnings=["doc_fee not stated in document"],
        questions_to_ask=["Is the balloon payment negotiable?"],
        timings={"parse": 3.2, "classify": 9.8},
    )


def test_source_span_round_trip():
    s = _span()
    back = SourceSpan.from_dict(s.to_dict())
    assert back == s
    assert isinstance(back.bbox, tuple)


def test_source_span_none_bbox():
    s = SourceSpan(page=2, text="x", bbox=None)
    assert SourceSpan.from_json(s.to_json()) == s


def test_text_block_round_trip():
    b = TextBlock(page=3, text="Fee Schedule", bbox=(1, 2, 3, 4), kind="heading")
    assert TextBlock.from_json(b.to_json()) == b


def test_clause_defaults_are_none():
    c = Clause(id="c001", text="t", span=_span())
    assert c.category is None and c.risk is None and c.dollar_impact is None
    assert Clause.from_dict(c.to_dict()) == c


def test_loan_terms_round_trip():
    t = _terms()
    back = LoanTerms.from_json(t.to_json())
    assert back == t
    assert isinstance(back.fees[0], Fee)
    assert isinstance(back.fees[0].span, SourceSpan)
    assert isinstance(back.spans["principal"], SourceSpan)
    assert back.fees[1].span.bbox is None


def test_loan_terms_empty_defaults():
    t = LoanTerms()
    assert t.fees == [] and t.spans == {} and t.missing_fields == []
    assert LoanTerms.from_dict(t.to_dict()) == t


def test_analysis_round_trip():
    a = _analysis()
    back = Analysis.from_json(a.to_json())
    assert back == a
    assert isinstance(back.terms, LoanTerms)
    assert all(isinstance(c, Clause) for c in back.clauses)


def test_analysis_json_is_plain():
    """Output must be plain JSON — no tuples, dataclasses, or custom objects."""
    raw = _analysis().to_json()
    parsed = json.loads(raw)
    assert isinstance(parsed["terms"]["spans"]["principal"]["bbox"], list)
    assert parsed["grade"] == "D"


def test_from_dict_ignores_unknown_keys():
    """Older fixtures / API clients may send extra keys; don't crash on them."""
    d = _span().to_dict() | {"extra": 1}
    assert SourceSpan.from_dict(d) == _span()
