"""
debate/bull.py — Bullish sub-agent (heuristic implementation).

Bias: reduces hedges, favours premium collection strategies.
Proposes selling OTM puts or collars when IV is elevated.

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


class BullSubAgent(SubAgent):
    """
    Argues the bullish case.

    Heuristics:
    - If portfolio is already well-hedged (many option positions), propose HOLD.
    - If IV rank > 50, sell OTM put to collect premium (neutral-to-bull).
    - Otherwise, HOLD — the market looks fine.
    """

    @property
    def name(self) -> str:
        return "bull"

    async def propose(
        self,
        snapshot: PortfolioSnapshot,
        chain: OptionChain,
        iv_rank: float,
    ) -> AgentProposal:
        portfolio_delta = snapshot.net_portfolio_delta

        # If we're already quite long (delta > target), no more bullish trades
        if portfolio_delta >= 0.40:
            return AgentProposal(
                agent_name=self.name,
                bias="bull",
                structure=HOLD_STRUCTURE,
                contracts=0,
                confidence=0.4,
                reasoning="Portfolio already long-leaning (delta=%.3f). HOLD." % portfolio_delta,
                expected_pnl=0.0,
                max_loss=0.0,
            )

        structure = select_structure(snapshot, chain, bias="bull", iv_rank=iv_rank)
        qty = size_structure(structure, snapshot)

        confidence = 0.5 + (iv_rank / 200)  # higher IV → more confident in premium collection
        expected_pnl = structure.net_debit * -100 * qty  # selling = negative net_debit = credit

        return AgentProposal(
            agent_name=self.name,
            bias="bull",
            structure=structure,
            contracts=qty,
            confidence=round(confidence, 3),
            reasoning=(
                f"Bullish bias. IV rank={iv_rank:.1f}. "
                f"Proposing {structure.name} on {chain.underlying}: {structure.rationale}"
            ),
            expected_pnl=expected_pnl,
            max_loss=structure.max_loss * qty,
        )
