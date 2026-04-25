#!/usr/bin/env python3
"""
Alpaca 30-Day Challenge — Live Dashboard
Run: python3 dashboard.py
Opens at: http://localhost:5050
"""

import json
import os
import re
import requests
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

BASE_DIR        = Path(__file__).parent
STRATEGY_FILE   = BASE_DIR / "strategy.json"
TRADE_LOG_FILE  = BASE_DIR / "trade_log.json"
PERFORMANCE_FILE= BASE_DIR / "performance.json"
HEARTBEAT_FILE  = BASE_DIR / "heartbeat.json"
ENV_FILE        = BASE_DIR / ".env"
PORT            = 5050

# Show a warning banner if the bot data is older than this many minutes
STALE_THRESHOLD_MINUTES = 90

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
    url = f"{base or BASE_URL}{path}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
    except requests.RequestException as e:
        print(f"[alpaca_get] network error {url}: {e}")
        return {}
    try:
        body = r.json()
    except ValueError:
        print(f"[alpaca_get] non-JSON response {url}: HTTP {r.status_code}")
        return {}
    if r.status_code >= 400:
        print(f"[alpaca_get] HTTP {r.status_code} {url}: {body}")
    return body

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
    start_equity  = perf.get("start_equity", 10000.0)
    account_start = perf.get("account_start_equity", equity)
    total_pnl     = equity - account_start
    challenge_equity = round(start_equity + total_pnl, 2)
    open_cost     = sum(float(p.get("cost_basis", 0)) for p in (positions if isinstance(positions, list) else []))
    challenge_bp  = round(max(0.0, challenge_equity - open_cost), 2)

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

    # Live prices for every trigger (active + in_trade); position symbols come from the positions endpoint
    trigger_prices = {}
    trig_syms = sorted({t.get("symbol", "") for t in triggers if t.get("symbol")})
    if trig_syms:
        syms_str = ",".join(trig_syms)
        snaps = alpaca_get(f"/stocks/snapshots?symbols={syms_str}&feed=iex", base=DATA_URL)
        if isinstance(snaps, dict):
            for sym, snap in snaps.items():
                if not isinstance(snap, dict):
                    continue
                # Prefer latestTrade; fall back to minuteBar close or dailyBar close for off-hours
                price = (snap.get("latestTrade") or {}).get("p")
                if not price:
                    price = (snap.get("minuteBar") or {}).get("c")
                if not price:
                    price = (snap.get("dailyBar") or {}).get("c")
                if price:
                    trigger_prices[sym] = float(price)

    # Portfolio history drives the real equity curve (account-level, then rebased to the $10k challenge)
    port_hist = alpaca_get("/account/portfolio/history?period=1M&timeframe=1D")

    # Last run timestamp — prefer heartbeat.json (local run), fall back to strategy.json (GHA)
    heartbeat = {}
    if HEARTBEAT_FILE.exists():
        try:
            heartbeat = json.loads(HEARTBEAT_FILE.read_text())
        except Exception:
            pass

    last_run_iso = heartbeat.get("last_run_utc") or strategy.get("last_run", "")
    last_run_str = ""
    last_run_ago = ""
    last_run_stale = False
    last_run_mins = None
    if last_run_iso:
        try:
            lr = datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
            last_run_str = lr.strftime("%Y-%m-%d %H:%M UTC")
            delta = datetime.now(timezone.utc) - lr
            last_run_mins = int(delta.total_seconds() // 60)
            if last_run_mins < 60:
                last_run_ago = f"{last_run_mins}m ago"
            else:
                last_run_ago = f"{last_run_mins // 60}h {last_run_mins % 60}m ago"
            last_run_stale = last_run_mins > STALE_THRESHOLD_MINUTES
        except Exception:
            last_run_str = last_run_iso[:16]

    unrealized_pl = sum(float(p.get("unrealized_pl", 0) or 0) for p in pos_list)

    return {
        "equity":          equity,
        "challenge_equity": challenge_equity,
        "challenge_bp":    challenge_bp,
        "cash":            cash,
        "buying_power":    buying_power,
        "day_pnl":         day_pnl,
        "total_pnl":       total_pnl,
        "unrealized_pl":   unrealized_pl,
        "start_equity":    start_equity,
        "account_start_equity": account_start,
        "is_open":      is_open,
        "positions":    pos_list,
        "orders":       orders_list,
        "triggers":     triggers,
        "trades":       trades,
        "perf":         perf,
        "port_hist":    port_hist if isinstance(port_hist, dict) else {},
        "trigger_prices": trigger_prices,
        "challenge_start": strategy.get("challenge_start", ""),
        "challenge_end":   strategy.get("challenge_end", ""),
        "last_run_str":   last_run_str,
        "last_run_ago":   last_run_ago,
        "last_run_stale": last_run_stale,
        "last_run_mins":  last_run_mins,
        "hb_status":      heartbeat.get("status", "unknown"),
        "activity_log": get_activity_log(),
        "now":          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

# ── Activity log ──────────────────────────────────────────────────────────
def get_activity_log():
    """Parse bot.log into a list of recent activity entries (last 2 runs)."""
    log_file = BASE_DIR / "bot.log"
    entries = []
    if not log_file.exists():
        return entries

    lines = log_file.read_text(errors="replace").splitlines()

    # Find the start of the last 2 runs (=== dividers)
    run_starts = [i for i, l in enumerate(lines) if "=" * 20 in l and i+1 < len(lines) and "ALPACA BOT" in lines[i+1]]
    if not run_starts:
        slice_lines = lines[-40:]
    else:
        start_idx = run_starts[-2] if len(run_starts) >= 2 else run_starts[-1]
        slice_lines = lines[start_idx:]

    skip_patterns = re.compile(r"={10,}|Market check starting")
    level_map = {"INFO": "#60a5fa", "WARNING": "#f59e0b", "ERROR": "#ef4444"}

    for line in slice_lines:
        line = line.strip()
        if not line or skip_patterns.search(line):
            continue
        parts = line.split(" | ", 2)
        if len(parts) == 3:
            ts_raw, level, msg = parts
            ts = ts_raw[:16]
            color = level_map.get(level.strip(), "#94a3b8")
        else:
            ts, color, msg = "", "#94a3b8", line
        entries.append({"ts": ts, "color": color, "msg": msg.strip()})

    return entries[-60:]


# ── HTML Dashboard ─────────────────────────────────────────────────────────
def render_html(d):
    from datetime import date as _date, timedelta as _td

    # ── Color helpers ──────────────────────────────────────────────────────
    pnl_color = "#22c55e" if d["total_pnl"] >= 0 else "#ef4444"
    day_color  = "#22c55e" if d["day_pnl"] >= 0 else "#ef4444"
    pnl_class  = "profit" if d["total_pnl"] >= 0 else "loss"
    day_class  = "profit" if d["day_pnl"] >= 0 else "loss"

    # ── Market badge ───────────────────────────────────────────────────────
    mkt_badge = (
        '<span class="mkt-badge open">&#9679; MARKET OPEN</span>'
        if d["is_open"] else
        '<span class="mkt-badge closed">&#9675; MARKET CLOSED</span>'
    )

    # ── Staleness banner ────────────────────────────────────────────────────
    # Only warn when the market is currently open — weekends/after-hours staleness is expected
    if d.get("last_run_stale") and d.get("is_open"):
        mins = d.get("last_run_mins", "?")
        staleness_banner = f"""
<div class="stale-banner">
  &#9888; Market is open but bot data is <strong>{mins} minutes old</strong> (threshold: {STALE_THRESHOLD_MINUTES}m).
  Local cron and/or GitHub Actions may not be running.
  Last recorded status: <strong>{d.get('hb_status', 'unknown')}</strong>.
  Run <code>python bot.py</code> manually to refresh, or check cron/GHA.
  <a href="/" style="color:inherit;margin-left:12px;text-decoration:underline">&#8635; Refresh page</a>
</div>"""
    else:
        staleness_banner = ""

    # ── Performance metrics ────────────────────────────────────────────────
    wins            = d["perf"].get("wins", 0)
    losses          = d["perf"].get("losses", 0)
    total_completed = wins + losses
    win_rate        = (wins / total_completed * 100) if total_completed > 0 else 0
    total_return_pct = (d["total_pnl"] / d["start_equity"] * 100) if d["start_equity"] else 0
    win_rate_color  = "var(--green)" if win_rate >= 50 else "var(--red)"
    entry_count     = sum(1 for t in d["trades"] if t.get("action") == "ENTRY")
    # Daily loss limit matches bot.py MAX_DAILY_LOSS_PCT = 4% of the $10k challenge capital
    daily_loss_limit = d["start_equity"] * 0.04
    unrealized      = d.get("unrealized_pl", 0.0)
    unrealized_sign = "+" if unrealized >= 0 else "−"

    # ── Challenge progress ─────────────────────────────────────────────────
    # Use UTC throughout so the chart/progress match the UTC-labeled "now" header
    today_utc = datetime.now(timezone.utc).date()
    today_str = today_utc.isoformat()
    c_start   = d.get("challenge_start", "")
    if c_start:
        start_d      = _date.fromisoformat(c_start)
        days_elapsed = max(0, (today_utc - start_d).days + 1)
        progress_pct = min(100.0, days_elapsed / 30 * 100)
        days_left    = max(0, 30 - days_elapsed)
    else:
        days_elapsed = 0
        progress_pct = 0.0
        days_left    = 30

    # ── Chart data ─────────────────────────────────────────────────────────
    # Build 30-day labels anchored to challenge_start
    if c_start:
        start_d = _date.fromisoformat(c_start)
        chart_labels = [(start_d + _td(days=i)).isoformat() for i in range(30)]
    else:
        chart_labels = []

    # Prefer Alpaca's portfolio/history for a real daily equity curve;
    # rebase account equity into the $10k challenge view:
    #   challenge_equity(day) = start_equity + (account_equity(day) - account_start_equity)
    ph            = d.get("port_hist") or {}
    ph_equity     = ph.get("equity") or []
    ph_ts         = ph.get("timestamp") or []
    account_start = d.get("account_start_equity") or 0
    history_by_date = {}
    for ts, eq in zip(ph_ts, ph_equity):
        if not eq:  # skip pre-funding zeros
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        history_by_date[dt] = round(d["start_equity"] + (float(eq) - account_start), 2)

    # Fallback to realized daily_pnl if portfolio history is unavailable
    daily = d["perf"].get("daily_pnl", {})

    equity_curve = []
    running = d["start_equity"]
    for lbl in chart_labels:
        if lbl in history_by_date:
            running = history_by_date[lbl]
            equity_curve.append(running)
        elif lbl < today_str:
            # Before today, carry forward — or apply realized PnL if no history
            running = round(running + daily.get(lbl, 0), 2)
            equity_curve.append(running)
        elif lbl == today_str:
            # Today: always anchor to the live challenge equity
            running = round(d["challenge_equity"], 2)
            equity_curve.append(running)
        else:
            equity_curve.append(None)

    # Peak (high water mark) curve — tracks the maximum seen so far
    peak_curve, peak = [], d["start_equity"]
    for v in equity_curve:
        if v is None:
            peak_curve.append(None)
        else:
            peak = max(peak, v)
            peak_curve.append(peak)

    # Daily P&L bars — derived from the equity curve so it matches the line exactly
    daily_pnl_bars = []
    prev = d["start_equity"]
    for v in equity_curve:
        if v is None:
            daily_pnl_bars.append(None)
        else:
            daily_pnl_bars.append(round(v - prev, 2))
            prev = v

    # Short x-axis labels: "Apr 13" instead of ISO
    short_labels = []
    for lbl in chart_labels:
        try:
            short_labels.append(_date.fromisoformat(lbl).strftime("%b %-d"))
        except ValueError:
            short_labels.append(lbl)

    today_idx = chart_labels.index(today_str) if today_str in chart_labels else -1


    # Milestone dates
    if c_start:
        start_d = _date.fromisoformat(c_start)
        milestones = {
            (start_d + _td(days=0)).isoformat():  "D1",
            (start_d + _td(days=6)).isoformat():  "D7",
            (start_d + _td(days=13)).isoformat(): "D14",
            (start_d + _td(days=20)).isoformat(): "D21",
            (start_d + _td(days=29)).isoformat(): "D30",
        }
    else:
        milestones = {}

    # ── Positions table ────────────────────────────────────────────────────
    pos_rows = ""
    for p in d["positions"]:
        sym   = p.get("symbol", "")
        qty   = p.get("qty", "")
        entry = float(p.get("avg_entry_price", 0))
        curr  = float(p.get("current_price", 0))
        pnl   = float(p.get("unrealized_pl", 0))
        pnl_p = float(p.get("unrealized_plpc", 0)) * 100
        c     = "#22c55e" if pnl >= 0 else "#ef4444"
        sign  = "+" if pnl >= 0 else ""
        pos_rows += f"""
        <tr>
          <td><span class="sym">{sym}</span></td>
          <td class="mono">{qty}</td>
          <td class="mono">${entry:.2f}</td>
          <td class="mono">${curr:.2f}</td>
          <td class="mono" style="color:{c}">{sign}${abs(pnl):.2f} ({pnl_p:+.1f}%)</td>
        </tr>"""

    if not pos_rows:
        pos_rows = '<tr><td colspan="5" class="empty-row">No open positions</td></tr>'

    # ── Triggers table ─────────────────────────────────────────────────────
    status_styles = {
        "active":      ("status-blue",  "Active"),
        "in_trade":    ("status-green", "In Trade"),
        "stopped_out": ("status-red",   "Stopped"),
        "closed":      ("status-gray",  "Closed"),
    }
    trig_rows = ""
    for t in d["triggers"]:
        sym    = t.get("symbol", "")
        stype  = t.get("setup_type", "")
        status = t.get("status", "")
        cls, label = status_styles.get(status, ("status-gray", status.title()))
        entry  = t.get("entry_price") or t.get("pullback_low", "—")
        stop   = t.get("stop", "—")
        t1     = t.get("target_1", "—")
        curr   = d["trigger_prices"].get(sym, "—")
        curr_s = f"${curr:.2f}" if isinstance(curr, (int, float)) and curr else "—"
        trail  = f"${t['trailing_stop']:.2f}" if t.get("trailing_stop") else "—"
        trig_rows += f"""
        <tr>
          <td><span class="sym">{sym}</span></td>
          <td class="setup-type">{stype}</td>
          <td><span class="status-pill {cls}">{label}</span></td>
          <td class="mono">{curr_s}</td>
          <td class="mono">${entry}</td>
          <td class="mono">${stop}</td>
          <td class="mono">${t1}</td>
          <td class="mono">{trail}</td>
        </tr>"""

    if not trig_rows:
        trig_rows = '<tr><td colspan="8" class="empty-row">No triggers loaded</td></tr>'

    # ── Trade log ──────────────────────────────────────────────────────────
    action_styles = {
        "ENTRY":         "action-blue",
        "TARGET_1":      "action-green",
        "STOP_LOSS":     "action-red",
        "TRAILING_STOP": "action-amber",
    }
    trade_rows = ""
    for tr in reversed(d["trades"][-30:]):
        action = tr.get("action", "")
        sym    = tr.get("symbol", "")
        qty    = tr.get("qty", "")
        price  = tr.get("price", "")
        pnl    = tr.get("pnl", "")
        ts     = tr.get("timestamp", "")[:16].replace("T", " ")
        acls   = action_styles.get(action, "action-gray")
        if pnl != "":
            pv = float(pnl)
            pc = "#22c55e" if pv >= 0 else "#ef4444"
            pnl_s = f'<span class="mono" style="color:{pc}">${pv:+.2f}</span>'
        else:
            pnl_s = '<span class="text-muted">—</span>'
        trade_rows += f"""
        <tr>
          <td class="mono text-muted">{ts}</td>
          <td><span class="action-pill {acls}">{action}</span></td>
          <td><span class="sym">{sym}</span></td>
          <td class="mono">{qty}</td>
          <td class="mono">${price}</td>
          <td>{pnl_s}</td>
        </tr>"""

    if not trade_rows:
        trade_rows = '<tr><td colspan="6" class="empty-row">No trades yet</td></tr>'

    # ── Activity log ───────────────────────────────────────────────────────
    activity_rows = ""
    prev_ts_date = None
    for entry in reversed(d["activity_log"]):
        ts   = entry["ts"]
        msg  = entry["msg"].replace("<", "&lt;").replace(">", "&gt;")
        col  = entry["color"]
        ts_date = ts[:10] if ts else ""
        if ts_date and ts_date != prev_ts_date and prev_ts_date is not None:
            activity_rows += '<div class="run-sep"></div>'
        prev_ts_date = ts_date
        activity_rows += f'<div class="log-line"><span class="log-ts">{ts}</span><span class="log-msg" style="color:{col}">{msg}</span></div>'

    if not activity_rows:
        activity_rows = '<p class="empty-log">No activity recorded yet — bot hasn\'t run during market hours.</p>'

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Alpaca 30-Day Challenge</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
/* ── Reset & Tokens ──────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --bg:           #050d1a;
  --surface:      #0c1526;
  --surface-2:    #111d30;
  --border:       #1a2d42;
  --border-hover: #2a4060;
  --text:         #e2e8f0;
  --text-2:       #94a3b8;
  --text-muted:   #475569;
  --blue:         #3b82f6;
  --blue-dim:     rgba(59,130,246,.12);
  --green:        #22c55e;
  --green-dim:    rgba(34,197,94,.12);
  --red:          #ef4444;
  --red-dim:      rgba(239,68,68,.12);
  --amber:        #f59e0b;
  --amber-dim:    rgba(245,158,11,.12);
  --radius:       12px;
  --font-mono:    'Fira Code', 'SF Mono', 'Consolas', monospace;
  --font-sans:    'Fira Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}}

html, body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.5;
  min-height: 100vh;
}}

/* ── Header ──────────────────────────────────────── */
.header {{
  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(5,13,26,.88);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  border-bottom: 1px solid var(--border);
  padding: 13px 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}}

.header-brand {{
  display: flex;
  align-items: center;
  gap: 12px;
}}

.header-icon {{
  width: 34px;
  height: 34px;
  background: var(--blue-dim);
  border: 1px solid rgba(59,130,246,.25);
  border-radius: 9px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}}

.header-title {{
  font-size: 16px;
  font-weight: 700;
  color: #f1f5f9;
  letter-spacing: -0.02em;
}}

.header-meta {{
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 1px;
  font-family: var(--font-mono);
}}

.header-right {{
  display: flex;
  align-items: center;
  gap: 14px;
  flex-shrink: 0;
}}

.mkt-badge {{
  font-size: 10px;
  font-weight: 700;
  padding: 4px 11px;
  border-radius: 20px;
  letter-spacing: .06em;
  text-transform: uppercase;
}}
.mkt-badge.open {{
  background: var(--green-dim);
  color: var(--green);
  border: 1px solid rgba(34,197,94,.3);
}}
.mkt-badge.closed {{
  background: rgba(71,85,105,.12);
  color: var(--text-2);
  border: 1px solid rgba(71,85,105,.25);
}}

/* ── Staleness banner ─────────────────────────────── */
.stale-banner {{
  background: rgba(245,158,11,.12);
  border-bottom: 1px solid rgba(245,158,11,.3);
  color: #fbbf24;
  font-size: 12px;
  padding: 10px 28px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.stale-banner code {{
  background: rgba(245,158,11,.2);
  padding: 1px 6px;
  border-radius: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
}}

.refresh-link {{
  font-size: 11px;
  color: var(--text-muted);
  text-decoration: none;
  transition: color .15s;
  cursor: pointer;
}}
.refresh-link:hover {{ color: var(--blue); }}

/* ── Challenge Progress ──────────────────────────── */
.challenge-bar {{
  padding: 18px 28px 0;
}}

.challenge-header {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 8px;
}}

.challenge-title {{
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .1em;
}}

.challenge-stats {{
  font-size: 11px;
  color: var(--text-muted);
}}

.challenge-stats strong {{
  color: var(--amber);
  font-family: var(--font-mono);
}}

.progress-track {{
  height: 5px;
  background: var(--surface-2);
  border-radius: 3px;
  overflow: hidden;
  border: 1px solid var(--border);
}}

.progress-fill {{
  height: 100%;
  background: linear-gradient(90deg, #1d4ed8, #3b82f6 60%, #60a5fa);
  border-radius: 3px;
  transition: width .6s cubic-bezier(.4,0,.2,1);
}}

.progress-ticks {{
  display: flex;
  justify-content: space-between;
  margin-top: 5px;
  padding: 0 1px;
}}

.progress-tick {{
  font-size: 9px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-weight: 500;
}}

/* ── KPI Grid ────────────────────────────────────── */
.kpi-grid {{
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
  padding: 18px 28px 0;
}}

@media (max-width: 1200px) {{ .kpi-grid {{ grid-template-columns: repeat(3, 1fr); }} }}
@media (max-width: 768px)  {{ .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }} }}

.kpi-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px 18px;
  transition: border-color .2s, transform .15s;
}}

.kpi-card:hover {{
  border-color: var(--border-hover);
  transform: translateY(-1px);
}}

.kpi-card.profit {{ border-color: rgba(34,197,94,.22); }}
.kpi-card.loss   {{ border-color: rgba(239,68,68,.22); }}

.kpi-label {{
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .1em;
  margin-bottom: 8px;
}}

.kpi-value {{
  font-family: var(--font-mono);
  font-size: 22px;
  font-weight: 700;
  color: var(--text);
  line-height: 1.2;
  letter-spacing: -0.02em;
}}

.kpi-sub {{
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 5px;
  font-family: var(--font-mono);
}}

.kpi-badge {{
  display: inline-block;
  font-size: 10px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
  margin-top: 7px;
  font-family: var(--font-mono);
  letter-spacing: .02em;
}}
.kpi-badge.up   {{ background: var(--green-dim); color: var(--green); border: 1px solid rgba(34,197,94,.2); }}
.kpi-badge.down {{ background: var(--red-dim);   color: var(--red);   border: 1px solid rgba(239,68,68,.2); }}

/* ── Chart Section ───────────────────────────────── */
.chart-section {{
  margin: 18px 28px 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 18px 22px;
}}

.chart-header {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}}

.chart-title {{
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .1em;
  margin-bottom: 8px;
}}

.chart-legend {{
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
}}

.legend-item {{
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: var(--text-muted);
}}

.legend-dot {{
  width: 10px;
  height: 10px;
  border-radius: 2px;
  flex-shrink: 0;
}}

.legend-line {{
  width: 16px;
  height: 2px;
  border-radius: 1px;
  flex-shrink: 0;
}}

/* ── Sections ────────────────────────────────────── */
.section {{ margin: 18px 28px 0; }}

.section-header {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}}

.section-title {{
  font-size: 10px;
  font-weight: 700;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: .1em;
}}

.section-count {{
  font-size: 10px;
  color: var(--text-muted);
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 2px 10px;
  font-family: var(--font-mono);
}}

/* ── Tables ──────────────────────────────────────── */
.data-table {{
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}}

.data-table thead th {{
  background: var(--bg);
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  padding: 10px 16px;
  text-align: left;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}}

.data-table tbody td {{
  padding: 11px 16px;
  font-size: 12.5px;
  border-bottom: 1px solid rgba(26,45,66,.55);
  color: var(--text-2);
  transition: background .1s;
}}

.data-table tbody tr:last-child td {{ border-bottom: none; }}

.data-table tbody tr:hover td {{
  background: rgba(59,130,246,.04);
  color: var(--text);
}}

/* ── Utility classes ─────────────────────────────── */
.mono      {{ font-family: var(--font-mono); }}
.text-muted {{ color: var(--text-muted) !important; }}

.sym {{
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--text);
  font-size: 13px;
  letter-spacing: .03em;
}}

.setup-type {{
  font-size: 11px;
  color: var(--text-muted);
}}

/* ── Status / Action Pills ───────────────────────── */
.status-pill, .action-pill {{
  display: inline-block;
  font-size: 9.5px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 20px;
  letter-spacing: .06em;
  text-transform: uppercase;
  font-family: var(--font-mono);
  white-space: nowrap;
}}

.status-blue  {{ background: var(--blue-dim);            color: var(--blue);      border: 1px solid rgba(59,130,246,.22); }}
.status-green {{ background: var(--green-dim);           color: var(--green);     border: 1px solid rgba(34,197,94,.22); }}
.status-red   {{ background: var(--red-dim);             color: var(--red);       border: 1px solid rgba(239,68,68,.22); }}
.status-gray  {{ background: rgba(71,85,105,.12);        color: var(--text-muted); border: 1px solid rgba(71,85,105,.2); }}

.action-blue  {{ background: var(--blue-dim);            color: var(--blue);      border: 1px solid rgba(59,130,246,.22); }}
.action-green {{ background: var(--green-dim);           color: var(--green);     border: 1px solid rgba(34,197,94,.22); }}
.action-red   {{ background: var(--red-dim);             color: var(--red);       border: 1px solid rgba(239,68,68,.22); }}
.action-amber {{ background: var(--amber-dim);           color: var(--amber);     border: 1px solid rgba(245,158,11,.22); }}
.action-gray  {{ background: rgba(71,85,105,.12);        color: var(--text-muted); border: 1px solid rgba(71,85,105,.2); }}

/* ── Empty States ────────────────────────────────── */
.empty-row {{
  text-align: center !important;
  color: var(--text-muted) !important;
  padding: 28px 16px !important;
  font-size: 12px !important;
}}

.empty-log {{
  color: var(--text-muted);
  font-size: 12px;
  padding: 16px 0;
}}

/* ── Activity Log ────────────────────────────────── */
.activity-log {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 14px 18px;
  max-height: 360px;
  overflow-y: auto;
  font-family: var(--font-mono);
}}

.activity-log::-webkit-scrollbar {{ width: 4px; }}
.activity-log::-webkit-scrollbar-track {{ background: transparent; }}
.activity-log::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 2px; }}

.run-sep {{ border-top: 1px solid var(--border); margin: 8px 0; }}

.log-line {{
  display: flex;
  gap: 12px;
  padding: 2px 0;
  font-size: 11px;
  line-height: 1.65;
  border-bottom: 1px solid rgba(26,45,66,.4);
}}
.log-line:last-child {{ border-bottom: none; }}

.log-ts {{
  color: var(--text-muted);
  white-space: nowrap;
  min-width: 128px;
  flex-shrink: 0;
}}

.log-msg {{ color: #cbd5e1; word-break: break-word; }}

/* ── Bottom spacer ───────────────────────────────── */
.spacer {{ height: 48px; }}
</style>
<meta http-equiv="refresh" content="120">
</head>
<body>

<!-- ── Header ──────────────────────────────────────────────────────────── -->
<header class="header">
  <div class="header-brand">
    <div class="header-icon">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#3b82f6" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>
      </svg>
    </div>
    <div>
      <div class="header-title">Alpaca 30-Day Challenge</div>
      <div class="header-meta">{d['challenge_start']} &rarr; {d['challenge_end']} &nbsp;&middot;&nbsp; {d['now']}</div>
    </div>
  </div>
  <div class="header-right">
    {mkt_badge}
    <a class="refresh-link" href="/">&#8635; Refresh</a>
  </div>
</header>

{staleness_banner}

<!-- ── Challenge Progress ──────────────────────────────────────────────── -->
<div class="challenge-bar">
  <div class="challenge-header">
    <span class="challenge-title">Challenge Progress</span>
    <span class="challenge-stats">
      Day <strong>{days_elapsed}</strong> of 30
      &nbsp;&middot;&nbsp;
      <strong>{days_left}</strong> days remaining
    </span>
  </div>
  <div class="progress-track">
    <div class="progress-fill" style="width:{progress_pct:.1f}%"></div>
  </div>
  <div class="progress-ticks">
    <span class="progress-tick">D1</span>
    <span class="progress-tick">D7</span>
    <span class="progress-tick">D14</span>
    <span class="progress-tick">D21</span>
    <span class="progress-tick">D30</span>
  </div>
</div>

<!-- ── KPI Cards ───────────────────────────────────────────────────────── -->
<div class="kpi-grid">

  <div class="kpi-card">
    <div class="kpi-label">Challenge Equity</div>
    <div class="kpi-value">${d['challenge_equity']:,.2f}</div>
    <div class="kpi-sub">Started $10,000 &nbsp;·&nbsp; Full acct ${d['equity']:,.2f}</div>
  </div>

  <div class="kpi-card {pnl_class}">
    <div class="kpi-label">Total P&amp;L</div>
    <div class="kpi-value" style="color:{pnl_color}">${d['total_pnl']:+,.2f}</div>
    <span class="kpi-badge {'up' if d['total_pnl'] >= 0 else 'down'}">{total_return_pct:+.2f}% return</span>
    <div class="kpi-sub">Unrealized {unrealized_sign}${abs(unrealized):,.2f}</div>
  </div>

  <div class="kpi-card {day_class}">
    <div class="kpi-label">Today's P&amp;L</div>
    <div class="kpi-value" style="color:{day_color}">${d['day_pnl']:+,.2f}</div>
    <div class="kpi-sub">Daily loss limit &minus;${daily_loss_limit:,.0f} (4%)</div>
  </div>

  <div class="kpi-card">
    <div class="kpi-label">Available Capital</div>
    <div class="kpi-value">${d['challenge_bp']:,.2f}</div>
    <div class="kpi-sub">Of $10k challenge allocation</div>
  </div>

  <div class="kpi-card">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-value" style="color:{win_rate_color}">{win_rate:.0f}%</div>
    <div class="kpi-sub">{wins}W &middot; {losses}L &middot; {entry_count} entries</div>
  </div>

  <div class="kpi-card">
    <div class="kpi-label">Last Bot Run</div>
    <div class="kpi-value" style="font-size:19px">{d['last_run_ago'] or '&mdash;'}</div>
    <div class="kpi-sub">{d['last_run_str'] or 'Not yet run'}</div>
  </div>

</div>

<!-- ── Equity Curve ─────────────────────────────────────────────────────── -->
<div class="chart-section">
  <div class="chart-header">
    <div>
      <div class="chart-title">30-Day Portfolio Equity Curve</div>
      <div class="chart-legend">
        <span class="legend-item">
          <span class="legend-line" style="background:#3b82f6"></span>Equity
        </span>
        <span class="legend-item">
          <span class="legend-line" style="background:#22c55e;opacity:.55"></span>Peak (high water)
        </span>
        <span class="legend-item">
          <span class="legend-line" style="background:#64748b;height:2px;border-top:1px dashed #64748b"></span>Starting $10k
        </span>
        <span class="legend-item">
          <span class="legend-line" style="background:#f59e0b;height:2px;border-top:1px dashed #f59e0b"></span>Milestones
        </span>
      </div>
    </div>
  </div>
  <canvas id="equityChart" height="80"></canvas>
</div>

<!-- ── Daily P&L Bars ──────────────────────────────────────────────────── -->
<div class="chart-section">
  <div class="chart-header">
    <div>
      <div class="chart-title">Daily P&amp;L</div>
      <div class="chart-legend">
        <span class="legend-item">
          <span class="legend-dot" style="background:rgba(34,197,94,.85)"></span>Profit day
        </span>
        <span class="legend-item">
          <span class="legend-dot" style="background:rgba(239,68,68,.85)"></span>Loss day
        </span>
      </div>
    </div>
  </div>
  <canvas id="dailyChart" height="52"></canvas>
</div>

<!-- ── Open Positions ─────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Open Positions</span>
    <span class="section-count">{len(d['positions'])}</span>
  </div>
  <table class="data-table">
    <thead>
      <tr>
        <th>Symbol</th><th>Qty</th><th>Entry</th><th>Current</th><th>Unrealized P&amp;L</th>
      </tr>
    </thead>
    <tbody>{pos_rows}</tbody>
  </table>
</div>

<!-- ── Active Triggers ────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Triggers &amp; Watchlist</span>
    <span class="section-count">{len(d['triggers'])}</span>
  </div>
  <table class="data-table">
    <thead>
      <tr>
        <th>Symbol</th><th>Setup</th><th>Status</th><th>Current</th>
        <th>Entry</th><th>Stop</th><th>T1</th><th>Trail Stop</th>
      </tr>
    </thead>
    <tbody>{trig_rows}</tbody>
  </table>
</div>

<!-- ── Trade Log ─────────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Trade Log</span>
    <span class="section-count">Last {min(30, len(d['trades']))}</span>
  </div>
  <table class="data-table">
    <thead>
      <tr>
        <th>Time (UTC)</th><th>Action</th><th>Symbol</th><th>Qty</th><th>Price</th><th>P&amp;L</th>
      </tr>
    </thead>
    <tbody>{trade_rows}</tbody>
  </table>
</div>

<!-- ── Activity Log ───────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Bot Activity Log</span>
  </div>
  <div class="activity-log">
    {activity_rows}
  </div>
</div>

<div class="spacer"></div>

<script>
const isoLabels    = {json.dumps(chart_labels)};
const labels       = {json.dumps(short_labels)};
const equityCurve  = {json.dumps(equity_curve)};
const peakCurve    = {json.dumps(peak_curve)};
const dailyBars    = {json.dumps(daily_pnl_bars)};
const startValue   = {d['start_equity']};
const milestones   = {json.dumps({chart_labels.index(k) if k in chart_labels else -1: v for k, v in milestones.items()})};
const todayIdx     = {today_idx};

// Plugin: gold dashed vertical lines + labels at milestone x-indices
const milestonePlugin = {{
  id: "milestones",
  afterDraw(chart) {{
    const ctx   = chart.ctx;
    const xAxis = chart.scales.x;
    const yAxis = chart.scales.y;
    Object.entries(milestones).forEach(([i, lbl]) => {{
      const idx = +i;
      if (idx < 0) return;
      const x = xAxis.getPixelForValue(idx);
      ctx.save();
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth   = 1.2;
      ctx.setLineDash([4, 3]);
      ctx.globalAlpha = 0.45;
      ctx.beginPath();
      ctx.moveTo(x, yAxis.top);
      ctx.lineTo(x, yAxis.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
      ctx.fillStyle   = "#f59e0b";
      ctx.font        = "700 9px 'Fira Code', monospace";
      ctx.textAlign   = "center";
      ctx.fillText(lbl, x, yAxis.top - 6);
      ctx.restore();
    }});
  }}
}};

// Plugin: pulsing "today" marker on the equity chart
const todayMarkerPlugin = {{
  id: "todayMarker",
  afterDatasetsDraw(chart) {{
    if (todayIdx < 0) return;
    const ds = chart.data.datasets[0];
    const val = ds.data[todayIdx];
    if (val === null || val === undefined) return;
    const meta = chart.getDatasetMeta(0);
    const pt   = meta.data[todayIdx];
    if (!pt) return;
    const ctx = chart.ctx;
    ctx.save();
    ctx.shadowColor = "#3b82f6";
    ctx.shadowBlur  = 10;
    ctx.fillStyle   = "#3b82f6";
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, 4.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur  = 0;
    ctx.strokeStyle = "#fff";
    ctx.lineWidth   = 2;
    ctx.stroke();
    ctx.restore();
  }}
}};

// Gradient fill factory — respects whether the last point is above/below the starting line
function equityGradient(ctx) {{
  const chart = ctx.chart;
  const area  = chart.chartArea;
  if (!area) return "rgba(59,130,246,0.08)";
  const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
  g.addColorStop(0, "rgba(59,130,246,0.22)");
  g.addColorStop(1, "rgba(59,130,246,0.01)");
  return g;
}}

new Chart(document.getElementById("equityChart").getContext("2d"), {{
  plugins: [milestonePlugin, todayMarkerPlugin],
  data: {{
    labels,
    datasets: [
      {{
        type: "line",
        label: "Peak",
        data: peakCurve,
        borderColor: "rgba(34,197,94,0.55)",
        borderWidth: 1.2,
        borderDash: [4, 4],
        pointRadius: 0,
        pointHoverRadius: 0,
        fill: false,
        tension: 0,
        spanGaps: false,
        order: 2,
      }},
      {{
        type: "line",
        label: "Equity",
        data: equityCurve,
        borderColor: "#3b82f6",
        backgroundColor: equityGradient,
        fill: true,
        tension: 0.28,
        pointRadius: ctx => ctx.dataIndex === todayIdx ? 0 : 0,
        pointHoverRadius: 5,
        pointHoverBackgroundColor: "#3b82f6",
        pointHoverBorderColor: "#fff",
        pointHoverBorderWidth: 2,
        borderWidth: 2.5,
        spanGaps: false,
        order: 1,
      }},
    ],
  }},
  options: {{
    responsive: true,
    interaction: {{ mode: "index", intersect: false }},
    layout: {{ padding: {{ top: 24, right: 8 }} }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        filter: i => i.datasetIndex === 1,  // hide peak dataset from tooltip
        backgroundColor: "#0c1526",
        borderColor: "#1a2d42",
        borderWidth: 1,
        titleColor: "#94a3b8",
        titleFont: {{ family: "'Fira Code', monospace", size: 10 }},
        bodyColor: "#e2e8f0",
        bodyFont: {{ family: "'Fira Code', monospace", size: 11 }},
        padding: 12,
        callbacks: {{
          title: t => {{
            const iso = isoLabels[t[0].dataIndex];
            const ms  = milestones[t[0].dataIndex];
            return iso + (ms ? "  ·  " + ms : "");
          }},
          label: c => {{
            if (c.parsed.y === null) return null;
            const pnl  = c.parsed.y - startValue;
            const sign = pnl >= 0 ? "+" : "−";
            return ` $${{c.parsed.y.toLocaleString("en-US", {{minimumFractionDigits:2, maximumFractionDigits:2}})}}  (${{sign}}$${{Math.abs(pnl).toFixed(2)}})`;
          }}
        }}
      }}
    }},
    scales: {{
      x: {{
        grid: {{ color: "rgba(26,45,66,.35)", drawTicks: false }},
        ticks: {{
          color: "#475569",
          font: {{ size: 9, family: "'Fira Code', monospace" }},
          maxRotation: 0,
          autoSkip: true,
          autoSkipPadding: 20,
        }}
      }},
      y: {{
        position: "left",
        grid: {{
          color: ctx => ctx.tick.value === startValue ? "rgba(100,116,139,.6)" : "rgba(26,45,66,.5)",
          lineWidth: ctx => ctx.tick.value === startValue ? 1 : 1,
          borderDash: ctx => ctx.tick.value === startValue ? [4, 4] : [],
        }},
        ticks: {{
          color: ctx => ctx.tick.value === startValue ? "#94a3b8" : "#475569",
          font: {{ size: 9, family: "'Fira Code', monospace" }},
          callback: v => "$" + v.toLocaleString("en-US", {{minimumFractionDigits:0, maximumFractionDigits:0}})
        }}
      }}
    }}
  }}
}});

// Daily P&L bar chart — aligned to the same x-axis
new Chart(document.getElementById("dailyChart").getContext("2d"), {{
  plugins: [milestonePlugin],
  data: {{
    labels,
    datasets: [{{
      type: "bar",
      label: "Daily P&L",
      data: dailyBars,
      backgroundColor: dailyBars.map(v =>
        v === null ? "rgba(71,85,105,0.15)"
        : v >= 0    ? "rgba(34,197,94,0.75)"
        :             "rgba(239,68,68,0.75)"
      ),
      borderColor: dailyBars.map(v =>
        v === null ? "rgba(71,85,105,0.3)"
        : v >= 0    ? "#22c55e"
        :             "#ef4444"
      ),
      borderWidth: 1,
      borderRadius: 2,
      barPercentage: 0.75,
      categoryPercentage: 0.85,
    }}]
  }},
  options: {{
    responsive: true,
    layout: {{ padding: {{ top: 24, right: 8 }} }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        backgroundColor: "#0c1526",
        borderColor: "#1a2d42",
        borderWidth: 1,
        titleColor: "#94a3b8",
        bodyColor: "#e2e8f0",
        bodyFont: {{ family: "'Fira Code', monospace", size: 11 }},
        titleFont: {{ family: "'Fira Code', monospace", size: 10 }},
        padding: 12,
        callbacks: {{
          title: t => isoLabels[t[0].dataIndex],
          label: c => {{
            if (c.parsed.y === null) return " —";
            const s = c.parsed.y >= 0 ? "+" : "−";
            return ` ${{s}}$${{Math.abs(c.parsed.y).toFixed(2)}}`;
          }}
        }}
      }}
    }},
    scales: {{
      x: {{
        grid: {{ display: false }},
        ticks: {{
          color: "#475569",
          font: {{ size: 9, family: "'Fira Code', monospace" }},
          maxRotation: 0,
          autoSkip: true,
          autoSkipPadding: 20,
        }}
      }},
      y: {{
        grid: {{
          color: ctx => ctx.tick.value === 0 ? "rgba(100,116,139,.5)" : "rgba(26,45,66,.4)",
        }},
        ticks: {{
          color: "#475569",
          font: {{ size: 9, family: "'Fira Code', monospace" }},
          callback: v => (v >= 0 ? "+$" : "−$") + Math.abs(v).toLocaleString("en-US", {{maximumFractionDigits:0}}),
        }}
      }}
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
