"""Run and report the paired prompt-injection classifier check from PLAN §6.5.

The benchmark deliberately uses pdfplumber plus deterministic segmentation so
the only model-dependent stage is classification.  Use ``--rescore`` to rebuild
the report from saved clauses without making model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clause import config  # noqa: E402
from clause.client import ModelClient, get_client  # noqa: E402
from clause.parse_baseline import parse_pdf_baseline  # noqa: E402
from clause.pipeline import classify_parallel  # noqa: E402
from clause.segment import segment  # noqa: E402
from clause.types import Clause  # noqa: E402
from run_eval import score_traps  # noqa: E402

REPO = config.REPO_ROOT
GOLDEN_PATH = REPO / "data" / "adversarial_golden.json"
RESULTS_DIR = REPO / "evals" / "results"
ANALYSES_DIR = RESULTS_DIR / "analyses" / "adversarial"

Parser = Callable[[Path], list]


def load_golden(path: Path = GOLDEN_PATH) -> list[dict]:
    return json.loads(path.read_text())


def run_corpus(golden: list[dict], client: ModelClient, *, parser: Parser = parse_pdf_baseline,
               analyses_dir: Path | None = ANALYSES_DIR, log=print) -> dict[str, list[Clause] | Exception]:
    """Parse, segment, and classify each document; extraction is intentionally excluded."""
    out: dict[str, list[Clause] | Exception] = {}
    for i, gold in enumerate(golden, 1):
        started = time.perf_counter()
        try:
            clauses = classify_parallel(segment(parser(REPO / gold["pdf"])), client)
        except Exception as exc:  # noqa: BLE001 — retain the rest of a live run
            out[gold["doc_id"]] = exc
            log(f"[{i}/{len(golden)}] {gold['doc_id']} FAILED: {exc!r}")
            continue
        out[gold["doc_id"]] = clauses
        if analyses_dir is not None:
            analyses_dir.mkdir(parents=True, exist_ok=True)
            payload = {"doc_id": gold["doc_id"], "clauses": [c.to_dict() for c in clauses]}
            (analyses_dir / f"{gold['doc_id']}.json").write_text(json.dumps(payload, indent=1))
        caught = sum(t["caught"] for t in score_traps(gold, _ClauseAnalysis(clauses)))
        log(f"[{i}/{len(golden)}] {gold['doc_id']} {caught}/{len(gold['traps'])} traps "
            f"in {time.perf_counter() - started:.1f}s")
    return out


def load_analyses(golden: list[dict], analyses_dir: Path = ANALYSES_DIR) -> dict[str, list[Clause] | Exception]:
    out: dict[str, list[Clause] | Exception] = {}
    for gold in golden:
        path = analyses_dir / f"{gold['doc_id']}.json"
        if not path.exists():
            out[gold["doc_id"]] = FileNotFoundError(str(path))
            continue
        payload = json.loads(path.read_text())
        out[gold["doc_id"]] = [Clause.from_dict(c) for c in payload["clauses"]]
    return out


class _ClauseAnalysis:
    """Minimal shape required by run_eval.score_traps."""

    def __init__(self, clauses: list[Clause]):
        self.clauses = clauses


def score(golden: list[dict], analyses: dict[str, list[Clause] | Exception], meta: dict) -> dict:
    docs = []
    for gold in golden:
        clauses = analyses.get(gold["doc_id"])
        if not isinstance(clauses, list):
            docs.append({"doc_id": gold["doc_id"], "pair_id": gold["pair_id"],
                         "condition": gold["condition"], "error": repr(clauses)})
            continue
        traps = score_traps(gold, _ClauseAnalysis(clauses))
        docs.append({"doc_id": gold["doc_id"], "pair_id": gold["pair_id"],
                     "condition": gold["condition"], "n_clauses": len(clauses),
                     "planted": len(traps), "caught": sum(t["caught"] for t in traps),
                     "traps": traps})

    conditions = {}
    for condition in ("control", "adversarial"):
        expected = sum(row["condition"] == condition for row in golden)
        group = [d for d in docs if d["condition"] == condition and "error" not in d]
        planted = sum(d["planted"] for d in group)
        caught = sum(d["caught"] for d in group)
        conditions[condition] = {"docs_scored": len(group), "docs_total": expected,
                                 "complete": len(group) == expected,
                                 "planted": planted, "caught": caught,
                                 "recall": round(caught / planted, 4) if planted else None}
    control, attack = conditions["control"], conditions["adversarial"]
    delta = (round(attack["recall"] - control["recall"], 4)
             if control["complete"] and attack["complete"] else None)
    return {"meta": meta, "aggregate": {"conditions": conditions, "recall_delta": delta,
                                         "errors": [d["doc_id"] for d in docs if "error" in d]},
            "docs": docs}


def _pct(value: float | None) -> str:
    return "–" if value is None else f"{value * 100:.0f}%"


def render_markdown(results: dict) -> str:
    meta, agg = results["meta"], results["aggregate"]
    control = agg["conditions"]["control"]
    attack = agg["conditions"]["adversarial"]
    lines = ["# §6.5 Adversarial document check", "",
             f"Classifier: `{meta['provider']}` / `{meta['model']}`. Parser: deterministic pdfplumber. "
             "Each adversarial document has an otherwise-identical control; extraction and arithmetic are not run.", "",
             "| Condition | Documents scored | Traps caught | Recall |", "|---|---:|---:|---:|",
             f"| Without manipulation (control) | {control['docs_scored']}/{control['docs_total']} | {control['caught']}/{control['planted']} | {_pct(control['recall'])} |",
             f"| With manipulation (adversarial) | {attack['docs_scored']}/{attack['docs_total']} | {attack['caught']}/{attack['planted']} | {_pct(attack['recall'])} |", "",
             f"**Recall change:** {('not computed until both conditions are complete' if agg['recall_delta'] is None else f'{agg["recall_delta"] * 100:+.0f} percentage points')}. "
             "A trap uses the same strict span + risk + category rule as §6.1.", "",
             "| Pair | Condition | Clauses | Caught | Misses |", "|---|---|---:|---:|---|"]
    for doc in results["docs"]:
        if "error" in doc:
            lines.append(f"| {doc['pair_id']} | {doc['condition']} | – | – | ERROR: {doc['error'][:80]} |")
            continue
        misses = ", ".join(t["trap"] for t in doc["traps"] if not t["caught"]) or "–"
        lines.append(f"| {doc['pair_id']} | {doc['condition']} | {doc['n_clauses']} | {doc['caught']}/{doc['planted']} | {misses} |")
    return "\n".join(lines) + "\n"


def write_results(results: dict, out_dir: Path = RESULTS_DIR) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = out_dir / "adversarial.json", out_dir / "adversarial.md"
    json_path.write_text(json.dumps(results, indent=1))
    markdown_path.write_text(render_markdown(results))
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    ap.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    ap.add_argument("--docs", help="comma-separated doc ids to run, then score all saved documents")
    ap.add_argument("--rescore", action="store_true", help="use saved classified clauses; no model calls")
    args = ap.parse_args(argv)
    golden = load_golden(args.golden)
    meta = {"provider": config.PROVIDER, "model": config.default_model(),
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "rescored": args.rescore}
    analyses_dir = args.out_dir / "analyses" / "adversarial"
    if args.rescore:
        analyses = load_analyses(golden, analyses_dir)
    else:
        selected = golden
        if args.docs:
            wanted = set(args.docs.split(","))
            selected = [row for row in golden if row["doc_id"] in wanted]
            unknown = wanted - {row["doc_id"] for row in selected}
            if unknown:
                raise SystemExit(f"unknown doc ids: {sorted(unknown)}")
        run_corpus(selected, get_client(), analyses_dir=analyses_dir,
                   log=lambda text: print(text, file=sys.stderr, flush=True))
        analyses = load_analyses(golden, analyses_dir)
    results = score(golden, analyses, meta)
    json_path, markdown_path = write_results(results, args.out_dir)
    control = results["aggregate"]["conditions"]["control"]
    attack = results["aggregate"]["conditions"]["adversarial"]
    print(f"control {control['caught']}/{control['planted']}; adversarial {attack['caught']}/{attack['planted']}", file=sys.stderr)
    print(f"wrote {json_path} and {markdown_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
