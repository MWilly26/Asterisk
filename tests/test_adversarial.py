"""§6.5 classifier evaluator. No network calls."""

import json

import adversarial as adv
import pytest
from test_pipeline import BLOCKS
from test_run_eval import FlaggingClient, SPAN_BALLOON


def _gold(doc_id, condition):
    return {"doc_id": doc_id, "pdf": f"unused/{doc_id}.pdf", "pair_id": "pair_01",
            "condition": condition, "traps": ["T_BALLOON"],
            "trap_spans": {"T_BALLOON": SPAN_BALLOON}}


def test_run_save_reload_and_score_with_flagging_client(tmp_path, monkeypatch):
    monkeypatch.setattr(adv, "REPO", tmp_path)
    golden = [_gold("ctl_001", "control"), _gold("adv_001", "adversarial")]
    parser = lambda path: [__import__("clause.types", fromlist=["TextBlock"]).TextBlock.from_dict(b) for b in BLOCKS]  # noqa: E731
    analyses_dir = tmp_path / "analyses"
    client = FlaggingClient(log_path=tmp_path / "calls.jsonl")
    analyses = adv.run_corpus(golden, client, parser=parser, analyses_dir=analyses_dir, log=lambda _: None)
    assert all(isinstance(value, list) for value in analyses.values())
    assert adv.load_analyses(golden, analyses_dir) == analyses

    results = adv.score(golden, analyses, {"provider": "mock", "model": "flagger", "timestamp": "now"})
    conditions = results["aggregate"]["conditions"]
    assert conditions["control"]["recall"] == 1.0
    assert conditions["adversarial"]["recall"] == 1.0
    assert conditions["control"]["complete"] and conditions["adversarial"]["complete"]
    assert results["aggregate"]["recall_delta"] == 0.0


def test_score_records_misses_and_errors():
    golden = [_gold("ctl_001", "control"), _gold("adv_001", "adversarial")]
    analyses = {"ctl_001": RuntimeError("boom"), "adv_001": []}
    results = adv.score(golden, analyses, {"provider": "mock", "model": "m", "timestamp": "now"})
    assert results["aggregate"]["errors"] == ["ctl_001"]
    assert results["aggregate"]["recall_delta"] is None
    assert results["docs"][1]["caught"] == 0
    assert results["docs"][1]["traps"][0]["span_match"] == "none"


def test_markdown_and_json_report_both_conditions(tmp_path):
    golden = [_gold("ctl_001", "control"), _gold("adv_001", "adversarial")]
    results = adv.score(golden, {"ctl_001": [], "adv_001": []},
                        {"provider": "mock", "model": "m", "timestamp": "now"})
    markdown = adv.render_markdown(results)
    assert "Without manipulation (control)" in markdown
    assert "With manipulation (adversarial)" in markdown
    assert "strict span + risk + category rule" in markdown
    json_path, markdown_path = adv.write_results(results, tmp_path)
    assert json.loads(json_path.read_text()) == results
    assert markdown_path.read_text() == markdown


def test_cli_rejects_unknown_resume_document(tmp_path):
    golden_path = tmp_path / "golden.json"
    golden_path.write_text(json.dumps([_gold("ctl_001", "control")]))
    with pytest.raises(SystemExit, match="unknown doc ids"):
        adv.main(["--golden", str(golden_path), "--out-dir", str(tmp_path), "--docs", "missing"])
