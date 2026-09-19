"""web/index.html: single file, embedded sample Analysis, ids the script relies on, served by the API."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from clause import config
from clause.types import Analysis

PAGE = config.REPO_ROOT / "web" / "index.html"


@pytest.fixture(scope="module")
def html():
    return PAGE.read_text()


@pytest.fixture(scope="module")
def script(html):
    m = re.search(r"<script>\n(.*?)</script>\n</body>", html, re.S)
    assert m, "app script must be the last <script> before </body>"
    return m.group(1)


def test_no_build_step(html):
    assert "<script src=" not in html and "<link rel=\"stylesheet\"" not in html
    assert "__SAMPLE_JSON__" not in html


def test_embedded_sample_is_a_complete_analysis(html):
    m = re.search(r'<script id="sample" type="application/json">(.*?)</script>', html, re.S)
    assert m
    a = Analysis.from_dict(json.loads(m.group(1).replace("<\\/", "</")))
    assert a.total_cost is not None and a.effective_apr is not None and not a.incomplete
    assert a.grade in "ABCDEF"
    assert any(c.risk == "high" for c in a.clauses) and any(c.risk == "standard" for c in a.clauses)
    assert all(c.span.text and c.span.page >= 1 for c in a.clauses)
    assert a.questions_to_ask and a.timings


def test_every_id_the_script_uses_exists_in_markup(html, script):
    ids = set(re.findall(r'(?:\$|show)\("#([\w-]+)', script))  # $("#x") and show("#x")
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, missing


def test_progress_list_covers_every_pipeline_stage(html):
    from clause.pipeline import STAGES
    for s in STAGES:
        assert f'data-stage="{s}"' in html


def test_script_talks_to_the_stream_endpoint_and_handles_all_events(script):
    assert "/analyze/stream" in script
    for ev in ("stage", "result", "error"):
        assert f'event === "{ev}"' in script


def test_dark_mode_tokens_defined(html):
    assert "prefers-color-scheme: dark" in html and ':root:not([data-theme="light"])' in html


def test_clause_source_is_highlighted_with_page_and_exact_text(script):
    assert '<mark>${esc(c.span.text)}</mark>' in script
    assert "Source · page ${c.span.page} · exact wording" in script
    assert 'row.setAttribute("aria-expanded", "true")' in script
    assert 'ev.key === "Enter" || ev.key === " "' in script


def test_uploaded_pdf_link_targets_source_page_without_affecting_demo(script):
    assert "URL.createObjectURL(file)" in script
    assert "URL.revokeObjectURL(currentPdfUrl)" in script
    assert "#page=${span.page}&search=${encodeURIComponent(excerpt)}" in script
    assert 'usePdf(null); render(JSON.parse($("#sample").textContent))' in script


def test_low_confidence_values_are_visibly_marked(html, script):
    assert "const LOW_CONFIDENCE = 0.5" in script
    assert "value < LOW_CONFIDENCE" in script
    assert "Verify · ${Math.round(value * 100)}%" in script
    assert "confidenceTag(f.confidence)" in script
    assert "confidenceTag(c.confidence)" in script
    assert ".low-confidence" in html and ".verify" in html


def test_missing_fields_and_totals_are_not_rendered_as_numbers(script):
    assert 'a.total_cost == null ? "Not enough stated terms" : usd(a.total_cost)' in script
    assert "Total withheld — required terms are missing." in script
    assert "missing ? \"—\" : esc(fmtField(k, v))" in script
    assert '<span class="missing-tag">Not stated</span>' in script


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_is_valid_javascript(script, tmp_path):
    f = tmp_path / "app.js"
    f.write_text(script)
    r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_served_at_root_by_api():
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as tc:
        r = tc.get("/")
    assert r.status_code == 200 and r.text.startswith("<!doctype html>")


def test_default_clause_list_flags_only_high_and_medium(script):
    """Nano labels most boilerplate `low` (1,380 of 1,930 corpus clauses) where Opus
    said `standard`; the default view must use the eval's high/medium rule, not
    `!= standard`, or a clean document opens with dozens of "flagged" rows."""
    assert 'c.risk === "high" || c.risk === "medium"' in script
    assert 'c.risk !== "standard"' not in script
