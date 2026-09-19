"""evals/parse_compare.py: parse-level stats, two-column aggregation, markdown, CLI. Offline only."""

import json

import parse_compare as pc
import run_eval as ev
from clause.types import TextBlock
from test_pipeline import BLOCKS
from test_run_eval import FlaggingClient, GOLD, SPAN_BALLOON

BLOCK_OBJS = [TextBlock.from_dict(b) for b in BLOCKS]
GOLD_ONE = {**GOLD, "traps": ["T_BALLOON"], "trap_spans": {"T_BALLOON": SPAN_BALLOON}}


# ---- parse stats ----------------------------------------------------------


def test_parse_stats_counts_kinds_and_finds_span_in_one_clause():
    s = pc.parse_stats(GOLD_ONE, BLOCK_OBJS)
    assert s["n_blocks"] == 6 and s["kinds"]["heading"] == 1 and s["kinds"]["footnote"] == 1
    assert s["n_clauses"] > 0
    sp = s["spans"]["T_BALLOON"]
    assert sp["in_raw_text"] and sp["clause_match"] == "substring" and sp["n_clauses_containing"] == 1


def test_parse_stats_reports_lost_and_split_spans():
    lost = pc.parse_stats(GOLD_ONE, [b for b in BLOCK_OBJS if "balloon" not in b.text])
    assert not lost["spans"]["T_BALLOON"]["in_raw_text"]
    # Span present in the text but broken across two blocks -> two clauses, neither contains it whole.
    a, b = SPAN_BALLOON.split(" shall ")
    split = [TextBlock(page=1, text=a + " shall."), TextBlock(page=1, text=b.capitalize())]
    s = pc.parse_stats(GOLD_ONE, split)
    assert not s["spans"]["T_BALLOON"]["in_raw_text"]  # "shall." vs "shall be" -> not a literal substring either
    agg = pc.aggregate_parse_stats({"d1": lost, "d2": pc.parse_stats(GOLD_ONE, BLOCK_OBJS), "d3": {"error": "x"}})
    assert (agg["n_docs"], agg["spans_planted"], agg["spans_in_raw_text"], agg["spans_in_one_clause"], agg["spans_lost"]) == (2, 2, 1, 1, 1)
    assert agg["kinds_total"]["table_row"] == 0


def test_aggregate_parse_stats_counts_split_spans():
    whole = pc.parse_stats(GOLD_ONE, BLOCK_OBJS)
    whole["spans"]["T_BALLOON"]["n_clauses_containing"] = 0  # in text, but no single clause holds it
    agg = pc.aggregate_parse_stats({"d": whole})
    assert agg["spans_split"] == 1 and agg["spans_in_one_clause"] == 0
    whole["spans"]["T_BALLOON"]["n_clauses_containing"] = 2
    assert pc.aggregate_parse_stats({"d": whole})["spans_duplicated"] == 1


def test_collect_parse_stats_uses_baseline_or_vlm(tmp_path, monkeypatch):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    gold = {**GOLD_ONE, "pdf": str(pdf)}
    monkeypatch.setattr(pc, "parse_pdf_baseline", lambda p: BLOCK_OBJS)
    monkeypatch.setattr(pc, "parse_pdf", lambda p, c: BLOCK_OBJS[:1])
    monkeypatch.setattr(ev, "REPO", tmp_path)
    assert pc.collect_parse_stats([gold], None, "baseline", log=lambda s: None)["eq_t01"]["n_blocks"] == 6
    assert pc.collect_parse_stats([gold], None, "vlm", log=lambda s: None)["eq_t01"]["n_blocks"] == 1

    def boom(p):
        raise OSError("bad pdf")
    monkeypatch.setattr(pc, "parse_pdf_baseline", boom)
    assert "error" in pc.collect_parse_stats([gold], None, "baseline", log=lambda s: None)["eq_t01"]


# ---- compare + markdown ---------------------------------------------------


def _cmp():
    from test_run_eval import GOOD_CLAUSES, analysis, clause
    from dataclasses import replace
    golden = [GOLD]
    meta = {"parser": "x", "provider": "mock", "model": "m", "timestamp": "now"}
    good = analysis(GOOD_CLAUSES)
    worse = analysis([replace(c, risk="standard", category="standard") if c.id == "c002" else c for c in GOOD_CLAUSES]
                     + [clause(9, "Fees per schedule.", risk="high", category="origination_fee")])
    results = {"vlm": ev.score(golden, {"eq_t01": good}, meta), "baseline": ev.score(golden, {"eq_t01": worse}, meta)}
    parse = {"vlm": {"eq_t01": pc.parse_stats(GOLD, BLOCK_OBJS)},
             "baseline": {"eq_t01": pc.parse_stats(GOLD, [b for b in BLOCK_OBJS if b.kind != "footnote"])}}
    return pc.compare(golden, results, parse, {"provider": "mock", "model": "m", "timestamp": "now", "docs": ["eq_t01"]})


def test_compare_has_both_columns_and_golden_context():
    cmp = _cmp()
    assert cmp["eval"]["vlm"]["trap_detection"]["recall"] == 1.0
    assert cmp["eval"]["baseline"]["trap_detection"]["recall"] == 0.5
    assert cmp["eval"]["baseline"]["trap_detection"]["trap_docs_off_trap_total"] == 1
    assert cmp["parse"]["vlm"]["aggregate"]["kinds_total"]["footnote"] == 1
    assert cmp["parse"]["baseline"]["aggregate"]["kinds_total"]["footnote"] == 0
    assert cmp["golden"]["eq_t01"]["traps"] == ["T_BALLOON", "T_VENUE"]


def test_markdown_two_columns():
    md = pc.render_markdown(_cmp())
    assert "| **Recall (all traps)** | **2/2 = 100%** | **1/2 = 50%** |" in md
    assert "| `T_BALLOON` | 1/1 | 0/1 |" in md
    assert "| Off-trap flags on trap docs | 0 | 1 |" in md
    assert "| `footnote` blocks (total) | 1 | 0 |" in md
    assert "| eq_t01 |  | BALLOON, VENUE | 2/2 | 1/2 | correct | correct |" in md


# ---- CLI ------------------------------------------------------------------


def test_cli_runs_baseline_reuses_vlm_and_rescoring_makes_no_calls(tmp_path, monkeypatch, capsys):
    pdf = tmp_path / "data" / "docs" / "eq_t01.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 not really")
    gold = {**GOLD_ONE, "pdf": str(pdf), "terms": {**GOLD["terms"], "fees": []},
            "computed": {"total_cost": 48 * 229.31 + 2820.0, "effective_apr": None}}
    gp = tmp_path / "golden.json"
    gp.write_text(json.dumps([gold]))
    out_dir = tmp_path / "results"
    client = FlaggingClient(log_path=tmp_path / "l.jsonl")
    monkeypatch.setattr(ev, "REPO", tmp_path)
    monkeypatch.setattr(ev, "get_client", lambda **kw: client)
    import clause.parse_baseline
    monkeypatch.setattr(clause.parse_baseline, "parse_pdf_baseline", lambda p: BLOCK_OBJS)  # run_eval imports lazily
    monkeypatch.setattr(pc, "parse_pdf_baseline", lambda p: BLOCK_OBJS)
    monkeypatch.setattr(pc, "parse_pdf", lambda p, c: c.parse_document(p))

    # Without a saved VLM column, that side is an error but the run still completes.
    assert pc.main(["--golden", str(gp), "--out-dir", str(out_dir)]) == 0
    cmp = json.loads((out_dir / "parse_compare.json").read_text())
    assert cmp["eval"]["baseline"]["trap_detection"]["recall"] == 1.0
    assert cmp["eval"]["vlm"]["errors"] == ["eq_t01"]
    assert (out_dir / "eval_baseline.md").exists() and not (out_dir / "eval_vlm.md").exists()

    # --run-vlm fills the other column and writes eval_vlm.*
    assert pc.main(["--golden", str(gp), "--out-dir", str(out_dir), "--run-vlm"]) == 0
    cmp = json.loads((out_dir / "parse_compare.json").read_text())
    assert cmp["eval"]["vlm"]["trap_detection"]["recall"] == 1.0
    assert (out_dir / "eval_vlm.md").exists()
    md = (out_dir / "parse_compare.md").read_text()
    assert "| **Recall (all traps)** | **1/1 = 100%** | **1/1 = 100%** |" in md
    assert "VLM parse: recall 1/1" in capsys.readouterr().err

    n = len(client.calls)
    assert pc.main(["--golden", str(gp), "--out-dir", str(out_dir), "--rescore"]) == 0
    assert sum(1 for c in client.calls[n:] if c["kind"] == "complete") == 0  # parse-stat calls only
