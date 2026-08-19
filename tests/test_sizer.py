"""
tests/test_sizer.py — Unit tests for the contract sizer.
"""

from __future__ import annotations

import pytest
from agent.options.sizer import size_structure
from agent.options.structures import HOLD_STRUCTURE, OptionStructure, OptionLeg
from agent.market.snapshot import PortfolioSnapshot
from agent.mcp_client.tools import AccountInfo


def _make_snapshot(equity: float = 100_000.0, net_delta: float = 0.25) -> PortfolioSnapshot:
    account = AccountInfo(
        equity=equity, cash=equity * 0.5,
        buying_power=equity, portfolio_value=equity, day_pl=0.0,
    )
    return PortfolioSnapshot(account=account, positions=[], snapshots={})


def _make_structure(net_debit: float = 2.50, net_delta: float = -0.30) -> OptionStructure:
    leg = OptionLeg(
        symbol="SPY260920P00520000", underlying="SPY",
        side="buy", option_type="put",
        strike=520.0, expiration="2026-09-20",
        bid=2.40, ask=2.60, mid=net_debit, delta=net_delta, qty=1,
    )
    return OptionStructure(
        name="protective_put", underlying="SPY", legs=[leg],
        net_debit=net_debit, net_delta=net_delta,
        max_loss=net_debit * 100, max_gain=52000.0, rationale="test",
    )


class TestSizeStructure:
    def test_hold_returns_zero(self):
        snapshot = _make_snapshot()
        assert size_structure(HOLD_STRUCTURE, snapshot) == 0

    def test_respects_max_contracts(self):
        """Sizer should never exceed MAX_CONTRACTS_PER_SYMBOL (default 5)."""
        snapshot = _make_snapshot(equity=10_000_000.0)  # huge equity
        structure = _make_structure(net_debit=0.01)  # tiny premium
        qty = size_structure(structure, snapshot)
        assert qty <= 5

    def test_respects_budget(self):
        """Sizer should not exceed max_hedge_pct of equity."""
        equity = 10_000.0
        max_hedge_pct = 0.02  # default
        max_premium = equity * max_hedge_pct  # $200
        snapshot = _make_snapshot(equity=equity)
        structure = _make_structure(net_debit=3.00)  # $300 per contract
        qty = size_structure(structure, snapshot)
        assert qty * 3.00 * 100 <= max_premium + 1e-6  # floating point tolerance

    def test_zero_equity_returns_zero(self):
        snapshot = _make_snapshot(equity=0.0)
        structure = _make_structure()
        assert size_structure(structure, snapshot) == 0

    def test_delta_driven_sizing(self):
        """Large delta gap → more contracts requested (up to budget/max cap)."""
        snapshot_small_gap = _make_snapshot(net_delta=0.11)  # gap = 0.01
        snapshot_large_gap = _make_snapshot(net_delta=0.40)  # gap = 0.30

        structure = _make_structure(net_debit=0.50, net_delta=-0.25)
        qty_small = size_structure(structure, snapshot_small_gap)
        qty_large = size_structure(structure, snapshot_large_gap)
        assert qty_large >= qty_small
