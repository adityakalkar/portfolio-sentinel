"""Score a morning brief against the events planted in its day (task 5.3, SPEC §10).

Pure functions, unit-tested with fake briefs. Four measures:
    recall        share of expected events the brief mentions
    false alarms  sentences claiming a breach or warning that isn't there
    ranking       the headline leads with the most important event (or says it's calm)
    made-up       numbers found in no tool result (same check as the question eval)
"""

from __future__ import annotations

import re

import pandas as pd

from scoring import hallucinated_numbers, has_phrase

BIG_MOVE = 0.05  # a price move at least this large, either way, is an event

# Gross/net flags have subject "portfolio"; a brief names them by these words instead.
RULE_WORDS = {"max_gross": "gross", "min_net": "net", "max_net": "net"}
CLAIM = re.compile(r"\b(breach\w*|warning\w*)\b", re.IGNORECASE)
# Words that make a breach/warning sentence not a claim: "no breaches", "0 warnings",
# "inside the limit", "selling would ...".
NOT_A_CLAIM = re.compile(
    r"\b(no|not|none|nothing|zero|0|without|isn't|aren't|inside|within|would|could|if)\b",
    re.IGNORECASE,
)


def expected_events(flags: list[dict], moves: pd.DataFrame) -> list[dict]:
    """The events a brief should report, most important first: breaches, warnings, then
    price moves of at least BIG_MOVE, biggest first. Built from analytics outputs."""
    rank = {"breach": 0, "warning": 1}
    events = [{"kind": f["status"], "subject": f["subject"], "rule": f["rule"]}
              for f in sorted(flags, key=lambda f: rank[f["status"]])]
    big = moves[moves["price_return"].abs() >= BIG_MOVE]
    big = big.reindex(big["price_return"].abs().sort_values(ascending=False).index)
    events += [{"kind": "move", "subject": t, "rule": None} for t in big["ticker"]]
    return events


def _event_words(event: dict) -> str:
    if event["subject"] == "portfolio":
        return RULE_WORDS.get(event["rule"], event["rule"])
    return event["subject"]


def _sentences(brief: str) -> list[str]:
    """Plain sentences: Markdown headings, list numbers and bold markers removed."""
    sentences = []
    for line in brief.splitlines():
        line = re.sub(r"^\s*(#+|\d+\.|[-*])\s*", "", line).replace("**", "").strip()
        sentences += [s for s in re.split(r"(?<=[.!?])\s+", line) if s]
    return sentences


def _headline(brief: str) -> str | None:
    for line in brief.splitlines():
        if line.strip():
            return line.lstrip("#").strip() if line.lstrip().startswith("#") else None
    return None


def false_alarms(brief: str, events: list[dict], universe: list[str]) -> list[str]:
    """Sentences that claim a breach or warning about something that has no flag.

    A claim is a sentence with "breach" or "warning" and none of the NOT_A_CLAIM words. It
    is false if it names a stock or sector with no flag, or names nothing on a day with no
    flag of that kind ("1 limit breach today" on a quiet day).
    """
    flagged = {e["subject"] for e in events if e["kind"] != "move"}
    kinds = {e["kind"] for e in events}
    alarms = []
    for sentence in _sentences(brief):
        claims = {m.group(1).lower()[:6] for m in CLAIM.finditer(sentence)}  # breach/warnin
        if not claims or NOT_A_CLAIM.search(sentence):
            continue
        named = [u for u in universe if has_phrase(sentence, u)]
        unflagged = [u for u in named if u not in flagged]
        no_such_flag = not named and not any(k[:6] in claims for k in kinds)
        if unflagged or no_such_flag:
            alarms.append(sentence)
    return alarms


def ranking_ok(brief: str, events: list[dict]) -> bool:
    """The headline exists and leads with the top event; on a day with no events, it says
    so (a calm headline that claims nothing)."""
    headline = _headline(brief)
    if headline is None:
        return False
    if events:
        return has_phrase(headline, _event_words(events[0]))
    return bool(NOT_A_CLAIM.search(headline)) or not CLAIM.search(headline)


def score_brief(brief: str, log: dict, events: list[dict], universe: list[str]) -> dict:
    missed = [f"{e['kind']} {_event_words(e)}" for e in events
              if not has_phrase(brief, _event_words(e))]
    return {
        "headline": _headline(brief),
        "expected": len(events),
        "found": len(events) - len(missed),
        "recall": (len(events) - len(missed)) / len(events) if events else None,
        "missed": missed,
        "false_alarms": false_alarms(brief, events, universe),
        "ranking_ok": ranking_ok(brief, events),
        "hallucinated": hallucinated_numbers(log),
    }


def summarize(rows: list[dict]) -> dict:
    expected = sum(r["expected"] for r in rows)
    return {
        "briefs": len(rows),
        "event_recall": sum(r["found"] for r in rows) / expected if expected else None,
        "false_alarms": sum(len(r["false_alarms"]) for r in rows),
        "quiet_day_false_alarms": sum(len(r["false_alarms"]) for r in rows if r["quiet"]),
        "ranking_ok": sum(r["ranking_ok"] for r in rows) / len(rows),
        "briefs_with_made_up_numbers": sum(bool(r["hallucinated"]) for r in rows),
        "total_cost_usd": round(sum(r["cost_usd"] or 0 for r in rows), 4),
    }
