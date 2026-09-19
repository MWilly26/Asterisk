"""§6.3 parse-layer comparison: VLM parse (parse.py) vs pdfplumber (parse_baseline.py).

Everything downstream of the parser is identical; both columns are scored by
run_eval.py with the same rules. On top of the §6.1/§6.2 numbers this adds
parse-level evidence for *why* a column differs: how many table rows / footnotes
each parser recovered and whether each planted trap span survived parsing and
segmentation intact.

Writes evals/results/parse_compare.json and evals/results/parse_compare.md.

CLI:
    python evals/parse_compare.py                 # run the pdfplumber column; reuse eval_vlm.json
    python evals/parse_compare.py --run-vlm       # also (re)run the VLM column
    python evals/parse_compare.py --rescore       # score saved analyses for both; no model calls
    python evals/parse_compare.py --docs eq_007,eq_015
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_eval as ev  # noqa: E402
from clause import config  # noqa: E402
from clause.client import ModelClient  # noqa: E402
from clause.parse import parse_pdf  # noqa: E402
from clause.parse_baseline import parse_pdf_baseline  # noqa: E402
from clause.segment import segment  # noqa: E402
from clause.types import TextBlock  # noqa: E402

COLUMNS = ("vlm", "baseline")
LABELS = {"vlm": "VLM parse", "baseline": "pdfplumber"}


# ---------------------------------------------------------------------------
# Parse-level stats (no classifier involved)
# ---------------------------------------------------------------------------


def parse_stats(gold: dict, blocks: list[TextBlock]) -> dict:
    """What the parser handed downstream: block kinds, and whether each trap
    span is a literal substring of the raw text / sits inside one clause."""
    kinds = Counter(b.kind for b in blocks)
    raw = ev.norm(" ".join(b.text for b in blocks))
    clauses = segment(blocks)
    spans = {}
    for trap, text in gold["trap_spans"].items():
        target = ev.norm(text)
        clause, how = ev.find_span_clause(clauses, text)
        spans[trap] = {"in_raw_text": target in raw, "clause_match": how,
                       "n_clauses_containing": sum(1 for c in clauses if target in ev.norm(c.text))}
    return {
        "n_blocks": len(blocks), "n_clauses": len(clauses),
        "kinds": {k: kinds.get(k, 0) for k in ("paragraph", "heading", "table_row", "footnote")},
        "spans": spans,
    }


def collect_parse_stats(golden: list[dict], client: ModelClient | None, column: str, log=print) -> dict[str, dict]:
    out = {}
    for g in golden:
        pdf = ev.REPO / g["pdf"]
        try:
            blocks = parse_pdf_baseline(pdf) if column == "baseline" else parse_pdf(pdf, client)
        except Exception as e:  # noqa: BLE001
            log(f"parse stats {column} {g['doc_id']} FAILED: {e!r}")
            out[g["doc_id"]] = {"error": repr(e)}
            continue
        out[g["doc_id"]] = parse_stats(g, blocks)
    return out


def aggregate_parse_stats(stats: dict[str, dict]) -> dict:
    ok = [s for s in stats.values() if "error" not in s]
    spans = [sp for s in ok for sp in s["spans"].values()]
    in_one = sum(sp["n_clauses_containing"] == 1 for sp in spans)
    dup = sum(sp["n_clauses_containing"] > 1 for sp in spans)
    # A span absent from the space-joined raw text can still be whole inside a
    # clause (the segmenter re-joins page-split paragraphs), so "lost" requires both.
    lost = sum(sp["n_clauses_containing"] == 0 and not sp["in_raw_text"] for sp in spans)
    split = sum(sp["n_clauses_containing"] == 0 and sp["in_raw_text"] for sp in spans)
    return {
        "n_docs": len(ok),
        "mean_blocks": ev._rate(sum(s["n_blocks"] for s in ok), len(ok)),
        "mean_clauses": ev._rate(sum(s["n_clauses"] for s in ok), len(ok)),
        "kinds_total": {k: sum(s["kinds"][k] for s in ok) for k in ("paragraph", "heading", "table_row", "footnote")},
        "spans_planted": len(spans),
        "spans_in_raw_text": len(spans) - lost,
        "spans_in_one_clause": in_one,
        "spans_split": split,
        "spans_duplicated": dup,
        "spans_lost": lost,
    }


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare(golden: list[dict], results: dict[str, dict], parse: dict[str, dict], meta: dict) -> dict:
    return {"meta": meta, "golden": {g["doc_id"]: {"ugly": g.get("ugly"), "traps": g["traps"]} for g in golden},
            "eval": {c: results[c]["aggregate"] for c in COLUMNS},
            "parse": {c: {"aggregate": aggregate_parse_stats(parse[c]), "docs": parse[c]} for c in COLUMNS},
            "docs": {c: {d["doc_id"]: d for d in results[c]["docs"]} for c in COLUMNS}}


def _row(label: str, a, b, fmt=str) -> str:
    return f"| {label} | {fmt(a)} | {fmt(b)} |"


def render_markdown(cmp: dict) -> str:
    ev_, pa, meta = cmp["eval"], cmp["parse"], cmp["meta"]
    v, b = ev_["vlm"], ev_["baseline"]
    pv, pb = pa["vlm"]["aggregate"], pa["baseline"]["aggregate"]
    hdr = f"| | {LABELS['vlm']} | {LABELS['baseline']} |\n|---|---:|---:|"
    L = [f"# §6.3 Parse layer comparison — provider `{meta['provider']}`, model `{meta['model']}`", "",
         f"Same pipeline, same classifier, same extractor; only the parse stage differs. "
         f"{v['n_scored']} / {b['n_scored']} docs scored per column ({meta['timestamp']}).", "",
         "## Trap detection (§6.1)", "", hdr,
         _row("**Recall (all traps)**", f"**{v['trap_detection']['caught']}/{v['trap_detection']['planted']} = {ev._pct(v['trap_detection']['recall'])}**",
              f"**{b['trap_detection']['caught']}/{b['trap_detection']['planted']} = {ev._pct(b['trap_detection']['recall'])}**")]
    for t in sorted(set(v["trap_detection"]["by_type"]) | set(b["trap_detection"]["by_type"])):
        tv, tb = v["trap_detection"]["by_type"].get(t, {}), b["trap_detection"]["by_type"].get(t, {})
        L.append(_row(f"`{t}`", f"{tv.get('caught', 0)}/{tv.get('planted', 0)}", f"{tb.get('caught', 0)}/{tb.get('planted', 0)}"))
    L += [_row("Missed: flagged, wrong category", sum(x["flagged_wrong_category"] for x in v["trap_detection"]["by_type"].values()),
               sum(x["flagged_wrong_category"] for x in b["trap_detection"]["by_type"].values())),
          _row("Missed: not flagged", sum(x["not_flagged"] for x in v["trap_detection"]["by_type"].values()),
               sum(x["not_flagged"] for x in b["trap_detection"]["by_type"].values())),
          _row("Missed: span not in any clause", sum(x["span_not_found"] for x in v["trap_detection"]["by_type"].values()),
               sum(x["span_not_found"] for x in b["trap_detection"]["by_type"].values())),
          _row("Clean-doc false positives (high / medium)", f"{v['trap_detection']['clean_high_total']} / {v['trap_detection']['clean_medium_total']}",
               f"{b['trap_detection']['clean_high_total']} / {b['trap_detection']['clean_medium_total']}"),
          _row("Off-trap flags on trap docs", v["trap_detection"]["trap_docs_off_trap_total"], b["trap_detection"]["trap_docs_off_trap_total"]),
          "", "## Term extraction (§6.2)", "", hdr]
    for f in ev.TERM_FIELDS:
        fv, fb = v["fields"][f], b["fields"][f]
        L.append(_row(f"`{f}` exact", f"{ev._pct(fv['exact_rate'])} ({fv['wrong']} wrong, {fv['missing']} missing)",
                      f"{ev._pct(fb['exact_rate'])} ({fb['wrong']} wrong, {fb['missing']} missing)"))
    L += [_row("Fees: recall", ev._pct(v["fees"]["recall"]), ev._pct(b["fees"]["recall"])),
          _row("Fees: precision", ev._pct(v["fees"]["precision"]), ev._pct(b["fees"]["precision"])),
          _row("Fees: value+basis correct (of matched)", ev._pct(v["fees"]["value_accuracy"]), ev._pct(b["fees"]["value_accuracy"])),
          _row("Fees: financed flag correct (of matched)", ev._pct(v["fees"]["financed_accuracy"]), ev._pct(b["fees"]["financed_accuracy"]))]
    for k in sorted(set(v["fees"]["by_kind"]) | set(b["fees"]["by_kind"])):
        kv, kb = v["fees"]["by_kind"].get(k, {}), b["fees"]["by_kind"].get(k, {})
        L.append(_row(f"  `{k}` matched / missed / spurious", f"{kv.get('matched', 0)} / {kv.get('missed', 0)} / {kv.get('spurious', 0)}",
                      f"{kb.get('matched', 0)} / {kb.get('missed', 0)} / {kb.get('spurious', 0)}"))
    for f in ("total_cost", "effective_apr"):
        L.append(_row(f"`{f}` exact / wrong / withheld", f"{v['computed'][f]['correct']} / {v['computed'][f]['wrong']} / {v['computed'][f]['missing']}",
                      f"{b['computed'][f]['correct']} / {b['computed'][f]['wrong']} / {b['computed'][f]['missing']}"))

    L += ["", "## What each parser handed downstream", "",
          "Block kinds are what the parser labelled; trap spans are checked as literal substrings of the parsed text "
          "and then of a single segmented clause (the classifier only sees clauses).", "", hdr,
          _row("Blocks per doc (mean)", pv["mean_blocks"], pb["mean_blocks"], ev._fmt),
          _row("Clauses per doc (mean)", pv["mean_clauses"], pb["mean_clauses"], ev._fmt)]
    for k in ("heading", "table_row", "footnote"):
        L.append(_row(f"`{k}` blocks (total)", pv["kinds_total"][k], pb["kinds_total"][k]))
    L += [_row("Trap spans intact in parsed text", f"{pv['spans_in_raw_text']}/{pv['spans_planted']}", f"{pb['spans_in_raw_text']}/{pb['spans_planted']}"),
          _row("Trap spans inside exactly one clause", f"{pv['spans_in_one_clause']}/{pv['spans_planted']}", f"{pb['spans_in_one_clause']}/{pb['spans_planted']}"),
          _row("  lost in parse (not in text at all)", pv["spans_lost"], pb["spans_lost"]),
          _row("  present but split across clauses", pv["spans_split"], pb["spans_split"]),
          _row("  present in more than one clause", pv["spans_duplicated"], pb["spans_duplicated"]),
          _row("Mean pipeline time (s)", v["timing"]["mean_total_s"], b["timing"]["mean_total_s"], ev._fmt),
          _row("  parse stage (s)", v["timing"]["mean_by_stage"]["parse"], b["timing"]["mean_by_stage"]["parse"], ev._fmt)]

    L += ["", "## Per document", "", "| Doc | Layout | Traps | Caught (VLM) | Caught (pdfplumber) | Total cost (VLM) | Total cost (pdfplumber) | Spans intact (VLM / pdfplumber) |",
          "|---|---|---|---:|---:|---|---|---|"]
    for doc_id, g in cmp["golden"].items():
        dv = cmp["docs"]["vlm"].get(doc_id, {"error": "not run"})
        db = cmp["docs"]["baseline"].get(doc_id, {"error": "not run"})
        sv, sb = pa["vlm"]["docs"].get(doc_id, {}), pa["baseline"]["docs"].get(doc_id, {})

        def caught(d):
            if "error" in d:
                return "ERR"
            return f"{sum(t['caught'] for t in d['traps'])}/{len(d['traps'])}" if d["traps"] else "–"

        def cost(d):
            return "ERR" if "error" in d else d["computed"]["total_cost"]["outcome"]

        def intact(s):
            if "error" in s or not s:
                return "?"
            return f"{sum(x['n_clauses_containing'] == 1 for x in s['spans'].values())}/{len(s['spans'])}"
        layout = g["ugly"] or ""
        traps = ", ".join(t.removeprefix("T_") for t in g["traps"]) or "*clean*"
        L.append(f"| {doc_id} | {layout} | {traps} | {caught(dv)} | {caught(db)} | {cost(dv)} | {cost(db)} | {intact(sv)} / {intact(sb)} |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python evals/parse_compare.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-vlm", action="store_true", help="(re)run the VLM column too instead of reusing eval_vlm.json")
    ap.add_argument("--rescore", action="store_true", help="score saved analyses for both columns; no pipeline runs")
    ap.add_argument("--docs", help="comma-separated doc ids")
    ap.add_argument("--golden", type=Path, default=ev.GOLDEN_PATH)
    ap.add_argument("--out-dir", type=Path, default=ev.RESULTS_DIR)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    golden = ev.load_golden(args.golden, args.docs.split(",") if args.docs else None)
    log = lambda s: print(s, file=sys.stderr, flush=True)  # noqa: E731
    client = ev.get_client(use_cache=not args.no_cache)  # needed even under --rescore: VLM parse stats read the cache
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results: dict[str, dict] = {}
    for column in COLUMNS:
        analyses_dir = args.out_dir / "analyses" / column
        meta = {"parser": "pdfplumber" if column == "baseline" else "vlm", "provider": config.PROVIDER,
                "model": config.default_model(), "timestamp": stamp, "docs": [g["doc_id"] for g in golden]}
        if args.rescore or (column == "vlm" and not args.run_vlm):
            meta["rescored"] = True
            analyses = ev.load_analyses(golden, analyses_dir)
        else:
            log(f"--- running {column} column ---")
            meta["rescored"] = False
            analyses = ev.run_corpus(golden, client, parser=ev.baseline_parser if column == "baseline" else None,
                                     analyses_dir=analyses_dir, log=log)
        results[column] = ev.score(golden, analyses, meta)
        if not meta["rescored"]:
            ev.write_results(results[column], args.out_dir, column)

    # Parse-level stats: pdfplumber is local; the VLM parse is served from cache when available.
    parse = {c: collect_parse_stats(golden, client, c, log=log) for c in COLUMNS}
    cmp = compare(golden, results, parse, {"provider": config.PROVIDER, "model": config.default_model(), "timestamp": stamp,
                                   "docs": [g["doc_id"] for g in golden]})
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "parse_compare.json").write_text(json.dumps(cmp, indent=1))
    (args.out_dir / "parse_compare.md").write_text(render_markdown(cmp))
    for c in COLUMNS:
        td = results[c]["aggregate"]["trap_detection"]
        log(f"{LABELS[c]:>10}: recall {td['caught']}/{td['planted']} = {ev._pct(td['recall'])}, "
            f"clean-doc high={td['clean_high_total']}, fees recall {ev._pct(results[c]['aggregate']['fees']['recall'])}")
    log(f"wrote {args.out_dir / 'parse_compare.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
