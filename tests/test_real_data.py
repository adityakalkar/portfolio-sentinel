"""Sanity tests for the committed real dataset in data/real/ (M6).

Real prices change every day, so these check the data's shape and consistency, not exact
numbers. Rebuild with: python scripts/make_real_data.py
"""

from pathlib import Path

import pandas as pd
import pytest

from pm_agent import analytics, data

REAL_DIR = Path(__file__).resolve().parents[1] / "data" / "real"


@pytest.fixture(autouse=True)
def use_real_data(monkeypatch):
    monkeypatch.setenv("PM_AGENT_DATA_DIR", str(REAL_DIR))


@pytest.fixture
def fund():
    return data.load_portfolio("berkshire_13f")


def test_portfolio_loads_with_limits_and_benchmark(fund):
    assert data.list_portfolios() == ["berkshire_13f"]
    assert fund.benchmark == "sp500_ivv"
    assert fund.limits["max_position"] == 0.25
    assert len(fund.positions) >= 20
    assert all(shares > 0 for shares in fund.positions.values())  # a 13F lists longs only


def test_benchmark_is_the_sp500(fund):
    weights = data.benchmark_weights(fund.benchmark)
    assert 495 <= len(weights) <= 510
    assert weights.sum() == pytest.approx(1.0)


def test_every_ticker_has_a_sector_and_every_holding_a_price_each_day(fund):
    sectors = data.sector_map()
    tickers = set(fund.positions) | set(data.benchmark_weights(fund.benchmark).index)
    assert tickers <= set(sectors.index)
    assert set(sectors) == {
        "Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy",
        "Financials", "Health Care", "Industrials", "Information Technology", "Materials",
        "Real Estate", "Utilities",
    }
    prices = data.load_prices()
    days = prices.groupby("date")["ticker"].apply(set)
    assert all(set(fund.positions) <= held for held in days)
    assert len(days) >= 20


def test_cusip_mapping_covers_every_holding(fund):
    mapping = pd.read_csv(REAL_DIR / "cusip_tickers.csv")
    assert set(fund.positions) <= set(mapping["ticker"])
    assert mapping["cusip"].is_unique


def test_cash_is_two_percent_and_the_tools_run(fund):
    latest, prices = data.prices_on()
    summary = analytics.exposure_summary(fund, prices)
    # Cash was set to 2% of NAV at the build date's prices; prices move it slightly after.
    assert summary["cash_weight"] == pytest.approx(0.02, abs=0.005)
    flags = analytics.check_limits(fund, prices, data.sector_map(),
                                   data.benchmark_weights(fund.benchmark))
    assert all(f["status"] in {"breach", "warning"} for f in flags)
