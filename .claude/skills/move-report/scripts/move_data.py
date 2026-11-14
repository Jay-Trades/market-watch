#!/usr/bin/env python3
"""move_data.py - hard numbers for a big market day. No AI, no news, no writing to the db.

  move_data.py [YYYY-MM-DD] [--save]   full JSON (default: latest session); --save also writes data/moves/<date>-data.json
  move_data.py --check        list big days among finished sessions since the last --done
  move_data.py --done DATE    mark sessions through DATE as handled
  move_data.py --test         run the self-check

Intraday/overnight bars only exist for roughly the last 60 days.
"""
import json, sys, time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from events import REPORT, YEAR, regime

ET = "America/New_York"
ROOT = Path(__file__).resolve().parents[4]  # ai_os
OUT = ROOT / "data" / "moves"
LAST = OUT / ".last_check"
TICKERS = {
    "broad": {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000", "DIA": "Dow", "RSP": "S&P equal weight",
              "^GSPC": "S&P 500 index", "^IXIC": "Nasdaq Composite", "^DJI": "Dow index", "^SOX": "Philly Semiconductor index"},
    "fear_rates": {"^VIX": "VIX", "^VIX3M": "3-month VIX", "^TNX": "10Y yield", "^IRX": "3M yield",
                   "DX-Y.NYB": "Dollar index", "HYG": "High yield credit", "TLT": "Long Treasuries"},
    "sectors": {"SMH": "Semiconductors", "XLK": "Tech", "XLF": "Financials", "XLE": "Energy", "XLV": "Health care",
                "XLI": "Industrials", "XLY": "Consumer discretionary", "XLP": "Consumer staples",
                "XLU": "Utilities", "XLRE": "Real estate", "XLC": "Communications", "XLB": "Materials"},
    "semis_megacap": {"NVDA": "Nvidia", "AMD": "AMD", "QCOM": "Qualcomm", "INTC": "Intel", "AVGO": "Broadcom", "TSM": "TSMC", "MU": "Micron", "ASML": "ASML",
                      "AAPL": "Apple", "MSFT": "Microsoft", "META": "Meta", "GOOGL": "Alphabet", "AMZN": "Amazon",
                      "TSLA": "Tesla"},
    "other": {"BTC-USD": "Bitcoin", "ZEC-USD": "Zcash", "CL=F": "WTI oil", "GC=F": "Gold"},
}
PATHS = ("SPY", "QQQ", "SMH", "ES=F", "NQ=F", "BTC-USD", "ZEC-USD")  # ES/NQ trade overnight, so they show the real overnight low

# Full reports come from the regime rules in events.py (SPY, Bitcoin) plus this VIX rule.
VIX_JUMP = 25.0     # % up in a day


def daily(names, start=None):
    """Daily bars per ticker, skipping any Yahoo won't return. Yahoo can leave the newest daily close blank for hours
    after the bell (seen on Nov 5, 2026), which would drop the very day we care about, so that gets the last price.
    One ticker at a time, because parallel requests came back empty. `start` is for past dates: a fixed 3y window
    from today leaves too little history before an old day for the 252-day stats."""
    out = {}
    for t in names:
        for attempt in range(3):
            tick = yf.Ticker(t)
            # Yahoo returns nothing for ^VIX from start=2023-03-12 (a DST Sunday), so retries move start a day.
            df = (tick.history(start=start + timedelta(days=attempt), auto_adjust=False) if start
                  else tick.history(period="3y", auto_adjust=False))
            if not df.empty:
                break
            time.sleep(2 * (attempt + 1))
        if df.empty:
            continue
        df = df.tz_localize(None)
        if pd.isna(df["Close"].iloc[-1]) and pd.notna(df["High"].iloc[-1]):
            df.loc[df.index[-1], "Close"] = tick.fast_info.last_price
        out[t] = df
    return out


def rnd(x, n=2):
    return None if x is None or pd.isna(x) else round(float(x), n)


def rsi(close, n=14):
    d = close.diff()
    up, down = d.clip(lower=0).rolling(n).mean(), (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / down)


def stats(df, day):
    """One ticker's numbers for `day` from daily bars. "_before" fields stop at the prior close."""
    df = df.dropna(subset=["Close"])
    df = df[df.index.date <= day]
    if len(df) < 61 or df.index[-1].date() != day:
        return None
    c, o, h, l, v = df["Close"], df["Open"], df["High"], df["Low"], df["Volume"]
    prev = c.iloc[-2]
    ret = c.pct_change() * 100
    span = h.iloc[-1] - l.iloc[-1]
    avg_vol = v.iloc[-21:-1].mean()
    return {
        "close": rnd(c.iloc[-1]), "prev_close": rnd(prev), "day_pct": rnd(ret.iloc[-1]),
        "change": rnd(c.iloc[-1] - prev, 3),
        "gap_pct": rnd((o.iloc[-1] / prev - 1) * 100),
        "open_to_close_pct": rnd((c.iloc[-1] / o.iloc[-1] - 1) * 100),
        "range_pct": rnd(span / prev * 100),
        "close_in_range": rnd((c.iloc[-1] - l.iloc[-1]) / span) if span > 0 else None,  # 0 = closed on the low
        "z": rnd(ret.iloc[-1] / ret.iloc[-61:-1].std()),
        "vol_vs_20d": rnd(v.iloc[-1] / avg_vol) if avg_vol > 0 else None,
        "ret_5d_before": rnd((c.iloc[-2] / c.iloc[-7] - 1) * 100),
        "ret_20d_before": rnd((c.iloc[-2] / c.iloc[-22] - 1) * 100),
        "vs_ma20_before": rnd((prev / c.iloc[-21:-1].mean() - 1) * 100),
        "vs_ma50_before": rnd((prev / c.iloc[-51:-1].mean() - 1) * 100),
        "rsi14_before": rnd(rsi(c).iloc[-2], 1),
        "off_high_before": rnd((prev / h.iloc[-253:-1].max() - 1) * 100),
    }


def triggers(raw):
    """Maps date -> [(symbol, reason)] for days that get a full report."""
    why = {}
    for sym, year in REPORT.items():
        days = regime(raw[sym].dropna(subset=["Close"]).rename(columns=str.lower), year)
        for d, reasons in days.items():
            why.setdefault(d, []).append((sym, f"{sym} {', '.join(reasons)}"))
    vix = raw["^VIX"]["Close"].dropna()
    jump = vix.pct_change() * 100
    for d, p in jump[jump >= VIX_JUMP].items():
        why.setdefault(d.date().isoformat(), []).append(("^VIX", f"VIX up {p:.0f}% to {vix[d]:.1f}"))
    return why


def new_days(why, since, done):
    """Days to report. Stocks count through `done`. Bitcoin stops a day earlier, because its bar closes at 8pm ET."""
    out = {}
    for d, reasons in why.items():
        day = date.fromisoformat(d)
        for sym, text in reasons:
            shift = 1 if sym == "BTC-USD" else 0
            if since < day + timedelta(days=shift) <= done:
                out.setdefault(d, []).append(text)
    return dict(sorted(out.items()))


def session_path(bars, day):
    """Overnight and regular-session path from intraday bars (ET index, with extended hours)."""
    prior = bars[bars.index.date < day]
    if prior.empty or bars[bars.index.date == day].empty:
        return None
    start = pd.Timestamp(f"{prior.index.date[-1]} 16:00", tz=ET)
    open_ = pd.Timestamp(f"{day} 09:30", tz=ET)
    close = pd.Timestamp(f"{day} 16:00", tz=ET)
    before = bars[bars.index < start]
    if before.empty:
        return None
    base = float(before["Close"].iloc[-1])
    pct = lambda p: rnd((p / base - 1) * 100)
    out = {"base_prev_4pm": rnd(base)}
    for name, b in (("overnight", bars[(bars.index >= start) & (bars.index < open_)]),
                    ("regular", bars[(bars.index >= open_) & (bars.index < close)])):
        if b.empty:
            continue
        # ponytail: bar closes, not highs/lows, because Yahoo extended-hours wicks have bad prints (SPY showed -6% on a -0.4% night). Real extremes run slightly further.
        cl = b["Close"]
        lo, hi = cl.idxmin(), cl.idxmax()
        out[name] = {"low": rnd(cl.min()), "low_pct": pct(cl.min()), "low_at": lo.strftime("%a %H:%M ET"),
                     "high": rnd(cl.max()), "high_pct": pct(cl.max()), "high_at": hi.strftime("%a %H:%M ET"),
                     "last": rnd(b["Close"].iloc[-1]), "last_pct": pct(b["Close"].iloc[-1])}
    return out


def intraday(sym, day):
    df = yf.Ticker(sym).history(start=day - timedelta(days=5), end=day + timedelta(days=1),
                                interval="5m", prepost=True, auto_adjust=False)
    if df.empty:
        return None
    df.index = df.index.tz_convert(ET)
    return session_path(df, day)


def trailing(q, day, close, lag=35):
    """Trailing P/E and sales growth from quarterly statements known by `day`.
    ponytail: a quarter counts as known `lag` days after it ends (typical 10-Q timing), not its real report date."""
    if q is None or q.empty or "Diluted EPS" not in q.index:
        return {}
    q = q[[c for c in q.columns if c.date() <= day - timedelta(days=lag)]]
    q = q[sorted(q.columns, reverse=True)]
    eps = q.loc["Diluted EPS"].dropna()
    out = {"last_quarter_known": str(q.columns[0].date()) if len(q.columns) else None}
    if len(eps) >= 4 and eps.iloc[:4].sum() > 0:
        out["trailing_eps"] = rnd(eps.iloc[:4].sum())
        out["trailing_pe"] = rnd(close / eps.iloc[:4].sum(), 1)
    rev = q.loc["Total Revenue"].dropna() if "Total Revenue" in q.index else pd.Series(dtype=float)
    if len(rev) >= 5:
        out["sales_growth_yoy_pct"] = rnd((rev.iloc[0] / rev.iloc[4] - 1) * 100, 0)
    if len(eps) >= 5 and eps.iloc[4] > 0:
        out["eps_growth_yoy_pct"] = rnd((eps.iloc[0] / eps.iloc[4] - 1) * 100, 0)
    return out


def valuations(rows, day):
    """Key numbers for the single stocks. Yahoo keeps about 5 quarters, so older days get little or nothing.
    Forward P/E is today's analyst figure, so it's only given when the day is recent."""
    out = {}
    recent = (date.today() - day).days <= 7
    for t in TICKERS["semis_megacap"]:
        if t not in rows:
            continue
        try:
            tick = yf.Ticker(t)
            v = trailing(tick.quarterly_income_stmt, day, rows[t]["close"])
            i = tick.info
            if i.get("financialCurrency", "USD") != i.get("currency", "USD"):  # TSMC reports in TWD, ASML in EUR
                v.pop("trailing_pe", None); v.pop("trailing_eps", None)  # growth rates are still fine
            if recent:
                v.update(forward_pe_now=rnd(i.get("forwardPE"), 1), gross_margin_now=rnd(i.get("grossMargins"), 2))
        except Exception as e:
            v = {"error": str(e)[:80]}
        if v:
            out[t] = v
    return out


def main(day=None):
    names = {t: n for m in TICKERS.values() for t, n in m.items()}
    raw = daily(list(names), day - timedelta(days=500) if day else None)
    day = day or raw["SPY"].dropna(subset=["Close"]).index[-1].date()
    rows, degraded = {}, []
    for t in names:
        r = stats(raw[t], day) if t in raw else None
        if r:
            rows[t] = r
        else:
            degraded.append(f"{t}: no daily data for {day}")
    paths = {}
    for sym in PATHS:
        try:
            paths[sym] = intraday(sym, day)
            if paths[sym] is None:
                degraded.append(f"{sym} intraday: no bars (over ~60 days old?)")
        except Exception as e:
            paths[sym] = None
            degraded.append(f"{sym} intraday: {e}")
    now = pd.Timestamp.now(tz=ET)
    spy, smh, rsp = rows.get("SPY", {}), rows.get("SMH", {}), rows.get("RSP", {})
    vix, vix3m = rows.get("^VIX"), rows.get("^VIX3M")
    sectors = [(t, rows[t]["day_pct"]) for t in TICKERS["sectors"] if t in rows]
    recent = {}
    for t in ("SPY", "QQQ", "SMH", "^VIX"):
        c = raw[t]["Close"].dropna()
        c = c[c.index.date <= day]
        recent[t] = {str(d.date()): rnd(p) for d, p in (c.pct_change() * 100).tail(12).items()}
    why = [text for _, text in triggers(raw).get(day.isoformat(), [])]
    return {
        "date": day.isoformat(),
        "as_of": now.isoformat(timespec="minutes"),
        "session_complete": day < now.date() or now.hour >= 16,
        "big_day": bool(why),
        "triggers": why,
        "session_paths": paths,
        "last_12_sessions_pct": recent,
        "sectors_worst_to_best": [f"{t} {p:+.2f}%" for t, p in sorted(sectors, key=lambda s: s[1] or 0)],
        "setup_before_the_move": {
            "vix_prev_close": vix and vix["prev_close"],
            "vix_vs_3m_prev": rnd(vix["prev_close"] / vix3m["prev_close"], 3) if vix and vix3m else None,
            "spy_5d": spy.get("ret_5d_before"), "spy_rsi14": spy.get("rsi14_before"),
            "spy_vs_ma50": spy.get("vs_ma50_before"), "spy_off_high": spy.get("off_high_before"),
            "smh_5d": smh.get("ret_5d_before"), "smh_rsi14": smh.get("rsi14_before"),
            "smh_vs_ma50": smh.get("vs_ma50_before"), "smh_off_high": smh.get("off_high_before"),
            "equal_weight_minus_spy_20d": rnd(rsp["ret_20d_before"] - spy["ret_20d_before"]) if rsp and spy else None,
            "hyg_5d": rows.get("HYG", {}).get("ret_5d_before"),
        },
        "tickers": {g: {t: dict(rows[t], name=n) for t, n in m.items() if t in rows} for g, m in TICKERS.items()},
        "valuations": valuations(rows, day),
        "degraded": degraded,
    }


def check():
    """Check every finished session since the last --done. Prints "date<TAB>reasons" per big day, then "LAST date"."""
    raw = daily(list(YEAR) + ["^VIX"])
    newest = {s: raw[s].dropna(subset=["Close"]).index[-1].date() for s in ("SPY", "QQQ", "^VIX")}
    if len(set(newest.values())) > 1:
        sys.exit(f"Yahoo bars disagree, retry next run: {newest}")  # nonzero exit, so the day isn't marked done
    now = pd.Timestamp.now(tz=ET)
    done = now.date() if now.hour >= 16 else now.date() - timedelta(days=1)
    if done - timedelta(days=1) not in raw["BTC-USD"].dropna(subset=["Close"]).index.date:
        sys.exit("Bitcoin's last finished day has no close yet, retry next run")
    since = date.fromisoformat(LAST.read_text().strip()) if LAST.exists() else done - timedelta(days=1)
    for d, reasons in new_days(triggers(raw), since, done).items():
        print(f"{d}\t{'; '.join(reasons)}")
    print("LAST", done)


def test():
    why = {"2026-11-07": [("BTC-USD", "weekend")], "2026-11-06": [("SPY", "friday"), ("BTC-USD", "friday btc")]}
    assert new_days(why, date(2026, 11, 5), date(2026, 11, 6)) == {"2026-11-06": ["friday"]}
    assert new_days(why, date(2026, 11, 6), date(2026, 11, 7)) == {"2026-11-06": ["friday btc"]}
    assert new_days(why, date(2026, 11, 7), date(2026, 11, 8)) == {"2026-11-07": ["weekend"]}
    i = pd.date_range("2024-01-01", periods=320, freq="B")
    calm = pd.DataFrame({"Close": 100 + (pd.Series(range(320), index=i) % 2) * 0.1}, index=i)
    calm["High"], calm["Low"] = calm["Close"] + 0.2, calm["Close"] - 0.2
    frames = {s: calm for s in YEAR} | {"^VIX": calm.copy()}
    frames["^VIX"].iloc[-1, 0] = 130.0
    assert [r[0][0] for r in triggers(frames).values()] == ["^VIX"]
    idx = pd.date_range("2026-11-03 15:55", "2026-11-04 10:00", freq="5min", tz=ET)
    b = pd.DataFrame({"Open": 100.0, "High": 100.0, "Low": 100.0, "Close": 100.0}, index=idx)
    b.loc[pd.Timestamp("2026-11-04 03:00", tz=ET), "Close"] = 98.0
    b.loc[pd.Timestamp("2026-11-04 04:00", tz=ET), "Low"] = 50.0  # bad wick must be ignored
    p = session_path(b, date(2026, 11, 4))
    assert p["overnight"]["low_pct"] == -2.0 and "03:00" in p["overnight"]["low_at"], p
    qs = pd.to_datetime(["2026-08-31", "2026-05-31", "2026-02-28", "2025-11-30", "2025-08-31"])
    q = pd.DataFrame([[9, 4, 3, 2, 1], [90, 40, 30, 20, 10]], index=["Diluted EPS", "Total Revenue"], columns=qs)
    v = trailing(q, date(2026, 9, 20), 100)  # Aug 31 quarter isn't known yet on Sep 20
    assert v["trailing_eps"] == 10 and v["trailing_pe"] == 10 and v["last_quarter_known"] == "2026-05-31", v
    assert trailing(q, date(2026, 10, 10), 100)["eps_growth_yoy_pct"] == 800
    print("ok")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if a[:1] == ["--test"]:
        test()
    elif a[:1] == ["--check"]:
        check()
    elif a[:1] == ["--done"]:
        LAST.write_text(date.fromisoformat(a[1]).isoformat())  # refuses anything that isn't a date
    else:
        save = "--save" in a
        a = [x for x in a if x != "--save"]
        res = main(date.fromisoformat(a[0]) if a else None)
        text = json.dumps(res, indent=1, default=str)
        if save:
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{res['date']}-data.json").write_text(text, encoding="utf-8")
        print(text)
