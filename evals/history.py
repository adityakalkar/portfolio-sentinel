"""Eval results history (task 4.3): one summary row per eval run in evals/results.db (SQLite).

The full per-case results go to evals/results/<timestamp>.json; this table keeps just the
headline numbers, so scores can be compared and charted over time (M7 dashboard).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

COLUMNS = {
    "timestamp": "TEXT",
    "label": "TEXT",  # what changed in this run, e.g. "baseline" or "prompt caching"
    "model": "TEXT",
    "effort": "TEXT",
    "git_commit": "TEXT",
    "case_filter": "TEXT",  # "all", "limit=3", "category=what_if", ...
    "cases": "INTEGER",
    "accuracy": "REAL",
    "right_tool_first": "REAL",
    "hallucination_rate": "REAL",
    "mean_wasted_calls": "REAL",
    "mean_latency_s": "REAL",
    "mean_input_tokens": "REAL",
    "mean_output_tokens": "REAL",
    "total_cost_usd": "REAL",
    "mean_cost_usd": "REAL",
    "results_file": "TEXT",
}


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    columns = ", ".join(f"{name} {kind}" for name, kind in COLUMNS.items())
    conn.execute(f"CREATE TABLE IF NOT EXISTS eval_runs ({columns})")
    return conn


def append_run(db_path: Path, row: dict) -> None:
    names = list(COLUMNS)
    placeholders = ", ".join("?" for _ in names)
    with _connect(db_path) as conn:  # commits on success
        conn.execute(
            f"INSERT INTO eval_runs ({', '.join(names)}) VALUES ({placeholders})",
            [row[name] for name in names],
        )
    conn.close()


def load_runs(db_path: Path) -> list[dict]:
    """All runs, oldest first."""
    with _connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM eval_runs ORDER BY rowid").fetchall()
    conn.close()
    return [dict(row) for row in rows]
