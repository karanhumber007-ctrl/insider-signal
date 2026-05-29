"""
Congressional trades scraper — STOCK Act disclosures.

Covers all U.S. House and Senate members required to disclose
trades within 45 days under the Stop Trading on Congressional
Knowledge Act (STOCK Act).

Primary sources:
  - House: https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json
  - Senate: https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json
  - CapitalTrades (freshest data): https://www.capitoltrades.com/trades
"""

import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from insider_signal.models import Trade, Tier, Action

logger = logging.getLogger(__name__)

# ── Public S3 feeds (no auth required) ──────────────────────────────────────
_HOUSE_JSON_URL = (
    "https://house-stock-watcher-data.s3-us-west-2.amazonaws.com"
    "/data/all_transactions.json"
)
_SENATE_JSON_URL = (
    "https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com"
    "/aggregate/all_transactions.json"
)

# ── CapitalTrades (HTML, freshest) ───────────────────────────────────────────
_CT_BASE = "https://www.capitoltrades.com"
_CT_TICKER_URL = "https://www.capitoltrades.com/trades?ticker={ticker}&pageSize=100"
_CT_SCAN_URL   = "https://www.capitoltrades.com/trades?pageSize=100"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# STOCK Act amount range midpoints (USD) — used for signal scoring
# Ranges are legally mandated — never fabricate exact values
_AMOUNT_RANGES = {
    "$1,001 - $15,000":      (1_001,    15_000),
    "$15,001 - $50,000":     (15_001,   50_000),
    "$50,001 - $100,000":    (50_001,  100_000),
    "$100,001 - $250,000":   (100_001, 250_000),
    "$250,001 - $500,000":   (250_001, 500_000),
    "$500,001 - $1,000,000": (500_001, 1_000_000),
    "$1,000,001 - $5,000,000":   (1_000_001, 5_000_000),
    "$5,000,001 - $25,000,000":  (5_000_001, 25_000_000),
    "$25,000,001 - $50,000,000": (25_000_001, 50_000_000),
    "Over $50,000,000":          (50_000_001, None),
}


def _parse_amount_range(raw: str) -> tuple[Optional[float], Optional[float]]:
    """
    Convert STOCK Act range string to (min, max) floats.
    Returns (None, None) if unrecognised — never guesses.
    """
    raw = raw.strip()
    for label, (lo, hi) in _AMOUNT_RANGES.items():
        if label.lower() in raw.lower():
            return float(lo), float(hi) if hi else None
    return None, None


def _parse_action(raw: str) -> Action:
    raw = raw.strip().lower()
    if any(w in raw for w in ("purchase", "buy", "bought")):
        return Action.BUY
    if any(w in raw for w in ("sale", "sell", "sold")):
        return Action.SELL
    return Action.UNKNOWN


def _safe_get(url: str, retries: int = 3, delay: float = 1.5) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=12)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning("Rate-limited by %s — waiting %.1fs", url, wait)
                time.sleep(wait)
            else:
                logger.error("HTTP %s fetching %s: %s", r.status_code, url, e)
                return None
        except requests.exceptions.RequestException as e:
            logger.error("Network error fetching %s: %s", url, e)
            if attempt == retries:
                return None
            time.sleep(delay)
    return None


# ── House JSON feed ──────────────────────────────────────────────────────────

def _parse_house_record(record: dict, ticker_filter: Optional[str] = None) -> Optional[Trade]:
    """
    House JSON schema fields used:
      representative, district, transaction_date, disclosure_date,
      ticker, asset_description, type, amount
    """
    try:
        ticker = record.get("ticker", "").strip().upper()
        if ticker_filter and ticker != ticker_filter.upper():
            return None
        if not ticker or ticker in ("--", "N/A", ""):
            return None

        trade_type = record.get("type", "")
        action = _parse_action(trade_type)
        if action == Action.UNKNOWN:
            return None

        amount_raw = record.get("amount", "")
        amount_min, amount_max = _parse_amount_range(amount_raw)

        representative = record.get("representative", "Unknown").strip()
        # Normalise "Lastname, Firstname" → "Firstname Lastname"
        if "," in representative:
            parts = representative.split(",", 1)
            representative = f"{parts[1].strip()} {parts[0].strip()}"

        return Trade(
            tier=Tier.POLITICIAN,
            insider_name=representative,
            title=f"House — {record.get('district', '')}",
            ticker=ticker,
            action=action,
            amount_min=amount_min,
            amount_max=amount_max,
            trade_date=record.get("transaction_date", ""),
            filed_date=record.get("disclosure_date", ""),
            source=f"House STOCK Act disclosure — {_HOUSE_JSON_URL}",
        )
    except (KeyError, AttributeError, TypeError) as e:
        logger.debug("Skipping malformed House record: %s", e)
        return None


def fetch_house_trades(ticker: Optional[str] = None) -> list[Trade]:
    """
    Fetch all House STOCK Act trades, optionally filtered to a ticker.
    Parses the public S3 JSON feed directly.
    """
    response = _safe_get(_HOUSE_JSON_URL)
    if not response:
        logger.error("Failed to fetch House JSON feed.")
        return []

    try:
        records = response.json()
    except ValueError as e:
        logger.error("House JSON parse error: %s", e)
        return []

    trades = []
    for record in records:
        trade = _parse_house_record(record, ticker_filter=ticker)
        if trade:
            trades.append(trade)

    logger.info("House feed: %d trades parsed (filter: %s)", len(trades), ticker or "none")
    return trades


# ── Senate JSON feed ─────────────────────────────────────────────────────────

def _parse_senate_record(record: dict, ticker_filter: Optional[str] = None) -> Optional[Trade]:
    """
    Senate JSON schema fields used:
      first_name, last_name, office, transaction_date, date_received,
      ticker, asset_description, type, amount
    """
    try:
        ticker = record.get("ticker", "").strip().upper()
        if ticker_filter and ticker != ticker_filter.upper():
            return None
        if not ticker or ticker in ("--", "N/A", ""):
            return None

        action = _parse_action(record.get("type", ""))
        if action == Action.UNKNOWN:
            return None

        amount_min, amount_max = _parse_amount_range(record.get("amount", ""))

        first = record.get("first_name", "").strip()
        last  = record.get("last_name", "").strip()
        name  = f"{first} {last}".strip() or "Unknown"
        office = record.get("office", "Senate")

        return Trade(
            tier=Tier.POLITICIAN,
            insider_name=name,
            title=f"Senate — {office}",
            ticker=ticker,
            action=action,
            amount_min=amount_min,
            amount_max=amount_max,
            trade_date=record.get("transaction_date", ""),
            filed_date=record.get("date_received", ""),
            source=f"Senate STOCK Act disclosure — {_SENATE_JSON_URL}",
        )
    except (KeyError, AttributeError, TypeError) as e:
        logger.debug("Skipping malformed Senate record: %s", e)
        return None


def fetch_senate_trades(ticker: Optional[str] = None) -> list[Trade]:
    """
    Fetch all Senate STOCK Act trades, optionally filtered to a ticker.
    """
    response = _safe_get(_SENATE_JSON_URL)
    if not response:
        logger.error("Failed to fetch Senate JSON feed.")
        return []

    try:
        records = response.json()
    except ValueError as e:
        logger.error("Senate JSON parse error: %s", e)
        return []

    trades = []
    for record in records:
        trade = _parse_senate_record(record, ticker_filter=ticker)
        if trade:
            trades.append(trade)

    logger.info("Senate feed: %d trades parsed (filter: %s)", len(trades), ticker or "none")
    return trades


# ── CapitalTrades HTML scraper (freshest data) ───────────────────────────────

def _parse_capitoltrades_html(html: str, ticker_filter: Optional[str] = None) -> list[Trade]:
    """
    CapitalTrades renders a table with columns:
      politician | party | chamber | ticker | company | trade date |
      filed date | type | amount
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("CapitalTrades: no table found in response.")
        return []

    trades = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 8:
            continue
        try:
            politician  = cells[0].get_text(strip=True)
            party       = cells[1].get_text(strip=True)
            chamber     = cells[2].get_text(strip=True)
            ticker      = cells[3].get_text(strip=True).upper()
            trade_date  = cells[5].get_text(strip=True)
            filed_date  = cells[6].get_text(strip=True)
            trade_type  = cells[7].get_text(strip=True)
            amount_raw  = cells[8].get_text(strip=True) if len(cells) > 8 else ""

            if ticker_filter and ticker != ticker_filter.upper():
                continue

            action = _parse_action(trade_type)
            if action == Action.UNKNOWN:
                continue

            amount_min, amount_max = _parse_amount_range(amount_raw)

            trades.append(Trade(
                tier=Tier.POLITICIAN,
                insider_name=politician,
                title=f"{chamber} — {party}",
                ticker=ticker,
                action=action,
                amount_min=amount_min,
                amount_max=amount_max,
                trade_date=trade_date,
                filed_date=filed_date,
                source=f"CapitalTrades — {_CT_BASE}",
            ))
        except (IndexError, AttributeError) as e:
            logger.debug("Skipping malformed CT row: %s", e)
            continue

    return trades


def fetch_capitoltrades_ticker(ticker: str) -> list[Trade]:
    """Fetch CapitalTrades disclosures for a specific ticker."""
    url = _CT_TICKER_URL.format(ticker=ticker.upper())
    response = _safe_get(url)
    if not response:
        logger.error("CapitalTrades fetch failed for ticker %s", ticker)
        return []
    trades = _parse_capitoltrades_html(response.text, ticker_filter=ticker)
    logger.info("CapitalTrades: %d trades for %s", len(trades), ticker)
    return trades


def fetch_capitoltrades_latest() -> list[Trade]:
    """Fetch the latest 100 trades across all politicians from CapitalTrades."""
    response = _safe_get(_CT_SCAN_URL)
    if not response:
        logger.error("CapitalTrades latest feed failed.")
        return []
    trades = _parse_capitoltrades_html(response.text)
    logger.info("CapitalTrades latest: %d trades parsed", len(trades))
    return trades


# ── Unified congressional fetch ──────────────────────────────────────────────

def fetch_congressional_trades(ticker: Optional[str] = None) -> list[Trade]:
    """
    Master function. Pulls from all three congressional sources and
    returns a deduplicated combined list.

    Deduplication at this layer is key-based (name + ticker + date + action).
    Full cross-source deduplication happens in dedup.py.
    """
    house  = fetch_house_trades(ticker)
    senate = fetch_senate_trades(ticker)
    ct     = fetch_capitoltrades_ticker(ticker) if ticker else fetch_capitoltrades_latest()

    all_trades = house + senate + ct

    # Light dedup: remove exact key matches within this source set
    seen: set[tuple] = set()
    deduped: list[Trade] = []
    for t in all_trades:
        key = (t.insider_name.lower(), t.ticker, t.trade_date, t.action)
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    dropped = len(all_trades) - len(deduped)
    if dropped:
        logger.info("Congressional dedup: removed %d duplicate trades", dropped)

    logger.info(
        "Congressional total: %d trades | House: %d | Senate: %d | CT: %d",
        len(deduped), len(house), len(senate), len(ct)
    )
    return deduped
