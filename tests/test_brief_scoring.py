"""Tests for morning brief scoring (task 5.3), using fake briefs: no API calls."""

import pandas as pd

import brief_scoring as bs

UNIVERSE = ["NVDA", "MSFT", "AAPL", "UNH", "META", "TSLA", "Information Technology"]


def flag(subject, status, rule="max_position", value=0.152):
    return {"rule": rule, "subject": subject, "value": value, "limit": 0.15, "status": status}


def moves(**returns):
    return pd.DataFrame({"ticker": list(returns), "price_return": list(returns.values())})


def log(answer, results=()):
    return {"question": "Write the morning brief for 2026-10-22.", "answer": answer,
            "tool_calls": [{"name": "t", "args": {}, "result": r} for r in results]}


# ---------- expected events ----------


def test_expected_events_are_ranked_breach_warning_then_biggest_move():
    events = bs.expected_events(
        [flag("AAPL", "warning", value=0.14), flag("NVDA", "breach")],
        moves(NVDA=0.10, UNH=-0.08, MSFT=0.01, META=-0.12),
    )
    assert [(e["kind"], e["subject"]) for e in events] == [
        ("breach", "NVDA"), ("warning", "AAPL"),
        ("move", "META"), ("move", "NVDA"), ("move", "UNH"),  # |move| >= 5%, biggest first
    ]


# ---------- recall, false alarms, ranking ----------

STORY_EVENTS = bs.expected_events([flag("NVDA", "breach")], moves(NVDA=0.10, UNH=-0.08))
STORY_BRIEF = ("# 1 limit breach: NVDA at 15.2% vs a 15.0% limit\n\n"
               "1. **NVDA is over its limit.** It rose 10.0% overnight to 15.2%.\n"
               "2. **No warnings.** Nothing else is near a limit.\n"
               "3. **UNH fell 8.0%.** Its weight is now 3.18%.\n")


def test_good_brief_scores_full_marks():
    result = bs.score_brief(STORY_BRIEF, log(STORY_BRIEF, ['{"v": 15.2, "w": 15.0, '
                                                          '"r": 10.0, "u": 8.0, "x": 3.18}']),
                            STORY_EVENTS, UNIVERSE)
    assert result["recall"] == 1.0
    assert result["missed"] == []
    assert result["false_alarms"] == []
    assert result["ranking_ok"]
    assert result["hallucinated"] == []


def test_missed_event_lowers_recall():
    brief = "# 1 limit breach: NVDA at 15.2%\n\n1. NVDA rose 10.0%.\n"
    result = bs.score_brief(brief, log(brief, ['{"v": 15.2, "r": 10.0}']), STORY_EVENTS, UNIVERSE)
    assert result["missed"] == ["move UNH"]
    assert result["recall"] == 2 / 3


def test_breach_claim_about_an_unflagged_stock_is_a_false_alarm():
    brief = "# Quiet night\n\n1. AAPL breached its position limit.\n"
    result = bs.score_brief(brief, log(brief), [], UNIVERSE)
    assert result["false_alarms"] == ["AAPL breached its position limit."]


def test_negated_mentions_are_not_false_alarms():
    brief = ("# No limit breaches; a quiet night\n\n"
             "1. MSFT and AAPL are well inside their limits; no warnings.\n"
             "2. Nothing else is near a limit.\n")
    result = bs.score_brief(brief, log(brief), [], UNIVERSE)
    assert result["false_alarms"] == []
    assert result["ranking_ok"]  # calm headline on a day with no events


def test_zero_counts_are_not_claims():
    # Found in the first real run: calm sentences that report a count of zero.
    brief = "# Quiet night\n\n1. The limit check found 0 breaches and 0 warnings.\n"
    assert bs.score_brief(brief, log(brief), [], UNIVERSE)["false_alarms"] == []
    brief = "# Quiet night\n\n1. There are zero warnings today.\n"
    assert bs.score_brief(brief, log(brief), [], UNIVERSE)["false_alarms"] == []


def test_quiet_day_headline_claiming_a_breach_fails_ranking_and_is_a_false_alarm():
    brief = "# 1 limit breach today\n\n1. Things moved a little.\n"
    result = bs.score_brief(brief, log(brief), [], UNIVERSE)
    assert not result["ranking_ok"]
    assert result["false_alarms"] == ["1 limit breach today"]


def test_headline_must_lead_with_the_most_important_event():
    brief = "# UNH fell 8.0% overnight\n\n1. NVDA breached its limit at 15.2%.\n"
    result = bs.score_brief(brief, log(brief), STORY_EVENTS, UNIVERSE)
    assert not result["ranking_ok"]


def test_missing_headline_fails_ranking():
    result = bs.score_brief("NVDA breached its limit.", log("x"), STORY_EVENTS, UNIVERSE)
    assert not result["ranking_ok"]


def test_made_up_numbers_use_the_same_check_as_the_question_eval():
    brief = "# NVDA breach\n\n1. NVDA is 15.2%, 0.2 points over.\n"
    result = bs.score_brief(brief, log(brief, ['{"v": 15.2}']), STORY_EVENTS, UNIVERSE)
    assert result["hallucinated"] == [0.2]


def test_summarize_briefs():
    rows = [
        {"day": "a", "expected": 2, "found": 2, "false_alarms": [], "ranking_ok": True,
         "hallucinated": [], "quiet": False, "cost_usd": 0.1},
        {"day": "b", "expected": 0, "found": 0, "false_alarms": ["x"], "ranking_ok": False,
         "hallucinated": [0.2], "quiet": True, "cost_usd": 0.1},
    ]
    s = bs.summarize(rows)
    assert s["event_recall"] == 1.0
    assert s["false_alarms"] == 1
    assert s["quiet_day_false_alarms"] == 1
    assert s["ranking_ok"] == 0.5
    assert s["briefs_with_made_up_numbers"] == 1
    assert s["total_cost_usd"] == 0.2
