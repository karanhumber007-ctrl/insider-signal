"""Tests for Trade and TierReport data models."""

import pytest
from insider_signal.models import Trade, Tier, Action


def make_trade(**kwargs):
    defaults = dict(
        tier=Tier.CORPORATE,
        insider_name="John Smith",
        title="CEO",
        ticker="NVDA",
        action=Action.BUY,
        amount_min=500_000,
        amount_max=500_000,
        trade_date="2024-01-15",
        filed_date="2024-01-17",
        source="OpenInsider",
    )
    defaults.update(kwargs)
    return Trade(**defaults)


def test_trade_created_successfully():
    trade = make_trade()
    assert trade.ticker == "NVDA"
    assert trade.action == Action.BUY
    assert trade.tier == Tier.CORPORATE


def test_trade_is_major_above_threshold():
    trade = make_trade(amount_min=1_500_000)
    assert trade.is_major is True


def test_trade_is_not_major_below_threshold():
    trade = make_trade(amount_min=500_000)
    assert trade.is_major is False


def test_disclosure_lag_days_calculated():
    trade = make_trade(trade_date="2024-01-15", filed_date="2024-01-17")
    assert trade.disclosure_lag_days == 2


def test_disclosure_lag_zero_same_day():
    trade = make_trade(trade_date="2024-01-15", filed_date="2024-01-15")
    assert trade.disclosure_lag_days == 0


def test_all_tiers_exist():
    assert Tier.TRUMP.value == 1
    assert Tier.CABINET.value == 2
    assert Tier.POLITICIAN.value == 3
    assert Tier.CORPORATE.value == 4


def test_all_actions_exist():
    assert Action.BUY.value == "BUY"
    assert Action.SELL.value == "SELL"
    assert Action.UNKNOWN.value == "UNKNOWN"


def test_trade_default_not_10b5():
    trade = make_trade()
    assert trade.is_10b5_1 is False


def test_trade_10b5_flag():
    trade = make_trade(is_10b5_1=True)
    assert trade.is_10b5_1 is True


def test_trade_ticker_stored_correctly():
    trade = make_trade(ticker="AAPL")
    assert trade.ticker == "AAPL"
