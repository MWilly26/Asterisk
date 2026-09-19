"""Extract stage: document text -> LoanTerms. PLAN §3.2, §2.4.

The model locates and types numbers. This module then checks every number
against the source text: a value that does not literally appear anywhere in
the document gets confidence UNVERIFIED_CONFIDENCE (0.3) and a "verify this"
treatment downstream. Nothing is ever defaulted to zero.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from clause import config
from clause.client import ModelClient
from clause.types import Fee, LoanTerms, SourceSpan, TextBlock

log = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "extract.txt"

SCALAR_FIELDS = ("principal", "stated_apr", "term_months", "payment_amount", "payment_frequency", "balloon_amount")
NUMERIC_FIELDS = ("principal", "stated_apr", "term_months", "payment_amount", "balloon_amount")
PERCENT_FIELDS = ("stated_apr",)
FEE_KINDS = ["origination", "late", "prepayment", "doc", "other"]
FEE_BASES = ["flat", "percent_of_principal", "percent_of_payment"]


def _field(type_: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": [type_, "null"]},
            "quote": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["value", "quote", "confidence"],
        "additionalProperties": False,
    }


SCHEMA = {
    "type": "object",
    "properties": {
        "principal": _field("number"),
        "stated_apr": _field("number"),
        "term_months": _field("integer"),
        "payment_amount": _field("number"),
        "payment_frequency": {
            "type": "object",
            "properties": {
                "value": {"anyOf": [{"type": "string", "enum": ["monthly", "biweekly", "weekly"]}, {"type": "null"}]},
                "quote": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
            },
            "required": ["value", "quote", "confidence"],
            "additionalProperties": False,
        },
        "balloon_amount": _field("number"),
        "fees": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": FEE_KINDS},
                    "basis": {"type": "string", "enum": FEE_BASES},
                    "value": {"type": "number"},
                    "financed": {"type": "boolean"},
                    "quote": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["kind", "basis", "value", "financed", "quote", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": list(SCALAR_FIELDS) + ["fees"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Source-text validation
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def document_text(blocks: list[TextBlock]) -> str:
    """What the model sees: page-tagged, normalized text."""
    out = []
    page = None
    for b in blocks:
        if b.page != page:
            page = b.page
            out.append(f"\n[page {page}]")
        out.append(_norm(b.text))
    return "\n".join(out).strip()


def number_variants(value: float | int, *, percent: bool = False) -> list[str]:
    """Ways a number is typically written in a contract, most specific first."""
    if percent:
        p = value * 100
        cands = [f"{p:.2f}%", f"{p:.1f}%", f"{p:g}%", f"{p:.2f} percent", f"{p:g} percent"]
    elif float(value).is_integer() and not isinstance(value, float):
        cands = [f"{int(value):,}", f"{int(value)}"]
    else:
        cands = [f"${value:,.2f}", f"{value:,.2f}", f"${value:,.0f}" if float(value).is_integer() else "",
                 f"{value:,}", f"{value:g}"]
    seen, out = set(), []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def number_in_text(value: float | int, text: str, *, percent: bool = False) -> bool:
    return any(v in text for v in number_variants(value, percent=percent))


def locate(quote: str | None, blocks: list[TextBlock]) -> SourceSpan | None:
    """Find the block containing the quote; returns a span with page + bbox, or None."""
    if not quote:
        return None
    q = _norm(quote)
    for b in blocks:
        if q in _norm(b.text):
            return SourceSpan(page=b.page, text=q, bbox=b.bbox)
    # Quote may straddle two blocks: fall back to the page of the first block that has its start.
    head = q[:40]
    for b in blocks:
        if head in _norm(b.text):
            return SourceSpan(page=b.page, text=q, bbox=None)
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _as_fraction(v: float | None) -> float | None:
    """The prompt asks for fractions; forgive a model that returns 7.9 for 7.9%."""
    return v / 100 if v is not None and v > 1 else v


def extract_terms(blocks: list[TextBlock], client: ModelClient, *, model: str | None = None) -> LoanTerms:
    model = model or config.default_model()
    text = document_text(blocks)
    messages = [{"role": "system", "content": PROMPT_PATH.read_text()},
                {"role": "user", "content": "Document:\n\n" + text}]
    raw = client.complete(model=model, messages=messages, json_schema=SCHEMA, max_tokens=4096, stage="extract")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("extract: unparseable output; treating every field as missing")
        return LoanTerms(missing_fields=list(SCALAR_FIELDS))
    return validate(data, blocks, text)


def validate(data: dict, blocks: list[TextBlock], text: str | None = None) -> LoanTerms:
    """Turn raw model JSON into LoanTerms, checking every number against the source."""
    text = text if text is not None else document_text(blocks)
    ntext = _norm(text)
    terms = LoanTerms()

    for f in SCALAR_FIELDS:
        item = data.get(f) or {}
        value = item.get("value") if isinstance(item, dict) else None
        if value is None or value == "":
            terms.missing_fields.append(f)
            continue
        conf = float(min(max(item.get("confidence", 0.5), 0.0), 1.0))
        quote = item.get("quote")
        if f in PERCENT_FIELDS:
            value = _as_fraction(float(value))
        elif f == "term_months":
            value = int(value)
        elif f != "payment_frequency":
            value = float(value)

        verified = (value in ("monthly", "biweekly", "weekly") and value in ntext.lower()) if f == "payment_frequency" \
            else number_in_text(value, ntext, percent=f in PERCENT_FIELDS)
        span = locate(quote, blocks)
        if not verified:
            conf = min(conf, config.UNVERIFIED_CONFIDENCE)
        elif span is None:
            conf = min(conf, config.LOW_CONFIDENCE)  # number is real but the quote isn't
        setattr(terms, f, value)
        terms.confidences[f] = conf
        terms.spans[f] = span or SourceSpan(page=0, text=_norm(quote) if quote else "", bbox=None)

    for item in data.get("fees") or []:
        if not isinstance(item, dict) or item.get("kind") not in FEE_KINDS or item.get("basis") not in FEE_BASES:
            continue
        try:
            value = float(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        is_pct = item["basis"] != "flat"
        if is_pct:
            value = _as_fraction(value)
        conf = float(min(max(item.get("confidence", 0.5), 0.0), 1.0))
        quote = item.get("quote")
        span = locate(quote, blocks)
        if not number_in_text(value, ntext, percent=is_pct):
            conf = min(conf, config.UNVERIFIED_CONFIDENCE)
        elif span is None:
            conf = min(conf, config.LOW_CONFIDENCE)
        terms.fees.append(Fee(kind=item["kind"], basis=item["basis"], value=value,
                              span=span or SourceSpan(page=0, text=_norm(quote) if quote else "", bbox=None),
                              confidence=conf, financed=bool(item.get("financed", False))))
    return terms
