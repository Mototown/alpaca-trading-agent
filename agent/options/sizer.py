"""
options/sizer.py — Determines how many contracts to trade.

Uses a Kelly-inspired approach capped by:
  1. Premium budget (max_hedge_pct * portfolio equity)
  2. Delta gap target (fill hedge_gap with minimum contracts)
  3. Hard max contracts per symbol
"""

from __future__ import annotations

import logging
import math

from agent.config import settings
from agent.market.snapshot import PortfolioSnapshot
from agent.options.structures import OptionStructure

logger = logging.getLogger(__name__)


def size_structure(
    structure: OptionStructure,
    snapshot: PortfolioSnapshot,
) -> int:
    """
    Return the number of contract-sets to trade for ``structure``.

    Returns 0 if sizing would violate any limit.
    """
    if structure.name == "hold" or not structure.legs:
        return 0

    equity = snapshot.account.equity
    if equity <= 0:
        return 0

    max_premium = equity * settings.max_hedge_pct

    # Cost per contract-set (1 set = all legs × 100 share multiplier)
    cost_per_set = abs(structure.net_debit) * 100
    if cost_per_set <= 0:
        # Credit structure — no premium limit applies, default to 1
        contracts_by_budget = settings.max_contracts_per_symbol
    else:
        contracts_by_budget = math.floor(max_premium / cost_per_set)

    # Delta-gap sizing: how many sets to close the delta gap?
    current_delta = snapshot.net_portfolio_delta
    delta_gap = abs(settings.target_delta - current_delta)
    delta_per_set = abs(structure.net_delta)

    if delta_per_set > 0:
        contracts_by_delta = math.ceil(delta_gap / delta_per_set)
    else:
        contracts_by_delta = 1

    qty = min(contracts_by_budget, contracts_by_delta, settings.max_contracts_per_symbol)
    qty = max(qty, 0)

    logger.debug(
        "sizer: structure=%s budget_qty=%d delta_qty=%d → qty=%d",
        structure.name, contracts_by_budget, contracts_by_delta, qty,
    )

    if qty == 0:
        logger.info("sizer: 0 contracts — premium budget exhausted or delta already within target")

    return qty
