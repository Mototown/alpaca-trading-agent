# Alpaca Hackathon — Technical Plan
## "Dynamic Adaptive Hedging Agent" (DAHA)

> Solo dev · 7-day build · Paper trading · Alpaca MCP + alpaca-py

---

## 1. Concept & Why This Agent Wins

**DAHA** owns a small portfolio of long equity positions and continuously hedges it using
options structures that it selects, prices, and manages autonomously.  Every 5 minutes it:

1. Re-reads its portfolio and market conditions via Alpaca MCP tools.
2. Runs a *Thesis Debate* inner loop — bull / bear / neutral sub-agents each propose an
   action, a Risk Arbiter scores them, and the highest-scoring action wins.
3. Places or adjusts options hedges (protective puts, collars, bear put spreads) through
   Alpaca's options API.
4. Writes a structured log entry explaining *why* it acted (visible in the UI).

This hits every judging axis:
| Criterion | How DAHA scores |
|---|---|
| Autonomy | 5-min loop, no human input needed after start |
| Options usage | Multi-leg (collar = call + put), spread sizing |
| Risk intelligence | Greeks-aware position sizing, delta-neutral targeting |
| Presentation | Per-decision reasoning log + web dashboard |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     DAHA Main Loop                      │
│  ┌──────────┐  ┌───────────────┐  ┌──────────────────┐  │
│  │  Market  │  │  Thesis       │  │  Risk Arbiter    │  │
│  │  Reader  │→ │  Debate       │→ │  (scorer)        │  │
│  │ (MCP)    │  │  Bull/Bear/   │  │  Greeks + Kelly  │  │
│  └──────────┘  │  Neutral      │  └────────┬─────────┘  │
│                └───────────────┘           │             │
│  ┌──────────────────────────────────────────▼──────────┐ │
│  │              Order Executor (Alpaca MCP / alpaca-py) │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │       State Store  (SQLite, in-memory cache)         │ │
│  └──────────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────────┐ │
│  │       Reasoning Logger  →  FastAPI dashboard         │ │
│  └──────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## 3. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Fastest iteration; rich finance libs |
| Alpaca integration | `alpaca-py` + Alpaca MCP server | MCP for tool-call demo; SDK for reliability fallback |
| AI orchestration | `openai` (GPT-4o) or `anthropic` (Claude 3.5) | Structured function calling for debate sub-agents |
| Options math | `py_vollib` + `scipy` | Black-Scholes Greeks locally, no round-trip |
| State | SQLite via `aiosqlite` | Zero-infra, portable, queryable |
| Dashboard | FastAPI + Jinja2 + HTMX | Lightweight, no JS build step |
| Scheduling | `APScheduler` | Cron-like loop, easy to demo |
| Config | `pydantic-settings` + `.env` | Type-safe, 12-factor |
| Testing | `pytest` + `pytest-asyncio` | Unit-testable sub-agents |
| Packaging | `uv` + `pyproject.toml` | Modern, fast installs |

### MCP server note
Alpaca publishes an official MCP server image at
`ghcr.io/alpacahq/alpaca-mcp` (or via `npx @alpacahq/mcp`).
The agent calls it as an MCP client — every market data fetch and order
placement is a named MCP tool call, which is visible in logs and scores
highly on the "Alpaca integration" criterion.

---

## 4. Exact Decision Loop (every 5 minutes)

```
TICK START
│
├─ 1. GATHER  ──────────────────────────────────────────────────
│      MCP: get_account()            → equity, buying_power
│      MCP: get_positions()          → holdings, unrealised P&L
│      MCP: get_market_snapshot(     → last price, IV rank, IV %ile
│              symbols=watchlist)
│      MCP: get_option_chain(        → strikes, expiries, bid/ask,
│              symbol, expiry_range) →   delta, gamma, theta, vega
│
├─ 2. ASSESS  ──────────────────────────────────────────────────
│      Compute:
│        portfolio_delta  = Σ(position_delta)
│        portfolio_vega   = Σ(position_vega)
│        hedge_gap        = target_delta – portfolio_delta
│        iv_rank          = (current_iv – 52w_low) /
│                           (52w_high – 52w_low)
│
├─ 3. DEBATE  ──────────────────────────────────────────────────
│      Each sub-agent receives: snapshot + Greeks + current hedges
│      │
│      ├─ BULL  sub-agent:  proposes reducing / removing hedges,
│      │                    possibly selling covered calls
│      │
│      ├─ BEAR  sub-agent:  proposes buying protective puts or
│      │                    bear put spreads
│      │
│      └─ NEUTRAL sub-agent: proposes collars or iron condors
│                             if IV rank > 50
│
├─ 4. SCORE  ───────────────────────────────────────────────────
│      Risk Arbiter evaluates each proposal:
│        score = expected_pnl_after_hedge
│                – drawdown_penalty(max_loss)
│                – cost_penalty(premium / portfolio_equity)
│                – delta_violation_penalty(|resulting_delta| > limit)
│      Winning proposal must beat min_score threshold.
│      If no proposal beats threshold → HOLD.
│
├─ 5. EXECUTE  ─────────────────────────────────────────────────
│      If action ≠ HOLD:
│        Close conflicting existing option positions
│        MCP: place_option_order(legs, qty, order_type)
│        MCP: get_order_status(order_id) → confirm fill
│
├─ 6. LOG  ─────────────────────────────────────────────────────
│      Insert DecisionRecord into SQLite:
│        (timestamp, action, reasoning, proposals, scores,
│         greeks_before, greeks_after, expected_cost)
│
└─ TICK END  (sleep until next 5-min mark)
```

---

## 5. Options Structures — Selection & Sizing

### Structure Selection Matrix

| Market Condition | IV Rank | Portfolio Delta | Selected Structure |
|---|---|---|---|
| Bearish / High vol | > 60 | Long | **Bear Put Spread** (buy ATM put, sell OTM put) |
| Bearish / Low vol | < 40 | Long | **Protective Put** (buy ATM-5% put) |
| Neutral / High vol | > 60 | Any | **Collar** (buy put + sell call, net credit) |
| Bullish / Any | Any | Short | **Sell OTM Put** to collect premium while bullish |
| Volatile / High vol | > 70 | Flat | **Iron Condor** (on index ETF like SPY) |

### Sizing Algorithm (Kelly-inspired)

```python
def size_hedge(portfolio_equity, max_hedge_pct, greeks, structure):
    # Never spend more than max_hedge_pct (default 2%) per tick
    max_premium = portfolio_equity * max_hedge_pct

    # Target: delta-neutralise within ±0.10 delta tolerance
    delta_gap = abs(target_delta - portfolio_delta)
    contracts_by_delta = ceil(delta_gap / abs(structure.delta_per_contract))

    # Cap by premium budget
    cost_per_contract = structure.ask * 100  # 1 contract = 100 shares
    contracts_by_budget = floor(max_premium / cost_per_contract)

    return min(contracts_by_delta, contracts_by_budget, MAX_CONTRACTS)
```

### Expiry Selection

- **Default**: nearest expiry ≥ 21 DTE (avoid gamma risk of near-expiry)
- **High IV rank**: prefer 30–45 DTE (more premium to sell)
- **Emergency hedge**: nearest weekly if delta gap > 0.30

---

## 6. Risk Management Rules

### Hard Limits (enforced before every order)
```
MAX_OPTION_SPEND_PER_TICK   = 2% of portfolio equity
MAX_TOTAL_OPTION_EXPOSURE   = 10% of portfolio equity
MAX_CONTRACTS_PER_SYMBOL    = 5
MAX_DELTA_EXPOSURE          = ±0.30 (net portfolio delta)
MIN_OPTION_LIQUIDITY        = bid/ask spread < $0.15 OR < 5% of mid
MIN_OPEN_INTEREST           = 100 contracts
MAX_POSITIONS               = 20 (equity + options combined)
```

### Soft Guardrails (trigger warning + reduced sizing)
```
PORTFOLIO_DRAWDOWN_ALERT    = –5% from session high → halve sizing
PORTFOLIO_DRAWDOWN_PAUSE    = –8% from session high → no new positions
IV_SPIKE_CAUTION            = IV rank > 80 → only selling structures
```

### Circuit Breakers
```
DAILY_LOSS_LIMIT            = –3% of equity → stop all activity, alert log
API_ERROR_CONSECUTIVE       = 3 failures → pause loop, log critical
MARKET_CLOSED               = skip tick, log "market closed"
```

---

## 7. File / Folder Structure

```
alpaca-trading-agent/
├── pyproject.toml              # deps, scripts
├── .env.example                # API keys template
├── README.md
├── PLAN.md                     # this file
│
├── agent/
│   ├── __init__.py
│   ├── main.py                 # entry point, APScheduler loop
│   ├── config.py               # pydantic-settings Config model
│   │
│   ├── mcp_client/
│   │   ├── __init__.py
│   │   ├── client.py           # MCP session wrapper (connect/call)
│   │   └── tools.py            # typed wrappers: get_account(),
│   │                           #   get_positions(), place_order(), etc.
│   │
│   ├── market/
│   │   ├── __init__.py
│   │   ├── snapshot.py         # MarketSnapshot dataclass, fetch logic
│   │   └── option_chain.py     # OptionChain fetch + filtering
│   │
│   ├── debate/
│   │   ├── __init__.py
│   │   ├── base.py             # SubAgent ABC
│   │   ├── bull.py             # BullSubAgent
│   │   ├── bear.py             # BearSubAgent
│   │   ├── neutral.py          # NeutralSubAgent
│   │   └── arbiter.py          # RiskArbiter — scores proposals
│   │
│   ├── options/
│   │   ├── __init__.py
│   │   ├── structures.py       # OptionStructure dataclasses
│   │   ├── selector.py         # picks structure from matrix
│   │   ├── sizer.py            # Kelly / budget sizing
│   │   └── greeks.py           # local Black-Scholes Greeks
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── executor.py         # builds + submits MCP order calls
│   │   └── position_manager.py # close/roll existing hedges
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   └── guardrails.py       # hard limits, circuit breakers
│   │
│   ├── state/
│   │   ├── __init__.py
│   │   ├── db.py               # aiosqlite init + migrations
│   │   └── models.py           # DecisionRecord, PositionRecord ORM
│   │
│   └── dashboard/
│       ├── __init__.py
│       ├── server.py           # FastAPI app
│       ├── routes.py           # /decisions, /portfolio, /health
│       └── templates/
│           ├── base.html
│           └── index.html
│
└── tests/
    ├── test_arbiter.py
    ├── test_greeks.py
    ├── test_sizer.py
    └── test_guardrails.py
```

---

## 8. Minimal Viable Version (Days 1–2)

**Goal**: Agent runs, reads portfolio, debates, places one real option order on paper.

### Day 1 — Foundation
- [ ] `pyproject.toml` with all deps
- [ ] `.env.example` + `config.py`
- [ ] `mcp_client/client.py` + `mcp_client/tools.py` — connect and call MCP
- [ ] `market/snapshot.py` — fetch account + positions + one symbol snapshot
- [ ] `state/db.py` — SQLite init
- [ ] `agent/main.py` — bare loop that gathers data and logs a JSON record
- [ ] Confirm data flows end-to-end with `python -m agent.main --dry-run`

### Day 2 — First real decision
- [ ] `options/greeks.py` — local Black-Scholes
- [ ] `options/structures.py` + `options/selector.py`
- [ ] `debate/bull.py`, `debate/bear.py`, `debate/neutral.py` (simple heuristic, no LLM yet)
- [ ] `debate/arbiter.py` — score + select winner
- [ ] `risk/guardrails.py` — hard limit checks
- [ ] `execution/executor.py` — place a single protective put on paper
- [ ] `agent/main.py` — wire full loop

**Day 2 exit criteria**: agent autonomously places a protective put on the largest
equity position every tick without human intervention.

---

## 9. Later Polish (Days 3–7)

| Day | Feature |
|---|---|
| 3 | Replace heuristic sub-agents with LLM calls (GPT-4o structured output) |
| 3 | Multi-leg collar and bear put spread execution |
| 4 | `dashboard/` — FastAPI UI showing decisions + P&L |
| 4 | `position_manager.py` — roll / close stale hedges |
| 5 | IV rank calculation from historical data |
| 5 | Kelly-fraction position sizing with full Greeks |
| 6 | Iron condor on SPY when IV rank > 70 |
| 6 | Automated end-of-day position report |
| 7 | Polish, README, demo video, deploy to fly.io or Railway |

---

## 10. Environment Setup

```bash
# Install uv (fast Python package manager)
curl -Lsf https://astral.sh/uv/install.sh | sh

# Create project
uv init alpaca-trading-agent
cd alpaca-trading-agent
uv add alpaca-py openai anthropic py-vollib scipy \
       aiosqlite fastapi uvicorn jinja2 httpx \
       apscheduler pydantic-settings python-dotenv \
       mcp pytest pytest-asyncio

# Run Alpaca MCP server (Docker)
docker run -d \
  -e ALPACA_API_KEY=$ALPACA_API_KEY \
  -e ALPACA_SECRET_KEY=$ALPACA_SECRET_KEY \
  -e ALPACA_PAPER=true \
  -p 3001:3001 \
  ghcr.io/alpacahq/alpaca-mcp:latest

# Start agent
uv run python -m agent.main
```

---

## 11. Key Design Decisions

1. **MCP-first, SDK-fallback**: All calls go through MCP tool calls for demo value.
   If a tool is missing from the MCP server, fall back to `alpaca-py` directly,
   but log the fallback so it's visible.

2. **Reasoning is a first-class output**: Every decision writes a structured JSON blob
   with `proposals`, `scores`, `winner`, `reasoning`. This is the "AI brain" judges see.

3. **No over-engineering**: Sub-agents in Days 1–2 are pure Python heuristics.
   LLM calls are dropped in on Day 3 with zero structural changes.

4. **Defensive execution**: Every order is preceded by guardrail checks.
   The circuit breaker is a simple `is_halted: bool` flag in the state store.

5. **Testable without live API**: All MCP calls go through a thin wrapper that can
   be replaced with a mock. Tests run offline.
