"""Generate the synthetic sample data in data/sample/.

Prices are illustrative, not real quotes. Everything here is made up so the
morning brief has a known story to find (see docs/SPEC.md §5):

- Through 2026-10-21, NVDA is 14.0% of the fund: inside the warning band
  (>= 13.5%) of its 15% single-position limit.
- On 2026-10-22, NVDA jumps +10% overnight and breaches the limit (~15.2%).
  The same day UNH drops -8% (biggest loser), AMD +4%, INTC -3% (the short gains).
- Selling $1M of NVDA on 10-22 brings it to ~13.2%, clearly below the warning line.

It also writes planted-event scenario days for the morning brief eval (task 5.2), each a full
dataset in data/scenarios/<name>/ (see SCENARIOS below).

Deterministic: a fixed seed, so running it twice produces identical files.

    python scripts/make_sample_data.py
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "sample"
SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"
SEED = 42

DATES = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
ANCHOR_DATE = "2026-10-21"  # shares are sized so weights hit their targets on this date
STORY_DATE = "2026-10-22"  # the day the planted moves happen

FUND_NAME = "sample_fund"
FUND_DISPLAY_NAME = "Sample Large Cap Fund"
BENCHMARK_NAME = "sample_large_cap"
NAV = 50_000_000  # USD on the anchor date

# ticker: (name, sector, anchor price on 2026-10-21)
SECURITIES = {
    "NVDA": ("NVIDIA Corp", "Information Technology", 180.00),
    "AAPL": ("Apple Inc", "Information Technology", 250.00),
    "MSFT": ("Microsoft Corp", "Information Technology", 500.00),
    "AVGO": ("Broadcom Inc", "Information Technology", 340.00),
    "ORCL": ("Oracle Corp", "Information Technology", 280.00),
    "AMD": ("Advanced Micro Devices", "Information Technology", 230.00),
    "INTC": ("Intel Corp", "Information Technology", 37.00),
    "GOOGL": ("Alphabet Inc", "Communication Services", 250.00),
    "META": ("Meta Platforms", "Communication Services", 720.00),
    "AMZN": ("Amazon.com Inc", "Consumer Discretionary", 220.00),
    "TSLA": ("Tesla Inc", "Consumer Discretionary", 440.00),
    "LLY": ("Eli Lilly and Co", "Health Care", 820.00),
    "UNH": ("UnitedHealth Group", "Health Care", 360.00),
    "JNJ": ("Johnson & Johnson", "Health Care", 190.00),
    "ABBV": ("AbbVie Inc", "Health Care", 230.00),
    "JPM": ("JPMorgan Chase", "Financials", 300.00),
    "V": ("Visa Inc", "Financials", 345.00),
    "MA": ("Mastercard Inc", "Financials", 570.00),
    "PG": ("Procter & Gamble", "Consumer Staples", 155.00),
    "COST": ("Costco Wholesale", "Consumer Staples", 920.00),
    "KO": ("Coca-Cola Co", "Consumer Staples", 68.00),
    "CAT": ("Caterpillar Inc", "Industrials", 520.00),
    "GE": ("GE Aerospace", "Industrials", 300.00),
    "XOM": ("Exxon Mobil", "Energy", 115.00),
    "CVX": ("Chevron Corp", "Energy", 155.00),
}

# Fund target weights on the anchor date (decimals of NAV). Longs sum to 0.98,
# the INTC short is -0.02, so cash is the remaining 0.04.
FUND_TARGET_WEIGHTS = {
    "NVDA": 0.140, "AAPL": 0.085, "MSFT": 0.100, "AVGO": 0.040, "AMD": 0.025, "INTC": -0.020,
    "GOOGL": 0.075, "META": 0.065,
    "AMZN": 0.075, "TSLA": 0.030,
    "LLY": 0.035, "UNH": 0.035, "JNJ": 0.025,
    "JPM": 0.050, "V": 0.040, "MA": 0.025,
    "PG": 0.025, "COST": 0.025,
    "CAT": 0.025, "GE": 0.020,
    "XOM": 0.030, "CVX": 0.010,
}

# Benchmark weights (static, sum to 1). Excludes AMD and INTC on purpose; ORCL, ABBV
# and KO are in the benchmark but not held. Chosen so that only NVDA's position limit
# flags on the anchor date: every stock's active weight stays under 4.5% and every
# sector's under 7.2% (the warning lines for the 5% and 8% active limits).
BENCHMARK_WEIGHTS = {
    "NVDA": 0.12, "AAPL": 0.11, "MSFT": 0.11, "AVGO": 0.04, "ORCL": 0.02,
    "GOOGL": 0.08, "META": 0.06,
    "AMZN": 0.08, "TSLA": 0.04,
    "LLY": 0.03, "UNH": 0.03, "JNJ": 0.02, "ABBV": 0.02,
    "JPM": 0.05, "V": 0.04, "MA": 0.02,
    "PG": 0.02, "COST": 0.02, "KO": 0.01,
    "CAT": 0.02, "GE": 0.02,
    "XOM": 0.03, "CVX": 0.01,
}

# The planted overnight moves on STORY_DATE (price return vs ANCHOR_DATE).
STORY_MOVES = {"NVDA": 0.10, "UNH": -0.08, "AMD": 0.04, "INTC": -0.03}

LIMITS = {
    "max_position": 0.15,
    "max_stock_active": 0.05,
    "max_sector_active": 0.08,
    "max_gross": 1.15,
    "min_net": 0.90,
    "max_net": 1.02,
    "warn_at": 0.9,
    "net_warn_buffer": 0.02,
}

@dataclass
class Scenario:
    """A planted-event day for the brief eval: the sample fund with some weights moved and
    its own overnight moves on STORY_DATE. Weight changes must keep the totals the same."""

    name: str
    description: str
    weight_changes: dict[str, float] = field(default_factory=dict)
    moves: dict[str, float] = field(default_factory=dict)


# NVDA and MSFT trade weight so the right stock is (or isn't) near its 15% limit. Every
# scenario keeps the other limits clear; tests/test_scenarios.py checks the exact events.
SCENARIOS = [
    Scenario("quiet", "Nothing notable: no stock near a limit, only small price moves.",
             {"NVDA": 0.12, "MSFT": 0.12}),
    Scenario("big_move", "META falls 9% overnight; no limit is close.",
             {"NVDA": 0.12, "MSFT": 0.12}, {"META": -0.09}),
    Scenario("single_breach", "MSFT rises 10% and breaches its 15% position limit.",
             {"NVDA": 0.10, "MSFT": 0.14}, {"MSFT": 0.10}),
    Scenario("two_events", "MSFT breaches its position limit and TSLA falls 10%, same night.",
             {"NVDA": 0.10, "MSFT": 0.14}, {"MSFT": 0.10, "TSLA": -0.10}),
]

# Background noise for every other price move, small enough not to compete with the story.
DAILY_NOISE = 0.008  # standard deviation of daily returns before the anchor date
STORY_DAY_NOISE = 0.003  # other stocks on STORY_DATE stay within roughly ±1%


def make_prices(rng: np.random.Generator,
                story_moves: dict[str, float]) -> dict[str, dict[str, float]]:
    """Close prices per date per ticker, anchored to the anchor prices on ANCHOR_DATE.

    Days before the anchor are a small random walk backwards from the anchor price,
    so the anchor date stays exact. The story date applies story_moves (or tiny noise).
    """
    anchor_i = DATES.index(ANCHOR_DATE)
    prices: dict[str, dict[str, float]] = {d: {} for d in DATES}
    for ticker, (_, _, anchor) in SECURITIES.items():
        prices[ANCHOR_DATE][ticker] = anchor
        price = anchor
        for d in reversed(DATES[:anchor_i]):
            price = price / (1 + rng.normal(0, DAILY_NOISE))
            prices[d][ticker] = round(price, 2)
        noise = rng.normal(0, STORY_DAY_NOISE)  # always drawn, so the random sequence is stable
        move = story_moves.get(ticker, noise)
        prices[STORY_DATE][ticker] = round(anchor * (1 + move), 2)
    return prices


def make_portfolio(target_weights: dict[str, float]) -> dict:
    """Whole-share positions sized to the target weights at anchor prices.

    Cash is whatever makes NAV exactly NAV, so weights match targets to within
    share rounding (well under 0.01%).
    """
    positions = []
    invested = 0.0
    for ticker, weight in target_weights.items():
        price = SECURITIES[ticker][2]
        shares = round(weight * NAV / price)
        positions.append({"ticker": ticker, "shares": shares})
        invested += shares * price
    return {
        "display_name": FUND_DISPLAY_NAME,
        "benchmark": BENCHMARK_NAME,
        "cash": round(NAV - invested, 2),
        "limits": LIMITS,
        "positions": positions,
    }


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def write_dataset(out_dir: Path, target_weights: dict[str, float],
                  story_moves: dict[str, float]) -> None:
    """One complete data directory: securities, prices, benchmark, portfolio."""
    rng = np.random.default_rng(SEED)
    prices = make_prices(rng, story_moves)

    write_csv(
        out_dir / "securities.csv",
        ["ticker", "name", "sector"],
        [[t, name, sector] for t, (name, sector, _) in SECURITIES.items()],
    )
    write_csv(
        out_dir / "prices.csv",
        ["date", "ticker", "close"],
        [[d, t, f"{prices[d][t]:.2f}"] for d in DATES for t in SECURITIES],
    )
    write_csv(
        out_dir / "benchmarks" / f"{BENCHMARK_NAME}.csv",
        ["ticker", "weight"],
        [[t, w] for t, w in BENCHMARK_WEIGHTS.items()],
    )

    portfolio_path = out_dir / "portfolios" / f"{FUND_NAME}.json"
    portfolio_path.parent.mkdir(parents=True, exist_ok=True)
    portfolio_path.write_text(json.dumps(make_portfolio(target_weights), indent=2) + "\n")


def main() -> None:
    assert abs(sum(BENCHMARK_WEIGHTS.values()) - 1) < 1e-9, "benchmark weights must sum to 1"
    assert set(FUND_TARGET_WEIGHTS) <= set(SECURITIES)
    assert set(BENCHMARK_WEIGHTS) <= set(SECURITIES)

    write_dataset(OUT_DIR, FUND_TARGET_WEIGHTS, STORY_MOVES)
    print(f"Wrote sample data to {OUT_DIR}")

    for scenario in SCENARIOS:
        weights = {**FUND_TARGET_WEIGHTS, **scenario.weight_changes}
        assert abs(sum(weights.values()) - sum(FUND_TARGET_WEIGHTS.values())) < 1e-9, \
            f"{scenario.name}: weight changes must keep the fund's total weight"
        write_dataset(SCENARIOS_DIR / scenario.name, weights, scenario.moves)
        print(f"Wrote scenario '{scenario.name}': {scenario.description}")


if __name__ == "__main__":
    main()
