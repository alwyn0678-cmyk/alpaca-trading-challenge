#!/bin/bash
# Run the trading bot — called by cron every 2 hours
cd "$(dirname "$0")"
/usr/bin/python3 bot.py >> bot.log 2>&1
