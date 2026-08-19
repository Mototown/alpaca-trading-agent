"""
debate/arbiter.py — Risk Arbiter: scores all proposals and picks the winner.

Scoring formula:
    score = expected_pnl
            - max_loss_penalty * max_loss
            - cost_penalty * total_cost
            - delta_violation_penalty

The winning proposal must beat MIN_SCORE_THRESHOLD; otherwise HOLD wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.config import settings
from agent.debate.base import AgentProposal
from agent.market.snapshot import PortfolioSnapshot
from agent.options.structures import HOLD_STRUCTURE, OptionStructure

logger = logging.getLogger(__name__)

# Tuning knobs
MAX_LOSS_PENALTY_FACTOR = 0.30    # penalise 30 cents per dollar of max loss
COST_PENALTY_FACTOR = 0.50        # penalise 50 cents per dollar of premium spent
DELTA_VIOLATION_PENALTY = 500.0   # flat penalty if post-trade delta exceeds limit
MIN_SCORE_THRESHOLD = -50.0       # score must beat this or we HOLD


@dataclass
class ScoredProposal:
    proposal: AgentProposal
    score: float
    score_breakdown: dict[str, float]


@dataclass
class ArbiterDecision:
    winner: AgentProposal
    all_scored: list[ScoredProposal]
    reasoning: str


def score_proposal(
    proposal: AgentProposal,
    snapshot: PortfolioSnapshot,
) -> ScoredProposal:
    """Score a single proposal against risk parameters."""
    structure = proposal.structure
    qty = proposal.contracts

    # Base: expected P&L
    score = proposal.expected_pnl

    # Penalty: max loss exposure
    max_loss_penalty = structure.max_loss * qty * MAX_LOSS_PENALTY_FACTOR
    score -= max_loss_penalty

    # Penalty: premium cost relative to portfolio
    equity = snapshot.account.equity or 1.0
    total_cost = abs(structure.total_cost) * qty
    cost_pct = total_cost / equity
    cost_penalty = total_cost * COST_PENALTY_FACTOR
    score -= cost_penalty

    # Penalty: would trade push portfolio delta out of bounds?
    current_delta = snapshot.net_portfolio_delta
    post_delta = current_delta + structure.net_delta * qty
    delta_penalty = 0.0
    if abs(post_delta) > settings.max_delta:
        delta_penalty = DELTA_VIOLATION_PENALTY
        score -= delta_penalty

    # Bonus: confidence factor
    score *= (0.5 + proposal.confidence * 0.5)

    breakdown = {
        "expected_pnl": proposal.expected_pnl,
        "max_loss_penalty": -max_loss_penalty,
        "cost_pct": cost_pct,
        "cost_penalty": -cost_penalty,
        "delta_penalty": -delta_penalty,
        "confidence_factor": proposal.confidence,
        "final": score,
    }

    logger.debug(
        "arbiter score [%s]: %.2f (pnl=%.2f loss_pen=%.2f cost_pen=%.2f delta_pen=%.2f)",
        proposal.agent_name, score, proposal.expected_pnl,
        max_loss_penalty, cost_penalty, delta_penalty,
    )

    return ScoredProposal(proposal=proposal, score=score, score_breakdown=breakdown)


def arbitrate(
    proposals: list[AgentProposal],
    snapshot: PortfolioSnapshot,
) -> ArbiterDecision:
    """
    Score all proposals, pick the winner.

    If the best score is below MIN_SCORE_THRESHOLD, the HOLD proposal wins.
    """
    if not proposals:
        hold = AgentProposal(
            agent_name="arbiter",
            bias="neutral",
            structure=HOLD_STRUCTURE,
            contracts=0,
            confidence=1.0,
            reasoning="No proposals received — defaulting to HOLD.",
            expected_pnl=0.0,
            max_loss=0.0,
        )
        return ArbiterDecision(winner=hold, all_scored=[], reasoning=hold.reasoning)

    scored = [score_proposal(p, snapshot) for p in proposals]
    scored.sort(key=lambda s: s.score, reverse=True)

    best = scored[0]

    if best.score < MIN_SCORE_THRESHOLD or best.proposal.contracts == 0:
        hold = AgentProposal(
            agent_name="arbiter",
            bias="neutral",
            structure=HOLD_STRUCTURE,
            contracts=0,
            confidence=1.0,
            reasoning=(
                f"Best proposal ({best.proposal.agent_name}, score={best.score:.2f}) "
                f"did not beat threshold {MIN_SCORE_THRESHOLD}. HOLD."
            ),
            expected_pnl=0.0,
            max_loss=0.0,
        )
        reasoning = hold.reasoning
        return ArbiterDecision(winner=hold, all_scored=scored, reasoning=reasoning)

    reasoning = (
        f"Winner: {best.proposal.agent_name} ({best.proposal.structure.name}) "
        f"score={best.score:.2f}. "
        f"Runners-up: "
        + ", ".join(
            f"{s.proposal.agent_name}={s.score:.2f}"
            for s in scored[1:]
        )
    )

    logger.info("arbiter: %s", reasoning)

    return ArbiterDecision(winner=best.proposal, all_scored=scored, reasoning=reasoning)
