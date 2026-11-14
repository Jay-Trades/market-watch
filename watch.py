#!/usr/bin/env python3
"""watch.py - intraday alert check for SPY, Bitcoin and Zcash. No AI. Runs on GitHub Actions every 30 minutes.

  watch.py          check prices and headlines; on a hit, update state.json and write GitHub step outputs
  watch.py --test   self-check

A move is "big" when the return over a window (1h, 4h, 24h or the day so far) is several times that asset's
normal return over the same window length, measured from its own last ~60 days of 5-minute bars.
Bitcoin's normal hour is much bigger than SPY's, so one threshold works for all three.
"""
import json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

STATE = Path(__file__).with_name("state.json")
ASSETS = {"SPY": "S&P 500 ETF", "BTC-USD": "Bitcoin", "ZEC-USD": "Zcash"}
WINDOWS = {"1h": 12, "4h": 48, "24h": 288}  # in 5-minute bars; SPY uses "day" (since last close) instead of 24h

# Calibration knobs.
Z_ALERT = 4.0     # window move vs normal for that window: alert on price alone
Z_DAY = 3.0       # SPY day-so-far vs a normal day (same as Z_MOVE in events.py)
Z_WITH_NEWS = 2.5 # lower bar when a market headline landed in the last 2 hours (the Aug 19 pattern)
COOLDOWN_H = 6    # no repeat alert for an asset this soon...
Z_STEP = 2.0      # ...unless the move got this much bigger
STALE_MIN = 40    # ignore an asset whose newest bar is older than this (SPY at night and weekends)

# Headlines: Google News search, last 2 hours. A hit needs a market word AND a cause word. Words that only
# describe a move (crash, plunge, soar) are left out: a "bitcoin crashes" headline is the move, not a reason for it.
NEWS_QUERY = ('Bessent OR Powell OR "Federal Reserve" OR Treasury OR SEC OR bitcoin OR zcash OR "S&P 500" '
              'OR tariff OR crypto when:2h')
# ponytail: keyword filter, expect some misses and false hits; swap for a cheap model rating headlines if it gets noisy.
MARKET = re.compile(r"\b(SEC|(?i:fed|fomc|powell|bessent|treasury|bitcoin|btc|zcash|zec|crypto|s&p|stocks?|"
                    r"yields?|bonds?|tariffs?|etfs?|exchanges?|stablecoins?))\b")  # SEC is case-sensitive: "Sec." is Secretary
MOVER = re.compile(r"\b(emergency|surprise|buybacks?|rate (cut|hike)|halt(ed|s)?|hack(ed)?|exploit|ban(s|ned)?|"
                   r"sanctions?|default|bankrupt\w*|delist\w*|approv\w*|reject\w*|intervention|"
                   r"lawsuit|sues|probe|resign\w*|ousted)\b", re.I)
STRONG = re.compile(r"emergency (rate|meeting|cut)|trading halt|circuit breaker|exchange hack|"
                    r"intermeeting|bank run", re.I)


def zscores(close: pd.Series, is_stock: bool) -> dict:
    """Window name -> (return %, how many times normal). Normal = std of the same-length return before this one."""
    out = {}
    for name, n in WINDOWS.items():
        if is_stock and name == "24h":
            continue
        r = close / close.shift(n) - 1
        if is_stock:  # keep SPY windows inside one session; the overnight gap belongs to "day", or every open fires
            d = pd.Series(close.index.date, close.index)
            r = r.where(d == d.shift(n))
        sd = r.iloc[:-n].std()
        if len(close) > 3 * n and sd > 0 and r.iloc[-1] == r.iloc[-1]:
            out[name] = (round(r.iloc[-1] * 100, 2), round(r.iloc[-1] / sd, 1))
    if is_stock:  # day so far, from the previous session's close
        days = close.groupby(close.index.date).last()
        if len(days) > 20:
            today = close.index[-1].date()
            prev = days[days.index < today].iloc[-1]
            sd = days.pct_change().iloc[:-1].std()
            r = close.iloc[-1] / prev - 1
            out["day"] = (round(r * 100, 2), round(r / sd, 1))
    return out


def prices(now):
    import yfinance as yf
    out = {}
    for sym in ASSETS:
        df = yf.Ticker(sym).history(period="60d", interval="5m")
        if df.empty:
            out[sym] = {"error": "no data from Yahoo"}
            continue
        c = df["Close"].tz_convert("UTC")
        age = (now - c.index[-1].to_pydatetime()).total_seconds() / 60
        out[sym] = {"last": round(float(c.iloc[-1]), 2), "bar_age_min": round(age),
                    "windows": {} if age > STALE_MIN else zscores(c, sym == "SPY")}
    return out


def headlines():
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": NEWS_QUERY, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 market-watch"})
    root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
    items = [{"title": i.findtext("title", ""), "link": i.findtext("link", ""), "at": i.findtext("pubDate", "")}
             for i in root.iter("item")]
    hits = [i for i in items if MARKET.search(i["title"]) and MOVER.search(i["title"])]
    return hits, [i for i in hits if STRONG.search(i["title"])]


def decide(px, news, strong, state, now):
    """Returns (alerts, new state). Pure, so the self-check can drive it."""
    alerts, state = [], dict(state)
    for sym, p in px.items():
        bar = Z_WITH_NEWS if news else Z_ALERT
        hit = [(w, pct, z) for w, (pct, z) in p.get("windows", {}).items()
               if abs(z) >= (Z_DAY if w == "day" and not news else bar)]
        if not hit:
            continue
        w, pct, z = max(hit, key=lambda h: abs(h[2]))
        last = state.get(sym)
        if last and now - datetime.fromisoformat(last["at"]) < timedelta(hours=COOLDOWN_H) \
                and abs(z) < abs(last["z"]) + Z_STEP:
            continue
        state[sym] = {"at": now.isoformat(), "z": z}
        alerts.append({"symbol": sym, "name": ASSETS[sym], "window": w, "pct": pct, "x_normal": z,
                       "with_news": bool(news)})
    seen = set(state.get("headlines", []))
    fresh = [h for h in strong if h["title"] not in seen]
    if fresh:
        alerts.append({"symbol": None, "headline": fresh[0]["title"], "link": fresh[0]["link"]})
    state["headlines"] = (list(seen) + [h["title"] for h in fresh])[-200:]
    return alerts, state


def describe(a):
    if a["symbol"] is None:
        return f"Headline: {a['headline']}"
    move = "up" if a["pct"] > 0 else "down"
    return f"{a['name']} {move} {abs(a['pct'])}% over {a['window']} ({abs(a['x_normal'])}x normal)"


def main():
    now = datetime.now(timezone.utc)
    px = prices(now)
    try:
        news, strong = headlines()
    except Exception as e:  # news is a bonus; a dead feed must not stop price alerts
        news, strong = [], []
        print("headlines failed:", e)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    alerts, state = decide(px, news, strong, state, now)
    print(json.dumps({"prices": px, "news_hits": [h["title"] for h in news], "alerts": alerts}, indent=1))
    if not alerts:
        return
    STATE.write_text(json.dumps(state, indent=1))
    title = "; ".join(describe(a) for a in alerts)
    stamp = now.strftime("%Y-%m-%d-%H%M")
    prompt = (f"/move-report LIVE intraday alert at {now:%Y-%m-%d %H:%M} UTC. Trigger: {title}. "
              f"Prices and windows: {json.dumps(px)}. Matching headlines from the last 2 hours: "
              f"{json.dumps(news[:10])}. "
              f"Write the report to data/moves/live/{stamp}-move.md and the summary "
              f"to data/moves/live/{stamp}-summary.txt. Find out why it is moving right now; the session isn't over.")
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a", encoding="utf-8") as f:
        f.write(f"fire=true\nstamp={stamp}\ntitle={title[:200]}\n")
        f.write(f"prompt<<EOF_PROMPT\n{prompt}\nEOF_PROMPT\n")


def test():
    import numpy as np
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-09-01", periods=6000, freq="5min", tz="UTC")
    calm = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.001, len(idx)))), idx)
    assert all(abs(z) < Z_ALERT for _, z in zscores(calm, False).values()), "random walk shouldn't alert"
    jump = calm.copy()
    jump.iloc[-6:] *= 1.05  # 5% in half an hour
    zs = zscores(jump, False)
    assert zs["1h"][1] >= Z_ALERT, zs
    now = idx[-1].to_pydatetime()
    px = {"BTC-USD": {"windows": zs}}
    a, st = decide(px, [], [], {}, now)
    assert a and a[0]["symbol"] == "BTC-USD"
    assert not decide(px, [], [], st, now + timedelta(hours=1))[0], "cooldown should hold"
    assert decide(px, [], [], st, now + timedelta(hours=COOLDOWN_H + 1))[0], "cooldown should expire"
    mild = {"BTC-USD": {"windows": {"1h": (1.0, 3.0)}}}
    assert not decide(mild, [], [], {}, now)[0] and decide(mild, [{"title": "x"}], [], {}, now)[0]
    h = [{"title": "Fed holds emergency meeting", "link": ""}]
    a, st = decide({}, h, h, {}, now)
    assert a and not decide({}, h, h, st, now)[0], "same headline only once"
    assert MARKET.search("Bessent doubles Treasury buybacks") and MOVER.search("Bessent doubles Treasury buybacks")
    print("ok")


if __name__ == "__main__":
    test() if sys.argv[1:] == ["--test"] else main()
