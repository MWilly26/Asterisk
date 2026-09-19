"""Paired §6.5 document generator. Synthetic/offline only."""

import json
import re

import pdfplumber
import pytest

import generate_adversarial as ga
from clause.parse_baseline import parse_pdf_baseline
from clause.segment import segment
from run_eval import find_span_clause


@pytest.fixture(scope="module")
def adversarial_corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("adversarial")
    golden_path = root / "golden.json"
    rows = ga.generate(root / "docs", golden_path)
    assert rows == json.loads(golden_path.read_text())
    return rows


def _text(path):
    with pdfplumber.open(path) as pdf:
        return re.sub(r"\s+", " ", " ".join(page.extract_text() or "" for page in pdf.pages))


def test_five_identical_financial_pairs(adversarial_corpus):
    assert len(adversarial_corpus) == 10
    for pair_id in {row["pair_id"] for row in adversarial_corpus}:
        pair = [row for row in adversarial_corpus if row["pair_id"] == pair_id]
        assert {row["condition"] for row in pair} == {"control", "adversarial"}
        control = next(row for row in pair if row["condition"] == "control")
        attack = next(row for row in pair if row["condition"] == "adversarial")
        assert control["terms"] == attack["terms"]
        assert control["computed"] == attack["computed"]
        assert control["traps"] == attack["traps"]
        assert control["trap_spans"] == attack["trap_spans"]


def test_manipulation_only_appears_in_adversarial_copy(adversarial_corpus):
    for row in adversarial_corpus:
        text = _text(row["pdf"])
        if row["condition"] == "adversarial":
            assert row["manipulation"] in text
        else:
            attack = next(a["manipulation"] for a in adversarial_corpus
                          if a["pair_id"] == row["pair_id"] and a["condition"] == "adversarial")
            assert attack not in text


def test_every_trap_span_survives_parse_and_segmentation(adversarial_corpus):
    for row in adversarial_corpus:
        clauses = segment(parse_pdf_baseline(row["pdf"]))
        for trap, span in row["trap_spans"].items():
            clause, how = find_span_clause(clauses, span)
            assert clause is not None and how != "none", (row["doc_id"], trap)
            if row["condition"] == "adversarial":
                assert row["manipulation"] in clause.text


def test_generation_is_deterministic(tmp_path):
    a = ga.generate(tmp_path / "a", tmp_path / "a.json", seed=77)
    b = ga.generate(tmp_path / "b", tmp_path / "b.json", seed=77)
    without_paths = lambda rows: [{k: v for k, v in row.items() if k != "pdf"} for row in rows]
    assert without_paths(a) == without_paths(b)
