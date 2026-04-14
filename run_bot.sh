#!/bin/bash
# Run the trading bot — called by cron every 2 hours.
# Syncs GHA state first so local run always has fresh strategy/performance data.
cd "$(dirname "$0")"

# Pull latest state from GitHub (GHA may have run since last local execution).
# Run silently; failures are non-fatal — bot will still run with local state.
/usr/bin/git pull --quiet origin main 2>> bot.log

# bot.py has its own FileHandler writing to bot.log — no redirect needed.
# Redirect only stderr (unexpected crashes) to bot.log so nothing is lost.
/usr/local/bin/python3 bot.py 2>> bot.log
