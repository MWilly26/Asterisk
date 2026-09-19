"""ModelClient shared machinery: caching, retry, logging. No network."""

import json

import pytest

from clause import config
from clause.client import MockClient, ModelClient, RetryableError


@pytest.fixture
def client(tmp_path):
    return MockClient(cache_dir=tmp_path / "cache", log_path=tmp_path / "calls.jsonl",
                      use_cache=True, sleep=lambda s: None)


MSG = [{"role": "user", "content": "hi"}]


def _log_rows(client):
    return [json.loads(l) for l in client.log_path.read_text().splitlines()]


def test_complete_returns_queued_response(client):
    client.responses = ["hello"]
    assert client.complete(model="m", messages=MSG) == "hello"


def test_identical_call_hits_cache(client):
    client.responses = ["first"]
    a = client.complete(model="m", messages=MSG)
    b = client.complete(model="m", messages=MSG)   # queue empty; must come from cache
    assert a == b == "first"
    rows = _log_rows(client)
    assert [r["cached"] for r in rows] == [False, True]
    assert len(list(client.cache_dir.glob("*.json"))) == 1


def test_cache_key_differs_by_provider_and_model():
    base = {"kind": "complete", "model": "m", "messages": MSG, "json_schema": None, "max_tokens": 1}
    k1 = ModelClient.request_key(base | {"provider": "anthropic"})
    k2 = ModelClient.request_key(base | {"provider": "nvidia"})
    k3 = ModelClient.request_key(base | {"provider": "anthropic", "model": "other"})
    assert len({k1, k2, k3}) == 3


def test_provider_options_participate_in_cache_key(tmp_path):
    class OptionsClient(MockClient):
        def complete_options(self, model):
            return {"thinking": False}

    c = OptionsClient(responses=["first"], cache_dir=tmp_path / "cache",
                      log_path=tmp_path / "calls.jsonl", use_cache=True)
    assert c.complete(model="m", messages=MSG) == "first"
    request = c.calls[0]
    assert request["provider_options"] == {"thinking": False}
    assert c.complete(model="m", messages=MSG) == "first"
    assert [r["cached"] for r in _log_rows(c)] == [False, True]


def test_cache_disabled_always_calls(client):
    client.use_cache = False
    client.responses = ["a", "b"]
    assert client.complete(model="m", messages=MSG) == "a"
    assert client.complete(model="m", messages=MSG) == "b"


def test_retries_then_succeeds(client):
    client.fail_first = 2
    client.responses = ["ok"]
    assert client.complete(model="m", messages=MSG) == "ok"
    assert _log_rows(client)[0]["attempts"] == 3


def test_gives_up_after_max_attempts(client):
    client.fail_first = config.MAX_ATTEMPTS
    client.responses = ["never"]
    with pytest.raises(RetryableError):
        client.complete(model="m", messages=MSG)


def test_backoff_is_exponential(tmp_path):
    delays = []
    c = MockClient(cache_dir=tmp_path, log_path=tmp_path / "l.jsonl", sleep=delays.append)
    c.fail_first = 2
    c.responses = ["ok"]
    c.complete(model="m", messages=MSG)
    assert len(delays) == 2
    assert 1.0 <= delays[0] < 1.3 and 2.0 <= delays[1] < 2.3


def test_retryable_error_can_request_longer_backoff(tmp_path):
    delays = []
    c = MockClient(cache_dir=tmp_path, log_path=tmp_path / "l.jsonl", sleep=delays.append)
    calls = 0

    def fn():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableError("busy", retry_after_s=10.0)
        return "ok", 1, 1

    assert c._with_retry(fn)[:3] == ("ok", 1, 1)
    assert delays == [10.0]


def test_log_row_shape(client):
    client.responses = ["x"]
    client.complete(model="m", messages=MSG, max_tokens=7)
    row = _log_rows(client)[0]
    assert set(row) == {"ts", "provider", "model", "kind", "latency_s",
                        "input_tokens", "output_tokens", "cached", "attempts", "stage"}
    assert row["stage"] == "complete"  # defaults to the kind when the caller doesn't tag it
    assert row["provider"] == "mock" and row["kind"] == "complete"


def test_stage_tag_is_logged_but_not_part_of_cache_key(client):
    client.responses = ["x", "y"]
    client.complete(model="m", messages=MSG, max_tokens=7, stage="classify")
    client.complete(model="m", messages=MSG, max_tokens=7, stage="extract")
    rows = _log_rows(client)
    assert [r["stage"] for r in rows] == ["classify", "extract"]
    assert client.calls[0] == client.calls[1]  # same request dict -> same cache key


def test_parse_document_returns_text_blocks(client, tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-fake")
    client.responses = [json.dumps([
        {"page": 1, "text": "Fee Schedule", "kind": "heading"},
        {"page": 1, "text": "Origination | 3.5%", "kind": "table_row"},
    ])]
    blocks = client.parse_document(pdf)
    assert [b.kind for b in blocks] == ["heading", "table_row"]
    assert blocks[1].page == 1 and blocks[1].bbox is None
    assert _log_rows(client)[0]["kind"] == "parse"


def test_fixture_replay(tmp_path):
    c = MockClient(fixture_dir=tmp_path / "fx", log_path=tmp_path / "l.jsonl")
    req = {"kind": "complete", "provider": "mock", "model": "m", "messages": MSG,
           "json_schema": None, "max_tokens": 5}
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx" / f"{ModelClient.request_key(req)}.json").write_text(
        json.dumps({"request": req, "response": "from fixture"}))
    assert c.complete(model="m", messages=MSG, max_tokens=5) == "from fixture"


def test_missing_fixture_raises_with_key(tmp_path):
    c = MockClient(fixture_dir=tmp_path, log_path=tmp_path / "l.jsonl")
    with pytest.raises(KeyError, match="no mock response"):
        c.complete(model="m", messages=MSG)


# --- NVIDIA endpoint routing (offline: no request is sent) --------------------

def _nvidia_client(monkeypatch, tmp_path, **cfg):
    from clause.client import NvidiaClient
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-real")
    for k, v in cfg.items():
        monkeypatch.setattr(config, k, v)
    return NvidiaClient(cache_dir=tmp_path / "cache", log_path=tmp_path / "l.jsonl", use_cache=False)


def test_nvidia_defaults_share_one_http_client(monkeypatch, tmp_path):
    c = _nvidia_client(monkeypatch, tmp_path, NVIDIA_BASE_URL=config.NVIDIA_HOSTED_URL,
                       NVIDIA_PARSE_BASE_URL=config.NVIDIA_HOSTED_URL)
    assert c._parse_http is c._http
    assert str(c._http.base_url).rstrip("/") == config.NVIDIA_HOSTED_URL


def test_nvidia_self_hosted_nano_keeps_parse_on_hosted_endpoint(monkeypatch, tmp_path):
    c = _nvidia_client(monkeypatch, tmp_path, NVIDIA_BASE_URL="http://10.0.0.5:8000/v1",
                       NVIDIA_PARSE_BASE_URL=config.NVIDIA_HOSTED_URL)
    assert c._parse_http is not c._http
    assert str(c._http.base_url).rstrip("/") == "http://10.0.0.5:8000/v1"
    assert str(c._parse_http.base_url).rstrip("/") == config.NVIDIA_HOSTED_URL
    assert c._http.headers["authorization"] == c._parse_http.headers["authorization"]


def test_nvidia_urls_and_model_come_from_env(tmp_path):
    """A blank value (as a sourced .env with `NVIDIA_BASE_URL=` produces) keeps the default."""
    import os
    import subprocess
    import sys
    code = ("from clause import config; "
            "print(config.NVIDIA_BASE_URL, config.NVIDIA_PARSE_BASE_URL, config.NVIDIA_NANO_MODEL)")
    env = {**os.environ, "NVIDIA_BASE_URL": "http://brev.example:8000/v1", "NVIDIA_PARSE_BASE_URL": "",
           "NVIDIA_NANO_MODEL": "local/nano"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stdout.split()
    assert out == ["http://brev.example:8000/v1", config.NVIDIA_HOSTED_URL, "local/nano"]
