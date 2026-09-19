"""One client for every model call. See PLAN §3.3.

`ModelClient` handles the parts that are the same for every provider:
retry with backoff, on-disk response caching, and per-call logging to
`runs/calls.jsonl`. Subclasses implement `_complete_raw` / `_parse_raw`.

`MockClient` replays recorded responses from `tests/fixtures/` (or an
in-memory list) and never touches the network. All tests use it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from clause import config
from clause.types import TextBlock


class RetryableError(Exception):
    """Raised by a backend for 429 / 5xx so the shared retry loop can catch it."""


@dataclass
class CallRecord:
    """One row of runs/calls.jsonl."""
    ts: float
    provider: str
    model: str
    kind: str                 # "complete" | "parse"
    latency_s: float
    input_tokens: int | None
    output_tokens: int | None
    cached: bool
    attempts: int
    stage: str | None = None  # "classify" | "extract" | ... — set by the caller, for the §6.4 cost table


class ModelClient:
    """Base class. Do not instantiate directly — use `get_client()`."""

    provider: str = "base"

    def __init__(self, *, cache_dir: Path | None = None, log_path: Path | None = None,
                 use_cache: bool = True, sleep: Callable[[float], None] = time.sleep):
        self.cache_dir = Path(cache_dir) if cache_dir else config.CACHE_DIR
        self.log_path = Path(log_path) if log_path else config.CALL_LOG
        self.use_cache = use_cache
        self._sleep = sleep

    # ---- public API ---------------------------------------------------------

    def complete(self, *, model: str, messages: list[dict],
                 json_schema: dict | None = None, max_tokens: int = 2048,
                 stage: str | None = None) -> str:
        """`stage` only labels the call-log row; it is not part of the cache key."""
        request = {"kind": "complete", "provider": self.provider, "model": model,
                   "messages": messages, "json_schema": json_schema, "max_tokens": max_tokens}
        return self._cached_call(request, lambda: self._complete_raw(
            model=model, messages=messages, json_schema=json_schema, max_tokens=max_tokens), stage=stage)

    def parse_document(self, path: Path) -> list[TextBlock]:
        path = Path(path)
        data = path.read_bytes()
        request = {"kind": "parse", "provider": self.provider, "model": self.parse_model(),
                   "sha256": hashlib.sha256(data).hexdigest()}
        raw = self._cached_call(request, lambda: self._parse_raw(data))
        return [TextBlock.from_dict(b) for b in json.loads(raw)]

    def parse_model(self) -> str:
        return "none"

    # ---- backend hooks (return (text, input_tokens, output_tokens)) ----------

    def _complete_raw(self, *, model: str, messages: list[dict],
                      json_schema: dict | None, max_tokens: int) -> tuple[str, int | None, int | None]:
        raise NotImplementedError

    def _parse_raw(self, pdf_bytes: bytes) -> tuple[str, int | None, int | None]:
        raise NotImplementedError

    # ---- shared machinery ----------------------------------------------------

    @staticmethod
    def request_key(request: dict) -> str:
        return hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()

    def _cached_call(self, request: dict, fn: Callable[[], tuple[str, int | None, int | None]],
                     *, stage: str | None = None) -> str:
        key = self.request_key(request)
        cache_file = self.cache_dir / f"{key}.json"
        if self.use_cache and cache_file.exists():
            text = json.loads(cache_file.read_text())["response"]
            self._log(request, latency=0.0, in_tok=None, out_tok=None, cached=True, attempts=0, stage=stage)
            return text

        t0 = time.perf_counter()
        text, in_tok, out_tok, attempts = self._with_retry(fn)
        latency = time.perf_counter() - t0
        self._log(request, latency=latency, in_tok=in_tok, out_tok=out_tok, cached=False, attempts=attempts, stage=stage)

        if self.use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"request": request, "response": text}))
        return text

    def _with_retry(self, fn: Callable[[], tuple[str, int | None, int | None]]):
        last: Exception | None = None
        for attempt in range(1, config.MAX_ATTEMPTS + 1):
            try:
                text, in_tok, out_tok = fn()
                return text, in_tok, out_tok, attempt
            except RetryableError as e:
                last = e
                if attempt < config.MAX_ATTEMPTS:
                    delay = config.BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                    self._sleep(delay)
        assert last is not None
        raise last

    def _log(self, request: dict, *, latency: float, in_tok: int | None, out_tok: int | None,
             cached: bool, attempts: int, stage: str | None = None) -> None:
        rec = CallRecord(ts=time.time(), provider=self.provider, model=request["model"],
                         kind=request["kind"], latency_s=round(latency, 4),
                         input_tokens=in_tok, output_tokens=out_tok, cached=cached, attempts=attempts,
                         stage=stage or request["kind"])
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a") as f:
            f.write(json.dumps(rec.__dict__) + "\n")


# ---------------------------------------------------------------------------
# Anthropic backend (development)
# ---------------------------------------------------------------------------

_PARSE_PROMPT = (
    "Extract every piece of text from this PDF into an ordered list of blocks, "
    "preserving reading order. Each table row is its own block with cells separated "
    "by ' | '. Footnotes and fine print are separate blocks. Do not summarize, "
    "paraphrase, or omit anything; copy the text exactly."
)

_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "text": {"type": "string"},
                    "kind": {"type": "string", "enum": ["paragraph", "heading", "table_row", "footnote"]},
                },
                "required": ["page", "text", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}


class AnthropicClient(ModelClient):
    provider = "anthropic"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        import anthropic  # lazy so tests never need the SDK configured
        self._anthropic = anthropic
        # SDK retries off: the shared loop owns retry so every attempt is logged.
        self._client = anthropic.Anthropic(max_retries=0, timeout=config.REQUEST_TIMEOUT_S)

    def parse_model(self) -> str:
        return config.ANTHROPIC_MODEL

    def _call(self, **params: Any) -> tuple[str, int | None, int | None]:
        a = self._anthropic
        try:
            r = self._client.messages.create(**params)
        except a.RateLimitError as e:
            raise RetryableError(str(e)) from e
        except a.APIStatusError as e:
            if e.status_code >= 500:
                raise RetryableError(str(e)) from e
            raise
        except a.APIConnectionError as e:
            raise RetryableError(str(e)) from e
        if r.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {r.stop_details}")
        text = "".join(b.text for b in r.content if b.type == "text")
        return text, r.usage.input_tokens, r.usage.output_tokens

    def _complete_raw(self, *, model, messages, json_schema, max_tokens):
        system = [m["content"] for m in messages if m["role"] == "system"]
        params: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens,
            "messages": [m for m in messages if m["role"] != "system"],
        }
        if system:
            params["system"] = "\n\n".join(system)
        if json_schema is not None:
            params["output_config"] = {"format": {"type": "json_schema", "schema": json_schema}}
        return self._call(**params)

    def _parse_raw(self, pdf_bytes: bytes):
        text, i, o = self._call(
            model=config.ANTHROPIC_MODEL, max_tokens=16000,
            messages=[{"role": "user", "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                "data": base64.standard_b64encode(pdf_bytes).decode()}},
                {"type": "text", "text": _PARSE_PROMPT},
            ]}],
            output_config={"format": {"type": "json_schema", "schema": _PARSE_SCHEMA}},
        )
        return json.dumps(json.loads(text)["blocks"]), i, o


# ---------------------------------------------------------------------------
# NVIDIA backend (final build)
# ---------------------------------------------------------------------------


class NvidiaClient(ModelClient):
    provider = "nvidia"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        import os
        import httpx
        key = os.environ.get("NVIDIA_API_KEY")
        if not key:
            raise RuntimeError("NVIDIA_API_KEY is not set")
        self._http = httpx.Client(
            base_url=config.NVIDIA_BASE_URL, timeout=config.REQUEST_TIMEOUT_S,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )

    def parse_model(self) -> str:
        return config.NVIDIA_PARSE_MODEL

    def _complete_raw(self, *, model, messages, json_schema, max_tokens):
        body: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens}
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "out", "schema": json_schema}}
        r = self._http.post("/chat/completions", json=body)
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"{r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        d = r.json()
        usage = d.get("usage") or {}
        return (d["choices"][0]["message"]["content"],
                usage.get("prompt_tokens"), usage.get("completion_tokens"))

    def _parse_raw(self, pdf_bytes: bytes):
        """nemotron-parse: one chat call per rasterized page, `markdown_bbox` tool.

        Contract (NIM docs): content is a single image_url data URL; the parsed
        layout comes back as a JSON array in tool_calls[0].function.arguments with
        {bbox:{xmin,ymin,xmax,ymax}, text, type}, bbox normalized 0-1, origin top-left.
        """
        blocks: list[dict] = []
        in_tok = out_tok = 0
        for pno, png in enumerate(_rasterize(pdf_bytes), start=1):
            body = {
                "model": config.NVIDIA_PARSE_MODEL,
                "tools": [{"type": "function", "function": {"name": "markdown_bbox"}}],
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}],
                "temperature": 0.0,
            }

            def call(body=body):
                r = self._http.post("/chat/completions", json=body)
                if r.status_code == 429 or r.status_code >= 500:
                    raise RetryableError(f"{r.status_code}: {r.text[:200]}")
                r.raise_for_status()
                d = r.json()
                msg = d["choices"][0]["message"]
                if msg.get("tool_calls"):
                    raw = msg["tool_calls"][0]["function"]["arguments"]
                else:
                    raw = msg.get("content") or "[]"
                usage = d.get("usage") or {}
                return raw, usage.get("prompt_tokens"), usage.get("completion_tokens")

            raw, i, o = self._with_retry(call)[:3]
            in_tok += i or 0
            out_tok += o or 0
            for el in _flatten(json.loads(raw)):
                blocks.extend(_nemotron_element_to_blocks(el, pno))
        return json.dumps(blocks), in_tok, out_tok


_NEMOTRON_KIND = {
    "Section-header": "heading", "Title": "heading",
    "Table": "table_row",
    "Footnote": "footnote",
}


def _rasterize(pdf_bytes: bytes, dpi: int = 150) -> list[bytes]:
    import io
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(pdf_bytes)
    out = []
    for page in pdf:
        img = page.render(scale=dpi / 72).to_pil()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(buf.getvalue())
    return out


def _flatten(parsed) -> list[dict]:
    """The tool result is a list of elements; some server versions nest one list per page."""
    if isinstance(parsed, dict):
        return [parsed]
    out: list[dict] = []
    for el in parsed:
        out.extend(_flatten(el) if isinstance(el, list) else [el])
    return out


def _nemotron_element_to_blocks(el: dict, page: int) -> list[dict]:
    text = (el.get("text") or "").strip()
    if not text:
        return []
    kind = _NEMOTRON_KIND.get(el.get("type", ""), "paragraph")
    bb = el.get("bbox") or {}
    bbox = [bb.get("xmin"), bb.get("ymin"), bb.get("xmax"), bb.get("ymax")] if bb else None
    if kind == "table_row":
        rows = [r.strip() for r in text.splitlines()
                if r.strip() and not set(r.strip()) <= set("|-: ")]  # drop markdown separator rows
        return [{"page": page, "text": " | ".join(c.strip() for c in r.strip("|").split("|")),
                 "bbox": bbox, "kind": kind} for r in rows]
    return [{"page": page, "text": text, "bbox": bbox, "kind": kind}]


# ---------------------------------------------------------------------------
# Mock backend (tests)
# ---------------------------------------------------------------------------


class MockClient(ModelClient):
    """Replays recorded responses. Never touches the network.

    Lookup order for each request:
      1. `responses` queue (FIFO) if given — simplest for unit tests.
      2. `<fixture_dir>/<request_key>.json` with a "response" field.
    Raises KeyError with the request key if neither has it, so you can record it.
    """

    provider = "mock"

    def __init__(self, responses: list[str] | None = None,
                 fixture_dir: Path | None = None, **kwargs: Any):
        kwargs.setdefault("use_cache", False)
        kwargs.setdefault("log_path", Path(kwargs.get("log_path") or config.REPO_ROOT / "runs" / "calls_mock.jsonl"))
        super().__init__(**kwargs)
        self.responses = list(responses or [])
        self.fixture_dir = Path(fixture_dir) if fixture_dir else config.REPO_ROOT / "tests" / "fixtures"
        self.calls: list[dict] = []
        self.fail_first: int = 0   # tests set this to simulate transient errors

    def parse_model(self) -> str:
        return "mock-parse"

    def _lookup(self, request: dict) -> str:
        if self.fail_first > 0:
            self.fail_first -= 1
            raise RetryableError("simulated 429")
        if self.responses:
            return self.responses.pop(0)
        key = self.request_key(request)
        f = self.fixture_dir / f"{key}.json"
        if f.exists():
            return json.loads(f.read_text())["response"]
        raise KeyError(f"no mock response for request {key}")

    def _cached_call(self, request, fn, **kw):
        self.calls.append(request)
        return super()._cached_call(request, fn, **kw)

    def _complete_raw(self, *, model, messages, json_schema, max_tokens):
        return self._lookup({"kind": "complete", "provider": self.provider, "model": model,
                             "messages": messages, "json_schema": json_schema,
                             "max_tokens": max_tokens}), 0, 0

    def _parse_raw(self, pdf_bytes: bytes):
        return self._lookup({"kind": "parse", "provider": self.provider, "model": self.parse_model(),
                             "sha256": hashlib.sha256(pdf_bytes).hexdigest()}), 0, 0


# ---------------------------------------------------------------------------


def get_client(**kwargs: Any) -> ModelClient:
    """Return the backend selected by CLAUSE_PROVIDER."""
    if config.PROVIDER == "nvidia":
        return NvidiaClient(**kwargs)
    if config.PROVIDER == "anthropic":
        return AnthropicClient(**kwargs)
    raise ValueError(f"unknown CLAUSE_PROVIDER {config.PROVIDER!r}")
