"""
options/structures.py — Dataclasses representing option strategies.

An OptionStructure is a named collection of legs with sizing info.
It is the output of the selector and the input to the executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


OptionSide = Literal["buy", "sell"]
OptionType = Literal["call", "put"]
StructureName = Literal[
    "protective_put",
    "bear_put_spread",
    "collar",
    "covered_call",
    "iron_condor",
    "sell_otm_put",
    "hold",
]


@dataclass
class OptionLeg:
    """One leg of a multi-leg option structure."""
    symbol: str           # OCC option symbol e.g. "SPY240920P00520000"
    underlying: str
    side: OptionSide
    option_type: OptionType
    strike: float
    expiration: str       # ISO date string
    bid: float
    ask: float
    mid: float
    delta: float
    qty: int = 1          # number of contracts


@dataclass
class OptionStructure:
    """A complete named option strategy ready for execution."""
    name: StructureName
    underlying: str
    legs: list[OptionLeg]
    net_debit: float     # positive = we pay, negative = we collect credit
    net_delta: float     # combined delta of all legs
    max_loss: float      # worst-case loss per contract set
    max_gain: float      # best-case gain (float('inf') for naked shorts)
    rationale: str       # human-readable explanation written by sub-agent

    @property
    def total_cost(self) -> float:
        """Total premium cost = net_debit * 100 * contracts."""
        if not self.legs:
            return 0.0
        contracts = max(leg.qty for leg in self.legs)
        return self.net_debit * 100 * contracts

    @property
    def is_executable(self) -> bool:
        return (
            self.name != "hold"
            and len(self.legs) > 0
            and all(leg.mid > 0 for leg in self.legs)
        )


# Sentinel "do nothing" structure
HOLD_STRUCTURE = OptionStructure(
    name="hold",
    underlying="",
    legs=[],
    net_debit=0.0,
    net_delta=0.0,
    max_loss=0.0,
    max_gain=0.0,
    rationale="No action — risk arbiter found no proposal above min score.",
)
