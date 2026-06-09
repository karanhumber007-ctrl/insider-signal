"""Tests for signal scoring engine."""

import pytest
from insider_signal.models import Trade, Tier, Action
from insider_signal.scoring import score_trade, score_all, apply_convergence


def make_trade(**kwargs):
    defaults = dict(
        tier=Tier.CORPORATE,
        insider_name="Jane Doe",
        title="CFO",
        ticker="AAPL",
        action=Action.BUY,
        amount_min=1_000_000,
        amount_max=1_000_000,
        trade_date="2024-01-15",
        filed_date="2024-01-17",
        source="OpenInsider",
    )
    defaults.update(kwargs)
    return Trade(**defaults)


def test_score_is_positive_for_buy():
    trade = make_trade(action=Action.BUY)
    result = score_trade(trade)
    assert result.score > 0


def test_sell_scores_lower_than_buy():
    buy = score_trade(make_trade(action=Action.BUY))
    sell = score_trade(make_trade(action=Action.SELL))
    assert buy.score > sell.score


def test_cabinet_scores_higher_than_corporate():
    cabinet = score_trade(make_trade(tier=Tier.CABINET))
    corporate = score_trade(make_trade(tier=Tier.CORPORATE))
    assert cabinet.score > corporate.score


def test_trump_scores_highest():
    trump = score_trade(make_trade(tier=Tier.TRUMP))
    cabinet = score_trade(make_trade(tier=Tier.CABINET))
    assert trump.score > cabinet.score


def test_10b5_penalty_applied():
    normal = score_trade(make_trade(is_10b5_1=False))
    planned = score_trade(make_trade(is_10b5_1=True))
    assert normal.score > planned.score


def test_large_trade_scores_higher():
    small = score_trade(make_trade(amount_min=10_000))
    large = score_trade(make_trade(amount_min=10_000_000))
    assert large.score > small.score


def test_score_breakdown_has_all_keys():
    result = score_trade(make_trade())
    assert "tier_weight" in result.breakdown
    assert "action_weight" in result.breakdown
    assert "size_score" in result.breakdown
    assert "lag_penalty" in result.breakdown
    assert "final" in result.breakdown


def test_score_all_returns_sorted():
    trades = [
        make_trade(tier=Tier.CORPORATE, amount_min=10_000),
        make_trade(tier=Tier.TRUMP, amount_min=5_000_000),
        make_trade(tier=Tier.CABINET, amount_min=1_000_000),
    ]
    results = score_all(trades)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_convergence_bonus_applied():
    trades = [
        make_trade(ticker="NVDA", source="OpenInsider — http://openinsider.com"),
        make_trade(ticker="NVDA", source="CapitalTrades — http://capitoltrades.com"),
    ]
    scored = score_all(trades)
    scored = apply_convergence(scored)
    assert any(s.is_convergence for s in scored)


def test_no_convergence_single_source():
    trades = [
        make_trade(ticker="AAPL", source="OpenInsider — http://openinsider.com"),
        make_trade(ticker="AAPL", source="OpenInsider — http://openinsider.com"),
    ]
    scored = score_all(trades)
    scored = apply_convergence(scored)
    assert not any(s.is_convergence for s in scored)
