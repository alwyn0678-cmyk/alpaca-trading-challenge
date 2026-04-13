#!/usr/bin/env python3
"""
Alpaca 30-Day Challenge — Live Dashboard
Run: python3 dashboard.py
Opens at: http://localhost:5050
"""

import json
import os
import requests
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR        = Path(__file__).parent
STRATEGY_FILE   = BASE_DIR / "strategy.json"
TRADE_LOG_FILE  = BASE_DIR / "trade_log.json"
PERFORMANCE_FILE= BASE_DIR / "performance.json"
ENV_FILE        = BASE_DIR / ".env"
PORT            = 5050

# ── Load credentials ───────────────────────────────────────────────────────
def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

_env       = load_env()
API_KEY    = _env.get("ALPACA_API_KEY", "")
SECRET_KEY = _env.get("ALPACA_SECRET_KEY", "")
BASE_URL   = _env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets/v2")
DATA_URL   = "https://data.alpaca.markets/v2"
HEADERS    = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}

def alpaca_get(path, base=None):
    try:
        url = f"{base or BASE_URL}{path}"
        r   = requests.get(url, headers=HEADERS, timeout=8)
        return r.json()
    except:
        return {}

def get_live_data():
    account   = alpaca_get("/account")
    positions = alpaca_get("/positions")
    orders    = alpaca_get("/orders?status=open")
    clock     = alpaca_get("/clock")

    equity       = float(account.get("equity", 0))
    last_equity  = float(account.get("last_equity", equity))
    cash         = float(account.get("cash", 0))
    buying_power = float(account.get("buying_power", 0))
    day_pnl      = equity - last_equity
    is_open      = clock.get("is_open", False)

    perf = json.loads(PERFORMANCE_FILE.read_text()) if PERFORMANCE_FILE.exists() else {}
    start_equity = perf.get("start_equity", equity)
    total_pnl    = equity - start_equity

    strategy = json.loads(STRATEGY_FILE.read_text()) if STRATEGY_FILE.exists() else {}
    triggers = strategy.get("triggers", [])

    trades = []
    if TRADE_LOG_FILE.exists():
        for line in TRADE_LOG_FILE.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except:
                    pass

    # Enrich positions with live price
    pos_list = positions if isinstance(positions, list) else []
    orders_list = orders if isinstance(orders, list) else []

    # Get prices for active triggers
    trigger_prices = {}
    active_syms = [t["symbol"] for t in triggers if t.get("status") == "active"]
    if active_syms:
        syms_str = ",".join(active_syms)
        snaps = alpaca_get(f"/stocks/snapshots?symbols={syms_str}&feed=iex", base=DATA_URL)
        if isinstance(snaps, dict):
            for sym, data in snaps.items():
                if isinstance(data, dict):
                    lt = data.get("latestTrade", {})
                    trigger_prices[sym] = lt.get("p", 0)

    return {
        "equity":       equity,
        "cash":         cash,
        "buying_power": buying_power,
        "day_pnl":      day_pnl,
        "total_pnl":    total_pnl,
        "start_equity": start_equity,
        "is_open":      is_open,
        "positions":    pos_list,
        "orders":       orders_list,
        "triggers":     triggers,
        "trades":       trades,
        "perf":         perf,
        "trigger_prices": trigger_prices,
        "challenge_start": strategy.get("challenge_start", ""),
        "challenge_end":   strategy.get("challenge_end", ""),
        "now":          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

# ── HTML Dashboard ─────────────────────────────────────────────────────────
def render_html(d):
    pnl_color  = "#22c55e" if d["total_pnl"] >= 0 else "#ef4444"
    day_color  = "#22c55e" if d["day_pnl"] >= 0 else "#ef4444"
    mkt_badge  = ('<span style="background:#22c55e;color:#fff;padding:3px 10px;border-radius:20px;font-size:12px">● MARKET OPEN</span>'
                  if d["is_open"] else
                  '<span style="background:#6b7280;color:#fff;padding:3px 10px;border-radius:20px;font-size:12px">○ MARKET CLOSED</span>')

    total_return_pct = (d["total_pnl"] / d["start_equity"] * 100) if d["start_equity"] else 0

    # ── Positions table ────────────────────────────────────────────────────
    pos_rows = ""
    for p in d["positions"]:
        sym    = p.get("symbol","")
        qty    = p.get("qty","")
        entry  = float(p.get("avg_entry_price", 0))
        curr   = float(p.get("current_price", 0))
        pnl    = float(p.get("unrealized_pl", 0))
        pnl_p  = float(p.get("unrealized_plpc", 0)) * 100
        c      = "#22c55e" if pnl >= 0 else "#ef4444"
        pos_rows += f"""
        <tr>
          <td><b>{sym}</b></td><td>{qty}</td>
          <td>${entry:.2f}</td><td>${curr:.2f}</td>
          <td style="color:{c}">${pnl:+.2f} ({pnl_p:+.1f}%)</td>
        </tr>"""

    if not pos_rows:
        pos_rows = '<tr><td colspan="5" style="color:#6b7280;text-align:center">No open positions</td></tr>'

    # ── Triggers table ─────────────────────────────────────────────────────
    status_colors = {
        "active": "#3b82f6", "in_trade": "#22c55e",
        "stopped_out": "#ef4444", "closed": "#6b7280"
    }
    trig_rows = ""
    for t in d["triggers"]:
        sym    = t.get("symbol","")
        stype  = t.get("setup_type","")
        status = t.get("status","")
        sc     = status_colors.get(status, "#6b7280")
        entry  = t.get("entry_price") or t.get("pullback_low","—")
        stop   = t.get("stop","—")
        t1     = t.get("target_1","—")
        curr   = d["trigger_prices"].get(sym, "—")
        curr_s = f"${curr:.2f}" if isinstance(curr, (int, float)) and curr else "—"
        trail  = f"${t['trailing_stop']:.2f}" if t.get("trailing_stop") else "—"
        trig_rows += f"""
        <tr>
          <td><b>{sym}</b></td>
          <td>{stype}</td>
          <td><span style="background:{sc};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px">{status}</span></td>
          <td>{curr_s}</td>
          <td>${entry}</td>
          <td>${stop}</td>
          <td>${t1}</td>
          <td>{trail}</td>
        </tr>"""

    if not trig_rows:
        trig_rows = '<tr><td colspan="8" style="color:#6b7280;text-align:center">No triggers loaded</td></tr>'

    # ── Trade log ──────────────────────────────────────────────────────────
    trade_rows = ""
    action_colors = {
        "ENTRY": "#3b82f6", "TARGET_1": "#22c55e",
        "STOP_LOSS": "#ef4444", "TRAILING_STOP": "#f59e0b"
    }
    for tr in reversed(d["trades"][-30:]):
        action = tr.get("action","")
        sym    = tr.get("symbol","")
        qty    = tr.get("qty","")
        price  = tr.get("price","")
        pnl    = tr.get("pnl", "")
        ts     = tr.get("timestamp","")[:16].replace("T"," ")
        ac     = action_colors.get(action, "#6b7280")
        pnl_s  = f'<span style="color:{"#22c55e" if float(pnl)>=0 else "#ef4444"}">${float(pnl):+.2f}</span>' if pnl != "" else "—"
        trade_rows += f"""
        <tr>
          <td>{ts}</td>
          <td><span style="background:{ac};color:#fff;padding:2px 8px;border-radius:12px;font-size:11px">{action}</span></td>
          <td><b>{sym}</b></td><td>{qty}</td><td>${price}</td><td>{pnl_s}</td>
        </tr>"""

    if not trade_rows:
        trade_rows = '<tr><td colspan="6" style="color:#6b7280;text-align:center">No trades yet</td></tr>'

    # ── Daily PnL chart data ───────────────────────────────────────────────
    daily = d["perf"].get("daily_pnl", {})
    chart_labels = list(daily.keys())[-14:]
    chart_values = [daily[k] for k in chart_labels]
    chart_colors = ['"#22c55e"' if v >= 0 else '"#ef4444"' for v in chart_values]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpaca 30-Day Challenge Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
          background: #0f172a; color: #e2e8f0; min-height: 100vh; }}
  .header {{ background: #1e293b; border-bottom: 1px solid #334155;
             padding: 16px 32px; display: flex; align-items: center;
             justify-content: space-between; }}
  .header h1 {{ font-size: 20px; font-weight: 700; color: #f1f5f9; }}
  .header .meta {{ font-size: 12px; color: #94a3b8; }}
  .grid {{ display: grid; grid-template-columns: repeat(5,1fr);
           gap: 16px; padding: 24px 32px 0; }}
  .card {{ background: #1e293b; border: 1px solid #334155;
           border-radius: 12px; padding: 20px; }}
  .card .label {{ font-size: 11px; color: #94a3b8; text-transform: uppercase;
                  letter-spacing: .05em; margin-bottom: 6px; }}
  .card .value {{ font-size: 26px; font-weight: 700; }}
  .card .sub   {{ font-size: 12px; color: #64748b; margin-top: 4px; }}
  .section {{ margin: 24px 32px; }}
  .section h2 {{ font-size: 14px; font-weight: 600; color: #94a3b8;
                 text-transform: uppercase; letter-spacing: .05em;
                 margin-bottom: 12px; }}
  table {{ width: 100%; border-collapse: collapse;
           background: #1e293b; border-radius: 12px; overflow: hidden; }}
  th {{ background: #0f172a; color: #94a3b8; font-size: 11px;
        text-transform: uppercase; letter-spacing: .05em;
        padding: 10px 14px; text-align: left; }}
  td {{ padding: 10px 14px; font-size: 13px;
        border-top: 1px solid #1e293b; }}
  tr:hover td {{ background: #273548; }}
  .chart-wrap {{ background: #1e293b; border: 1px solid #334155;
                 border-radius: 12px; padding: 20px; margin: 24px 32px; }}
  .refresh {{ font-size: 11px; color: #64748b; }}
  a {{ color: #60a5fa; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
<meta http-equiv="refresh" content="120">
</head>
<body>

<div class="header">
  <div>
    <h1>Alpaca 30-Day Challenge</h1>
    <div class="meta">{d['challenge_start']} → {d['challenge_end']} &nbsp;|&nbsp; {d['now']} &nbsp;|&nbsp; {mkt_badge}</div>
  </div>
  <div class="refresh">Auto-refreshes every 2 min &nbsp;|&nbsp; <a href="/">Refresh now</a></div>
</div>

<!-- KPI cards -->
<div class="grid">
  <div class="card">
    <div class="label">Account Equity</div>
    <div class="value">${d['equity']:,.2f}</div>
    <div class="sub">Started at ${d['start_equity']:,.2f}</div>
  </div>
  <div class="card">
    <div class="label">Total P&L</div>
    <div class="value" style="color:{pnl_color}">${d['total_pnl']:+,.2f}</div>
    <div class="sub" style="color:{pnl_color}">{total_return_pct:+.2f}% return</div>
  </div>
  <div class="card">
    <div class="label">Today's P&L</div>
    <div class="value" style="color:{day_color}">${d['day_pnl']:+,.2f}</div>
    <div class="sub">Daily limit: -${d['start_equity']*0.015:,.0f}</div>
  </div>
  <div class="card">
    <div class="label">Buying Power</div>
    <div class="value">${d['buying_power']:,.2f}</div>
    <div class="sub">Cash: ${d['cash']:,.2f}</div>
  </div>
  <div class="card">
    <div class="label">Total Trades</div>
    <div class="value">{len(d['trades'])}</div>
    <div class="sub">Wins: {d['perf'].get('wins',0)} | Losses: {d['perf'].get('losses',0)}</div>
  </div>
</div>

<!-- Daily P&L chart -->
<div class="chart-wrap">
  <h2 style="font-size:14px;font-weight:600;color:#94a3b8;text-transform:uppercase;
              letter-spacing:.05em;margin-bottom:14px">Daily P&L (Last 14 Days)</h2>
  <canvas id="pnlChart" height="80"></canvas>
</div>

<!-- Open Positions -->
<div class="section">
  <h2>Open Positions</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th><th>Unrealized P&L</th>
    </tr></thead>
    <tbody>{pos_rows}</tbody>
  </table>
</div>

<!-- Active Triggers -->
<div class="section">
  <h2>Active Triggers & Watchlist</h2>
  <table>
    <thead><tr>
      <th>Symbol</th><th>Setup</th><th>Status</th><th>Current</th>
      <th>Entry</th><th>Stop</th><th>T1</th><th>Trail Stop</th>
    </tr></thead>
    <tbody>{trig_rows}</tbody>
  </table>
</div>

<!-- Trade Log -->
<div class="section">
  <h2>Trade Log (Last 30)</h2>
  <table>
    <thead><tr>
      <th>Time (UTC)</th><th>Action</th><th>Symbol</th><th>Qty</th><th>Price</th><th>P&L</th>
    </tr></thead>
    <tbody>{trade_rows}</tbody>
  </table>
</div>

<div style="height:40px"></div>

<script>
const labels = {json.dumps(chart_labels)};
const values = {json.dumps(chart_values)};
const colors = [{",".join(chart_colors)}];
const ctx = document.getElementById("pnlChart").getContext("2d");
new Chart(ctx, {{
  type: "bar",
  data: {{
    labels: labels,
    datasets: [{{
      label: "Daily P&L ($)",
      data: values,
      backgroundColor: colors,
      borderRadius: 4,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => " $" + ctx.parsed.y.toFixed(2)
        }}
      }}
    }},
    scales: {{
      x: {{ grid: {{ color: "#1e293b" }}, ticks: {{ color: "#94a3b8", font: {{ size: 11 }} }} }},
      y: {{ grid: {{ color: "#334155" }}, ticks: {{ color: "#94a3b8",
               callback: v => "$" + v.toFixed(0) }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

# ── HTTP Server ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access logs

    def do_GET(self):
        if self.path == "/":
            data  = get_live_data()
            html  = render_html(data)
            body  = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/data":
            data = get_live_data()
            # Convert non-serializable items
            body = json.dumps({
                k: v for k, v in data.items()
                if not isinstance(v, list) or k in ("triggers", "trades", "perf")
            }, default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

if __name__ == "__main__":
    print(f"")
    print(f"  Alpaca 30-Day Challenge Dashboard")
    print(f"  ──────────────────────────────────")
    print(f"  Open in browser:  http://localhost:{PORT}")
    print(f"  Press Ctrl+C to stop")
    print(f"")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
