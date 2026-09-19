"""evals/cost_table.py: log filtering, stage attribution, per-model aggregation, markdown, CLI. Offline only."""

import json

import pytest

import cost_table as ct
from clause import config


def row(model="claude-opus-5", provider="anthropic", kind="complete", stage=None, latency=1.0,
        in_tok=100, out_tok=10, cached=False, attempts=1, ts=1000.0):
    r = {"ts": ts, "provider": provider, "model": model, "kind": kind, "latency_s": latency,
         "input_tokens": in_tok, "output_tokens": out_tok, "cached": cached, "attempts": attempts}
    if stage is not None:
        r["stage"] = stage
    return r


@pytest.fixture
def log(tmp_path):
    rows = [
        row(stage="classify", latency=2.0, in_tok=2000, out_tok=500),
        row(stage="classify", latency=4.0, in_tok=2000, out_tok=500, attempts=2),
        row(stage="extract", latency=6.0, in_tok=9000, out_tok=600),
        row(kind="parse", latency=70.0, in_tok=20000, out_tok=8000),
        row(cached=True, latency=0.0, in_tok=None, out_tok=None, attempts=0),    # cache hit: excluded
        row(in_tok=2100, out_tok=400, latency=3.0),                              # legacy untagged -> classify
        row(in_tok=8000, out_tok=300, latency=5.0),                              # legacy untagged -> extract
        row(model=config.NVIDIA_NANO_MODEL, provider="nvidia", stage="classify", latency=0.5, in_tok=2000, out_tok=400, ts=2000.0),
        row(model=config.NVIDIA_NANO_MODEL, provider="nvidia", stage="extract", latency=0.9, in_tok=9000, out_tok=500, ts=2000.0),
    ]
    p = tmp_path / "calls.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    return p


def test_read_calls_drops_cache_hits_and_honours_since(log):
    rows = ct.read_calls(log)
    assert len(rows) == 8 and not any(r["cached"] for r in rows)
    assert len(ct.read_calls(log, since=1500.0)) == 2


def test_stage_attribution_prefers_tag_then_token_split():
    assert ct.stage_of(row(stage="extract", in_tok=10)) == "extract"
    assert ct.stage_of(row(kind="parse")) == "parse"
    assert ct.stage_of(row(in_tok=2000)) == "classify"
    assert ct.stage_of(row(in_tok=ct.LEGACY_EXTRACT_MIN_INPUT_TOKENS)) == "extract"
    assert ct.stage_of(row(in_tok=None)) == "classify"


def test_percentile_and_cost():
    assert ct.percentile([], 50) is None
    assert ct.percentile([3.0], 95) == 3.0
    assert ct.percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert ct.cost_usd("claude-opus-5", 1_000_000, 1_000_000) == 30.0
    assert ct.cost_usd("unknown-model", 1, 1) is None


def test_summarize_per_model(log, monkeypatch):
    monkeypatch.setitem(config.PRICING, config.NVIDIA_NANO_MODEL, (0.10, 0.40))
    s = ct.summarize(ct.read_calls(log))
    opus = s["configs"]["claude-opus-5"]
    assert opus["documents"] == 2 and opus["model_calls"] == 5 and opus["model_calls_per_doc"] == 2.5
    assert opus["stages"]["classify"]["calls"] == 3 and opus["stages"]["classify"]["retried_calls"] == 1
    assert opus["stages"]["extract"]["calls"] == 2 and opus["stages"]["parse"]["calls"] == 1
    # classify: 6100 in / 1400 out; extract: 17000 in / 900 out -> (23100*5 + 2300*25)/1e6
    assert opus["model_cost_usd"] == pytest.approx(0.173, abs=1e-4)
    assert opus["cost_per_doc_usd"] == pytest.approx(0.0865, abs=1e-4)
    assert opus["parse_cost_per_doc_usd"] == pytest.approx((20000 * 5 + 8000 * 25) / 1e6, abs=1e-4)
    assert opus["model_latency_p50_s"] == 4.0
    nano = s["configs"][config.NVIDIA_NANO_MODEL]
    assert nano["documents"] == 1 and nano["cost_per_doc_usd"] == pytest.approx((11000 * 0.1 + 900 * 0.4) / 1e6, abs=1e-4)
    assert nano["eval"] is None


def test_eval_summary_joins_recall_and_clauses(tmp_path):
    ev = {"aggregate": {"n_scored": 2, "trap_detection": {"recall": 0.5, "caught": 1, "planted": 2, "clean_high_total": 0}},
          "docs": [{"doc_id": "a", "n_clauses": 60}, {"doc_id": "b", "n_clauses": 80}, {"doc_id": "c", "error": "x"}]}
    p = tmp_path / "eval_x.json"
    p.write_text(json.dumps(ev))
    e = ct._eval_summary(p)
    assert (e["clauses"], e["clauses_per_doc"], e["traps"], e["trap_recall"]) == (140, 70.0, "1/2", 0.5)
    assert ct._eval_summary(tmp_path / "missing.json") is None


def test_cli_writes_json_and_markdown(log, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ct, "DEFAULT_EVALS", {})
    ev = {"aggregate": {"n_scored": 1, "trap_detection": {"recall": 1.0, "caught": 3, "planted": 3, "clean_high_total": 1}},
          "docs": [{"doc_id": "a", "n_clauses": 70}]}
    evp = tmp_path / "eval_vlm.json"
    evp.write_text(json.dumps(ev))
    out = tmp_path / "res"
    assert ct.main(["--log", str(log), "--out-dir", str(out), "--eval", f"claude-opus-5={evp}"]) == 0
    md = (out / "cost_latency.md").read_text()
    assert f"| `claude-opus-5` | `{config.NVIDIA_NANO_MODEL}` |" in md
    assert "| **Trap recall** | **3/3 = 100%** | – |" in md
    assert "n/a (set config.PRICING)" in md  # Nano has no price configured
    assert f"Recall column missing for: `{config.NVIDIA_NANO_MODEL}`" in md
    assert "| `claude-opus-5` | parse | 1 | 0 |" in md
    j = json.loads((out / "cost_latency.json").read_text())
    assert j["configs"]["claude-opus-5"]["eval"]["clauses"] == 70
    assert "cost/doc" in capsys.readouterr().err


def test_cli_since_iso_and_bad_eval_spec(log, tmp_path):
    out = tmp_path / "res"
    assert ct.main(["--log", str(log), "--out-dir", str(out), "--since", "1970-01-01T00:25"]) == 0  # ts 1500
    j = json.loads((out / "cost_latency.json").read_text())
    assert list(j["configs"]) == [config.NVIDIA_NANO_MODEL]
    with pytest.raises(SystemExit):
        ct.main(["--log", str(log), "--out-dir", str(out), "--eval", "nofile"])
    with pytest.raises(SystemExit):
        ct.main(["--log", str(tmp_path / "nope.jsonl"), "--out-dir", str(out)])
