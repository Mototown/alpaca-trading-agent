"""
state/models.py — Insert/query helpers for the decisions and snapshots tables.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from agent.state.db import get_db


async def insert_decision(
    tick: int,
    underlying: str,
    action: str,
    contracts: int,
    winner_agent: str,
    arbiter_score: float | None,
    reasoning: str,
    proposals: list[dict[str, Any]],
    greeks_before: dict[str, Any],
    expected_cost: float,
    order_id: str,
    order_status: str,
    guardrail_block: str | None,
    dry_run: bool,
) -> None:
    db = get_db()
    await db.execute(
        """
        INSERT INTO decisions
          (ts, tick, underlying, action, contracts, winner_agent,
           arbiter_score, reasoning, proposals_json, greeks_before,
           expected_cost, order_id, order_status, guardrail_block, dry_run)
        VALUES
          (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            tick,
            underlying,
            action,
            contracts,
            winner_agent,
            arbiter_score,
            reasoning,
            json.dumps(proposals),
            json.dumps(greeks_before),
            expected_cost,
            order_id,
            order_status,
            guardrail_block,
            int(dry_run),
        ),
    )
    await db.commit()


async def insert_snapshot(
    tick: int,
    equity: float,
    cash: float,
    day_pl: float,
    net_delta: float,
    net_vega: float,
    positions: list[dict[str, Any]],
) -> None:
    db = get_db()
    await db.execute(
        """
        INSERT INTO portfolio_snapshots
          (ts, tick, equity, cash, day_pl, net_delta, net_vega, positions_json)
        VALUES
          (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(timezone.utc).isoformat(),
            tick,
            equity,
            cash,
            day_pl,
            net_delta,
            net_vega,
            json.dumps(positions),
        ),
    )
    await db.commit()


async def get_recent_decisions(limit: int = 50) -> list[aiosqlite.Row]:
    db = get_db()
    async with db.execute(
        "SELECT * FROM decisions ORDER BY ts DESC LIMIT ?", (limit,)
    ) as cursor:
        return await cursor.fetchall()


async def get_recent_snapshots(limit: int = 100) -> list[aiosqlite.Row]:
    db = get_db()
    async with db.execute(
        "SELECT * FROM portfolio_snapshots ORDER BY ts DESC LIMIT ?", (limit,)
    ) as cursor:
        return await cursor.fetchall()
