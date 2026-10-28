# Portfolio Sentinel

An AI agent for portfolio managers: it answers questions about a fund versus its benchmark and
risk limits, and writes a daily morning brief. Built on a custom MCP server, with a
hand-written agent loop and an eval harness.

**Work in progress.** See [docs/SPEC.md](docs/SPEC.md) for the design and
[docs/TASKS.md](docs/TASKS.md) for progress.

## Data

Two datasets, in the same format (`PM_AGENT_DATA_DIR` picks one):

- **`data/sample/`** (default): a synthetic $50M fund with a planted story (an NVDA limit
  breach after a +10% day). Tests and evals use it, so results are repeatable.
  `data/scenarios/` holds planted-event practice days for the morning-brief eval.
- **`data/real/`**: Berkshire Hathaway's latest 13F holdings, measured against the S&P 500,
  with daily prices. Built by `scripts/make_real_data.py`; the morning brief runs on it every
  weekday.

Real-data sources and assumptions:

| What | Source | Notes |
|---|---|---|
| Holdings | Berkshire Hathaway's 13F filing, SEC EDGAR | Quarterly, filed up to 45 days after quarter end, so holdings are weeks or months old. Longs only. |
| Tickers | OpenFIGI, plus one manual entry | 13Fs identify stocks by CUSIP; `data/real/cusip_tickers.csv` is the reviewed mapping. |
| Benchmark | iShares Core S&P 500 ETF (IVV) holdings CSV | Downloaded by hand from the iShares site. |
| Prices, some sectors | Yahoo Finance via `yfinance` | **Unofficial** API: it can change, break, or rate-limit without notice. |
| Cash | Assumed | A 13F reports no cash; set to 2% of NAV. |
| Risk limits | Chosen for a concentrated fund | Max 25% in one stock (the US fund-diversification tax rule), wide limits versus the index (20% per stock, 25% per sector). |

Known simplifications everywhere: benchmark weights are static (in reality they drift with
prices); what-if trades ignore costs, taxes and settlement; prices are daily closes.
