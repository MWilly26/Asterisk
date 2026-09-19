"""pdfplumber text extraction. Used ONLY for the §6.3 parse-layer comparison.

Deliberately simple: pull text lines per page, merge consecutive lines into
blocks at sentence-ish boundaries, flag lines that look like headings. Tables
and footnotes get whatever pdfplumber gives them, which is the point of the
comparison.
"""

from __future__ import annotations

import re
from pathlib import Path

import pdfplumber

from clause.types import TextBlock

_HEADING = re.compile(r"^(\d{1,2}\.\s+[A-Z]|Section \d+\.\s|ARTICLE [IVXLC]+\s|SCHEDULE [A-Z]\s)", re.I)
_ENDS_BLOCK = re.compile(r"[.:;]\s*$")


def _is_heading(text: str) -> bool:
    return bool(_HEADING.match(text)) and len(text) < 80


def parse_pdf_baseline(path: Path) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    with pdfplumber.open(str(path)) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            cur: list[dict] = []

            def flush() -> None:
                if not cur:
                    return
                text = " ".join(l["text"].strip() for l in cur)
                x0 = min(l["x0"] for l in cur)
                x1 = max(l["x1"] for l in cur)
                top = min(l["top"] for l in cur)
                bottom = max(l["bottom"] for l in cur)
                kind = "heading" if len(cur) == 1 and _is_heading(text) else "paragraph"
                blocks.append(TextBlock(page=pno, text=text, bbox=(x0, top, x1, bottom), kind=kind))
                cur.clear()

            for line in page.extract_text_lines():
                t = line["text"].strip()
                if not t:
                    flush()
                    continue
                if _is_heading(t):
                    flush()
                    cur.append(line)
                    flush()
                    continue
                cur.append(line)
                if _ENDS_BLOCK.search(t):
                    flush()
            flush()
    return blocks
