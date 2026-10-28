"""Tests for the eval results history (task 4.3): one summary row per run, in SQLite."""

import pytest

import history


def summary_row(label, accuracy):
    return {"timestamp": f"2026-10-27T10:00:0{len(label)}Z", "label": label,
            "model": "claude-opus-5-5", "effort": "medium", "git_commit": "abc1234",
            "case_filter": "all", "cases": 35, "accuracy": accuracy, "right_tool_first": 0.9,
            "hallucination_rate": 0.1, "mean_wasted_calls": 0.2, "mean_latency_s": 6.5,
            "mean_input_tokens": 7000.0, "mean_output_tokens": 300.0, "total_cost_usd": 1.2,
            "mean_cost_usd": 0.034, "results_file": "evals/results/x.json"}


def test_rows_are_appended_and_read_back_in_order(tmp_path):
    db = tmp_path / "results.db"
    history.append_run(db, summary_row("baseline", 0.8))
    history.append_run(db, summary_row("caching", 0.83))

    runs = history.load_runs(db)
    assert [r["label"] for r in runs] == ["baseline", "caching"]
    assert runs[1]["accuracy"] == pytest.approx(0.83)
    assert runs[0] == summary_row("baseline", 0.8)


def test_missing_cost_is_stored_as_null(tmp_path):
    db = tmp_path / "results.db"
    history.append_run(db, {**summary_row("x", 0.5), "total_cost_usd": None, "mean_cost_usd": None})
    assert history.load_runs(db)[0]["total_cost_usd"] is None
