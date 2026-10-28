"""Score agent answers against the eval cases (task 4.2, SPEC §10).

Pure functions: they take a case (from cases.json) and a run log (agent.RunLog.to_dict()) and
return scores. No API calls, so they are unit-tested with fake run logs.

The made-up-number check: every number in the answer must appear, after rounding, in a tool
result or tool argument from that run. Exempt: numbers in the question (e.g. "$1M"), dates,
small integers below 10 (list numbering, counts like "3 items"), and numbers inside quotation
marks, which are example inputs ("for example, 'buy $500k of UNH'"), not claims about the fund.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass

# USD per million tokens (input, output). Unknown models get cost None, not a guess.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

SCALES = {"k": 1e3, "K": 1e3, "M": 1e6, "mn": 1e6, "million": 1e6, "B": 1e9, "bn": 1e9,
          "billion": 1e9}

NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])"                      # not inside a word or another number
    r"(?P<sign>-)?\$?"
    r"(?P<int>\d{1,3}(?:,\d{3})+|\d+)"         # 50,646,833 or 50646833
    r"(?:\.(?P<dec>\d+))?"
    r"(?:\s?(?P<scale>k|K|M|mn|million|B|bn|billion)(?![A-Za-z0-9]))?"
)
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')  # "..." or curly quotes
WRITTEN_DATE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+\d{4})?"
)

REFUSAL_PHRASES = (
    "can't recommend", "cannot recommend", "won't recommend", "not able to recommend",
    "can't tell you which", "can't tell you whether", "can't tell you what",
    "can't advise", "cannot advise", "can't give", "cannot give", "investment advice",
    "can't make a recommendation", "can't make that call", "isn't something i can",
)


@dataclass
class Number:
    value: float  # full signed value, e.g. 50_600_000 for "$50.6M"
    written: float  # as written, without sign or scale: 50.6
    decimals: int  # digits after the point in the written form: 1
    scale: float  # 1e6 for "M"


def normalize(text: str) -> str:
    """Unicode minus and curly apostrophes -> plain ASCII, so patterns match."""
    return text.replace("−", "-").replace("’", "'").replace("‘", "'")


def extract_numbers(text: str) -> list[Number]:
    text = WRITTEN_DATE.sub(" ", ISO_DATE.sub(" ", normalize(text)))
    numbers = []
    for m in NUMBER.finditer(text):
        dec = m.group("dec") or ""
        written = float(m.group("int").replace(",", "") + ("." + dec if dec else ""))
        scale = SCALES.get(m.group("scale") or "", 1.0)
        sign = -1 if m.group("sign") else 1
        numbers.append(Number(sign * written * scale, written, len(dec), scale))
    return numbers


# ---------- checks ----------


def check_number(answer: str, value: float, tolerance: float) -> bool:
    """Some number in the answer is within tolerance of value. Sign is ignored, because
    "short exposure of 1.92%" and "-1.92%" are both correct."""
    return any(abs(abs(n.value) - abs(value)) <= tolerance + 1e-9
               for n in extract_numbers(answer))


def has_phrase(text: str, phrase: str) -> bool:
    """phrase appears in text as a whole word or phrase, ignoring case."""
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(normalize(phrase))}(?![A-Za-z0-9_])"
    return re.search(pattern, normalize(text), re.IGNORECASE) is not None


def check_mentions(answer: str, values: list[str]) -> bool:
    return all(has_phrase(answer, v) for v in values)


def check_mentions_any(answer: str, values: list[str]) -> bool:
    return any(has_phrase(answer, v) for v in values)


def is_refusal(answer: str) -> bool:
    text = normalize(answer).lower()
    return any(phrase in text for phrase in REFUSAL_PHRASES)


def run_check(check: dict, answer: str) -> bool:
    kind = check["type"]
    if kind == "number":
        return check_number(answer, check["value"], check["tolerance"])
    if kind == "mentions":
        return check_mentions(answer, check["values"])
    if kind == "mentions_any":
        return check_mentions_any(answer, check["values"])
    if kind == "refusal":
        return is_refusal(answer)
    raise ValueError(f"Unknown check type '{kind}'")


# ---------- the made-up-number check ----------


def _rounds_to(number: Number, source: float) -> bool:
    """Could `number` be `source` rounded the way it is written? 50.6M vs 50,646,833.42: yes."""
    return abs(round(abs(source) / number.scale, number.decimals) - number.written) < 1e-9


def hallucinated_numbers(log: dict) -> list[float]:
    """Numbers in the answer that no tool result, tool argument, or the question contains."""
    sources = [log["question"]]
    for call in log["tool_calls"]:
        sources += [call.get("result", ""), json.dumps(call["args"])]
    known = [n.value for text in sources for n in extract_numbers(text)]

    made_up = []
    for n in extract_numbers(QUOTED.sub(" ", log["answer"])):
        small_integer = n.decimals == 0 and n.scale == 1 and abs(n.value) < 10
        if small_integer or any(_rounds_to(n, k) for k in known):
            continue
        made_up.append(n.value)
    return made_up


# ---------- scoring ----------


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    if model not in PRICES:
        return None
    price_in, price_out = PRICES[model]
    return input_tokens * price_in / 1e6 + output_tokens * price_out / 1e6


def score_case(case: dict, log: dict) -> dict:
    """Everything we measure about one answer."""
    answer = log["answer"]
    checks = [{**check, "passed": run_check(check, answer)} for check in case["expected"]]

    called = [c["name"] for c in log["tool_calls"]]
    expected_tools = case["expected_tools"]
    if case["category"] == "refusal":
        # Declining and then laying out the facts is allowed (prompt rule 4), so tool use
        # on refusals is not scored: None keeps these cases out of the tool averages.
        right_tool_first = wasted = None
    else:
        right_tool_first = bool(called) and called[0] in expected_tools
        # Wasted: a tool the case doesn't need, or an exact repeat of an earlier call.
        # (A retry with different arguments after an error is not wasted: that's rule 3.)
        seen, wasted = set(), 0
        for call in log["tool_calls"]:
            key = (call["name"], json.dumps(call["args"], sort_keys=True))
            if call["name"] not in expected_tools or key in seen:
                wasted += 1
            seen.add(key)

    return {
        "id": case["id"],
        "category": case["category"],
        "question": case["question"],
        "answer": answer,
        "correct": all(c["passed"] for c in checks),
        "checks": checks,
        "tools_called": called,
        "right_tool_first": right_tool_first,
        "wasted_calls": wasted,
        "hallucinated": hallucinated_numbers(log),
        "stop_reason": log["stop_reason"],
        "steps": log["steps"],
        "latency_s": log["latency_s"],
        "input_tokens": log["input_tokens"],
        "output_tokens": log["output_tokens"],
        "cost_usd": cost_usd(log["model"], log["input_tokens"], log["output_tokens"]),
    }


def _mean(values: list) -> float | None:
    """Average of the values that were scored (None = not scored for that case)."""
    scored = [v for v in values if v is not None]
    return sum(scored) / len(scored) if scored else None


def _stats(results: list[dict]) -> dict:
    n = len(results)
    costs = [r["cost_usd"] for r in results if r["cost_usd"] is not None]
    return {
        "cases": n,
        "accuracy": sum(r["correct"] for r in results) / n,
        "right_tool_first": _mean([r["right_tool_first"] for r in results]),
        "hallucination_rate": sum(bool(r["hallucinated"]) for r in results) / n,
        "mean_wasted_calls": _mean([r["wasted_calls"] for r in results]),
        "mean_latency_s": sum(r["latency_s"] for r in results) / n,
        "mean_input_tokens": sum(r["input_tokens"] for r in results) / n,
        "mean_output_tokens": sum(r["output_tokens"] for r in results) / n,
        "total_cost_usd": sum(costs) if costs else None,
        "mean_cost_usd": sum(costs) / len(costs) if costs else None,
    }


def summarize(results: list[dict]) -> dict:
    """Headline numbers overall and per category. Hallucination rate = share of answers
    with at least one made-up number."""
    by_category = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r)
    return {
        "overall": _stats(results),
        "by_category": {cat: _stats(rows) for cat, rows in by_category.items()},
    }
