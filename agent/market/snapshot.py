"""
market/snapshot.py — Builds a PortfolioSnapshot: the complete picture of
account state, equity positions, and market data for all watchlist symbols.
This is the input to every debate tick.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from agent.mcp_client.client import MCPClient
from agent.mcp_client.tools import (
    AccountInfo,
    MarketSnapshot,
    Position,
    get_account,
    get_market_snapshot,
    get_positions,
)

logger = logging.getLogger(__name__)


@dataclass
class PortfolioSnapshot:
    """Complete market + account state captured at one tick."""

    account: AccountInfo
    positions: list[Position]
    snapshots: dict[str, MarketSnapshot]  # symbol → snapshot

    @property
    def equity_positions(self) -> list[Position]:
        return [p for p in self.positions if p.asset_class == "us_equity"]

    @property
    def option_positions(self) -> list[Position]:
        return [p for p in self.positions if p.asset_class == "us_option"]

    @property
    def net_portfolio_delta(self) -> float:
        """
        Approximate portfolio delta.

        Equity positions each contribute delta ≈ qty shares (delta=1 per share).
        Option positions contribute their stored delta * qty * 100.
        Normalised to portfolio equity.
        """
        equity = self.account.equity or 1.0
        total = 0.0

        for p in self.equity_positions:
            last = self.snapshots.get(p.symbol)
            price = last.last_price if last else p.avg_entry_price
            total += p.qty * price  # $ delta

        for p in self.option_positions:
            if p.delta is not None:
                total += p.delta * p.qty * 100  # 1 contract = 100 shares notional

        return total / equity  # normalised

    @property
    def net_portfolio_vega(self) -> float:
        total = 0.0
        for p in self.option_positions:
            if p.vega is not None:
                total += p.vega * p.qty * 100
        return total


async def fetch_portfolio_snapshot(
    client: MCPClient, watchlist: list[str]
) -> PortfolioSnapshot:
    """
    Concurrently fetch account info, all positions, and market snapshots
    for every symbol in ``watchlist``.
    """
    account_task = asyncio.create_task(get_account(client))
    positions_task = asyncio.create_task(get_positions(client))

    snapshot_tasks = {
        sym: asyncio.create_task(get_market_snapshot(client, sym))
        for sym in watchlist
    }

    account, positions = await asyncio.gather(account_task, positions_task)

    snapshots: dict[str, MarketSnapshot] = {}
    for sym, task in snapshot_tasks.items():
        try:
            snapshots[sym] = await task
        except Exception as exc:
            logger.warning("Failed to fetch snapshot for %s: %s", sym, exc)

    logger.info(
        "Snapshot captured: equity=$%.2f, positions=%d, market_symbols=%d",
        account.equity,
        len(positions),
        len(snapshots),
    )
    return PortfolioSnapshot(account=account, positions=positions, snapshots=snapshots)
