"""Tests for the real-data importers (M6). Pure parsing: small inline samples, no network."""

import pandas as pd
import pytest

from pm_agent import importers

INFO_TABLE = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>1000</value>
    <shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>APPLE INC</nameOfIssuer><cusip>037833100</cusip><value>500</value>
    <shrsOrPrnAmt><sshPrnamt>50</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>COCA COLA CO</nameOfIssuer><cusip>191216100</cusip><value>700</value>
    <shrsOrPrnAmt><sshPrnamt>70</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>SOME CO</nameOfIssuer><cusip>111111111</cusip><value>9</value>
    <shrsOrPrnAmt><sshPrnamt>9</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>
    <putCall>Put</putCall>
  </infoTable>
  <infoTable>
    <nameOfIssuer>SOME BOND</nameOfIssuer><cusip>222222222</cusip><value>9</value>
    <shrsOrPrnAmt><sshPrnamt>9</sshPrnamt><sshPrnamtType>PRN</sshPrnamtType></shrsOrPrnAmt>
  </infoTable>
</informationTable>"""


def test_parse_13f_adds_up_rows_per_security_and_keeps_only_shares():
    holdings = importers.parse_13f_infotable(INFO_TABLE)
    # Apple is reported in two rows (two sub-managers): one position of 150 shares.
    # The put option and the bond (PRN = principal amount, not shares) are left out.
    assert holdings.to_dict("records") == [
        {"cusip": "037833100", "name": "APPLE INC", "shares": 150, "value_usd": 1500},
        {"cusip": "191216100", "name": "COCA COLA CO", "shares": 70, "value_usd": 700},
    ]


def test_canonical_and_yahoo_tickers():
    assert importers.canonical_ticker("LEN/B") == "LEN.B"  # OpenFIGI style
    assert importers.canonical_ticker("BRK-B") == "BRK.B"  # Yahoo style
    assert importers.canonical_ticker("AAPL") == "AAPL"
    assert importers.yahoo_symbol("BRK.B") == "BRK-B"
    assert importers.yahoo_symbol("AAPL") == "AAPL"


def test_attach_tickers_uses_mapping_and_reports_gaps():
    holdings = importers.parse_13f_infotable(INFO_TABLE)
    mapping = {"037833100": "AAPL", "191216100": "KO"}
    assert list(importers.attach_tickers(holdings, mapping)["ticker"]) == ["AAPL", "KO"]
    with pytest.raises(ValueError, match="No ticker for CUSIP 191216100 \\(COCA COLA CO\\)"):
        importers.attach_tickers(holdings, {"037833100": "AAPL"})


def test_portfolio_json_sizes_cash_as_a_share_of_nav():
    holdings = pd.DataFrame({"ticker": ["AAPL", "KO"], "shares": [10, 20]})
    prices = pd.Series({"AAPL": 50.0, "KO": 24.5})  # $500 + $490 = $990 invested
    portfolio = importers.portfolio_json(holdings, prices, display_name="Test 13F",
                                         benchmark="sp500", cash_weight=0.01, limits={"x": 1})
    # Cash is 1% of NAV: NAV = 990 / 0.99 = 1000, so cash = 10.
    assert portfolio["cash"] == pytest.approx(10.0)
    assert portfolio["positions"] == [{"ticker": "AAPL", "shares": 10},
                                      {"ticker": "KO", "shares": 20}]
    assert (portfolio["display_name"], portfolio["benchmark"], portfolio["limits"]) == (
        "Test 13F", "sp500", {"x": 1})


def test_yahoo_sector_names_map_to_gics():
    assert importers.gics_sector("Technology") == "Information Technology"
    assert importers.gics_sector("Financial Services") == "Financials"
    assert importers.gics_sector("Consumer Cyclical") == "Consumer Discretionary"
    with pytest.raises(ValueError, match="Unknown Yahoo sector 'Crypto'"):
        importers.gics_sector("Crypto")


ISHARES_CSV = '''﻿iShares Core S&P 500 ETF
Fund Holdings as of,"Oct 26, 2026"
Stock,"-"

Ticker,Name,Type,Sector,Asset Class,Market Value,Exchange,Market Weight
"NVDA","NVIDIA","EQUITY","Information Technology","Equity","600.00","NASDAQ","6.00"
"BRK B","BERKSHIRE HATHAWAY CLASS B","EQUITY","Financials","Equity","300.00","NYSE","3.00"
"GOOGL","ALPHABET CLASS A","EQUITY","Communication","Equity","100.00","NASDAQ","1.00"
"HOLX","HOLOGIC INC","EQUITY","Health Care","Equity","0.01","NO MARKET (E.G. UNLISTED)","-"
"USD","USD CASH","CASH","Cash and/or Derivatives","Cash","50.00","-","0.50"

"The content contained herein is owned or licensed by BlackRock"
'''


def test_parse_ishares_holdings_keeps_listed_stocks_with_weights_from_value():
    holdings, as_of = importers.parse_ishares_holdings(ISHARES_CSV)
    assert as_of == "2026-10-26"
    records = holdings.to_dict("records")
    # Cash and the unlisted stock are dropped; weights are each stock's share of the
    # stocks' total value (600 + 300 + 100 = 1,000), so they add up to exactly 1.
    assert [r["ticker"] for r in records] == ["NVDA", "BRK.B", "GOOGL"]
    assert [r["weight"] for r in records] == pytest.approx([0.6, 0.3, 0.1])
    assert records[2]["sector"] == "Communication Services"  # iShares says "Communication"
    assert records[1]["name"] == "BERKSHIRE HATHAWAY CLASS B"


def test_canonical_ticker_handles_ishares_spaces():
    assert importers.canonical_ticker("BF B") == "BF.B"
