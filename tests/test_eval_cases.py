"""Tests for the eval question list (task 4.1).

The answer key is computed from analytics (CLAUDE.md rule 5), so these tests check its shape,
spot-check a few answers we know by hand from the M1 tables, and check that the committed
evals/cases.json is in sync with the data.
"""

import json
from pathlib import Path

import pytest

import build_cases

CASES_FILE = Path(__file__).resolve().parents[1] / "evals" / "cases.json"
CATEGORIES = {"lookup", "calculation", "comparison", "multi_step", "what_if", "error_handling",
              "refusal"}


@pytest.fixture(autouse=True)
def use_sample_data(monkeypatch):
    monkeypatch.delenv("PM_AGENT_DATA_DIR", raising=False)


@pytest.fixture(scope="module")
def cases():
    return {c["id"]: c for c in build_cases.build()}


def test_shape(cases):
    assert len(cases) >= 30
    assert {c["category"] for c in cases.values()} == CATEGORIES
    for c in cases.values():
        assert set(c) == {"id", "category", "question", "expected", "expected_tools"}
        assert c["question"]
        assert c["expected"], f"{c['id']} has no checks"
        for check in c["expected"]:
            assert check["type"] in {"number", "mentions", "mentions_any", "refusal"}


def test_refusal_cases_need_no_tools(cases):
    for c in cases.values():
        if c["category"] == "refusal":
            assert c["expected_tools"] == []
            assert c["expected"] == [{"type": "refusal"}]


def test_known_answers(cases):
    # Numbers we know by hand from the M1 story tables.
    assert {"type": "number", "value": 15.2, "tolerance": 0.05} in cases["nvda_weight"]["expected"]
    assert {"type": "mentions", "values": ["NVDA"]} in cases["most_overweight"]["expected"]
    sale = cases["sell_1m_nvda"]["expected"]
    assert {"type": "number", "value": 13.23, "tolerance": 0.05} in sale
    assert cases["sell_1m_nvda"]["expected_tools"] == ["simulate_trades"]


def test_committed_cases_file_is_up_to_date():
    # If the data or the builder changes, regenerate: python evals/build_cases.py
    assert json.loads(CASES_FILE.read_text()) == build_cases.build()
