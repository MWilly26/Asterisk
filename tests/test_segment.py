"""segment.py: deterministic, and every planted trap lands in exactly one clause."""

import re

import pytest

from clause.parse_baseline import parse_pdf_baseline
from clause.segment import MAX_WORDS, _split_sentences, segment
from clause.types import TextBlock


def B(page, text, kind="paragraph", bbox=(0, 0, 1, 1)):
    return TextBlock(page=page, text=text, bbox=bbox, kind=kind)


def _norm(s):
    return re.sub(r"\s+", " ", s).strip()


# ---- unit rules ---------------------------------------------------------------

def test_ids_are_sequential_and_stable():
    cl = segment([B(1, "One provision."), B(1, "Another provision."), B(2, "Third.")])
    assert [c.id for c in cl] == ["c001", "c002", "c003"]
    assert cl[2].span.page == 2


def test_span_text_is_exact_clause_text():
    cl = segment([B(1, "Borrower shall pay a fee of $150.00 at closing.")])
    assert cl[0].text == cl[0].span.text
    assert cl[0].span.bbox == (0, 0, 1, 1)


def test_drops_headings_and_page_numbers():
    cl = segment([B(1, "3. Payment Terms", kind="heading"), B(1, "Section 4. DEFAULT"),
                  B(1, "Page 1"), B(1, "7"), B(1, "A real provision.")])
    assert [c.text for c in cl] == ["A real provision."]


def test_drops_repeated_footer():
    footer = "CONFIDENTIAL - FOR EVALUATION ONLY"
    blocks = [B(p, footer) for p in (1, 2, 3)] + [B(1, "Real text."), B(2, "Twice only.", ), B(3, "Twice only.")]
    texts = [c.text for c in segment(blocks)]
    assert footer not in texts
    assert texts.count("Twice only.") == 2  # seen on 2 pages: kept


def test_rejoins_paragraph_split_by_page_break():
    blocks = [B(1, "Borrower may prepay the Obligations in full upon thirty (30) days' notice, provided that Borrower shall pay a Prepayment Premium equal to 5.00% of the then outstanding"),
              B(2, "principal balance."),
              B(2, "Next provision.")]
    cl = segment(blocks)
    assert len(cl) == 2
    assert cl[0].text.endswith("outstanding principal balance.")
    assert cl[0].span.page == 1 and cl[0].span.bbox is None


def test_does_not_rejoin_when_previous_is_terminal():
    cl = segment([B(1, "Complete sentence."), B(2, "lowercase start but separate.")])
    assert len(cl) == 2


def test_table_rows_and_footnotes_are_one_clause_each():
    row = "Origination Fee | 3.50% of principal ($329.00) | Financed"
    fn = "1 A final balloon payment of $2,820.00 shall be due with the final installment. " * 3
    cl = segment([B(1, row, kind="table_row"), B(1, fn.strip(), kind="footnote")])
    assert [c.text for c in cl] == [row, _norm(fn)]


def test_long_paragraph_chunked_at_sentence_boundaries():
    sents = [f"Sentence number {i} says that Borrower shall do the thing described in this clause of the agreement." for i in range(12)]
    text = " ".join(sents)
    assert len(text.split()) > MAX_WORDS
    cl = segment([B(1, text)])
    assert len(cl) > 1
    for c in cl:
        assert c.text.startswith("Sentence number") and c.text.endswith("agreement.")
        assert c.span.bbox is None
    assert " ".join(c.text for c in cl) == text


def test_sentence_splitter_keeps_numbers_and_abbreviations():
    s = ("A final balloon payment of $2,820.00 shall be due. Payment after 5:00 p.m. Eastern is late. "
         "Keystone Capital, Inc. is the Lender.")
    assert _split_sentences(s) == [
        "A final balloon payment of $2,820.00 shall be due.",
        "Payment after 5:00 p.m. Eastern is late.",
        "Keystone Capital, Inc. is the Lender.",
    ]


def test_deterministic_and_non_mutating():
    blocks = [B(1, "Alpha."), B(1, "Beta.")]
    before = [b.to_dict() for b in blocks]
    assert segment(blocks) == segment(blocks)
    assert [b.to_dict() for b in blocks] == before


# ---- corpus: every trap in exactly one clause -----------------------------------

@pytest.fixture(scope="module")
def segmented(corpus):
    out = {}
    for g in corpus:
        if g["ugly"] == "two_column":
            continue  # pdfplumber interleaves the columns; that's the baseline's known failure
        out[g["doc_id"]] = (g, segment(parse_pdf_baseline(g["pdf"])))
    return out


def test_clause_counts_reasonable(segmented):
    for doc_id, (g, cl) in segmented.items():
        assert 40 <= len(cl) <= 150, (doc_id, len(cl))


def test_every_trap_span_in_exactly_one_clause(segmented):
    for doc_id, (g, cl) in segmented.items():
        texts = [_norm(c.text) for c in cl]
        for trap, span in g["trap_spans"].items():
            hits = [t for t in texts if _norm(span) in t]
            assert len(hits) == 1, (doc_id, trap, len(hits))


def test_no_footer_in_clauses(segmented):
    for doc_id, (g, cl) in segmented.items():
        assert not any("SYNTHETIC DOCUMENT FOR EVALUATION" in c.text for c in cl), doc_id
