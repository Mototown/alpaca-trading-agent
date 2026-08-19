"""
execution/executor.py — Builds MCP order payloads and submits them.

``execute_proposal()`` is the single entry point called by the main loop.
It handles dry-run mode, constructs multi-leg order payloads,
submits via MCP, and returns a structured ExecutionResult.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent.config import settings
from agent.debate.base import AgentProposal
from agent.mcp_client.client import MCPClient
from agent.mcp_client.tools import OrderResult, place_option_order
from agent.options.structures import OptionLeg

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    success: bool
    dry_run: bool
    order_id: str = ""
    order_status: str = ""
    legs_submitted: list[dict] = field(default_factory=list)
    error: str = ""


async def execute_proposal(
    client: MCPClient,
    proposal: AgentProposal,
) -> ExecutionResult:
    """
    Execute the winning proposal.

    In dry-run mode, logs the intended order without placing it.
    """
    structure = proposal.structure
    qty = proposal.contracts

    if structure.name == "hold" or qty == 0:
        return ExecutionResult(success=True, dry_run=False, order_status="hold")

    # Scale leg quantities
    legs = _build_leg_payloads(structure.legs, qty)

    # Compute limit price = mid × slight improvement
    limit_price = round(_net_limit_price(structure.legs) * 1.01, 2)

    if settings.dry_run:
        logger.info(
            "[DRY RUN] Would place %s order: %d contract(s), legs=%s, limit=%.2f",
            structure.name, qty, [l["symbol"] for l in legs], limit_price,
        )
        return ExecutionResult(
            success=True,
            dry_run=True,
            order_status="dry_run",
            legs_submitted=legs,
        )

    try:
        result: OrderResult = await place_option_order(
            client=client,
            legs=legs,
            order_type="limit",
            time_in_force="day",
            limit_price=limit_price,
        )
        logger.info(
            "Order placed: id=%s status=%s structure=%s qty=%d",
            result.order_id, result.status, structure.name, qty,
        )
        return ExecutionResult(
            success=True,
            dry_run=False,
            order_id=result.order_id,
            order_status=result.status,
            legs_submitted=legs,
        )
    except Exception as exc:
        logger.error("Order submission failed: %s", exc)
        return ExecutionResult(success=False, dry_run=False, error=str(exc))


def _build_leg_payloads(legs: list[OptionLeg], qty: int) -> list[dict]:
    return [
        {
            "symbol": leg.symbol,
            "side": leg.side,
            "ratio_qty": leg.qty * qty,
        }
        for leg in legs
    ]


def _net_limit_price(legs: list[OptionLeg]) -> float:
    """
    Net debit/credit at mid prices.
    Buys add to cost, sells subtract.
    """
    total = 0.0
    for leg in legs:
        if leg.side == "buy":
            total += leg.mid
        else:
            total -= leg.mid
    return total
