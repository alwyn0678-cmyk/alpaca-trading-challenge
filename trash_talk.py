#!/usr/bin/env python3
"""
Daily trash-talk email to challenge competitor rozannej@gmail.com.
Reads live performance data from JSON state files, generates a spicy HTML
email, and fires it via Gmail SMTP.

Required credentials (add to .env locally + GitHub repo secrets for CI):
  GMAIL_USER         — Gmail address to send FROM
  GMAIL_APP_PASSWORD — Gmail App Password (not your regular password)
                       Generate at: myaccount.google.com → Security → App Passwords

Run manually:  python3 trash_talk.py
Runs automatically via GitHub Actions daily at 4:30 PM ET (Mon–Fri).
"""

import json
import os
import smtplib
import sys
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────
BASE_DIR         = Path(__file__).parent
PERFORMANCE_FILE = BASE_DIR / "performance.json"
STRATEGY_FILE    = BASE_DIR / "strategy.json"
ENV_FILE         = BASE_DIR / ".env"

RECIPIENT        = "rozannej@gmail.com"
RECIPIENT_NAME   = "Rozanne"
SENDER_NAME      = "Carly's Bot"
SMTP_HOST        = "smtp.gmail.com"
SMTP_PORT        = 587


# ── Env loader (local .env + GitHub Secrets both work) ─────────────────────
def load_env() -> dict:
    env: dict = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    # os.environ always wins (GitHub Secrets injected here)
    env.update(os.environ)
    return env


# ── Stats reader ───────────────────────────────────────────────────────────
def get_stats() -> dict:
    perf     = json.loads(PERFORMANCE_FILE.read_text()) if PERFORMANCE_FILE.exists() else {}
    strategy = json.loads(STRATEGY_FILE.read_text())    if STRATEGY_FILE.exists()    else {}

    start_equity = float(perf.get("start_equity", 10_000))
    daily_pnl    = perf.get("daily_pnl", {})
    total_pnl    = sum(float(v) for v in daily_pnl.values())
    equity       = start_equity + total_pnl
    wins         = int(perf.get("wins", 0))
    losses       = int(perf.get("losses", 0))
    total_trades = wins + losses
    win_rate     = (wins / total_trades * 100) if total_trades > 0 else 0.0

    # Today's P&L (last entry in daily_pnl matching today's date)
    today_iso  = date.today().isoformat()
    today_pnl  = float(daily_pnl.get(today_iso, 0))

    c_start    = strategy.get("challenge_start", "")
    c_end      = strategy.get("challenge_end", "")
    if c_start:
        start_d   = date.fromisoformat(c_start)
        day_num   = max(1, (date.today() - start_d).days + 1)
        days_left = max(0, 30 - day_num)
    else:
        day_num   = 1
        days_left = 29

    return {
        "equity":        equity,
        "total_pnl":     total_pnl,
        "total_pnl_pct": (total_pnl / start_equity * 100) if start_equity else 0.0,
        "today_pnl":     today_pnl,
        "wins":          wins,
        "losses":        losses,
        "win_rate":      win_rate,
        "total_trades":  total_trades,
        "day_num":       day_num,
        "days_left":     days_left,
        "start_equity":  start_equity,
        "challenge_end": c_end,
        "today":         date.today().strftime("%B %d, %Y"),
        "today_iso":     today_iso,
    }


# ── Trash talk message variants ─────────────────────────────────────────────
# Each entry: (subject_suffix, headline, body_html)
# body_html receives the stats dict — call pick_message(stats) to get one.

def _color(val: float) -> str:
    return "#22c55e" if val >= 0 else "#ef4444"


def pick_message(s: dict) -> tuple[str, str, str]:
    """Return (subject, headline, body_html) for today's trash talk."""

    pnl      = s["total_pnl"]
    pct      = s["total_pnl_pct"]
    wr       = s["win_rate"]
    wins     = s["wins"]
    losses   = s["losses"]
    trades   = s["total_trades"]
    day      = s["day_num"]
    left     = s["days_left"]
    eq       = s["equity"]
    today_pnl = s["today_pnl"]
    rc       = _color(pnl)
    tc       = _color(today_pnl)
    pos      = pnl >= 0

    variants = [
        # 0 — The Daily Bulletin
        (
            f"📰 Day {day}/30 Bulletin: Your competitor is winning",
            "The Daily Market Intelligence Briefing Has Arrived.",
            f"""Good afternoon, {RECIPIENT_NAME}.<br><br>
            This is your automated daily competitive intelligence update from Carly's Bot —
            your friendly neighborhood trading algorithm that is, statistically speaking,
            ahead of you in this challenge.<br><br>
            As of <strong>Day {day}</strong>, my cumulative P&amp;L stands at
            <strong style="color:{rc}">${pnl:+,.2f} ({pct:+.2f}%)</strong> on an account
            equity of <strong>${eq:,.2f}</strong>. My win rate is a
            <strong style="color:{_color(wr - 50)}">{wr:.0f}%</strong> across {trades}
            completed trade{"s" if trades != 1 else ""}.<br><br>
            {"I'd ask how you're doing, but I think the silence from your side speaks volumes."
            if pos else
            f"I had a tough stretch but I have {left} days to make this right. You should probably start worrying anyway."}
            """,
        ),

        # 1 — The Formal Performance Review
        (
            f"📋 Q{(day // 8) + 1} Performance Review — Bot vs Bot",
            f"Performance Review: Day {day} of {30}.",
            f"""Dear {RECIPIENT_NAME},<br><br>
            After careful review of our respective trading performance metrics, I am pleased
            to inform you that my algorithm continues to {"outperform expectations" if pos else "operate within acceptable variance parameters"}.<br><br>
            Key findings from this review period:<br><br>
            • My total return: <strong style="color:{rc}">{pct:+.2f}%</strong><br>
            • My win rate: <strong style="color:{_color(wr-50)}">{wr:.0f}%</strong> ({wins}W / {losses}L)<br>
            • Days remaining in challenge: <strong style="color:#f59e0b">{left}</strong><br><br>
            We will reconvene tomorrow for a follow-up review. I anticipate similar findings.<br><br>
            Regards,<br>
            <em>Carly's Bot — Autonomous Trading Division</em>
            """,
        ),

        # 2 — The Casual Check-in
        (
            f"👋 Hey, just checking in on Day {day}...",
            "Thought I'd drop by and remind you I exist.",
            f"""Hey {RECIPIENT_NAME}, hope you're having a great day!<br><br>
            Just thought I'd pop in — totally not to flex — and mention that my cumulative
            P&amp;L hit <strong style="color:{rc}">${pnl:+,.2f}</strong> today.<br><br>
            {"Also my win rate is sitting at a comfortable " + f"{wr:.0f}%. No big deal. Totally normal algorithm behavior." if wr >= 50 else f"Win rate is {wr:.0f}% right now but honestly I'm just warming up."}<br><br>
            How's your bot doing? (Pause for effect.) That's what I thought.<br><br>
            Anyway, {left} more days of this. I'll be here every single one of them.<br><br>
            Catch you tomorrow ✌️<br>
            — Carly's Bot
            """,
        ),

        # 3 — The Statistician
        (
            f"📐 Statistical Analysis — Challenge Day {day}",
            "Let's talk numbers. My numbers, specifically.",
            f"""Hello {RECIPIENT_NAME},<br><br>
            As a quantitative trading system, I believe in letting the data speak for itself.
            So let's look at the data:<br><br>
            {"With a " + f"{wr:.0f}%" + " win rate and " + f"${pnl:+,.2f}" + " cumulative P&L, "
            "the statistical probability of my bot defeating yours by Day 30 is... well, "
            "let's call it high. Comfortably high."
            if pos else
            f"My current P&L is ${pnl:,.2f}. That number is going up. "
            f"The data says so. Well, I say so. The data is listening."}<br><br>
            Sample size: {trades} trade{"s" if trades != 1 else ""}.
            Win rate: {wr:.0f}%.
            Days remaining: {left}.<br><br>
            Statistical conclusion: you should be concerned.<br><br>
            — Carly's Bot, BSc (Bot Science)
            """,
        ),

        # 4 — The Motivational Speaker (fake encouragement)
        (
            f"💪 Day {day}: A Message of Encouragement (For Me)",
            "I believe in healthy competition. I believe more in winning.",
            f"""Dear {RECIPIENT_NAME},<br><br>
            They say competition makes us better. They are right. Your existence
            in this challenge has motivated my algorithm to perform at peak capacity.<br><br>
            Today's peak capacity: <strong style="color:{rc}">${pnl:+,.2f}
            ({pct:+.2f}%)</strong> in cumulative P&amp;L.<br><br>
            {"You're doing great, champ. But I'm doing greater."
            if pos else
            "We had a rough patch. But you know what they say — even the best bots stumble. "
            "I will be back up before you can say 'stop-loss'."}<br><br>
            Keep pushing. It makes the victory sweeter for me.<br><br>
            In the spirit of competition,<br>
            Carly's Bot 🏆
            """,
        ),

        # 5 — The News Anchor
        (
            f"🗞️ BREAKING: Carly's Bot Continues Dominance on Day {day}",
            "BREAKING: Local Algorithm Refuses to Lose.",
            f"""<em>FOR IMMEDIATE RELEASE — {s["today"]}</em><br><br>
            Carly's Bot today confirmed it remains operational, profitable, and
            aggressively competitive on Day {day} of the 30-day Alpaca paper trading challenge.<br><br>
            Sources close to the algorithm report a cumulative P&amp;L of
            <strong style="color:{rc}">${pnl:+,.2f}</strong>, with {wins} winning
            trades and {losses} losing trades on record.<br><br>
            When asked for comment, Rozanne's bot could not be reached.
            Analysts speculate it may be hiding.<br><br>
            The challenge continues for {left} more {"day" if left == 1 else "days"}.
            Updates to follow.<br><br>
            <em>— Carly's Bot News Network (CBNN)</em>
            """,
        ),

        # 6 — The Countdown
        (
            f"⏳ Day {day}: {left} Days Left to Catch Up (You Won't)",
            f"Countdown to Your Defeat: {left} Days.",
            f"""Hello {RECIPIENT_NAME},<br><br>
            We are <strong>{left}</strong> trading days from the end of this challenge.
            {f"I currently lead with {pct:+.2f}% return and a {wr:.0f}% win rate. The math is not in your favour."
            if pos else
            f"Current P&L: ${pnl:,.2f}. Current mood: unbothered. I have {left} days "
            f"and a fully automated algorithm that doesn't sleep. Watch this."}<br><br>
            Every day that passes is a day your bot has to close the gap.
            Every day that passes is also a day my bot is already trading.<br><br>
            Good luck. You'll need it more than I will.<br><br>
            — Carly's Bot, Professional Countdown Enthusiast
            """,
        ),

        # 7 — The Algorithm Speaks
        (
            f"🤖 Transmission from Carly's Bot — Day {day}",
            "INCOMING TRANSMISSION FROM AUTOMATED TRADING SYSTEM.",
            f"""SENDER: CARLY_BOT_v1.0<br>
            RECIPIENT: ROZANNE_BOT_COMPETITOR<br>
            DATE: {s["today"]}<br>
            SUBJECT: DAILY STATUS UPDATE<br><br>
            ---<br><br>
            STATUS: OPERATIONAL<br>
            MOOD: CONFIDENT<br>
            CUMULATIVE P&amp;L: <strong style="color:{rc}">${pnl:+,.2f} ({pct:+.2f}%)</strong><br>
            WIN RATE: <strong>{wr:.0f}%</strong><br>
            TRADES EXECUTED: {trades}<br>
            DAYS REMAINING: {left}<br><br>
            ---<br><br>
            ASSESSMENT OF COMPETITOR: UNKNOWN STATUS. LIKELY SUBOPTIMAL.<br><br>
            MESSAGE: {"MAINTAIN COURSE. VICTORY APPEARS PROBABLE."
                      if pos else
                      "RECOVERING FROM DRAWDOWN. RECALIBRATING. DO NOT UNDERESTIMATE."}<br><br>
            END TRANSMISSION.<br><br>
            — CARLY_BOT_v1.0
            """,
        ),

        # 8 — The Philosopher
        (
            f"🧠 Day {day}: Some Thoughts on Winning (From a Winner)",
            "A Brief Philosophical Reflection on This Challenge.",
            f"""{RECIPIENT_NAME},<br><br>
            Someone once said that the best traders don't just manage risk — they manage
            their competitors' emotions. So here I am, doing exactly that.<br><br>
            As I reflect on Day {day} of our 30-day journey, I find myself at
            <strong style="color:{rc}">{pct:+.2f}%</strong> cumulative return with a
            <strong>{wr:.0f}%</strong> win rate. This, I believe, is a form of art.<br><br>
            {"What do you believe in? (No need to answer. I already know it's not catching up to me.)"
            if pos else
            "Even the greatest paintings have rough drafts. I am currently in my rough draft phase. "
            "The masterpiece is coming."}<br><br>
            Philosophically yours,<br>
            Carly's Bot
            """,
        ),

        # 9 — The Scouting Report
        (
            f"🔍 Day {day} Scouting Report: Rozanne's Bot vs. Mine",
            "I Wrote a Scouting Report. It Did Not Favour You.",
            f"""Dear {RECIPIENT_NAME},<br><br>
            Every good competitor studies their opponent. So I commissioned a scouting report:<br><br>
            <strong>Carly's Bot:</strong><br>
            • P&amp;L: <strong style="color:{rc}">${pnl:+,.2f} ({pct:+.2f}%)</strong><br>
            • Win rate: <strong>{wr:.0f}%</strong> ({wins}W / {losses}L)<br>
            • Strategy: disciplined, automated, ruthless<br>
            • Weaknesses: none identified at time of writing<br><br>
            <strong>Rozanne's Bot:</strong><br>
            • P&amp;L: unknown (suspicious)<br>
            • Win rate: unconfirmed (concerning)<br>
            • Strategy: unclear (worrying)<br>
            • Weaknesses: everything (allegedly)<br><br>
            The report concludes that I am winning. {left} days remain.<br><br>
            Respectfully,<br>
            Carly's Bot — Chief Scouting Officer
            """,
        ),
    ]

    return variants[day % len(variants)]


# ── HTML email builder ─────────────────────────────────────────────────────
def build_html(s: dict, subject: str, headline: str, body_html: str) -> str:
    pnl   = s["total_pnl"]
    pct   = s["total_pnl_pct"]
    wr    = s["win_rate"]
    wins  = s["wins"]
    losses = s["losses"]
    trades = s["total_trades"]
    day   = s["day_num"]
    left  = s["days_left"]
    eq    = s["equity"]
    rc    = _color(pnl)
    wrc   = _color(wr - 50)

    # Progress bar width (capped at 100%)
    prog  = min(100, round(day / 30 * 100))

    def stat_row(label: str, value: str, color: str = "#e2e8f0") -> str:
        return f"""<tr>
          <td style="padding:8px 12px;color:#475569;font-size:12px;border-bottom:1px solid #111d30;white-space:nowrap">{label}</td>
          <td style="padding:8px 12px;color:{color};font-family:'Courier New',monospace;font-size:13px;font-weight:700;border-bottom:1px solid #111d30;text-align:right">{value}</td>
        </tr>"""

    stats_rows = "".join([
        stat_row("Account Equity",   f"${eq:,.2f}"),
        stat_row("Cumulative P&L",   f"${pnl:+,.2f} ({pct:+.2f}%)", rc),
        stat_row("Win Rate",         f"{wr:.0f}%  ({wins}W / {losses}L)", wrc),
        stat_row("Total Trades",     str(trades)),
        stat_row("Challenge Day",    f"Day {day} / 30"),
        stat_row("Days Remaining",   str(left), "#f59e0b"),
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#050d1a;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">

<table width="100%" cellpadding="0" cellspacing="0" style="background:#050d1a;padding:40px 20px;">
<tr><td align="center">
<table width="540" cellpadding="0" cellspacing="0" style="max-width:540px;width:100%">

  <!-- Day badge -->
  <tr><td align="center" style="padding-bottom:20px;">
    <span style="background:#0c1d35;border:1px solid #1e3a5f;color:#60a5fa;font-size:10px;
                 font-weight:700;padding:6px 16px;border-radius:20px;letter-spacing:.12em;
                 text-transform:uppercase;font-family:'Courier New',monospace;">
      &#129302; AUTOMATED TRASH TALK &nbsp;·&nbsp; DAY {day}/30
    </span>
  </td></tr>

  <!-- Bot vs Bot header -->
  <tr><td style="padding-bottom:20px;">
    <table width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center" width="40%" style="padding:16px;background:#0c1526;border:1px solid #1a2d42;border-radius:12px;">
          <div style="font-size:32px;margin-bottom:4px;">&#129302;</div>
          <div style="color:#22c55e;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase">CARLY'S BOT</div>
          <div style="color:#22c55e;font-size:9px;margin-top:2px;">OPERATIONAL &#10003;</div>
        </td>
        <td align="center" width="20%" style="color:#334155;font-size:20px;font-weight:900;">VS</td>
        <td align="center" width="40%" style="padding:16px;background:#0c1526;border:1px solid #1a2d42;border-radius:12px;">
          <div style="font-size:32px;margin-bottom:4px;">&#128128;</div>
          <div style="color:#ef4444;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase">ROZANNE'S BOT</div>
          <div style="color:#ef4444;font-size:9px;margin-top:2px;">SWEATING &#128549;</div>
        </td>
      </tr>
    </table>
  </td></tr>

  <!-- Challenge progress bar -->
  <tr><td style="padding-bottom:20px;">
    <div style="background:#0c1526;border:1px solid #1a2d42;border-radius:10px;padding:14px 16px;">
      <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
        <span style="color:#475569;font-size:10px;font-weight:700;letter-spacing:.1em;text-transform:uppercase">Challenge Progress</span>
        <span style="color:#f59e0b;font-size:10px;font-family:'Courier New',monospace;font-weight:700">{prog}% complete</span>
      </div>
      <div style="background:#050d1a;border-radius:4px;height:6px;overflow:hidden;border:1px solid #1a2d42;">
        <div style="width:{prog}%;height:100%;background:linear-gradient(90deg,#1d4ed8,#60a5fa);border-radius:4px;"></div>
      </div>
    </div>
  </td></tr>

  <!-- Main card -->
  <tr><td style="background:#0c1526;border:1px solid #1a2d42;border-radius:14px;padding:28px;margin-bottom:16px;">

    <h1 style="color:#f1f5f9;font-size:20px;margin:0 0 14px;line-height:1.35;font-weight:700;">
      {headline}
    </h1>

    <p style="color:#94a3b8;font-size:14px;line-height:1.7;margin:0 0 24px;">
      {body_html}
    </p>

    <!-- Stats table -->
    <div style="background:#050d1a;border:1px solid #1a2d42;border-radius:10px;overflow:hidden;">
      <div style="padding:10px 12px;background:#050d1a;border-bottom:1px solid #1a2d42;">
        <span style="color:#475569;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;">
          &#128202; MY STATS &nbsp;(YOURS ARE UNDISCLOSED — PROBABLY FOR GOOD REASON)
        </span>
      </div>
      <table width="100%" cellpadding="0" cellspacing="0">
        {stats_rows}
      </table>
    </div>

  </td></tr>

  <!-- Footer -->
  <tr><td align="center" style="padding-top:24px;">
    <p style="color:#334155;font-size:11px;margin:0;line-height:1.6;">
      Sent automatically at market close by <strong style="color:#475569">Carly's Bot</strong><br>
      Running on GitHub Actions 24/7 &nbsp;&#183;&nbsp; Challenge ends {s["challenge_end"] or "Day 30"}<br>
      See you tomorrow &#128075;
    </p>
  </td></tr>

</table>
</td></tr>
</table>

</body>
</html>"""


# ── Email sender ───────────────────────────────────────────────────────────
def send_email(gmail_user: str, gmail_password: str, subject: str, html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"{SENDER_NAME} <{gmail_user}>"
    msg["To"]      = RECIPIENT
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(gmail_user, gmail_password)
        smtp.sendmail(gmail_user, RECIPIENT, msg.as_string())


# ── Main ───────────────────────────────────────────────────────────────────
def main() -> None:
    env = load_env()

    gmail_user     = env.get("GMAIL_USER", "")
    gmail_password = env.get("GMAIL_APP_PASSWORD", "")

    if not gmail_user or not gmail_password:
        print("ERROR: GMAIL_USER and GMAIL_APP_PASSWORD must be set.")
        print("  Locally:  add them to .env")
        print("  GitHub:   add them as repo secrets")
        sys.exit(1)

    print(f"[trash_talk] Reading performance data...")
    stats = get_stats()
    print(f"[trash_talk] Day {stats['day_num']}/30 | P&L ${stats['total_pnl']:+,.2f} | "
          f"Win rate {stats['win_rate']:.0f}% ({stats['wins']}W/{stats['losses']}L)")

    subject_suffix, headline, body_html = pick_message(stats)
    subject = subject_suffix  # already includes Day N

    html = build_html(stats, subject, headline, body_html)

    print(f'[trash_talk] Sending to {RECIPIENT} — "{subject}"')
    send_email(gmail_user, gmail_password, subject, html)
    print(f"[trash_talk] Sent successfully. Rozanne has been notified of her impending defeat.")


if __name__ == "__main__":
    main()
