"""Tests for the agent loop, using a fake model (no API call, no cost).

The fake plays Claude's part from a script: first it asks for a tool, then it answers.
The tools are real: the loop talks to our actual MCP server, in-process.
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from anthropic.types.beta import BetaTextBlock, BetaToolUseBlock

from pm_agent import agent, prompts
from pm_agent.mcp_server import server


@pytest.fixture(autouse=True)
def use_sample_data(monkeypatch):
    monkeypatch.delenv("PM_AGENT_DATA_DIR", raising=False)


@pytest.fixture(autouse=True)
def runs_log(tmp_path, monkeypatch):
    """Send run logs to a temp file, so tests never write to the real logs/runs.jsonl."""
    path = tmp_path / "logs" / "runs.jsonl"
    monkeypatch.setattr(agent, "RUNS_LOG", path)
    return path


def reply(stop_reason, *blocks, input_tokens=100, output_tokens=20):
    """A stand-in for the API's response object, with only the fields the loop reads."""
    return SimpleNamespace(
        content=list(blocks),
        stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
    )


class FakeModel:
    """Returns scripted replies in order and records every request it was sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    async def __call__(self, **request):
        # Copy the message list: the loop keeps appending to the same list afterwards.
        self.requests.append({**request, "messages": list(request["messages"])})
        return self.replies.pop(0)


def ask_tool(name, tool_id="call_1", **args):
    return BetaToolUseBlock(type="tool_use", id=tool_id, name=name, input=args)


def text(value):
    return BetaTextBlock(type="text", text=value)


def run(question, fake, **kwargs):
    return asyncio.run(agent.run_agent(question, create_message=fake, server=server, **kwargs))


def test_one_tool_call_then_answer():
    fake = FakeModel(
        reply("tool_use", text("Let me check."), ask_tool("check_limits")),
        reply("end_turn", text("Yes: NVDA is at 15.2% vs a 15% limit."), output_tokens=30),
    )
    log = run("Are we breaching any limits?", fake)

    assert log.answer == "Yes: NVDA is at 15.2% vs a 15% limit."
    assert log.stop_reason == "end_turn"
    assert log.steps == 2
    assert [(c.name, c.args, c.error) for c in log.tool_calls] == [("check_limits", {}, None)]
    assert log.tool_calls[0].duration_s >= 0
    assert (log.input_tokens, log.output_tokens) == (200, 50)
    assert log.latency_s >= 0


def test_tool_definitions_come_from_the_mcp_server():
    fake = FakeModel(reply("end_turn", text("Hello.")))
    run("Hi", fake)

    tools = {t["name"]: t for t in fake.requests[0]["tools"]}
    assert len(tools) == 8
    assert "Example" in tools["check_limits"]["description"]
    assert tools["simulate_trades"]["input_schema"]["required"] == ["trades"]


def test_tool_result_is_sent_back_to_the_model():
    fake = FakeModel(
        reply("tool_use", ask_tool("check_limits", tool_id="call_42")),
        reply("end_turn", text("Done.")),
    )
    run("Are we breaching any limits?", fake)

    messages = fake.requests[1]["messages"]
    # user question, assistant tool request (sent back unchanged), user tool result
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    result = messages[2]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "call_42"
    assert not result["is_error"]
    assert '"value_pct": 15.2' in result["content"]


def test_tool_error_is_passed_to_the_model_and_logged():
    fake = FakeModel(
        reply("tool_use", ask_tool("check_limits", portfolio="nope")),
        reply("end_turn", text("That portfolio doesn't exist; sample_fund does.")),
    )
    log = run("Check limits for nope", fake)

    result = fake.requests[1]["messages"][2]["content"][0]
    assert result["is_error"]
    assert "Available: sample_fund" in result["content"]
    assert "Unknown portfolio 'nope'" in log.tool_calls[0].error


def test_several_tool_calls_in_one_turn_return_in_one_message():
    fake = FakeModel(
        reply("tool_use", ask_tool("get_positions", "a", top_n=5),
              ask_tool("compare_to_benchmark", "b")),
        reply("end_turn", text("Here you go.")),
    )
    log = run("Top positions and biggest bets?", fake)

    results = fake.requests[1]["messages"][2]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]
    assert [c.name for c in log.tool_calls] == ["get_positions", "compare_to_benchmark"]


def test_max_steps_guard_stops_a_model_that_never_answers():
    fake = FakeModel(*[reply("tool_use", ask_tool("check_limits", f"c{i}")) for i in range(3)])
    log = run("Loop forever", fake, max_steps=3)

    assert log.stop_reason == "max_steps"
    assert log.steps == 3
    assert len(log.tool_calls) == 3
    assert "3 steps" in log.answer


def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("PM_AGENT_MODEL", "claude-test-model")
    fake = FakeModel(reply("end_turn", text("Hi.")))
    log = run("Hi", fake)
    assert fake.requests[0]["model"] == "claude-test-model"
    assert log.model == "claude-test-model"


def test_default_model(monkeypatch):
    monkeypatch.delenv("PM_AGENT_MODEL", raising=False)
    assert agent.model_id() == "claude-opus-5-5"


def test_load_dotenv_does_not_override_existing_vars(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nPM_AGENT_FOO=from_file\nPM_AGENT_BAR=\"quoted\"\n")
    monkeypatch.setenv("PM_AGENT_FOO", "already_set")
    monkeypatch.delenv("PM_AGENT_BAR", raising=False)

    agent.load_dotenv(env_file)

    assert os.environ["PM_AGENT_FOO"] == "already_set"
    assert os.environ["PM_AGENT_BAR"] == "quoted"
    monkeypatch.delenv("PM_AGENT_BAR")


def test_system_prompt_has_the_rules_and_the_server_instructions():
    fake = FakeModel(reply("end_turn", text("Hi.")))
    run("Hi", fake)

    system = fake.requests[0]["system"]
    assert system.startswith(prompts.SYSTEM_PROMPT)
    assert "must appear in a tool result" in system  # rule 1
    assert "investment advice" in system  # rule 4
    assert "Fields ending _usd are" in system  # units, from the MCP server's instructions


def test_custom_system_prompt_still_gets_server_instructions():
    fake = FakeModel(reply("end_turn", text("Hi.")))
    run("Hi", fake, system="Write a morning brief.")

    system = fake.requests[0]["system"]
    assert system.startswith("Write a morning brief.")
    assert "Fields ending _usd are" in system


def test_each_run_is_appended_to_the_runs_log(runs_log):
    run("Are we breaching any limits?", FakeModel(
        reply("tool_use", ask_tool("check_limits")),
        reply("end_turn", text("Yes: NVDA.")),
    ))
    run("Hi", FakeModel(reply("end_turn", text("Hello."))))

    lines = runs_log.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["question"] == "Are we breaching any limits?"
    assert first["answer"] == "Yes: NVDA."
    assert first["tool_calls"][0]["name"] == "check_limits"
    assert first["tool_calls"][0]["args"] == {}
    assert first["tool_calls"][0]["error"] is None
    assert '"value_pct": 15.2' in first["tool_calls"][0]["result"]  # what the model saw
    assert {"model", "stop_reason", "steps", "input_tokens", "output_tokens",
            "latency_s", "timestamp"} <= set(first)
    assert json.loads(lines[1])["question"] == "Hi"
