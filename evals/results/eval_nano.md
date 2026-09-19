# Eval — parser: `vlm` · provider: `nvidia` · model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`

23/23 documents scored (2026-09-19 21:45 UTC).

## §6.1 Trap detection

**Recall: 56/58 = 97%** (target ≥ 85%). A trap counts as caught when the clause holding its planted span is flagged high/medium *with the matching category*.

| Trap type | Planted | Caught | Recall | Flagged, wrong category | Not flagged | Span not found |
|---|---:|---:|---:|---:|---:|---:|
| `T_APR_GAP` | 6 | 6 | 100% | 0 | 0 | 0 |
| `T_AUTO_RENEW` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_BALLOON` | 4 | 4 | 100% | 0 | 0 | 0 |
| `T_CONFESSION` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_CROSS_DEFAULT` | 6 | 4 | 67% | 0 | 2 | 0 |
| `T_INSURANCE` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_LATE_CASCADE` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_ORIG_FEE` | 4 | 4 | 100% | 0 | 0 | 0 |
| `T_PREPAY` | 7 | 7 | 100% | 0 | 0 | 0 |
| `T_UCC` | 6 | 6 | 100% | 0 | 0 | 0 |
| `T_VENUE` | 5 | 5 | 100% | 0 | 0 | 0 |

**Clean documents (false positives):** 7 high, 20 medium across 3 clean docs (target ~0 high).

| Clean doc | Clauses | High | Medium |
|---|---:|---:|---:|
| eq_004 | 83 | 3 | 8 |
| eq_010 | 92 | 4 | 6 |
| eq_016 | 66 | 0 | 6 |

**Trap documents:** 12.20 flagged clauses per doc on average; 129 flags whose category matches no planted trap ("off-trap").


**Missed traps:**

| Doc | Trap | Span clause | Match | Risk | Category | Right category flagged elsewhere |
|---|---|---|---|---|---|---|
| eq_007 | `T_CROSS_DEFAULT` | c055 | substring | low | standard | yes |
| eq_015 | `T_CROSS_DEFAULT` | c047 | substring | low | standard | no |

## §6.2 Term extraction

Outcome per field per document. *Wrong, flagged* = wrong value but the extractor reported confidence < 0.5 (the UI marks it "verify this"). *Missing* = not found (excluded from totals, never zero).

| Field | Correct | Correct (absent) | Wrong, confident | Wrong, flagged | Missing | Spurious | Exact rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| `principal` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `stated_apr` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `term_months` | 21 | 0 | 1 | 1 | 0 | 0 | 91% |
| `payment_amount` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `payment_frequency` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `balloon_amount` | 4 | 19 | 0 | 0 | 0 | 0 | 100% |

**Fees:** precision 78% (43/55 extracted), recall 77% (43/56 planted); on matched fees, value+basis correct 100%, financed flag correct 91%.

| Fee kind | Matched | Missed | Spurious | Value correct | Financed correct |
|---|---:|---:|---:|---:|---:|
| `doc` | 6 | 10 | 0 | 6 | 6 |
| `late` | 20 | 3 | 0 | 20 | 20 |
| `origination` | 10 | 0 | 9 | 10 | 6 |
| `other` | 0 | 0 | 3 | 0 | 0 |
| `prepayment` | 7 | 0 | 0 | 7 | 7 |

**Computed figures** (deterministic, from extracted terms — §2.3):

| Figure | Exact | Wrong | Withheld (incomplete) | Exact rate |
|---|---:|---:|---:|---:|
| `total_cost` | 8 | 15 | 0 | 35% |
| `effective_apr` | 8 | 15 | 0 | 35% |

## Per document

Mean wall time 170.42s (parse 16.49s, segment 0.0038s, classify 133.52s, extract 20.40s, compute 0.0017s).

| Doc | Layout | Traps planted | Caught | Flagged (H/M) | Off-trap | Total cost | Eff. APR | Grade | Time |
|---|---|---|---:|---:|---:|---|---|---|---:|
| eq_001 | rotated_table | CROSS_DEFAULT, APR_GAP | 2/2 | 2/8 | 6 | correct | correct | F | 0.015s |
| eq_002 |  | INSURANCE, PREPAY, CONFESSION | 3/3 | 3/14 | 11 | wrong | wrong | F | 0.018s |
| eq_003 |  | VENUE, UCC, ORIG_FEE | 3/3 | 5/13 | 9 | correct | correct | F | 277.51s |
| eq_004 |  | *clean* | – | 3/8 | 11 | correct | correct | D | 120.21s |
| eq_005 |  | INSURANCE, CONFESSION | 2/2 | 4/10 | 9 | wrong | wrong | F | 207.22s |
| eq_006 |  | VENUE, PREPAY, CONFESSION | 3/3 | 4/6 | 5 | wrong | wrong | D | 206.53s |
| eq_007 |  | CROSS_DEFAULT, APR_GAP, VENUE | 2/3 | 5/8 | 7 | wrong | wrong | F | 43.72s |
| eq_008 | two_column | UCC, APR_GAP, CROSS_DEFAULT | 3/3 | 1/9 | 4 | wrong | wrong | D | 133.06s |
| eq_009 |  | APR_GAP, CROSS_DEFAULT, LATE_CASCADE | 3/3 | 5/10 | 8 | correct | correct | F | 224.99s |
| eq_010 |  | *clean* | – | 4/6 | 10 | wrong | wrong | F | 206.38s |
| eq_011 |  | INSURANCE, PREPAY, UCC | 3/3 | 4/7 | 5 | wrong | wrong | D | 227.03s |
| eq_012 |  | UCC, ORIG_FEE, BALLOON, AUTO_RENEW | 4/4 | 3/9 | 4 | correct | correct | F | 139.58s |
| eq_013 |  | AUTO_RENEW, VENUE, LATE_CASCADE | 3/3 | 11/6 | 12 | correct | correct | F | 183.16s |
| eq_014 |  | PREPAY, BALLOON | 2/2 | 4/9 | 10 | correct | correct | F | 172.05s |
| eq_015 |  | CONFESSION, INSURANCE, CROSS_DEFAULT | 2/3 | 1/5 | 4 | wrong | wrong | C | 252.02s |
| eq_016 |  | *clean* | – | 0/6 | 6 | wrong | wrong | C | 76.28s |
| eq_017 | tiny_footnotes | PREPAY, LATE_CASCADE, ORIG_FEE | 3/3 | 4/10 | 5 | wrong | wrong | F | 178.33s |
| eq_018 |  | UCC, LATE_CASCADE, APR_GAP | 3/3 | 2/6 | 1 | wrong | wrong | D | 211.43s |
| eq_019 |  | LATE_CASCADE, AUTO_RENEW, PREPAY | 3/3 | 4/9 | 8 | wrong | wrong | F | 264.36s |
| eq_020 |  | VENUE, PREPAY, CONFESSION, AUTO_RENEW | 4/4 | 6/12 | 10 | wrong | wrong | F | 170.99s |
| eq_021 |  | ORIG_FEE, CROSS_DEFAULT, BALLOON | 3/3 | 1/7 | 2 | correct | correct | D | 186.65s |
| eq_022 | rotated_table | BALLOON, UCC, INSURANCE | 3/3 | 3/8 | 7 | wrong | wrong | D | 211.12s |
| eq_023 | two_column | AUTO_RENEW, APR_GAP | 2/2 | 1/5 | 2 | wrong | wrong | C | 226.95s |
