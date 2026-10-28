"""Build the real dataset in data/real/ (M6): Berkshire Hathaway's 13F holdings, measured
against the S&P 500 (iShares IVV holdings), with daily closing prices from Yahoo Finance.

    pip install -e ".[real]"                 # yfinance, needed only here
    python scripts/make_real_data.py         # refresh everything (prices: new days only)
    python scripts/make_real_data.py --prices-only

Then point the agent at it: PM_AGENT_DATA_DIR=data/real, portfolio "berkshire_13f".

Inputs (raw downloads live in data/raw/, git-ignored):
- data/raw/IVV_holdings.csv: download by hand from the iShares IVV page (Holdings ->
  Download); the site doesn't serve it to scripts.
- The latest 13F filing, fetched from SEC EDGAR. The SEC asks automated requests to
  identify themselves with a contact email: SEC_USER_AGENT_EMAIL in .env.
- data/real/cusip_tickers.csv: 13F filings name stocks by CUSIP; this reviewed list maps
  them to tickers. New CUSIPs are looked up on OpenFIGI and appended.
- Prices and sectors from Yahoo Finance via yfinance (unofficial; may break or rate-limit).

Assumptions (also in the README): a 13F reports no cash, so cash is set to 2% of NAV; the
holdings are as of the filing's quarter end, while prices are current; risk limits suit a
concentrated fund (see LIMITS).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from pm_agent import importers  # noqa: E402  (after the path tweak, so the script runs anywhere)
from pm_agent.agent import load_dotenv  # noqa: E402

RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "real"
MAPPING_FILE = OUT / "cusip_tickers.csv"

BERKSHIRE_CIK = "0001067983"
PORTFOLIO = "berkshire_13f"
BENCHMARK = "sp500_ivv"
CASH_WEIGHT = 0.02
PRICE_HISTORY_DAYS = 120  # calendar days of prices on a fresh build
# Limits for a concentrated fund that doesn't try to track the index. Under the sample
# fund's tight limits (15% / 5% / 8%), Berkshire shows ~15 breaches every day that only say
# "this isn't the S&P 500", which would bury real news in the brief.
LIMITS = {
    "max_position": 0.25,  # US tax rule for funds (RIC diversification): <= 25% in one issuer
    "max_stock_active": 0.20,  # wide: the fund isn't benchmarked stock by stock
    "max_sector_active": 0.25,
    "max_gross": 1.15, "min_net": 0.90, "max_net": 1.02,  # same as the sample fund
    "warn_at": 0.9, "net_warn_buffer": 0.02,
}


# ---------- downloads ----------


def sec_get(url: str) -> bytes:
    email = os.environ.get("SEC_USER_AGENT_EMAIL", "").strip()
    if not email:
        sys.exit("Set SEC_USER_AGENT_EMAIL in .env: the SEC asks for a contact email.")
    request = urllib.request.Request(url, headers={"User-Agent": f"pm-agent research {email}"})
    time.sleep(0.2)  # stay well under the SEC's 10 requests per second
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def latest_13f() -> tuple[str, str]:
    """(quarter end date, information table XML) of the newest 13F-HR, cached in data/raw."""
    filings = json.loads(sec_get(f"https://data.sec.gov/submissions/CIK{BERKSHIRE_CIK}.json"))
    recent = filings["filings"]["recent"]
    i = recent["form"].index("13F-HR")  # newest first; amendments (13F-HR/A) are skipped
    quarter_end, accession = recent["reportDate"][i], recent["accessionNumber"][i]
    cached = RAW / f"brk_13f_{quarter_end}.xml"
    if not cached.exists():
        folder = (f"https://www.sec.gov/Archives/edgar/data/{int(BERKSHIRE_CIK)}/"
                  f"{accession.replace('-', '')}")
        files = json.loads(sec_get(f"{folder}/index.json"))["directory"]["item"]
        table = next(f["name"] for f in files
                     if f["name"].endswith(".xml") and f["name"] != "primary_doc.xml")
        cached.write_bytes(sec_get(f"{folder}/{table}"))
    return quarter_end, cached.read_text()


def ticker_mapping(holdings: pd.DataFrame) -> dict[str, str]:
    """The reviewed CUSIP -> ticker list, extended via OpenFIGI for any new CUSIPs."""
    rows = list(csv.DictReader(MAPPING_FILE.open())) if MAPPING_FILE.exists() else []
    known = {r["cusip"]: r["ticker"] for r in rows}
    new = [(c, n) for c, n in zip(holdings["cusip"], holdings["name"]) if c not in known]
    for i in range(0, len(new), 10):  # OpenFIGI: up to 10 lookups per request without a key
        batch = new[i:i + 10]
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"}
                           for c, _ in batch]).encode()
        request = urllib.request.Request("https://api.openfigi.com/v3/mapping", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            answers = json.load(response)
        for (cusip, name), answer in zip(batch, answers):
            tickers = {d["ticker"] for d in answer.get("data", [])}
            if len(tickers) != 1:
                sys.exit(f"OpenFIGI gave {sorted(tickers) or 'nothing'} for {cusip} ({name}); "
                         f"add it to {MAPPING_FILE.relative_to(REPO)} by hand")
            ticker = importers.canonical_ticker(tickers.pop())
            rows.append({"cusip": cusip, "ticker": ticker, "name": name, "source": "OpenFIGI"})
            known[cusip] = ticker
        time.sleep(3)  # OpenFIGI allows 25 requests a minute without a key
    write_csv(MAPPING_FILE, ["cusip", "ticker", "name", "source"],
              [[r["cusip"], r["ticker"], r["name"], r["source"]]
               for r in sorted(rows, key=lambda r: r["cusip"])])
    return known


def update_prices(tickers: list[str]) -> pd.DataFrame:
    """Daily closes for tickers (a date x ticker table), cached in data/raw/yahoo_prices.csv.
    Only days after the cache's last date are downloaded."""
    import yfinance as yf  # optional dependency: pip install -e ".[real]"

    cache_file = RAW / "yahoo_prices.csv"
    cache = (pd.read_csv(cache_file, index_col="date", dtype={"date": str})
             if cache_file.exists() else pd.DataFrame())
    missing_tickers = [t for t in tickers if t not in cache.columns]
    start = (date.today() - timedelta(days=PRICE_HISTORY_DAYS) if cache.empty or missing_tickers
             else date.fromisoformat(cache.index.max()) + timedelta(days=1))
    if start <= date.today():
        symbols = {importers.yahoo_symbol(t): t for t in tickers}
        raw = yf.download(list(symbols), start=start.isoformat(), interval="1d",
                          auto_adjust=False, progress=False)["Close"]
        fresh = raw.rename(columns=symbols)
        fresh.index = fresh.index.strftime("%Y-%m-%d")
        fresh.index.name = "date"
        cache = fresh.combine_first(cache) if not cache.empty else fresh
        cache.sort_index().to_csv(cache_file)
    return cache[tickers].sort_index()


def sectors_from_yahoo(tickers: list[str]) -> dict[str, str]:
    """GICS sector for stocks not in the benchmark file, cached in data/raw/yahoo_sectors.json."""
    import yfinance as yf

    cache_file = RAW / "yahoo_sectors.json"
    cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
    for ticker in tickers:
        if ticker not in cache:
            cache[ticker] = yf.Ticker(importers.yahoo_symbol(ticker)).info["sector"]
    cache_file.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    return {t: importers.gics_sector(cache[t]) for t in tickers}


# ---------- writing data/real ----------


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build data/real from real-world sources.")
    parser.add_argument("--prices-only", action="store_true",
                        help="only add new days of prices (for the daily brief)")
    args = parser.parse_args()
    load_dotenv()
    RAW.mkdir(parents=True, exist_ok=True)

    portfolio_file = OUT / "portfolios" / f"{PORTFOLIO}.json"
    if args.prices_only:
        tickers = [p["ticker"] for p in json.loads(portfolio_file.read_text())["positions"]]
    else:
        ivv_file = RAW / "IVV_holdings.csv"
        if not ivv_file.exists():
            sys.exit(f"Missing {ivv_file.relative_to(REPO)}: download it from the iShares IVV "
                     "page (Holdings -> Download) and save it there.")
        benchmark, benchmark_as_of = importers.parse_ishares_holdings(
            ivv_file.read_text(encoding="utf-8"))
        quarter_end, xml_text = latest_13f()
        holdings = importers.parse_13f_infotable(xml_text)
        holdings = importers.attach_tickers(holdings, ticker_mapping(holdings))
        tickers = list(holdings["ticker"])

    # Keep only days when every holding has a close (e.g. not today mid-session).
    prices = update_prices(tickers).dropna()
    write_csv(OUT / "prices.csv", ["date", "ticker", "close"],
              [[d, t, f"{prices.loc[d, t]:.2f}"] for d in prices.index for t in tickers])
    print(f"Prices: {len(prices)} days, {prices.index[0]} to {prices.index[-1]}")
    if args.prices_only:
        return

    write_csv(OUT / "benchmarks" / f"{BENCHMARK}.csv", ["ticker", "weight"],
              [[t, f"{w:.8f}"] for t, w in zip(benchmark["ticker"], benchmark["weight"])])
    outside = [t for t in tickers if t not in set(benchmark["ticker"])]
    extra_sectors = sectors_from_yahoo(outside)
    names = dict(zip(holdings["ticker"], holdings["name"].str.title()))
    securities = [[t, n, s] for t, n, s in zip(benchmark["ticker"], benchmark["name"],
                                                benchmark["sector"])]
    securities += [[t, names[t], extra_sectors[t]] for t in outside]
    write_csv(OUT / "securities.csv", ["ticker", "name", "sector"], securities)

    latest_prices = prices.iloc[-1]
    portfolio = importers.portfolio_json(
        holdings, latest_prices, benchmark=BENCHMARK, cash_weight=CASH_WEIGHT, limits=LIMITS,
        display_name=f"Berkshire Hathaway 13F (holdings as of {quarter_end})",
    )
    portfolio_file.parent.mkdir(parents=True, exist_ok=True)
    portfolio_file.write_text(json.dumps(portfolio, indent=2) + "\n")
    print(f"Portfolio: {len(tickers)} holdings from the 13F for {quarter_end}; "
          f"benchmark: {len(benchmark)} stocks as of {benchmark_as_of}; "
          f"{len(outside)} holdings outside the benchmark (sectors from Yahoo)")


if __name__ == "__main__":
    main()
