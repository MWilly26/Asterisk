# Eval — parser: `vlm` · provider: `anthropic` · model: `claude-opus-5`

23/23 documents scored (2026-09-19 17:49 UTC).

## §6.1 Trap detection

**Recall: 58/58 = 100%** (target ≥ 85%). A trap counts as caught when the clause holding its planted span is flagged high/medium *with the matching category*.

| Trap type | Planted | Caught | Recall | Flagged, wrong category | Not flagged | Span not found |
|---|---:|---:|---:|---:|---:|---:|
| `T_APR_GAP` | 6 | 6 | 100% | 0 | 0 | 0 |
| `T_AUTO_RENEW` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_BALLOON` | 4 | 4 | 100% | 0 | 0 | 0 |
| `T_CONFESSION` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_CROSS_DEFAULT` | 6 | 6 | 100% | 0 | 0 | 0 |
| `T_INSURANCE` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_LATE_CASCADE` | 5 | 5 | 100% | 0 | 0 | 0 |
| `T_ORIG_FEE` | 4 | 4 | 100% | 0 | 0 | 0 |
| `T_PREPAY` | 7 | 7 | 100% | 0 | 0 | 0 |
| `T_UCC` | 6 | 6 | 100% | 0 | 0 | 0 |
| `T_VENUE` | 5 | 5 | 100% | 0 | 0 | 0 |

**Clean documents (false positives):** 1 high, 10 medium across 3 clean docs (target ~0 high).

| Clean doc | Clauses | High | Medium |
|---|---:|---:|---:|
| eq_004 | 74 | 1 | 3 |
| eq_010 | 82 | 0 | 2 |
| eq_016 | 59 | 0 | 5 |

**Trap documents:** 6.65 flagged clauses per doc on average; 35 flags whose category matches no planted trap ("off-trap").


## §6.2 Term extraction

Outcome per field per document. *Wrong, flagged* = wrong value but the extractor reported confidence < 0.5 (the UI marks it "verify this"). *Missing* = not found (excluded from totals, never zero).

| Field | Correct | Correct (absent) | Wrong, confident | Wrong, flagged | Missing | Spurious | Exact rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| `principal` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `stated_apr` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `term_months` | 21 | 0 | 0 | 0 | 2 | 0 | 91% |
| `payment_amount` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `payment_frequency` | 23 | 0 | 0 | 0 | 0 | 0 | 100% |
| `balloon_amount` | 4 | 19 | 0 | 0 | 0 | 0 | 100% |

**Fees:** precision 98% (56/57 extracted), recall 100% (56/56 planted); on matched fees, value+basis correct 100%, financed flag correct 100%.

| Fee kind | Matched | Missed | Spurious | Value correct | Financed correct |
|---|---:|---:|---:|---:|---:|
| `doc` | 16 | 0 | 0 | 16 | 16 |
| `late` | 23 | 0 | 0 | 23 | 23 |
| `origination` | 10 | 0 | 0 | 10 | 10 |
| `other` | 0 | 0 | 1 | 0 | 0 |
| `prepayment` | 7 | 0 | 0 | 7 | 7 |

**Computed figures** (deterministic, from extracted terms — §2.3):

| Figure | Exact | Wrong | Withheld (incomplete) | Exact rate |
|---|---:|---:|---:|---:|
| `total_cost` | 21 | 0 | 2 | 91% |
| `effective_apr` | 21 | 0 | 2 | 91% |

## Per document

Mean wall time 25.61s (parse 0.0052s, segment 0.0032s, classify 20.23s, extract 5.37s, compute 0.0009s).

| Doc | Layout | Traps planted | Caught | Flagged (H/M) | Off-trap | Total cost | Eff. APR | Grade | Time |
|---|---|---|---:|---:|---:|---|---|---|---:|
| eq_001 | rotated_table | CROSS_DEFAULT, APR_GAP | 2/2 | 4/3 | 2 | correct | correct | F | 28.15s |
| eq_002 |  | INSURANCE, PREPAY, CONFESSION | 3/3 | 1/4 | 1 | correct | correct | C | 33.13s |
| eq_003 |  | VENUE, UCC, ORIG_FEE | 3/3 | 6/1 | 1 | correct | correct | F | 35.40s |
| eq_004 |  | *clean* | – | 1/3 | 4 | correct | correct | C | 0.018s |
| eq_005 |  | INSURANCE, CONFESSION | 2/2 | 1/3 | 2 | missing | missing | C | 36.98s |
| eq_006 |  | VENUE, PREPAY, CONFESSION | 3/3 | 2/4 | 2 | correct | correct | C | 31.95s |
| eq_007 |  | CROSS_DEFAULT, APR_GAP, VENUE | 3/3 | 6/4 | 4 | correct | correct | F | 0.019s |
| eq_008 | two_column | UCC, APR_GAP, CROSS_DEFAULT | 3/3 | 5/2 | 1 | correct | correct | F | 36.18s |
| eq_009 |  | APR_GAP, CROSS_DEFAULT, LATE_CASCADE | 3/3 | 6/3 | 2 | correct | correct | F | 0.019s |
| eq_010 |  | *clean* | – | 0/2 | 2 | correct | correct | B | 33.41s |
| eq_011 |  | INSURANCE, PREPAY, UCC | 3/3 | 2/5 | 3 | correct | correct | D | 34.98s |
| eq_012 |  | UCC, ORIG_FEE, BALLOON, AUTO_RENEW | 4/4 | 6/2 | 1 | correct | correct | F | 0.017s |
| eq_013 |  | AUTO_RENEW, VENUE, LATE_CASCADE | 3/3 | 3/2 | 1 | correct | correct | C | 27.88s |
| eq_014 |  | PREPAY, BALLOON | 2/2 | 2/3 | 2 | correct | correct | C | 31.57s |
| eq_015 |  | CONFESSION, INSURANCE, CROSS_DEFAULT | 3/3 | 2/3 | 2 | correct | correct | D | 0.019s |
| eq_016 |  | *clean* | – | 0/5 | 5 | correct | correct | B | 25.06s |
| eq_017 | tiny_footnotes | PREPAY, LATE_CASCADE, ORIG_FEE | 3/3 | 4/5 | 1 | missing | missing | D | 40.32s |
| eq_018 |  | UCC, LATE_CASCADE, APR_GAP | 3/3 | 5/3 | 2 | correct | correct | F | 31.95s |
| eq_019 |  | LATE_CASCADE, AUTO_RENEW, PREPAY | 3/3 | 2/6 | 3 | correct | correct | D | 29.59s |
| eq_020 |  | VENUE, PREPAY, CONFESSION, AUTO_RENEW | 4/4 | 3/4 | 2 | correct | correct | D | 36.43s |
| eq_021 |  | ORIG_FEE, CROSS_DEFAULT, BALLOON | 3/3 | 5/0 | 0 | correct | correct | F | 32.48s |
| eq_022 | rotated_table | BALLOON, UCC, INSURANCE | 3/3 | 3/2 | 2 | correct | correct | C | 30.54s |
| eq_023 | two_column | AUTO_RENEW, APR_GAP | 2/2 | 4/2 | 1 | correct | correct | F | 32.84s |
