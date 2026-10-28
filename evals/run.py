"""Run the agent on the eval cases, score it, and save the results (tasks 4.2, 4.3).

Every case is a real API call, so iterate on a few cases and run the full set on purpose:

    python evals/run.py --limit 3                  # first 3 cases (a few cents)
    python evals/run.py --category what_if         # one category
    python evals/run.py --case sell_1m_nvda        # one case
    python evals/run.py --label baseline           # all 35 cases (~$1-2)

Prints a summary table, saves per-case results to evals/results/<timestamp>.json, and
appends a summary row to evals/results.db.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import history
import scoring

from pm_agent import agent

EVALS_DIR = Path(__file__).resolve().parent
CASES_FILE = EVALS_DIR / "cases.json"
RESULTS_DIR = EVALS_DIR / "results"
RESULTS_DB = EVALS_DIR / "results.db"


def select_cases(cases: list[dict], limit: int | None, category: str | None,
                 case_id: str | None) -> tuple[list[dict], str]:
    """The cases to run, and a short description of the filter for the history table."""
    if case_id:
        chosen = [c for c in cases if c["id"] == case_id]
        if not chosen:
            raise SystemExit(f"No case '{case_id}'. Ids: {', '.join(c['id'] for c in cases)}")
        return chosen, f"case={case_id}"
    if category:
        cases = [c for c in cases if c["category"] == category]
        if not cases:
            raise SystemExit(f"No category '{category}'.")
    chosen = cases[:limit] if limit else cases
    parts = [f"category={category}"] if category else []
    parts += [f"limit={limit}"] if limit else []
    return chosen, ", ".join(parts) or "all"


async def run_cases(cases: list[dict]) -> list[dict]:
    results = []
    for i, case in enumerate(cases, 1):
        log = (await agent.run_agent(case["question"])).to_dict()
        result = {**scoring.score_case(case, log), "tool_calls": log["tool_calls"]}
        results.append(result)
        mark = "PASS" if result["correct"] else "FAIL"
        extra = f"  made-up numbers: {result['hallucinated']}" if result["hallucinated"] else ""
        print(f"[{i}/{len(cases)}] {mark}  {case['id']:<22} "
              f"tools: {', '.join(result['tools_called']) or 'none'}{extra}", flush=True)
    return results


def percent(x: float | None) -> str:
    return "  n/a" if x is None else f"{100 * x:5.1f}%"


def print_summary(summary: dict) -> None:
    header = (f"{'category':<16}{'cases':>6}{'correct':>9}{'tool 1st':>10}{'made-up':>9}"
              f"{'wasted':>8}{'latency':>9}{'tokens in/out':>16}{'cost':>9}")
    print("\n" + header + "\n" + "-" * len(header))
    rows = list(summary["by_category"].items()) + [("OVERALL", summary["overall"])]
    for name, s in rows:
        cost = f"${s['total_cost_usd']:.3f}" if s["total_cost_usd"] is not None else "n/a"
        tokens = f"{s['mean_input_tokens']:.0f}/{s['mean_output_tokens']:.0f}"
        wasted = "n/a" if s["mean_wasted_calls"] is None else f"{s['mean_wasted_calls']:.2f}"
        print(f"{name:<16}{s['cases']:>6}{percent(s['accuracy']):>9}"
              f"{percent(s['right_tool_first']):>10}{percent(s['hallucination_rate']):>9}"
              f"{wasted:>8}{s['mean_latency_s']:>8.1f}s{tokens:>16}{cost:>9}")
    print("(latency and tokens are per case; cost is the total for the run)")


def git_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True,
                               text=True, check=True).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and score the agent on eval cases.")
    parser.add_argument("--limit", type=int, help="run only the first N cases")
    parser.add_argument("--category", help="run only this category")
    parser.add_argument("--case", dest="case_id", help="run only this case id")
    parser.add_argument("--label", default="", help="what this run is testing, e.g. baseline")
    args = parser.parse_args()

    agent.load_dotenv()
    cases, case_filter = select_cases(json.loads(CASES_FILE.read_text()), args.limit,
                                      args.category, args.case_id)
    print(f"Running {len(cases)} case(s) with {agent.model_id()} (effort {agent.EFFORT})\n")

    results = asyncio.run(run_cases(cases))
    summary = scoring.summarize(results)
    print_summary(summary)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_info = {"timestamp": timestamp, "label": args.label, "model": agent.model_id(),
                "effort": agent.EFFORT, "git_commit": git_commit(), "case_filter": case_filter}
    RESULTS_DIR.mkdir(exist_ok=True)
    results_file = RESULTS_DIR / f"{timestamp}.json"
    results_file.write_text(json.dumps(
        {**run_info, "summary": summary, "results": results}, indent=2) + "\n")
    history.append_run(RESULTS_DB, {
        **run_info, **summary["overall"],
        "results_file": str(results_file.relative_to(EVALS_DIR.parent)),
    })
    print(f"\nSaved {results_file.relative_to(EVALS_DIR.parent)} and a row in "
          f"{RESULTS_DB.relative_to(EVALS_DIR.parent)}")


if __name__ == "__main__":
    main()
