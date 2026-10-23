import json

import pandas as pd
import pytest

from pm_agent import data

DATES = ["2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]


def test_list_portfolios():
    assert data.list_portfolios() == ["sample_fund"]


def test_load_portfolio():
    port = data.load_portfolio("sample_fund")
    assert port["name"] == "sample_fund"
    assert port["benchmark"] == "sample_large_cap"
    assert port["cash"] > 0
    assert port["limits"]["max_position"] == 0.15
    assert list(port["positions"].columns) == ["ticker", "shares"]
    assert len(port["positions"]) == 22


def test_unknown_portfolio_lists_available():
    with pytest.raises(ValueError, match="Unknown portfolio 'nope'.*sample_fund"):
        data.load_portfolio("nope")


def test_load_benchmark():
    bench = data.load_benchmark("sample_large_cap")
    assert list(bench.columns) == ["ticker", "weight"]
    assert bench["weight"].sum() == pytest.approx(1.0)


def test_unknown_benchmark_lists_available():
    with pytest.raises(ValueError, match="Unknown benchmark 'nope'.*sample_large_cap"):
        data.load_benchmark("nope")


def test_load_securities():
    sec = data.load_securities()
    assert list(sec.columns) == ["ticker", "name", "sector"]
    assert sec.loc[sec["ticker"] == "NVDA", "sector"].item() == "Information Technology"


def test_available_dates_sorted():
    assert data.available_dates() == DATES


def test_prices_on_defaults_to_latest():
    as_of, prices = data.prices_on()
    assert as_of == "2026-10-22"
    assert isinstance(prices, pd.Series)
    assert prices["NVDA"] == pytest.approx(198.00)  # 180 anchor * 1.10


def test_prices_on_specific_date():
    as_of, prices = data.prices_on("2026-10-21")
    assert as_of == "2026-10-21"
    assert prices["NVDA"] == pytest.approx(180.00)


def test_unknown_date_lists_available():
    with pytest.raises(ValueError, match="No prices for 2026-10-17.*2026-10-16.*2026-10-22"):
        data.prices_on("2026-10-17")


def test_previous_date():
    assert data.previous_date("2026-10-22") == "2026-10-21"
    assert data.previous_date("2026-10-19") == "2026-10-16"  # skips the weekend


def test_previous_date_of_first_date_errors():
    with pytest.raises(ValueError, match="no earlier price date"):
        data.previous_date("2026-10-16")


def test_data_dir_override(tmp_path, monkeypatch):
    # PM_AGENT_DATA_DIR points every loader at a different folder (used by tests and evals).
    (tmp_path / "portfolios").mkdir()
    (tmp_path / "portfolios" / "tiny.json").write_text(
        json.dumps({"display_name": "Tiny", "benchmark": None, "cash": 10, "limits": {},
                    "positions": [{"ticker": "AAA", "shares": 1}]})
    )
    (tmp_path / "prices.csv").write_text("date,ticker,close\n2026-01-02,AAA,5.0\n")
    monkeypatch.setenv("PM_AGENT_DATA_DIR", str(tmp_path))

    assert data.list_portfolios() == ["tiny"]
    assert data.load_portfolio("tiny")["benchmark"] is None
    assert data.available_dates() == ["2026-01-02"]
