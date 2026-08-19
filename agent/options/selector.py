"""
options/selector.py — Picks the right option structure based on market conditions.

Uses the selection matrix from PLAN.md:

| Market Condition    | IV Rank | Portfolio Delta | Structure         |
|---------------------|---------|-----------------|-------------------|
| Bearish / High vol  | > 60    | Long            | Bear Put Spread   |
| Bearish / Low vol   | < 40    | Long            | Protective Put    |
| Neutral / High vol  | > 60    | Any             | Collar            |
| Bullish / Any       | Any     | Short           | Sell OTM Put      |
| Volatile / High vol | > 70    | Flat            | Iron Condor (SPY) |
"""

from __future__ import annotations

import logging
from datetime import date

from agent.market.option_chain import OptionChain
from agent.market.snapshot import PortfolioSnapshot
from agent.options.structures import (
    HOLD_STRUCTURE,
    OptionLeg,
    OptionStructure,
    StructureName,
)

logger = logging.getLogger(__name__)


def select_structure(
    snapshot: PortfolioSnapshot,
    chain: OptionChain,
    bias: str,             # "bull" | "bear" | "neutral" from calling sub-agent
    iv_rank: float,        # 0–100
) -> OptionStructure:
    """
    Returns the best-fit option structure for the current market state.
    Returns HOLD_STRUCTURE if no liquid contracts exist.
    """
    spot = chain.spot_price
    portfolio_delta = snapshot.net_portfolio_delta

    structure_name: StructureName

    if bias == "bull" or portfolio_delta < -0.10:
        # Bullish or already short → collect premium selling OTM put
        structure_name = "sell_otm_put"
    elif bias == "bear" and iv_rank > 60:
        structure_name = "bear_put_spread"
    elif bias == "bear" and iv_rank <= 60:
        structure_name = "protective_put"
    elif bias == "neutral" and iv_rank > 60:
        structure_name = "collar"
    elif iv_rank > 70 and chain.underlying in ("SPY", "QQQ"):
        structure_name = "iron_condor"
    else:
        structure_name = "protective_put"  # safe default

    logger.info(
        "selector: bias=%s iv_rank=%.1f delta=%.3f → %s",
        bias, iv_rank, portfolio_delta, structure_name,
    )

    return _build_structure(structure_name, chain, spot)


def _build_structure(
    name: StructureName, chain: OptionChain, spot: float
) -> OptionStructure:
    today = date.today()

    if name == "protective_put":
        put = chain.nearest_put(target_strike_pct=0.97)
        if put is None:
            return HOLD_STRUCTURE
        leg = _make_leg(put, "buy", 1)
        return OptionStructure(
            name="protective_put",
            underlying=chain.underlying,
            legs=[leg],
            net_debit=put.mid,
            net_delta=put.delta or -0.30,
            max_loss=put.mid * 100,
            max_gain=spot * 100,  # theoretical: stock crashes to zero
            rationale=f"Buy ATM-3% put @ ${put.strike_price} for downside protection.",
        )

    elif name == "bear_put_spread":
        atm_put = chain.nearest_put(target_strike_pct=1.00)
        otm_put = chain.nearest_put(target_strike_pct=0.95)
        if atm_put is None or otm_put is None or atm_put.symbol == otm_put.symbol:
            return HOLD_STRUCTURE
        buy_leg = _make_leg(atm_put, "buy", 1)
        sell_leg = _make_leg(otm_put, "sell", 1)
        net_debit = atm_put.mid - otm_put.mid
        spread_width = atm_put.strike_price - otm_put.strike_price
        return OptionStructure(
            name="bear_put_spread",
            underlying=chain.underlying,
            legs=[buy_leg, sell_leg],
            net_debit=net_debit,
            net_delta=(atm_put.delta or -0.50) - (otm_put.delta or -0.30),
            max_loss=net_debit * 100,
            max_gain=(spread_width - net_debit) * 100,
            rationale=(
                f"Bear put spread: buy ${atm_put.strike_price} put, "
                f"sell ${otm_put.strike_price} put. Cost ${net_debit:.2f}/contract."
            ),
        )

    elif name == "collar":
        put = chain.nearest_put(target_strike_pct=0.97)
        call = chain.nearest_call(target_strike_pct=1.03)
        if put is None or call is None:
            return HOLD_STRUCTURE
        buy_leg = _make_leg(put, "buy", 1)
        sell_leg = _make_leg(call, "sell", 1)
        net_debit = put.mid - call.mid  # may be negative (credit collar)
        return OptionStructure(
            name="collar",
            underlying=chain.underlying,
            legs=[buy_leg, sell_leg],
            net_debit=net_debit,
            net_delta=(put.delta or -0.30) - (call.delta or 0.30),
            max_loss=net_debit * 100,
            max_gain=(call.strike_price - spot + (call.mid - put.mid)) * 100,
            rationale=(
                f"Collar: buy ${put.strike_price} put, sell ${call.strike_price} call. "
                f"Net {'debit' if net_debit > 0 else 'credit'} ${abs(net_debit):.2f}."
            ),
        )

    elif name == "sell_otm_put":
        otm_put = chain.nearest_put(target_strike_pct=0.95)
        if otm_put is None:
            return HOLD_STRUCTURE
        leg = _make_leg(otm_put, "sell", 1)
        return OptionStructure(
            name="sell_otm_put",
            underlying=chain.underlying,
            legs=[leg],
            net_debit=-otm_put.mid,  # credit received
            net_delta=-(otm_put.delta or -0.20),  # selling negative delta
            max_loss=(otm_put.strike_price - otm_put.mid) * 100,
            max_gain=otm_put.mid * 100,
            rationale=f"Sell OTM put @ ${otm_put.strike_price} to collect ${otm_put.mid:.2f} premium.",
        )

    elif name == "iron_condor":
        # Short condor: sell OTM put + buy further OTM put + sell OTM call + buy further OTM call
        sell_put = chain.nearest_put(target_strike_pct=0.95)
        buy_put = chain.nearest_put(target_strike_pct=0.93)
        sell_call = chain.nearest_call(target_strike_pct=1.05)
        buy_call = chain.nearest_call(target_strike_pct=1.07)
        if any(x is None for x in [sell_put, buy_put, sell_call, buy_call]):
            return HOLD_STRUCTURE
        assert sell_put and buy_put and sell_call and buy_call  # narrow type
        legs = [
            _make_leg(sell_put, "sell", 1),
            _make_leg(buy_put, "buy", 1),
            _make_leg(sell_call, "sell", 1),
            _make_leg(buy_call, "buy", 1),
        ]
        credit = (sell_put.mid - buy_put.mid) + (sell_call.mid - buy_call.mid)
        spread = sell_put.strike_price - buy_put.strike_price
        return OptionStructure(
            name="iron_condor",
            underlying=chain.underlying,
            legs=legs,
            net_debit=-credit,
            net_delta=0.0,
            max_loss=(spread - credit) * 100,
            max_gain=credit * 100,
            rationale=(
                f"Iron condor on {chain.underlying}: collect ${credit:.2f} premium, "
                f"max loss ${spread - credit:.2f}/contract."
            ),
        )

    return HOLD_STRUCTURE


def _make_leg(contract: object, side: str, qty: int) -> OptionLeg:
    """Helper: build an OptionLeg from a contract dataclass."""
    from agent.mcp_client.tools import OptionContract
    c: OptionContract = contract  # type: ignore[assignment]
    return OptionLeg(
        symbol=c.symbol,
        underlying=c.underlying,
        side=side,  # type: ignore[arg-type]
        option_type=c.option_type,  # type: ignore[arg-type]
        strike=c.strike_price,
        expiration=c.expiration_date.isoformat(),
        bid=c.bid,
        ask=c.ask,
        mid=c.mid,
        delta=c.delta or 0.0,
        qty=qty,
    )
