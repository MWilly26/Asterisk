"""§6.4 cost and latency table, built from runs/calls.jsonl.

One column per model configuration seen in the log (provider + model). Only
uncached calls count (cache hits are logged with `cached: true`, zero latency,
null tokens). Rows are attributed to a pipeline stage by the `stage` field the
client writes; rows logged before that field existed are split by input size
(an extract call carries the whole document, a classify call carries ~8 clauses).

Cost per document = (classify + extract cost) / documents analyzed, where a
document is one extract call. Parse cost is reported on its own row because
the parse model is priced separately. Trap recall and clause counts come from
the eval results file mapped to each model (`--eval MODEL=FILE`).

Writes evals/results/cost_latency.json and evals/results/cost_latency.md.

CLI:
    python evals/cost_table.py
    python evals/cost_table.py --since 2026-09-19T13:00 --eval nvidia/nemotron-3-nano-omni-30b-a3b-reasoning=evals/results/eval_nano.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clause import config  # noqa: E402

RESULTS_DIR = config.REPO_ROOT / "evals" / "results"
STAGES = ("parse", "classify", "extract")
LEGACY_EXTRACT_MIN_INPUT_TOKENS = 5000  # untagged `complete` rows above this are extract calls

# model -> eval results file whose trap recall / clause counts belong in that column
DEFAULT_EVALS = {
    config.ANTHROPIC_MODEL: RESULTS_DIR / "eval_vlm.json",
    config.NVIDIA_NANO_MODEL: RESULTS_DIR / "eval_nano.json",
}


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------


def read_calls(path: Path, since: float | None = None) -> list[dict]:
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("cached") or (since is not None and r["ts"] < since):
            continue
        rows.append(r)
    return rows


def stage_of(row: dict) -> str:
    """`stage` when the client tagged it; otherwise the legacy token-size split."""
    s = row.get("stage")
    if s in STAGES:
        return s
    if row["kind"] == "parse":
        return "parse"
    return "extract" if (row.get("input_tokens") or 0) >= LEGACY_EXTRACT_MIN_INPUT_TOKENS else "classify"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    qs = statistics.quantiles(values, n=100, method="inclusive")
    return round(qs[int(p) - 1], 3)


def cost_usd(model: str, in_tok: int, out_tok: int) -> float | None:
    price = config.PRICING.get(model)
    if price is None:
        return None
    return round((in_tok * price[0] + out_tok * price[1]) / 1_000_000, 4)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize(rows: list[dict], evals: dict[str, Path] | None = None) -> dict:
    evals = evals or {}
    by_model: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_model[(r["provider"], r["model"])][stage_of(r)].append(r)

    configs = {}
    for (provider, model), stages in sorted(by_model.items()):
        col: dict = {"provider": provider, "model": model, "stages": {}}
        for stage in STAGES:
            rs = stages.get(stage, [])
            lat = [r["latency_s"] for r in rs]
            in_tok = sum(r.get("input_tokens") or 0 for r in rs)
            out_tok = sum(r.get("output_tokens") or 0 for r in rs)
            col["stages"][stage] = {
                "calls": len(rs), "retried_calls": sum(1 for r in rs if r.get("attempts", 1) > 1),
                "p50_latency_s": percentile(lat, 50), "p95_latency_s": percentile(lat, 95),
                "mean_latency_s": round(statistics.fmean(lat), 3) if lat else None,
                "input_tokens": in_tok, "output_tokens": out_tok,
                "cost_usd": cost_usd(model, in_tok, out_tok),
            }
        cl, ex = col["stages"]["classify"], col["stages"]["extract"]
        docs = ex["calls"]
        model_cost = None if cl["cost_usd"] is None else round(cl["cost_usd"] + ex["cost_usd"], 4)
        col["documents"] = docs
        col["model_calls"] = cl["calls"] + ex["calls"]
        col["model_calls_per_doc"] = round(col["model_calls"] / docs, 2) if docs else None
        col["model_latency_p50_s"] = percentile([r["latency_s"] for r in stages.get("classify", []) + stages.get("extract", [])], 50)
        col["model_latency_p95_s"] = percentile([r["latency_s"] for r in stages.get("classify", []) + stages.get("extract", [])], 95)
        col["model_tokens"] = cl["input_tokens"] + ex["input_tokens"] + cl["output_tokens"] + ex["output_tokens"]
        col["model_cost_usd"] = model_cost
        col["cost_per_doc_usd"] = round(model_cost / docs, 4) if model_cost is not None and docs else None
        col["parse_cost_per_doc_usd"] = (round(col["stages"]["parse"]["cost_usd"] / col["stages"]["parse"]["calls"], 4)
                                         if col["stages"]["parse"]["cost_usd"] is not None and col["stages"]["parse"]["calls"] else None)
        col["pricing_usd_per_mtok"] = config.PRICING.get(model)
        col["eval"] = _eval_summary(evals.get(model))
        configs[model] = col
    return {"configs": configs, "n_rows": len(rows)}


def _eval_summary(path: Path | None) -> dict | None:
    if path is None or not Path(path).exists():
        return None
    r = json.loads(Path(path).read_text())
    a = r["aggregate"]
    scored = [d for d in r["docs"] if "error" not in d]
    return {
        "file": str(path), "docs": a["n_scored"],
        "clauses": sum(d["n_clauses"] for d in scored),
        "clauses_per_doc": round(sum(d["n_clauses"] for d in scored) / len(scored), 1) if scored else None,
        "trap_recall": a["trap_detection"]["recall"],
        "traps": f"{a['trap_detection']['caught']}/{a['trap_detection']['planted']}",
        "clean_high": a["trap_detection"]["clean_high_total"],
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _n(v, fmt="{:,}") -> str:
    return "–" if v is None else fmt.format(v)


def _usd(v) -> str:
    return "n/a (set config.PRICING)" if v is None else f"${v:,.4f}" if v < 1 else f"${v:,.2f}"


def render_markdown(summary: dict, meta: dict) -> str:
    cols = list(summary["configs"].values())
    if not cols:
        return "# §6.4 Cost and latency\n\nNo uncached calls in the log.\n"
    L = [f"# §6.4 Cost and latency — from `{meta['log']}`", "",
         f"{summary['n_rows']} uncached calls{' since ' + meta['since'] if meta.get('since') else ''} ({meta['timestamp']}). "
         "Cache hits are excluded; latency is wall-clock per call including retries.", ""]
    hdr = "| | " + " | ".join(f"`{c['model']}`" for c in cols) + " |"
    sep = "|---|" + "---:|" * len(cols)

    def row(label, fn):
        return f"| {label} | " + " | ".join(fn(c) for c in cols) + " |"

    L += ["## Per configuration", "", hdr, sep,
          row("Provider", lambda c: c["provider"]),
          row("Documents analyzed (extract calls)", lambda c: _n(c["documents"])),
          row("Clauses classified (from eval run)", lambda c: _n(c["eval"]["clauses"]) if c["eval"] else "–"),
          row("Clauses per document", lambda c: _n(c["eval"]["clauses_per_doc"], "{}") if c["eval"] else "–"),
          row("Model calls (classify + extract)", lambda c: _n(c["model_calls"])),
          row("Model calls per document", lambda c: _n(c["model_calls_per_doc"], "{}")),
          row("p50 / p95 latency per call (s)", lambda c: f"{_n(c['model_latency_p50_s'], '{}')} / {_n(c['model_latency_p95_s'], '{}')}"),
          row("Total tokens (in + out)", lambda c: _n(c["model_tokens"])),
          row("Price ($/MTok in, out)", lambda c: "n/a" if c["pricing_usd_per_mtok"] is None else f"${c['pricing_usd_per_mtok'][0]:.2f}, ${c['pricing_usd_per_mtok'][1]:.2f}"),
          row("**Cost per document**", lambda c: f"**{_usd(c['cost_per_doc_usd'])}**"),
          row("Parse cost per document", lambda c: _usd(c["parse_cost_per_doc_usd"]) if c["stages"]["parse"]["calls"] else "–"),
          row("**Trap recall**", lambda c: f"**{c['eval']['traps']} = {100 * c['eval']['trap_recall']:.0f}%**" if c["eval"] and c["eval"]["trap_recall"] is not None else "–"),
          row("Clean-doc high false positives", lambda c: _n(c["eval"]["clean_high"]) if c["eval"] else "–"),
          "", "## Per stage", "",
          "| Model | Stage | Calls | Retried | p50 (s) | p95 (s) | Mean (s) | Input tokens | Output tokens | Cost |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for c in cols:
        for stage in STAGES:
            s = c["stages"][stage]
            if not s["calls"]:
                continue
            L.append(f"| `{c['model']}` | {stage} | {s['calls']} | {s['retried_calls']} | {_n(s['p50_latency_s'], '{}')} | "
                     f"{_n(s['p95_latency_s'], '{}')} | {_n(s['mean_latency_s'], '{}')} | {s['input_tokens']:,} | {s['output_tokens']:,} | {_usd(s['cost_usd'])} |")
    missing = [c["model"] for c in cols if c["eval"] is None]
    if missing:
        L += ["", "Recall column missing for: " + ", ".join(f"`{m}`" for m in missing)
              + " — pass `--eval MODEL=FILE` pointing at that configuration's `run_eval.py` output."]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_since(s: str | None) -> float | None:
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python evals/cost_table.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--log", type=Path, default=config.CALL_LOG)
    ap.add_argument("--since", help="ignore rows before this ISO timestamp (UTC) or epoch seconds")
    ap.add_argument("--eval", action="append", default=[], metavar="MODEL=FILE",
                    help="eval results file for a model's recall column (repeatable)")
    ap.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    args = ap.parse_args(argv)

    if not args.log.exists():
        ap.error(f"{args.log} not found — run the pipeline first")
    evals = dict(DEFAULT_EVALS)
    for spec in args.eval:
        model, _, file = spec.partition("=")
        if not file:
            ap.error(f"--eval expects MODEL=FILE, got {spec!r}")
        evals[model] = Path(file)
    since = _parse_since(args.since)
    rows = read_calls(args.log, since)
    summary = summarize(rows, evals)
    meta = {"log": str(args.log), "since": args.since, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "pricing": {k: v for k, v in config.PRICING.items()}}
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "cost_latency.json").write_text(json.dumps({"meta": meta, **summary}, indent=1))
    md = render_markdown(summary, meta)
    (args.out_dir / "cost_latency.md").write_text(md)
    for m, c in summary["configs"].items():
        print(f"{m}: {c['documents']} docs, {c['model_calls']} calls, p50 {c['model_latency_p50_s']}s, "
              f"cost/doc {_usd(c['cost_per_doc_usd'])}", file=sys.stderr)
    print(f"wrote {args.out_dir / 'cost_latency.md'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
