#!/usr/bin/env python3
"""events.py - find candidate events for SPY and Bitcoin from stored daily bars. No AI.

  events.py          write ai_os/data/moves/candidates.csv and print counts
  events.py --test   self-check

Every limit scales with the asset's own volatility, so a "big" Bitcoin day and a "big" SPY day differ.
"""
import csv, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]  # ai_os
DB = ROOT / "data" / "market_history.db"
OUT = ROOT / "data" / "moves" / "candidates.csv"
YEAR = {"SPY": 252, "QQQ": 252, "BTC-USD": 365}  # Bitcoin trades every day
REPORT = {"BTC-USD": 365, "SPY": 252}  # short notes and full reports cover these two
# ponytail: scheduled.csv is typed in from the Fed and BLS calendars; extend it each year.
with open(Path(__file__).with_name("scheduled.csv"), encoding="utf-8") as f:
    SCHEDULED = {r["date"]: r["event"] for r in csv.DictReader(f)}

# Calibration knobs.
Z_MOVE = 3.0        # day's move vs the asset's normal day (the larger of its 60-day and 1-year volatility)
BAR_SIZE = 1.5      # day's true range vs its 20-day average
QUIET_LEVEL = 20    # a new yearly high/low only counts if there was none in this many sessions
HOLD_SIDE = 20      # 200-day cross only counts if price sat on the other side this many sessions
CONFIRM = 3         # ...and it then holds for this many closes in a row
DECISIVE_ATR = 1.0  # ...or clears the line by this many daily ranges on the day
LONG_RANGE = 63     # regime: about 3 months of trading days
QUIET = 20          # regime: no break of that range in this many sessions


def load(sym, con):
    df = pd.read_sql("SELECT ts, open, high, low, close FROM bars WHERE symbol=? AND interval='1d' ORDER BY ts",
                     con, params=(sym,), parse_dates=["ts"]).set_index("ts")
    return df


def detect(df, year=252):
    """Returns one row per event. Assumes daily bars. Later bars only ever confirm a cross, never move its date."""
    c, h, l = df["close"], df["high"], df["low"]
    ret = c.pct_change() * 100
    sigma = pd.concat([ret.shift(1).rolling(60).std(), ret.shift(1).rolling(year).std()], axis=1).max(axis=1)
    z = ret / sigma
    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.shift(1).rolling(20).mean()
    bar = tr / atr
    hi = h.shift(1).rolling(year).max()
    lo = l.shift(1).rolling(year).min()
    new_hi, new_lo = c > hi, c < lo
    ma = c.rolling(200).mean()
    side = np.sign(c - ma).where(ma.notna())
    held = (side.shift(1).rolling(HOLD_SIDE).max() == side.shift(1).rolling(HOLD_SIDE).min())
    cross = (side != side.shift(1)) & side.notna() & side.shift(1).notna()

    out = []
    idx = df.index
    def add(i, kind, detail):
        out.append({"date": idx[i].date().isoformat(), "type": kind, "detail": detail,
                    "close": round(c.iloc[i], 2), "day_pct": round(ret.iloc[i], 2),
                    "x_normal": None if pd.isna(z.iloc[i]) else round(abs(z.iloc[i]), 1),
                    "bar_size": None if pd.isna(bar.iloc[i]) else round(bar.iloc[i], 1),
                    "next_day_pct": round((c.iloc[i + 1] / c.iloc[i] - 1) * 100, 2) if i + 1 < len(c) else None,
                    "next_5d_pct": round((c.iloc[i + 5] / c.iloc[i] - 1) * 100, 2) if i + 5 < len(c) else None})

    for i in range(len(df)):
        if idx[i].date().isoformat() in SCHEDULED:
            add(i, "scheduled", SCHEDULED[idx[i].date().isoformat()])
        if z.iloc[i] == z.iloc[i] and abs(z.iloc[i]) >= Z_MOVE and bar.iloc[i] >= BAR_SIZE:
            add(i, "big_move", "up" if z.iloc[i] > 0 else "down")
        if i >= QUIET_LEVEL and new_hi.iloc[i] and not new_hi.iloc[i - QUIET_LEVEL:i].any():
            add(i, "year_high", "first new yearly high in 20+ sessions")
        if i >= QUIET_LEVEL and new_lo.iloc[i] and not new_lo.iloc[i - QUIET_LEVEL:i].any():
            add(i, "year_low", "first new yearly low in 20+ sessions")
        if cross.iloc[i] and held.iloc[i]:
            s = side.iloc[i]
            kept = i + CONFIRM <= len(c) and (side.iloc[i:i + CONFIRM] == s).all()
            decisive = abs(c.iloc[i] - ma.iloc[i]) >= DECISIVE_ATR * atr.iloc[i]
            if kept or decisive:
                add(i, "ma200_reclaim" if s > 0 else "ma200_lost",
                    "held 3 closes" if kept else "decisive, failed to hold")
    return out, int(cross.sum())


def true_range(df):
    c, h, l = df["close"], df["high"], df["low"]
    return pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)


def regime(df, year):
    """Days that deserve a full report: maps day -> reasons."""
    c, h, l = df["close"], df["high"], df["low"]
    found, out = detect(df, year)[0], {}
    for e in found:
        if e["type"] == "big_move":
            out.setdefault(e["date"], []).append("huge bar")
        elif e["type"] == "scheduled" and (e["x_normal"] or 0) >= 2:
            out.setdefault(e["date"], []).append(f"big reaction to {e['detail']}")
    tr = true_range(df)
    wide = tr / tr.rolling(14).mean().shift() >= 1.5
    for name, broke in (("range breakout", c > h.shift().rolling(LONG_RANGE).max()),
                        ("range breakdown", c < l.shift().rolling(LONG_RANGE).min())):
        first = broke & (broke.shift().rolling(QUIET).sum() == 0) & wide
        for t in df.index[first]:
            out.setdefault(t.date().isoformat(), []).append(f"{name} of {LONG_RANGE}+ days")
    ma = c.rolling(200).mean()
    side = (c > ma).where(ma.notna())
    held = side.shift().rolling(year).min() == side.shift().rolling(year).max()
    for i in (held & (side != side.shift())).to_numpy().nonzero()[0]:
        if (side.iloc[i:i + 3] == side.iloc[i]).all():
            word = "reclaimed" if side.iloc[i] else "lost"
            out.setdefault(df.index[i].date().isoformat(), []).append(f"{word} SMA200 after a year on the other side")
    return out


def main():
    con = sqlite3.connect(DB)
    rows = []
    for sym, year in YEAR.items():
        df = load(sym, con)
        found, raw_cross = detect(df, year)
        rows += [dict(e, symbol=sym) for e in found]
        kept = sum(e["type"].startswith("ma200") for e in found)
        print(f"{sym}: {df.index[0].date()} to {df.index[-1].date()}, 200-day crosses raw {raw_cross}, after filter {kept}")
    ev = pd.DataFrame(rows).sort_values(["date", "symbol"])
    key = ["date", "symbol", "type"]
    if OUT.exists():  # keep hand marks across reruns
        old = pd.read_csv(OUT, dtype=str, keep_default_na=False)[key + ["keep", "note"]]
        ev = ev.merge(old, on=key, how="left")
    else:
        ev["keep"], ev["note"] = "", ""
    ev[["keep", "note"]] = ev[["keep", "note"]].fillna("")
    ev = ev[["keep", "note", "date", "symbol", "type", "detail", "close", "day_pct", "x_normal", "bar_size",
             "next_day_pct", "next_5d_pct"]]
    ev.to_csv(OUT, index=False)
    print(ev.groupby(["symbol", "type"]).size().to_string())
    print(len(ev), "total ->", OUT)


def test():
    n = 320
    # quiet walk with one 8% shock, then a slide under and hold below the 200-day
    rng = np.random.default_rng(1)
    c = pd.Series(100 * np.cumprod(1 + rng.normal(0.0008, 0.004, n)))
    c.iloc[250:] *= 1.10
    c.iloc[300:] *= 0.75
    df = pd.DataFrame({"close": c, "high": c * 1.002, "low": c * 0.998})
    df.index = pd.date_range("2024-01-01", periods=n, freq="B")
    found, raw = detect(df)
    kinds = {e["type"] for e in found}
    assert {"big_move", "ma200_lost", "year_high"} <= kinds, kinds
    moves = [e for e in found if e["type"] == "big_move"]
    assert moves[0]["detail"] == "up" and moves[-1]["detail"] == "down"
    SCHEDULED["2024-06-03"] = "test event"
    assert [e for e in detect(df)[0] if e["type"] == "scheduled" and e["date"] == "2024-06-03"]
    del SCHEDULED["2024-06-03"]
    # a one-day poke through the line that fails is dropped
    c2 = pd.Series(np.r_[np.linspace(100, 120, 230), np.linspace(120, 121, 30)])
    pos = 240
    c2.iloc[pos] = c2.iloc[pos] * 0 + df["close"].iloc[0] * 0 + 112.5
    d2 = pd.DataFrame({"close": c2, "high": c2 + 0.05, "low": c2 - 0.05})
    d2.index = pd.date_range("2024-01-01", periods=len(c2), freq="B")
    f2, _ = detect(d2)
    assert not [e for e in f2 if e["type"].startswith("ma200")], f2
    print("ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    test() if sys.argv[1:] == ["--test"] else main()
