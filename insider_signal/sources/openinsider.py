"""
OpenInsider scraper — SEC Form 4 corporate insider trades.

Covers all CEOs, CFOs, directors, and 10%+ owners required
to file Form 4 with the SEC within 2 business days of a trade.

Source: https://openinsider.com
"""

import time
import logging
from typing import Optional
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from insider_signal.models import Trade, Tier, Action

logger = logging.getLogger(__name__)

# OpenInsider table column positions (0-indexed)
_COL_FILING_DATE = 1
_COL_TRADE_DATE  = 2
_COL_TICKER      = 3
_COL_COMPANY     = 4
_COL_INSIDER     = 5
_COL_TITLE       = 6
_COL_TRADE_TYPE  = 7
_COL_PRICE       = 8
_COL_QTY         = 9
_COL_VALUE       = 11

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_BASE_URL = "https://openinsider.com"
_SEARCH_URL = "https://openinsider.com/search?q={ticker}"

# Transaction codes that represent real open-market purchases
_BUY_CODES  = {"P"}
# Transaction codes that represent real open-market sales
_SELL_CODES = {"S"}
# Planned-sale codes — never scored negative per hard rules
_10B5_CODES = {"S-", "10b5"}


def _clean(text: str) -> str:
    return text.strip().replace("\n", "").replace("\xa0", " ")


def _parse_dollar(text: str) -> Optional[float]:
    """Convert '$1,234,567' or '1.2M' to float. Returns None on failure."""
    try:
        text = text.replace("$", "").replace(",", "").strip()
        if text.endswith("M"):
            return float(text[:-1]) * 1_000_000
        if text.endswith("K"):
            return float(text[:-1]) * 1_000
        return float(text) if text else None
    except (ValueError, AttributeError):
        return None


def _parse_action(trade_type_code: str) -> tuple[Action, bool]:
    """
    Returns (Action, is_10b5_1).
    10b5-1 sales are pre-scheduled — never meaningful as bearish signal.
    """
    code = trade_type_code.strip()
    is_10b5 = any(flag in code for flag in _10B5_CODES)
    if code in _BUY_CODES:
        return Action.BUY, is_10b5
    if code in _SELL_CODES or is_10b5:
        return Action.SELL, is_10b5
    return Action.UNKNOWN, is_10b5


def fetch_ticker(
    ticker: str,
    max_retries: int = 3,
    delay: float = 1.5,
) -> list[Trade]:
    """
    Fetch all recent insider trades for a given ticker from OpenInsider.

    Args:
        ticker:      Stock symbol, e.g. 'NVDA'
        max_retries: Retry count on transient HTTP errors
        delay:       Seconds to wait between retries (polite scraping)

    Returns:
        List of Trade objects. Empty list if no data or fetch fails.
    """
    url = _SEARCH_URL.format(ticker=ticker.upper())
    trades: list[Trade] = []

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=_HEADERS, timeout=10)
            response.raise_for_status()
            break
        except requests.exceptions.HTTPError as e:
            if response.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning(
                    "OpenInsider rate-limited. Waiting %.1fs (attempt %d/%d)",
                    wait, attempt, max_retries
                )
                time.sleep(wait)
            else:
                logger.error("HTTP error fetching %s: %s", url, e)
                return trades
        except requests.exceptions.RequestException as e:
            logger.error("Network error fetching %s: %s", url, e)
            if attempt == max_retries:
                return trades
            time.sleep(delay)
    else:
        logger.error("All retries exhausted for %s", url)
        return trades

    soup = BeautifulSoup(response.text, "html.parser")

    # OpenInsider renders results in a table with class "tinytable"
    table = soup.find("table", {"class": "tinytable"})
    if not table:
        logger.info("No insider trade table found for ticker: %s", ticker)
        return trades

    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 12:
            continue

        try:
            filing_date = _clean(cells[_COL_FILING_DATE].get_text())
            trade_date  = _clean(cells[_COL_TRADE_DATE].get_text())
            insider     = _clean(cells[_COL_INSIDER].get_text())
            title       = _clean(cells[_COL_TITLE].get_text())
            trade_code  = _clean(cells[_COL_TRADE_TYPE].get_text())
            value_text  = _clean(cells[_COL_VALUE].get_text())

            action, is_10b5 = _parse_action(trade_code)

            # Skip unknown transaction types (gifts, awards, etc.)
            if action == Action.UNKNOWN:
                continue

            amount = _parse_dollar(value_text)

            trades.append(Trade(
                tier=Tier.CORPORATE,
                insider_name=insider,
                title=title,
                ticker=ticker.upper(),
                action=action,
                amount_min=amount,
                amount_max=amount,
                trade_date=trade_date,
                filed_date=filing_date,
                source=f"OpenInsider — {url}",
                is_10b5_1=is_10b5,
                is_cross_company=False,  # resolved at aggregation layer
            ))

        except (IndexError, AttributeError) as e:
            logger.debug("Skipping malformed row: %s", e)
            continue

    logger.info("OpenInsider: found %d trades for %s", len(trades), ticker)
    return trades


def fetch_latest_large(
    min_value: float = 1_000_000,
) -> list[Trade]:
    """
    Fetch latest large insider purchases from OpenInsider homepage.
    Filters to trades >= min_value (default $1M).

    Used by SMART-MONEY-SCAN mode.
    """
    url = f"{_BASE_URL}/latest-cluster-buys"
    trades = fetch_ticker.__wrapped__ if hasattr(fetch_ticker, "__wrapped__") \
             else _fetch_from_url(url)
    return [t for t in trades if t.amount_min and t.amount_min >= min_value]


def _fetch_from_url(url: str) -> list[Trade]:
    """Internal: fetch and parse any OpenInsider listing page."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=10)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", {"class": "tinytable"})
    if not table:
        return []

    trades = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 12:
            continue
        try:
            ticker      = _clean(cells[_COL_TICKER].get_text())
            filing_date = _clean(cells[_COL_FILING_DATE].get_text())
            trade_date  = _clean(cells[_COL_TRADE_DATE].get_text())
            insider     = _clean(cells[_COL_INSIDER].get_text())
            title       = _clean(cells[_COL_TITLE].get_text())
            trade_code  = _clean(cells[_COL_TRADE_TYPE].get_text())
            value_text  = _clean(cells[_COL_VALUE].get_text())

            action, is_10b5 = _parse_action(trade_code)
            if action == Action.UNKNOWN:
                continue

            amount = _parse_dollar(value_text)

            trades.append(Trade(
                tier=Tier.CORPORATE,
                insider_name=insider,
                title=title,
                ticker=ticker,
                action=action,
                amount_min=amount,
                amount_max=amount,
                trade_date=trade_date,
                filed_date=filing_date,
                source=f"OpenInsider — {url}",
                is_10b5_1=is_10b5,
                is_cross_company=False,
            ))
        except (IndexError, AttributeError):
            continue

    return trades
