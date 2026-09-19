"""Eval harness: PLAN §6.1 (trap detection) and §6.2 (term extraction) against data/golden.json.

Runs `clause.pipeline.analyze` over the corpus, scores each Analysis against the
golden entry, and writes:

    evals/results/analyses/<parser>/<doc_id>.json   raw Analysis per doc (re-scorable)
    evals/results/eval_<parser>.json                 per-doc + aggregate scores
    evals/results/eval_<parser>.md                   rendered tables

CLI:
    python evals/run_eval.py                       # VLM parser, whole corpus
    python evals/run_eval.py --baseline            # pdfplumber parser (§6.3 second column)
    python evals/run_eval.py --docs eq_007,eq_015  # subset
    python evals/run_eval.py --rescore             # score saved analyses, no model calls
    python evals/run_eval.py --rescore --name nano_partial --analyses-dir evals/results/analyses/nano
                                                   # partial corpus: unsaved docs are reported as "not run"

Scoring rules (see DECISIONS.md, T16):
  * A trap is CAUGHT when the clause containing its golden span is flagged
    (risk high or medium) with the category that trap maps to. "Flagged, wrong
    category" and "span not found in any clause" are reported separately.
  * Clean-doc false positives are the raw count of high (and medium) clauses.
  * Each term field gets one outcome: correct | wrong | missing | spurious |
    correct_absent. Wrong values are split by whether the extractor's own
    confidence was below config.LOW_CONFIDENCE ("wrong, flagged") — the §2.4
    missing-vs-wrong argument.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clause import config  # noqa: E402
from clause.client import ModelClient, get_client  # noqa: E402
from clause.pipeline import analyze  # noqa: E402
from clause.types import Analysis, Clause, Fee, LoanTerms  # noqa: E402

REPO = config.REPO_ROOT
GOLDEN_PATH = REPO / "data" / "golden.json"
RESULTS_DIR = REPO / "evals" / "results"

# Trap type -> classify category that counts as catching it (config.CATEGORIES).
TRAP_CATEGORY: dict[str, str] = {
    "T_BALLOON": "balloon",
    "T_APR_GAP": "origination_fee",
    "T_ORIG_FEE": "origination_fee",
    "T_PREPAY": "prepayment_penalty",
    "T_LATE_CASCADE": "late_fee",
    "T_AUTO_RENEW": "auto_renewal",
    "T_CROSS_DEFAULT": "cross_default",
    "T_CONFESSION": "personal_guarantee",
    "T_INSURANCE": "insurance",
    "T_VENUE": "venue",
    "T_UCC": "blanket_lien",
}
FLAGGED_RISKS = ("high", "medium")

TERM_FIELDS = ("principal", "stated_apr", "term_months", "payment_amount", "payment_frequency", "balloon_amount")
# Absolute tolerance per field; None = exact equality.
TOLERANCE: dict[str, float | None] = {
    "principal": 0.005, "stated_apr": 5e-5, "term_months": None, "payment_amount": 0.005,
    "payment_frequency": None, "balloon_amount": 0.005,
    "total_cost": 0.005, "effective_apr": 5e-5,
}
FIELD_OUTCOMES = ("correct", "wrong", "missing", "spurious", "correct_absent")
SPAN_OVERLAP_MIN = 0.6  # fallback token-overlap when the span isn't a literal substring

Parser = Callable[[Path, ModelClient | None], list]


# ---------------------------------------------------------------------------
# Text matching
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def norm(s: str) -> str:
    return _WS.sub(" ", s.translate(_QUOTES)).strip().lower()


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9$%.]+", norm(s))


def find_span_clause(clauses: list[Clause], span_text: str) -> tuple[Clause | None, str]:
    """Clause containing the golden trap span. Returns (clause, how) where how is
    "substring" | "overlap" | "none". The VLM parse can re-flow whitespace or
    hyphenation, so a token-overlap fallback catches near-misses."""
    target = norm(span_text)
    for c in clauses:
        if target in norm(c.text):
            return c, "substring"
    want = Counter(_tokens(span_text))
    if not want:
        return None, "none"
    best, best_score = None, 0.0
    for c in clauses:
        have = Counter(_tokens(c.text))
        overlap = sum(min(n, have[t]) for t, n in want.items()) / sum(want.values())
        if overlap > best_score:
            best, best_score = c, overlap
    if best is not None and best_score >= SPAN_OVERLAP_MIN:
        return best, "overlap"
    return None, "none"


# ---------------------------------------------------------------------------
# §6.1 trap detection
# ---------------------------------------------------------------------------


def score_traps(gold: dict, analysis: Analysis) -> list[dict]:
    out = []
    for trap in gold["traps"]:
        want_cat = TRAP_CATEGORY[trap]
        clause, how = find_span_clause(analysis.clauses, gold["trap_spans"][trap])
        flagged = clause is not None and clause.risk in FLAGGED_RISKS
        cat_ok = clause is not None and clause.category == want_cat
        # Diagnostic: did *some* flagged clause carry the right category, even if not the span's?
        elsewhere = any(c.risk in FLAGGED_RISKS and c.category == want_cat for c in analysis.clauses)
        out.append({
            "trap": trap,
            "expected_category": want_cat,
            "clause_id": clause.id if clause else None,
            "span_match": how,
            "risk": clause.risk if clause else None,
            "category": clause.category if clause else None,
            "caught": bool(flagged and cat_ok),
            "flagged_wrong_category": bool(flagged and not cat_ok),
            "not_flagged": bool(clause is not None and not flagged),
            "category_flagged_elsewhere": elsewhere,
        })
    return out


def score_false_positives(gold: dict, analysis: Analysis) -> dict:
    """Raw counts of flagged clauses. On a clean doc every one is a false positive;
    on a trap doc, `off_trap` counts flags whose category matches no planted trap."""
    flagged = [c for c in analysis.clauses if c.risk in FLAGGED_RISKS]
    trap_cats = {TRAP_CATEGORY[t] for t in gold["traps"]}
    return {
        "high": sum(1 for c in flagged if c.risk == "high"),
        "medium": sum(1 for c in flagged if c.risk == "medium"),
        "off_trap": sum(1 for c in flagged if c.category not in trap_cats),
        "flagged": [{"id": c.id, "risk": c.risk, "category": c.category, "text": c.text[:120]} for c in flagged],
    }


# ---------------------------------------------------------------------------
# §6.2 term extraction
# ---------------------------------------------------------------------------


def _close(a, b, tol: float | None) -> bool:
    if tol is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    return abs(float(a) - float(b)) <= tol


def _outcome(gold, got, tol: float | None) -> str:
    if gold is None and got is None:
        return "correct_absent"
    if gold is None:
        return "spurious"
    if got is None:
        return "missing"
    return "correct" if _close(gold, got, tol) else "wrong"


def score_fields(gold_terms: dict, terms: LoanTerms) -> dict[str, dict]:
    out = {}
    for f in TERM_FIELDS:
        got = getattr(terms, f)
        conf = terms.confidences.get(f)
        o = _outcome(gold_terms.get(f), got, TOLERANCE[f])
        out[f] = {
            "gold": gold_terms.get(f), "got": got, "confidence": conf, "outcome": o,
            "low_confidence": conf is not None and conf < config.LOW_CONFIDENCE,
            "in_missing_fields": f in terms.missing_fields,
        }
    return out


def score_fees(gold_fees: list[dict], fees: list[Fee]) -> dict:
    """Greedy match on kind (golden has at most one fee per kind per doc)."""
    remaining = list(fees)
    matched, missed = [], []
    for g in gold_fees:
        hit = next((f for f in remaining if f.kind == g["kind"]), None)
        if hit is None:
            missed.append(g["kind"])
            continue
        remaining.remove(hit)
        value_ok = hit.basis == g["basis"] and _close(g["value"], hit.value, 1e-6 if "percent" in g["basis"] else 0.005)
        matched.append({
            "kind": g["kind"], "gold": {"basis": g["basis"], "value": g["value"], "financed": g["financed"]},
            "got": {"basis": hit.basis, "value": hit.value, "financed": hit.financed, "confidence": hit.confidence},
            "value_correct": value_ok, "financed_correct": hit.financed == g["financed"],
            "low_confidence": hit.confidence < config.LOW_CONFIDENCE,
        })
    return {
        "gold_count": len(gold_fees), "extracted_count": len(fees),
        "matched": matched, "missed_kinds": missed,
        "spurious": [{"kind": f.kind, "basis": f.basis, "value": f.value} for f in remaining],
    }


def score_computed(gold_computed: dict, analysis: Analysis) -> dict:
    return {
        "total_cost": {"gold": gold_computed["total_cost"], "got": analysis.total_cost,
                       "outcome": _outcome(gold_computed["total_cost"], analysis.total_cost, TOLERANCE["total_cost"])},
        "effective_apr": {"gold": gold_computed["effective_apr"], "got": analysis.effective_apr,
                          "outcome": _outcome(gold_computed["effective_apr"], analysis.effective_apr, TOLERANCE["effective_apr"])},
        "incomplete": analysis.incomplete,
        "grade": analysis.grade,
    }


# ---------------------------------------------------------------------------
# Per-doc + aggregate
# ---------------------------------------------------------------------------


def score_doc(gold: dict, analysis: Analysis) -> dict:
    traps = score_traps(gold, analysis)
    return {
        "doc_id": gold["doc_id"],
        "ugly": gold.get("ugly"),
        "clean": not gold["traps"],
        "n_clauses": len(analysis.clauses),
        "traps": traps,
        "false_positives": score_false_positives(gold, analysis),
        "fields": score_fields(gold["terms"], analysis.terms),
        "fees": score_fees(gold["terms"]["fees"], analysis.terms.fees),
        "computed": score_computed(gold["computed"], analysis),
        "warnings": list(analysis.warnings),
        "timings": dict(analysis.timings),
    }


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def aggregate(docs: list[dict]) -> dict:
    scored = [d for d in docs if "error" not in d]
    # --- §6.1 ---
    by_type: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for d in scored:
        for t in d["traps"]:
            c = by_type[t["trap"]]
            c["planted"] += 1
            for k in ("caught", "flagged_wrong_category", "not_flagged"):
                c[k] += int(t[k])
            c["span_not_found"] += int(t["span_match"] == "none")
            if not t["caught"]:
                misses.append({"doc_id": d["doc_id"], **{k: t[k] for k in ("trap", "clause_id", "span_match", "risk", "category", "category_flagged_elsewhere")}})
    trap_types = {k: {**v, "recall": _rate(v["caught"], v["planted"])} for k, v in sorted(by_type.items())}
    planted = sum(v["planted"] for v in by_type.values())
    caught = sum(v["caught"] for v in by_type.values())
    clean = [d for d in scored if d["clean"]]
    trap_docs = [d for d in scored if not d["clean"]]

    # --- §6.2 ---
    fields = {}
    for f in TERM_FIELDS:
        c = Counter()
        for d in scored:
            r = d["fields"][f]
            c[r["outcome"]] += 1
            if r["outcome"] == "wrong":
                c["wrong_flagged" if r["low_confidence"] else "wrong_confident"] += 1
        present = c["correct"] + c["wrong"] + c["missing"]
        fields[f] = {**{k: c[k] for k in FIELD_OUTCOMES + ("wrong_confident", "wrong_flagged")},
                     "n": len(scored), "exact_rate": _rate(c["correct"] + c["correct_absent"], len(scored)),
                     "exact_rate_when_present": _rate(c["correct"], present)}
    fee_gold = sum(d["fees"]["gold_count"] for d in scored)
    fee_extracted = sum(d["fees"]["extracted_count"] for d in scored)
    fee_matched = [m for d in scored for m in d["fees"]["matched"]]
    fee_by_kind: dict[str, Counter] = defaultdict(Counter)
    for d in scored:
        for m in d["fees"]["matched"]:
            fee_by_kind[m["kind"]]["matched"] += 1
            fee_by_kind[m["kind"]]["value_correct"] += int(m["value_correct"])
            fee_by_kind[m["kind"]]["financed_correct"] += int(m["financed_correct"])
        for k in d["fees"]["missed_kinds"]:
            fee_by_kind[k]["missed"] += 1
        for s in d["fees"]["spurious"]:
            fee_by_kind[s["kind"]]["spurious"] += 1
    computed = {}
    for f in ("total_cost", "effective_apr"):
        c = Counter(d["computed"][f]["outcome"] for d in scored)
        computed[f] = {**{k: c[k] for k in ("correct", "wrong", "missing")}, "exact_rate": _rate(c["correct"], len(scored))}

    return {
        "n_docs": len(docs), "n_scored": len(scored), "errors": [d["doc_id"] for d in docs if "error" in d],
        "trap_detection": {
            "planted": planted, "caught": caught, "recall": _rate(caught, planted),
            "by_type": trap_types, "misses": misses,
            "clean_docs": {d["doc_id"]: {"high": d["false_positives"]["high"], "medium": d["false_positives"]["medium"],
                                         "n_clauses": d["n_clauses"]} for d in clean},
            "clean_high_total": sum(d["false_positives"]["high"] for d in clean),
            "clean_medium_total": sum(d["false_positives"]["medium"] for d in clean),
            "trap_docs_flagged_mean": _rate(sum(d["false_positives"]["high"] + d["false_positives"]["medium"] for d in trap_docs),
                                            len(trap_docs)),
            "trap_docs_off_trap_total": sum(d["false_positives"]["off_trap"] for d in trap_docs),
        },
        "fields": fields,
        "fees": {
            "gold": fee_gold, "extracted": fee_extracted, "matched": len(fee_matched),
            "precision": _rate(len(fee_matched), fee_extracted), "recall": _rate(len(fee_matched), fee_gold),
            "value_accuracy": _rate(sum(m["value_correct"] for m in fee_matched), len(fee_matched)),
            "financed_accuracy": _rate(sum(m["financed_correct"] for m in fee_matched), len(fee_matched)),
            "by_kind": {k: dict(v) for k, v in sorted(fee_by_kind.items())},
        },
        "computed": computed,
        "timing": {
            "mean_total_s": _rate(sum(d["timings"].get("total", 0) for d in scored), len(scored)),
            "mean_by_stage": {s: _rate(sum(d["timings"].get(s, 0) for d in scored), len(scored))
                              for s in ("parse", "segment", "classify", "extract", "compute")},
        },
    }


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.0f}%"


def _fmt(v) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1 else f"{v:,.2f}"
    return str(v)


def render_markdown(results: dict) -> str:
    a = results["aggregate"]
    td, meta = a["trap_detection"], results["meta"]
    L = [f"# Eval — parser: `{meta['parser']}` · provider: `{meta['provider']}` · model: `{meta['model']}`", "",
         f"{a['n_scored']}/{a['n_docs']} documents scored ({meta['timestamp']})."
         + (f" **Errors:** {', '.join(a['errors'])}." if a["errors"] else ""), ""]

    L += ["## §6.1 Trap detection", "",
          f"**Recall: {td['caught']}/{td['planted']} = {_pct(td['recall'])}** "
          f"(target ≥ 85%). A trap counts as caught when the clause holding its planted span is flagged "
          f"high/medium *with the matching category*.", "",
          "| Trap type | Planted | Caught | Recall | Flagged, wrong category | Not flagged | Span not found |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for t, v in td["by_type"].items():
        L.append(f"| `{t}` | {v['planted']} | {v['caught']} | {_pct(v['recall'])} | {v['flagged_wrong_category']} | {v['not_flagged']} | {v['span_not_found']} |")
    L += ["", f"**Clean documents (false positives):** {td['clean_high_total']} high, {td['clean_medium_total']} medium "
              f"across {len(td['clean_docs'])} clean docs (target ~0 high).", "",
          "| Clean doc | Clauses | High | Medium |", "|---|---:|---:|---:|"]
    for doc, v in td["clean_docs"].items():
        L.append(f"| {doc} | {v['n_clauses']} | {v['high']} | {v['medium']} |")
    L += ["", f"**Trap documents:** {_fmt(td['trap_docs_flagged_mean'])} flagged clauses per doc on average; "
              f"{td['trap_docs_off_trap_total']} flags whose category matches no planted trap (\"off-trap\").", ""]
    if td["misses"]:
        L += ["", "**Missed traps:**", "", "| Doc | Trap | Span clause | Match | Risk | Category | Right category flagged elsewhere |", "|---|---|---|---|---|---|---|"]
        for m in td["misses"]:
            L.append(f"| {m['doc_id']} | `{m['trap']}` | {m['clause_id'] or '–'} | {m['span_match']} | {m['risk'] or '–'} | {m['category'] or '–'} | {'yes' if m['category_flagged_elsewhere'] else 'no'} |")

    L += ["", "## §6.2 Term extraction", "",
          "Outcome per field per document. *Wrong, flagged* = wrong value but the extractor reported confidence "
          f"< {config.LOW_CONFIDENCE} (the UI marks it \"verify this\"). *Missing* = not found (excluded from totals, never zero).", "",
          "| Field | Correct | Correct (absent) | Wrong, confident | Wrong, flagged | Missing | Spurious | Exact rate |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for f, v in a["fields"].items():
        L.append(f"| `{f}` | {v['correct']} | {v['correct_absent']} | {v['wrong_confident']} | {v['wrong_flagged']} | {v['missing']} | {v['spurious']} | {_pct(v['exact_rate'])} |")
    fe = a["fees"]
    L += ["", f"**Fees:** precision {_pct(fe['precision'])} ({fe['matched']}/{fe['extracted']} extracted), "
              f"recall {_pct(fe['recall'])} ({fe['matched']}/{fe['gold']} planted); on matched fees, "
              f"value+basis correct {_pct(fe['value_accuracy'])}, financed flag correct {_pct(fe['financed_accuracy'])}.", "",
          "| Fee kind | Matched | Missed | Spurious | Value correct | Financed correct |", "|---|---:|---:|---:|---:|---:|"]
    for k, v in fe["by_kind"].items():
        L.append(f"| `{k}` | {v.get('matched', 0)} | {v.get('missed', 0)} | {v.get('spurious', 0)} | {v.get('value_correct', 0)} | {v.get('financed_correct', 0)} |")
    co = a["computed"]
    L += ["", "**Computed figures** (deterministic, from extracted terms — §2.3):", "",
          "| Figure | Exact | Wrong | Withheld (incomplete) | Exact rate |", "|---|---:|---:|---:|---:|"]
    for f, v in co.items():
        L.append(f"| `{f}` | {v['correct']} | {v['wrong']} | {v['missing']} | {_pct(v['exact_rate'])} |")

    tm = a["timing"]
    L += ["", "## Per document", "",
          f"Mean wall time {_fmt(tm['mean_total_s'])}s (" + ", ".join(f"{s} {_fmt(v)}s" for s, v in tm["mean_by_stage"].items()) + ").", "",
          "| Doc | Layout | Traps planted | Caught | Flagged (H/M) | Off-trap | Total cost | Eff. APR | Grade | Time |",
          "|---|---|---|---:|---:|---:|---|---|---|---:|"]
    for d in results["docs"]:
        if "error" in d:
            L.append(f"| {d['doc_id']} | | | | | | ERROR: {d['error'][:60]} | | | |")
            continue
        fp = d["false_positives"]
        traps = ", ".join(t["trap"].removeprefix("T_") for t in d["traps"]) or "*clean*"
        caught = f"{sum(t['caught'] for t in d['traps'])}/{len(d['traps'])}" if d["traps"] else "–"
        c = d["computed"]
        L.append(f"| {d['doc_id']} | {d['ugly'] or ''} | {traps} | {caught} | {fp['high']}/{fp['medium']} | {fp['off_trap']} | "
                 f"{c['total_cost']['outcome']} | {c['effective_apr']['outcome']} | {c['grade']} | {_fmt(d['timings'].get('total'))}s |")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def load_golden(path: Path = GOLDEN_PATH, doc_ids: list[str] | None = None) -> list[dict]:
    golden = json.loads(path.read_text())
    if doc_ids:
        want = set(doc_ids)
        golden = [g for g in golden if g["doc_id"] in want]
        unknown = want - {g["doc_id"] for g in golden}
        if unknown:
            raise SystemExit(f"unknown doc ids: {sorted(unknown)}")
    return golden


def baseline_parser(path: Path, client: ModelClient | None = None):
    from clause.parse_baseline import parse_pdf_baseline
    return parse_pdf_baseline(path)


def run_corpus(golden: list[dict], client: ModelClient, *, parser: Parser | None = None,
               analyses_dir: Path | None = None, log=print) -> dict[str, Analysis | Exception]:
    """analyze() every golden doc; saves each Analysis JSON when analyses_dir is given.
    A failing doc is recorded as its exception so one 500 doesn't lose the run."""
    out: dict[str, Analysis | Exception] = {}
    for i, g in enumerate(golden, 1):
        pdf = REPO / g["pdf"]
        t0 = time.perf_counter()
        try:
            a = analyze(pdf, client, parser=parser)
        except Exception as e:  # noqa: BLE001 — report and continue
            log(f"[{i}/{len(golden)}] {g['doc_id']} FAILED: {e!r}")
            traceback.print_exc()
            out[g["doc_id"]] = e
            continue
        out[g["doc_id"]] = a
        if analyses_dir is not None:
            analyses_dir.mkdir(parents=True, exist_ok=True)
            (analyses_dir / f"{g['doc_id']}.json").write_text(a.to_json(indent=1))
        log(f"[{i}/{len(golden)}] {g['doc_id']} {time.perf_counter() - t0:.1f}s grade={a.grade} "
            f"clauses={len(a.clauses)} flagged={sum(c.risk in FLAGGED_RISKS for c in a.clauses)}")
    return out


def load_analyses(golden: list[dict], analyses_dir: Path) -> dict[str, Analysis | Exception]:
    out: dict[str, Analysis | Exception] = {}
    for g in golden:
        p = analyses_dir / f"{g['doc_id']}.json"
        if p.exists():
            out[g["doc_id"]] = Analysis.from_json(p.read_text())
        else:  # no path in the message: results files are committed and must not carry a machine path
            out[g["doc_id"]] = FileNotFoundError("not run: no saved analysis")
    return out


def score(golden: list[dict], analyses: dict[str, Analysis | Exception], meta: dict) -> dict:
    docs = []
    for g in golden:
        a = analyses.get(g["doc_id"])
        if isinstance(a, Analysis):
            docs.append(score_doc(g, a))
        else:
            docs.append({"doc_id": g["doc_id"], "error": repr(a) if a is not None else "not run"})
    return {"meta": meta, "aggregate": aggregate(docs), "docs": docs}


def write_results(results: dict, out_dir: Path, name: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, mp = out_dir / f"eval_{name}.json", out_dir / f"eval_{name}.md"
    jp.write_text(json.dumps(results, indent=1))
    mp.write_text(render_markdown(results))
    return jp, mp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python evals/run_eval.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--baseline", action="store_true", help="parse with pdfplumber instead of the VLM")
    ap.add_argument("--docs", help="comma-separated doc ids (default: whole corpus)")
    ap.add_argument("--rescore", action="store_true", help="score saved analyses; no model calls")
    ap.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    ap.add_argument("--out-dir", type=Path, default=RESULTS_DIR)
    ap.add_argument("--name", help="results file suffix (default: vlm | baseline)")
    ap.add_argument("--analyses-dir", type=Path,
                    help="where per-doc Analysis JSON is saved/read (default: <out-dir>/analyses/<name>); "
                         "lets a partial rescore be written under a distinct name")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args(argv)

    name = args.name or ("baseline" if args.baseline else "vlm")
    golden = load_golden(args.golden, args.docs.split(",") if args.docs else None)
    analyses_dir = args.analyses_dir or args.out_dir / "analyses" / name
    meta = {
        "parser": "pdfplumber" if args.baseline else "vlm",
        "provider": config.PROVIDER, "model": config.default_model(),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "docs": [g["doc_id"] for g in golden], "rescored": args.rescore,
    }
    if args.rescore:
        analyses = load_analyses(golden, analyses_dir)
    else:
        client = get_client(use_cache=not args.no_cache)
        meta["parse_model"] = client.parse_model() if not args.baseline else "pdfplumber"
        analyses = run_corpus(golden, client, parser=baseline_parser if args.baseline else None,
                              analyses_dir=analyses_dir, log=lambda s: print(s, file=sys.stderr, flush=True))
    results = score(golden, analyses, meta)
    jp, mp = write_results(results, args.out_dir, name)
    td = results["aggregate"]["trap_detection"]
    print(f"trap recall {td['caught']}/{td['planted']} = {_pct(td['recall'])}; "
          f"clean-doc high={td['clean_high_total']} medium={td['clean_medium_total']}", file=sys.stderr)
    print(f"wrote {jp} and {mp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
