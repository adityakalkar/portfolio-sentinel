"""Loaders for portfolios, benchmarks, securities and prices.

This is the only module that reads data files. By default it reads data/sample/;
set PM_AGENT_DATA_DIR to point it somewhere else (tests and evals do this).
Bad input raises ValueError with a message that says what IS available, so the
agent can read it and correct itself.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from pm_agent.analytics import Portfolio

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "sample"


def data_dir() -> Path:
    # Read the env var on every call (not once at import) so tests can change it.
    return Path(os.environ.get("PM_AGENT_DATA_DIR", DEFAULT_DATA_DIR))


def list_portfolios() -> list[str]:
    return sorted(p.stem for p in (data_dir() / "portfolios").glob("*.json"))


def load_portfolio(name: str) -> Portfolio:
    """The named portfolio, with positions as {ticker: shares} (negative shares = short)."""
    path = data_dir() / "portfolios" / f"{name}.json"
    if not path.exists():
        raise ValueError(
            f"Unknown portfolio '{name}'. Available: {', '.join(list_portfolios())}"
        )
    raw = json.loads(path.read_text())
    return Portfolio(
        name=name,
        display_name=raw["display_name"],
        benchmark=raw["benchmark"],
        cash=float(raw["cash"]),
        limits=raw["limits"],
        positions={p["ticker"]: p["shares"] for p in raw["positions"]},
    )


def list_benchmarks() -> list[str]:
    return sorted(p.stem for p in (data_dir() / "benchmarks").glob("*.csv"))


def load_benchmark(name: str) -> pd.DataFrame:
    """Benchmark constituents: columns ticker, weight (decimals summing to ~1)."""
    path = data_dir() / "benchmarks" / f"{name}.csv"
    if not path.exists():
        raise ValueError(
            f"Unknown benchmark '{name}'. Available: {', '.join(list_benchmarks())}"
        )
    return pd.read_csv(path)


def benchmark_weights(name: str) -> pd.Series:
    """Benchmark weights as a Series indexed by ticker (the shape analytics expects)."""
    return load_benchmark(name).set_index("ticker")["weight"]


def load_securities() -> pd.DataFrame:
    """Security master: columns ticker, name, sector."""
    return pd.read_csv(data_dir() / "securities.csv")


def sector_map() -> pd.Series:
    """Sector for every known ticker, as a Series indexed by ticker."""
    return load_securities().set_index("ticker")["sector"]


def load_prices() -> pd.DataFrame:
    """All close prices: columns date, ticker, close. Dates stay YYYY-MM-DD strings."""
    return pd.read_csv(data_dir() / "prices.csv", dtype={"date": str})


def available_dates() -> list[str]:
    return sorted(load_prices()["date"].unique())


def prices_on(date: str | None = None) -> tuple[str, pd.Series]:
    """Close prices on one date as a Series indexed by ticker.

    date defaults to the latest available date. Returns (date used, prices).
    """
    prices = load_prices()
    dates = sorted(prices["date"].unique())
    if date is None:
        date = dates[-1]
    if date not in dates:
        raise ValueError(f"No prices for {date}. Available dates: {', '.join(dates)}")
    day = prices[prices["date"] == date]
    return date, day.set_index("ticker")["close"].astype(float)


def previous_date(date: str) -> str:
    """The price date just before date (skips weekends and holidays naturally)."""
    dates = available_dates()
    if date not in dates:
        raise ValueError(f"No prices for {date}. Available dates: {', '.join(dates)}")
    i = dates.index(date)
    if i == 0:
        raise ValueError(f"{date} is the first price date; there is no earlier price date")
    return dates[i - 1]
