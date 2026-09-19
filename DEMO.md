# Demo script (T21) — 3 minutes, works with wifi off

Demo document: **`data/docs/eq_012.pdf`** on the NVIDIA path (`nemotron-parse` +
`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`). Every model response for it
is in `.cache/`, so the full pipeline replays in well under a second with no
network. The committed result it reproduces is
`evals/results/analyses/nano/eq_012.json` (scored in `evals/results/eval_nano.json`).

Why eq_012: four planted traps — balloon stated once in a footnote, financed
origination fee, blanket UCC lien, auto-renewal — all four caught by Nano, and
the computed total and APR are exact against `data/golden.json`. Advertised
rate 6.90%, effective 8.60%.

## Pre-flight (do this at the venue, before you present)

```bash
cd <repo>
set -a && . ./.env && set +a          # must set CLAUSE_PROVIDER=nvidia and NVIDIA_API_KEY
.venv/bin/python data/generate.py     # only if data/docs/ is missing; deterministic

# 1. Prove the replay with the network actually removed (Linux):
unshare -rn .venv/bin/python -m clause.pipeline data/docs/eq_012.pdf --out /tmp/eq_012.json
#    Expect five stage lines each ≤ 0.05s and "wrote /tmp/eq_012.json".
#    Any cache miss fails loudly here (connection error after 3 retries) — that is the point.

# 2. Start the app:
.venv/bin/uvicorn api.main:app --port 8000
#    Open http://127.0.0.1:8000 — do NOT open web/index.html from file:// for the
#    upload demo; the API must serve it.
```

If step 1 fails, do **not** re-run online at the venue hoping it warms up — the
hosted endpoint returns `503 ResourceExhausted` under load and a document takes
3–4 minutes when it works. Use fallback A below.

Do not press "Re-run live" on stage — it forces live calls and overwrites the cached responses for that document. Do not delete `.cache/`. Do not change `CLASSIFY_*`/`NVIDIA_*` settings in
`clause/config.py` or `.env` before the demo — the cache key includes the model,
batch composition, `max_tokens`, and `enable_thinking`, so any change is a cache
miss.

## The run-through (≈3:00)

**0:00 — The person (20 s).** A contractor is buying a $11,600 piece of
equipment. The lender hands over an 8-page agreement and says the rate is 6.9%.
Everything expensive in it is somewhere other than where it says 6.9%.

**0:20 — Drop the PDF (15 s).** Drag `eq_012.pdf` onto the page. The five
stages tick past: parse → segment → classify → extract → compute. Say what each
one is: *parse* is `nemotron-parse` turning the PDF into ordered blocks with
page and bounding box; *segment* is deterministic Python; *classify* and
*extract* are Nemotron 3 Nano; *compute* is Python and the only thing that ever
produces a number.

(If asked why it was instant: "responses are cached from the run that produced
our published numbers; uncached, this document takes about two minutes on
NVIDIA's free endpoint.")

**0:35 — Verdict card (30 s).** Total cost **$14,764.04** against a $11,602.50
principal; effective APR **8.60%** vs the advertised **6.90%**; grade **F**.
Point at the APR gap: that 1.7 points is the financed origination fee. None of
these numbers came out of a model — Nano found and typed the terms, `compute.py`
did the arithmetic, and the result matches the generator's ground truth to the
cent.

**1:05 — Ranked clauses (50 s).** Top of the list by dollar impact:

1. **Auto-renewal, high, $12,475.20** — miss a 30-day notice window 90–120 days
   before the end and the whole loan renews for another term. Click it: the
   exact source sentence and page. That is the "show me where it says that"
   guarantee — every clause text is a literal substring of the document.
2. **Balloon, high, $2,288.84** — stated exactly once, in a small-print
   footnote under the payment terms. This is the one a hurried human misses;
   the fee schedule table does not mention it.
3. **Origination fee, medium, $545.32** — 4.70%, financed into the balance so
   interest accrues on it.
4. Further down: the blanket UCC lien on all business assets (no dollar figure
   on purpose — we don't invent a number for a non-monetary clause).

The list shows only high/medium rows by default; "Show all 73 clauses" reveals the low-risk and standard boilerplate plus three unrated ones.

Note the imperfection honestly if it comes up: the "Lender's costs and
attorneys' fees" clause is also tagged `origination_fee` — a known
over-flag; it's in the eval as an off-trap false positive.

**1:55 — "Questions to ask" (15 s).** Three sentences the borrower can say back
to the salesperson, generated from the flagged categories, not free-form chat.

**2:10 — What could go wrong (35 s).** This is the slide judges ask about.

- Missing never means zero: if a required term isn't in the document, the
  total is withheld, not computed. Show the amber "Verify" badge concept
  (any extracted value the validator couldn't find verbatim in the text drops
  to 0.3 confidence).
- The failure we found: with Nano as the *extractor*, 15 of 23 documents in
  our corpus get a wrong total because fees are mistyped — documentation fees
  returned as origination fees. Nano is within two traps of the frontier model
  as a *classifier* (56/58 vs 58/58) but not yet trustworthy as a typed
  extractor. It's all in `evals/results/eval_nano.md`.
- What we'd fix next: exclude any fee below 0.5 confidence from the total and
  withhold instead of computing. We didn't apply it after seeing the numbers.

**2:45 — Close (15 s).** No chat box anywhere. Every document is synthetic. One
codebase, two tracks. Repo + eval tables are public.

## Fallbacks

**A. Server won't start / cache miss.** Open `web/index.html#demo` directly in
the browser (file://). It renders the same eq_012 Nano analysis embedded in
the page — identical verdict card, clause list, and source quotes. You lose
only the "drop a PDF" moment and the "open at source page" PDF link.

**B. Laptop dies.** Play the screen recording (record it during rehearsal with
the offline replay: `unshare -rn` proves it will look the same). Not committed
to the repo.

**C. Someone asks to upload their own PDF.** Only if you have the Brev NIM up
(`NVIDIA_BASE_URL` in `.env`, see README "Run locally"). On the free hosted
endpoint a fresh document takes 2–4 minutes and can fail with 503 — say so
rather than trying it live.

## What eq_007 would have shown instead (why we didn't pick it)

eq_007 is also fully cached and was the original sample. Nano marks its
origination fee as financed when it isn't, so the verdict card would show a
total of $45,110.96 instead of $47,199.76 (the $2,088.80 fee dropped from the
upfront cost) and an APR of 12.8% instead of 16.9%, with only the generic
"undisclosed financed charges" warning to hint at it. It also misses the
cross-default trap. Good evidence, bad first impression; it stays in the eval
table, not on stage.
