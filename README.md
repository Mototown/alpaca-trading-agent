# DAHA — Dynamic Adaptive Hedging Agent

> **Alpaca AI Trading Agents Hackathon 2026** · Solo dev · Paper trading · MIT

DAHA is a fully autonomous options trading agent that continuously hedges a long equity portfolio using a **Thesis Debate + Risk Arbiter** architecture. Every 5 minutes it gathers live market data via the **Alpaca MCP server**, runs three competing sub-agents (bull, bear, neutral), scores their proposals through a Risk Arbiter, and places multi-leg options hedges — all without human intervention.

**One-page write-up (required for judges):** [WRITEUP.md](WRITEUP.md) · **Submission packet:** [SUBMISSION.md](SUBMISSION.md)

---

## Architecture

```
GATHER (MCP) → ASSESS (Greeks/delta) → DEBATE (bull/bear/neutral)
    → SCORE (Risk Arbiter) → GUARDRAILS → EXECUTE (MCP) → LOG (SQLite)
```

Each tick is fully logged: proposals, scores, winner reasoning, and Greeks are persisted to SQLite and visible on the web dashboard.

---

## Quick Start

### 1. Prerequisites

- Python 3.12+
- [uv](https://astral.sh/uv) package manager
- Docker (for the Alpaca MCP server)
- Alpaca paper trading account ([sign up free](https://app.alpaca.markets))

### 2. Install

```bash
git clone https://github.com/Mototown/alpaca-trading-agent
cd alpaca-trading-agent
uv sync
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your Alpaca *paper* API key and secret
```

### 4. Start the Alpaca MCP server

```bash
docker run -d \
  --name alpaca-mcp \
  -e ALPACA_API_KEY=$ALPACA_API_KEY \
  -e ALPACA_SECRET_KEY=$ALPACA_SECRET_KEY \
  -e ALPACA_PAPER=true \
  -p 3001:3001 \
  ghcr.io/alpacahq/alpaca-mcp:latest
```

### 5. Run the agent

```bash
# Dry-run (no orders placed, great for testing)
uv run python -m agent.main --dry-run

# Single tick (run once and exit)
uv run python -m agent.main --once

# Full autonomous loop (5-minute schedule)
uv run python -m agent.main
```

Dashboard is available at **http://localhost:8080** once the agent starts.

---

## Project Structure

```
agent/
├── main.py                 # Entry point — APScheduler loop
├── config.py               # All settings via pydantic-settings + .env
│
├── mcp_client/
│   ├── client.py           # Async MCP session wrapper
│   └── tools.py            # Typed wrappers: get_account, get_positions,
│                           #   get_option_chain, place_option_order, …
│
├── market/
│   ├── snapshot.py         # PortfolioSnapshot — full state per tick
│   └── option_chain.py     # Fetch + filter + Greek-enrich option chains
│
├── debate/
│   ├── base.py             # SubAgent ABC + AgentProposal dataclass
│   ├── bull.py             # Bullish sub-agent (premium collection)
│   ├── bear.py             # Bearish sub-agent (protective puts/spreads)
│   ├── neutral.py          # Neutral sub-agent (collars, iron condors)
│   └── arbiter.py          # Risk Arbiter — scores and picks winner
│
├── options/
│   ├── structures.py       # OptionStructure + OptionLeg dataclasses
│   ├── selector.py         # Structure selection matrix
│   ├── sizer.py            # Kelly-inspired contract sizing
│   └── greeks.py           # Local Black-Scholes Greeks (no API call)
│
├── execution/
│   ├── executor.py         # Builds + submits MCP order payloads
│   └── position_manager.py # Roll/close expiring and profitable hedges
│
├── risk/
│   └── guardrails.py       # Hard limits, drawdown checks, circuit breakers
│
├── state/
│   ├── db.py               # aiosqlite init + schema
│   └── models.py           # insert_decision, insert_snapshot, queries
│
└── dashboard/
    ├── server.py            # FastAPI app factory
    └── routes.py            # / (HTML dashboard), /api/decisions, /api/portfolio

tests/
├── test_greeks.py          # Black-Scholes unit tests
├── test_arbiter.py         # Arbiter scoring tests
├── test_sizer.py           # Contract sizer tests
└── test_guardrails.py      # Guardrail / circuit-breaker tests
```

---

## Options Strategies

| Market Condition | IV Rank | Structure Used |
|---|---|---|
| Bearish / High vol | > 60 | Bear Put Spread (buy ATM, sell OTM) |
| Bearish / Low vol | < 40 | Protective Put (buy ATM-3% put) |
| Neutral / High vol | > 60 | Collar (put + short call, net credit) |
| Bullish | Any | Sell OTM Put (premium collection) |
| High vol / Index | > 70 | Iron Condor on SPY/QQQ |

---

## Risk Management

| Rule | Limit |
|---|---|
| Max premium per tick | 2% of equity |
| Max total option exposure | 10% of equity |
| Max contracts per symbol | 5 |
| Max net portfolio delta | ±0.30 |
| Daily loss circuit breaker | −3% of equity → halt |
| Drawdown pause | −8% from session high |
| Min option open interest | 100 contracts |
| Max bid/ask spread | $0.15 |

---

## Running Tests

```bash
uv run pytest tests/ -v
```

All tests run offline — no Alpaca credentials needed.

---

## Configuration Reference

All settings live in `.env` (see `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `ALPACA_API_KEY` | — | Paper trading API key |
| `ALPACA_SECRET_KEY` | — | Paper trading secret key |
| `WATCHLIST` | SPY,QQQ,AAPL,MSFT | Symbols to watch and hedge |
| `LOOP_INTERVAL_SECONDS` | 300 | Tick frequency |
| `MAX_HEDGE_PCT` | 0.02 | Max premium per tick as % of equity |
| `TARGET_DELTA` | 0.10 | Target net portfolio delta |
| `MAX_DELTA` | 0.30 | Hard delta limit before forced hedge |
| `DAILY_LOSS_LIMIT` | 0.03 | Fraction of equity — triggers halt |
| `DRY_RUN` | false | Log decisions without placing orders |
| `DASHBOARD_PORT` | 8080 | Web dashboard port |

---

*Built for the Alpaca AI Trading Agents Hackathon, Aug 28 – Sep 4 2026. Paper trading only. Not investment advice.*
