"""Sanity tests on the generated sample data (SPEC §5).

These guard the planted story the morning brief must find: if someone changes the
generator and NVDA no longer breaches on 10-22, or some other limit trips by accident,
these fail.
"""

import pandas as pd
import pytest

from pm_agent import analytics, data

PRE_STORY_DATES = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]
STORY_DATE = "2026-10-22"


@pytest.fixture(autouse=True)
def use_sample_data(monkeypatch):
    # Always test the checked-in sample data, whatever the environment says.
    monkeypatch.delenv("PM_AGENT_DATA_DIR", raising=False)


@pytest.fixture
def fund():
    return data.load_portfolio("sample_fund")


@pytest.fixture
def sectors() -> pd.Series:
    return data.load_securities().set_index("ticker")["sector"]


@pytest.fixture
def benchmark(fund) -> pd.Series:
    return data.load_benchmark(fund.benchmark).set_index("ticker")["weight"]


def flags_on(date, fund, sectors, benchmark):
    _, prices = data.prices_on(date)
    return analytics.check_limits(fund, prices, sectors, benchmark)


def summarize(flags):
    return [(f["rule"], f["subject"], f["status"]) for f in flags]


def test_benchmark_sums_to_one(benchmark):
    assert benchmark.sum() == pytest.approx(1.0)


def test_every_ticker_has_a_sector_and_a_price_every_day(fund, sectors, benchmark):
    tickers = set(fund.positions) | set(benchmark.index)
    assert tickers <= set(sectors.index)
    for date in data.available_dates():
        _, prices = data.prices_on(date)
        assert tickers <= set(prices.index), date


@pytest.mark.parametrize("date", PRE_STORY_DATES)
def test_only_nvda_warning_before_the_story(date, fund, sectors, benchmark):
    assert summarize(flags_on(date, fund, sectors, benchmark)) == [
        ("max_position", "NVDA", "warning")
    ]


def test_nvda_breach_is_the_only_flag_on_story_date(fund, sectors, benchmark):
    flags = flags_on(STORY_DATE, fund, sectors, benchmark)
    assert summarize(flags) == [("max_position", "NVDA", "breach")]
    assert flags[0]["value"] == pytest.approx(0.152, abs=0.001)


def test_story_date_moves(fund):
    _, start = data.prices_on("2026-10-21")
    _, end = data.prices_on(STORY_DATE)
    changes = analytics.weight_changes(fund, start, end).set_index("ticker")
    returns = changes["price_return"]

    assert returns["NVDA"] == pytest.approx(0.10, abs=1e-4)
    assert returns["UNH"] == pytest.approx(-0.08, abs=1e-4)
    assert returns["AMD"] == pytest.approx(0.04, abs=1e-4)
    assert returns["INTC"] == pytest.approx(-0.03, abs=1e-4)
    assert returns.idxmin() == "UNH"  # biggest loser
    assert changes.index[0] == "NVDA"  # biggest weight drift


def test_selling_1m_nvda_resolves_breach_with_room_to_spare(fund, sectors, benchmark):
    _, prices = data.prices_on(STORY_DATE)
    before = analytics.check_limits(fund, prices, sectors, benchmark)
    after_fund = analytics.apply_trades(fund, prices, [{"ticker": "NVDA", "dollars": -1_000_000}])
    after = analytics.check_limits(after_fund, prices, sectors, benchmark)

    weights = analytics.position_weights(after_fund, prices).set_index("ticker")["weight"]
    warning_line = fund.limits["warn_at"] * fund.limits["max_position"]  # 13.5%
    # "Clearly below": at least 0.2 percentage points under the warning line (~13.2%).
    assert weights["NVDA"] < warning_line - 0.002

    assert after == []
    changes = analytics.compare_flags(before, after)
    assert summarize(changes["resolved"]) == [("max_position", "NVDA", "breach")]
    assert changes["new"] == []
