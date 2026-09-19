"""api/main.py: health, upload validation, sync analyze, SSE stream. Mock client only."""

import json

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.main import app, model_client
from clause.types import Analysis
from test_pipeline import ScriptedClient

PDF = b"%PDF-1.4\n% not a real pdf, the mock client never reads it\n"


@pytest.fixture
def client(tmp_path):
    mock = ScriptedClient(log_path=tmp_path / "l.jsonl")
    app.dependency_overrides[model_client] = lambda: mock
    with TestClient(app) as tc:
        tc.mock = mock
        yield tc
    app.dependency_overrides.clear()


def upload(name=b"doc.pdf", data=PDF, ctype="application/pdf"):
    return {"file": (name.decode() if isinstance(name, bytes) else name, data, ctype)}


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok" and "provider" in r.json() and "model" in r.json()


def test_analyze_returns_analysis_json(client):
    r = client.post("/analyze", files=upload())
    assert r.status_code == 200, r.text
    a = Analysis.from_dict(r.json())
    assert a.terms.principal == 9400.0 and not a.incomplete
    assert a.total_cost == pytest.approx(48 * 229.31 + 2820.0, abs=0.01)
    assert set(a.timings) >= {"parse", "classify", "total"}


@pytest.mark.parametrize("files, why", [
    (upload(name="notes.txt", data=b"hello", ctype="text/plain"), "extension"),
    (upload(name="doc.pdf", data=b"hello world"), "header"),
    (upload(name="doc.pdf", data=b""), "empty"),
    (upload(name="image.png", data=b"\x89PNG", ctype="image/png"), "png"),
])
def test_rejects_non_pdf_with_400(client, files, why):
    r = client.post("/analyze", files=files)
    assert r.status_code == 400, why
    assert "detail" in r.json()
    assert not client.mock.calls, "rejected upload must not reach the model"


def test_rejects_oversize_upload(client, monkeypatch):
    monkeypatch.setattr(api_main, "MAX_UPLOAD_BYTES", 16)
    r = client.post("/analyze", files=upload(data=PDF))
    assert r.status_code == 400 and "MB" in r.json()["detail"]


def test_missing_file_field_is_422(client):
    assert client.post("/analyze").status_code == 422


def read_sse(response):
    events, ev, data = [], None, []
    for line in response.iter_lines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
        elif line == "" and ev is not None:
            events.append((ev, json.loads("".join(data))))
            ev, data = None, []
    return events


def test_stream_emits_stage_events_then_result(client):
    with client.stream("POST", "/analyze/stream", files=upload()) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        events = read_sse(r)
    kinds = [e for e, _ in events]
    assert kinds[-1] == "result" and "error" not in kinds
    stages = [(d["stage"], d["event"]) for e, d in events if e == "stage"]
    assert stages[0] == ("parse", "start") and stages[-1] == ("compute", "done")
    assert [s for s, ev in stages if ev == "done"] == ["parse", "segment", "classify", "extract", "compute"]
    a = Analysis.from_dict(events[-1][1])
    assert a.terms.principal == 9400.0


def test_stream_rejects_non_pdf_before_streaming(client):
    r = client.post("/analyze/stream", files=upload(name="x.txt", data=b"nope", ctype="text/plain"))
    assert r.status_code == 400


def test_stream_reports_pipeline_failure_as_error_event(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(api_main, "analyze", boom)
    with client.stream("POST", "/analyze/stream", files=upload()) as r:
        events = read_sse(r)
    assert events[-1][0] == "error" and "model exploded" in events[-1][1]["detail"]


def test_index_serves_frontend_or_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(api_main, "WEB_DIR", tmp_path)
    assert client.get("/").status_code == 404
    (tmp_path / "index.html").write_text("<html><body>Clause</body></html>")
    r = client.get("/")
    assert r.status_code == 200 and "Clause" in r.text


def test_fresh_form_field_selects_a_cache_bypassing_client(tmp_path, monkeypatch):
    """`fresh=true` builds a second client with cache_read=False and keeps it on app.state;
    the default path keeps using the normal cached client."""
    import api.main as m
    built = []

    def fake_get_client(**kw):
        c = ScriptedClient(log_path=tmp_path / "l.jsonl", **kw)
        built.append(c)
        return c

    monkeypatch.setattr(m, "get_client", fake_get_client)
    app.dependency_overrides.clear()
    with TestClient(app) as tc:
        for k in ("client", "fresh_client"):
            if hasattr(app.state, k):
                delattr(app.state, k)
        assert tc.post("/analyze", files=upload()).status_code == 200
        assert tc.post("/analyze", files=upload(), data={"fresh": "true"}).status_code == 200
        assert tc.post("/analyze", files=upload(), data={"fresh": "true"}).status_code == 200
        assert tc.post("/analyze", files=upload()).status_code == 200
    assert [c.cache_read for c in built] == [True, False]   # one client each, reused
