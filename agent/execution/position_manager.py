"""
execution/position_manager.py — Manages rolling and closing existing option hedges.

Rules:
  - Close options that are < 7 DTE (avoid pin risk).
  - Close options that have achieved 80%+ of their max gain.
  - Close conflicting hedges before opening a new one on the same underlying.
"""

from __future__ import annotations

import logging
from datetime import date

from agent.config import settings
from agent.mcp_client.client import MCPClient
from agent.mcp_client.tools import Position, close_position

logger = logging.getLogger(__name__)

CLOSE_AT_GAIN_PCT = 0.80   # close when 80% of max gain is captured
MIN_DTE_TO_KEEP = 7        # close options with fewer than 7 DTE


async def close_expiring_positions(
    client: MCPClient,
    option_positions: list[Position],
) -> list[str]:
    """Close any option positions that are close to expiry."""
    closed = []
    today = date.today()

    for pos in option_positions:
        # Extract expiry from OCC symbol: last 6 chars before P/C = YYMMDD
        dte = _extract_dte(pos.symbol, today)
        if dte is not None and dte < MIN_DTE_TO_KEEP:
            logger.info(
                "Closing expiring option %s (%d DTE < %d threshold)",
                pos.symbol, dte, MIN_DTE_TO_KEEP,
            )
            try:
                await close_position(client, pos.symbol)
                closed.append(pos.symbol)
            except Exception as exc:
                logger.error("Failed to close %s: %s", pos.symbol, exc)

    return closed


async def close_profitable_positions(
    client: MCPClient,
    option_positions: list[Position],
) -> list[str]:
    """Close positions that have captured most of their potential gain."""
    closed = []

    for pos in option_positions:
        if pos.avg_entry_price <= 0:
            continue
        gain_pct = pos.unrealized_pl / (abs(pos.avg_entry_price) * 100)
        if gain_pct >= CLOSE_AT_GAIN_PCT:
            logger.info(
                "Closing profitable option %s (gain=%.1f%% >= %.1f%% target)",
                pos.symbol, gain_pct * 100, CLOSE_AT_GAIN_PCT * 100,
            )
            try:
                await close_position(client, pos.symbol)
                closed.append(pos.symbol)
            except Exception as exc:
                logger.error("Failed to close %s: %s", pos.symbol, exc)

    return closed


async def close_conflicting_hedges(
    client: MCPClient,
    option_positions: list[Position],
    new_underlying: str,
) -> list[str]:
    """Close existing option positions on ``new_underlying`` before opening new ones."""
    closed = []

    for pos in option_positions:
        # OCC symbol starts with underlying padded to 6 chars
        if pos.symbol.startswith(new_underlying[:6].ljust(6)):
            logger.info(
                "Closing conflicting hedge %s before new trade on %s",
                pos.symbol, new_underlying,
            )
            try:
                await close_position(client, pos.symbol)
                closed.append(pos.symbol)
            except Exception as exc:
                logger.error("Failed to close %s: %s", pos.symbol, exc)

    return closed


def _extract_dte(occ_symbol: str, today: date) -> int | None:
    """
    Extract days-to-expiry from an OCC option symbol.
    OCC format: LLLLLLYYMMDDCNNNNNNN  (6 chars underlying, 6 chars date, C/P, strike)
    """
    try:
        date_str = occ_symbol[6:12]  # YYMMDD
        exp = date(2000 + int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6]))
        return (exp - today).days
    except (ValueError, IndexError):
        return None
