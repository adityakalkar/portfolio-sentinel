"""Deliver the morning brief to Slack through an incoming webhook (task 5.5).

An incoming webhook is a private URL: anything POSTed to it appears as a message in one
Slack channel. The URL is a secret (anyone with it can post), so it comes from the
SLACK_WEBHOOK_URL environment variable (.env locally, GitHub Secrets in Actions) and is never
printed. Uses only the standard library.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request


def webhook_url() -> str:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url:
        raise ValueError("Set SLACK_WEBHOOK_URL (in .env, or as a GitHub secret) to post to Slack")
    return url


def to_slack_markdown(markdown: str) -> str:
    """Slack's formatting differs from Markdown: *bold* (not **bold**), and no # headings,
    so the headline becomes a bold line."""
    lines = []
    for line in markdown.strip().splitlines():
        if line.startswith("#"):
            line = "*" + line.lstrip("#").strip() + "*"
        lines.append(re.sub(r"\*\*(.+?)\*\*", r"*\1*", line))
    return "\n".join(lines)


def slack_message(portfolio: str, date: str, brief: str) -> str:
    return f"_Morning brief: {portfolio}, {date}_\n" + to_slack_markdown(brief)


def post_to_slack(text: str, url: str, opener=urllib.request.urlopen) -> None:
    """POST the message. opener is replaceable so tests never touch the network."""
    request = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with opener(request, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"Slack returned HTTP {response.status}; the brief was not posted")
