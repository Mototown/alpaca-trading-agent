# DAHA — One-Page Write-up

**Project:** Dynamic Adaptive Hedging Agent  
**Hackathon:** Alpaca AI Trading Agents Hackathon (lablab.ai × Alpaca), 28 Aug – 4 Sep 2026  
**Builder:** Mototown (solo) · Paper trading only  
**Repo:** https://github.com/Mototown/alpaca-trading-agent

## What it is

DAHA is an autonomous options agent that owns a long equity book (default `SPY, QQQ, AAPL, MSFT`) and re-hedges it every five minutes. It does not chase directional alpha. It debates *how* to hedge, then places multi-leg options only when a Risk Arbiter and hard guardrails both agree.

Loop:

```
GATHER (Alpaca MCP) → ASSESS (Greeks / delta gap / IV rank)
  → DEBATE (bull / bear / neutral) → SCORE (Risk Arbiter)
  → GUARDRAILS → EXECUTE (MCP multi-leg) → LOG (SQLite + dashboard)
```

## AI logic

Three competing sub-agents receive the same portfolio snapshot, option chain, and IV rank:

| Agent | Bias | Typical structure |
|---|---|---|
| Bull | Reduce hedges / collect premium | Sell OTM put |
| Bear | Protect a long book | Protective put or bear put spread |
| Neutral | Harvest elevated IV | Collar or iron condor on SPY/QQQ |

Each returns an `AgentProposal` (structure, contracts, confidence, expected P&L, max loss, reasoning). The Risk Arbiter scores:

`score = expected_pnl − drawdown_penalty(max_loss) − cost_penalty(premium / equity) − delta_violation_penalty`

If no proposal beats the minimum score, the tick is **HOLD**. Heuristic agents ship today so the loop is deterministic and testable offline; LLM structured-output slots (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) are wired in config for the same `propose()` interface.

Sizing is Kelly-inspired and budget-capped: contracts = min(delta-gap need, 2% equity premium budget, 5 contracts/symbol).

## Risk gates (cannot be overridden by the debate)

Enforced in `agent/risk/guardrails.py` before every order:

- Max premium per tick: **2% of equity**
- Max total option exposure: **10% of equity**
- Max contracts per symbol: **5**
- Max net portfolio delta: **±0.30** (target **+0.10**)
- Daily loss circuit breaker: **−3% equity → halt**
- Drawdown pause: **−8% from session high → no new risk**
- Liquidity: open interest ≥ **100**, bid/ask ≤ **$0.15**
- Prefer ≥ **21 DTE** (weekly only if emergency delta gap > 0.30)
- Defined-risk structures preferred (spreads, collars, condors)
- `DRY_RUN=true` logs the full decision without sending orders

Every tick writes proposals, scores, winner reasoning, Greeks-before, and any guardrail block to SQLite. The FastAPI dashboard at `:8080` shows equity, day P&L, net delta/vega, and the decision tape.

## Alpaca infrastructure

- **Paper Trading API** via official MCP server (`MCP_SERVER_URL`, Docker `ghcr.io/alpacahq/alpaca-mcp`) for account, positions, snapshots, option chains, and multi-leg orders.
- **`alpaca-py` fallback** if an MCP tool is missing; fallback is logged so judges can see the path.
- Options are first-class: chain filter + local Black-Scholes Greeks (`agent/options/greeks.py`) so the agent does not wait on an extra API hop to score delta/vega.
- Config is 12-factor (`pydantic-settings` + `.env`). Never commit live keys.

## How to judge it in five minutes

```bash
uv sync
cp .env.example .env          # paper keys only
uv run pytest tests/ -v       # 32 offline tests: Greeks, arbiter, sizer, guardrails
uv run python -m agent.main --dry-run --once
# dashboard: http://localhost:8080
```

This is paper trading, not investment advice. Starting competition balance should be **$100,000** on a dedicated paper account.
