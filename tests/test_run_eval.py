"""evals/run_eval.py: trap matching, field outcomes, aggregation, markdown, CLI. Offline only."""

import json
from dataclasses import replace

import pytest

import run_eval as ev
from clause.types import Analysis, Clause, Fee, LoanTerms, SourceSpan
from test_pipeline import BLOCKS, ScriptedClient  # noqa: F401 — reuse the scripted mock


class FlaggingClient(ScriptedClient):
    """ScriptedClient, but classifies any clause mentioning a balloon as high/balloon."""

    def _complete_raw(self, *, model, messages, json_schema, max_tokens):
        user = messages[-1]["content"]
        if user.startswith("Document:"):
            return super()._complete_raw(model=model, messages=messages, json_schema=json_schema, max_tokens=max_tokens)
        results = []
        for chunk in user.split("\n\n")[1:]:
            cid, _, text = chunk.partition("\n")
            hit = "balloon" in text.lower()
            results.append({"id": cid[1:-1], "category": "balloon" if hit else "standard",
                            "risk": "high" if hit else "standard", "plain_language": "Plain.", "confidence": 0.9})
        return json.dumps({"results": results}), 0, 0


def clause(i, text, risk="standard", category="standard", page=1):
    return Clause(id=f"c{i:03d}", text=text, span=SourceSpan(page=page, text=text),
                  category=category, risk=risk, plain_language="x", confidence=0.9)


SPAN_BALLOON = "A final balloon payment of $2,820.00 shall be due with the final installment."
SPAN_VENUE = "Borrower irrevocably submits to the exclusive jurisdiction of the courts of Delaware."

GOLD = {
    "doc_id": "eq_t01", "pdf": "data/docs/eq_t01.pdf", "ugly": None,
    "terms": {"principal": 9400.0, "stated_apr": 0.079, "term_months": 48, "payment_amount": 229.31,
              "payment_frequency": "monthly", "balloon_amount": 2820.0,
              "fees": [{"kind": "origination", "basis": "percent_of_principal", "value": 0.035, "financed": False},
                       {"kind": "late", "basis": "flat", "value": 35.0, "financed": False}]},
    "computed": {"total_cost": 14156.88, "effective_apr": 0.1361},
    "traps": ["T_BALLOON", "T_VENUE"],
    "trap_spans": {"T_BALLOON": SPAN_BALLOON, "T_VENUE": SPAN_VENUE},
}
GOLD_CLEAN = {**GOLD, "doc_id": "eq_t02", "traps": [], "trap_spans": {},
              "terms": {**GOLD["terms"], "balloon_amount": None}}


def fee(kind, basis, value, conf=0.95, financed=False):
    return Fee(kind=kind, basis=basis, value=value, span=SourceSpan(page=1, text="q"), confidence=conf, financed=financed)


def terms(**over):
    base = dict(principal=9400.0, stated_apr=0.079, term_months=48, payment_amount=229.31,
                payment_frequency="monthly", balloon_amount=2820.0,
                fees=[fee("origination", "percent_of_principal", 0.035), fee("late", "flat", 35.0)],
                confidences={"principal": 0.95, "balloon_amount": 0.9})
    base.update(over)
    return LoanTerms(**base)


def analysis(clauses, t=None, total_cost=14156.88, eff=0.1361, incomplete=False):
    return Analysis(terms=t or terms(), clauses=clauses, total_cost=total_cost, effective_apr=eff,
                    cost_above_stated=0.0, grade="C", incomplete=incomplete, timings={"total": 1.0, "classify": 0.9})


GOOD_CLAUSES = [
    clause(1, "The principal amount financed is $9,400.00."),
    clause(2, "Footnote: " + SPAN_BALLOON + " See Schedule C.", risk="high", category="balloon", page=2),
    clause(3, "Governing law. " + SPAN_VENUE, risk="high", category="venue"),
    clause(4, "Notices shall be in writing."),
]


# ---- span matching --------------------------------------------------------


def test_find_span_substring_ignores_whitespace_and_quotes():
    c, how = ev.find_span_clause(GOOD_CLAUSES, "A final  balloon payment of $2,820.00\nshall be due with the final installment.")
    assert (c.id, how) == ("c002", "substring")


def test_find_span_overlap_fallback_and_none():
    reflowed = "A final balloon pay- ment of $2,820.00 shall be due together with the final installment."
    c, how = ev.find_span_clause(GOOD_CLAUSES, reflowed)
    assert (c.id, how) == ("c002", "overlap")
    c, how = ev.find_span_clause(GOOD_CLAUSES, "Completely unrelated words about weather forecasts today.")
    assert (c, how) == (None, "none")


# ---- §6.1 -----------------------------------------------------------------


def test_score_traps_caught_wrong_category_and_not_flagged():
    a = analysis(GOOD_CLAUSES)
    r = {t["trap"]: t for t in ev.score_traps(GOLD, a)}
    assert r["T_BALLOON"]["caught"] and r["T_VENUE"]["caught"]

    wrong_cat = [replace(c, category="standard") if c.id == "c002" else c for c in GOOD_CLAUSES]
    r = {t["trap"]: t for t in ev.score_traps(GOLD, analysis(wrong_cat))}
    assert not r["T_BALLOON"]["caught"] and r["T_BALLOON"]["flagged_wrong_category"]
    assert not r["T_BALLOON"]["category_flagged_elsewhere"]

    unflagged = [replace(c, risk="low") if c.id == "c003" else c for c in GOOD_CLAUSES]
    r = {t["trap"]: t for t in ev.score_traps(GOLD, analysis(unflagged))}
    assert not r["T_VENUE"]["caught"] and r["T_VENUE"]["not_flagged"]
    assert r["T_VENUE"]["risk"] == "low"


def test_score_traps_span_missing_from_clauses():
    r = ev.score_traps(GOLD, analysis(GOOD_CLAUSES[:1]))
    assert all(t["span_match"] == "none" and not t["caught"] and t["clause_id"] is None for t in r)


def test_false_positives_count_high_medium_and_off_trap():
    cl = GOOD_CLAUSES + [clause(5, "Assignment.", risk="medium", category="standard"),
                         clause(6, "Fees per schedule.", risk="high", category="origination_fee")]
    fp = ev.score_false_positives(GOLD, analysis(cl))
    assert (fp["high"], fp["medium"], fp["off_trap"]) == (3, 1, 2)
    fp = ev.score_false_positives(GOLD_CLEAN, analysis(cl))
    assert fp["off_trap"] == 4  # every flag is off-trap on a clean doc


# ---- §6.2 -----------------------------------------------------------------


def test_field_outcomes_cover_every_case():
    t = terms(principal=9401.0, stated_apr=None, term_months=48, balloon_amount=2820.0,
              confidences={"principal": 0.2}, missing_fields=["stated_apr"])
    f = ev.score_fields(GOLD["terms"], t)
    assert f["principal"]["outcome"] == "wrong" and f["principal"]["low_confidence"]
    assert f["stated_apr"]["outcome"] == "missing" and f["stated_apr"]["in_missing_fields"]
    assert f["term_months"]["outcome"] == "correct"
    assert f["balloon_amount"]["outcome"] == "correct"
    f = ev.score_fields(GOLD_CLEAN["terms"], t)
    assert f["balloon_amount"]["outcome"] == "spurious"
    f = ev.score_fields(GOLD_CLEAN["terms"], terms(balloon_amount=None))
    assert f["balloon_amount"]["outcome"] == "correct_absent"


def test_field_tolerance_is_tight():
    assert ev.score_fields(GOLD["terms"], terms(payment_amount=229.314))["payment_amount"]["outcome"] == "correct"
    assert ev.score_fields(GOLD["terms"], terms(payment_amount=229.32))["payment_amount"]["outcome"] == "wrong"
    assert ev.score_fields(GOLD["terms"], terms(stated_apr=0.0791))["stated_apr"]["outcome"] == "wrong"


def test_fee_matching_precision_recall_value_and_financed():
    got = [fee("origination", "percent_of_principal", 0.035, financed=True),  # financed flag wrong
           fee("doc", "flat", 125.0)]                                         # spurious; late missed
    r = ev.score_fees(GOLD["terms"]["fees"], got)
    assert r["missed_kinds"] == ["late"] and [s["kind"] for s in r["spurious"]] == ["doc"]
    m = r["matched"][0]
    assert m["kind"] == "origination" and m["value_correct"] and not m["financed_correct"]
    r = ev.score_fees(GOLD["terms"]["fees"], [fee("origination", "flat", 329.0), fee("late", "flat", 35.0)])
    assert [m["value_correct"] for m in r["matched"]] == [False, True]


def test_computed_outcomes():
    c = ev.score_computed(GOLD["computed"], analysis(GOOD_CLAUSES))
    assert c["total_cost"]["outcome"] == "correct" and c["effective_apr"]["outcome"] == "correct"
    c = ev.score_computed(GOLD["computed"], analysis(GOOD_CLAUSES, total_cost=None, eff=None, incomplete=True))
    assert c["total_cost"]["outcome"] == "missing" and c["incomplete"]
    c = ev.score_computed(GOLD["computed"], analysis(GOOD_CLAUSES, total_cost=14000.0))
    assert c["total_cost"]["outcome"] == "wrong"


# ---- aggregate + markdown -------------------------------------------------


def _results():
    bad = [replace(c, risk="low") if c.id == "c003" else c for c in GOOD_CLAUSES]
    analyses = {
        "eq_t01": analysis(bad, t=terms(principal=9000.0, confidences={"principal": 0.3})),
        "eq_t02": analysis([clause(1, "Fees per schedule.", risk="high", category="origination_fee")],
                           t=terms(balloon_amount=None)),
        "eq_t03": RuntimeError("boom"),
    }
    golden = [GOLD, GOLD_CLEAN, {**GOLD_CLEAN, "doc_id": "eq_t03"}]
    return ev.score(golden, analyses, {"parser": "vlm", "provider": "mock", "model": "m", "timestamp": "now"})


def test_aggregate_recall_by_type_misses_and_clean_counts():
    r = _results()
    a = r["aggregate"]
    assert (a["n_docs"], a["n_scored"], a["errors"]) == (3, 2, ["eq_t03"])
    td = a["trap_detection"]
    assert (td["planted"], td["caught"], td["recall"]) == (2, 1, 0.5)
    assert td["by_type"]["T_BALLOON"]["recall"] == 1.0 and td["by_type"]["T_VENUE"]["recall"] == 0.0
    assert td["by_type"]["T_VENUE"]["not_flagged"] == 1
    assert [m["trap"] for m in td["misses"]] == ["T_VENUE"]
    assert td["clean_docs"] == {"eq_t02": {"high": 1, "medium": 0, "n_clauses": 1}}
    assert td["clean_high_total"] == 1


def test_aggregate_fields_split_wrong_flagged_from_confident():
    a = _results()["aggregate"]
    p = a["fields"]["principal"]
    assert (p["correct"], p["wrong"], p["wrong_flagged"], p["wrong_confident"]) == (1, 1, 1, 0)
    assert p["exact_rate"] == 0.5
    b = a["fields"]["balloon_amount"]
    assert (b["correct"], b["correct_absent"], b["exact_rate"]) == (1, 1, 1.0)
    assert a["fees"]["recall"] == 1.0 and a["fees"]["precision"] == 1.0
    assert a["computed"]["total_cost"]["exact_rate"] == 1.0


def test_markdown_has_every_section_and_error_row():
    md = ev.render_markdown(_results())
    for needle in ("## §6.1 Trap detection", "`T_VENUE` | 1 | 0 | 0%", "**Missed traps:**", "eq_t02 | 1 | 1 | 0",
                   "## §6.2 Term extraction", "`principal` | 1 | 0 | 0 | 1 | 0 | 0 | 50%",
                   "**Fees:** precision 100%", "## Per document", "ERROR: RuntimeError('boom')"):
        assert needle in md, needle


# ---- running --------------------------------------------------------------


@pytest.fixture
def mini_corpus(tmp_path):
    pdf = tmp_path / "data" / "docs" / "eq_t01.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 not really")
    gold = {**GOLD, "pdf": str(pdf), "traps": ["T_BALLOON"], "trap_spans": {"T_BALLOON": SPAN_BALLOON},
            "terms": {**GOLD["terms"], "fees": []}, "computed": {"total_cost": 48 * 229.31 + 2820.0, "effective_apr": None}}
    gp = tmp_path / "golden.json"
    gp.write_text(json.dumps([gold]))
    return gp


def test_run_corpus_saves_analyses_and_records_failures(mini_corpus, tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "REPO", tmp_path)  # golden "pdf" is absolute here, REPO / abs == abs
    golden = ev.load_golden(mini_corpus)
    client = ScriptedClient(log_path=tmp_path / "l.jsonl")
    out = ev.run_corpus(golden, client, analyses_dir=tmp_path / "an", log=lambda s: None)
    assert isinstance(out["eq_t01"], Analysis)
    assert Analysis.from_json((tmp_path / "an" / "eq_t01.json").read_text()) == out["eq_t01"]
    reloaded = ev.load_analyses(golden, tmp_path / "an")
    assert reloaded["eq_t01"] == out["eq_t01"]

    def boom(*a, **k):
        raise RuntimeError("no model")
    monkeypatch.setattr(ev, "analyze", boom)
    out = ev.run_corpus(golden, client, log=lambda s: None)
    assert isinstance(out["eq_t01"], RuntimeError)


def test_custom_parser_is_forwarded(mini_corpus, tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(ev, "REPO", tmp_path)
    parser = lambda p, c=None: seen.append(p) or [__import__("clause.types", fromlist=["TextBlock"]).TextBlock.from_dict(b) for b in BLOCKS]  # noqa: E731
    ev.run_corpus(ev.load_golden(mini_corpus), ScriptedClient(log_path=tmp_path / "l.jsonl"), parser=parser, log=lambda s: None)
    assert len(seen) == 1


def test_load_golden_filters_and_rejects_unknown(mini_corpus):
    assert [g["doc_id"] for g in ev.load_golden(mini_corpus, ["eq_t01"])] == ["eq_t01"]
    with pytest.raises(SystemExit):
        ev.load_golden(mini_corpus, ["eq_zzz"])


def test_cli_end_to_end_then_rescore(mini_corpus, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ev, "REPO", tmp_path)
    client = FlaggingClient(log_path=tmp_path / "l.jsonl")
    monkeypatch.setattr(ev, "get_client", lambda **kw: client)
    out_dir = tmp_path / "results"
    assert ev.main(["--golden", str(mini_corpus), "--out-dir", str(out_dir), "--name", "t"]) == 0
    res = json.loads((out_dir / "eval_t.json").read_text())
    assert res["meta"]["parser"] == "vlm" and not res["meta"]["rescored"]
    assert res["aggregate"]["trap_detection"]["recall"] == 1.0  # FlaggingClient labels the balloon clause
    assert res["aggregate"]["computed"]["total_cost"]["correct"] == 1
    assert (out_dir / "analyses" / "t" / "eq_t01.json").exists()
    assert "trap recall 1/1 = 100%" in capsys.readouterr().err
    n_calls = len(client.calls)

    # --rescore reads the saved analyses and makes no model calls
    assert ev.main(["--golden", str(mini_corpus), "--out-dir", str(out_dir), "--name", "t", "--rescore"]) == 0
    assert len(client.calls) == n_calls
    res2 = json.loads((out_dir / "eval_t.json").read_text())
    assert res2["meta"]["rescored"] and res2["docs"][0]["traps"] == res["docs"][0]["traps"]
    md = (out_dir / "eval_t.md").read_text()
    assert "| `T_BALLOON` | 1 | 1 | 100%" in md


def test_rescore_partial_corpus_under_distinct_name(mini_corpus, tmp_path, monkeypatch):
    """A partial run can be re-scored under its own name (--analyses-dir) and the
    unsaved documents are reported as "not run" without any machine-specific path."""
    monkeypatch.setattr(ev, "REPO", tmp_path)
    client = FlaggingClient(log_path=tmp_path / "l.jsonl")
    monkeypatch.setattr(ev, "get_client", lambda **kw: client)
    out_dir = tmp_path / "results"
    assert ev.main(["--golden", str(mini_corpus), "--out-dir", str(out_dir), "--name", "t"]) == 0
    n_calls = len(client.calls)

    # a second golden doc that was never analyzed
    golden = json.loads(mini_corpus.read_text())
    golden.append({**golden[0], "doc_id": "eq_t02"})
    bigger = tmp_path / "golden2.json"
    bigger.write_text(json.dumps(golden))

    assert ev.main(["--golden", str(bigger), "--out-dir", str(out_dir), "--name", "t_partial",
                    "--analyses-dir", str(out_dir / "analyses" / "t"), "--rescore"]) == 0
    assert len(client.calls) == n_calls
    res = json.loads((out_dir / "eval_t_partial.json").read_text())
    agg = res["aggregate"]
    assert agg["n_docs"] == 2 and agg["n_scored"] == 1 and agg["errors"] == ["eq_t02"]
    err = res["docs"][1]["error"]
    assert "not run" in err and str(tmp_path) not in err
    md = (out_dir / "eval_t_partial.md").read_text()
    assert "1/2 documents scored" in md and "**Errors:** eq_t02" in md
