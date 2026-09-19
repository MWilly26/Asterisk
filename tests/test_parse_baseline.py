"""pdfplumber baseline: same TextBlock contract as parse.py."""

import re

from clause.parse_baseline import parse_pdf_baseline
from clause.types import TextBlock


def _norm(s):
    return re.sub(r"\s+", " ", s)


def test_returns_ordered_text_blocks(corpus):
    entry = next(g for g in corpus if not g["ugly"])
    blocks = parse_pdf_baseline(entry["pdf"])
    assert blocks and all(isinstance(b, TextBlock) for b in blocks)
    assert [b.page for b in blocks] == sorted(b.page for b in blocks)
    assert all(b.bbox is not None and len(b.bbox) == 4 for b in blocks)
    assert all(b.text.strip() for b in blocks)


def test_detects_headings(corpus):
    entry = next(g for g in corpus if not g["ugly"])
    blocks = parse_pdf_baseline(entry["pdf"])
    heads = [b.text for b in blocks if b.kind == "heading"]
    assert any("Payment Terms" in h or "PAYMENT TERMS" in h for h in heads)
    assert any(h.startswith("SCHEDULE A") for h in heads)


def test_terms_present_in_text(corpus):
    """Single-column docs: the key numbers survive extraction."""
    for entry in corpus:
        if entry["ugly"] == "two_column":
            continue
        text = _norm(" ".join(b.text for b in parse_pdf_baseline(entry["pdf"])))
        t = entry["terms"]
        assert f"${t['principal']:,.2f}" in text, entry["doc_id"]
        assert f"${t['payment_amount']:,.2f}" in text, entry["doc_id"]


def test_round_trip_serializable(corpus):
    b = parse_pdf_baseline(corpus[0]["pdf"])[0]
    assert TextBlock.from_dict(b.to_dict()) == b
