#!/usr/bin/env python3
"""backtest.py - replay the last ~60 days of 5-minute bars through watch.py's price rules, checking every 30 min.

  backtest.py [z]   list what would have fired (default threshold: watch.Z_ALERT). Price only: old headlines can't be replayed.
"""
import sys
from zoneinfo import ZoneInfo

import yfinance as yf

import watch

z_bar = float(sys.argv[1]) if len(sys.argv) > 1 else watch.Z_ALERT
watch.Z_ALERT = z_bar
for sym in watch.ASSETS:
    c = yf.Ticker(sym).history(period="60d", interval="5m")["Close"].tz_convert("UTC")
    state, fired = {}, []
    for i in range(len(c) // 6, len(c), 6):  # skip the first sixth so every window has some history
        part = c.iloc[:i]
        alerts, state = watch.decide({sym: {"windows": watch.zscores(part, sym == "SPY")}}, [], [], state,
                                     part.index[-1].to_pydatetime())
        fired += [(part.index[-1], a) for a in alerts]
    print(f"\n{sym}: {len(fired)} alerts from {c.index[len(c) // 6]:%b %d} to {c.index[-1]:%b %d}")
    for t, a in fired:
        print(f"  {t.astimezone(ZoneInfo("America/New_York")):%a %b %d %H:%M} ET  {watch.describe(a)}")
