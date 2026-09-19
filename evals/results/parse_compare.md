# §6.3 Parse layer comparison — provider `anthropic`, model `claude-opus-5`

Same pipeline, same classifier, same extractor; only the parse stage differs. 23 / 23 docs scored per column (2026-09-19 18:15 UTC).

## Trap detection (§6.1)

| | VLM parse | pdfplumber |
|---|---:|---:|
| **Recall (all traps)** | **58/58 = 100%** | **58/58 = 100%** |
| `T_APR_GAP` | 6/6 | 6/6 |
| `T_AUTO_RENEW` | 5/5 | 5/5 |
| `T_BALLOON` | 4/4 | 4/4 |
| `T_CONFESSION` | 5/5 | 5/5 |
| `T_CROSS_DEFAULT` | 6/6 | 6/6 |
| `T_INSURANCE` | 5/5 | 5/5 |
| `T_LATE_CASCADE` | 5/5 | 5/5 |
| `T_ORIG_FEE` | 4/4 | 4/4 |
| `T_PREPAY` | 7/7 | 7/7 |
| `T_UCC` | 6/6 | 6/6 |
| `T_VENUE` | 5/5 | 5/5 |
| Missed: flagged, wrong category | 0 | 0 |
| Missed: not flagged | 0 | 0 |
| Missed: span not in any clause | 0 | 0 |
| Clean-doc false positives (high / medium) | 1 / 10 | 1 / 7 |
| Off-trap flags on trap docs | 35 | 44 |

## Term extraction (§6.2)

| | VLM parse | pdfplumber |
|---|---:|---:|
| `principal` exact | 100% (0 wrong, 0 missing) | 100% (0 wrong, 0 missing) |
| `stated_apr` exact | 100% (0 wrong, 0 missing) | 100% (0 wrong, 0 missing) |
| `term_months` exact | 91% (0 wrong, 2 missing) | 91% (0 wrong, 2 missing) |
| `payment_amount` exact | 100% (0 wrong, 0 missing) | 100% (0 wrong, 0 missing) |
| `payment_frequency` exact | 100% (0 wrong, 0 missing) | 100% (0 wrong, 0 missing) |
| `balloon_amount` exact | 100% (0 wrong, 0 missing) | 100% (0 wrong, 0 missing) |
| Fees: recall | 100% | 100% |
| Fees: precision | 98% | 97% |
| Fees: value+basis correct (of matched) | 100% | 100% |
| Fees: financed flag correct (of matched) | 100% | 100% |
|   `doc` matched / missed / spurious | 16 / 0 / 0 | 16 / 0 / 0 |
|   `late` matched / missed / spurious | 23 / 0 / 0 | 23 / 0 / 1 |
|   `origination` matched / missed / spurious | 10 / 0 / 0 | 10 / 0 / 0 |
|   `other` matched / missed / spurious | 0 / 0 / 1 | 0 / 0 / 1 |
|   `prepayment` matched / missed / spurious | 7 / 0 / 0 | 7 / 0 / 0 |
| `total_cost` exact / wrong / withheld | 21 / 0 / 2 | 21 / 0 / 2 |
| `effective_apr` exact / wrong / withheld | 21 / 0 / 2 | 21 / 0 / 2 |

## What each parser handed downstream

Block kinds are what the parser labelled; trap spans are checked as literal substrings of the parsed text and then of a single segmented clause (the classifier only sees clauses).

| | VLM parse | pdfplumber |
|---|---:|---:|
| Blocks per doc (mean) | 112.78 | 94.87 |
| Clauses per doc (mean) | 75.74 | 65.91 |
| `heading` blocks (total) | 577 | 546 |
| `table_row` blocks (total) | 436 | 0 |
| `footnote` blocks (total) | 225 | 0 |
| Trap spans intact in parsed text | 58/58 | 53/58 |
| Trap spans inside exactly one clause | 58/58 | 53/58 |
|   lost in parse (not in text at all) | 0 | 5 |
|   present but split across clauses | 0 | 0 |
|   present in more than one clause | 0 | 0 |
| Mean pipeline time (s) | 25.61 | 27.97 |
|   parse stage (s) | 0.0052 | 0.3253 |

## Per document

| Doc | Layout | Traps | Caught (VLM) | Caught (pdfplumber) | Total cost (VLM) | Total cost (pdfplumber) | Spans intact (VLM / pdfplumber) |
|---|---|---|---:|---:|---|---|---|
| eq_001 | rotated_table | CROSS_DEFAULT, APR_GAP | 2/2 | 2/2 | correct | correct | 2/2 / 2/2 |
| eq_002 |  | INSURANCE, PREPAY, CONFESSION | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_003 |  | VENUE, UCC, ORIG_FEE | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_004 |  | *clean* | – | – | correct | correct | 0/0 / 0/0 |
| eq_005 |  | INSURANCE, CONFESSION | 2/2 | 2/2 | missing | missing | 2/2 / 2/2 |
| eq_006 |  | VENUE, PREPAY, CONFESSION | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_007 |  | CROSS_DEFAULT, APR_GAP, VENUE | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_008 | two_column | UCC, APR_GAP, CROSS_DEFAULT | 3/3 | 3/3 | correct | correct | 3/3 / 0/3 |
| eq_009 |  | APR_GAP, CROSS_DEFAULT, LATE_CASCADE | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_010 |  | *clean* | – | – | correct | correct | 0/0 / 0/0 |
| eq_011 |  | INSURANCE, PREPAY, UCC | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_012 |  | UCC, ORIG_FEE, BALLOON, AUTO_RENEW | 4/4 | 4/4 | correct | correct | 4/4 / 4/4 |
| eq_013 |  | AUTO_RENEW, VENUE, LATE_CASCADE | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_014 |  | PREPAY, BALLOON | 2/2 | 2/2 | correct | correct | 2/2 / 2/2 |
| eq_015 |  | CONFESSION, INSURANCE, CROSS_DEFAULT | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_016 |  | *clean* | – | – | correct | correct | 0/0 / 0/0 |
| eq_017 | tiny_footnotes | PREPAY, LATE_CASCADE, ORIG_FEE | 3/3 | 3/3 | missing | missing | 3/3 / 3/3 |
| eq_018 |  | UCC, LATE_CASCADE, APR_GAP | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_019 |  | LATE_CASCADE, AUTO_RENEW, PREPAY | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_020 |  | VENUE, PREPAY, CONFESSION, AUTO_RENEW | 4/4 | 4/4 | correct | correct | 4/4 / 4/4 |
| eq_021 |  | ORIG_FEE, CROSS_DEFAULT, BALLOON | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_022 | rotated_table | BALLOON, UCC, INSURANCE | 3/3 | 3/3 | correct | correct | 3/3 / 3/3 |
| eq_023 | two_column | AUTO_RENEW, APR_GAP | 2/2 | 2/2 | correct | correct | 2/2 / 0/2 |
