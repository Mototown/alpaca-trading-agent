"""
agent/main.py — Entry point and main autonomous decision loop.

The agent runs on a fixed schedule (default: every 5 minutes).
Each tick executes the full GATHER → ASSESS → DEBATE → SCORE → EXECUTE → LOG pipeline.

Usage:
    uv run python -m agent.main                # live loop
    uv run python -m agent.main --dry-run      # log decisions, no orders placed
    uv run python -m agent.main --once         # run exactly one tick and exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from agent.config import settings
from agent.debate.arbiter import arbitrate
from agent.debate.bear import BearSubAgent
from agent.debate.bull import BullSubAgent
from agent.debate.neutral import NeutralSubAgent
from agent.execution.executor import execute_proposal
from agent.execution.position_manager import (
    close_conflicting_hedges,
    close_expiring_positions,
    close_profitable_positions,
)
from agent.market.option_chain import fetch_option_chain
from agent.market.snapshot import fetch_portfolio_snapshot
from agent.mcp_client.client import MCPClient, MCPUnavailableError
from agent.risk.guardrails import check_all, is_halted, set_session_start_equity
from agent.state.db import close_db, init_db
from agent.state.models import insert_decision, insert_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("daha.main")

# ── Sub-agent instances (stateless, reused each tick) ────────────────────────
_bull = BullSubAgent()
_bear = BearSubAgent()
_neutral = NeutralSubAgent()

# ── Tick counter ─────────────────────────────────────────────────────────────
_tick: int = 0


async def run_tick(client: MCPClient) -> None:
    """Execute one full decision loop tick."""
    global _tick
    _tick += 1
    tick_id = _tick
    tick_start = datetime.now(timezone.utc)

    logger.info("═" * 60)
    logger.info("TICK %d  started at %s", tick_id, tick_start.strftime("%H:%M:%S UTC"))
    logger.info("═" * 60)

    # ── 1. GATHER ────────────────────────────────────────────────────────────
    try:
        snapshot = await fetch_portfolio_snapshot(client, settings.watchlist)
    except Exception as exc:
        logger.error("GATHER failed — skipping tick %d: %s", tick_id, exc)
        return

    # Set session baseline on first tick
    if tick_id == 1:
        set_session_start_equity(snapshot.account.equity)
        logger.info("Session start equity: $%.2f", snapshot.account.equity)

    # ── 2. ASSESS ────────────────────────────────────────────────────────────
    net_delta = snapshot.net_portfolio_delta
    net_vega = snapshot.net_portfolio_vega

    logger.info(
        "Portfolio: equity=$%.2f  day_pl=$%.2f  delta=%.3f  vega=%.2f  positions=%d",
        snapshot.account.equity,
        snapshot.account.day_pl,
        net_delta,
        net_vega,
        len(snapshot.positions),
    )

    # Persist snapshot
    await insert_snapshot(
        tick=tick_id,
        equity=snapshot.account.equity,
        cash=snapshot.account.cash,
        day_pl=snapshot.account.day_pl,
        net_delta=net_delta,
        net_vega=net_vega,
        positions=[
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "market_value": p.market_value,
                "unrealized_pl": p.unrealized_pl,
                "asset_class": p.asset_class,
            }
            for p in snapshot.positions
        ],
    )

    # Maintenance: close expiring and profitable option positions
    option_positions = snapshot.option_positions
    if option_positions:
        closed_exp = await close_expiring_positions(client, option_positions)
        closed_pnl = await close_profitable_positions(client, option_positions)
        if closed_exp or closed_pnl:
            logger.info(
                "Maintenance closed: expiring=%s profitable=%s",
                closed_exp, closed_pnl,
            )

    # If halted, skip trade decisions but keep running (to log state)
    if is_halted():
        logger.warning("Agent is HALTED — monitoring only, no new trades this tick.")
        return

    # ── 3. DEBATE + 4. SCORE (per watchlist symbol) ─────────────────────────
    # We run the debate loop for the primary hedge symbol (largest equity position,
    # or SPY if no equity positions exist).
    target_symbol = _pick_hedge_symbol(snapshot)
    snap = snapshot.snapshots.get(target_symbol)
    if snap is None or snap.last_price <= 0:
        logger.warning("No market snapshot for %s — skipping debate.", target_symbol)
        return

    spot = snap.last_price
    iv_rank = snap.implied_volatility_rank or _estimate_iv_rank(snap)

    logger.info("Hedge target: %s  spot=$%.2f  IV rank=%.1f", target_symbol, spot, iv_rank)

    try:
        chain = await fetch_option_chain(client, target_symbol, spot)
    except Exception as exc:
        logger.error("Failed to fetch option chain for %s: %s", target_symbol, exc)
        return

    if not chain.contracts:
        logger.warning("Empty option chain for %s — skipping debate.", target_symbol)
        return

    # Run all three sub-agents concurrently
    bull_p, bear_p, neutral_p = await asyncio.gather(
        _bull.propose(snapshot, chain, iv_rank),
        _bear.propose(snapshot, chain, iv_rank),
        _neutral.propose(snapshot, chain, iv_rank),
    )

    proposals = [bull_p, bear_p, neutral_p]

    for p in proposals:
        logger.info(
            "  [%s] %s  qty=%d  confidence=%.2f  expected_pnl=$%.2f",
            p.agent_name.upper(),
            p.structure.name,
            p.contracts,
            p.confidence,
            p.expected_pnl,
        )

    decision = arbitrate(proposals, snapshot)
    winner = decision.winner

    logger.info("ARBITER → %s  (%s)", winner.structure.name.upper(), decision.reasoning)

    # ── 5. EXECUTE ───────────────────────────────────────────────────────────
    guardrail_block: str | None = None
    exec_result = None

    if winner.structure.name != "hold" and winner.contracts > 0:
        # Check guardrails before touching anything
        guard = check_all(snapshot, winner)
        if not guard.ok:
            guardrail_block = guard.reason
            logger.warning("ORDER BLOCKED by guardrail: %s", guard.reason)
        else:
            # Close any conflicting hedges on this symbol first
            await close_conflicting_hedges(client, option_positions, target_symbol)
            exec_result = await execute_proposal(client, winner)

            if exec_result.success:
                status = "dry_run" if exec_result.dry_run else exec_result.order_status
                logger.info(
                    "ORDER %s: id=%s status=%s legs=%s",
                    "SIMULATED" if exec_result.dry_run else "PLACED",
                    exec_result.order_id,
                    status,
                    [l.get("symbol") for l in exec_result.legs_submitted],
                )
            else:
                logger.error("ORDER FAILED: %s", exec_result.error)

    # ── 6. LOG ───────────────────────────────────────────────────────────────
    best_scored = decision.all_scored[0] if decision.all_scored else None
    proposals_data = [
        {
            "agent": p.agent_name,
            "structure": p.structure.name,
            "contracts": p.contracts,
            "confidence": p.confidence,
            "expected_pnl": p.expected_pnl,
            "max_loss": p.max_loss,
            "reasoning": p.reasoning,
        }
        for p in proposals
    ]

    await insert_decision(
        tick=tick_id,
        underlying=target_symbol,
        action=winner.structure.name,
        contracts=winner.contracts,
        winner_agent=winner.agent_name,
        arbiter_score=best_scored.score if best_scored else None,
        reasoning=decision.reasoning,
        proposals=proposals_data,
        greeks_before={"net_delta": net_delta, "net_vega": net_vega},
        expected_cost=abs(winner.structure.total_cost) * winner.contracts,
        order_id=exec_result.order_id if exec_result else "",
        order_status=exec_result.order_status if exec_result else "hold",
        guardrail_block=guardrail_block,
        dry_run=settings.dry_run or (exec_result.dry_run if exec_result else False),
    )

    elapsed = (datetime.now(timezone.utc) - tick_start).total_seconds()
    logger.info("TICK %d complete in %.1fs", tick_id, elapsed)


def _pick_hedge_symbol(snapshot) -> str:
    """
    Return the symbol to hedge this tick.
    Prefers the largest equity position; falls back to first watchlist symbol.
    """
    if snapshot.equity_positions:
        largest = max(snapshot.equity_positions, key=lambda p: abs(p.market_value))
        if largest.symbol in snapshot.snapshots:
            return largest.symbol

    # Fall back to first watchlist symbol with a valid snapshot
    for sym in settings.watchlist:
        if sym in snapshot.snapshots:
            return sym

    return settings.watchlist[0]


def _estimate_iv_rank(snap) -> float:
    """
    Rough IV rank estimate when not provided by the API.
    Uses a heuristic: map IV% to an approximate rank.
    Real IV rank requires 52-week IV history (added on Day 5).
    """
    iv = snap.implied_volatility_pct or 0.20
    # VIX-like mapping: <15% → low rank, >35% → high rank
    rank = max(0.0, min(100.0, (iv - 0.10) / 0.30 * 100))
    return rank


# ── Startup / shutdown ───────────────────────────────────────────────────────

async def _start_dashboard() -> None:
    """Start the FastAPI dashboard in the background (non-blocking)."""
    try:
        import uvicorn
        from agent.dashboard.server import create_app
        app = create_app()
        config = uvicorn.Config(
            app,
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        logger.info(
            "Dashboard running at http://%s:%d",
            settings.dashboard_host,
            settings.dashboard_port,
        )
    except ImportError:
        logger.warning("Dashboard dependencies not available — skipping dashboard.")
    except Exception as exc:
        logger.warning("Dashboard failed to start: %s", exc)


async def main(dry_run: bool = False, once: bool = False) -> None:
    """Main coroutine — initialises everything and runs the loop."""
    if dry_run:
        # Override settings at runtime (env var takes precedence normally)
        import os
        os.environ["DRY_RUN"] = "true"
        # Re-read settings after env change
        settings.__init__()  # type: ignore[misc]

    logger.info("Starting DAHA — Dynamic Adaptive Hedging Agent")
    logger.info("Watchlist: %s", settings.watchlist)
    logger.info("Loop interval: %ds", settings.loop_interval_seconds)
    logger.info("Dry-run: %s", settings.dry_run)
    logger.info("MCP server: %s", settings.mcp_server_url)

    # Initialise SQLite
    await init_db()

    # Start dashboard (non-blocking)
    await _start_dashboard()

    async with MCPClient(url=settings.mcp_server_url) as client:
        if once:
            await run_tick(client)
        else:
            # Run once immediately, then on schedule
            await run_tick(client)

            scheduler = AsyncIOScheduler()
            scheduler.add_job(
                run_tick,
                trigger="interval",
                seconds=settings.loop_interval_seconds,
                args=[client],
                id="main_tick",
                max_instances=1,  # never overlap ticks
                coalesce=True,
            )
            scheduler.start()
            logger.info(
                "Scheduler started — next tick in %ds",
                settings.loop_interval_seconds,
            )

            # Run until interrupted
            stop_event = asyncio.Event()

            def _handle_signal(*_):
                logger.info("Shutdown signal received.")
                stop_event.set()

            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, _handle_signal)

            await stop_event.wait()
            scheduler.shutdown(wait=False)

    await close_db()
    logger.info("DAHA shut down cleanly.")


def run() -> None:
    """Entry point for `daha` script and `python -m agent.main`."""
    parser = argparse.ArgumentParser(description="DAHA — Dynamic Adaptive Hedging Agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log decisions without placing real orders.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Execute one tick then exit (useful for testing).",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(dry_run=args.dry_run, once=args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
