"""pipeline.py: stage wiring, timings, progress callback, CLI. Mock client only."""

import json

import pytest

from clause import config, pipeline
from clause.client import MockClient
from clause.pipeline import STAGES, analyze, main
from clause.types import Analysis, TextBlock

# A tiny "document" as the parse stage would return it. Numbers match the
# extract response below so validation passes and compute produces totals.
BLOCKS = [
    {"page": 1, "text": "1. Amount Financed", "bbox": None, "kind": "heading"},
    {"page": 1, "text": "The principal amount financed under this Agreement is $9,400.00.", "bbox": None, "kind": "paragraph"},
    {"page": 1, "text": "Interest shall accrue at a fixed Annual Percentage Rate (APR) of 7.90%.", "bbox": None, "kind": "paragraph"},
    {"page": 1, "text": "Borrower shall repay in 48 consecutive monthly installments of $229.31 each.", "bbox": None, "kind": "paragraph"},
    {"page": 2, "text": "A final balloon payment of $2,820.00 shall be due with the final installment.", "bbox": None, "kind": "footnote"},
    {"page": 2, "text": "This Agreement is governed by the laws of the State of Delaware.", "bbox": None, "kind": "paragraph"},
]

EXTRACT = {
    "principal": {"value": 9400.0, "quote": "principal amount financed under this Agreement is $9,400.00", "confidence": 0.95},
    "stated_apr": {"value": 0.079, "quote": "Annual Percentage Rate (APR) of 7.90%", "confidence": 0.95},
    "term_months": {"value": 48, "quote": "48 consecutive monthly installments", "confidence": 0.95},
    "payment_amount": {"value": 229.31, "quote": "installments of $229.31 each", "confidence": 0.95},
    "payment_frequency": {"value": "monthly", "quote": "consecutive monthly installments", "confidence": 0.95},
    "balloon_amount": {"value": 2820.0, "quote": "A final balloon payment of $2,820.00", "confidence": 0.9},
    "fees": [],
}


def classify_response(ids):
    return json.dumps({"results": [
        {"id": i, "category": "balloon" if "balloon" in i else "standard",
         "risk": "standard", "plain_language": "Plain.", "confidence": 0.9} for i in ids]})


class ScriptedClient(MockClient):
    """Answers parse with BLOCKS, extract with EXTRACT, and classify per batch by clause id."""

    def _complete_raw(self, *, model, messages, json_schema, max_tokens):
        user = messages[-1]["content"]
        if user.startswith("Document:"):
            return json.dumps(EXTRACT), 0, 0
        ids = [line[1:-1] for line in user.splitlines() if line.startswith("[c") and line.endswith("]")]
        return classify_response(ids), 0, 0

    def _parse_raw(self, pdf_bytes):
        return json.dumps(BLOCKS), 0, 0


@pytest.fixture
def pdf(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 not really")
    return p


@pytest.fixture
def client(tmp_path):
    return ScriptedClient(log_path=tmp_path / "l.jsonl")


def test_analyze_runs_every_stage_and_records_timings(pdf, client):
    a = analyze(pdf, client)
    assert isinstance(a, Analysis)
    assert set(a.timings) == set(STAGES) | {"total"}
    assert all(v >= 0 for v in a.timings.values())
    assert a.timings["total"] >= sum(a.timings[s] for s in STAGES) - 1e-3


def test_analyze_produces_numbers_from_compute_not_model(pdf, client):
    a = analyze(pdf, client)
    assert a.terms.principal == 9400.0 and a.terms.balloon_amount == 2820.0
    assert not a.incomplete
    assert a.total_cost == pytest.approx(48 * 229.31 + 2820.0, abs=0.01)
    assert a.effective_apr is not None
    # every clause is classified and carries a source span from the document
    assert a.clauses and all(c.risk is not None for c in a.clauses)
    assert all(c.span.text in " ".join(b["text"] for b in BLOCKS) for c in a.clauses)


def test_progress_callback_sees_start_and_done_for_each_stage(pdf, client):
    events = []
    analyze(pdf, client, progress=lambda stage, ev, secs: events.append((stage, ev, secs)))
    assert [e[0] for e in events if e[1] == "start"] == list(STAGES)
    assert [e[0] for e in events if e[1] == "done"] == list(STAGES)
    assert all(secs is None for _, ev, secs in events if ev == "start")
    assert all(isinstance(secs, float) for _, ev, secs in events if ev == "done")


def test_custom_parser_is_used(pdf, client):
    seen = {}

    def parser(path, c=None):
        seen["path"], seen["client"] = path, c
        return [TextBlock.from_dict(b) for b in BLOCKS]

    analyze(pdf, client, parser=parser)
    assert seen == {"path": pdf, "client": client}
    assert not any(c["kind"] == "parse" for c in client.calls)


def test_analysis_round_trips_through_json(pdf, client):
    a = analyze(pdf, client)
    assert Analysis.from_json(a.to_json()) == a


def test_default_client_is_get_client(pdf, client, monkeypatch):
    monkeypatch.setattr(pipeline, "get_client", lambda **kw: client)
    a = analyze(pdf)
    assert a.timings["parse"] >= 0


def test_cli_prints_analysis_json(pdf, client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "get_client", lambda **kw: client)
    assert main([str(pdf)]) == 0
    out = capsys.readouterr()
    a = Analysis.from_json(out.out)
    assert a.grade in "ABCDEF" and "total" in a.timings
    assert "parse" in out.err and "compute" in out.err


def test_cli_out_file_and_quiet(pdf, client, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pipeline, "get_client", lambda **kw: client)
    dest = tmp_path / "a.json"
    assert main([str(pdf), "--out", str(dest), "-q"]) == 0
    out = capsys.readouterr()
    assert out.out == ""
    assert "parse" not in out.err
    assert Analysis.from_json(dest.read_text()).terms.principal == 9400.0


def test_cli_no_cache_flag_is_forwarded(pdf, client, monkeypatch):
    seen = {}

    def fake_get_client(**kw):
        seen.update(kw)
        return client

    monkeypatch.setattr(pipeline, "get_client", fake_get_client)
    main([str(pdf), "--no-cache", "-q"])
    assert seen == {"use_cache": False}


def test_cli_rejects_missing_file(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope.pdf"), "-q"])


def test_classify_parallel_preserves_order_and_batches(client):
    from clause.pipeline import classify_parallel
    from clause.types import Clause, SourceSpan
    cl = [Clause(id=f"c{i:03d}", text=f"Clause {i}.", span=SourceSpan(page=1, text=f"Clause {i}.")) for i in range(1, 21)]
    out = classify_parallel(cl, client, workers=3, batch_size=8)
    assert [c.id for c in out] == [c.id for c in cl]
    assert all(c.risk == "standard" for c in out)
    assert sum(1 for c in client.calls if c["kind"] == "complete") == 3  # 8 + 8 + 4
    assert all(c.risk is None for c in cl)  # input not mutated


def test_classify_parallel_single_worker_matches(client):
    from clause.pipeline import classify_parallel
    from clause.types import Clause, SourceSpan
    cl = [Clause(id=f"c{i:03d}", text=f"Clause {i}.", span=SourceSpan(page=1, text=f"Clause {i}.")) for i in range(1, 11)]
    assert classify_parallel(cl, client, workers=1) == classify_parallel(cl, client, workers=4)


def test_nvidia_uses_provider_specific_worker_limit(client):
    client.provider = "nvidia"
    assert pipeline._classification_workers(client) == config.NVIDIA_CLASSIFY_WORKERS
    assert pipeline._classification_batch_size(client) == config.NVIDIA_CLASSIFY_BATCH_SIZE
    client.provider = "mock"
    assert pipeline._classification_workers(client) == config.CLASSIFY_WORKERS
    assert pipeline._classification_batch_size(client) == config.CLASSIFY_BATCH_SIZE
