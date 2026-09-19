# Status

Claim a task by setting status to IN_PROGRESS with your name, and COMMIT THAT
CHANGE BEFORE STARTING WORK. This is how parallel agents avoid collisions.

| ID  | Task                   | Status      | Owner       | Notes |
|-----|------------------------|-------------|-------------|-------|
| T01 | Repo scaffold          | DONE        | claude-code | venv at .venv; python 3.14 |
| T02 | types.py               | DONE        | claude-code | +TextBlock added to §3.1 |
| T03 | client.py              | DONE        | claude-code | Anthropic+NVIDIA backends; nvidia parse stub -> T06 |
| T04 | Synthetic data         | DONE        | claude-code | run `python data/generate.py` after clone; PDFs gitignored |
| T05 | compute.py             | DONE        | claude-code | +Fee.financed in §3.1; categories in config.py |
| T06 | parse.py               | DONE        | claude-code | Anthropic verified live 23/23; nemotron path written from NIM docs, needs live check on swap |
| T07 | parse_baseline.py      | DONE        | claude-code |       |
| T08 | segment.py             | DONE        | claude-code | 58-81 clauses/doc; all trap spans intact (baseline parser, non-2col) |
| T09 | classify.py            | DONE        | claude-code | live batch 8/8 correct |
| T10 | extract.py             | DONE        | claude-code | live 3/3 docs exact incl. compute totals |
| T11 | pipeline.py            | DONE        | claude-code | classify batches parallel (4 workers); 35s with cached parse, +75s uncached on Anthropic |
| T12 | API                    | DONE        | claude-code | SSE at /analyze/stream; GET / serves web/index.html |
| T13 | Frontend               | DONE        | claude-code | stub = embedded eq_007 Analysis; open web/index.html#demo; source quote on click already in |
| T14 | Source highlighting    | DONE        | codex       | exact quote highlighted; uploaded PDF opens at source page; keyboard accessible |
| T15 | Confidence UI          | DONE        | codex       | <0.5 values marked Verify; missing values/total never default to zero |
| T16 | Eval harness           | DONE        | claude-code | VLM run: recall 58/58, clean FP 1 high; results + per-doc analyses in evals/results/ |
| T17 | Parse comparison       | DONE        | claude-code | negative result with Opus: pdfplumber 58/58 too; VLM keeps 5 spans pdfplumber loses (2-col docs) |
| T18 | Cost/latency table     | BLOCKED     | claude-code | tool + Opus column done ($0.34/doc, 10.4 calls/doc); Nano column needs NVIDIA_API_KEY |
| T19 | Adversarial docs       | BLOCKED     | codex       | 5 pairs generated; control 10/10, attack 8/8 on 4/5; adv_005 blocked by exhausted Anthropic credit |
| T20 | README                 | DONE        | codex       | run/setup, architecture, evidence links, honest negative/blocked results; example keys blanked |
| T21 | Demo script            | TODO        |             |       |
| T22 | Submission blurbs      | TODO        |             |       |

Statuses: TODO | IN_PROGRESS | DONE | BLOCKED (say what on)
