"""
tests/test_guardrails.py — Unit tests for hard-limit guardrails.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from agent.debate.base import AgentProposal
from agent.market.snapshot import PortfolioSnapshot
from agent.mcp_client.tools import AccountInfo, Position
from agent.options.structures import HOLD_STRUCTURE, OptionStructure, OptionLeg
from agent.risk import guardrails


def _make_snapshot(
    equity: float = 100_000.0,
    option_market_value: float = 0.0,
    n_positions: int = 0,
) -> PortfolioSnapshot:
    account = AccountInfo(
        equity=equity, cash=equity * 0.5,
        buying_power=equity, portfolio_value=equity, day_pl=0.0,
    )
    positions = []
    if n_positions > 0:
        for i in range(n_positions):
            positions.append(Position(
                symbol=f"SYM{i}", qty=1.0, side="long",
                market_value=1000.0, unrealized_pl=0.0,
                avg_entry_price=100.0, asset_class="us_equity",
            ))
    if option_market_value > 0:
        positions.append(Position(
            symbol="OPT0", qty=1.0, side="long",
            market_value=option_market_value, unrealized_pl=0.0,
            avg_entry_price=option_market_value, asset_class="us_option",
        ))
    return PortfolioSnapshot(account=account, positions=positions, snapshots={})


def _make_proposal(net_debit: float = 2.50) -> AgentProposal:
    leg = OptionLeg(
        symbol="SPY260920P00520000", underlying="SPY",
        side="buy", option_type="put",
        strike=520.0, expiration="2026-09-20",
        bid=net_debit - 0.05, ask=net_debit + 0.05, mid=net_debit,
        delta=-0.30, qty=1,
    )
    structure = OptionStructure(
        name="protective_put", underlying="SPY", legs=[leg],
        net_debit=net_debit, net_delta=-0.30,
        max_loss=net_debit * 100, max_gain=52000.0, rationale="test",
    )
    return AgentProposal(
        agent_name="bear", bias="bear", structure=structure,
        contracts=1, confidence=0.7, reasoning="test",
        expected_pnl=100.0, max_loss=250.0,
    )


class TestGuardrails:
    def setup_method(self):
        """Reset circuit breaker state before each test."""
        guardrails._is_halted = False
        guardrails._halt_reason = ""
        guardrails._session_start_equity = 0.0
        guardrails._session_high_equity = 0.0

    def test_circuit_breaker_blocks(self):
        guardrails.halt("test halt")
        snapshot = _make_snapshot()
        proposal = _make_proposal()
        result = guardrails.check_all(snapshot, proposal)
        assert not result.ok
        assert "Circuit breaker" in result.reason

    def test_daily_loss_triggers_halt(self):
        guardrails.set_session_start_equity(100_000.0)
        # Equity dropped 4% → above 3% daily loss limit
        snapshot = _make_snapshot(equity=96_000.0)
        proposal = _make_proposal()
        result = guardrails._check_daily_loss(96_000.0)
        assert not result.ok
        assert guardrails.is_halted()

    def test_no_loss_passes(self):
        guardrails.set_session_start_equity(100_000.0)
        result = guardrails._check_daily_loss(100_500.0)
        assert result.ok

    def test_max_positions_blocks(self):
        snapshot = _make_snapshot(n_positions=20)  # at limit
        proposal = _make_proposal()
        result = guardrails._check_position_count(snapshot)
        assert not result.ok

    def test_option_exposure_blocks(self):
        # Already at 9% option exposure; trying to add 2% → would exceed 10%
        snapshot = _make_snapshot(equity=100_000.0, option_market_value=9_000.0)
        # Proposal costs $2,500 (1 contract × $2.50 × 100) → total = $11,500 > $10,000
        proposal = _make_proposal(net_debit=25.0)  # $25 × 100 = $2,500
        result = guardrails._check_option_exposure(snapshot, proposal)
        assert not result.ok

    def test_wide_spread_blocks(self):
        proposal = _make_proposal()
        # Manually widen the spread beyond limit
        proposal.structure.legs[0].bid = 1.00
        proposal.structure.legs[0].ask = 2.50  # $1.50 spread > $0.15 limit
        proposal.structure.legs[0].mid = 1.75
        result = guardrails._check_min_liquidity(proposal)
        assert not result.ok

    def test_clean_proposal_passes_all(self):
        guardrails.set_session_start_equity(100_000.0)
        snapshot = _make_snapshot(equity=100_000.0, n_positions=3)
        proposal = _make_proposal(net_debit=2.50)  # tight spread: bid=2.45, ask=2.55
        # Market hours check will fail outside hours; patch it to pass
        with patch.object(guardrails, "_check_market_hours",
                          return_value=guardrails.GuardrailResult(ok=True, reason="")):
            result = guardrails.check_all(snapshot, proposal)
        assert result.ok
