"""Classify stage: label each clause with category, risk, plain-language gloss. PLAN §3.2.

Batches of CLASSIFY_BATCH_SIZE clauses per model call. A batch whose output
is unparseable is retried once (with a note that changes the cache key);
if it fails again, those clauses degrade to category/risk = None rather
than crashing the pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from clause import config
from clause.client import ModelClient
from clause.types import Clause

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "classify.txt"

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": list(config.CATEGORIES)},
                    "risk": {"type": "string", "enum": config.RISK_LEVELS},
                    "plain_language": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "category", "risk", "plain_language", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def system_prompt() -> str:
    cats = "\n".join(f'- "{k}": {v}' for k, v in config.CATEGORIES.items())
    return PROMPT_PATH.read_text().replace("{categories}", cats)


def _batch_message(batch: list[Clause]) -> str:
    return "Clauses:\n\n" + "\n\n".join(f"[{c.id}]\n{c.text}" for c in batch)


def _parse(raw: str, batch: list[Clause]) -> dict[str, dict] | None:
    """Return {clause_id: result} or None if the output is unusable."""
    try:
        data = json.loads(raw)
        results = data["results"]
        assert isinstance(results, list)
    except (json.JSONDecodeError, KeyError, TypeError, AssertionError):
        return None
    ids = {c.id for c in batch}
    out: dict[str, dict] = {}
    for r in results:
        if not isinstance(r, dict) or r.get("id") not in ids:
            continue
        out[r["id"]] = r
    return out or None


def _apply(c: Clause, r: dict | None) -> Clause:
    if r is None:
        return replace(c, category=None, risk=None, plain_language=None, confidence=None)
    cat = r.get("category") if r.get("category") in config.CATEGORIES else None
    risk = r.get("risk") if r.get("risk") in config.RISK_LEVELS else None
    conf = r.get("confidence")
    conf = float(min(max(conf, 0.0), 1.0)) if isinstance(conf, (int, float)) else None
    pl = r.get("plain_language")
    return replace(c, category=cat, risk=risk, confidence=conf,
                   plain_language=pl.strip() if isinstance(pl, str) and pl.strip() else None)


def classify(clauses: list[Clause], client: ModelClient, *, model: str | None = None,
             batch_size: int = config.CLASSIFY_BATCH_SIZE) -> list[Clause]:
    """Returns a new list; does not mutate input."""
    model = model or config.default_model()
    system = system_prompt()
    out: list[Clause] = []
    for start in range(0, len(clauses), batch_size):
        batch = clauses[start:start + batch_size]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": _batch_message(batch)}]
        results = None
        for attempt in (1, 2):
            raw = client.complete(model=model, messages=messages, json_schema=SCHEMA, max_tokens=4096, stage="classify")
            results = _parse(raw, batch)
            if results is not None:
                break
            log.warning("classify: unparseable output for batch at %s (attempt %d)", batch[0].id, attempt)
            # A different message → different cache key, so the retry really re-asks.
            messages = messages + [{"role": "user", "content":
                                    "Your previous reply was not valid JSON matching the schema. Reply again."}]
        out.extend(_apply(c, (results or {}).get(c.id)) for c in batch)
    return out
