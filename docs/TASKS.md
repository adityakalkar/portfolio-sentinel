# PM Agent — Task Backlog

Work top to bottom. Each task lists what "done" means. A box is ticked when its acceptance
criteria pass and the change has been reviewed.

---

## M0 — Repo setup

- [x] **0.1 Project skeleton**
  - `pyproject.toml` (setuptools, src layout, package `pm_agent`), deps: `mcp>=2.3,<3`,
    `pandas`, `numpy`; dev extra: `pytest`. pytest config with `pythonpath = ["src"]`.
  - Folders: `src/pm_agent/`, `tests/`, `scripts/`, `data/sample/`, `evals/`, `briefs/`.
  - `.gitignore` (Python, `.venv`, `.env`, `evals/results/`), `.env.example`.
  - **Done when:** `pip install -e ".[dev]"` works in a fresh venv and `pytest` runs (0 tests is fine).
- [x] **0.2 CI**
  - `.github/workflows/ci.yml`: on push and PR, Python 3.12, install, run `pytest`.
  - **Done when:** the workflow file is valid and runs green after the first push.

## M1 — Sample data and analytics core

- [x] **1.1 Sample data generator** — `scripts/make_sample_data.py` per SPEC §5 (deterministic,
  seeded, planted NVDA/UNH story).
  - **Done when:** running it creates all files in `data/sample/`, and running it twice gives
    identical files.
- [x] **1.2 `data.py` loaders** — portfolios, benchmarks, securities, prices, `prices_on(date)`,
  `available_dates()`, `previous_date()`, `list_portfolios()`; `PM_AGENT_DATA_DIR` override.
  - **Done when:** unknown portfolio/date raise `ValueError` listing what is available (tested).
- [x] **1.3 Weights and exposure** — `Portfolio` dataclass, `position_weights`,
  `exposure_summary` (NAV, long, short, gross, net, cash weight, counts).
  - **Done when:** known-answer tests pass, including a short position case
    ($100 long, $20 short, $20 cash → gross 120%, net 80%) and a missing-price error.
- [x] **1.4 Benchmark comparison** — `active_weights`, `sector_exposure` (long/short/net/gross,
  plus benchmark and active when a benchmark is given).
  - **Done when:** tests cover names held but not in the benchmark, benchmark names not held,
    and a sector containing a short.
- [x] **1.5 Limits** — `check_limits` per SPEC §6.
  - **Done when:** tests cover breach, warning, all-clear, the net buffer rule, and unknown keys.
- [x] **1.6 What-if trades** — `apply_trades` (returns a new portfolio; original unchanged).
  - **Done when:** tests cover buying more, opening a new position, fully closing one, cash
    movement, unknown ticker error, and "trimming fixes a breach".
- [x] **1.7 Weight changes** — `weight_changes(portfolio, prices_start, prices_end)`.
  - **Done when:** a "price doubles" test passes (50/50 → 66.7/33.3).
- [x] **1.8 Sample data sanity tests** — benchmark sums to 1; every ticker has a sector and a
  price on every date; the planted story holds (NVDA warning on 10-21, breach on 10-22).
  Checks the **exact** set of flags per SPEC §5 (only the NVDA warning on 10-21), and that a
  $1M NVDA sale on 10-22 lands clearly below the 13.5% warning line.

## M2 — MCP server

- [x] **2.1 Server and tools** — `src/pm_agent/mcp_server.py` with the 8 tools in SPEC §7,
  structured output, read-only annotations, `ValueError` → `ToolError`.
  - **Done when:** tests using the in-process `Client` check that all 8 tools are listed with
    descriptions, `check_limits` returns the NVDA breach in percent, a simulated $1M NVDA sale
    resolves it, and a bad portfolio name returns a readable error.
- [x] **2.2 Connect to Claude Code** — add the server to Claude Code with project scope and
  try it by chatting.
  - **Done when:** `claude mcp list` shows it connected and Claude Code answers
    "are we breaching any limits?" correctly using the tools.
- [x] **2.3 Tool description pass** — rewrite any tool description that made Claude Code pick
  the wrong tool or ask for clarification during 2.2. Note what you changed in `docs/NOTES.md`.

## M3 — The agent (harness)

- [x] **3.1 Agent loop** — `src/pm_agent/agent.py`: connect to the MCP server over stdio, load
  tool definitions from `list_tools()`, run the loop per SPEC §8, return the final answer plus
  a run log (tool calls with args/duration/errors, tokens, latency). Max-steps guard.
  - **Done when:** `python -m pm_agent.agent "Are we breaching any limits?"` prints a correct
    answer, and a unit test runs the loop against a **fake model** (no API call) that requests
    one tool and then answers.
- [x] **3.2 System prompt** — `src/pm_agent/prompts.py` with the rules in SPEC §8.
- [x] **3.3 Run logging** — each run appended to `logs/runs.jsonl`.
- [x] **3.4 Loop walkthrough** — document, message by message, what the loop sends and
  receives for "Are we breaching any limits?" (in the design document).

## M4 — Eval harness

- [x] **4.1 Case builder** — `evals/build_cases.py` → `evals/cases.json` with ~30 cases to start
  (grow to 50–100), all categories from SPEC §10. Expected values computed from analytics.
- [x] **4.2 Runner and scoring** — `evals/run.py`: run the agent per case, score numbers within
  tolerance, ticker/flag answers, refusals, tool usage, hallucinated numbers, latency, tokens,
  cost. `--limit N` and `--category X` flags for cheap iteration.
  - **Done when:** a run prints a summary table and saves results; scoring logic has unit tests
    using fake agent outputs.
- [x] **4.3 Results history** — append a summary row per run to `evals/results.db` (SQLite).
- [x] **4.4 First improvement loop** — run, find the worst category, change one thing (tool
  description, prompt, tool output shape), rerun. Log before/after in `docs/NOTES.md`.

## M5 — Morning brief

- [x] **5.1 Brief generator** — `src/pm_agent/brief.py` per SPEC §9, saves markdown.
  - **Done when:** the brief for 2026-10-22 leads with the NVDA breach and mentions the UNH drop.
- [x] **5.2 Scenario days** — extend the data generator with planted-event scenarios (breach,
  big move, quiet day, two events at once) in a separate data dir.
- [x] **5.3 Brief eval** — event recall, false alarms on quiet days, number accuracy, ranking.
- [ ] **5.4 Schedule it** — `.github/workflows/morning-brief.yml` with manual
  `workflow_dispatch` only until M6 (see SPEC §9); add the `0 12 * * 1-5` cron after real data
  lands. API key from GitHub Secrets, brief saved as a workflow artifact.
- [ ] **5.5 Delivery** — Slack incoming webhook or email (secret in GitHub Secrets).

## M6 — Real data

- [ ] **6.1 Real benchmark** — loader for an ETF holdings CSV (e.g. an S&P 500 ETF's published
  holdings file, downloaded manually) → `benchmarks/<name>.csv` + sectors.
- [ ] **6.2 Real portfolio from a 13F** — load an institution's 13F holdings from SEC EDGAR
  (send a User-Agent with contact info, respect rate limits) into portfolio JSON.
- [ ] **6.3 Real prices** — daily closes via yfinance (unofficial; note that in README), cached.

## M7 — UI and demo

- [ ] **7.1 Streamlit chat page** — pick a portfolio and date, ask questions, show answers and
  the tool calls behind them.
- [ ] **7.2 Metrics dashboard** — eval scores over time from `evals/results.db`.
- [ ] **7.3 Deploy** — Streamlit Community Cloud, with a password or demo mode so visitors can't
  run up the API bill.
- [ ] **7.4 README + 2-minute demo video.**

## M8 — Stretch (pick 2–3)

- [ ] **8.1 Model comparison** — run evals across models; chart accuracy vs cost vs latency.
- [ ] **8.2 Answer verifier** — reject answers containing numbers not found in tool results; retry.
- [ ] **8.3 Prompt injection tests** — poisoned tool output (e.g. a fake filing) with hidden
  instructions; measure how often the agent follows them, before/after defenses.
- [ ] **8.4 Tracing view** — click into any run and see each step.
- [ ] **8.5 Memory across briefs** — "NVDA has been in the warning band 4 days running."
- [ ] **8.6 Leveraged-fund mode** — target tracking for 2x single-stock ETFs (fund daily return
  vs 2x the stock's), collateral (T-bills/cash) as a holding type.
- [ ] **8.7 Phase 2: thematic basket builder** — agent reads SEC filings to build a theme basket
  with evidence per pick; feeds it into `simulate_trades`.
