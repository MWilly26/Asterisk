"""Pipeline: wires parse -> segment -> classify -> extract -> compute. PLAN §2.1, §3.2.

`analyze()` is the single entry point used by the API (T12) and the eval
harness (T16). It records wall-clock seconds per stage into
`Analysis.timings` and can report progress through a callback so the API can
stream "now classifying..." events.

CLI:  python -m clause.pipeline data/docs/eq_007.pdf [--baseline] [--no-cache] [--out FILE]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Protocol

from clause import config
from clause.classify import classify
from clause.client import ModelClient, get_client
from clause.compute import compute
from clause.extract import extract_terms
from clause.parse import parse_pdf
from clause.segment import segment
from clause.types import Analysis, Clause, TextBlock

log = logging.getLogger(__name__)

STAGES = ("parse", "segment", "classify", "extract", "compute")

# progress(stage, event, seconds): event is "start" (seconds=None) or "done".
ProgressFn = Callable[[str, str, float | None], None]


class Parser(Protocol):
    def __call__(self, path: Path, client: ModelClient | None = None) -> list[TextBlock]: ...


class _Timer:
    """Times each stage, stores it in `timings`, and forwards progress events."""

    def __init__(self, timings: dict[str, float], progress: ProgressFn | None):
        self.timings = timings
        self.progress = progress or (lambda *_: None)

    def run(self, stage: str, fn: Callable[[], object]):
        self.progress(stage, "start", None)
        t0 = time.perf_counter()
        result = fn()
        secs = round(time.perf_counter() - t0, 3)
        self.timings[stage] = secs
        log.info("%s: %.2fs", stage, secs)
        self.progress(stage, "done", secs)
        return result


def classify_parallel(clauses: list[Clause], client: ModelClient, *,
                      workers: int = config.CLASSIFY_WORKERS,
                      batch_size: int = config.CLASSIFY_BATCH_SIZE) -> list[Clause]:
    """`classify` over independent batches concurrently; output order is preserved.

    A 12-page agreement is 8-12 batches and each is a separate model call, so
    this is the difference between a 2-minute and a 20-second analysis.
    """
    batches = [clauses[i:i + batch_size] for i in range(0, len(clauses), batch_size)]
    if workers <= 1 or len(batches) <= 1:
        return classify(clauses, client, batch_size=batch_size)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = pool.map(lambda b: classify(b, client, batch_size=batch_size), batches)
    return [c for batch in results for c in batch]


def analyze(path: Path, client: ModelClient | None = None, *,
            parser: Parser | None = None, progress: ProgressFn | None = None) -> Analysis:
    """PDF -> Analysis. `parser` defaults to the VLM parse; pass
    `parse_baseline.parse_pdf_baseline` for the §6.3 comparison."""
    path = Path(path)
    client = client or get_client()
    parser = parser or parse_pdf
    timings: dict[str, float] = {}
    t = _Timer(timings, progress)
    t_total = time.perf_counter()

    blocks = t.run("parse", lambda: parser(path, client))
    clauses = t.run("segment", lambda: segment(blocks))
    classified = t.run("classify", lambda: classify_parallel(clauses, client))
    terms = t.run("extract", lambda: extract_terms(blocks, client))
    analysis: Analysis = t.run("compute", lambda: compute(terms, classified))

    timings["total"] = round(time.perf_counter() - t_total, 3)
    analysis.timings = timings
    return analysis


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_progress(stage: str, event: str, secs: float | None) -> None:
    if event == "start":
        print(f"{stage:>9} ...", end="", file=sys.stderr, flush=True)
    else:
        print(f" {secs:.2f}s", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m clause.pipeline", description="Analyze a financing PDF.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--baseline", action="store_true", help="parse with pdfplumber instead of the VLM")
    ap.add_argument("--no-cache", action="store_true", help="bypass the response cache")
    ap.add_argument("--out", type=Path, help="write JSON here instead of stdout")
    ap.add_argument("-q", "--quiet", action="store_true", help="no per-stage progress on stderr")
    args = ap.parse_args(argv)

    if not args.pdf.is_file():
        ap.error(f"{args.pdf} not found")
    parser: Parser | None = None
    if args.baseline:
        from clause.parse_baseline import parse_pdf_baseline
        parser = lambda path, client=None: parse_pdf_baseline(path)  # noqa: E731 — baseline takes no client
    client = get_client(use_cache=not args.no_cache)

    analysis = analyze(args.pdf, client, parser=parser,
                       progress=None if args.quiet else _print_progress)
    out = analysis.to_json(indent=2)
    if args.out:
        args.out.write_text(out)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
