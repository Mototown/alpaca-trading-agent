"""
tests/test_arbiter.py — Unit tests for the Risk Arbiter scoring logic.

Uses stub proposals so no network calls are needed.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agent.debate.arbiter import arbitrate, score_proposal, MIN_SCORE_THRESHOLD
from agent.debate.base import AgentProposal
from agent.market.snapshot import PortfolioSnapshot
from agent.mcp_client.tools import AccountInfo, Position
from agent.options.structures import HOLD_STRUCTURE, OptionStructure, OptionLeg


def _make_snapshot(equity: float = 100_000.0, net_delta: float = 0.20) -> PortfolioSnapshot:
    account = AccountInfo(
        equity=equity, cash=equity * 0.5, buying_power=equity,
        portfolio_value=equity, day_pl=0.0,
    )
    snap = PortfolioSnapshot(account=account, positions=[], snapshots={})
    # Patch net_portfolio_delta via a Position whose market_value drives the delta calc
    # Simplest approach: inject a long equity position sized so delta ≈ net_delta * equity
    if net_delta > 0:
        from agent.mcp_client.tools import MarketSnapshot as MS
        fake_sym = "TEST"
        snap.snapshots[fake_sym] = MS(
            symbol=fake_sym, last_price=100.0, bid=99.9, ask=100.1, volume=1_000_000
        )
        snap.positions.append(
            Position(
                symbol=fake_sym,
                qty=net_delta * equity / 100.0,  # qty × price = net_delta × equity
                side="long",
                market_value=net_delta * equity,
                unrealized_pl=0.0,
                avg_entry_price=100.0,
                asset_class="us_equity",
            )
        )
    return snap


def _make_put_structure(net_debit: float = 2.50, net_delta: float = -0.30) -> OptionStructure:
    leg = OptionLeg(
        symbol="SPY260920P00520000", underlying="SPY",
        side="buy", option_type="put",
        strike=520.0, expiration="2026-09-20",
        bid=2.40, ask=2.60, mid=2.50, delta=-0.30, qty=1,
    )
    return OptionStructure(
        name="protective_put",
        underlying="SPY",
        legs=[leg],
        net_debit=net_debit,
        net_delta=net_delta,
        max_loss=net_debit * 100,
        max_gain=52000.0,
        rationale="Test protective put.",
    )


def _make_proposal(
    agent_name: str = "bear",
    structure: OptionStructure | None = None,
    contracts: int = 1,
    confidence: float = 0.7,
    expected_pnl: float = 100.0,
    max_loss: float = 250.0,
) -> AgentProposal:
    return AgentProposal(
        agent_name=agent_name,
        bias=agent_name,
        structure=structure or _make_put_structure(),
        contracts=contracts,
        confidence=confidence,
        reasoning=f"Test proposal from {agent_name}.",
        expected_pnl=expected_pnl,
        max_loss=max_loss,
    )


class TestScoreProposal:
    def test_hold_proposal_score(self):
        """HOLD proposal should score 0."""
        hold_proposal = _make_proposal(structure=HOLD_STRUCTURE, contracts=0, expected_pnl=0, max_loss=0)
        snapshot = _make_snapshot(net_delta=0.0)
        scored = score_proposal(hold_proposal, snapshot)
        assert scored.score == 0.0

    def test_better_expected_pnl_scores_higher(self):
        """Proposal with higher expected P&L should outscore lower one."""
        snapshot = _make_snapshot()
        p_high = _make_proposal(expected_pnl=500.0)
        p_low = _make_proposal(expected_pnl=50.0)
        s_high = score_proposal(p_high, snapshot)
        s_low = score_proposal(p_low, snapshot)
        assert s_high.score > s_low.score

    def test_delta_violation_penalised(self):
        """Proposal that pushes delta over max should be penalised."""
        snapshot = _make_snapshot(net_delta=0.25)
        # Structure adds +0.30 delta → post-trade = 0.55 > max 0.30 → penalised
        structure_bad = _make_put_structure(net_delta=0.30)
        p_violates = _make_proposal(structure=structure_bad, expected_pnl=300.0)
        # Structure reduces delta by -0.30 → post-trade = -0.05, within bounds → no penalty
        p_safe = _make_proposal(expected_pnl=300.0)  # net_delta=-0.30

        s_violates = score_proposal(p_violates, snapshot)
        s_safe = score_proposal(p_safe, snapshot)
        assert s_safe.score > s_violates.score

    def test_score_breakdown_keys_present(self):
        snapshot = _make_snapshot()
        p = _make_proposal()
        scored = score_proposal(p, snapshot)
        assert "expected_pnl" in scored.score_breakdown
        assert "max_loss_penalty" in scored.score_breakdown
        assert "cost_penalty" in scored.score_breakdown
        assert "final" in scored.score_breakdown


class TestArbitrate:
    def test_returns_hold_when_no_proposals(self):
        snapshot = _make_snapshot()
        decision = arbitrate([], snapshot)
        assert decision.winner.structure.name == "hold"

    def test_returns_hold_when_all_below_threshold(self):
        """All proposals with very negative expected P&L → HOLD wins."""
        snapshot = _make_snapshot()
        bad_proposals = [
            _make_proposal("bull", expected_pnl=-10_000.0, max_loss=50_000.0),
            _make_proposal("bear", expected_pnl=-8_000.0, max_loss=40_000.0),
        ]
        decision = arbitrate(bad_proposals, snapshot)
        assert decision.winner.structure.name == "hold"

    def test_best_proposal_wins(self):
        snapshot = _make_snapshot()
        p_bear = _make_proposal("bear", expected_pnl=400.0, confidence=0.8)
        p_bull = _make_proposal("bull", expected_pnl=100.0, confidence=0.5,
                                structure=_make_put_structure(net_debit=1.0))
        decision = arbitrate([p_bear, p_bull], snapshot)
        # Bear has much better expected P&L and confidence
        assert decision.winner.agent_name == "bear"

    def test_all_scored_sorted_descending(self):
        snapshot = _make_snapshot()
        proposals = [
            _make_proposal("bull", expected_pnl=50.0),
            _make_proposal("bear", expected_pnl=200.0),
            _make_proposal("neutral", expected_pnl=120.0),
        ]
        decision = arbitrate(proposals, snapshot)
        scores = [s.score for s in decision.all_scored]
        assert scores == sorted(scores, reverse=True)
