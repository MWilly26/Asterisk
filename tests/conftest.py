"""Shared fixtures. Also makes the script directories (data/, evals/) and the
repo root (for `api.main`) importable."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(ROOT / "data"), str(ROOT / "evals")):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="session")
def corpus(tmp_path_factory):
    """Generated PDFs + golden entries, built once per test session."""
    import generate as gen
    out = tmp_path_factory.mktemp("corpus")
    gen.generate(out_dir=out, golden_path=out / "golden.json", seed=42)
    return json.loads((out / "golden.json").read_text())
