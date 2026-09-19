"""classify.py with a mock client: batching, field fill, graceful degradation."""

import json

from clause import config
from clause.classify import SCHEMA, classify, system_prompt
from clause.client import MockClient
from clause.types import Clause, SourceSpan


def C(i, text="Borrower shall pay a fee."):
    return Clause(id=f"c{i:03d}", text=text, span=SourceSpan(page=1, text=text))


def R(clauses, **over):
    return json.dumps({"results": [
        {"id": c.id, "category": "standard", "risk": "standard",
         "plain_language": f"You agree to {c.id}.", "confidence": 0.9} | over for c in clauses]})


def client(*responses, tmp_path):
    return MockClient(responses=list(responses), log_path=tmp_path / "l.jsonl")


def test_prompt_lists_every_category():
    p = system_prompt()
    assert all(f'"{k}"' in p for k in config.CATEGORIES)
    assert "{categories}" not in p


def test_schema_enums_match_config():
    item = SCHEMA["properties"]["results"]["items"]["properties"]
    assert item["category"]["enum"] == list(config.CATEGORIES)
    assert item["risk"]["enum"] == config.RISK_LEVELS


def test_fills_fields_and_does_not_mutate(tmp_path):
    cl = [C(1), C(2)]
    resp = json.dumps({"results": [
        {"id": "c001", "category": "balloon", "risk": "high", "plain_language": "You owe a big final payment.", "confidence": 0.95},
        {"id": "c002", "category": "standard", "risk": "standard", "plain_language": "Boilerplate.", "confidence": 0.8},
    ]})
    out = classify(cl, client(resp, tmp_path=tmp_path))
    assert (out[0].category, out[0].risk, out[0].confidence) == ("balloon", "high", 0.95)
    assert out[0].plain_language == "You owe a big final payment."
    assert out[0].text == cl[0].text and out[0].span == cl[0].span
    assert cl[0].category is None  # input untouched


def test_batches_of_eight(tmp_path):
    cl = [C(i) for i in range(1, 20)]  # 19 clauses -> 8 + 8 + 3
    c = client(R(cl[:8]), R(cl[8:16]), R(cl[16:]), tmp_path=tmp_path)
    out = classify(cl, c)
    assert len(c.calls) == 3
    assert [x.id for x in out] == [x.id for x in cl]
    assert all(x.risk == "standard" for x in out)
    # batch message carries the ids and text
    assert "[c009]" in c.calls[1]["messages"][1]["content"]


def test_uses_json_schema_and_default_model(tmp_path):
    c = client(R([C(1)]), tmp_path=tmp_path)
    classify([C(1)], c)
    assert c.calls[0]["json_schema"] == SCHEMA
    assert c.calls[0]["model"] == config.default_model()


def test_bad_json_retries_once_then_degrades(tmp_path):
    cl = [C(1), C(2)]
    c = client("not json", "{still bad", tmp_path=tmp_path)
    out = classify(cl, c)
    assert len(c.calls) == 2
    assert all(x.risk is None and x.category is None for x in out)
    # retry appended a corrective message so the cache key differs
    assert len(c.calls[1]["messages"]) == 3


def test_bad_json_then_good(tmp_path):
    cl = [C(1)]
    c = client("garbage", R(cl, risk="high", category="venue"), tmp_path=tmp_path)
    out = classify(cl, c)
    assert out[0].risk == "high" and out[0].category == "venue"


def test_missing_and_unknown_ids_degrade_only_those(tmp_path):
    cl = [C(1), C(2), C(3)]
    resp = json.dumps({"results": [
        {"id": "c001", "category": "venue", "risk": "medium", "plain_language": "x", "confidence": 0.7},
        {"id": "c999", "category": "venue", "risk": "high", "plain_language": "x", "confidence": 0.7},
    ]})
    out = classify(cl, client(resp, tmp_path=tmp_path))
    assert out[0].risk == "medium"
    assert out[1].risk is None and out[2].risk is None


def test_invalid_enum_values_become_none(tmp_path):
    cl = [C(1)]
    resp = json.dumps({"results": [{"id": "c001", "category": "made_up", "risk": "extreme",
                                    "plain_language": "  ", "confidence": 7}]})
    [x] = classify(cl, client(resp, tmp_path=tmp_path))
    assert x.category is None and x.risk is None and x.plain_language is None
    assert x.confidence == 1.0  # clamped


def test_empty_input(tmp_path):
    c = client(tmp_path=tmp_path)
    assert classify([], c) == [] and c.calls == []
