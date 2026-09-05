# Submission packet — DAHA

Official lablab submit window: **Fri 4 Sep 2026, 11:00 AM EDT** (15:00 UTC).  
Live board marked the event **ended / judging in progress** after 15:10 UTC. Community hearts on the live page can still move the social prize if a project card exists.

## Paste-ready lablab fields

**Title:** DAHA — Dynamic Adaptive Hedging Agent

**Short description:** Autonomous options hedging agent. Every 5 minutes it debates bull / bear / neutral theses, a Risk Arbiter scores them, hard guardrails veto bad risk, and Alpaca MCP places multi-leg paper orders. Full decision tape on a live dashboard.

**Long description:**
DAHA treats a long equity book as something to *hedge*, not something to day-trade. It pulls account, positions, snapshots, and option chains through Alpaca’s official MCP server, computes portfolio delta/vega and IV rank, then runs three sub-agents. The Risk Arbiter picks a winner only if expected P&L survives cost, drawdown, and delta-violation penalties. Guardrails then cap premium (2% / tick), exposure (10%), contracts, delta (±0.30), daily loss (−3%), and liquidity. Losing the debate means HOLD. Every tick is written to SQLite and rendered at `/` plus `/api/decisions`.

Built solo for the Alpaca AI Trading Agents Hackathon. Options are the product, not a checkbox: protective puts, bear put spreads, collars, short OTM puts, and iron condors on SPY/QQQ.

**Tags:** Alpaca, MCP, options, FastAPI, Python, autonomous-agent, paper-trading

**Public repo:** https://github.com/Mototown/alpaca-trading-agent

**Write-up:** https://github.com/Mototown/alpaca-trading-agent/blob/master/WRITEUP.md

**Demo URL:** `http://localhost:8080` after `uv run python -m agent.main` (deploy to Railway/Fly if you want a public link for judges).

**Paper account ID:** *fill in your dedicated competition paper account ID — $100k start, options enabled, one email.*

## Official requirements checklist

- [x] Public GitHub repo
- [x] Autonomous loop on Alpaca Trading API + MCP
- [x] Options are the core strategy (multi-leg)
- [x] One-page write-up: AI logic, risk gates, Alpaca infra (`WRITEUP.md`)
- [x] MIT license
- [ ] Dedicated competition paper account (one email)
- [ ] Competition account reset to **$100,000**
- [ ] Options trading level enabled on that paper account
- [ ] Paper account ID pasted on the lablab form
- [ ] Cover image + optional 60–90s demo video
- [ ] Optional: up to 5 posts tagging @lablabai and @AlpacaHQ (community prize)

## If the form is already locked

1. Confirm whether a draft/final project card exists on https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live
2. If yes: drop the write-up link in comments / social and collect hearts.
3. If no: this repo is still the clean artifact for judges who have the URL, for your portfolio, and as a starter for the next lablab sprint.

## Social draft (X / LinkedIn)

DAHA — an autonomous options hedging agent for the Alpaca × lablab.ai hackathon.

Every 5 minutes: MCP gather → Greeks → bull/bear/neutral debate → Risk Arbiter → guardrails → multi-leg paper order. HOLD is a first-class action.

Repo: https://github.com/Mototown/alpaca-trading-agent

@lablabai @AlpacaHQ #AlpacaHackathon
