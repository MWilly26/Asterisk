# §6.4 Cost and latency — from `/home/masonwilly/Documents/Steelhacks/Asterisk/runs/calls.jsonl`

500 uncached calls (2026-09-19 18:19 UTC). Cache hits are excluded; latency is wall-clock per call including retries.

## Per configuration

| | `claude-opus-5` |
|---|---:|
| Provider | anthropic |
| Documents analyzed (extract calls) | 46 |
| Clauses classified | 1,742 |
| Clauses per document | 75.7 |
| Model calls (classify + extract) | 476 |
| Model calls per document | 10.35 |
| p50 / p95 latency per call (s) | 10.177 / 14.073 |
| Total tokens (in + out) | 1,681,846 |
| Price ($/MTok in, out) | $5.00, $25.00 |
| **Cost per document** | **$0.3442** |
| Parse cost per document | $0.3196 |
| **Trap recall** | **58/58 = 100%** |
| Clean-doc high false positives | 1 |

## Per stage

| Model | Stage | Calls | Retried | p50 (s) | p95 (s) | Mean (s) | Input tokens | Output tokens | Cost |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `claude-opus-5` | parse | 24 | 0 | 75.235 | 89.123 | 73.4 | 469,063 | 213,045 | $7.67 |
| `claude-opus-5` | classify | 430 | 3 | 10.442 | 14.158 | 10.098 | 891,192 | 345,233 | $13.09 |
| `claude-opus-5` | extract | 46 | 0 | 6.422 | 10.198 | 6.942 | 419,547 | 25,874 | $2.74 |
