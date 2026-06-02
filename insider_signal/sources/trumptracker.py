"""
Trump administration trades scraper — Tier 1 signals.

Covers executive branch officials required to file financial
disclosures under the Ethics in Government Act (EIGA):
  - President / Vice President
  - Cabinet secretaries
  - Senior White House staff
  - Agency heads

Source: https://www.trumptracker.org
OGE public disclosures: https://extapps2.oge.gov/201/Presiden.nsf

Tier 1 rationale: executive branch officials have access to
non-public policy decisions BEFORE markets react. A cabinet
secretary buying energy stocks before a pipeline approval is
categorically different from a CEO buying their own company.
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

_TRUMPTRACKER_URL = "https://www.trumptracker.org"
_TRADES_URL       = "https://www.trumptracker.org/trades"
_PERSON_URL       = "https://www.trumptracker.org/person/{slug}"

# Officials ranked by policy access level
# Higher rank = stronger signal weight (used in scoring.py)
_CABINET_RANK = {
    "donald trump":        10,
    "jd vance":            9,
    "marco rubio":         8,   # State
    "scott bessent":       8,   # Treasury
    "pete hegseth":        7,   # Defense
    "doug burgum":         7,   # Interior
    "chris wright":        7,   # Energy
    "howard lutnick":      7,   # Commerce
    "robert f. kennedy":   6,   # HHS
    "tulsi gabbard":       6,   # DNI
}


def _get_rank(name: str) -> int:
    """Return policy-access rank for a given official. Default 5 for unknown cabinet."""
    return _CABINET_RANK.get(name.lower().strip(), 5)


def _parse_action(raw: str) -> Action:
    raw = raw.strip().lower()
    if any(w in raw for w in ("purchase", "buy", "bought", "acquisition")):
        return Action.BUY
    if any(w in raw for w in ("sale", "sell", "sold", "disposition")):
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


def _parse_amount_range(raw: str) -> tuple[Optional[float], Optional[float]]:
    """Handle OGE-style range strings like '$1,001 - $15,000'."""
    if " - " in raw:
        parts = raw.split(" - ")
        lo = _parse_dollar(parts[0])
        hi = _parse_dollar(parts[1]) if len(parts) > 1 else None
        return lo, hi
    amount = _parse_dollar(raw)
    return amount, amount


def _safe_get(url: str, retries: int = 3, delay: float = 1.5) -> Optional[requests.Response]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=_HEADERS, timeout=12)
            r.raise_for_status()
            return r
        except requests.exceptions.HTTPError as e:
            if r.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning("TrumpTracker rate-limited — waiting %.1fs", wait)
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


def _parse_trades_table(html: str, ticker_filter: Optional[str] = None) -> list[Trade]:
    """
    TrumpTracker trades table columns:
      official | title | ticker | company | type | amount | trade date | filed date
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        logger.warning("TrumpTracker: no table found.")
        return []

    trades = []
    rows = table.find("tbody").find_all("tr") if table.find("tbody") else []

    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 7:
            continue
        try:
            official   = cells[0].get_text(strip=True)
            title      = cells[1].get_text(strip=True)
            ticker     = cells[2].get_text(strip=True).upper()
            trade_type = cells[4].get_text(strip=True)
            amount_raw = cells[5].get_text(strip=True)
            trade_date = cells[6].get_text(strip=True)
            filed_date = cells[7].get_text(strip=True) if len(cells) > 7 else trade_date

            if ticker_filter and ticker != ticker_filter.upper():
                continue

            if not ticker or ticker in ("--", "N/A", ""):
                continue

            action = _parse_action(trade_type)
            if action == Action.UNKNOWN:
                continue

            amount_min, amount_max = _parse_amount_range(amount_raw)
            rank = _get_rank(official)

            trades.append(Trade(
                tier=Tier.CABINET if rank >= 7 else Tier.TRUMP
                     if rank >= 9 else Tier.POLITICIAN,
                insider_name=official,
                title=title,
                ticker=ticker,
                action=action,
                amount_min=amount_min,
                amount_max=amount_max,
                trade_date=trade_date,
                filed_date=filed_date,
                source=f"TrumpTracker — {_TRADES_URL}",
                notes=f"Policy access rank: {rank}/10",
            ))
        except (IndexError, AttributeError) as e:
            logger.debug("Skipping malformed TrumpTracker row: %s", e)
            continue

    return trades


def fetch_all_trades(ticker: Optional[str] = None) -> list[Trade]:
    """
    Fetch all Trump administration trades.
    Optionally filter by ticker.
    """
    response = _safe_get(_TRADES_URL)
    if not response:
        logger.error("TrumpTracker fetch failed.")
        return []

    trades = _parse_trades_table(response.text, ticker_filter=ticker)
    logger.info("TrumpTracker: %d trades (filter: %s)", len(trades), ticker or "none")
    return trades


def fetch_person(name_slug: str) -> list[Trade]:
    """
    Fetch trades for a specific official by URL slug.
    Example: fetch_person('scott-bessent')
    """
    url = _PERSON_URL.format(slug=name_slug.lower().replace(" ", "-"))
    response = _safe_get(url)
    if not response:
        logger.error("TrumpTracker person fetch failed: %s", url)
        return []

    trades = _parse_trades_table(response.text)
    logger.info("TrumpTracker person '%s': %d trades", name_slug, len(trades))
    return trades


def fetch_cabinet_buys() -> list[Trade]:
    """
    Return only BUY trades from Tier 1 and Tier 2 officials.
    These are the highest-conviction signals in the entire tool.
    """
    all_trades = fetch_all_trades()
    cabinet_buys = [
        t for t in all_trades
        if t.action == Action.BUY
        and t.tier in (Tier.TRUMP, Tier.CABINET)
    ]
    logger.info("Cabinet buys: %d high-conviction signals", len(cabinet_buys))
    return cabinet_buys
