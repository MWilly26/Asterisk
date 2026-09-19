"""Parse stage: PDF -> ordered TextBlocks with page numbers. PLAN §3.2.

The provider-specific work (nemotron-parse on NVIDIA, a document block on
Anthropic) lives in the client. This module owns the normalization both
backends get: reading order, no empty blocks, one table row per block.
"""

from __future__ import annotations

import re
from pathlib import Path

from clause.client import ModelClient, get_client
from clause.types import TextBlock

_WS = re.compile(r"\s+")


def normalize_blocks(blocks: list[TextBlock]) -> list[TextBlock]:
    """Stable-sort by page, collapse whitespace, drop empties, split multi-line table cells."""
    out: list[TextBlock] = []
    for b in sorted(blocks, key=lambda b: b.page):
        if b.kind == "table_row" and "\n" in b.text:
            rows = [r for r in b.text.splitlines() if r.strip()]
        else:
            rows = [b.text]
        for r in rows:
            t = _WS.sub(" ", r).strip()
            if t:
                out.append(TextBlock(page=b.page, text=t, bbox=b.bbox, kind=b.kind))
    return out


def parse_pdf(path: Path, client: ModelClient | None = None) -> list[TextBlock]:
    """VLM parse (nemotron-parse or Claude document input). Cached per PDF hash by the client."""
    client = client or get_client()
    return normalize_blocks(client.parse_document(Path(path)))
