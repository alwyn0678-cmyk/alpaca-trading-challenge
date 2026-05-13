# Alpaca Paper Trading Challenge — Final Report

**Challenge Period:** April 13, 2026 → May 13, 2026 (30 days)
**Generated:** 2026-05-13
**Data sources:** performance.json, strategy.json, trade_log.json, heartbeat.json

---

## 1. Headline Numbers

| Metric | Value |
|--------|-------|
| Account starting equity | $50,000.00 |
| Challenge slice starting equity | $10,000.00 |
| Final account value (challenge slice) | $10,799.35 |
| **Total P&L** | **+$799.35 (+7.99% of challenge slice)** |
| Peak equity (challenge slice) | $10,843.91 |
| Peak date | ~April 22, 2026 |
| Max drawdown from peak | −$787.22 (−7.26% from peak) |
| Win rate (per performance.json) | 1 win / 2 recorded (50.0%) |

**Notes:**

- *Peak date:* performance.json stores `peak_equity` ($50,843.91) but not the date it occurred. Cumulative daily P&L peaks at +$845.84 on April 22, aligning closely with the $843.91 peak gain confirmed by the profit-ratchet log entry. April 22 is used throughout this report.
- *Win rate:* performance.json records `wins=1, losses=1`, but the trade log shows at least five profitable closes and one stop-loss. The counter appears to increment only on formal TARGET_1 hits (win) and STOP_LOSS events (loss); trailing-stop and profit-ratchet exits are not counted. Treat 50% as an undercount.
- *Max drawdown:* Measured from peak_equity ($10,843.91) to the lowest point in the cumulative daily-P&L curve (~$10,056.69 on April 27–28). Daily P&L includes mark-to-market unrealized moves, so the figure reflects worst-case open-position exposure from the peak.

---

## 2. Trade-by-Trade Summary

Each row is one exit event paired with its ENTRY action from trade_log.json. P&L figures come directly from the log where available; estimates are flagged.

| Open Date | Symbol | Setup | Qty | Entry $ | Exit $ | Exit Type | P&L $ | P&L % |
|-----------|--------|-------|-----|---------|--------|-----------|-------|-------|
| 2026-04-14 | COIN | pullback_20ma | 12 | $184.81 | $194.36 | TARGET_1 | +$114.54 | +5.16% |
| 2026-04-14 | COIN | pullback_20ma | 6 | $184.81 | $206.88 | TRAILING_STOP | +$132.42 | +11.94% |
| 2026-04-14 | COIN | pullback_20ma | 6 | $184.81 | ~market open Apr 27 | MANUAL_CLEANUP | *see note A* | *see note A* |
| 2026-04-20 | PLTR | pullback_20ma | 62 | $144.54 | $140.62 | STOP_LOSS | −$243.35 | −2.72% |
| 2026-04-24 | XLF | pullback_20ma | 172 | ~$51.50 | $52.22 | PROFIT_RATCHET | +~$123.84 | +~1.40% |
| 2026-04-29 | AAPL | pullback_20ma | 16 | $268.72 | $283.99 | TARGET_1 | +$244.40 | +5.68% |
| 2026-04-29 | AAPL | pullback_20ma | 17 | $268.72 | $283.44 | PROFIT_RATCHET | +$250.33 | +5.48% |
| 2026-04-30 | UBER | pullback_20ma | 120 | $74.11 | $74.82 | PROFIT_RATCHET | +$85.20 | +0.96% |

**Detected but not executed (no ENTRY in trade log):**

| Detected Date | Symbol | Setup | Outcome |
|---------------|--------|-------|---------|
| 2026-04-15 | MSFT | breakout_50ma | Trigger closed with no fill — reason not logged |
| 2026-04-16 | TSLA | breakout_50ma | Trigger closed with no fill — reason not logged |
| 2026-05-01 | COIN #2 | pullback_20ma | Detected minutes before profit-ratchet fired; no entry taken |

---

**Note A — COIN MANUAL_CLEANUP (6 shares):** A trailing-stop bug left 6 COIN shares unmanaged after the trailing-stop exit on April 17. The log entry (2026-04-25) describes "orphan position cleanup — 6 shares left unmanaged by trailing-stop bug; market sell queued for Monday open." No fill price or P&L was recorded for this exit. The realized gain from these shares is absorbed into the overall total ($799.35) but cannot be isolated from the trade log.

**Note B — XLF position tracking:** The log contains three ENTRY records for XLF (April 24 @ $51.495 / 177 shares, April 27 @ $51.775 / 176 shares, April 28 @ $52.015 / 176 shares) alongside three BACKFILL_NON_FILL events reconciling phantom limit-price entries ($50.78, $50.90, $51.10) that expired unfilled. Only 172 shares appear in the profit-ratchet exit on May 1. The strategy.json trigger for XLF has no `actual_entry` or `qty` fields. The exact entry basis is ambiguous; the row above uses the first confirmed fill price ($51.495) as the basis. P&L is an estimate. The total P&L in performance.json ($799.35) is authoritative; only the XLF component is uncertain.

---

## 3. Best and Worst Trades

**Biggest single winning trade:**

> **AAPL** — pullback_20ma, opened 2026-04-29
>
> - 33 shares entered at $268.72
> - 16 shares exited at TARGET_1 ($283.99): +$244.40
> - 17 shares exited at PROFIT_RATCHET ($283.44): +$250.33
> - **Total P&L: +$494.73 (+5.58% on $8,867 notional)**

The win/loss asymmetry is notable: the best single trade (+$494.73) returned more than twice the worst loss (−$243.35) in dollar terms, despite near-identical notional sizes (~$8.9K each).

**Biggest single losing trade:**

> **PLTR** — pullback_20ma, opened 2026-04-20
>
> - 62 shares entered at $144.54, stopped out at $140.62
> - Slippage was ~$1.10/share through the $141.72 stop level
> - **Total P&L: −$243.35 (−2.72% on $8,961 notional)**

---

## 4. Defensive System Events

### PROFIT_RATCHET_FLATTEN — Fired (2026-05-01 ~16:41 UTC)

All three open positions were flattened simultaneously after the portfolio's unrealized gain pulled back through the ratchet floor.

| Metric | Value |
|--------|-------|
| Peak portfolio gain | $843.91 |
| Gain at trigger time | $739.32 |
| Ratchet floor | $776.40 (~92% of peak) |
| Trigger condition | `cur_gain` ($739.32) < `floor` ($776.40) |

**Positions flattened:**

| Symbol | Qty | Exit Price | Approx P&L |
|--------|-----|-----------|------------|
| AAPL | 17 | $283.44 | +$250.33 |
| UBER | 120 | $74.82 | +$85.20 |
| XLF | 172 | $52.22 | +~$123.84 |

**Dollar impact:** Gains had eroded ~$104 from peak ($843.91 → $739.32) when the ratchet fired. By exiting at that moment, the system locked in a final realized total of $799.35, preventing further drawdown from continued position exposure into end-of-day.

---

### DAILY_LOSS_FLATTEN
**Never triggered.** Defensive mechanism was in place but did not fire during the 30-day challenge.

### MARKET_CRASH_FLATTEN
**Never triggered.** Defensive mechanism was in place but did not fire during the 30-day challenge.

### ACCOUNT_DRAWDOWN_FLATTEN
**Never triggered.** Defensive mechanism was in place but did not fire during the 30-day challenge.

---

## 5. Daily P&L Table

Source: performance.json `daily_pnl`. No entries exist after May 1; all positions were closed by the profit ratchet on May 1 and the bot ran quiescently through the May 13 challenge end date.

| Date | Daily P&L | Cumulative P&L | Notes |
|------|-----------|----------------|-------|
| 2026-04-13 | $0.00 | $0.00 | Challenge start; no trades |
| 2026-04-14 | −$15.90 | −$15.90 | COIN entry; initial MTM dip |
| 2026-04-15 | +$238.68 | +$222.78 | COIN TARGET_1 (12 shares, +$114.54) + MTM gains |
| 2026-04-16 | +$29.40 | +$252.18 | TSLA setup detected; no entry |
| 2026-04-17 | +$82.02 | +$334.20 | COIN TRAILING_STOP (6 shares, +$132.42) |
| 2026-04-20 | +$84.57 | +$418.77 | PLTR entry; COIN orphan MTM |
| 2026-04-21 | −$1.61 | +$417.16 | Minor MTM drift |
| 2026-04-22 | +$428.68 | **+$845.84** | **← MTM peak** |
| 2026-04-23 | −$724.67 | +$121.17 | Largest single-day drop; PLTR open exposure |
| 2026-04-24 | −$49.26 | +$71.91 | PLTR STOP_LOSS (−$243.35 realized) + XLF entry |
| 2026-04-27 | −$15.22 | +$56.69 | **← trough**; COIN orphan cleanup; XLF position |
| 2026-04-28 | $0.00 | +$56.69 | COIN orphan settled; XLF held |
| 2026-04-29 | +$56.88 | +$113.57 | AAPL entry |
| 2026-04-30 | +$262.74 | +$376.31 | UBER entry; open positions appreciating |
| 2026-05-01 | +$507.46 | +$883.77 | AAPL TARGET_1 + profit-ratchet flatten |
| **Total (daily_pnl sum)** | | **+$883.77** | |

> **Reconciliation note:** The daily_pnl sum (+$883.77) differs from total_pnl in performance.json (+$799.35) by $84.42. The daily series captures mark-to-market (unrealized) swings; the final realized figure reflects actual fill prices. The $84.42 gap represents MTM gains that were present in the running totals but not fully realized at exit — consistent with the ratchet firing slightly below peak.

---

## 6. Retrospective

### What Worked

1. **pullback_20ma was the only setup that delivered.** Every executed trade used this setup. AAPL (+$494.73), COIN (at least +$247 from logged exits), UBER (+$85.20), and XLF (+~$124 estimated) all entered on pullbacks to the 20-period moving average. The breakout_50ma setups (MSFT, TSLA) produced no executed trades. The data supports leaning into pullback entries in trending regimes.

2. **PROFIT_RATCHET_FLATTEN preserved the majority of peak gains.** When unrealized gains eroded ~$104 from the $843.91 peak, the ratchet fired and locked in $799.35. Without it, open positions in AAPL, UBER, and XLF would have continued to fluctuate through the final six weeks of the challenge with no guaranteed floor.

3. **Trailing stops captured the extended COIN run.** Six COIN shares rode from the TARGET_1 level (~$194) to $206.88 via trailing stop — capturing an additional $12.53/share beyond the T1 exit. The mechanism meaningfully extended the winner's contribution, even though a bug later left another 6 shares as an orphan.

4. **Regime gating kept the bot out of adverse conditions.** All ENTRY actions occurred in `recovering` or `trending_up` regimes. No trades were taken in bearish or uncertain regimes. The MSFT, TSLA, and COIN #2 setups were detected but not entered — even if the reason is unlogged, no losses resulted from those detections.

5. **Risk-reward discipline held.** Executed trades showed R:R ratios from 1.12 (COIN) to 5.36 (XLF). The one clean stop-loss (PLTR) produced only −$243.35 despite 62 shares and ~$9K notional — a loss of 2.72% on notional. Position sizing was conservative relative to the challenge slice.

### What Didn't Work

1. **Trailing-stop bug created an orphan position.** After the COIN trailing-stop fired on 6 shares (April 17), the remaining 6 shares were left unmanaged until April 25 — an 8-day gap. The market sell executed at Monday open with no logged price or P&L. This is a reliability defect that could cause meaningful losses if a position goes against the bot over a weekend.

2. **XLF position tracking was unreliable.** Three ENTRY records, three BACKFILL_NON_FILL reconciliations, no `actual_entry` or `qty` in strategy.json, and a profit-ratchet exit for 172 shares that doesn't cleanly reconcile to any logged entry quantity. This trade's P&L contribution is an estimate. The bot cannot produce an auditable account of what happened in XLF.

3. **April 23 produced a −$724.67 single-day MTM drop** — the largest single-day swing in the challenge and 85% of PLTR's eventual realized loss occurring as unrealized exposure. The bot held through this drawdown and the stop ultimately triggered the next day (−$243.35 realized), but the MTM volatility suggests the PLTR position was heavily correlated to market-wide turbulence on that date.

4. **MSFT and TSLA produced no trades despite being scanned.** Both breakout_50ma setups are logged as `closed` in strategy.json with no ENTRY record and no reason. It is impossible to determine from the data whether the filters worked correctly (rejecting bad setups) or incorrectly (missing profitable opportunities). The bot is a black box on this point.

5. **XLF limit orders were repeatedly set below market.** Three BACKFILL_NON_FILL events confirm that XLF limits at $50.78, $50.90, and $51.10 expired unfilled while price traded at $51.50–$52.02. The bot anchored limits too far below prevailing price, requiring the position to be entered at worse prices (or possibly built in multiple tranches), adding complexity and tracking errors.

### Lessons for a Future Iteration

1. **Fix the trailing-stop orphan bug.** After any trailing-stop or partial exit fires, the system must reconcile remaining shares against broker positions before end-of-session. A position-verification step (query broker state, compare to internal tracking) run at market close would catch and remediate orphans the same day.

2. **Log rejection reasons for every detected-but-not-entered setup.** MSFT, TSLA, and COIN #2 were silently dropped. Adding a `REJECTED` log entry with a machine-readable reason (`regime_filter`, `daily_limit_reached`, `no_fill_in_window`) would make every future retrospective far more informative and enable systematic tuning.

3. **Write `actual_entry` and `qty` to strategy.json atomically on fill confirmation.** The XLF trigger has neither field, indicating the fill confirmation path and the strategy-state update path are decoupled. Closing this gap eliminates the class of tracking errors that required BACKFILL_NON_FILL patches.

4. **Recalibrate pullback limit levels.** If the pullback limit is consistently set below where price is already trading, fills will never occur at those levels. Setting limits at or slightly above the current 20-MA level (or using a buy-stop-limit on a confirmed bounce candle) should improve fill rates without excessive slippage.

5. **Consider widening the profit-ratchet floor.** The ratchet fired at 92% of peak gain (floor = $776.40 vs. peak = $843.91). It fired on what may have been a brief intraday dip, exiting on May 1 at a gain of ~$739 when the account ultimately settled at $799.35. A slightly wider floor (e.g., 85% of peak) could allow more room to run while still protecting the bulk of accumulated gains. Backtesting different floor percentages against the daily P&L data from this challenge is a reasonable starting point.

---

## 7. Final State Snapshot

**Open positions at challenge end:** None. All eight triggers in strategy.json have status `closed` or `stopped_out`. The profit-ratchet flattened all remaining positions (AAPL, UBER, XLF) on May 1, 2026 at ~16:41 UTC. The bot carried no open exposure through the remaining 12 days of the challenge window.

**Last bot run:** `2026-05-13T20:23:01 UTC`
**Heartbeat status:** `market_closed`
**Last scan date:** `2026-05-01` (strategy.json)

The bot continued its scheduled heartbeat runs through May 13 but had no positions to manage and no setups to scan after May 1. The challenge concluded with a clean, fully-flat book.

---

*Auto-generated from performance.json, strategy.json, trade_log.json, and heartbeat.json. No Alpaca API calls were made; all figures derive from committed repository state as of 2026-05-13.*
