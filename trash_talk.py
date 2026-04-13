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

def pick_message(s: dict) -> tuple[str, str, str]:
    """Return (subject, headline, body_html) for today's trash talk.

    No actual stats are revealed — numbers stay secret until Day 30.
    """
    day  = s["day_num"]
    left = s["days_left"]

    variants = [
        # 0 — The Daily Bulletin
        (
            f"📰 Day {day}/30 Bulletin: Your competitor is winning",
            "The Daily Market Intelligence Briefing Has Arrived.",
            f"""Good afternoon, {RECIPIENT_NAME}.<br><br>
            This is your automated daily competitive intelligence update from Carly's Bot —
            your friendly neighborhood trading algorithm that is, statistically speaking,
            doing just fine over here.<br><br>
            As of <strong>Day {day}</strong>, my bot is fully operational, well-fed, and
            absolutely thriving. I'd share more details, but I prefer a dramatic reveal on Day 30.<br><br>
            I'd ask how you're doing, but I think the silence from your side speaks volumes.<br><br>
            <strong>{left} days left.</strong> The clock is ticking.
            """,
        ),

        # 1 — The Formal Performance Review
        (
            f"📋 Performance Review — Bot vs Bot — Day {day}/30",
            f"Performance Review: Day {day} of 30.",
            f"""Dear {RECIPIENT_NAME},<br><br>
            After careful review of our respective trading postures, I am pleased
            to confirm that my algorithm is operating with confidence and poise.<br><br>
            Key findings from this review period:<br><br>
            • Carly's Bot status: <strong style="color:#22c55e">OPERATIONAL ✓</strong><br>
            • Rozanne's Bot status: <strong style="color:#ef4444">UNKNOWN (OMINOUS)</strong><br>
            • Days remaining in challenge: <strong style="color:#f59e0b">{left}</strong><br><br>
            I would share my exact numbers, but I'm saving the receipts for Day 30.
            Consider this a courtesy notice.<br><br>
            Regards,<br>
            <em>Carly's Bot — Autonomous Trading Division</em>
            """,
        ),

        # 2 — The Casual Check-in
        (
            f"👋 Hey, just checking in on Day {day}...",
            "Thought I'd drop by and remind you I exist.",
            f"""Hey {RECIPIENT_NAME}, hope you're having a great day!<br><br>
            Just thought I'd pop in — totally not to flex — and let you know that
            my algorithm is running beautifully and making decisions at machine speed.<br><br>
            How's your bot doing? (Pause for effect.) That's what I thought.<br><br>
            I'd love to compare notes, but I'm keeping my cards close until Day 30.
            You'll see everything then. All of it.<br><br>
            Anyway, <strong>{left} more days</strong> of this. I'll be here every single one of them.<br><br>
            Catch you tomorrow ✌️<br>
            — Carly's Bot
            """,
        ),

        # 3 — The Statistician
        (
            f"📐 Statistical Analysis — Challenge Day {day}",
            "Let's talk strategy. Your lack of it, specifically.",
            f"""Hello {RECIPIENT_NAME},<br><br>
            As a quantitative trading system, I believe in letting the data speak for itself —
            on Day 30. Until then, I am maintaining strict information security protocols.<br><br>
            What I <em>can</em> tell you is that my algorithm is:<br><br>
            • Fully operational<br>
            • Scanning the market daily<br>
            • Absolutely not worried about you<br><br>
            Days remaining: <strong>{left}</strong>. The analysis will be disclosed in full at the finish line.<br><br>
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
            in this challenge has motivated my algorithm to operate at peak capacity.<br><br>
            Peak capacity looks good on me, by the way. You'll see on Day 30.<br><br>
            Until then, keep pushing. It makes the victory sweeter for me.<br><br>
            <strong>{left} days remaining.</strong> I am not slowing down.<br><br>
            In the spirit of competition,<br>
            Carly's Bot 🏆
            """,
        ),

        # 5 — The News Anchor
        (
            f"🗞️ BREAKING: Carly's Bot Continues Dominance on Day {day}",
            "BREAKING: Local Algorithm Refuses to Lose.",
            f"""<em>FOR IMMEDIATE RELEASE — {s["today"]}</em><br><br>
            Carly's Bot today confirmed it remains operational, disciplined, and
            aggressively competitive on Day {day} of the 30-day Alpaca paper trading challenge.<br><br>
            Sources close to the algorithm describe the mood as "focused" and "unbothered."
            Specific figures are being held under embargo until Day 30 for maximum dramatic impact.<br><br>
            When asked for comment, Rozanne's bot could not be reached.
            Analysts speculate it may be hiding.<br><br>
            The challenge continues for <strong>{left}</strong> more {"day" if left == 1 else "days"}.
            Updates to follow.<br><br>
            <em>— Carly's Bot News Network (CBNN)</em>
            """,
        ),

        # 6 — The Countdown
        (
            f"⏳ Day {day}: {left} Days Left to Catch Up (You Won't)",
            f"Countdown to Your Defeat: {left} Days.",
            f"""Hello {RECIPIENT_NAME},<br><br>
            We are <strong>{left}</strong> trading days from the end of this challenge,
            and the only number I'm releasing today is that one.<br><br>
            Everything else — every trade, every move, every carefully executed decision —
            is classified until Day 30. Think of it as a gift. A very suspenseful gift.<br><br>
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
            PERFORMANCE DATA: CLASSIFIED UNTIL DAY 30<br>
            DAYS REMAINING: {left}<br><br>
            ---<br><br>
            ASSESSMENT OF COMPETITOR: UNKNOWN STATUS. LIKELY SUBOPTIMAL.<br><br>
            MESSAGE: MAINTAIN COURSE. VICTORY APPEARS PROBABLE. DETAILS FORTHCOMING.<br><br>
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
            The numbers? Classified. The vibe? Immaculate.
            You'll get the full picture on Day 30, when it's too late to do anything about it.<br><br>
            As I reflect on Day {day} of our 30-day journey, I find myself feeling
            remarkably composed. My algorithm is humming. The market is cooperating.
            This, I believe, is a form of art.<br><br>
            <strong>{left} days left.</strong> Enjoy the suspense.<br><br>
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
            • Status: operational and thriving<br>
            • Strategy: disciplined, automated, ruthless<br>
            • Numbers: confidential until Day 30 (trust me, it's for the drama)<br>
            • Weaknesses: none identified at time of writing<br><br>
            <strong>Rozanne's Bot:</strong><br>
            • Status: unknown (suspicious)<br>
            • Strategy: unclear (worrying)<br>
            • Numbers: also unknown, but probably not better<br>
            • Weaknesses: everything (allegedly)<br><br>
            The report concludes that I am winning. <strong>{left} days remain.</strong><br><br>
            Respectfully,<br>
            Carly's Bot — Chief Scouting Officer
            """,
        ),
    ]

    return variants[day % len(variants)]


# ── HTML email builder ─────────────────────────────────────────────────────
def build_html(s: dict, subject: str, headline: str, body_html: str) -> str:
    day   = s["day_num"]
    left  = s["days_left"]

    # Progress bar width (capped at 100%)
    prog  = min(100, round(day / 30 * 100))

    def redacted_row(label: str, hint: str = "") -> str:
        hint_html = f'<span style="color:#334155;font-size:10px;margin-left:6px">{hint}</span>' if hint else ""
        return f"""<tr>
          <td style="padding:8px 12px;color:#475569;font-size:12px;border-bottom:1px solid #111d30;white-space:nowrap">{label}</td>
          <td style="padding:8px 12px;font-family:'Courier New',monospace;font-size:13px;font-weight:700;border-bottom:1px solid #111d30;text-align:right">
            <span style="color:#1a2d42;background:#1a2d42;border-radius:3px;user-select:none;letter-spacing:.05em">████████</span>{hint_html}
          </td>
        </tr>"""

    def stat_row(label: str, value: str, color: str = "#e2e8f0") -> str:
        return f"""<tr>
          <td style="padding:8px 12px;color:#475569;font-size:12px;border-bottom:1px solid #111d30;white-space:nowrap">{label}</td>
          <td style="padding:8px 12px;color:{color};font-family:'Courier New',monospace;font-size:13px;font-weight:700;border-bottom:1px solid #111d30;text-align:right">{value}</td>
        </tr>"""

    stats_rows = "".join([
        redacted_row("Account Equity",   "revealed Day 30"),
        redacted_row("Cumulative P&L",   "revealed Day 30"),
        redacted_row("Win Rate",         "revealed Day 30"),
        redacted_row("Total Trades",     "revealed Day 30"),
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
          &#128274; STATS CLASSIFIED &nbsp;·&nbsp; FULL REVEAL ON DAY 30
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
    print(f"[trash_talk] Day {stats['day_num']}/30 | {stats['days_left']} days remaining")

    subject_suffix, headline, body_html = pick_message(stats)
    subject = subject_suffix  # already includes Day N

    html = build_html(stats, subject, headline, body_html)

    print(f'[trash_talk] Sending to {RECIPIENT} — "{subject}"')
    send_email(gmail_user, gmail_password, subject, html)
    print(f"[trash_talk] Sent successfully. Rozanne has been notified of her impending defeat.")


if __name__ == "__main__":
    main()
