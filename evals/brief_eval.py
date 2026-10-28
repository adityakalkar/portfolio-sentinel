"""Run the morning brief on planted-event days and score it (task 5.3, SPEC §10).

    python evals/brief_eval.py               # all 6 days (~$0.60)
    python evals/brief_eval.py --day quiet   # one day

For each day, the expected events come from analytics on that day's data (CLAUDE.md rule 5).
Prints a table and saves the briefs and scores to evals/results/brief-<timestamp>.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone

import brief_scoring
import scoring

from pm_agent import agent, analytics, brief, data

DATA_DIR = agent.REPO_ROOT / "data"
RESULTS_DIR = agent.REPO_ROOT / "evals" / "results"
PORTFOLIO = "sample_fund"

# day id -> (data directory, brief date; None = the latest date in that data)
DAYS = {
    "sample_10_21": (DATA_DIR / "sample", "2026-10-21"),  # calm, one warning
    "sample_10_22": (DATA_DIR / "sample", "2026-10-22"),  # the NVDA/UNH story
    "quiet": (DATA_DIR / "scenarios" / "quiet", None),
    "big_move": (DATA_DIR / "scenarios" / "big_move", None),
    "single_breach": (DATA_DIR / "scenarios" / "single_breach", None),
    "two_events": (DATA_DIR / "scenarios" / "two_events", None),
}


def expected_for(date: str) -> tuple[list[dict], list[str]]:
    """Events planted on this date, and every name a brief could mention (for false alarms)."""
    fund = data.load_portfolio(PORTFOLIO)
    sectors = data.sector_map()
    _, prices = data.prices_on(date)
    _, prev_prices = data.prices_on(data.previous_date(date))
    flags = analytics.check_limits(fund, prices, sectors, data.benchmark_weights(fund.benchmark))
    moves = analytics.weight_changes(fund, prev_prices, prices)
    universe = list(sectors.index) + sorted(set(sectors))
    return brief_scoring.expected_events(flags, moves), universe


async def run_day(day: str) -> dict:
    data_dir, date = DAYS[day]
    os.environ["PM_AGENT_DATA_DIR"] = str(data_dir)  # data.py and the MCP server read this
    date = brief.resolve_date(date)
    events, universe = expected_for(date)
    text, log = await brief.write_brief(PORTFOLIO, date)
    result = brief_scoring.score_brief(text, log.to_dict(), events, universe)
    return {"day": day, "date": date, "quiet": not events, **result,
            "events": [f"{e['kind']} {e['subject']}" for e in events],
            "cost_usd": scoring.cost_usd(log.model, log.input_tokens, log.output_tokens),
            "latency_s": log.latency_s, "brief": text}


def print_table(rows: list[dict], summary: dict) -> None:
    print(f"\n{'day':<15}{'events found':>14}{'false alarms':>14}{'headline ok':>13}"
          f"{'made-up':>9}{'cost':>8}")
    print("-" * 73)
    for r in rows:
        print(f"{r['day']:<15}{r['found']:>8}/{r['expected']:<5}{len(r['false_alarms']):>14}"
              f"{'yes' if r['ranking_ok'] else 'NO':>13}{len(r['hallucinated']):>9}"
              f"{'$%.3f' % (r['cost_usd'] or 0):>8}")
    recall = summary["event_recall"]
    print(f"\nEvent recall {recall:.0%} | false alarms {summary['false_alarms']} "
          f"(quiet day: {summary['quiet_day_false_alarms']}) | headline leads with the top "
          f"event {summary['ranking_ok']:.0%} | briefs with made-up numbers "
          f"{summary['briefs_with_made_up_numbers']} | cost ${summary['total_cost_usd']:.2f}")
    for r in rows:
        for problem in ([f"missed {m}" for m in r["missed"]]
                        + [f"false alarm: {s}" for s in r["false_alarms"]]
                        + [f"made-up numbers: {r['hallucinated']}"] * bool(r["hallucinated"])):
            print(f"  {r['day']}: {problem}")


async def run_all(days: list[str]) -> list[dict]:
    rows = []
    for day in days:
        print(f"Writing brief: {day} ...", flush=True)
        rows.append(await run_day(day))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Score the morning brief on planted days.")
    parser.add_argument("--day", choices=sorted(DAYS), help="run only this day")
    args = parser.parse_args()

    agent.load_dotenv()
    saved_env = os.environ.get("PM_AGENT_DATA_DIR")
    try:
        rows = asyncio.run(run_all([args.day] if args.day else list(DAYS)))
    finally:  # put the data directory back the way it was
        if saved_env is None:
            os.environ.pop("PM_AGENT_DATA_DIR", None)
        else:
            os.environ["PM_AGENT_DATA_DIR"] = saved_env

    summary = brief_scoring.summarize(rows)
    print_table(rows, summary)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"brief-{timestamp}.json"
    path.write_text(json.dumps({"timestamp": timestamp, "model": agent.model_id(),
                                "summary": summary, "days": rows}, indent=2) + "\n")
    print(f"\nSaved {path.relative_to(agent.REPO_ROOT)}")


if __name__ == "__main__":
    main()
