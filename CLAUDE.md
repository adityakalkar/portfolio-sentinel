# CLAUDE.md

Instructions for Claude Code working in this repo. Read this, `docs/SPEC.md`, and
`docs/TASKS.md` before starting any task.

## About this project and its owner

PM Agent: an AI agent for portfolio managers built on our own MCP server, with a scheduled
morning brief and an eval harness. Full design in `docs/SPEC.md`.

Working principles:
- **Readable over clever.** Every line should be easy to explain. Prefer simple code.
- **Explain non-obvious decisions** in your summary at the end of each task (why this approach,
  what the alternatives were). Keep it short.
- **Don't add frameworks or dependencies without asking.** No LangChain, LlamaIndex, CrewAI, etc.
  The agent loop is hand-written on purpose.

## Architecture rules (do not break these)

1. **The agent never does math.** All numbers come from `analytics.py` via MCP tools.
2. `analytics.py` is pure: no file reads, network, or LLM calls. Every function has
   known-answer tests in `tests/test_analytics.py`.
3. `data.py` is the only module that reads data files.
4. `mcp_server.py` is a thin layer: load data → call analytics → convert decimals to percent
   and round. No new math there.
5. Expected answers in evals are computed from analytics, never typed in by hand.

## Conventions

- Weights are decimals in code (0.05 = 5%); percent only at the MCP boundary in `*_pct` fields.
- Money in USD, `*_usd` fields. Shorts are negative shares / market value / weight.
- NAV = position market values + cash. Gross = long − short. Net = long + short.
- Dates are `YYYY-MM-DD`; default to the latest price date.
- Bad input (unknown ticker, portfolio, date) raises `ValueError` with a message that tells
  the caller what *is* available.

## MCP Python SDK v2 — read this before writing MCP code

We use `mcp>=2.3,<3`. A lot of example code online (and in your training data) is for v1.
Differences that matter here:
- `FastMCP` was renamed: use `from mcp.server import MCPServer`.
- For structured output, annotate tool returns as `dict[str, Any]` (or a pydantic model).
  A bare `dict` return type fails with "not serializable for structured output".
- To send an error message the model can read, raise
  `ToolError` from `mcp.server.mcpserver.exceptions`. **Any other exception is hidden** from
  the client as just "Error executing tool <name>". Convert `ValueError` to `ToolError` at the
  MCP layer.
- Tests can connect in-process: `async with Client(mcp_server) as client:` (`from mcp import Client`).
  For a subprocess, pass `StdioServerParameters(command=..., args=[...])` to `Client`.
- Result/tool fields are snake_case: `tool.input_schema`, `tool.output_schema`,
  `result.structured_content`, `result.is_error`.
- If something here doesn't match the installed version, check the installed package source
  (`python -c "import mcp, inspect; ..."`) rather than guessing, and say what changed.

## Anthropic SDK

- Look up current model names and SDK usage in the official docs; don't rely on memory.
- Model ID comes from the `PM_AGENT_MODEL` env var (with a sensible default in one place).
- API key from `ANTHROPIC_API_KEY` in `.env`. **Never commit `.env` or print keys.**
- Keep eval runs cheap: default to a small number of cases when iterating; only run the full
  set when asked.

## How to work

- **One task at a time** from `docs/TASKS.md`. Don't start the next task unless asked.
- **Tests first** for analytics and tools: write the known-answer test, see it fail, then implement.
- Run `pytest` before saying a task is done. All tests must pass.
- When a task is done: tick its checkbox in `docs/TASKS.md`, then summarize what changed,
  how you verified it, and anything you weren't sure about.
- **Design document (required):** at the end of every milestone in `docs/TASKS.md` (M0, M1,
  M2, ...), or of a group of its tasks done together (so far: M1a = tasks 1.1–1.2,
  M1b = 1.3–1.8), update `docs/design/design_doc.tex` before calling it done. Add a
  section to Part II (goal, what was built, how the code works with short excerpts, decisions
  and why, how it was verified, review questions, a key takeaway), extend the Part I primers
  with any new background concept (finance, AI/agents, MCP, Python, Git/GitHub/CI), and
  update the milestone map, glossary, and "Last updated" date. Write for a reader new to the
  project: explain generously, with context, background, and worked examples.
- Commit in small steps with clear messages when asked to commit.
- **Stop and ask** if the spec is ambiguous, a change would break an architecture rule, or you
  want to add a dependency.

## Commands

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q
python scripts/make_sample_data.py
python -m pm_agent.mcp_server          # MCP server over stdio
```
