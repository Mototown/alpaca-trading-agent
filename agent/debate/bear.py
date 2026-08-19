"""
debate/bear.py — Bearish sub-agent (heuristic implementation).

Bias: buys protective puts or bear put spreads when downside risk is elevated.

Day 3+: replace ``propose()`` body with an LLM structured-output call.
"""

from __future__ import annotations

import logging

from agent.debate.base import AgentProposal, SubAgent
from agent.market.option_chain import OptionChain
from agent.market.snapshot import PortfolioSnapshot
from agent.options.selector import select_structure
from agent.options.sizer import size_structure
from agent.options.structures import HOLD_STRUCTURE

logger = logging.getLogger(__name__)


class BearSubAgent(SubAgent):
    """
    Argues the bearish case.

    Heuristics:
    - Always advocates for downside protection when portfolio has equity exposure.
    - Prefers bear put spreads when IV rank > 60 (cheaper debit spread).
    - Falls back to protective puts when IV is low.
    """

    @property
    def name(self) -> str:
        return "bear"

    async def propose(
        self,
        snapshot: PortfolioSnapshot,
        chain: OptionChain,
        iv_rank: float,
    ) -> AgentProposal:
        equity_positions = snapshot.equity_positions
        portfolio_delta = snapshot.net_portfolio_delta

        # No equity exposure → nothing to protect
        if not equity_positions and portfolio_delta >= 0:
            return AgentProposal(
                agent_name=self.name,
                bias="bear",
                structure=HOLD_STRUCTURE,
                contracts=0,
                confidence=0.2,
                reasoning="No equity exposure to hedge. HOLD.",
                expected_pnl=0.0,
                max_loss=0.0,
            )

        structure = select_structure(snapshot, chain, bias="bear", iv_rank=iv_rank)
        qty = size_structure(structure, snapshot)

        # Confidence rises with portfolio delta (more long = more to hedge)
        confidence = min(0.9, 0.40 + portfolio_delta * 0.5)

        # For a debit hedge, expected P&L is negative premium spent + expected protection value
        # Simple estimate: 40% chance market drops 5%, hedge covers ~30% of that
        equity = snapshot.account.equity
        downside_scenario = equity * 0.05 * 0.30  # rough expected value of hedge
        expected_pnl = downside_scenario - (structure.net_debit * 100 * qty)

        return AgentProposal(
            agent_name=self.name,
            bias="bear",
            structure=structure,
            contracts=qty,
            confidence=round(confidence, 3),
            reasoning=(
                f"Bearish hedge needed. Portfolio delta={portfolio_delta:.3f}, "
                f"IV rank={iv_rank:.1f}. "
                f"Proposing {structure.name}: {structure.rationale}"
            ),
            expected_pnl=expected_pnl,
            max_loss=structure.max_loss * qty,
        )
