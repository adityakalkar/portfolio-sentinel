"""MCP server exposing the portfolio analytics as read-only tools.

A thin layer: load data (data.py) -> call analytics (analytics.py) -> convert decimal weights
to percent (*_pct fields) and round. No math of its own. ValueError from the layers below
becomes ToolError, because MCP hides any other exception's message from the client.

    python -m pm_agent.mcp_server      # serve over stdio
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, TypedDict

import pandas as pd
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from pm_agent import analytics, data

DEFAULT_PORTFOLIO = "sample_fund"

INSTRUCTIONS = """\
Tools for a portfolio manager's fund: holdings, benchmark comparison, sector exposure,
risk-limit checks, what-if trades and weight drift.

Units: fields ending _pct are percent of NAV (15.2 means 15.2%); differences between two
percentages (active weights, weight changes) are percentage points. Fields ending _usd are
US dollars. Negative shares, values or weights are short positions or underweights.

Report numbers exactly as the tools return them; do not compute new numbers yourself.
If a tool returns an error, read it: it lists what is available (portfolios, dates, tickers).
Dates are YYYY-MM-DD and default to the latest price date. The default portfolio is
"sample_fund".
"""

server = MCPServer("pm-agent", instructions=INSTRUCTIONS)


def tool(fn):
    """Register fn as a read-only tool. Its docstring, with the code indentation removed,
    is the description the model reads to choose a tool."""
    register = server.tool(
        description=inspect.cleandoc(fn.__doc__),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    return register(fn)


class Trade(TypedDict):
    """One what-if trade: positive dollars buy (or cover a short), negative dollars sell."""

    ticker: str
    dollars: float


# ---------- helpers: errors, loading, formatting ----------


@contextmanager
def readable_errors() -> Iterator[None]:
    """Turn ValueError into ToolError so the model sees the message and can fix its call."""
    try:
        yield
    except ValueError as e:
        raise ToolError(str(e)) from e


def pct(weight: float) -> float:
    """Decimal weight -> percent, rounded (0.152034 -> 15.2)."""
    return round(float(weight) * 100, 2)


def usd(amount: float) -> float:
    return round(float(amount), 2)


def top(rows: list[dict], top_n: int) -> list[dict]:
    """The first top_n rows (rows are already sorted biggest first); 0 means all."""
    if top_n < 0:
        raise ValueError(f"top_n must be 0 or more (0 = all), got {top_n}")
    return rows if top_n == 0 else rows[:top_n]


def load(portfolio: str, date: str | None) -> tuple[analytics.Portfolio, str, pd.Series]:
    """Portfolio, the price date actually used, and that date's prices."""
    port = data.load_portfolio(portfolio)
    used_date, prices = data.prices_on(date)
    return port, used_date, prices


def benchmark_for(port: analytics.Portfolio) -> pd.Series | None:
    return data.benchmark_weights(port.benchmark) if port.benchmark else None


def summary_fields(summary: dict) -> dict[str, Any]:
    return {
        "nav_usd": usd(summary["nav_usd"]),
        "cash_usd": usd(summary["cash_usd"]),
        "cash_pct": pct(summary["cash_weight"]),
        "long_pct": pct(summary["long_exposure"]),
        "short_pct": pct(summary["short_exposure"]),
        "gross_pct": pct(summary["gross_exposure"]),
        "net_pct": pct(summary["net_exposure"]),
        "num_positions": summary["num_positions"],
        "num_long": summary["num_long"],
        "num_short": summary["num_short"],
    }


def flag_fields(flag: dict) -> dict[str, Any]:
    return {
        "rule": flag["rule"],
        "subject": flag["subject"],
        "value_pct": pct(flag["value"]),
        "limit_pct": pct(flag["limit"]),
        "status": flag["status"],
    }


def limit_fields(limits: dict) -> dict[str, Any]:
    """Configured limits in percent. warn_at stays a fraction: it scales each limit."""
    fields = {}
    for key, value in limits.items():
        if key == "warn_at":
            fields["warn_at"] = value
        else:
            fields[f"{key}_pct"] = pct(value)
    return fields


# ---------- tools ----------


@tool
def list_portfolios() -> dict[str, Any]:
    """List the portfolios you can ask about and the dates that have prices.

    Use this first if you don't know the portfolio name or which dates are available.
    Example: "Which funds can I look at?" or "What's the latest date we have data for?"
    """
    with readable_errors():
        portfolios = []
        for name in data.list_portfolios():
            port = data.load_portfolio(name)
            portfolios.append(
                {"name": name, "display_name": port.display_name, "benchmark": port.benchmark}
            )
        dates = data.available_dates()
        return {"portfolios": portfolios, "price_dates": dates, "latest_date": dates[-1]}


@tool
def get_portfolio_summary(
    portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None
) -> dict[str, Any]:
    """Headline numbers for a fund on one date: NAV, cash, and long, short, gross and net
    exposure (percent of NAV), plus position counts and the benchmark name.

    Gross = long - short (total size of all bets); net = long + short (market direction).
    date is YYYY-MM-DD and defaults to the latest price date.
    Example: "What's the fund's NAV and net exposure?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        summary = analytics.exposure_summary(port, prices)
        return {
            "portfolio": port.name,
            "display_name": port.display_name,
            "date": used_date,
            "benchmark": port.benchmark,
            **summary_fields(summary),
        }


@tool
def get_positions(
    portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None, top_n: int = 10
) -> dict[str, Any]:
    """The fund's holdings, biggest first by absolute size (large shorts rank high too):
    ticker, name, sector, shares, price, market value and weight (percent of NAV).

    top_n limits how many are returned (default 10; 0 = all). total_positions says how many
    the fund holds in all. Shorts have negative shares, value and weight.
    Example: "What are our five biggest positions?" or "How much NVDA do we own?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        securities = data.load_securities().set_index("ticker")
        weights = analytics.position_weights(port, prices)
        rows = [
            {
                "ticker": r.ticker,
                "name": securities["name"].get(r.ticker),
                "sector": securities["sector"].get(r.ticker),
                "shares": round(float(r.shares), 2),
                "price_usd": usd(r.price),
                "market_value_usd": usd(r.market_value),
                "weight_pct": pct(r.weight),
            }
            for r in weights.itertuples()
        ]
        return {
            "portfolio": port.name,
            "date": used_date,
            "nav_usd": usd(analytics.exposure_summary(port, prices)["nav_usd"]),
            "total_positions": len(rows),
            "positions": top(rows, top_n),
        }


@tool
def compare_to_benchmark(
    portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None, top_n: int = 10
) -> dict[str, Any]:
    """Stock-by-stock comparison with the fund's benchmark: portfolio weight, benchmark
    weight and active weight (portfolio minus benchmark, in percentage points; positive =
    overweight). Biggest bets first, in either direction. Includes stocks the fund holds
    that are not in the benchmark, and benchmark stocks the fund does not hold.

    top_n limits how many are returned (default 10; 0 = all).
    Example: "Where are we most overweight vs the benchmark?" or "What are our biggest
    underweights?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        benchmark = benchmark_for(port)
        if benchmark is None:
            raise ValueError(f"Portfolio '{port.name}' has no benchmark to compare against")
        securities = data.load_securities().set_index("ticker")
        active = analytics.active_weights(port, prices, benchmark)
        rows = [
            {
                "ticker": r.ticker,
                "name": securities["name"].get(r.ticker),
                "sector": securities["sector"].get(r.ticker),
                "portfolio_pct": pct(r.portfolio_weight),
                "benchmark_pct": pct(r.benchmark_weight),
                "active_pct": pct(r.active_weight),
            }
            for r in active.itertuples()
        ]
        return {
            "portfolio": port.name,
            "benchmark": port.benchmark,
            "date": used_date,
            "total_stocks": len(rows),
            "stocks": top(rows, top_n),
        }


@tool
def get_sector_exposure(
    portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None
) -> dict[str, Any]:
    """Exposure by sector (percent of NAV): long, short, net and gross, plus the benchmark's
    sector weight and the active sector weight (net minus benchmark, percentage points).
    Largest sectors first.

    Example: "What are our sector bets?" or "How much tech do we own, net of the short?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        sectors = analytics.sector_exposure(port, prices, data.sector_map(), benchmark_for(port))
        rows = []
        for r in sectors.to_dict("records"):
            row = {"sector": r["sector"]}
            for column in ("long", "short", "net", "gross", "benchmark", "active"):
                if column in r:  # benchmark/active only when the fund has a benchmark
                    row[f"{column}_pct"] = pct(r[column])
            rows.append(row)
        return {
            "portfolio": port.name,
            "benchmark": port.benchmark,
            "date": used_date,
            "sectors": rows,
        }


@tool
def check_limits(portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None) -> dict[str, Any]:
    """Check the fund against its risk limits. Returns the configured limits, how many
    breaches and warnings there are, and each flag (breaches first).

    A breach means over the limit; a warning means close to it (at or above warn_at times
    the limit, or for net exposure within net_warn_buffer_pct of either end). Each flag has
    rule, subject (ticker, sector or "portfolio"), value_pct, limit_pct and status.
    Size limits count shorts and underweights by their size. No flags means all clear.
    Example: "Are we breaching any limits?" or "Is anything close to a limit?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        flags = analytics.check_limits(port, prices, data.sector_map(), benchmark_for(port))
        return {
            "portfolio": port.name,
            "date": used_date,
            "breach_count": sum(f["status"] == "breach" for f in flags),
            "warning_count": sum(f["status"] == "warning" for f in flags),
            "limits": limit_fields(port.limits),
            "flags": [flag_fields(f) for f in flags],
        }


@tool
def simulate_trades(
    trades: list[Trade], portfolio: str = DEFAULT_PORTFOLIO, date: str | None = None
) -> dict[str, Any]:
    """What-if: apply proposed trades at that date's closing prices and show the fund before
    and after (summary and limit flags), which flags the trades resolve, which new ones they
    create, and each traded stock's weight before and after. Nothing is actually traded.

    trades is a list of {"ticker": ..., "dollars": ...}: positive dollars buy (or cover a
    short), negative dollars sell (or go short). Cash moves the other way, so NAV stays the
    same. A breach that becomes a warning counts as resolved.
    Example: "If I sell $1M of NVDA, do we still breach anything?" or "What happens if I
    buy $500k of UNH?"
    """
    with readable_errors():
        port, used_date, prices = load(portfolio, date)
        sectors, benchmark = data.sector_map(), benchmark_for(port)
        after_port = analytics.apply_trades(port, prices, list(trades))

        flags_before = analytics.check_limits(port, prices, sectors, benchmark)
        flags_after = analytics.check_limits(after_port, prices, sectors, benchmark)
        changes = analytics.compare_flags(flags_before, flags_after)

        weights_before = analytics.position_weights(port, prices).set_index("ticker")
        weights_after = analytics.position_weights(after_port, prices).set_index("ticker")
        traded = []
        for ticker in dict.fromkeys(t["ticker"] for t in trades):  # unique, in order given
            traded.append({
                "ticker": ticker,
                "shares_before": round(float(port.positions.get(ticker, 0)), 2),
                "shares_after": round(float(after_port.positions.get(ticker, 0)), 2),
                "weight_before_pct": pct(weights_before["weight"].get(ticker, 0.0)),
                "weight_after_pct": pct(weights_after["weight"].get(ticker, 0.0)),
            })

        return {
            "portfolio": port.name,
            "date": used_date,
            "trades": [{"ticker": t["ticker"], "dollars_usd": usd(t["dollars"])} for t in trades],
            "before": {
                "summary": summary_fields(analytics.exposure_summary(port, prices)),
                "flags": [flag_fields(f) for f in flags_before],
            },
            "after": {
                "summary": summary_fields(analytics.exposure_summary(after_port, prices)),
                "flags": [flag_fields(f) for f in flags_after],
            },
            "resolved_flags": [flag_fields(f) for f in changes["resolved"]],
            "new_flags": [flag_fields(f) for f in changes["new"]],
            "traded_positions": traded,
        }


@tool
def get_weight_changes(
    portfolio: str = DEFAULT_PORTFOLIO,
    start_date: str | None = None,
    end_date: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """How each position's weight drifted between two dates because prices moved (no
    trading assumed): start and end weight, the change in percentage points, and the
    stock's price return in percent. Biggest weight changes first.

    end_date defaults to the latest price date; start_date defaults to the price date just
    before end_date, so with no dates this shows overnight moves. top_n limits how many are
    returned (default 10; 0 = all).
    Example: "What moved overnight?" or "Which positions drifted most since 2026-10-16?"
    """
    with readable_errors():
        port = data.load_portfolio(portfolio)
        end_date, end_prices = data.prices_on(end_date)
        start_date, start_prices = data.prices_on(start_date or data.previous_date(end_date))
        securities = data.load_securities().set_index("ticker")
        changes = analytics.weight_changes(port, start_prices, end_prices)
        rows = [
            {
                "ticker": r.ticker,
                "name": securities["name"].get(r.ticker),
                "start_weight_pct": pct(r.start_weight),
                "end_weight_pct": pct(r.end_weight),
                "weight_change_pct": pct(r.change),
                "price_return_pct": pct(r.price_return),
            }
            for r in changes.itertuples()
        ]
        return {
            "portfolio": port.name,
            "start_date": start_date,
            "end_date": end_date,
            "total_positions": len(rows),
            "positions": top(rows, top_n),
        }


if __name__ == "__main__":
    server.run()  # stdio by default
