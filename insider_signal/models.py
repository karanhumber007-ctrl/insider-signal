from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Tier(Enum):
    TRUMP = 1
    CABINET = 2
    POLITICIAN = 3
    CORPORATE = 4


class Action(Enum):
    BUY = "BUY"
    SELL = "SELL"
    UNKNOWN = "UNKNOWN"


@dataclass
class Trade:
    tier: Tier
    insider_name: str
    title: str
    ticker: str
    action: Action
    amount_min: Optional[float]
    amount_max: Optional[float]
    trade_date: str
    filed_date: str
    source: str
    is_10b5_1: bool = False
    is_cross_company: bool = False
    notes: str = ""

    @property
    def is_major(self) -> bool:
        """Flag trades over $1 million."""
        if self.amount_min and self.amount_min >= 1_000_000:
            return True
        return False

    @property
    def disclosure_lag_days(self) -> Optional[int]:
        """Days between trade date and filing date."""
        try:
            from datetime import datetime
            fmt = "%Y-%m-%d"
            delta = datetime.strptime(self.filed_date, fmt) - \
                    datetime.strptime(self.trade_date, fmt)
            return delta.days
        except Exception:
            return None


@dataclass
class TierReport:
    tier: Tier
    trades: list
    source_urls: list
    fetch_failed: bool = False
    error_message: str = ""
