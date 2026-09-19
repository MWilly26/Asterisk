"""Deterministic clause segmentation. No model. PLAN §3.2.

TextBlocks (from either parser) -> numbered Clauses. Target: one clause is
one enforceable provision, small enough to classify in a batch and large
enough that a planted trap sentence never straddles two clauses.

Rules, in order:
  1. Drop noise: page numbers, and any short block that repeats (digits
     wildcarded) on three or more pages — running headers/footers. The same
     string is also stripped from inside other blocks.
  2. Drop headings — they are structure, not provisions.
  3. Re-join a paragraph that a page break split in two (previous block has no
     terminal punctuation and this one starts lowercase).
  4. Table rows and footnotes are one clause each.
  5. Paragraphs up to MAX_WORDS are one clause. Longer ones are packed
     sentence-by-sentence into chunks of at most MAX_WORDS; a single sentence
     is never split.
"""

from __future__ import annotations

import re

from clause.types import Clause, SourceSpan, TextBlock

MAX_WORDS = 90          # a paragraph longer than this gets chunked
CHUNK_WORDS = 70        # target size of each chunk
REPEAT_PAGES = 3        # a block seen on this many pages is a header/footer
FOOTER_MAX_WORDS = 20   # only short blocks can be headers/footers

_PAGE_NO = re.compile(r"^(page\s+\d+(\s+of\s+\d+)?|\d+)$", re.I)
_HEADING = re.compile(r"^(\d{1,2}\.\s+[A-Z]|Section \d+\.\s|ARTICLE [IVXLC]+\b|SCHEDULE [A-Z]\b)", re.I)
_TERMINAL = re.compile(r"[.!?:;\"')\]]\s*$")
# Sentence boundary: terminal punctuation, whitespace, then an uppercase/quote/paren opener.
# "$2,820.00" and "5:00 p.m." are safe because the next char is not an opener.
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[])")
_ABBREV = re.compile(r"\b(Inc|Corp|Co|Ltd|L\.P|LLC|No|U\.S|p\.m|a\.m|Mr|Ms|Dr|St)\.$")


def _words(s: str) -> int:
    return len(s.split())


def _is_heading(b: TextBlock) -> bool:
    return b.kind == "heading" or (bool(_HEADING.match(b.text)) and _words(b.text) <= 12)


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_SPLIT.split(text)
    out: list[str] = []
    for p in parts:
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    return [s.strip() for s in out if s.strip()]


def _chunk(text: str) -> list[str]:
    if _words(text) <= MAX_WORDS:
        return [text]
    chunks: list[str] = []
    cur: list[str] = []
    n = 0
    for s in _split_sentences(text):
        w = _words(s)
        if cur and n + w > CHUNK_WORDS:
            chunks.append(" ".join(cur))
            cur, n = [], 0
        cur.append(s)
        n += w
    if cur:
        chunks.append(" ".join(cur))
    return chunks


def segment(blocks: list[TextBlock]) -> list[Clause]:
    """Deterministic. Does not mutate input."""
    # 1. noise. Footers are matched with digits wildcarded ("Page 2" == "Page 3"),
    #    and stripped from inside blocks too, since a parser may glue a footer
    #    onto a paragraph that runs across the page break.
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    key = lambda t: re.sub(r"\d+", "#", norm(t))
    pages_seen: dict[str, set[int]] = {}
    for b in blocks:
        if _words(b.text) <= FOOTER_MAX_WORDS:
            pages_seen.setdefault(key(b.text), set()).add(b.page)
    footers = [k for k, pages in pages_seen.items() if len(pages) >= REPEAT_PAGES]
    footer_res = [re.compile(r"\s*" + re.escape(k).replace(r"\#", r"\d+").replace(r"\ ", r"\s+") + r"\s*")
                  for k in footers]

    kept: list[TextBlock] = []
    for b in blocks:
        text = norm(b.text)
        if not text or _PAGE_NO.match(text) or key(text) in footers:
            continue
        for fr in footer_res:
            text = fr.sub(" ", text)
        text = norm(text)
        if text:
            kept.append(TextBlock(page=b.page, text=text, bbox=b.bbox, kind=b.kind))

    # 2. headings
    kept = [b for b in kept if not _is_heading(b)]

    # 3. re-join page-split paragraphs
    merged: list[TextBlock] = []
    for b in kept:
        prev = merged[-1] if merged else None
        if (prev is not None and prev.kind == "paragraph" and b.kind == "paragraph"
                and b.page > prev.page
                and not _TERMINAL.search(prev.text)
                and b.text[:1].islower()):
            merged[-1] = TextBlock(page=prev.page, text=norm(prev.text + " " + b.text),
                                   bbox=None, kind="paragraph")
        else:
            merged.append(b)

    # 4-5. emit clauses
    clauses: list[Clause] = []
    for b in merged:
        text = norm(b.text)
        pieces = [text] if b.kind in ("table_row", "footnote") else _chunk(text)
        for piece in pieces:
            cid = f"c{len(clauses) + 1:03d}"
            bbox = b.bbox if piece == text else None
            clauses.append(Clause(id=cid, text=piece, span=SourceSpan(page=b.page, text=piece, bbox=bbox)))
    return clauses
