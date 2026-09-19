# Eval — parser: `vlm` · provider: `nvidia` · model: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`

3/23 documents scored (2026-09-19 20:23 UTC). **Errors:** eq_004, eq_005, eq_006, eq_007, eq_008, eq_009, eq_010, eq_011, eq_012, eq_013, eq_014, eq_015, eq_016, eq_017, eq_018, eq_019, eq_020, eq_021, eq_022, eq_023.

## §6.1 Trap detection

**Recall: 8/8 = 100%** (target ≥ 85%). A trap counts as caught when the clause holding its planted span is flagged high/medium *with the matching category*.

| Trap type | Planted | Caught | Recall | Flagged, wrong category | Not flagged | Span not found |
|---|---:|---:|---:|---:|---:|---:|
| `T_APR_GAP` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_CONFESSION` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_CROSS_DEFAULT` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_INSURANCE` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_ORIG_FEE` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_PREPAY` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_UCC` | 1 | 1 | 100% | 0 | 0 | 0 |
| `T_VENUE` | 1 | 1 | 100% | 0 | 0 | 0 |

**Clean documents (false positives):** 0 high, 0 medium across 0 clean docs (target ~0 high).

| Clean doc | Clauses | High | Medium |
|---|---:|---:|---:|

**Trap documents:** 15.00 flagged clauses per doc on average; 26 flags whose category matches no planted trap ("off-trap").


## §6.2 Term extraction

Outcome per field per document. *Wrong, flagged* = wrong value but the extractor reported confidence < 0.5 (the UI marks it "verify this"). *Missing* = not found (excluded from totals, never zero).

| Field | Correct | Correct (absent) | Wrong, confident | Wrong, flagged | Missing | Spurious | Exact rate |
|---|---:|---:|---:|---:|---:|---:|---:|
| `principal` | 3 | 0 | 0 | 0 | 0 | 0 | 100% |
| `stated_apr` | 3 | 0 | 0 | 0 | 0 | 0 | 100% |
| `term_months` | 3 | 0 | 0 | 0 | 0 | 0 | 100% |
| `payment_amount` | 3 | 0 | 0 | 0 | 0 | 0 | 100% |
| `payment_frequency` | 3 | 0 | 0 | 0 | 0 | 0 | 100% |
| `balloon_amount` | 0 | 3 | 0 | 0 | 0 | 0 | 100% |

**Fees:** precision 88% (7/8 extracted), recall 88% (7/8 planted); on matched fees, value+basis correct 100%, financed flag correct 100%.

| Fee kind | Matched | Missed | Spurious | Value correct | Financed correct |
|---|---:|---:|---:|---:|---:|
| `doc` | 1 | 1 | 0 | 1 | 1 |
| `late` | 3 | 0 | 0 | 3 | 3 |
| `origination` | 2 | 0 | 1 | 2 | 2 |
| `prepayment` | 1 | 0 | 0 | 1 | 1 |

**Computed figures** (deterministic, from extracted terms — §2.3):

| Figure | Exact | Wrong | Withheld (incomplete) | Exact rate |
|---|---:|---:|---:|---:|
| `total_cost` | 2 | 1 | 0 | 67% |
| `effective_apr` | 2 | 1 | 0 | 67% |

## Per document

Mean wall time 92.52s (parse 0.007s, segment 0.004s, classify 87.51s, extract 4.99s, compute 0.0013s).

| Doc | Layout | Traps planted | Caught | Flagged (H/M) | Off-trap | Total cost | Eff. APR | Grade | Time |
|---|---|---|---:|---:|---:|---|---|---|---:|
| eq_001 | rotated_table | CROSS_DEFAULT, APR_GAP | 2/2 | 2/8 | 6 | correct | correct | F | 0.015s |
| eq_002 |  | INSURANCE, PREPAY, CONFESSION | 3/3 | 3/14 | 11 | wrong | wrong | F | 0.018s |
| eq_003 |  | VENUE, UCC, ORIG_FEE | 3/3 | 5/13 | 9 | correct | correct | F | 277.51s |
| eq_004 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_005 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_006 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_007 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_008 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_009 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_010 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_011 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_012 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_013 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_014 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_015 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_016 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_017 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_018 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_019 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_020 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_021 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_022 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
| eq_023 | | | | | | ERROR: FileNotFoundError('not run: no saved analysis') | | | |
