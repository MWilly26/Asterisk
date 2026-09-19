"""DEMO.md (T21) stays tied to committed evidence: the demo document, its
figures, and the fallback sample must all trace to files in the repo."""

import json
import re

from clause import config
from clause.types import Analysis

REPO = config.REPO_ROOT
DEMO = REPO / "DEMO.md"


def test_demo_document_is_scored_correct_in_the_committed_nano_eval():
    text = DEMO.read_text()
    assert "eq_012" in text
    ev = json.load(open(REPO / "evals" / "results" / "eval_nano.json"))
    doc = next(d for d in ev["docs"] if d["doc_id"] == "eq_012")
    assert doc["computed"]["total_cost"]["outcome"] == "correct"
    assert doc["computed"]["effective_apr"]["outcome"] == "correct"
    assert all(t["caught"] for t in doc["traps"]) and len(doc["traps"]) == 4


def test_demo_figures_match_the_committed_analysis():
    text = DEMO.read_text()
    a = Analysis.from_json((REPO / "evals" / "results" / "analyses" / "nano" / "eq_012.json").read_text())
    assert f"${a.total_cost:,.2f}" in text                      # $14,764.04
    assert f"{100 * a.effective_apr:.2f}%" in text              # 8.60%
    assert f"{100 * a.terms.stated_apr:.2f}%" in text           # 6.90%
    assert f"${a.terms.balloon_amount:,.2f}" in text            # $2,288.84
    impacts = {c.category: c.dollar_impact for c in a.clauses if c.risk == "high"}
    assert f"${impacts['auto_renewal']:,.2f}" in text
    assert a.grade in text


def test_demo_fallback_sample_is_the_same_document():
    html = (REPO / "web" / "index.html").read_text()
    m = re.search(r'<script id="sample" type="application/json">(.*?)</script>', html, re.S)
    embedded = Analysis.from_dict(json.loads(m.group(1).replace("<\\/", "</")))
    committed = Analysis.from_json((REPO / "evals" / "results" / "analyses" / "nano" / "eq_012.json").read_text())
    assert embedded.total_cost == committed.total_cost and embedded.grade == committed.grade
    assert [c.id for c in embedded.clauses] == [c.id for c in committed.clauses]


def test_demo_names_the_honest_failures_and_the_offline_proof():
    text = DEMO.read_text()
    assert "unshare -rn" in text
    assert "15 of 23" in text and "56/58" in text
    assert "synthetic" in text
    assert "Do not delete `.cache/`" in text
