"""The morning brief (SPEC §9): the same agent, given a brief-writing prompt.

    python -m pm_agent.brief --portfolio sample_fund --date 2026-10-22   # date defaults to latest
    python -m pm_agent.brief --slack     # also post it to Slack (needs SLACK_WEBHOOK_URL)

Saves the brief to briefs/<portfolio>/<date>.md and logs the run like any other.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from pm_agent import agent, data, delivery
from pm_agent.prompts import BRIEF_PROMPT

BRIEFS_DIR = agent.REPO_ROOT / "briefs"


def resolve_date(date: str | None) -> str:
    """The brief date: the given one if it has prices, else the latest price date."""
    used, _ = data.prices_on(date)  # raises ValueError listing the available dates
    return used


def brief_request(portfolio: str, date: str) -> str:
    return (f"Write the morning brief for portfolio '{portfolio}' for {date}. Pass "
            f"portfolio='{portfolio}' to every tool, date='{date}' where a tool takes a date, "
            f"and end_date='{date}' to get_weight_changes.")


async def write_brief(portfolio: str, date: str, *, create_message=None,
                      server=None) -> tuple[str, agent.RunLog]:
    """Returns the brief's Markdown and the run log. Bad input fails before any API call."""
    data.load_portfolio(portfolio)  # raises ValueError listing the available portfolios
    date = resolve_date(date)
    log = await agent.run_agent(brief_request(portfolio, date), system=BRIEF_PROMPT,
                                create_message=create_message, server=server)
    return log.answer, log


def save_brief(portfolio: str, date: str, text: str) -> Path:
    path = BRIEFS_DIR / portfolio / f"{date}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the morning brief.")
    parser.add_argument("--portfolio", default="sample_fund")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to the latest price date")
    parser.add_argument("--slack", action="store_true", help="post the brief to Slack")
    args = parser.parse_args()

    agent.load_dotenv()
    try:
        url = delivery.webhook_url() if args.slack else None  # check before spending on the API
        date = resolve_date(args.date)
        text, log = asyncio.run(write_brief(args.portfolio, date))
    except ValueError as e:
        sys.exit(str(e))
    path = save_brief(args.portfolio, date, text)
    print(text)
    if url:
        delivery.post_to_slack(delivery.slack_message(args.portfolio, date, text), url)
        print("\n[posted to Slack]", file=sys.stderr)
    print(f"\n[saved {path.relative_to(agent.REPO_ROOT)} | {len(log.tool_calls)} tool calls | "
          f"{log.input_tokens} in / {log.output_tokens} out tokens | {log.latency_s}s]",
          file=sys.stderr)


if __name__ == "__main__":
    main()
