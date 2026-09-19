"""Synthetic data generator: golden.json is exact and every trap is really in the PDF."""

import json
import re

import pdfplumber
import pytest

import generate as gen

REQUIRED_TERM_KEYS = {"principal", "stated_apr", "term_months", "payment_amount",
                      "payment_frequency", "balloon_amount", "fees"}


@pytest.fixture
def golden(corpus):
    return corpus


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _pdf_text(entry: dict) -> str:
    """Full text, reading columns separately for two-column docs."""
    with pdfplumber.open(entry["pdf"]) as pdf:
        parts = []
        for page in pdf.pages:
            if entry["ugly"] == "two_column":
                w, h = page.width, page.height
                for box in [(0, 0, w / 2, h), (w / 2, 0, w, h)]:
                    parts.append(page.crop(box).extract_text() or "")
            else:
                parts.append(page.extract_text() or "")
        text = _norm(" ".join(parts))
        # Drop the running footer so spans that straddle a page break still match.
        return re.sub(r"CONFIDENTIAL - SYNTHETIC DOCUMENT FOR EVALUATION USE ONLY Page \d+ ?", "", text)


# ---- corpus shape ---------------------------------------------------------


def test_counts(golden):
    assert len(golden) == gen.N_TRAP_DOCS + gen.N_CLEAN_DOCS
    clean = [g for g in golden if not g["traps"]]
    assert len(clean) == gen.N_CLEAN_DOCS
    for g in golden:
        if g["traps"]:
            assert 2 <= len(g["traps"]) <= 4
            assert len(set(g["traps"])) == len(g["traps"])


def test_every_trap_type_at_least_twice(golden):
    counts = {t: 0 for t in gen.TRAP_TYPES}
    for g in golden:
        for t in g["traps"]:
            counts[t] += 1
    assert all(c >= 2 for c in counts.values()), counts


def test_ugly_docs_present(golden):
    assert sorted(g["ugly"] for g in golden if g["ugly"]) == sorted(gen.UGLY_STYLES)


def test_golden_schema(golden):
    for g in golden:
        assert {"doc_id", "pdf", "terms", "computed", "traps", "trap_spans"} <= set(g)
        assert REQUIRED_TERM_KEYS <= set(g["terms"])
        assert {"total_cost", "effective_apr"} <= set(g["computed"])
        for f in g["terms"]["fees"]:
            assert f["kind"] in {"origination", "late", "prepayment", "doc", "other"}
            assert f["basis"] in {"flat", "percent_of_principal", "percent_of_payment"}
        assert set(g["trap_spans"]) == set(g["traps"])


def test_page_counts(golden):
    for g in golden:
        with pdfplumber.open(g["pdf"]) as pdf:
            assert 6 <= len(pdf.pages) <= 14, (g["doc_id"], len(pdf.pages))


def test_deterministic(tmp_path):
    a = gen.generate(out_dir=tmp_path / "a", golden_path=tmp_path / "a.json", seed=7)
    b = gen.generate(out_dir=tmp_path / "b", golden_path=tmp_path / "b.json", seed=7)
    strip = lambda gs: [{k: v for k, v in g.items() if k != "pdf"} for g in gs]
    assert strip(a) == strip(b)


# ---- ground truth is exact -------------------------------------------------


def test_payment_matches_formula(golden):
    for g in golden:
        t, c = g["terms"], g["computed"]
        p = gen.scheduled_payment(c["amount_financed"], t["stated_apr"], t["term_months"],
                                  t["payment_frequency"], t["balloon_amount"] or 0.0)
        assert abs(round(p, 2) - t["payment_amount"]) <= 0.01, g["doc_id"]


def test_total_cost_is_sum_of_outflows(golden):
    for g in golden:
        t, c = g["terms"], g["computed"]
        expected = c["n_payments"] * t["payment_amount"] + (t["balloon_amount"] or 0) + c["upfront_fees"]
        assert abs(expected - c["total_cost"]) < 0.01


def test_effective_apr_never_below_stated(golden):
    # Payment is rounded to the cent, which can nudge the IRR a few basis points under.
    for g in golden:
        assert g["computed"]["effective_apr"] >= g["terms"]["stated_apr"] - 1e-4, g["doc_id"]


def test_clean_docs_have_no_expensive_fees(golden):
    for g in golden:
        if g["traps"]:
            continue
        kinds = {f["kind"] for f in g["terms"]["fees"]}
        assert kinds <= {"doc", "late"}
        assert g["terms"]["balloon_amount"] is None
        assert g["computed"]["effective_apr"] - g["terms"]["stated_apr"] < 0.01


def test_apr_gap_trap_is_at_least_four_points(golden):
    for g in golden:
        if "T_APR_GAP" in g["traps"]:
            assert g["computed"]["effective_apr"] - g["terms"]["stated_apr"] >= 0.04, g["doc_id"]


def test_balloon_is_8_to_15x_payment(golden):
    for g in golden:
        if "T_BALLOON" in g["traps"]:
            r = g["terms"]["balloon_amount"] / g["terms"]["payment_amount"]
            assert 8 <= r <= 15, (g["doc_id"], r)
        else:
            assert g["terms"]["balloon_amount"] is None


def test_orig_fee_financed_flag(golden):
    for g in golden:
        orig = next((f for f in g["terms"]["fees"] if f["kind"] == "origination"), None)
        if "T_ORIG_FEE" in g["traps"]:
            assert orig and orig["financed"] and orig["basis"] == "percent_of_principal"
        elif orig:
            assert not orig["financed"]


# ---- every span is really in the PDF ---------------------------------------


def test_trap_spans_and_terms_appear_in_pdf_text(golden):
    for g in golden:
        text = _pdf_text(g)
        for trap, span in g["trap_spans"].items():
            assert _norm(span) in text, (g["doc_id"], trap)
        t = g["terms"]
        assert gen.money(t["principal"]) in text
        assert gen.money(t["payment_amount"]) in text
        assert gen.pct(t["stated_apr"]) in text
        if t["balloon_amount"]:
            assert text.count(gen.money(t["balloon_amount"])) == 1, "balloon must be stated once"


def test_balloon_only_in_footnote_size_text(golden):
    """The balloon figure must not appear in body-size text."""
    for g in golden:
        if "T_BALLOON" not in g["traps"]:
            continue
        needle = gen.money(g["terms"]["balloon_amount"]).replace(",", "")
        with pdfplumber.open(g["pdf"]) as pdf:
            for page in pdf.pages:
                big = [c for c in page.chars if c["size"] >= 9]
                assert needle not in "".join(c["text"] for c in big).replace(",", ""), g["doc_id"]
