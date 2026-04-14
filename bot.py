#!/usr/bin/env python3
"""
Alpaca Paper Trading Bot — 30-Day Challenge
Strategy engine based on Skill.md framework.
Runs every 2 hours during market hours via cron.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
STRATEGY_FILE   = BASE_DIR / "strategy.json"
TRADE_LOG_FILE  = BASE_DIR / "trade_log.json"
PERFORMANCE_FILE= BASE_DIR / "performance.json"
ENV_FILE        = BASE_DIR / ".env"
LOG_FILE        = BASE_DIR / "bot.log"
HEARTBEAT_FILE  = BASE_DIR / "heartbeat.json"

# ── Staleness threshold ────────────────────────────────────────────────────
# Alert if the bot hasn't completed a successful cycle in this many minutes.
STALE_ALERT_MINUTES = 90

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# ── Load credentials ───────────────────────────────────────────────────────
def load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

_env       = load_env()
# Fall back to environment variables so the bot works in GitHub Actions
# (locally it reads from .env; in the cloud it reads from repo secrets)
API_KEY    = _env.get("ALPACA_API_KEY")    or os.environ.get("ALPACA_API_KEY", "")
SECRET_KEY = _env.get("ALPACA_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY", "")
BASE_URL   = (_env.get("ALPACA_BASE_URL")  or os.environ.get("ALPACA_BASE_URL")
              or "https://paper-api.alpaca.markets/v2")
DATA_URL   = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": SECRET_KEY,
    "Content-Type":        "application/json",
}

# ── Challenge capital cap ──────────────────────────────────────────────────
# Only $10,000 of the $50,000 account is allocated to this 30-day challenge.
# All sizing, loss limits, and buying-power checks use this cap.
CHALLENGE_CAPITAL  = 10_000.0

# ── Risk constants — COMPETITION MODE ─────────────────────────────────────
# Sized for a 30-day challenge: bigger positions = bigger wins.
MAX_RISK_PCT       = 0.02    # 2% per trade ($200 on $10k)
MAX_DAILY_LOSS_PCT = 0.04    # 4% per day ($400 on $10k)
MAX_OPEN_RISK_PCT  = 0.04    # 4% total open across all positions
MAX_TRADES_PER_DAY = 5       # up from 3 — more opportunities
MIN_RR             = 2.0     # require 2:1 reward-to-risk (higher bar, bigger winners)
TRAILING_ACTIVATE  = 0.05    # lock in gains earlier — activate trail at +5%
TRAILING_DISTANCE  = 0.03    # trail 3% below high (was 5% — tighter protection)

# Scan universe — high-momentum names + leveraged ETFs for competition edge
WATCH_UNIVERSE = [
    # Megacap momentum (high beta, liquid, trend well)
    "NVDA", "AMD", "META", "MSFT", "AAPL", "TSLA", "AMZN", "GOOGL",
    # Sector ETFs with catalyst exposure
    "XLF", "XLE", "XLK",
    # Leveraged ETFs — 3x amplification; huge upside in trending markets
    "TQQQ", "SOXL",
    # High-beta individual plays
    "PLTR", "COIN",
    # Regime check only — NEVER traded (see REGIME_ONLY below)
    "SPY", "QQQ",
]

# These are market health indicators only — the bot will never buy/sell them
REGIME_ONLY = {"SPY", "QQQ"}

# ── Alpaca helpers ─────────────────────────────────────────────────────────
def _get(path: str, base: str = BASE_URL) -> dict | list:
    try:
        r = requests.get(f"{base}{path}", headers=HEADERS, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"GET {path} error: {e}")
        return {}

def _post(path: str, payload: dict) -> dict:
    try:
        r = requests.post(f"{BASE_URL}{path}", headers=HEADERS, json=payload, timeout=10)
        return r.json()
    except Exception as e:
        log.error(f"POST {path} error: {e}")
        return {}

def _delete(path: str) -> int:
    try:
        r = requests.delete(f"{BASE_URL}{path}", headers=HEADERS, timeout=10)
        return r.status_code
    except Exception as e:
        log.error(f"DELETE {path} error: {e}")
        return 500

def market_is_open() -> bool:
    return _get("/clock").get("is_open", False)

def get_account() -> dict:
    return _get("/account")

def get_positions() -> dict:
    """Returns {symbol: position_dict}"""
    raw = _get("/positions")
    if isinstance(raw, list):
        return {p["symbol"]: p for p in raw}
    return {}

def get_open_order_symbols() -> set:
    raw = _get("/orders?status=open")
    if isinstance(raw, list):
        return {o["symbol"] for o in raw if isinstance(o, dict)}
    return set()

def get_latest_price(symbol: str) -> Optional[float]:
    data = _get(f"/stocks/{symbol}/trades/latest?feed=iex", base=DATA_URL)
    return data.get("trade", {}).get("p")

def get_bars(symbol: str, limit: int = 60) -> list:
    from datetime import timedelta
    start = (datetime.now(timezone.utc) - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
    # Use end=yesterday to exclude today's partial intraday bar from MA/volume calculations.
    # Including a partial day would make rel_vol appear artificially low all morning.
    end = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    data = _get(
        f"/stocks/{symbol}/bars?timeframe=1Day&limit={limit}"
        f"&start={start}&end={end}&sort=desc&feed=iex",
        base=DATA_URL,
    )
    bars = data.get("bars", []) if isinstance(data, dict) else []
    return list(reversed(bars))  # oldest-first for MA calculations

def place_order(symbol: str, qty: int, side: str,
                order_type: str = "market",
                limit_price: float = None) -> dict:
    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": "day",
    }
    if limit_price:
        payload["limit_price"] = str(round(limit_price, 2))
    return _post("/orders", payload)

# ── State ──────────────────────────────────────────────────────────────────
def load_strategy() -> dict:
    if STRATEGY_FILE.exists():
        return json.loads(STRATEGY_FILE.read_text())
    return {"triggers": [], "trades_today": {}, "last_scan_date": ""}

def save_strategy(s: dict):
    STRATEGY_FILE.write_text(json.dumps(s, indent=2))

def log_trade(entry: dict):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(TRADE_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
    log.info(f"TRADE: {json.dumps(entry)}")

def load_performance() -> dict:
    if PERFORMANCE_FILE.exists():
        return json.loads(PERFORMANCE_FILE.read_text())
    return {
        "start_date": datetime.now(timezone.utc).date().isoformat(),
        "start_equity": 0.0,
        "account_value": 0.0,
        "total_pnl": 0.0,
        "wins": 0,
        "losses": 0,
        "daily_pnl": {},
    }

def save_performance(p: dict):
    PERFORMANCE_FILE.write_text(json.dumps(p, indent=2))

# ── Heartbeat ──────────────────────────────────────────────────────────────
def write_heartbeat(status: str = "ok", note: str = ""):
    """Write a heartbeat file every run so external monitors can check freshness."""
    hb = {
        "last_run_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "note": note,
    }
    HEARTBEAT_FILE.write_text(json.dumps(hb, indent=2))

def check_staleness():
    """Warn in the log if the previous run was more than STALE_ALERT_MINUTES ago."""
    if not HEARTBEAT_FILE.exists():
        return  # first ever run — no history
    try:
        hb = json.loads(HEARTBEAT_FILE.read_text())
        last = datetime.fromisoformat(hb["last_run_utc"])
        age_minutes = (datetime.now(timezone.utc) - last).total_seconds() / 60
        if age_minutes > STALE_ALERT_MINUTES:
            log.warning(
                f"STALENESS ALERT: last successful run was {age_minutes:.0f} min ago "
                f"(threshold: {STALE_ALERT_MINUTES} min). Previous status: {hb.get('status')}"
            )
    except Exception as e:
        log.warning(f"Could not check staleness: {e}")

def record_outcome(pnl: float):
    """Increment wins or losses counter when a position fully closes."""
    perf = load_performance()
    if pnl > 0:
        perf["wins"] = perf.get("wins", 0) + 1
    else:
        perf["losses"] = perf.get("losses", 0) + 1
    save_performance(perf)

# ── Technical helpers ──────────────────────────────────────────────────────
def ma(closes: list, period: int) -> Optional[float]:
    return sum(closes[-period:]) / period if len(closes) >= period else None

def get_tech(symbol: str) -> dict:
    bars = get_bars(symbol, 60)
    if not bars:
        return {}
    closes = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]
    avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    last_vol = volumes[-1] if volumes else None
    return {
        "close":     closes[-1],
        "prev":      closes[-2] if len(closes) > 1 else None,
        "ma20":      ma(closes, 20),
        "ma50":      ma(closes, 50),
        "avg_vol":   avg_vol,
        "rel_vol":   (last_vol / avg_vol) if (last_vol and avg_vol) else None,
        "high_5d":   max(b["h"] for b in bars[-5:]),
        "low_5d":    min(b["l"] for b in bars[-5:]),
        "high_20d":  max(b["h"] for b in bars[-20:]),
        "low_20d":   min(b["l"] for b in bars[-20:]),
    }

def calc_rr(entry: float, stop: float, target: float) -> float:
    risk   = abs(entry - stop)
    reward = abs(target - entry)
    return reward / risk if risk > 0 else 0

def size_position(account_value: float, entry: float, stop: float) -> int:
    risk_dollars = account_value * MAX_RISK_PCT
    stop_dist    = abs(entry - stop)
    return max(int(risk_dollars / stop_dist), 1) if stop_dist > 0 else 0

# ── Regime check ───────────────────────────────────────────────────────────
def regime_check() -> dict:
    spy = get_tech("SPY")
    qqq = get_tech("QQQ")
    if not spy:
        return {"regime": "unknown", "posture": "reduced"}
    close, m20, m50 = spy.get("close", 0), spy.get("ma20", 0), spy.get("ma50", 0)
    if close and m20 and m50:
        if close > m50 and m20 > m50:
            regime, posture = "trending_up", "normal"
        elif close < m50 and m20 < m50:
            regime, posture = "trending_down", "reduced"
        else:
            regime, posture = "recovering", "selective"
    else:
        regime, posture = "unknown", "reduced"
    return {
        "regime":    regime,
        "posture":   posture,
        "spy_close": close,
        "spy_ma50":  m50,
        "qqq_close": qqq.get("close"),
    }

# ── Entry gate (Skill.md) ──────────────────────────────────────────────────
def should_enter(trigger: dict, price: float, tech: dict) -> tuple[bool, str]:
    entry_type = trigger.get("entry_type")
    stop       = trigger.get("stop", 0)
    target_1   = trigger.get("target_1", 0)

    # Skip placeholder triggers that haven't been priced by the scanner yet
    if not trigger.get("entry_price") or trigger.get("entry_price", 0) == 0:
        return False, "trigger not yet priced — waiting for scanner"

    # Price condition
    if entry_type == "breakout":
        if price < trigger.get("entry_price", 0):
            return False, f"price {price:.2f} below breakout trigger {trigger['entry_price']:.2f}"
    elif entry_type == "pullback":
        lo = trigger.get("pullback_low", 0)
        hi = trigger.get("pullback_high", float("inf"))
        if not (lo <= price <= hi):
            return False, f"price {price:.2f} not in pullback zone [{lo:.2f}–{hi:.2f}]"
    else:
        return False, "unknown entry type"

    # R:R gate — pullback entries plan to buy AT the MA level (entry_price),
    # not at the current bid. Use entry_price so the gate matches the scanner.
    entry_for_rr = trigger.get("entry_price", price) if entry_type == "pullback" else price
    rr = calc_rr(entry_for_rr, stop, target_1)
    if rr < MIN_RR:
        return False, f"R:R {rr:.2f} < minimum {MIN_RR}"

    # Volume gate — use completed-day rel_vol (today's partial bar excluded via get_bars end date)
    rel_vol = tech.get("rel_vol") or 1.0
    if rel_vol < 0.4:
        return False, f"volume too low ({rel_vol:.2f}x) — no conviction"

    return True, "entry conditions met"

# ── Exit gate ──────────────────────────────────────────────────────────────
def check_exits(trigger: dict, price: float) -> tuple[Optional[str], str]:
    stop     = trigger.get("stop", 0)
    target_1 = trigger.get("target_1", 0)
    t1_hit   = trigger.get("t1_hit", False)
    t_active = trigger.get("trailing_active", False)
    t_stop   = trigger.get("trailing_stop") or 0

    # Hard stop (highest priority)
    if stop and price <= stop:
        return "stop_loss", f"price {price:.2f} ≤ stop {stop:.2f}"

    # Trailing stop
    if t_active and t_stop and price <= t_stop:
        return "trailing_stop", f"price {price:.2f} ≤ trail {t_stop:.2f}"

    # Target 1 — partial exit
    if not t1_hit and target_1 and price >= target_1:
        return "target_1", f"price {price:.2f} ≥ T1 {target_1:.2f}"

    return None, "hold"

def update_trailing(trigger: dict, price: float):
    entry = trigger.get("actual_entry") or trigger.get("entry_price", price)
    gain  = (price - entry) / entry if entry else 0
    if gain >= TRAILING_ACTIVATE:
        trigger["trailing_active"] = True
    if trigger.get("trailing_active"):
        high = trigger.get("highest_price") or price
        if price > high:
            trigger["highest_price"] = price
            new_trail = round(price * (1 - TRAILING_DISTANCE), 2)
            if new_trail > (trigger.get("trailing_stop") or 0):
                trigger["trailing_stop"] = new_trail
                log.info(f"  Trailing stop → ${new_trail:.2f}")

# ── Daily setup scanner ────────────────────────────────────────────────────
def scan_new_setups(strategy: dict) -> list:
    """Scan WATCH_UNIVERSE for breakout or pullback setups using Skill.md criteria."""
    # Symbols already priced and active — skip re-scanning these
    # Symbols with entry_price=0 are placeholders — allow scanner to overwrite them
    active_symbols = {
        t["symbol"] for t in strategy.get("triggers", [])
        if t.get("status") in ("active", "in_trade") and t.get("entry_price", 0) != 0
    }
    # Remove placeholder triggers so scanner can replace them with live prices
    strategy["triggers"] = [
        t for t in strategy.get("triggers", [])
        if not (t.get("entry_price", 0) == 0 and t.get("status") == "active")
    ]
    found = []

    for sym in WATCH_UNIVERSE:
        if sym in active_symbols or sym in REGIME_ONLY:
            log.info(f"  SCAN {sym}: skipped (already active or regime-only)")
            continue
        t = get_tech(sym)
        if not t or not all([t.get("close"), t.get("ma20"), t.get("ma50")]):
            log.warning(f"  SCAN {sym}: skipped — missing technical data")
            continue
        close, prev, m20, m50 = t["close"], t.get("prev", 0), t["ma20"], t["ma50"]
        rel_vol  = t.get("rel_vol") or 1.0
        high_20d = t.get("high_20d") or 0

        log.info(f"  SCAN {sym}: close={close:.2f} ma20={m20:.2f} ma50={m50:.2f} "
                 f"rel_vol={rel_vol:.2f}x high_20d={high_20d:.2f}")

        setup = None
        skip_reason = None

        # Rule 1: Momentum breakout — new 20-day high on strong volume in uptrend
        # Relaxed: rel_vol 1.5→1.2 to catch more setups in recovering markets
        if (high_20d and close >= high_20d * 0.998
                and rel_vol >= 1.2 and close > m50 and prev and close > prev):
            stop  = round(max(m20, close * 0.94), 2)   # below 20-MA or -6% floor
            t1    = round(close * 1.06, 2)              # +6% target
            t2    = round(close * 1.12, 2)              # +12% target
            entry = round(close * 1.001, 2)
            rr    = calc_rr(entry, stop, t1)
            if rr >= MIN_RR:
                setup = {
                    "symbol": sym, "setup_type": "momentum_breakout", "status": "active",
                    "entry_type": "breakout", "entry_price": entry,
                    "stop": stop, "target_1": t1, "target_2": t2,
                    "t1_hit": False, "trailing_active": False,
                    "trailing_stop": None, "highest_price": None,
                    "source": "auto_scan",
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                    "notes": f"20-day high breakout, rel_vol={rel_vol:.2f}x"
                }
            else:
                skip_reason = f"momentum_breakout R:R {rr:.2f} < {MIN_RR} (entry={entry} stop={stop} T1={t1})"
        elif high_20d and close >= high_20d * 0.998 and rel_vol < 1.2:
            skip_reason = f"near 20d-high but rel_vol {rel_vol:.2f}x < 1.2x"
        elif high_20d and close >= high_20d * 0.998 and close <= m50:
            skip_reason = f"near 20d-high but price {close:.2f} below MA50 {m50:.2f}"

        # Rule 2: Fresh 50-MA breakout on volume (trend change signal)
        if not setup and close > m50 and prev and prev < m50:
            if rel_vol >= 1.2:
                entry  = round(close * 1.002, 2)
                stop   = round(m50 * 0.985, 2)
                t1     = round(close * 1.05, 2)
                t2     = round(close * 1.10, 2)
                rr     = calc_rr(entry, stop, t1)
                if rr >= MIN_RR:
                    setup = {
                        "symbol": sym, "setup_type": "breakout_50ma", "status": "active",
                        "entry_type": "breakout", "entry_price": entry,
                        "stop": stop, "target_1": t1, "target_2": t2,
                        "t1_hit": False, "trailing_active": False,
                        "trailing_stop": None, "highest_price": None,
                        "source": "auto_scan",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "notes": f"50-MA breakout, rel_vol={rel_vol:.2f}x"
                    }
                else:
                    skip_reason = f"50ma_breakout R:R {rr:.2f} < {MIN_RR}"
            else:
                skip_reason = f"50MA crossover but rel_vol {rel_vol:.2f}x < 1.2x"

        # Rule 3: Pullback to 20-MA within uptrend — buy the dip at support
        # Relaxed: proximity window 1.5%→2.5% to catch more pullbacks
        if not setup and close > m50 and m20 > m50:
            dist_to_m20 = abs(close - m20) / m20
            if dist_to_m20 < 0.025:   # within 2.5% of 20-MA (was 1.5%)
                entry = round(m20, 2)
                stop  = round(m50 * 0.988, 2)
                t1    = round(close * 1.05, 2)
                t2    = round(close * 1.10, 2)
                rr    = calc_rr(entry, stop, t1)
                if rr >= MIN_RR:
                    setup = {
                        "symbol": sym, "setup_type": "pullback_20ma", "status": "active",
                        "entry_type": "pullback",
                        "pullback_low":  round(m20 * 0.990, 2),
                        "pullback_high": round(m20 * 1.025, 2),
                        "entry_price": entry,
                        "stop": stop, "target_1": t1, "target_2": t2,
                        "t1_hit": False, "trailing_active": False,
                        "trailing_stop": None, "highest_price": None,
                        "source": "auto_scan",
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "notes": f"Pullback to 20-MA in uptrend, dist={dist_to_m20:.2%}"
                    }
                else:
                    skip_reason = f"pullback_20ma R:R {rr:.2f} < {MIN_RR}"
            elif close > m50 and m20 > m50:
                skip_reason = f"uptrend but dist_to_ma20 {dist_to_m20:.2%} > 2.5%"
        elif not setup and not skip_reason:
            if close <= m50:
                skip_reason = f"price {close:.2f} below MA50 {m50:.2f} — no uptrend"
            elif m20 <= m50:
                skip_reason = f"MA20 {m20:.2f} <= MA50 {m50:.2f} — trend not established"

        if setup:
            log.info(f"  NEW SETUP: {sym} | {setup['setup_type']} | "
                     f"entry={setup['entry_price']} stop={setup['stop']} T1={setup['target_1']}")
            found.append(setup)
        elif skip_reason:
            log.info(f"  SCAN {sym}: no setup — {skip_reason}")

    return found

# ── Main ───────────────────────────────────────────────────────────────────
def run():
    log.info("=" * 65)
    log.info("ALPACA BOT | 30-Day Challenge | Market check starting...")
    log.info("=" * 65)

    # 0. Staleness check — warn if prior run was too long ago
    check_staleness()

    # 1. Market open?
    if not market_is_open():
        log.info("Market is closed. Exiting until next scheduled run.")
        write_heartbeat("market_closed")
        return

    # 2. Account state
    account = get_account()
    if "equity" not in account:
        log.error(f"Failed to fetch account. Response: {account}. Aborting.")
        write_heartbeat("error", "account fetch failed")
        return

    equity       = float(account["equity"])
    cash         = float(account.get("cash", 0))
    buying_power = float(account.get("buying_power", 0))
    daily_pnl    = equity - float(account.get("last_equity", equity))

    # Risk limits and sizing are capped to CHALLENGE_CAPITAL ($10k), not full equity
    challenge_bp   = min(buying_power, CHALLENGE_CAPITAL)
    max_daily_loss = CHALLENGE_CAPITAL * MAX_DAILY_LOSS_PCT

    log.info(f"  Equity: ${equity:,.2f} | Cash: ${cash:,.2f} | Day PnL: ${daily_pnl:+.2f}")
    log.info(f"  Challenge capital: ${CHALLENGE_CAPITAL:,.2f} | Available: ${challenge_bp:,.2f}")

    # 3. Daily loss limit (Skill.md hard rule)
    if daily_pnl < -max_daily_loss:
        log.warning(f"DAILY LOSS LIMIT HIT (${daily_pnl:.2f}). No new trades today.")
        _save_perf_snapshot(equity, daily_pnl)
        return

    # 4. State
    strategy      = load_strategy()
    positions     = get_positions()
    pending_syms  = get_open_order_symbols()
    today         = datetime.now(timezone.utc).date().isoformat()
    trades_today  = strategy.get("trades_today", {}).get(today, 0)

    log.info(f"  Open positions: {list(positions.keys()) or 'none'}")
    log.info(f"  Trades today: {trades_today}/{MAX_TRADES_PER_DAY}")

    # ── STEP A: Manage open positions ──────────────────────────────────────
    for trigger in strategy["triggers"]:
        if trigger["status"] != "in_trade":
            continue

        sym = trigger["symbol"]
        if sym not in positions:
            log.info(f"  {sym}: position not found — marking closed")
            trigger["status"] = "closed"
            continue

        pos          = positions[sym]
        price        = float(pos["current_price"])
        qty          = abs(int(float(pos["qty"])))
        entry        = trigger.get("actual_entry") or trigger.get("entry_price", price)
        unrealized   = float(pos.get("unrealized_pl", 0))
        gain_pct     = (price - entry) / entry * 100 if entry else 0

        log.info(f"  {sym}: price=${price:.2f} | qty={qty} | "
                 f"entry=${entry:.2f} | PnL=${unrealized:+.2f} ({gain_pct:+.1f}%)")

        # Update trailing stop
        update_trailing(trigger, price)

        action, reason = check_exits(trigger, price)

        if action == "stop_loss" and sym not in pending_syms:
            log.info(f"  {sym}: STOP LOSS — {reason}")
            order = place_order(sym, qty, "sell")
            trigger["status"] = "stopped_out"
            pnl = (price - entry) * qty
            log_trade({"action": "STOP_LOSS", "symbol": sym, "qty": qty,
                       "price": price, "entry": entry, "pnl": round(pnl, 2)})
            record_outcome(pnl)

        elif action == "target_1" and sym not in pending_syms:
            half = max(qty // 2, 1)
            log.info(f"  {sym}: TARGET 1 — selling {half} shares. {reason}")
            order = place_order(sym, half, "sell")
            trigger["t1_hit"]         = True
            trigger["trailing_active"] = True
            trigger["highest_price"]  = price
            trigger["trailing_stop"]  = round(price * (1 - TRAILING_DISTANCE), 2)
            pnl = (price - entry) * half
            log_trade({"action": "TARGET_1", "symbol": sym, "qty": half,
                       "price": price, "entry": entry, "pnl": round(pnl, 2)})

        elif action == "trailing_stop" and sym not in pending_syms:
            remainder = (qty // 2) if trigger.get("t1_hit") else qty
            log.info(f"  {sym}: TRAILING STOP — selling {remainder} shares. {reason}")
            order = place_order(sym, remainder, "sell")
            trigger["status"] = "closed"
            pnl = (price - entry) * remainder
            log_trade({"action": "TRAILING_STOP", "symbol": sym, "qty": remainder,
                       "price": price, "entry": entry, "pnl": round(pnl, 2)})
            record_outcome(pnl)
        else:
            t_stop = trigger.get("trailing_stop")
            t_str  = f"trail=${t_stop:.2f}" if t_stop else "trail=inactive"
            log.info(f"  {sym}: HOLD | stop=${trigger['stop']:.2f} | {t_str}")

    # ── STEP B: Check entry triggers ───────────────────────────────────────
    if trades_today >= MAX_TRADES_PER_DAY:
        log.info(f"  Max trades per day reached. Skipping entries.")
    else:
        regime = regime_check()
        log.info(f"  Regime: {regime['regime']} | Posture: {regime['posture']}")

        for trigger in strategy["triggers"]:
            if trigger["status"] != "active":
                continue
            if trades_today >= MAX_TRADES_PER_DAY:
                break

            sym = trigger["symbol"]
            if sym in REGIME_ONLY:
                continue   # never trade the regime-check ETFs
            if sym in positions or sym in pending_syms:
                continue

            price = get_latest_price(sym)
            if not price:
                log.warning(f"  {sym}: could not fetch price. Skipping.")
                continue

            tech = get_tech(sym)
            enter, reason = should_enter(trigger, price, tech)

            if enter:
                # For pullback entries use a limit order at the planned MA entry level.
                # For breakout entries use a market order (momentum — don't miss the move).
                entry_type = trigger.get("entry_type", "breakout")
                planned_entry = trigger.get("entry_price", price)
                order_type  = "limit"    if entry_type == "pullback" else "market"
                limit_price = planned_entry if entry_type == "pullback" else None

                # Size from planned entry price for pullbacks (that's the actual risk basis)
                sizing_price = planned_entry if entry_type == "pullback" else price
                qty = size_position(CHALLENGE_CAPITAL, sizing_price, trigger["stop"])
                cost = qty * (limit_price or price)
                if cost > challenge_bp * 0.90:
                    qty = max(int(challenge_bp * 0.90 / (limit_price or price)), 0)
                if qty <= 0:
                    log.warning(f"  {sym}: size=0 — skipping.")
                    continue

                # Log R:R from planned entry (consistent with how should_enter evaluated it)
                rr = calc_rr(planned_entry, trigger["stop"], trigger["target_1"])
                order_desc = f"LIMIT@{limit_price:.2f}" if order_type == "limit" else f"MARKET@{price:.2f}"
                log.info(f"  {sym}: ENTERING | {order_desc} | qty={qty} | "
                         f"stop=${trigger['stop']:.2f} | T1=${trigger['target_1']:.2f} | R:R={rr:.2f}")

                order = place_order(sym, qty, "buy", order_type=order_type, limit_price=limit_price)
                if order.get("id"):
                    trigger["status"]       = "in_trade"
                    trigger["actual_entry"] = price
                    trigger["qty"]          = qty
                    strategy.setdefault("trades_today", {})[today] = trades_today + 1
                    trades_today += 1
                    log_trade({
                        "action": "ENTRY", "symbol": sym, "qty": qty,
                        "price": price, "stop": trigger["stop"],
                        "target_1": trigger["target_1"], "target_2": trigger["target_2"],
                        "setup_type": trigger["setup_type"], "rr": round(rr, 2),
                        "order_id": order.get("id"), "regime": regime["regime"],
                    })
                else:
                    log.error(f"  {sym}: order failed — {order}")
            else:
                log.info(f"  {sym}: no entry — {reason}")

    # ── STEP C: Daily setup scan (once per day, after 10:30 AM ET) ─────────
    now_utc_hour = datetime.now(timezone.utc).hour
    last_scan    = strategy.get("last_scan_date", "")
    # 14:30 UTC = 10:30 AM ET
    if now_utc_hour >= 14 and last_scan != today:
        log.info("  Running daily setup scan...")
        new_setups = scan_new_setups(strategy)
        for s in new_setups:
            strategy["triggers"].append(s)
            log.info(f"  Added trigger: {s['symbol']} | {s['setup_type']}")
        strategy["last_scan_date"] = today

    # ── Save ───────────────────────────────────────────────────────────────
    strategy["last_run"] = datetime.now(timezone.utc).isoformat()
    save_strategy(strategy)
    _save_perf_snapshot(equity, daily_pnl)

    write_heartbeat("ok", f"Day PnL=${daily_pnl:+.2f}")
    log.info(f"  Check complete. Day PnL=${daily_pnl:+.2f} | Equity=${equity:,.2f}")
    log.info("=" * 65)


def _save_perf_snapshot(equity: float, daily_pnl: float):
    perf = load_performance()
    today = datetime.now(timezone.utc).date().isoformat()
    # One-time: capture the real Alpaca account equity at challenge start
    if not perf.get("account_start_equity"):
        perf["account_start_equity"] = equity
    pnl = round(equity - perf["account_start_equity"], 2)
    perf["account_value"]        = round(CHALLENGE_CAPITAL + pnl, 2)
    perf["total_pnl"]            = pnl
    perf["daily_pnl"][today]     = round(daily_pnl, 2)
    save_performance(perf)


if __name__ == "__main__":
    run()
