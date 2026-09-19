"""Core types shared by every pipeline stage. See PLAN.md §3.1.

All types are plain dataclasses with `to_dict` / `from_dict` so an `Analysis`
can round-trip through JSON (API responses, fixtures, eval results) without
losing anything.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from typing import Any, get_args, get_origin, get_type_hints


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _from_dict(cls: type, data: Any) -> Any:
    """Rebuild a dataclass (recursively) from a JSON-compatible dict."""
    if data is None or not is_dataclass(cls):
        return data
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce(hints[f.name], data[f.name])
    return cls(**kwargs)


def _coerce(hint: Any, value: Any) -> Any:
    if value is None:
        return None
    origin = get_origin(hint)
    args = get_args(hint)
    # Optional[X] / X | None -> unwrap to X
    if origin is not None and type(None) in args:
        inner = [a for a in args if a is not type(None)]
        return _coerce(inner[0], value) if len(inner) == 1 else value
    if origin is list:
        return [_coerce(args[0], v) for v in value]
    if origin is dict:
        return {k: _coerce(args[1], v) for k, v in value.items()}
    if origin is tuple or hint is tuple:
        return tuple(value)
    if is_dataclass(hint):
        return _from_dict(hint, value)
    return value


class _Serializable:
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        return _from_dict(cls, data)

    @classmethod
    def from_json(cls, s: str):
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# §3.1 core types
# ---------------------------------------------------------------------------


@dataclass
class SourceSpan(_Serializable):
    page: int                          # 1-indexed
    text: str                          # exact substring from the document
    bbox: tuple | None = None          # (x0, y0, x1, y1) if available from parse


@dataclass
class TextBlock(_Serializable):
    """Output of the parse stage: one block of text in reading order."""
    page: int                          # 1-indexed
    text: str
    bbox: tuple | None = None          # (x0, y0, x1, y1) if available
    kind: str = "paragraph"            # "paragraph" | "heading" | "table_row" | "footnote"


@dataclass
class Clause(_Serializable):
    id: str                            # "c001", stable, assigned at segmentation
    text: str                          # full clause text
    span: SourceSpan
    category: str | None = None        # filled by classify
    risk: str | None = None            # "high" | "medium" | "low" | "standard"
    plain_language: str | None = None  # one sentence, filled by classify
    confidence: float | None = None
    dollar_impact: float | None = None # filled by compute, NOT by the model


@dataclass
class Fee(_Serializable):
    kind: str                          # "origination" | "late" | "prepayment" | "doc" | "other"
    basis: str                         # "flat" | "percent_of_principal" | "percent_of_payment"
    value: float
    span: SourceSpan
    confidence: float
    financed: bool = False             # added to the amount financed (vs paid at closing)


@dataclass
class LoanTerms(_Serializable):
    principal: float | None = None
    stated_apr: float | None = None    # the advertised rate
    term_months: int | None = None
    payment_amount: float | None = None
    payment_frequency: str | None = None  # "monthly" | "weekly" | "biweekly"
    balloon_amount: float | None = None
    fees: list[Fee] = field(default_factory=list)
    spans: dict[str, SourceSpan] = field(default_factory=dict)   # field name -> where we found it
    confidences: dict[str, float] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class Analysis(_Serializable):
    terms: LoanTerms
    clauses: list[Clause]
    total_cost: float | None
    effective_apr: float | None
    cost_above_stated: float | None    # total cost minus what stated_apr implies
    grade: str                         # "A".."F"
    incomplete: bool
    warnings: list[str] = field(default_factory=list)
    questions_to_ask: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)  # stage name -> seconds
