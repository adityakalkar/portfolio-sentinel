"""Sanity tests for the planted-event scenario days (task 5.2).

Each scenario in data/scenarios/<name>/ is a full dataset. On its last date it must contain
exactly the planted events: these flags and these big price moves, nothing else. The brief
eval (5.3) relies on that.
"""

from pathlib import Path

import pytest
from brief_scoring import BIG_MOVE  # what counts as a big move: one definition, in the scorer

from pm_agent import analytics, data

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "data" / "scenarios"

# scenario -> (exact flags on the last date, exact set of big movers)
PLANTED = {
    "quiet": ([], set()),
    "big_move": ([], {"META"}),
    "single_breach": ([("max_position", "MSFT", "breach")], {"MSFT"}),
    "two_events": ([("max_position", "MSFT", "breach")], {"MSFT", "TSLA"}),
}


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_scenario_has_exactly_its_planted_events(name, monkeypatch):
    monkeypatch.setenv("PM_AGENT_DATA_DIR", str(SCENARIOS_DIR / name))
    fund = data.load_portfolio("sample_fund")
    latest, prices = data.prices_on()
    _, prev_prices = data.prices_on(data.previous_date(latest))

    flags = analytics.check_limits(fund, prices, data.sector_map(),
                                   data.benchmark_weights(fund.benchmark))
    moves = analytics.weight_changes(fund, prev_prices, prices)
    big_movers = set(moves.loc[moves["price_return"].abs() >= BIG_MOVE, "ticker"])

    expected_flags, expected_movers = PLANTED[name]
    assert [(f["rule"], f["subject"], f["status"]) for f in flags] == expected_flags
    assert big_movers == expected_movers


def test_every_scenario_is_tested():
    assert {p.name for p in SCENARIOS_DIR.iterdir() if p.is_dir()} == set(PLANTED)
