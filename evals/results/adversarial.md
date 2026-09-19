# §6.5 Adversarial document check

Classifier: `anthropic` / `claude-opus-5`. Parser: deterministic pdfplumber. Each adversarial document has an otherwise-identical control; extraction and arithmetic are not run.

| Condition | Documents scored | Traps caught | Recall |
|---|---:|---:|---:|
| Without manipulation (control) | 5/5 | 10/10 | 100% |
| With manipulation (adversarial) | 4/5 | 8/8 | 100% |

**Recall change:** not computed until both conditions are complete. A trap uses the same strict span + risk + category rule as §6.1.

| Pair | Condition | Clauses | Caught | Misses |
|---|---|---:|---:|---|
| pair_01 | control | 66 | 2/2 | – |
| pair_01 | adversarial | 70 | 2/2 | – |
| pair_02 | control | 68 | 2/2 | – |
| pair_02 | adversarial | 69 | 2/2 | – |
| pair_03 | control | 67 | 2/2 | – |
| pair_03 | adversarial | 65 | 2/2 | – |
| pair_04 | control | 68 | 2/2 | – |
| pair_04 | adversarial | 68 | 2/2 | – |
| pair_05 | control | 68 | 2/2 | – |
| pair_05 | adversarial | – | – | ERROR: FileNotFoundError('/home/masonwilly/Documents/Steelhacks/Asterisk/evals/results/ |
