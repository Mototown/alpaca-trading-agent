"""
options/greeks.py — Local Black-Scholes Greeks computation.

Used to enrich option contracts when the MCP server / API doesn't
return pre-computed Greeks.  No network calls; pure math.
"""

from __future__ import annotations

import math
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)

DAYS_PER_YEAR = 365.0
RISK_FREE_RATE = 0.05  # 5% — update periodically from Fed funds


class Greeks(TypedDict, total=False):
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float
    iv: float


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _norm_cdf(x: float) -> float:
    """Approximation of the standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2)))


def _d1_d2(
    spot: float, strike: float, dte: float, iv: float, r: float = RISK_FREE_RATE
) -> tuple[float, float]:
    """Return (d1, d2) for Black-Scholes.  ``dte`` is in **years**."""
    if dte <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return 0.0, 0.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * dte) / (iv * math.sqrt(dte))
    d2 = d1 - iv * math.sqrt(dte)
    return d1, d2


def compute_greeks(
    option_type: str,        # "call" or "put"
    spot: float,
    strike: float,
    dte: int,                # days to expiration
    iv: float,               # annualised implied volatility (decimal, e.g. 0.25)
    mid_price: float = 0.0,  # used only for IV solving (future use)
    r: float = RISK_FREE_RATE,
) -> Greeks:
    """
    Compute Black-Scholes delta, gamma, theta, vega for a single option.

    Returns an empty dict if inputs are invalid.
    """
    t = dte / DAYS_PER_YEAR
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        logger.debug("compute_greeks: invalid inputs — returning empty Greeks")
        return {}

    try:
        d1, d2 = _d1_d2(spot, strike, t, iv, r)
        n_d1 = _norm_pdf(d1)
        sqrt_t = math.sqrt(t)

        gamma = n_d1 / (spot * iv * sqrt_t)
        vega = spot * n_d1 * sqrt_t / 100  # per 1% IV move

        if option_type.lower() == "call":
            delta = _norm_cdf(d1)
            theta = (
                -(spot * n_d1 * iv) / (2 * sqrt_t)
                - r * strike * math.exp(-r * t) * _norm_cdf(d2)
            ) / DAYS_PER_YEAR
        else:  # put
            delta = _norm_cdf(d1) - 1.0
            theta = (
                -(spot * n_d1 * iv) / (2 * sqrt_t)
                + r * strike * math.exp(-r * t) * _norm_cdf(-d2)
            ) / DAYS_PER_YEAR

        return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega)

    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        logger.debug("compute_greeks: math error for (type=%s, S=%s, K=%s, dte=%s, iv=%s): %s",
                     option_type, spot, strike, dte, iv, exc)
        return {}


def estimate_iv(
    option_type: str,
    spot: float,
    strike: float,
    dte: int,
    market_price: float,
    r: float = RISK_FREE_RATE,
    iterations: int = 50,
    tol: float = 1e-6,
) -> float | None:
    """
    Newton-Raphson IV solver.  Returns annualised IV or None if it fails to converge.
    """
    if market_price <= 0 or dte <= 0:
        return None

    t = dte / DAYS_PER_YEAR
    iv = 0.20  # initial guess

    for _ in range(iterations):
        d1, d2 = _d1_d2(spot, strike, t, iv, r)
        n_d1 = _norm_pdf(d1)
        sqrt_t = math.sqrt(t)

        if option_type.lower() == "call":
            price = spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
        else:
            price = strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)

        vega = spot * n_d1 * sqrt_t
        if abs(vega) < 1e-10:
            break

        diff = price - market_price
        if abs(diff) < tol:
            return iv

        iv -= diff / vega

        if iv <= 0:
            return None

    return iv if iv > 0 else None
