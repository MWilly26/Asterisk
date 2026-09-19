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
    assert "There is no Nano column yet" in text
    assert "4/5 documents" in text


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
