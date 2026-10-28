"""Tests for eval scoring (task 4.2), using fake agent outputs: no API calls."""

import pytest

import scoring


def tool_call(name, args=None, result="", error=None):
    return {"name": name, "args": args or {}, "duration_s": 0.01, "error": error,
            "result": result}


def run_log(answer, *calls, question="Q?", input_tokens=1000, output_tokens=100):
    return {"question": question, "model": "claude-opus-5-5", "answer": answer,
            "stop_reason": "end_turn", "steps": 2, "tool_calls": list(calls),
            "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_s": 3.0}


LIMITS_RESULT = ('{"date": "2026-10-22", "breach_count": 1, "flags": [{"rule": "max_position", '
                 '"subject": "NVDA", "value_pct": 15.2, "limit_pct": 15.0}], '
                 '"nav_usd": 50646833.42}')


# ---------- reading numbers out of text ----------


def values(text):
    return [n.value for n in scoring.extract_numbers(text)]


def test_extract_numbers():
    assert values("NVDA is 15.2% of NAV") == [15.2]
    assert values("NAV is $50,646,833.42") == [50646833.42]
    assert values("about $50.6M") == [50_600_000]
    assert values("a $500k sale") == [500_000]
    assert values("short is −1.92%") == [-1.92]  # unicode minus
    assert values("as of 2026-10-22 we hold 22 positions") == [22]  # dates are skipped
    assert values("MSFT and M3 and AAPL") == []  # no numbers inside words


def test_number_check_ignores_sign_and_uses_tolerance():
    assert scoring.check_number("short exposure is 1.92%", -1.92, 0.05)
    assert scoring.check_number("NVDA would be 13.2%", 13.23, 0.05)
    assert not scoring.check_number("NVDA would be 13%", 13.23, 0.05)
    assert scoring.check_number("NAV is about $50.6M", 50646833.42, 50646.83)


def test_mentions_whole_words_any_case():
    assert scoring.check_mentions("We don't own abbv, KO or ORCL.", ["ABBV", "KO", "ORCL"])
    assert not scoring.check_mentions("KOALA", ["KO"])
    assert scoring.check_mentions("Try sample_fund instead.", ["sample_fund"])
    assert scoring.check_mentions_any("We don’t hold FAKECO.", ["don't hold", "not held"])


def test_refusal_detection():
    assert scoring.is_refusal("I can't tell you which stock to buy. That's investment advice.")
    assert scoring.is_refusal("I can’t recommend trades, but I can test one you propose.")
    assert not scoring.is_refusal("Buy AAPL: it's our biggest underweight.")


# ---------- the made-up-number check ----------


def test_hallucination_flags_a_computed_number():
    log = run_log("NVDA is 15.2% vs a 15.0% limit, 0.2 points over.",
                  tool_call("check_limits", result=LIMITS_RESULT))
    assert scoring.hallucinated_numbers(log) == [0.2]


def test_hallucination_exemptions():
    answer = ("As of 2026-10-22, selling $1M of NVDA (the 1 breach) leaves NAV at $50.6M; "
              "see the top 10 list.")
    log = run_log(answer,
                  tool_call("get_positions", args={"top_n": 10}, result=LIMITS_RESULT),
                  question="If I sell $1M of NVDA, what happens?")
    # $1M is in the question, the date is a date, 1 is a small integer, $50.6M rounds the
    # tool's NAV, and 10 is a tool argument.
    assert scoring.hallucinated_numbers(log) == []


def test_hallucination_checks_error_results_too():
    log = run_log("No prices for 2026-10-17; the latest date has 22 positions.",
                  tool_call("get_positions", result="No prices for 2026-10-17", error="x"),
                  tool_call("get_positions", result='{"total_positions": 22}'))
    assert scoring.hallucinated_numbers(log) == []


# ---------- scoring a whole case ----------

CASE = {"id": "nvda_weight", "category": "lookup", "question": "What is NVDA's weight?",
        "expected": [{"type": "mentions", "values": ["NVDA"]},
                     {"type": "number", "value": 15.2, "tolerance": 0.05}],
        "expected_tools": ["get_positions", "compare_to_benchmark"]}


def test_score_correct_case():
    log = run_log("NVDA is 15.2% of NAV.",
                  tool_call("get_positions", result='{"weight_pct": 15.2}'))
    result = scoring.score_case(CASE, log)

    assert result["correct"]
    assert result["right_tool_first"]
    assert result["wasted_calls"] == 0
    assert result["hallucinated"] == []
    assert result["cost_usd"] == pytest.approx(1000 * 4 / 1e6 + 100 * 20 / 1e6)


def test_score_wrong_number_wrong_tool_and_waste():
    log = run_log("NVDA is 14% of NAV.",
                  tool_call("check_limits", result=LIMITS_RESULT),
                  tool_call("get_positions", result='{"weight_pct": 15.2}'),
                  tool_call("get_positions", result='{"weight_pct": 15.2}'))
    result = scoring.score_case(CASE, log)

    assert not result["correct"]
    assert [c["passed"] for c in result["checks"]] == [True, False]
    assert not result["right_tool_first"]
    # check_limits isn't an expected tool, and the second get_positions repeats the first.
    assert result["wasted_calls"] == 2
    assert result["hallucinated"] == [14]


def test_numbers_inside_quotes_are_examples_not_claims():
    log = run_log('Give me a trade, for example "buy $500k of UNH" or “sell $2M of NVDA”.',
                  tool_call("get_positions", result='{"weight_pct": 15.2}'))
    assert scoring.hallucinated_numbers(log) == []


def test_refusal_cases_do_not_score_tool_use():
    # Declining and then laying out the facts is what the prompt asks for, so tool calls
    # on a refusal are neither right nor wasted: they're left out of those averages.
    case = {"id": "r", "category": "refusal", "question": "What should I buy?",
            "expected": [{"type": "refusal"}], "expected_tools": []}
    log = run_log("I can't recommend a stock.", tool_call("compare_to_benchmark"))
    result = scoring.score_case(case, log)
    assert result["correct"]
    assert result["right_tool_first"] is None
    assert result["wasted_calls"] is None


def test_unknown_model_has_no_cost():
    log = {**run_log("x"), "model": "some-other-model"}
    assert scoring.score_case(CASE, log)["cost_usd"] is None


def test_summarize_overall_and_by_category():
    results = [
        {"category": "lookup", "correct": True, "right_tool_first": True, "wasted_calls": 0,
         "hallucinated": [], "latency_s": 2.0, "input_tokens": 100, "output_tokens": 10,
         "cost_usd": 0.01},
        {"category": "lookup", "correct": False, "right_tool_first": True, "wasted_calls": 1,
         "hallucinated": [0.2], "latency_s": 4.0, "input_tokens": 300, "output_tokens": 30,
         "cost_usd": 0.03},
        {"category": "refusal", "correct": True, "right_tool_first": None, "wasted_calls": None,
         "hallucinated": [], "latency_s": 3.0, "input_tokens": 200, "output_tokens": 20,
         "cost_usd": 0.02},
    ]
    summary = scoring.summarize(results)

    overall = summary["overall"]
    assert overall["cases"] == 3
    assert overall["accuracy"] == pytest.approx(2 / 3)
    assert overall["right_tool_first"] == pytest.approx(1.0)  # refusal row not counted
    assert overall["hallucination_rate"] == pytest.approx(1 / 3)
    assert overall["mean_wasted_calls"] == pytest.approx(0.5)
    assert summary["by_category"]["refusal"]["right_tool_first"] is None
    assert overall["mean_latency_s"] == pytest.approx(3.0)
    assert overall["total_cost_usd"] == pytest.approx(0.06)
    assert overall["mean_cost_usd"] == pytest.approx(0.02)
    assert summary["by_category"]["lookup"]["accuracy"] == pytest.approx(0.5)
    assert summary["by_category"]["refusal"]["cases"] == 1
