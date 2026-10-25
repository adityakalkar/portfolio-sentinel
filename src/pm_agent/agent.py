"""The agent: a hand-written loop between Claude and our MCP server (SPEC §8).

    python -m pm_agent.agent "Are we breaching any limits?"

1. Start the MCP server (a subprocess over stdio) and read its tool definitions.
2. Send the question and the tools to Claude.
3. If Claude asks for tools, run them through MCP and send the results back. Repeat.
4. Stop when Claude answers, or after max_steps model calls.

Every run returns a RunLog: the answer, each tool call (args, duration, error), tokens and
latency, and is appended to logs/runs.jsonl. The model is injectable (create_message), so
tests use a fake and cost nothing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters

from pm_agent.prompts import SYSTEM_PROMPT

DEFAULT_MODEL = "claude-opus-5-5"
MAX_STEPS = 10  # model calls per question, so a confused model can't loop forever
MAX_TOKENS = 16000
EFFORT = "medium"  # thinking depth; Opus 5.5's default, set explicitly so it's visible
# If a safety classifier declines a request, the API retries it on a fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_LOG = REPO_ROOT / "logs" / "runs.jsonl"  # one JSON line per run; metrics are built from it


def model_id() -> str:
    return os.environ.get("PM_AGENT_MODEL") or DEFAULT_MODEL


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Read KEY=value lines from .env into the environment. Variables already set win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    duration_s: float
    error: str | None = None


@dataclass
class RunLog:
    question: str
    model: str
    answer: str = ""
    stop_reason: str = ""  # end_turn, max_steps, refusal, max_tokens, ...
    steps: int = 0  # model calls made
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def append_run_log(log: RunLog, path: Path | None = None) -> None:
    """Append one run to the JSON Lines log (one JSON object per line)."""
    path = path or RUNS_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(log.to_dict()) + "\n")


def server_params() -> StdioServerParameters:
    """How to start our MCP server as a subprocess, with the same Python as this process."""
    env = {}
    if "PM_AGENT_DATA_DIR" in os.environ:  # evals point the tools at other data
        env["PM_AGENT_DATA_DIR"] = os.environ["PM_AGENT_DATA_DIR"]
    return StdioServerParameters(
        command=sys.executable, args=["-m", "pm_agent.mcp_server"], env=env
    )


def claude_create_message():
    """The real model: an async function that sends one request to the Claude API."""
    import anthropic  # imported here so tests with a fake model never need an API key

    client = anthropic.AsyncAnthropic()  # reads ANTHROPIC_API_KEY

    async def create(**request):
        return await client.beta.messages.create(
            betas=[FALLBACK_BETA], fallbacks="default", **request
        )

    return create


def to_claude_tool(tool) -> dict[str, Any]:
    """MCP tool definition -> Claude API tool definition (same three fields, renamed)."""
    return {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}


def result_text(result) -> str:
    """What the model sees from a tool: the structured JSON, or the error message."""
    if result.structured_content is not None and not result.is_error:
        return json.dumps(result.structured_content)
    return "\n".join(block.text for block in result.content if block.type == "text")


def final_text(content) -> str:
    return "\n\n".join(block.text for block in content if block.type == "text").strip()


async def run_agent(
    question: str,
    *,
    create_message=None,
    server=None,
    system: str | None = None,
    max_steps: int = MAX_STEPS,
) -> RunLog:
    """Answer one question. create_message and server default to the real API and the
    MCP server subprocess; tests pass a fake model and the in-process server. system
    defaults to the Q&A prompt; the server's own instructions are always appended."""
    create_message = create_message or claude_create_message()
    log = RunLog(question=question, model=model_id())
    started = time.perf_counter()

    async with Client(server or server_params()) as mcp:
        tools = [to_claude_tool(t) for t in (await mcp.list_tools()).tools]
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        request = {
            "model": log.model,
            "max_tokens": MAX_TOKENS,
            "system": f"{system or SYSTEM_PROMPT}\n\n{mcp.instructions or ''}".strip(),
            "tools": tools,
            "output_config": {"effort": EFFORT},
        }

        while log.steps < max_steps:
            response = await create_message(**request, messages=messages)
            log.steps += 1
            log.input_tokens += response.usage.input_tokens
            log.output_tokens += response.usage.output_tokens
            # Send the reply back exactly as received (thinking blocks included), so the
            # conversation history stays append-only.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason != "tool_use":
                log.stop_reason = response.stop_reason
                log.answer = final_text(response.content) or (
                    f"(No answer: the model stopped with '{response.stop_reason}'.)"
                )
                break

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_started = time.perf_counter()
                result = await mcp.call_tool(block.name, block.input)
                text = result_text(result)
                log.tool_calls.append(ToolCall(
                    name=block.name,
                    args=block.input,
                    duration_s=round(time.perf_counter() - tool_started, 3),
                    error=text if result.is_error else None,
                ))
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": text, "is_error": bool(result.is_error)})
            # All results from one turn go back together, in one user message.
            messages.append({"role": "user", "content": results})
        else:
            log.stop_reason = "max_steps"
            log.answer = f"(Stopped after {max_steps} steps without a final answer.)"

    log.latency_s = round(time.perf_counter() - started, 3)
    append_run_log(log)
    return log


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit('Usage: python -m pm_agent.agent "Are we breaching any limits?"')
    load_dotenv()
    log = asyncio.run(run_agent(sys.argv[1]))
    print(log.answer)
    calls = ", ".join(c.name + (" (error)" if c.error else "") for c in log.tool_calls)
    print(
        f"\n[{log.model} | {log.steps} model calls | tools: {calls or 'none'} | "
        f"{log.input_tokens} in / {log.output_tokens} out tokens | {log.latency_s}s]",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
