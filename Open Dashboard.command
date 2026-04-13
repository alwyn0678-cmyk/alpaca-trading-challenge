#!/bin/bash
# Double-click this file to launch the dashboard
cd "$(dirname "$0")"
echo "Starting Alpaca Dashboard at http://localhost:5050 ..."
open "http://localhost:5050"
/usr/local/bin/python3 dashboard.py
