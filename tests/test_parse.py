"""parse.py normalization and the nemotron-parse response mapping. No network."""

import json

from clause.client import MockClient, _flatten, _nemotron_element_to_blocks
from clause.parse import normalize_blocks, parse_pdf
from clause.types import TextBlock


def B(page, text, kind="paragraph", bbox=None):
    return TextBlock(page=page, text=text, bbox=bbox, kind=kind)


def test_parse_pdf_uses_client_and_normalizes(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-fake")
    c = MockClient(responses=[json.dumps([
        {"page": 2, "text": "  second   page ", "kind": "paragraph"},
        {"page": 1, "text": "Fee | Amount\nOrigination | 3.5%\n", "kind": "table_row"},
        {"page": 1, "text": "   ", "kind": "paragraph"},
    ])], log_path=tmp_path / "l.jsonl")
    blocks = parse_pdf(pdf, client=c)
    assert [(b.page, b.text, b.kind) for b in blocks] == [
        (1, "Fee | Amount", "table_row"), (1, "Origination | 3.5%", "table_row"), (2, "second page", "paragraph")]
    assert c.calls[0]["kind"] == "parse"


def test_normalize_is_stable_within_page():
    blocks = [B(1, "a"), B(1, "b"), B(1, "c")]
    assert [b.text for b in normalize_blocks(blocks)] == ["a", "b", "c"]


def test_nemotron_element_mapping():
    el = {"bbox": {"xmin": 0.1, "ymin": 0.2, "xmax": 0.9, "ymax": 0.25}, "text": "## 3. Fee Schedule", "type": "Section-header"}
    [b] = _nemotron_element_to_blocks(el, 4)
    assert b == {"page": 4, "text": "## 3. Fee Schedule", "bbox": [0.1, 0.2, 0.9, 0.25], "kind": "heading"}
    assert _nemotron_element_to_blocks({"text": "", "type": "Text"}, 1) == []
    [fn] = _nemotron_element_to_blocks({"text": "1 A final balloon payment...", "type": "Footnote"}, 2)
    assert fn["kind"] == "footnote"


def test_nemotron_table_split_into_rows():
    el = {"text": "| Fee | Amount | When |\n|---|---|---|\n| Origination Fee | 3.50% of principal | Financed |\n| Late Charge | $25.00 | Upon late payment |",
          "type": "Table", "bbox": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}}
    rows = _nemotron_element_to_blocks(el, 7)
    assert [r["text"] for r in rows] == [
        "Fee | Amount | When",
        "Origination Fee | 3.50% of principal | Financed",
        "Late Charge | $25.00 | Upon late payment",
    ]
    assert all(r["kind"] == "table_row" and r["page"] == 7 for r in rows)


def test_flatten_handles_nested_pages():
    assert _flatten([[{"a": 1}], [{"b": 2}, {"c": 3}]]) == [{"a": 1}, {"b": 2}, {"c": 3}]
    assert _flatten({"a": 1}) == [{"a": 1}]
