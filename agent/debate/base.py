"""
debate/base.py — Abstract SubAgent base class.

Every sub-agent (Bull, Bear, Neutral) must implement ``propose()``,
returning an ``AgentProposal``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agent.market.option_chain import OptionChain
from agent.market.snapshot import PortfolioSnapshot
from agent.options.structures import OptionStructure


@dataclass
class AgentProposal:
    """The structured output of a sub-agent's analysis."""
    agent_name: str            # "bull", "bear", "neutral"
    bias: str                  # "bull" | "bear" | "neutral"
    structure: OptionStructure # proposed trade (may be HOLD_STRUCTURE)
    contracts: int             # suggested qty from sizer
    confidence: float          # 0–1 self-assessed confidence
    reasoning: str             # plain-English explanation
    expected_pnl: float        # rough expected P&L in dollars
    max_loss: float            # worst-case loss in dollars


class SubAgent(ABC):
    """Base class for Bull / Bear / Neutral sub-agents."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def propose(
        self,
        snapshot: PortfolioSnapshot,
        chain: OptionChain,
        iv_rank: float,
    ) -> AgentProposal:
        """Analyse the current market state and propose an option action."""
        ...
