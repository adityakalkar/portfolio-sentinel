"""Turn real-world downloads into our data format (M6). Pure functions: they take text and
tables and return tables; scripts/make_real_data.py does the downloading and file writing.

Sources:
- SEC 13F filings: the quarterly list of US stocks a large investment manager holds.
- iShares ETF holdings CSV: an index fund's published holdings, used as the benchmark.
- Yahoo Finance (via yfinance): daily closing prices and, for a few stocks, sectors.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd

# Yahoo Finance's sector names -> the GICS names our data uses (from the benchmark file).
YAHOO_TO_GICS = {
    "Technology": "Information Technology",
    "Financial Services": "Financials",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Healthcare": "Health Care",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Basic Materials": "Materials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}

# iShares abbreviates one GICS sector name.
ISHARES_SECTORS = {"Communication": "Communication Services"}


def parse_13f_infotable(xml_text: str) -> pd.DataFrame:
    """A 13F information table -> one row per security: cusip, name, shares, value_usd.

    A filing can report one security in several rows (one per sub-manager), so rows are
    added up by CUSIP. Options (putCall) and bonds (PRN, a principal amount) are left out:
    we only model shares of stock.
    """
    root = ET.fromstring(xml_text)
    ns = {"n": root.tag.split("}")[0].strip("{")}  # the XML namespace, e.g. {http://...}
    rows = []
    for item in root.findall("n:infoTable", ns):
        is_shares = item.findtext("n:shrsOrPrnAmt/n:sshPrnamtType", namespaces=ns) == "SH"
        if not is_shares or item.find("n:putCall", ns) is not None:
            continue
        rows.append({
            "cusip": item.findtext("n:cusip", namespaces=ns),
            "name": item.findtext("n:nameOfIssuer", namespaces=ns),
            "shares": int(item.findtext("n:shrsOrPrnAmt/n:sshPrnamt", namespaces=ns)),
            "value_usd": int(item.findtext("n:value", namespaces=ns)),
        })
    table = pd.DataFrame(rows, columns=["cusip", "name", "shares", "value_usd"])
    return (table.groupby(["cusip", "name"], as_index=False)[["shares", "value_usd"]].sum()
            .sort_values("value_usd", ascending=False, kind="stable").reset_index(drop=True))


def parse_ishares_holdings(csv_text: str) -> tuple[pd.DataFrame, str]:
    """An iShares holdings CSV -> (ticker, name, sector, weight table; as-of date).

    The file has a few lines about the fund, then the table, then a disclaimer. Only listed
    stocks are kept (no cash, collateral, warrants, or unlisted shares). Each weight is the
    stock's market value over all kept stocks' value, so the weights add up to exactly 1.
    """
    lines = csv_text.lstrip("\ufeff").splitlines()
    as_of_line = next(line for line in lines if line.startswith("Fund Holdings as of"))
    as_of = datetime.strptime(as_of_line.split(",", 1)[1].strip('" '), "%b %d, %Y")
    start = next(i for i, line in enumerate(lines) if line.startswith("Ticker,"))
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    table = pd.read_csv(io.StringIO("\n".join(lines[start:end])), dtype=str)

    stocks = table[(table["Type"] == "EQUITY") & (table["Market Weight"] != "-")]
    value = stocks["Market Value"].str.replace(",", "").astype(float)
    holdings = pd.DataFrame({
        "ticker": stocks["Ticker"].map(canonical_ticker),
        "name": stocks["Name"],
        "sector": stocks["Sector"].replace(ISHARES_SECTORS),
        "weight": value / value.sum(),
    }).reset_index(drop=True)
    return holdings, as_of.strftime("%Y-%m-%d")


def canonical_ticker(ticker: str) -> str:
    """One spelling for share classes: OpenFIGI's LEN/B, Yahoo's BRK-B and iShares' BF B
    become LEN.B, BRK.B and BF.B."""
    return ticker.replace("/", ".").replace("-", ".").replace(" ", ".")


def yahoo_symbol(ticker: str) -> str:
    """Our ticker -> Yahoo Finance's spelling (BRK.B -> BRK-B)."""
    return ticker.replace(".", "-")


def attach_tickers(holdings: pd.DataFrame, cusip_to_ticker: dict[str, str]) -> pd.DataFrame:
    """Add a ticker column. Every CUSIP must be mapped: a silent gap would drop a holding."""
    missing = [f"{c} ({n})" for c, n in zip(holdings["cusip"], holdings["name"])
               if c not in cusip_to_ticker]
    if missing:
        raise ValueError(f"No ticker for CUSIP {', '.join(missing)}; add it to the mapping")
    return holdings.assign(ticker=holdings["cusip"].map(cusip_to_ticker))


def portfolio_json(holdings: pd.DataFrame, prices: pd.Series, *, display_name: str,
                   benchmark: str, cash_weight: float, limits: dict) -> dict:
    """Our portfolio format. A 13F reports no cash, so cash is set to cash_weight of NAV at
    these prices (an assumption, stated in the README)."""
    invested = float((holdings.set_index("ticker")["shares"] * prices).sum())
    cash = invested * cash_weight / (1 - cash_weight)
    return {
        "display_name": display_name,
        "benchmark": benchmark,
        "cash": round(cash, 2),
        "limits": limits,
        "positions": [{"ticker": t, "shares": int(s)}
                      for t, s in zip(holdings["ticker"], holdings["shares"])],
    }


def gics_sector(yahoo_sector: str) -> str:
    if yahoo_sector not in YAHOO_TO_GICS:
        raise ValueError(f"Unknown Yahoo sector '{yahoo_sector}'. Known: "
                         f"{', '.join(YAHOO_TO_GICS)}")
    return YAHOO_TO_GICS[yahoo_sector]
