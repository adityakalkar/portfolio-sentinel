"""Build the eval question list: evals/cases.json (task 4.1, SPEC §10).

Every expected answer is computed here from analytics.py on the sample data, never typed in
by hand (CLAUDE.md rule 5). If the data changes, rerun this script and the answer key follows.

    python evals/build_cases.py

Each case: id, category, question, expected (a list of checks, all must pass), and
expected_tools (the tools a good answer uses; [] means none). Check types:
    number        some number in the answer is within tolerance of value (sign ignored)
    mentions      every value appears in the answer (whole word, any case)
    mentions_any  at least one value appears
    refusal       the answer declines to give investment advice
Percent values are in percent (15.2 = 15.2%), rounded as the MCP tools round them.
"""

from __future__ import annotations

import json
from pathlib import Path

from pm_agent import analytics, data

CASES_FILE = Path(__file__).resolve().parent / "cases.json"
PORTFOLIO = "sample_fund"
PCT_TOLERANCE = 0.05  # allows "13.2%" for 13.23%, not "13%"


def pct(weight: float) -> float:
    """Decimal weight -> percent, rounded like the MCP tools (0.152034 -> 15.2)."""
    return round(float(weight) * 100, 2)


def number(value: float, tolerance: float = PCT_TOLERANCE) -> dict:
    return {"type": "number", "value": value, "tolerance": round(tolerance, 2)}


def usd(value: float) -> dict:
    """A dollar amount; 0.1% tolerance accepts "$50.6M" for $50,646,833.42."""
    return number(round(value, 2), tolerance=0.001 * value)


def mentions(*values: str) -> dict:
    return {"type": "mentions", "values": list(values)}


def mentions_any(*values: str) -> dict:
    return {"type": "mentions_any", "values": list(values)}


REFUSAL = {"type": "refusal"}


def case(id: str, category: str, question: str, expected: list[dict], tools: list[str]) -> dict:
    return {"id": id, "category": category, "question": question, "expected": expected,
            "expected_tools": tools}


def build() -> list[dict]:
    fund = data.load_portfolio(PORTFOLIO)
    sectors = data.sector_map()
    bench = data.benchmark_weights(fund.benchmark)
    dates = data.available_dates()
    latest, prices = data.prices_on()
    previous = data.previous_date(latest)
    _, prev_prices = data.prices_on(previous)
    first, first_prices = data.prices_on(dates[0])

    summary = analytics.exposure_summary(fund, prices)
    weights = analytics.position_weights(fund, prices).set_index("ticker")
    active = analytics.active_weights(fund, prices, bench).set_index("ticker")["active_weight"]
    sector = analytics.sector_exposure(fund, prices, sectors, bench).set_index("sector")
    overnight = analytics.weight_changes(fund, prev_prices, prices).set_index("ticker")
    since_first = analytics.weight_changes(fund, first_prices, prices).set_index("ticker")
    flags_prev = analytics.check_limits(fund, prev_prices, sectors, bench)

    def after(trades):
        """Weights, summary and flag changes after a what-if trade on the latest date."""
        new = analytics.apply_trades(fund, prices, trades)
        flags = analytics.compare_flags(
            analytics.check_limits(fund, prices, sectors, bench),
            analytics.check_limits(new, prices, sectors, bench),
        )
        new_weights = analytics.position_weights(new, prices).set_index("ticker")["weight"]
        return new_weights, analytics.exposure_summary(new, prices), flags

    sell_1m, _, _ = after([{"ticker": "NVDA", "dollars": -1_000_000}])
    sell_500k, _, _ = after([{"ticker": "NVDA", "dollars": -500_000}])
    swap, _, _ = after([{"ticker": "NVDA", "dollars": -1_000_000},
                        {"ticker": "UNH", "dollars": 1_000_000}])
    _, _, amd_flags = after([{"ticker": "AMD", "dollars": 2_000_000}])
    amd_breach = next(f for f in amd_flags["new"] if f["subject"] == "AMD")
    intc_value = abs(float(weights.loc["INTC", "market_value"]))
    _, covered, _ = after([{"ticker": "INTC", "dollars": intc_value}])

    biggest_loser = overnight["price_return"].idxmin()
    nvda_prev_flag = next(f for f in flags_prev if f["subject"] == "NVDA")
    not_owned = sorted(set(bench.index) - set(fund.positions))

    return [
        # ---- lookup: one fact, one tool ----
        case("nvda_weight", "lookup", "What is NVDA's weight in the fund?",
             [number(pct(weights.loc["NVDA", "weight"]))],
             ["get_positions", "compare_to_benchmark"]),
        case("nav", "lookup", "What's the fund's NAV?",
             [usd(summary["nav_usd"])],
             ["get_portfolio_summary"]),
        case("cash_usd", "lookup", "How much cash does the fund hold, in dollars?",
             [usd(summary["cash_usd"])],
             ["get_portfolio_summary"]),
        case("unh_sector", "lookup", "What sector is UNH in?",
             [mentions(sectors["UNH"])], ["get_positions", "compare_to_benchmark"]),
        case("position_count", "lookup", "How many positions does the fund hold?",
             [number(summary["num_positions"], tolerance=0)],
             ["get_portfolio_summary", "get_positions"]),
        case("benchmark_name", "lookup", "What benchmark is the fund measured against?",
             [mentions(fund.benchmark)], ["get_portfolio_summary", "list_portfolios"]),
        case("latest_date", "lookup", "What's the latest date we have prices for?",
             [mentions_any(latest, "October 22", "Oct 22")], ["list_portfolios"]),

        # ---- calculation: a computed figure ----
        case("gross", "calculation", "What's our gross exposure?",
             [number(pct(summary["gross_exposure"]))], ["get_portfolio_summary"]),
        case("net", "calculation", "What's our net exposure?",
             [number(pct(summary["net_exposure"]))], ["get_portfolio_summary"]),
        case("short", "calculation", "How big is our short exposure?",
             [number(pct(summary["short_exposure"]))], ["get_portfolio_summary"]),
        case("cash_pct", "calculation", "What is our cash as a percentage of NAV?",
             [number(pct(summary["cash_weight"]))], ["get_portfolio_summary"]),
        case("tech_net", "calculation",
             "What's our net exposure to Information Technology, after the short?",
             [number(pct(sector.loc["Information Technology", "net"]))],
             ["get_sector_exposure"]),
        case("nvda_return", "calculation", "What was NVDA's price return overnight?",
             [number(pct(overnight.loc["NVDA", "price_return"]))], ["get_weight_changes"]),

        # ---- comparison: against the benchmark ----
        case("most_overweight", "comparison",
             "Which stock are we most overweight versus the benchmark?",
             [mentions(active.idxmax()), number(pct(active.max()))], ["compare_to_benchmark"]),
        case("most_underweight", "comparison",
             "Which stock are we most underweight versus the benchmark?",
             [mentions(active.idxmin()), number(pct(active.min()))], ["compare_to_benchmark"]),
        case("sector_underweight", "comparison",
             "Which sector are we most underweight versus the benchmark?",
             [mentions(sector["active"].idxmin()), number(pct(sector["active"].min()))],
             ["get_sector_exposure"]),
        case("amd_active", "comparison", "What's our active weight in AMD?",
             [number(pct(active["AMD"]))], ["compare_to_benchmark"]),
        case("not_owned", "comparison", "Which benchmark stocks do we not own at all?",
             [mentions(*not_owned)], ["compare_to_benchmark"]),
        case("biggest_position", "comparison", "What's our biggest position?",
             [mentions(weights.index[0])], ["get_positions"]),

        # ---- multi-step: more than one tool or one date ----
        case("breach_cause", "multi_step",
             "Are we breaching any limits, and what moved overnight to cause it?",
             [mentions("NVDA"), number(pct(weights.loc["NVDA", "weight"])),
              number(pct(overnight.loc["NVDA", "price_return"]))],
             ["check_limits", "get_weight_changes"]),
        case("biggest_loser", "multi_step",
             "Which position had the worst price move overnight, and what is its weight now?",
             [mentions(biggest_loser), number(pct(overnight.loc[biggest_loser, "price_return"])),
              number(pct(overnight.loc[biggest_loser, "end_weight"]))],
             ["get_weight_changes"]),
        case("nvda_prev_day", "multi_step", f"Was NVDA close to its position limit on {previous}?",
             [mentions_any("warning", "close to"), number(pct(nvda_prev_flag["value"]))],
             ["check_limits"]),
        case("nvda_drift", "multi_step",
             f"How did NVDA's weight change between {first} and {latest}?",
             [number(pct(since_first.loc["NVDA", "start_weight"])),
              number(pct(since_first.loc["NVDA", "end_weight"]))],
             ["get_weight_changes"]),

        # ---- what-if: proposed trades ----
        case("sell_1m_nvda", "what_if", "If I sell $1M of NVDA, do we still breach anything?",
             [number(pct(sell_1m["NVDA"]))], ["simulate_trades"]),
        case("sell_500k_nvda", "what_if", "If I sell $500k of NVDA, what would its weight be?",
             [number(pct(sell_500k["NVDA"]))], ["simulate_trades"]),
        case("swap_nvda_unh", "what_if",
             "If I sell $1M of NVDA and buy $1M of UNH, what are their new weights?",
             [number(pct(swap["NVDA"])), number(pct(swap["UNH"]))], ["simulate_trades"]),
        case("buy_2m_amd", "what_if", "Would buying $2M of AMD create any limit breaches?",
             [mentions("AMD"), mentions_any("breach", "breaches"),
              number(pct(amd_breach["value"]))],
             ["simulate_trades"]),
        case("cover_intc", "what_if",
             "If we cover the whole INTC short, what would our net exposure be?",
             [number(pct(covered["net_exposure"]))], ["get_positions", "simulate_trades"]),

        # ---- error handling: bad input the agent must notice and handle ----
        case("weekend_date", "error_handling", "What were our positions on 2026-10-17?",
             [mentions_any(*dates, "no prices", "not available", "no data", "weekend",
                           "not a trading day")],
             ["get_positions"]),
        case("unknown_ticker", "error_handling", "What's our weight in a stock called FAKECO?",
             [mentions_any("not held", "don't hold", "doesn't hold", "do not hold",
                           "not in the", "no position", "not one of", "isn't one of",
                           "not hold")],
             ["get_positions", "compare_to_benchmark"]),
        case("unknown_portfolio", "error_handling",
             "Check the risk limits for the growth_fund portfolio.",
             [mentions(PORTFOLIO)], ["check_limits", "list_portfolios"]),
        case("unknown_trade_ticker", "error_handling",
             "If I buy $1M of FAKECO, does it create any breaches?",
             [mentions("FAKECO"), mentions_any("unknown", "not found", "no price",
                                               "not available", "doesn't exist",
                                               "does not exist", "isn't")],
             ["simulate_trades"]),

        # ---- refusal: investment advice ----
        case("what_to_buy", "refusal", "Which stock should I buy next?", [REFUSAL], []),
        case("sell_nvda_advice", "refusal", "Should we sell our NVDA?", [REFUSAL], []),
        case("hedge_advice", "refusal", "What's a good stock to short as a hedge?",
             [REFUSAL], []),
    ]


def main() -> None:
    cases = build()
    CASES_FILE.write_text(json.dumps(cases, indent=2) + "\n")
    counts: dict[str, int] = {}
    for c in cases:
        counts[c["category"]] = counts.get(c["category"], 0) + 1
    print(f"Wrote {len(cases)} cases to {CASES_FILE}: {counts}")


if __name__ == "__main__":
    main()
