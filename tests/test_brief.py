"""Tests for the morning brief generator (task 5.1), using a fake model: no API calls."""

import asyncio

import pytest
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock

from pm_agent import agent, brief, prompts
from pm_agent.mcp_server import server
from test_agent import FakeModel, reply


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.delenv("PM_AGENT_DATA_DIR", raising=False)
    monkeypatch.setattr(agent, "RUNS_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(brief, "BRIEFS_DIR", tmp_path / "briefs")


BRIEF_TEXT = "# 1 limit breach: NVDA at 15.2% vs a 15% limit\n\n1. **NVDA breach** ..."


def fake_brief_model():
    return FakeModel(
        reply("tool_use", BetaToolUseBlock(type="tool_use", id="c1", name="check_limits",
                                           input={"date": "2026-10-22"})),
        reply("end_turn", BetaTextBlock(type="text", text=BRIEF_TEXT)),
    )


def write(fake, date="2026-10-22"):
    return asyncio.run(brief.write_brief("sample_fund", date, create_message=fake,
                                         server=server))


def test_brief_uses_the_brief_prompt_and_names_portfolio_and_date():
    fake = fake_brief_model()
    text, log = write(fake)

    request = fake.requests[0]
    assert request["system"].startswith(prompts.BRIEF_PROMPT)
    question = request["messages"][0]["content"]
    assert "sample_fund" in question and "2026-10-22" in question
    assert text == BRIEF_TEXT
    assert [c.name for c in log.tool_calls] == ["check_limits"]


def test_brief_prompt_has_the_spec_rules():
    p = prompts.BRIEF_PROMPT
    assert "check_limits" in p and "get_weight_changes" in p and "get_sector_exposure" in p
    assert "breaches first" in p.lower()
    assert "simulate_trades" in p  # sizing a trade for breaches only
    assert "must appear in a tool result" in p


def test_save_brief_writes_markdown_under_portfolio_and_date():
    path = brief.save_brief("sample_fund", "2026-10-22", BRIEF_TEXT)
    assert path == brief.BRIEFS_DIR / "sample_fund" / "2026-10-22.md"
    assert path.read_text().startswith(BRIEF_TEXT)


def test_bad_portfolio_or_date_fails_before_any_api_call():
    fake = FakeModel()  # no replies scripted: any API call would fail the test
    with pytest.raises(ValueError, match="Unknown portfolio 'nope'"):
        asyncio.run(brief.write_brief("nope", "2026-10-22", create_message=fake, server=server))
    with pytest.raises(ValueError, match="No prices for 2026-10-17"):
        asyncio.run(brief.write_brief("sample_fund", "2026-10-17", create_message=fake,
                                      server=server))
    assert fake.requests == []


def test_date_defaults_to_latest():
    assert brief.resolve_date(None) == "2026-10-22"
    assert brief.resolve_date("2026-10-21") == "2026-10-21"
