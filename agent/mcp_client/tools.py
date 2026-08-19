"""
mcp_client/tools.py — Typed wrappers around every Alpaca MCP tool call.

Each function:
  1. Calls the MCP server via MCPClient.call_tool().
  2. Falls back to alpaca-py if MCPUnavailableError is raised.
  3. Returns a typed dataclass or dict.

This is intentionally thin — no business logic lives here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

from agent.config import settings
from agent.mcp_client.client import MCPClient, MCPUnavailableError

logger = logging.getLogger(__name__)

# ── alpaca-py fallback clients (lazy-initialised) ────────────────────────────
_trading_client: TradingClient | None = None
_data_client: StockHistoricalDataClient | None = None


def _get_trading_client() -> TradingClient:
    global _trading_client
    if _trading_client is None:
        _trading_client = TradingClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
            paper=settings.alpaca_paper,
        )
    return _trading_client


def _get_data_client() -> StockHistoricalDataClient:
    global _data_client
    if _data_client is None:
        _data_client = StockHistoricalDataClient(
            api_key=settings.alpaca_api_key,
            secret_key=settings.alpaca_secret_key,
        )
    return _data_client


# ── Typed return types ───────────────────────────────────────────────────────

@dataclass
class AccountInfo:
    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    day_pl: float


@dataclass
class Position:
    symbol: str
    qty: float
    side: str          # "long" | "short"
    market_value: float
    unrealized_pl: float
    avg_entry_price: float
    asset_class: str   # "us_equity" | "us_option"
    # Options-specific (None for equity)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass
class OptionContract:
    symbol: str
    underlying: str
    expiration_date: date
    strike_price: float
    option_type: str   # "call" | "put"
    bid: float
    ask: float
    mid: float
    open_interest: int
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    implied_volatility: float | None = None


@dataclass
class MarketSnapshot:
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume: int
    vwap: float | None = None
    implied_volatility_rank: float | None = None  # 0-100
    implied_volatility_pct: float | None = None   # annualised IV


@dataclass
class OrderResult:
    order_id: str
    status: str
    symbol: str
    legs: list[dict[str, Any]] = field(default_factory=list)


# ── Tool wrappers ────────────────────────────────────────────────────────────

async def get_account(client: MCPClient) -> AccountInfo:
    """Fetch account equity, cash, and day P&L."""
    try:
        data = await client.call_tool("get_account", {})
        return AccountInfo(
            equity=float(data["equity"]),
            cash=float(data["cash"]),
            buying_power=float(data["buying_power"]),
            portfolio_value=float(data["portfolio_value"]),
            day_pl=float(data.get("equity", 0) - data.get("last_equity", data["equity"])),
        )
    except MCPUnavailableError:
        logger.warning("MCP unavailable — falling back to alpaca-py for get_account")
        tc = _get_trading_client()
        acct = tc.get_account()
        return AccountInfo(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
            portfolio_value=float(acct.portfolio_value),
            day_pl=float(acct.equity) - float(acct.last_equity),
        )


async def get_positions(client: MCPClient) -> list[Position]:
    """Return all open positions (equity + options)."""
    try:
        data = await client.call_tool("get_positions", {})
        return [_parse_position(p) for p in data]
    except MCPUnavailableError:
        logger.warning("MCP unavailable — falling back to alpaca-py for get_positions")
        tc = _get_trading_client()
        positions = tc.get_all_positions()
        return [_parse_position(p.__dict__) for p in positions]


def _parse_position(p: dict[str, Any]) -> Position:
    return Position(
        symbol=p["symbol"],
        qty=float(p.get("qty", p.get("quantity", 0))),
        side=p.get("side", "long"),
        market_value=float(p.get("market_value", 0)),
        unrealized_pl=float(p.get("unrealized_pl", 0)),
        avg_entry_price=float(p.get("avg_entry_price", p.get("cost_basis", 0))),
        asset_class=p.get("asset_class", "us_equity"),
        delta=p.get("delta"),
        gamma=p.get("gamma"),
        theta=p.get("theta"),
        vega=p.get("vega"),
    )


async def get_market_snapshot(
    client: MCPClient, symbol: str
) -> MarketSnapshot:
    """Fetch latest quote + basic stats for a symbol."""
    try:
        data = await client.call_tool("get_market_snapshot", {"symbol": symbol})
        return MarketSnapshot(
            symbol=symbol,
            last_price=float(data.get("latestTrade", {}).get("p", 0)),
            bid=float(data.get("latestQuote", {}).get("bp", 0)),
            ask=float(data.get("latestQuote", {}).get("ap", 0)),
            volume=int(data.get("dailyBar", {}).get("v", 0)),
            vwap=float(data.get("dailyBar", {}).get("vw", 0)) or None,
        )
    except MCPUnavailableError:
        logger.warning("MCP unavailable — falling back to alpaca-py for snapshot(%s)", symbol)
        dc = _get_data_client()
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
        quotes = dc.get_stock_latest_quote(req)
        q = quotes[symbol]
        return MarketSnapshot(
            symbol=symbol,
            last_price=float(q.ask_price),  # best approximation without trade
            bid=float(q.bid_price),
            ask=float(q.ask_price),
            volume=0,
        )


async def get_option_chain(
    client: MCPClient,
    underlying: str,
    expiry_min_dte: int = 21,
    expiry_max_dte: int = 60,
) -> list[OptionContract]:
    """
    Fetch option contracts for ``underlying`` within the DTE window.
    Returns contracts sorted by expiration then strike.
    """
    try:
        data = await client.call_tool(
            "get_option_chain",
            {
                "underlying_symbol": underlying,
                "expiry_min_dte": expiry_min_dte,
                "expiry_max_dte": expiry_max_dte,
            },
        )
        return [_parse_option(c) for c in data]
    except MCPUnavailableError:
        logger.warning("MCP unavailable — falling back to alpaca-py for option_chain(%s)", underlying)
        tc = _get_trading_client()
        from datetime import date as _date, timedelta
        today = _date.today()
        req = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            expiration_date_gte=today + timedelta(days=expiry_min_dte),
            expiration_date_lte=today + timedelta(days=expiry_max_dte),
        )
        contracts = tc.get_option_contracts(req).option_contracts
        return [
            OptionContract(
                symbol=c.symbol,
                underlying=underlying,
                expiration_date=c.expiration_date,
                strike_price=float(c.strike_price),
                option_type=c.type.value,
                bid=0.0,  # alpaca-py contract object doesn't carry live quotes
                ask=0.0,
                mid=0.0,
                open_interest=int(c.open_interest or 0),
            )
            for c in contracts
        ]


def _parse_option(c: dict[str, Any]) -> OptionContract:
    bid = float(c.get("bid", 0))
    ask = float(c.get("ask", 0))
    return OptionContract(
        symbol=c["symbol"],
        underlying=c.get("underlying_symbol", c.get("underlying", "")),
        expiration_date=date.fromisoformat(c["expiration_date"]),
        strike_price=float(c["strike_price"]),
        option_type=c.get("type", c.get("option_type", "put")),
        bid=bid,
        ask=ask,
        mid=(bid + ask) / 2 if (bid + ask) > 0 else 0.0,
        open_interest=int(c.get("open_interest", 0)),
        delta=c.get("delta"),
        gamma=c.get("gamma"),
        theta=c.get("theta"),
        vega=c.get("vega"),
        implied_volatility=c.get("implied_volatility"),
    )


async def place_option_order(
    client: MCPClient,
    legs: list[dict[str, Any]],
    order_type: str = "limit",
    time_in_force: str = "day",
    limit_price: float | None = None,
) -> OrderResult:
    """
    Place a multi-leg option order via MCP.

    ``legs`` format::

        [
            {"symbol": "SPY240920P00520000", "side": "buy", "ratio_qty": 1},
            {"symbol": "SPY240920P00510000", "side": "sell", "ratio_qty": 1},
        ]
    """
    payload: dict[str, Any] = {
        "legs": legs,
        "order_type": order_type,
        "time_in_force": time_in_force,
    }
    if limit_price is not None:
        payload["limit_price"] = limit_price

    try:
        data = await client.call_tool("place_option_order", payload)
        return OrderResult(
            order_id=data.get("id", "unknown"),
            status=data.get("status", "pending"),
            symbol=data.get("symbol", legs[0]["symbol"] if legs else ""),
            legs=data.get("legs", legs),
        )
    except MCPUnavailableError as exc:
        logger.error("MCP unavailable for place_option_order — cannot fall back safely: %s", exc)
        raise


async def close_position(client: MCPClient, symbol: str) -> None:
    """Close / cancel an open position by symbol."""
    try:
        await client.call_tool("close_position", {"symbol": symbol})
    except MCPUnavailableError:
        logger.warning("MCP unavailable — falling back to alpaca-py for close_position(%s)", symbol)
        tc = _get_trading_client()
        tc.close_position(symbol)
