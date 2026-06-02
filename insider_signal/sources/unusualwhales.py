"""
UnusualWhales scraper — options flow + insider trade cross-reference.

Tracks unusual options activity correlated with insider disclosures.
When both an insider buy AND unusual call options appear on the same
ticker within 72 hours, signal strength multiplies — this is the
convergence signal that institutional desks watch.

Source: https://unusualwhales.com
"""

import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from insider_signal.models import Trade, Tier, Action

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_BASE_URL       = "https://unusualwhales.com"
_CONGRESS_URL   = "https://unusualwhales.com/political/congress"
_TICKER_URL     = "https://unusualwhales.com/stock/{ticker}/insider"
_LATEST_URL     = "https://unusualwhales.com/insider-trades"


def _safe_get(url: str, retries: int = 3, delay: float = 1.5) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=12)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning("UnusualWhales rate-limited — waiting %.1fs", wait)
                time.sleep(wait)
            else:
                logger.error("HTTP %s for %s: %s", r.status_code, url, e)
                return None
        except requests.exceptions.RequestException as e:
            logger.error("Network error %s: %s", url, e)
            if attempt == retries:
                return None
            time.sleep(delay)
    return None


def _parse_action(raw: str) -> Action:
    raw = raw.strip().lower()
    if any(w in raw for w in ("buy", "purchase", "call")):
        return Action.BUY
    if any(w in raw for w in ("sell", "sale", "put")):
        return Action.SELL
    return Action.UNKNOWN


def _parse_dollar(text: str) -> Optional[float]:
    try:
        text = text.replace("$", "").replace(",", "").strip()
        if text.endswith("M"):
            return float(text[:-1]) * 1_000_000
        if text.endswith("K"):
            return float(text[:-1]) * 1_000
        if text.endswith("B"):
            return float(text[:-1]) * 1_000_000_000
        return float(text) if text else None
    except (ValueError, AttributeError):
        return None


def _parse_table(html: str, source_url: str, ticker_filter: Optional[str] = None) -> list[Trade]:
    """
    UnusualWhales insider table columns:
      ticker | insider | title | trade type | value | trade date | filed date
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("UnusualWhales: no table found at %s", source_url)
        return []

    trades = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        try:
            ticker     = cells[0].get_text(strip=True).upper()
            insider    = cells[1].get_text(strip=True)
            title      = cells[2].get_text(strip=True)
            trade_type = cells[3].get_text(strip=True)
            value_raw  = cells[4].get_text(strip=True)
            trade_date = cells[5].get_text(strip=True)
            filed_date = cells[6].get_text(strip=True) if len(cells) > 6 else trade_date

            if ticker_filter and ticker != ticker_filter.upper():
                continue

            action = _parse_action(trade_type)
            if action == Action.UNKNOWN:
                continue

            amount = _parse_dollar(value_raw)

            trades.append(Trade(
                tier=Tier.CORPORATE,
                insider_name=insider,
                title=title,
                ticker=ticker,
                action=action,
                amount_min=amount,
                amount_max=amount,
                trade_date=trade_date,
                filed_date=filed_date,
                source=f"UnusualWhales — {source_url}",
            ))
        except (IndexError, AttributeError) as e:
            logger.debug("Skipping malformed UW row: %s", e)
            continue

    return trades


def fetch_ticker(ticker: str) -> list[Trade]:
    """Fetch UnusualWhales insider trades for a specific ticker."""
    url = _TICKER_URL.format(ticker=ticker.upper())
    response = _safe_get(url)
    if not response:
        return []
    trades = _parse_table(response.text, url, ticker_filter=ticker)
    logger.info("UnusualWhales: %d trades for %s", len(trades), ticker)
    return trades


def fetch_latest() -> list[Trade]:
    """Fetch latest insider trades across all tickers from UnusualWhales."""
    response = _safe_get(_LATEST_URL)
    if not response:
        return []
    trades = _parse_table(response.text, _LATEST_URL)
    logger.info("UnusualWhales latest: %d trades", len(trades))
    return trades


def fetch_congress() -> list[Trade]:
    """
    Fetch congressional trades tracked by UnusualWhales.
    This is a secondary congressional source — cross-referenced
    against capitoltrades.py for convergence detection.
    """
    response = _safe_get(_CONGRESS_URL)
    if not response:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("UnusualWhales congress: no table found")
        return []

    trades = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        try:
            politician = cells[0].get_text(strip=True)
            chamber    = cells[1].get_text(strip=True)
            ticker     = cells[2].get_text(strip=True).upper()
            trade_type = cells[3].get_text(strip=True)
            amount_raw = cells[4].get_text(strip=True)
            trade_date = cells[5].get_text(strip=True)
            filed_date = cells[6].get_text(strip=True) if len(cells) > 6 else trade_date

            action = _parse_action(trade_type)
            if action == Action.UNKNOWN:
                continue

            amount = _parse_dollar(amount_raw)

            trades.append(Trade(
                tier=Tier.POLITICIAN,
                insider_name=politician,
                title=chamber,
                ticker=ticker,
                action=action,
                amount_min=amount,
                amount_max=amount,
                trade_date=trade_date,
                filed_date=filed_date,
                source=f"UnusualWhales Congress — {_CONGRESS_URL}",
            ))
        except (IndexError, AttributeError) as e:
            logger.debug("Skipping malformed UW congress row: %s", e)
            continue

    logger.info("UnusualWhales congress: %d trades", len(trades))
    return trades
