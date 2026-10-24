"""Known-answer tests for analytics.py.

Every portfolio here is small enough to check by hand. Weights are decimals (0.25 = 25%).
"""

import pandas as pd
import pytest

from pm_agent.analytics import (
    Portfolio,
    active_weights,
    apply_trades,
    check_limits,
    compare_flags,
    exposure_summary,
    position_weights,
    sector_exposure,
    weight_changes,
)


def prices(**closes: float) -> pd.Series:
    """Close prices by ticker, e.g. prices(A=10, B=20)."""
    return pd.Series(closes, dtype=float)


# ---------- 1.3 weights and exposure ----------


def test_position_weights_simple():
    # Two $50 positions plus $100 cash: NAV $200, each position 25%.
    port = Portfolio(name="t", positions={"A": 5, "B": 2}, cash=100)
    w = position_weights(port, prices(A=10, B=25))

    assert list(w.columns) == ["ticker", "shares", "price", "market_value", "weight"]
    assert w.set_index("ticker")["market_value"].to_dict() == {"A": 50, "B": 50}
    assert w.set_index("ticker")["weight"].to_dict() == pytest.approx({"A": 0.25, "B": 0.25})


def test_position_weights_sorted_biggest_first_including_shorts():
    # A short counts as big by its absolute size.
    port = Portfolio(name="t", positions={"A": 1, "B": -3, "C": 2}, cash=0)
    w = position_weights(port, prices(A=10, B=10, C=10))
    assert list(w["ticker"]) == ["B", "C", "A"]


def test_position_weights_missing_price_errors():
    port = Portfolio(name="t", positions={"A": 1, "ZZZ": 1}, cash=0)
    with pytest.raises(ValueError, match="No price for ZZZ"):
        position_weights(port, prices(A=10))


def test_exposure_summary_with_short():
    # $100 long, $20 short, $20 cash -> NAV 100, gross 120%, net 80%.
    port = Portfolio(name="t", positions={"LONG": 10, "SHORT": -2}, cash=20)
    s = exposure_summary(port, prices(LONG=10, SHORT=10))

    assert s["nav_usd"] == pytest.approx(100)
    assert s["cash_usd"] == pytest.approx(20)
    assert s["cash_weight"] == pytest.approx(0.20)
    assert s["long_exposure"] == pytest.approx(1.00)
    assert s["short_exposure"] == pytest.approx(-0.20)
    assert s["gross_exposure"] == pytest.approx(1.20)
    assert s["net_exposure"] == pytest.approx(0.80)
    assert (s["num_positions"], s["num_long"], s["num_short"]) == (2, 1, 1)


def test_exposure_summary_missing_price_errors():
    port = Portfolio(name="t", positions={"A": 1}, cash=0)
    with pytest.raises(ValueError, match="No price for A"):
        exposure_summary(port, prices(B=10))


# ---------- 1.4 benchmark comparison ----------

# NAV $100: A 50%, B 30%, C -10% (short), cash 30%.
BENCH_PORT = Portfolio(name="t", positions={"A": 5, "B": 3, "C": -1}, cash=30)
BENCH_PRICES = prices(A=10, B=10, C=10, D=10)
# Benchmark holds A, B and D (not held by the fund); the fund's C is not in it.
BENCHMARK = pd.Series({"A": 0.40, "B": 0.40, "D": 0.20})
SECTORS = pd.Series({"A": "Tech", "B": "Health", "C": "Tech", "D": "Health"})


def test_active_weights():
    a = active_weights(BENCH_PORT, BENCH_PRICES, BENCHMARK).set_index("ticker")

    assert list(a.columns) == ["portfolio_weight", "benchmark_weight", "active_weight"]
    assert a.loc["A"].to_dict() == pytest.approx(
        {"portfolio_weight": 0.50, "benchmark_weight": 0.40, "active_weight": 0.10}
    )
    # Held but not in the benchmark: active = the whole (short) weight.
    assert a.loc["C"].to_dict() == pytest.approx(
        {"portfolio_weight": -0.10, "benchmark_weight": 0.0, "active_weight": -0.10}
    )
    # In the benchmark but not held: underweight by the full benchmark weight.
    assert a.loc["D"].to_dict() == pytest.approx(
        {"portfolio_weight": 0.0, "benchmark_weight": 0.20, "active_weight": -0.20}
    )


def test_active_weights_biggest_bets_first():
    a = active_weights(BENCH_PORT, BENCH_PRICES, BENCHMARK)
    # |active|: D 0.20, B 0.10, A 0.10, C 0.10 -> D first; ties keep a stable order.
    assert a["ticker"].iloc[0] == "D"
    assert set(a["ticker"]) == {"A", "B", "C", "D"}


def test_sector_exposure_without_benchmark():
    s = sector_exposure(BENCH_PORT, BENCH_PRICES, SECTORS).set_index("sector")

    assert list(s.columns) == ["long", "short", "net", "gross"]
    # Tech holds A (+50%) and the C short (-10%).
    assert s.loc["Tech"].to_dict() == pytest.approx(
        {"long": 0.50, "short": -0.10, "net": 0.40, "gross": 0.60}
    )
    assert s.loc["Health"].to_dict() == pytest.approx(
        {"long": 0.30, "short": 0.0, "net": 0.30, "gross": 0.30}
    )


def test_sector_exposure_with_benchmark():
    s = sector_exposure(BENCH_PORT, BENCH_PRICES, SECTORS, BENCHMARK).set_index("sector")

    assert list(s.columns) == ["long", "short", "net", "gross", "benchmark", "active"]
    # Active compares the fund's net sector weight with the benchmark's.
    assert s.loc["Tech", "benchmark"] == pytest.approx(0.40)
    assert s.loc["Tech", "active"] == pytest.approx(0.0)
    # Health includes D, which the fund does not hold.
    assert s.loc["Health", "benchmark"] == pytest.approx(0.60)
    assert s.loc["Health", "active"] == pytest.approx(-0.30)


def test_sector_exposure_includes_benchmark_only_sector():
    bench = pd.Series({"A": 0.5, "E": 0.5})
    sectors = pd.Series({"A": "Tech", "B": "Health", "C": "Tech", "E": "Energy"})
    s = sector_exposure(BENCH_PORT, BENCH_PRICES, sectors, bench).set_index("sector")
    assert s.loc["Energy"].to_dict() == pytest.approx(
        {"long": 0.0, "short": 0.0, "net": 0.0, "gross": 0.0, "benchmark": 0.5, "active": -0.5}
    )


def test_sector_exposure_missing_sector_errors():
    with pytest.raises(ValueError, match="No sector for C"):
        sector_exposure(BENCH_PORT, BENCH_PRICES, SECTORS.drop("C"))


# ---------- 1.5 limits ----------


def limits_port(positions: dict, cash: float, **limits: float) -> Portfolio:
    """A portfolio priced at $1 a share, so shares = dollars (and = weight % when NAV is 100)."""
    return Portfolio(name="t", positions=positions, cash=cash, limits=limits)


def one_dollar(port: Portfolio) -> pd.Series:
    return pd.Series(1.0, index=list(port.positions) + ["D"])


def test_limits_position_breach_and_warning():
    # NAV 100: A 16% (over 15%), B -14% short (inside the 13.5% warning band), C 10% fine.
    port = limits_port({"A": 16, "B": -14, "C": 10}, cash=88, max_position=0.15, warn_at=0.9)
    flags = check_limits(port, one_dollar(port))

    assert flags == [
        {"rule": "max_position", "subject": "A", "value": pytest.approx(0.16),
         "limit": 0.15, "status": "breach"},
        {"rule": "max_position", "subject": "B", "value": pytest.approx(-0.14),
         "limit": 0.15, "status": "warning"},
    ]


def test_limits_all_clear():
    port = limits_port({"A": 10, "B": 10}, cash=80, max_position=0.15, max_gross=1.15, warn_at=0.9)
    assert check_limits(port, one_dollar(port)) == []


def test_limits_breaches_listed_before_warnings():
    # Two position warnings (14% each) are checked first, but the gross breach (28% > 25%)
    # must come first in the output.
    port = limits_port({"A": 14, "B": 14}, cash=72, max_position=0.15, max_gross=0.25, warn_at=0.9)
    flags = check_limits(port, one_dollar(port))
    assert [(f["rule"], f["subject"], f["status"]) for f in flags] == [
        ("max_gross", "portfolio", "breach"),
        ("max_position", "A", "warning"),
        ("max_position", "B", "warning"),
    ]


@pytest.mark.parametrize(
    "a_dollars, cash, expected",
    [
        (89, 11, [("min_net", "breach")]),    # 89% < 90%
        (91, 9, [("min_net", "warning")]),    # within 2 points of 90%
        (95, 5, []),                          # comfortably inside 90-102%
        (101, -1, [("max_net", "warning")]),  # within 2 points of 102% (borrowed cash)
        (103, -3, [("max_net", "breach")]),   # 103% > 102%
    ],
)
def test_limits_net_uses_fixed_buffer(a_dollars, cash, expected):
    # warn_at=0.9 would put 95% in max_net's warning band (>= 91.8%); net rules ignore it
    # and use net_warn_buffer instead.
    port = limits_port({"A": a_dollars}, cash=cash, min_net=0.90, max_net=1.02,
                       warn_at=0.9, net_warn_buffer=0.02)
    flags = check_limits(port, one_dollar(port))
    assert [(f["rule"], f["status"]) for f in flags] == expected


def test_limits_stock_active():
    # A: 10% vs benchmark 4% -> active +6%, a breach of the 5% limit.
    # B: 85% vs 86% -> -1%, fine.
    # D: not held, benchmark 5% -> active -5%: exactly at the limit is a warning, not a breach.
    port = limits_port({"A": 10, "B": 85}, cash=5, max_stock_active=0.05, warn_at=0.9)
    bench = pd.Series({"A": 0.04, "B": 0.86, "D": 0.05})
    flags = check_limits(port, one_dollar(port), benchmark=bench)

    assert [(f["rule"], f["subject"], f["status"]) for f in flags] == [
        ("max_stock_active", "A", "breach"),
        ("max_stock_active", "D", "warning"),
    ]
    assert flags[1]["value"] == pytest.approx(-0.05)


def test_limits_sector_active():
    # Tech: 17.5% vs 10% -> +7.5% (warning line 7.2%). Health: 77.5% vs 90% -> -12.5%, breach.
    port = limits_port({"A": 17.5, "B": 77.5}, cash=5, max_sector_active=0.08, warn_at=0.9)
    bench = pd.Series({"A": 0.10, "B": 0.90})
    sectors = pd.Series({"A": "Tech", "B": "Health"})
    flags = check_limits(port, one_dollar(port), sectors, bench)

    assert flags == [
        {"rule": "max_sector_active", "subject": "Health", "value": pytest.approx(-0.125),
         "limit": 0.08, "status": "breach"},
        {"rule": "max_sector_active", "subject": "Tech", "value": pytest.approx(0.075),
         "limit": 0.08, "status": "warning"},
    ]


def test_limits_unknown_key_errors():
    port = limits_port({"A": 10}, cash=90, max_positon=0.15)  # typo
    with pytest.raises(ValueError, match="Unknown limit key.*max_positon.*Known keys.*max_position"):
        check_limits(port, one_dollar(port))


def test_limits_active_rule_needs_benchmark():
    port = limits_port({"A": 10}, cash=90, max_stock_active=0.05)
    with pytest.raises(ValueError, match="max_stock_active needs a benchmark"):
        check_limits(port, one_dollar(port))


# ---------- 1.6 what-if trades ----------

# NAV $100: A 5 shares @ $10 = $50, cash $50. B has a price but is not held.
TRADE_PORT = Portfolio(name="t", positions={"A": 5}, cash=50)
TRADE_PRICES = prices(A=10, B=5)


def test_trade_buy_more():
    after = apply_trades(TRADE_PORT, TRADE_PRICES, [{"ticker": "A", "dollars": 20}])
    assert after.positions == {"A": pytest.approx(7)}
    assert after.cash == pytest.approx(30)  # cash moves the opposite way


def test_trade_opens_new_position():
    after = apply_trades(TRADE_PORT, TRADE_PRICES, [{"ticker": "B", "dollars": 30}])
    assert after.positions == {"A": 5, "B": pytest.approx(6)}
    assert after.cash == pytest.approx(20)


def test_trade_fully_closes_position():
    after = apply_trades(TRADE_PORT, TRADE_PRICES, [{"ticker": "A", "dollars": -50}])
    assert after.positions == {}  # closed positions are dropped, not left at 0 shares
    assert after.cash == pytest.approx(100)


def test_trade_selling_more_than_held_goes_short():
    after = apply_trades(TRADE_PORT, TRADE_PRICES, [{"ticker": "A", "dollars": -70}])
    assert after.positions == {"A": pytest.approx(-2)}
    assert after.cash == pytest.approx(120)


def test_trades_keep_nav_and_leave_original_unchanged():
    trades = [{"ticker": "A", "dollars": -10}, {"ticker": "B", "dollars": 25}]
    after = apply_trades(TRADE_PORT, TRADE_PRICES, trades)

    nav_before = exposure_summary(TRADE_PORT, TRADE_PRICES)["nav_usd"]
    assert exposure_summary(after, TRADE_PRICES)["nav_usd"] == pytest.approx(nav_before)
    assert after.cash == pytest.approx(35)
    assert TRADE_PORT.positions == {"A": 5}  # the original is untouched
    assert TRADE_PORT.cash == 50


def test_trade_unknown_ticker_errors():
    with pytest.raises(ValueError, match="Unknown ticker 'ZZZ'.*A, B"):
        apply_trades(TRADE_PORT, TRADE_PRICES, [{"ticker": "ZZZ", "dollars": 10}])


def test_trimming_fixes_a_breach():
    # A is 16% of a $100 fund, over its 15% limit. Selling $2 brings it to 14%: still in the
    # warning band, but the breach is resolved and nothing new appears.
    port = limits_port({"A": 16}, cash=84, max_position=0.15, warn_at=0.9)
    px = one_dollar(port)
    before = check_limits(port, px)
    after = check_limits(apply_trades(port, px, [{"ticker": "A", "dollars": -2}]), px)

    assert [f["status"] for f in before] == ["breach"]
    assert [f["status"] for f in after] == ["warning"]
    assert after[0]["value"] == pytest.approx(0.14)

    changes = compare_flags(before, after)
    assert [(f["rule"], f["subject"], f["status"]) for f in changes["resolved"]] == [
        ("max_position", "A", "breach")
    ]
    assert changes["new"] == []


def flag(rule, subject, status):
    return {"rule": rule, "subject": subject, "value": 0.0, "limit": 0.0, "status": status}


def test_compare_flags_identity_rules():
    before = [
        flag("max_position", "A", "breach"),   # -> warning: resolved breach
        flag("max_position", "B", "warning"),  # -> breach: new
        flag("max_gross", "portfolio", "warning"),  # -> gone: resolved
        flag("max_position", "C", "warning"),  # -> still warning: neither
    ]
    after = [
        flag("max_position", "B", "breach"),
        flag("max_position", "A", "warning"),
        flag("max_position", "C", "warning"),
        flag("max_position", "D", "warning"),  # had no flag before: new
    ]
    changes = compare_flags(before, after)
    assert [(f["subject"], f["status"]) for f in changes["resolved"]] == [
        ("A", "breach"), ("portfolio", "warning")
    ]
    assert [(f["subject"], f["status"]) for f in changes["new"]] == [
        ("B", "breach"), ("D", "warning")
    ]


# ---------- 1.7 weight changes ----------


def test_weight_changes_price_doubles():
    # 50/50 with no cash; A doubles -> A $100 of $150 (66.7%), B $50 (33.3%). No trading.
    port = Portfolio(name="t", positions={"A": 1, "B": 1}, cash=0)
    c = weight_changes(port, prices(A=50, B=50), prices(A=100, B=50)).set_index("ticker")

    assert list(c.columns) == ["start_weight", "end_weight", "change", "price_return"]
    assert c.loc["A"].to_dict() == pytest.approx(
        {"start_weight": 0.5, "end_weight": 2 / 3, "change": 2 / 3 - 0.5, "price_return": 1.0}
    )
    assert c.loc["B"].to_dict() == pytest.approx(
        {"start_weight": 0.5, "end_weight": 1 / 3, "change": 1 / 3 - 0.5, "price_return": 0.0}
    )


def test_weight_changes_biggest_move_first():
    # Start 1/3 each. End A 11/26 (+9.0 pts), B 10/26 (+5.1), C 5/26 (-14.1): C moved most.
    port = Portfolio(name="t", positions={"A": 1, "B": 1, "C": 1}, cash=0)
    c = weight_changes(port, prices(A=10, B=10, C=10), prices(A=11, B=10, C=5))
    assert c["ticker"].iloc[0] == "C"


def test_weight_changes_missing_end_price_errors():
    port = Portfolio(name="t", positions={"A": 1}, cash=0)
    with pytest.raises(ValueError, match="No price for A"):
        weight_changes(port, prices(A=10), prices(B=10))
