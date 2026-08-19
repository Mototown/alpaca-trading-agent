"""
risk/guardrails.py — Hard limits and circuit breakers.

``GuardrailResult.ok`` must be True before any order is placed.
``check_all()`` runs every hard limit and returns the first violation found,
or a passing result if all limits are satisfied.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agent.config import settings
from agent.debate.base import AgentProposal
from agent.market.snapshot import PortfolioSnapshot

logger = logging.getLogger(__name__)


@dataclass
class GuardrailResult:
    ok: bool
    reason: str


# ── Module-level circuit-breaker state ──────────────────────────────────────
_is_halted: bool = False
_halt_reason: str = ""
_session_high_equity: float = 0.0
_session_start_equity: float = 0.0


def set_session_start_equity(equity: float) -> None:
    global _session_high_equity, _session_start_equity
    _session_start_equity = equity
    _session_high_equity = equity


def update_equity_high(equity: float) -> None:
    global _session_high_equity
    if equity > _session_high_equity:
        _session_high_equity = equity


def halt(reason: str) -> None:
    global _is_halted, _halt_reason
    _is_halted = True
    _halt_reason = reason
    logger.critical("CIRCUIT BREAKER TRIGGERED: %s", reason)


def is_halted() -> bool:
    return _is_halted


# ── Individual checks ────────────────────────────────────────────────────────

def _check_circuit_breaker() -> GuardrailResult:
    if _is_halted:
        return GuardrailResult(ok=False, reason=f"Circuit breaker active: {_halt_reason}")
    return GuardrailResult(ok=True, reason="")


def _check_market_hours() -> GuardrailResult:
    """Skip orders outside US market hours (rough check; Alpaca enforces precisely)."""
    from datetime import datetime, timezone, time as dtime
    now = datetime.now(timezone.utc)
    # Market hours: 09:30–16:00 ET = 13:30–20:00 UTC (rough; ignores DST)
    market_open = dtime(13, 30)
    market_close = dtime(20, 0)
    current_time = now.time()
    if not (market_open <= current_time <= market_close):
        return GuardrailResult(ok=False, reason="Outside market hours — no new orders.")
    # Skip weekends
    if now.weekday() >= 5:
        return GuardrailResult(ok=False, reason="Weekend — market closed.")
    return GuardrailResult(ok=True, reason="")


def _check_daily_loss(equity: float) -> GuardrailResult:
    if _session_start_equity <= 0:
        return GuardrailResult(ok=True, reason="")
    loss_pct = (_session_start_equity - equity) / _session_start_equity
    if loss_pct >= settings.daily_loss_limit:
        reason = (
            f"Daily loss limit hit: {loss_pct:.2%} >= {settings.daily_loss_limit:.2%}. "
            "Halting all trading."
        )
        halt(reason)
        return GuardrailResult(ok=False, reason=reason)
    return GuardrailResult(ok=True, reason="")


def _check_drawdown(equity: float) -> GuardrailResult:
    if _session_high_equity <= 0:
        return GuardrailResult(ok=True, reason="")
    dd = (_session_high_equity - equity) / _session_high_equity
    if dd >= settings.drawdown_pause_pct:
        return GuardrailResult(
            ok=False,
            reason=f"Drawdown {dd:.2%} >= pause threshold {settings.drawdown_pause_pct:.2%}.",
        )
    return GuardrailResult(ok=True, reason="")


def _check_position_count(snapshot: PortfolioSnapshot) -> GuardrailResult:
    n = len(snapshot.positions)
    if n >= settings.max_positions:
        return GuardrailResult(
            ok=False,
            reason=f"Max positions reached ({n}/{settings.max_positions}).",
        )
    return GuardrailResult(ok=True, reason="")


def _check_option_exposure(
    snapshot: PortfolioSnapshot,
    proposal: AgentProposal,
) -> GuardrailResult:
    equity = snapshot.account.equity or 1.0
    current_option_value = sum(
        abs(p.market_value)
        for p in snapshot.option_positions
    )
    proposed_cost = abs(proposal.structure.total_cost) * proposal.contracts
    total = current_option_value + proposed_cost
    limit = equity * settings.max_option_exposure_pct
    if total > limit:
        return GuardrailResult(
            ok=False,
            reason=(
                f"Total option exposure ${total:.0f} would exceed limit "
                f"${limit:.0f} ({settings.max_option_exposure_pct:.0%} of equity)."
            ),
        )
    return GuardrailResult(ok=True, reason="")


def _check_min_liquidity(proposal: AgentProposal) -> GuardrailResult:
    for leg in proposal.structure.legs:
        spread = leg.ask - leg.bid
        mid = leg.mid
        if spread > settings.max_spread_width and (mid <= 0 or spread / mid > 0.05):
            return GuardrailResult(
                ok=False,
                reason=(
                    f"Option {leg.symbol} spread ${spread:.2f} too wide "
                    f"(limit ${settings.max_spread_width:.2f})."
                ),
            )
    return GuardrailResult(ok=True, reason="")


# ── Master check ─────────────────────────────────────────────────────────────

def check_all(
    snapshot: PortfolioSnapshot,
    proposal: AgentProposal,
) -> GuardrailResult:
    """
    Run all guardrails in priority order.
    Returns the first failure, or ok=True if everything passes.
    """
    equity = snapshot.account.equity
    update_equity_high(equity)

    checks = [
        _check_circuit_breaker(),
        _check_market_hours(),
        _check_daily_loss(equity),
        _check_drawdown(equity),
        _check_position_count(snapshot),
        _check_option_exposure(snapshot, proposal),
        _check_min_liquidity(proposal),
    ]

    for result in checks:
        if not result.ok:
            logger.warning("Guardrail blocked: %s", result.reason)
            return result

    return GuardrailResult(ok=True, reason="All guardrails passed.")
