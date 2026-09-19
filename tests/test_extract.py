"""extract.py: schema, source-text validation, missing-field handling. Mock client only."""

import json

import pytest

from clause import config
from clause.client import MockClient
from clause.extract import SCHEMA, document_text, extract_terms, number_in_text, number_variants, validate
from clause.types import TextBlock

DOC = [
    TextBlock(page=1, text="Amount Financed. The principal amount financed under this Agreement is $9,400.00, representing the purchase price of $10,000.00 less a down payment of $600.00.", bbox=(1, 2, 3, 4), kind="paragraph"),
    TextBlock(page=1, text="Rate. Interest shall accrue at a fixed Annual Percentage Rate (APR) of 7.90%.", bbox=None, kind="paragraph"),
    TextBlock(page=1, text="Installments. Borrower shall repay in 48 consecutive monthly installments of $229.31 each.", bbox=None, kind="paragraph"),
    TextBlock(page=2, text="1 A final balloon payment of $2,820.00 shall be due with the final installment.", bbox=None, kind="footnote"),
    TextBlock(page=3, text="Origination Fee | 3.50% of principal ($329.00) | Financed into Amount Financed", bbox=None, kind="table_row"),
    TextBlock(page=3, text="Late Charge | $25.00 | Upon late payment", bbox=None, kind="table_row"),
]


def F(value, quote, conf=0.9):
    return {"value": value, "quote": quote, "confidence": conf}


GOOD = {
    "principal": F(9400.0, "principal amount financed under this Agreement is $9,400.00"),
    "stated_apr": F(0.079, "Annual Percentage Rate (APR) of 7.90%"),
    "term_months": F(48, "48 consecutive monthly installments"),
    "payment_amount": F(229.31, "installments of $229.31 each"),
    "payment_frequency": F("monthly", "consecutive monthly installments"),
    "balloon_amount": F(2820.0, "A final balloon payment of $2,820.00"),
    "fees": [
        {"kind": "origination", "basis": "percent_of_principal", "value": 0.035, "financed": True,
         "quote": "Origination Fee | 3.50% of principal", "confidence": 0.9},
        {"kind": "late", "basis": "flat", "value": 25.0, "financed": False,
         "quote": "Late Charge | $25.00", "confidence": 0.85},
    ],
}


# ---- helpers ----------------------------------------------------------------

def test_document_text_tags_pages():
    t = document_text(DOC)
    assert t.startswith("[page 1]") and "\n[page 2]\n" in t and "[page 3]" in t


@pytest.mark.parametrize("value, percent, text, ok", [
    (9400.0, False, "is $9,400.00, representing", True),
    (9400.0, False, "is 9400 dollars", True),
    (9400.0, False, "is $9,450.00", False),
    (0.079, True, "APR of 7.90%", True),
    (0.079, True, "APR of 7.9%", True),
    (0.079, True, "APR of 7.95%", False),
    (48, False, "in 48 consecutive", True),
    (48, False, "in 36 consecutive", False),
    (229.31, False, "of $229.31 each", True),
])
def test_number_in_text(value, percent, text, ok):
    assert number_in_text(value, text, percent=percent) is ok


def test_number_variants_dedupe():
    assert number_variants(25.0)[0] == "$25.00" and len(set(number_variants(25.0))) == len(number_variants(25.0))


# ---- happy path ----------------------------------------------------------------

def test_full_extraction(tmp_path):
    c = MockClient(responses=[json.dumps(GOOD)], log_path=tmp_path / "l.jsonl")
    t = extract_terms(DOC, c)
    assert (t.principal, t.stated_apr, t.term_months, t.payment_amount) == (9400.0, 0.079, 48, 229.31)
    assert t.payment_frequency == "monthly" and t.balloon_amount == 2820.0
    assert t.missing_fields == []
    assert all(v >= 0.85 for v in t.confidences.values()), t.confidences
    assert t.spans["balloon_amount"].page == 2
    assert t.spans["principal"].bbox == (1, 2, 3, 4)
    assert [(f.kind, f.basis, f.value, f.financed) for f in t.fees] == [
        ("origination", "percent_of_principal", 0.035, True), ("late", "flat", 25.0, False)]
    assert t.fees[0].span.page == 3
    assert c.calls[0]["json_schema"] == SCHEMA
    assert "[page 1]" in c.calls[0]["messages"][1]["content"]


# ---- §2.4: low / missing ---------------------------------------------------------

def test_missing_fields_stay_none():
    data = dict(GOOD, balloon_amount=F(None, None), term_months=F(None, None))
    t = validate(data, DOC)
    assert t.balloon_amount is None and t.term_months is None
    assert set(t.missing_fields) == {"balloon_amount", "term_months"}
    assert "balloon_amount" not in t.confidences


def test_unverifiable_number_gets_low_confidence():
    """Model hallucinates $9,450 — not in the document — value kept but flagged 0.3."""
    data = dict(GOOD, principal=F(9450.0, "principal amount financed under this Agreement is $9,450.00"))
    t = validate(data, DOC)
    assert t.principal == 9450.0
    assert t.confidences["principal"] == config.UNVERIFIED_CONFIDENCE
    assert "principal" not in t.missing_fields


def test_real_number_but_bad_quote_caps_at_low():
    data = dict(GOOD, payment_amount=F(229.31, "this quote is not in the document"))
    t = validate(data, DOC)
    assert t.confidences["payment_amount"] == config.LOW_CONFIDENCE
    assert t.spans["payment_amount"].page == 0


def test_unverifiable_fee_flagged():
    data = dict(GOOD, fees=[{"kind": "doc", "basis": "flat", "value": 150.0, "financed": False,
                             "quote": "Documentation Fee $150.00", "confidence": 0.9}])
    t = validate(data, DOC)
    assert t.fees[0].confidence == config.UNVERIFIED_CONFIDENCE


def test_percent_given_as_whole_number_is_normalized():
    data = dict(GOOD, stated_apr=F(7.9, "Annual Percentage Rate (APR) of 7.90%"),
                fees=[dict(GOOD["fees"][0], value=3.5)])
    t = validate(data, DOC)
    assert t.stated_apr == pytest.approx(0.079) and t.fees[0].value == pytest.approx(0.035)
    assert t.confidences["stated_apr"] >= 0.85  # verified after normalization


def test_invalid_fee_entries_skipped():
    data = dict(GOOD, fees=[{"kind": "bogus", "basis": "flat", "value": 1, "financed": False, "quote": "", "confidence": 1},
                            {"kind": "late", "basis": "flat", "value": "abc", "financed": False, "quote": "", "confidence": 1}])
    assert validate(data, DOC).fees == []


def test_frequency_must_appear_in_text():
    data = dict(GOOD, payment_frequency=F("weekly", "weekly"))
    t = validate(data, DOC)
    assert t.payment_frequency == "weekly" and t.confidences["payment_frequency"] == config.UNVERIFIED_CONFIDENCE


def test_unparseable_output_means_all_missing(tmp_path):
    c = MockClient(responses=["not json"], log_path=tmp_path / "l.jsonl")
    t = extract_terms(DOC, c)
    assert t.principal is None and "principal" in t.missing_fields and t.fees == []


def test_result_feeds_compute():
    from clause.compute import compute
    a = compute(validate(GOOD, DOC), [])
    assert a.incomplete is False and a.total_cost == pytest.approx(48 * 229.31 + 2820.0)
