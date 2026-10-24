"""All portfolio math lives here, as pure functions.

No file reads, network, or LLM calls: callers pass in the portfolio, prices, sectors and
benchmark weights. Weights are decimals (0.05 = 5%). Shorts have negative shares, market
value and weight. Every function has known-answer tests in tests/test_analytics.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

import pandas as pd


@dataclass(frozen=True)
class Portfolio:
    """A fund's holdings at a point in time.

    positions maps ticker -> shares (negative = short). limits holds decimals, keyed as in
    SPEC §6 (e.g. {"max_position": 0.15}).
    """

    name: str
    positions: dict[str, float]
    cash: float
    display_name: str = ""
    benchmark: str | None = None
    limits: dict[str, float] = field(default_factory=dict)


# ---------- weights and exposure ----------


def position_weights(portfolio: Portfolio, prices: pd.Series) -> pd.DataFrame:
    """One row per position: ticker, shares, price, market_value, weight.

    Sorted biggest first by absolute market value, so a large short ranks high too.
    Raises ValueError if any held ticker has no price.
    """
    missing = sorted(t for t in portfolio.positions if t not in prices.index)
    if missing:
        raise ValueError(f"No price for {', '.join(missing)} on this date")

    rows = pd.DataFrame(
        {"ticker": list(portfolio.positions), "shares": list(portfolio.positions.values())},
        columns=["ticker", "shares"],
    )
    rows["price"] = rows["ticker"].map(prices).astype(float)
    rows["market_value"] = rows["shares"] * rows["price"]
    nav = rows["market_value"].sum() + portfolio.cash
    rows["weight"] = rows["market_value"] / nav

    order = rows["market_value"].abs().sort_values(ascending=False).index
    return rows.loc[order].reset_index(drop=True)


def exposure_summary(portfolio: Portfolio, prices: pd.Series) -> dict:
    """NAV, cash, long/short/gross/net exposure (decimals) and position counts.

    Gross = long - short, net = long + short (short exposure is negative).
    """
    w = position_weights(portfolio, prices)
    nav = w["market_value"].sum() + portfolio.cash
    long = w.loc[w["weight"] > 0, "weight"].sum()
    short = w.loc[w["weight"] < 0, "weight"].sum()
    return {
        "nav_usd": float(nav),
        "cash_usd": float(portfolio.cash),
        "cash_weight": float(portfolio.cash / nav),
        "long_exposure": float(long),
        "short_exposure": float(short),
        "gross_exposure": float(long - short),
        "net_exposure": float(long + short),
        "num_positions": len(w),
        "num_long": int((w["shares"] > 0).sum()),
        "num_short": int((w["shares"] < 0).sum()),
    }


# ---------- benchmark comparison ----------


def active_weights(
    portfolio: Portfolio, prices: pd.Series, benchmark: pd.Series
) -> pd.DataFrame:
    """Portfolio vs benchmark weight for every stock in either one.

    benchmark maps ticker -> weight. Columns: ticker, portfolio_weight, benchmark_weight,
    active_weight (positive = overweight). A stock held but not in the benchmark has
    benchmark_weight 0; a benchmark stock not held has portfolio_weight 0.
    Sorted biggest bets first (absolute active weight).
    """
    held = position_weights(portfolio, prices).set_index("ticker")["weight"]
    table = pd.DataFrame({"portfolio_weight": held, "benchmark_weight": benchmark})
    table = table.fillna(0.0)
    table["active_weight"] = table["portfolio_weight"] - table["benchmark_weight"]

    order = table["active_weight"].abs().sort_values(ascending=False, kind="stable").index
    return table.loc[order].rename_axis("ticker").reset_index()


def sector_exposure(
    portfolio: Portfolio,
    prices: pd.Series,
    sectors: pd.Series,
    benchmark: pd.Series | None = None,
) -> pd.DataFrame:
    """Long, short, net and gross weight per sector, largest gross first.

    sectors maps ticker -> sector name. If a benchmark (ticker -> weight) is given, adds
    the benchmark's sector weight and active = net - benchmark. Sectors that only the
    benchmark holds appear with zero fund exposure.
    """
    w = position_weights(portfolio, prices)
    needed = set(w["ticker"]) | (set(benchmark.index) if benchmark is not None else set())
    missing = sorted(needed - set(sectors.index))
    if missing:
        raise ValueError(f"No sector for {', '.join(missing)}")

    w["sector"] = w["ticker"].map(sectors)
    w["long"] = w["weight"].clip(lower=0)
    w["short"] = w["weight"].clip(upper=0)
    table = w.groupby("sector")[["long", "short"]].sum()
    table["net"] = table["long"] + table["short"]
    table["gross"] = table["long"] - table["short"]

    if benchmark is not None:
        bench_by_sector = benchmark.groupby(benchmark.index.map(sectors)).sum()
        # Add benchmark-only sectors as zero rows, then line the benchmark up by sector.
        table = table.reindex(table.index.union(bench_by_sector.index), fill_value=0.0)
        table["benchmark"] = bench_by_sector.reindex(table.index, fill_value=0.0)
        table["active"] = table["net"] - table["benchmark"]

    table = table.sort_values("gross", ascending=False, kind="stable")
    return table.rename_axis("sector").reset_index()


# ---------- limits ----------

# Rules that produce flags, and the settings that tune their warning bands (SPEC §6).
LIMIT_RULES = ["max_position", "max_stock_active", "max_sector_active", "max_gross",
               "min_net", "max_net"]
LIMIT_SETTINGS = ["warn_at", "net_warn_buffer"]


def _max_rule_status(value: float, limit: float, warn_at: float) -> str | None:
    """For max_* rules: breach above the limit, warning at >= warn_at * limit (by size)."""
    size = abs(value)
    if size > limit:
        return "breach"
    if size >= warn_at * limit:
        return "warning"
    return None


def _flag(rule: str, subject: str, value: float, limit: float, status: str) -> dict:
    return {"rule": rule, "subject": subject, "value": float(value), "limit": limit,
            "status": status}


def check_limits(
    portfolio: Portfolio,
    prices: pd.Series,
    sectors: pd.Series | None = None,
    benchmark: pd.Series | None = None,
) -> list[dict]:
    """Check the portfolio against its own limits. Returns flags, breaches first.

    Each flag: rule, subject (ticker, sector, or "portfolio"), value, limit, status
    ("breach" or "warning"). value is signed (a -14% short or a -5% underweight), and
    max_* rules compare its absolute size. Net rules warn within net_warn_buffer of either
    end instead of using warn_at. Only configured rules are checked; an empty list means
    all clear. Unknown limit keys raise ValueError.
    """
    limits = portfolio.limits
    unknown = sorted(set(limits) - set(LIMIT_RULES) - set(LIMIT_SETTINGS))
    if unknown:
        raise ValueError(
            f"Unknown limit key(s): {', '.join(unknown)}. "
            f"Known keys: {', '.join(LIMIT_RULES + LIMIT_SETTINGS)}"
        )
    for rule in ("max_stock_active", "max_sector_active"):
        if rule in limits and benchmark is None:
            raise ValueError(f"{rule} needs a benchmark, but none was given")
    if "max_sector_active" in limits and sectors is None:
        raise ValueError("max_sector_active needs sectors, but none were given")

    # Missing settings mean "no warning band": warn only at exactly the limit.
    warn_at = limits.get("warn_at", 1.0)
    net_buffer = limits.get("net_warn_buffer", 0.0)

    # Collect (rule, subject, value) for every max_* check, then rate each one.
    checks = []
    if "max_position" in limits:
        for row in position_weights(portfolio, prices).itertuples():
            checks.append(("max_position", row.ticker, row.weight))
    if "max_stock_active" in limits:
        for row in active_weights(portfolio, prices, benchmark).itertuples():
            checks.append(("max_stock_active", row.ticker, row.active_weight))
    if "max_sector_active" in limits:
        for row in sector_exposure(portfolio, prices, sectors, benchmark).itertuples():
            checks.append(("max_sector_active", row.sector, row.active))

    summary = exposure_summary(portfolio, prices)
    if "max_gross" in limits:
        checks.append(("max_gross", "portfolio", summary["gross_exposure"]))

    flags = []
    for rule, subject, value in checks:
        status = _max_rule_status(value, limits[rule], warn_at)
        if status:
            flags.append(_flag(rule, subject, value, limits[rule], status))

    net = summary["net_exposure"]
    if "min_net" in limits:
        low = limits["min_net"]
        status = "breach" if net < low else "warning" if net <= low + net_buffer else None
        if status:
            flags.append(_flag("min_net", "portfolio", net, low, status))
    if "max_net" in limits:
        high = limits["max_net"]
        status = "breach" if net > high else "warning" if net >= high - net_buffer else None
        if status:
            flags.append(_flag("max_net", "portfolio", net, high, status))

    # sorted() is stable, so within breaches and within warnings the check order is kept.
    return sorted(flags, key=lambda f: f["status"] != "breach")



def compare_flags(before: list[dict], after: list[dict]) -> dict[str, list[dict]]:
    """What a change (e.g. a trade) did to the flags, using flag identity (rule, subject).

    resolved: flags from before that are gone, or breaches that became warnings.
    new: flags in after with no flag before, or warnings that became breaches.
    A flag that keeps its status is in neither list.
    """
    before_by_id = {(f["rule"], f["subject"]): f for f in before}
    after_by_id = {(f["rule"], f["subject"]): f for f in after}

    resolved = []
    for key, f in before_by_id.items():
        now = after_by_id.get(key)
        if now is None or (f["status"] == "breach" and now["status"] == "warning"):
            resolved.append(f)

    new = []
    for key, f in after_by_id.items():
        was = before_by_id.get(key)
        if was is None or (was["status"] == "warning" and f["status"] == "breach"):
            new.append(f)

    return {"resolved": resolved, "new": new}


# ---------- what-if trades ----------


def apply_trades(portfolio: Portfolio, prices: pd.Series, trades: list[dict]) -> Portfolio:
    """A new Portfolio after trading at these prices; the original is unchanged.

    trades: [{"ticker": ..., "dollars": ...}]. Positive dollars buy (or cover a short),
    negative sell (or short). Cash moves the opposite way, so NAV is unchanged.
    No costs, taxes or settlement. Positions that reach zero shares are dropped.
    """
    positions = dict(portfolio.positions)  # a copy, so the original is never modified
    cash = portfolio.cash
    for trade in trades:
        ticker, dollars = trade["ticker"], trade["dollars"]
        if ticker not in prices.index:
            raise ValueError(
                f"Unknown ticker '{ticker}'. Tickers with prices: {', '.join(sorted(prices.index))}"
            )
        shares = float(positions.get(ticker, 0) + dollars / prices[ticker])
        # Selling a whole position can leave a float crumb like 1e-13 shares; treat it as 0.
        if math.isclose(shares, 0, abs_tol=1e-9):
            positions.pop(ticker, None)
        else:
            positions[ticker] = shares
        cash -= dollars
    return replace(portfolio, positions=positions, cash=cash)


# ---------- weight changes ----------


def weight_changes(
    portfolio: Portfolio, prices_start: pd.Series, prices_end: pd.Series
) -> pd.DataFrame:
    """How each position's weight drifted between two dates with no trading.

    Columns: ticker, start_weight, end_weight, change (end - start), price_return
    (end / start - 1). Sorted by the absolute weight change, biggest first.
    """
    start = position_weights(portfolio, prices_start).set_index("ticker")
    end = position_weights(portfolio, prices_end).set_index("ticker")
    table = pd.DataFrame({"start_weight": start["weight"], "end_weight": end["weight"]})
    table["change"] = table["end_weight"] - table["start_weight"]
    table["price_return"] = end["price"] / start["price"] - 1

    order = table["change"].abs().sort_values(ascending=False, kind="stable").index
    return table.loc[order].rename_axis("ticker").reset_index()
