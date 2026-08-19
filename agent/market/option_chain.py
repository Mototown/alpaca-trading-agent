"""
market/option_chain.py — Fetches and filters option chains for a given symbol.

After fetching, we:
  1. Filter by min open interest and max bid/ask spread.
  2. Attach local Black-Scholes Greeks if the server didn't return them.
  3. Return a typed OptionChain object that callers can query by strike / DTE.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from agent.config import settings
from agent.mcp_client.client import MCPClient
from agent.mcp_client.tools import OptionContract, get_option_chain
from agent.options.greeks import compute_greeks

logger = logging.getLogger(__name__)


@dataclass
class OptionChain:
    underlying: str
    spot_price: float
    contracts: list[OptionContract] = field(default_factory=list)

    def puts(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.option_type == "put"]

    def calls(self) -> list[OptionContract]:
        return [c for c in self.contracts if c.option_type == "call"]

    def nearest_put(
        self,
        target_strike_pct: float = 0.97,
        min_dte: int | None = None,
    ) -> OptionContract | None:
        """Return the put contract closest to ``target_strike_pct * spot``."""
        target = self.spot_price * target_strike_pct
        min_dte = min_dte or settings.min_dte
        candidates = [
            c for c in self.puts()
            if (c.expiration_date - date.today()).days >= min_dte
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.strike_price - target))

    def nearest_call(
        self,
        target_strike_pct: float = 1.03,
        min_dte: int | None = None,
    ) -> OptionContract | None:
        """Return the call contract closest to ``target_strike_pct * spot``."""
        target = self.spot_price * target_strike_pct
        min_dte = min_dte or settings.min_dte
        candidates = [
            c for c in self.calls()
            if (c.expiration_date - date.today()).days >= min_dte
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda c: abs(c.strike_price - target))


async def fetch_option_chain(
    client: MCPClient,
    underlying: str,
    spot_price: float,
    min_dte: int | None = None,
    max_dte: int | None = None,
) -> OptionChain:
    """Fetch, filter, and Greek-enrich the option chain for ``underlying``."""
    min_dte = min_dte or settings.preferred_dte_low
    max_dte = max_dte or settings.preferred_dte_high

    raw: list[OptionContract] = await get_option_chain(
        client, underlying, expiry_min_dte=min_dte, expiry_max_dte=max_dte
    )

    # Filter by liquidity
    filtered = [
        c for c in raw
        if (
            c.open_interest >= settings.min_open_interest
            and (c.ask - c.bid) <= settings.max_spread_width
        )
    ]

    if not filtered:
        logger.warning(
            "No liquid contracts found for %s (relaxing filters)", underlying
        )
        filtered = raw  # use everything rather than nothing

    # Enrich with local Greeks where server didn't provide them
    today = date.today()
    for c in filtered:
        if c.delta is None and c.mid > 0:
            dte = (c.expiration_date - today).days
            greeks = compute_greeks(
                option_type=c.option_type,
                spot=spot_price,
                strike=c.strike_price,
                dte=dte,
                iv=c.implied_volatility or 0.20,
                mid_price=c.mid,
            )
            c.delta = greeks.get("delta")
            c.gamma = greeks.get("gamma")
            c.theta = greeks.get("theta")
            c.vega = greeks.get("vega")

    logger.debug(
        "option_chain(%s): %d raw → %d liquid contracts",
        underlying, len(raw), len(filtered)
    )
    return OptionChain(underlying=underlying, spot_price=spot_price, contracts=filtered)
