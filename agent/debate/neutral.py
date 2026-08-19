"""
debate/neutral.py — Neutral sub-agent (heuristic implementation).

Bias: volatility-neutral strategies — collars and iron condors when IV is high.

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


class NeutralSubAgent(SubAgent):
    """
    Argues the neutral / vol-selling case.

    Heuristics:
    - If IV rank > 60, sell a collar or iron condor.
    - If IV rank <= 40, HOLD — no edge in selling low vol.
    - Aims for approximately delta-neutral portfolio.
    """

    @property
    def name(self) -> str:
        return "neutral"

    async def propose(
        self,
        snapshot: PortfolioSnapshot,
        chain: OptionChain,
        iv_rank: float,
    ) -> AgentProposal:
        # Neutral strategy requires elevated IV to have edge
        if iv_rank < 40:
            return AgentProposal(
                agent_name=self.name,
                bias="neutral",
                structure=HOLD_STRUCTURE,
                contracts=0,
                confidence=0.3,
                reasoning=(
                    f"IV rank={iv_rank:.1f} too low for vol-selling strategy. HOLD."
                ),
                expected_pnl=0.0,
                max_loss=0.0,
            )

        structure = select_structure(snapshot, chain, bias="neutral", iv_rank=iv_rank)
        qty = size_structure(structure, snapshot)

        # Confidence rises with IV rank (more premium available)
        confidence = min(0.85, 0.40 + (iv_rank - 40) / 100)

        # Neutral strategies collect premium
        expected_pnl = abs(structure.net_debit) * 100 * qty * 0.60  # rough 60% prob of keeping credit

        return AgentProposal(
            agent_name=self.name,
            bias="neutral",
            structure=structure,
            contracts=qty,
            confidence=round(confidence, 3),
            reasoning=(
                f"IV rank={iv_rank:.1f} elevated — vol-selling edge present. "
                f"Proposing {structure.name}: {structure.rationale}"
            ),
            expected_pnl=expected_pnl,
            max_loss=structure.max_loss * qty,
        )
