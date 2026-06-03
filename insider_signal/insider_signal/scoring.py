"""
Signal scoring engine — converts raw trades into ranked signals.

Every trade that enters this engine exits with a numeric score.
Higher score = stronger conviction signal.

Scoring dimensions:
  1. Tier weight       — WHO is trading (cabinet > politician > CEO)
  2. Action weight     — BUY always scores higher than SELL
  3. Size weight       — larger dollar value = stronger signal
  4. Lag penalty       — slow disclosure = weaker signal
  5. 10b5-1 penalty    — pre-scheduled sales are noise, not signal
  6. Convergence bonus — same ticker appearing across multiple sources
"""

import logging
from dataclasses import dataclass
from typing import Optional

from insider_signal.models import Trade, Tier, Action

logger = logging.getLogger(__name__)


# ── Tier base weights ────────────────────────────────────────────────────────
# Cabinet and Trump trades carry 3x the base weight of corporate insiders.
# Rationale: policy access = asymmetric information advantage.
_TIER_WEIGHTS = {
    Tier.TRUMP:      10.0,
    Tier.CABINET:    8.0,
    Tier.POLITICIAN: 5.0,
    Tier.CORPORATE:  3.0,
}

# ── Action multipliers ───────────────────────────────────────────────────────
# Buys are voluntary and personal-capital-at-risk — strongest signal.
# Sells have too many legitimate reasons (divorce, house, tax planning).
_ACTION_WEIGHTS = {
    Action.BUY:     1.0,
    Action.SELL:    0.3,
    Action.UNKNOWN: 0.0,
}

# ── Size score brackets (USD) ────────────────────────────────────────────────
_SIZE_BRACKETS = [
    (50_000_000, 5.0),
    (10_000_000, 4.0),
    (5_000_000,  3.5),
    (1_000_000,  3.0),
    (500_000,    2.5),
    (250_000,    2.0),
    (100_000,    1.5),
    (50_000,     1.0),
    (15_000,     0.5),
    (0,          0.1),
]

# ── Disclosure lag penalty ───────────────────────────────────────────────────
# SEC requires Form 4 within 2 business days.
# STOCK Act requires 45 days.
# Longer lag = insider filed late = less credible or possibly stale.
_LAG_PENALTIES = [
    (180, -2.0),
    (90,  -1.5),
    (45,  -1.0),
    (10,  -0.5),
    (2,    0.0),
    (0,    0.5),   # Filed on time or early = small bonus
]


@dataclass
class ScoredTrade:
    trade: Trade
    score: float
    breakdown: dict   # Score component breakdown for transparency
    is_convergence: bool = False
    convergence_sources: list = None

    def __post_init__(self):
        if self.convergence_sources is None:
            self.convergence_sources = []

    @property
    def signal_label(self) -> str:
        if self.score >= 20:
            return "🔴 STRONG BUY"
        if self.score >= 12:
            return "🟠 MODERATE BUY"
        if self.score >= 6:
            return "🟡 WEAK SIGNAL"
        return "⚪ NOISE"


def _size_score(amount_min: Optional[float]) -> float:
    if not amount_min:
        return 0.1
    for threshold, score in _SIZE_BRACKETS:
        if amount_min >= threshold:
            return score
    return 0.1


def _lag_penalty(lag_days: Optional[int]) -> float:
    if lag_days is None:
        return 0.0
    for threshold, penalty in _LAG_PENALTIES:
        if lag_days >= threshold:
            return penalty
    return 0.0


def score_trade(trade: Trade) -> ScoredTrade:
    """
    Score a single trade across all dimensions.
    Returns a ScoredTrade with full breakdown.
    """
    tier_w   = _TIER_WEIGHTS.get(trade.tier, 1.0)
    action_w = _ACTION_WEIGHTS.get(trade.action, 0.0)
    size_s   = _size_score(trade.amount_min)
    lag_p    = _lag_penalty(trade.disclosure_lag_days)

    # 10b5-1 penalty — pre-scheduled sale, not a real signal
    plan_penalty = -3.0 if trade.is_10b5_1 else 0.0

    raw_score = (tier_w * action_w) + size_s + lag_p + plan_penalty
    final_score = max(0.0, round(raw_score, 2))

    breakdown = {
        "tier_weight":    tier_w,
        "action_weight":  action_w,
        "size_score":     size_s,
        "lag_penalty":    lag_p,
        "plan_penalty":   plan_penalty,
        "final":          final_score,
    }

    logger.debug(
        "Scored %s %s %s → %.2f",
        trade.insider_name, trade.action.value, trade.ticker, final_score
    )

    return ScoredTrade(trade=trade, score=final_score, breakdown=breakdown)


def score_all(trades: list[Trade]) -> list[ScoredTrade]:
    """Score a list of trades and return sorted highest-first."""
    scored = [score_trade(t) for t in trades]
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored


def apply_convergence(scored_trades: list[ScoredTrade]) -> list[ScoredTrade]:
    """
    Convergence bonus — if the same ticker appears across 2+ different
    sources, every trade on that ticker gets a +3.0 bonus.

    This is the most powerful signal in the tool:
    when a CEO buys AND a senator buys AND unusual options appear
    on the same ticker within the same window — that is institutional
    conviction, not coincidence.
    """
    # Group by ticker
    ticker_sources: dict[str, set] = {}
    for st in scored_trades:
        ticker = st.trade.ticker
        source = st.trade.source.split("—")[0].strip()
        if ticker not in ticker_sources:
            ticker_sources[ticker] = set()
        ticker_sources[ticker].add(source)

    # Apply bonus where convergence detected
    for st in scored_trades:
        ticker = st.trade.ticker
        sources = ticker_sources.get(ticker, set())
        if len(sources) >= 2:
            st.is_convergence = True
            st.convergence_sources = list(sources)
            st.score = round(st.score + 3.0, 2)
            st.breakdown["convergence_bonus"] = 3.0
            logger.info(
                "Convergence detected on %s across %d sources — +3.0 bonus",
                ticker, len(sources)
            )

    # Re-sort after convergence adjustment
    scored_trades.sort(key=lambda s: s.score, reverse=True)
    return scored_trades
