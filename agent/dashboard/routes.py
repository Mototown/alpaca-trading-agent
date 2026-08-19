"""
dashboard/routes.py — FastAPI route handlers.

Routes:
    GET /              — main dashboard (HTML)
    GET /api/decisions — last N decisions as JSON
    GET /api/portfolio — last N portfolio snapshots as JSON
    GET /api/health    — liveness probe
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

from agent.state.models import get_recent_decisions, get_recent_snapshots

router = APIRouter()


@router.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


@router.get("/api/decisions")
async def api_decisions(limit: int = 50) -> JSONResponse:
    rows = await get_recent_decisions(limit=limit)
    data = []
    for row in rows:
        d = dict(row)
        # Deserialise JSON blobs
        for key in ("proposals_json", "greeks_before"):
            if d.get(key):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        data.append(d)
    return JSONResponse(data)


@router.get("/api/portfolio")
async def api_portfolio(limit: int = 100) -> JSONResponse:
    rows = await get_recent_snapshots(limit=limit)
    data = []
    for row in rows:
        d = dict(row)
        if d.get("positions_json"):
            try:
                d["positions_json"] = json.loads(d["positions_json"])
            except (json.JSONDecodeError, TypeError):
                pass
        data.append(d)
    return JSONResponse(data)


@router.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Serve the inline dashboard HTML — no template engine needed."""
    decisions_raw = await get_recent_decisions(limit=20)
    snapshots_raw = await get_recent_snapshots(limit=50)

    decisions = []
    for row in decisions_raw:
        d = dict(row)
        if d.get("proposals_json"):
            try:
                d["proposals_json"] = json.loads(d["proposals_json"])
            except Exception:
                pass
        decisions.append(d)

    snapshots = [dict(r) for r in snapshots_raw]

    # Build equity chart data
    chart_labels = [s["ts"][11:19] for s in reversed(snapshots)]  # HH:MM:SS
    chart_equity = [s.get("equity", 0) for s in reversed(snapshots)]
    chart_delta = [s.get("net_delta", 0) for s in reversed(snapshots)]

    latest = snapshots[0] if snapshots else {}

    html = _render_dashboard(decisions, latest, chart_labels, chart_equity, chart_delta)
    return HTMLResponse(content=html)


def _action_badge(action: str) -> str:
    colors = {
        "hold": "#6b7280",
        "protective_put": "#3b82f6",
        "bear_put_spread": "#8b5cf6",
        "collar": "#0ea5e9",
        "sell_otm_put": "#10b981",
        "iron_condor": "#f59e0b",
        "covered_call": "#06b6d4",
    }
    color = colors.get(action, "#6b7280")
    return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:600">{action.replace("_"," ").upper()}</span>'


def _render_dashboard(
    decisions: list[dict],
    latest_snapshot: dict,
    chart_labels: list,
    chart_equity: list,
    chart_delta: list,
) -> str:
    equity = latest_snapshot.get("equity", 0)
    day_pl = latest_snapshot.get("day_pl", 0)
    net_delta = latest_snapshot.get("net_delta", 0)
    net_vega = latest_snapshot.get("net_vega", 0)
    pl_color = "#10b981" if day_pl >= 0 else "#ef4444"
    delta_color = "#10b981" if abs(net_delta) < 0.15 else ("#f59e0b" if abs(net_delta) < 0.25 else "#ef4444")

    decision_rows = ""
    for d in decisions:
        ts = d.get("ts", "")[:19].replace("T", " ")
        action = d.get("action", "hold")
        underlying = d.get("underlying", "")
        contracts = d.get("contracts", 0)
        winner = d.get("winner_agent", "")
        score = d.get("arbiter_score")
        score_str = f"{score:.1f}" if score is not None else "—"
        reasoning = (d.get("reasoning") or "")[:120]
        blocked = d.get("guardrail_block")
        dry = d.get("dry_run", 0)
        flags = ""
        if blocked:
            flags += ' <span style="color:#ef4444;font-size:11px">⛔ BLOCKED</span>'
        if dry:
            flags += ' <span style="color:#f59e0b;font-size:11px">DRY RUN</span>'
        decision_rows += f"""
        <tr>
          <td style="color:#6b7280;white-space:nowrap">{ts}</td>
          <td><strong>{underlying}</strong></td>
          <td>{_action_badge(action)}{flags}</td>
          <td style="text-align:center">{contracts}</td>
          <td style="color:#6b7280;font-size:12px">{winner}</td>
          <td style="text-align:right;font-family:monospace">{score_str}</td>
          <td style="color:#57606a;font-size:12px;max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{reasoning}">{reasoning}</td>
        </tr>"""

    labels_json = json.dumps(chart_labels)
    equity_json = json.dumps(chart_equity)
    delta_json = json.dumps(chart_delta)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DAHA — Alpaca Hackathon Agent</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system,"Segoe UI",system-ui,sans-serif; background:#f7f8fa; color:#1f2328; font-size:14px; line-height:1.6; }}
  .header {{ background:#1f2328; color:#fff; padding:16px 24px; display:flex; align-items:center; justify-content:space-between; }}
  .header h1 {{ font-size:18px; font-weight:700; letter-spacing:.5px; }}
  .header .sub {{ font-size:12px; color:#8b949e; margin-top:2px; }}
  .badge-live {{ background:#10b981; color:#fff; font-size:11px; font-weight:700; padding:2px 8px; border-radius:10px; }}
  .container {{ max-width:1200px; margin:0 auto; padding:24px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:16px; margin-bottom:24px; }}
  .kpi {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; }}
  .kpi .label {{ font-size:11px; color:#57606a; text-transform:uppercase; letter-spacing:.5px; }}
  .kpi .value {{ font-size:24px; font-weight:700; margin-top:4px; font-family:monospace; }}
  .charts {{ display:grid; grid-template-columns:2fr 1fr; gap:16px; margin-bottom:24px; }}
  .card {{ background:#fff; border:1px solid #e5e7eb; border-radius:8px; padding:16px; }}
  .card h2 {{ font-size:13px; font-weight:600; color:#57606a; text-transform:uppercase; letter-spacing:.5px; margin-bottom:12px; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ font-size:11px; font-weight:600; color:#57606a; text-transform:uppercase; letter-spacing:.5px; padding:8px 12px; text-align:left; border-bottom:2px solid #e5e7eb; }}
  td {{ padding:8px 12px; border-bottom:1px solid #f3f4f6; vertical-align:middle; }}
  tr:hover td {{ background:#f9fafb; }}
  canvas {{ max-height:200px; }}
  @media(max-width:700px){{ .kpis{{grid-template-columns:1fr 1fr;}} .charts{{grid-template-columns:1fr;}} }}
</style>
</head>
<body>
<div class="header">
  <div>
    <div class="header h1">DAHA — Dynamic Adaptive Hedging Agent</div>
    <div class="sub">Alpaca AI Trading Agents Hackathon 2026 · Paper Trading</div>
  </div>
  <span class="badge-live">● LIVE</span>
</div>
<div class="container">
  <div class="kpis">
    <div class="kpi"><div class="label">Portfolio Equity</div><div class="value">${equity:,.2f}</div></div>
    <div class="kpi"><div class="label">Day P&amp;L</div><div class="value" style="color:{pl_color}">${day_pl:+,.2f}</div></div>
    <div class="kpi"><div class="label">Net Delta</div><div class="value" style="color:{delta_color}">{net_delta:+.3f}</div></div>
    <div class="kpi"><div class="label">Net Vega</div><div class="value">{net_vega:.2f}</div></div>
  </div>
  <div class="charts">
    <div class="card">
      <h2>Portfolio Equity</h2>
      <canvas id="equityChart"></canvas>
    </div>
    <div class="card">
      <h2>Net Delta</h2>
      <canvas id="deltaChart"></canvas>
    </div>
  </div>
  <div class="card">
    <h2>Recent Decisions</h2>
    <div style="overflow-x:auto">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Symbol</th><th>Action</th><th>Qty</th>
            <th>Winner</th><th style="text-align:right">Score</th><th>Reasoning</th>
          </tr>
        </thead>
        <tbody>{decision_rows or '<tr><td colspan="7" style="text-align:center;color:#6b7280;padding:24px">No decisions yet — waiting for first tick.</td></tr>'}</tbody>
      </table>
    </div>
  </div>
  <div style="margin-top:24px;text-align:center;font-size:12px;color:#57606a;padding-top:16px;border-top:1px solid #e5e7eb">
    Refresh every 30s &nbsp;·&nbsp; <a href="/api/decisions" style="color:#3b82d4">JSON API</a> &nbsp;·&nbsp; Made with IBM Bob
  </div>
</div>
<script>
const labels = {labels_json};
const equity = {equity_json};
const delta = {delta_json};
const gridColor = 'rgba(0,0,0,0.05)';

new Chart(document.getElementById('equityChart'), {{
  type: 'line',
  data: {{ labels, datasets: [{{ label: 'Equity', data: equity, borderColor: '#3b82f6', backgroundColor: 'rgba(59,130,246,0.08)', tension: 0.3, pointRadius: 2, fill: true }}] }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ grid: {{ color: gridColor }}, ticks: {{ callback: v => '$' + v.toLocaleString() }} }}, x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8 }} }} }} }}
}});

new Chart(document.getElementById('deltaChart'), {{
  type: 'line',
  data: {{ labels, datasets: [
    {{ label: 'Delta', data: delta, borderColor: '#8b5cf6', backgroundColor: 'rgba(139,92,246,0.08)', tension: 0.3, pointRadius: 2, fill: true }},
    {{ label: 'Target', data: labels.map(() => {settings.target_delta}), borderColor: '#10b981', borderDash: [4,4], pointRadius: 0 }},
    {{ label: 'Max', data: labels.map(() => {settings.max_delta}), borderColor: '#ef4444', borderDash: [4,4], pointRadius: 0 }},
  ] }},
  options: {{ responsive: true, plugins: {{ legend: {{ display: true, position: 'bottom', labels: {{ boxWidth: 12, font: {{ size: 11 }} }} }} }}, scales: {{ y: {{ grid: {{ color: gridColor }} }}, x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 6 }} }} }} }}
}});

// Auto-refresh every 30 seconds
setTimeout(() => location.reload(), 30000);
</script>
</body>
</html>"""
