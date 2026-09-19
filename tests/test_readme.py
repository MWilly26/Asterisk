"""README promises remain tied to committed evidence and runnable setup."""

import re

from clause import config

README = config.REPO_ROOT / "README.md"


def test_readme_has_required_t20_sections_and_honest_results():
    text = README.read_text()
    for heading in ("## Architecture", "## Results", "## Run locally",
                    "## Synthetic data and safety", "## Known limitations"):
        assert heading in text
    assert "both reached **58/58 trap recall**" in text
    assert "Structured table-row blocks | 436 | 0" in text
    assert "Spans lost during parsing | 0 | 5" in text
    assert "4/5 documents" in text


def test_readme_nano_claims_trace_to_committed_eval_and_stay_honest():
    """T18: every Nano number in the README must match eval_nano.json, and the
    negative extraction result and the no-cost-ratio statement must stay."""
    import json
    text = README.read_text()
    ev = json.load(open(config.REPO_ROOT / "evals" / "results" / "eval_nano.json"))
    a = ev["aggregate"]
    assert a["n_scored"] == a["n_docs"] == 23 and not a["errors"]
    td = a["trap_detection"]
    assert f"{td['caught']}/{td['planted']}" == "56/58" and "56/58 (97%)" in text
    assert f"{td['clean_high_total']} high, {td['clean_medium_total']} medium" in text
    assert f"Off-trap flags across 20 trap docs | 35 | {td['trap_docs_off_trap_total']} |" in text
    c = a["computed"]["total_cost"]
    assert f"{c['correct']}/23 exact, {c['wrong']} wrong, {c['missing']} withheld" in text
    f = a["fees"]
    assert f"{f['matched']}/{f['gold']}" in text and f"{f['matched']}/{f['extracted']}" in text
    assert "eval_nano.json" in text and "eval_nano.md" in text
    assert "no cost ratio is claimed" in text
    assert "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning" in text and "HTTP 410" in text
    assert "not claimed to be faster or cheaper" in text
    assert "T_CROSS_DEFAULT" in text and "term_months=52" in text
    assert "eval_nano_partial" not in text


def test_every_link_to_local_evidence_exists():
    text = README.read_text()
    targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
    local = [target for target in targets if not target.startswith(("http://", "https://"))]
    assert local
    assert all((config.REPO_ROOT / target).is_file() for target in local)


def test_setup_uses_venv_and_declared_runtime_dependency():
    text = README.read_text()
    assert ".venv/bin/pip install" in text
    assert ".venv/bin/python data/generate.py" in text
    assert "set -a && . ./.env && set +a" in text
    assert '"pypdfium2"' in (config.REPO_ROOT / "pyproject.toml").read_text()


def test_example_environment_contains_no_key_material():
    values = {}
    for line in (config.REPO_ROOT / ".env.example").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    assert values["ANTHROPIC_API_KEY"] == ""
    assert values["NVIDIA_API_KEY"] == ""
