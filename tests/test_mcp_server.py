"""Tests for the MCP server, through a real MCP client.

Most tests connect in-process (fast). One launches the server as a subprocess over stdio,
the way Claude Code and our agent will run it. All numbers here are percent (15.2 = 15.2%).
"""

import asyncio
import sys

import pytest
from mcp import Client, StdioServerParameters

from pm_agent.mcp_server import server

TOOL_NAMES = {
    "list_portfolios", "get_portfolio_summary", "get_positions", "compare_to_benchmark",
    "get_sector_exposure", "check_limits", "simulate_trades", "get_weight_changes",
}


@pytest.fixture(autouse=True)
def use_sample_data(monkeypatch):
    monkeypatch.delenv("PM_AGENT_DATA_DIR", raising=False)


async def _list_tools():
    async with Client(server) as client:
        return (await client.list_tools()).tools


async def _call(target, name, args):
    async with Client(target) as client:
        return await client.call_tool(name, args)


def call(name, target=server, **args) -> dict:
    """Call a tool and return its structured output; fail the test if the tool errored."""
    result = asyncio.run(_call(target, name, args))
    assert not result.is_error, result.content[0].text
    return result.structured_content


def call_error(name, **args) -> str:
    """Call a tool that should fail and return the error text the model would see."""
    result = asyncio.run(_call(server, name, args))
    assert result.is_error
    return result.content[0].text


def by_key(rows, key):
    return {row[key]: row for row in rows}


# ---------- tool listing ----------


def test_all_tools_listed_with_descriptions():
    tools = asyncio.run(_list_tools())
    assert {t.name for t in tools} == TOOL_NAMES
    for tool in tools:
        assert "Example" in tool.description, f"{tool.name} has no example question"
        assert tool.annotations.read_only_hint is True, f"{tool.name} is not read-only"


def test_trades_input_schema_names_its_fields():
    tools = by_key([t.model_dump() for t in asyncio.run(_list_tools())], "name")
    schema = tools["simulate_trades"]["input_schema"]
    trade = schema["$defs"]["Trade"]
    assert set(trade["required"]) == {"ticker", "dollars"}


# ---------- each tool ----------


def test_list_portfolios():
    out = call("list_portfolios")
    assert out["portfolios"] == [
        {"name": "sample_fund", "display_name": "Sample Large Cap Fund",
         "benchmark": "sample_large_cap"}
    ]
    assert out["price_dates"][0] == "2026-10-16"
    assert out["latest_date"] == "2026-10-22"


def test_portfolio_summary_in_percent_and_defaults_to_latest_date():
    out = call("get_portfolio_summary")
    assert out["date"] == "2026-10-22"
    assert out["nav_usd"] == pytest.approx(50_646_833.42)
    assert out["gross_pct"] == 99.88
    assert out["net_pct"] == 96.05
    assert out["short_pct"] == -1.92
    assert (out["num_positions"], out["num_long"], out["num_short"]) == (22, 21, 1)


def test_positions_top_n_and_all():
    out = call("get_positions", top_n=3)
    assert out["total_positions"] == 22
    assert len(out["positions"]) == 3
    top = out["positions"][0]
    assert (top["ticker"], top["sector"], top["weight_pct"]) == (
        "NVDA", "Information Technology", 15.2
    )
    assert top["price_usd"] == 198.0

    assert len(call("get_positions", top_n=0)["positions"]) == 22


def test_negative_top_n_is_a_readable_error():
    assert "top_n must be 0 or more" in call_error("get_positions", top_n=-1)


def test_compare_to_benchmark():
    out = call("compare_to_benchmark", date="2026-10-21", top_n=0)
    stocks = by_key(out["stocks"], "ticker")
    assert out["benchmark"] == "sample_large_cap"
    assert stocks["NVDA"]["portfolio_pct"] == 14.0
    assert stocks["NVDA"]["benchmark_pct"] == 12.0
    assert stocks["NVDA"]["active_pct"] == 2.0
    # In the benchmark but not held, and held but not in the benchmark.
    assert stocks["ORCL"]["active_pct"] == -2.0
    assert stocks["AMD"]["benchmark_pct"] == 0.0


def test_sector_exposure():
    out = call("get_sector_exposure", date="2026-10-21")
    tech = by_key(out["sectors"], "sector")["Information Technology"]
    assert tech == {
        "sector": "Information Technology", "long_pct": 39.0, "short_pct": -2.0,
        "net_pct": 37.0, "gross_pct": 41.0, "benchmark_pct": 40.0, "active_pct": -3.0,
    }


def test_check_limits_returns_nvda_breach_in_percent():
    out = call("check_limits")
    assert out["date"] == "2026-10-22"
    assert (out["breach_count"], out["warning_count"]) == (1, 0)
    assert out["flags"] == [
        {"rule": "max_position", "subject": "NVDA", "value_pct": 15.2, "limit_pct": 15.0,
         "status": "breach"}
    ]
    assert out["limits"]["max_position_pct"] == 15.0
    assert out["limits"]["net_warn_buffer_pct"] == 2.0
    assert out["limits"]["warn_at"] == 0.9  # a fraction of each limit, not a weight


def test_check_limits_warning_day():
    out = call("check_limits", date="2026-10-21")
    assert [(f["subject"], f["status"], f["value_pct"]) for f in out["flags"]] == [
        ("NVDA", "warning", 14.0)
    ]


def test_simulated_1m_nvda_sale_resolves_the_breach():
    out = call("simulate_trades", trades=[{"ticker": "NVDA", "dollars": -1_000_000}])

    assert [f["status"] for f in out["before"]["flags"]] == ["breach"]
    assert out["after"]["flags"] == []
    assert [(f["subject"], f["status"]) for f in out["resolved_flags"]] == [("NVDA", "breach")]
    assert out["new_flags"] == []

    nvda = by_key(out["traded_positions"], "ticker")["NVDA"]
    assert (nvda["weight_before_pct"], nvda["weight_after_pct"]) == (15.2, 13.23)
    # A sale turns stock into cash, so NAV is unchanged.
    assert out["after"]["summary"]["nav_usd"] == out["before"]["summary"]["nav_usd"]


def test_weight_changes_default_to_overnight():
    out = call("get_weight_changes", top_n=2)
    assert (out["start_date"], out["end_date"]) == ("2026-10-21", "2026-10-22")
    first = out["positions"][0]
    assert (first["ticker"], first["weight_change_pct"], first["price_return_pct"]) == (
        "NVDA", 1.2, 10.0
    )
    unh = out["positions"][1]
    assert (unh["ticker"], unh["price_return_pct"]) == ("UNH", -8.0)


# ---------- errors the model can read and act on ----------


def test_bad_portfolio_returns_readable_error():
    message = call_error("check_limits", portfolio="nope")
    assert "Unknown portfolio 'nope'" in message
    assert "sample_fund" in message  # tells the model what IS available


def test_bad_date_returns_readable_error():
    message = call_error("get_portfolio_summary", date="2026-10-17")
    assert "No prices for 2026-10-17" in message
    assert "2026-10-22" in message


def test_unknown_trade_ticker_returns_readable_error():
    message = call_error("simulate_trades", trades=[{"ticker": "FAKECO", "dollars": 1000}])
    assert "Unknown ticker 'FAKECO'" in message


# ---------- over stdio, as a subprocess ----------


def test_server_runs_over_stdio():
    params = StdioServerParameters(command=sys.executable, args=["-m", "pm_agent.mcp_server"])
    out = call("check_limits", target=params)
    assert [(f["subject"], f["status"]) for f in out["flags"]] == [("NVDA", "breach")]
