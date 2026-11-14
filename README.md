# market-watch

**A quiet market alarm for people who can't watch screens all day.** It checks SPY, Bitcoin and Zcash every 30 minutes and stays silent unless something unusual is happening. When something is, you get a phone notification. A few minutes later you get a second one from Claude explaining what's driving the move, with a link to the full report.

## Why this exists

Most market alert tools fail in one of two ways:
- **Price alerts** ("BTC below $65k") fire on levels you set weeks ago, and say nothing about *why*.
- **News feeds** push every headline. Most of it is noise, so you learn to ignore it, and then you miss the one that mattered.

This is for the person with a day job and open positions, who only wants to be interrupted when a **potentially big move is starting**, usually because of macro news: a Fed or Treasury surprise, a policy shift, an exchange hack. That's when it's worth stepping out of a meeting to check your positions.

So the rule is: **no spam.** About 4 alerts a week across all three assets (see the backtest below), and each one comes with an explanation.

## How it decides

Two triggers, both measured against each asset's *own* normal behaviour:

**1. An outsized move.** Each check compares the last 1 hour, 4 hours and 24 hours (for SPY, the day so far) with every other stretch of the same length over the past ~60 days. A move of **4x normal** fires an alert.
Why "x normal" and not a fixed %: a 2% hour is routine for Zcash and a crash for SPY. Measuring each asset against itself lets one rule cover all three.

**2. Really big news.** It scans Google News every check for headlines that pair a market word (Fed, Treasury, SEC, bitcoin, SPY...) with a *cause* word (rate cut, buyback, hack, sanctions, lawsuit, delisting...).
- If a headline like that landed in the last 2 hours, the bar for an alert drops from 4x to **2.5x normal**. That catches a move while it's starting, when the news comes first and the price follows. Example: in August 2026, Treasury's surprise bond-buyback announcement came before bitcoin's three-day squeeze.
- A short list of rare events alerts even with no move yet: emergency rate cut, trading halt, circuit breaker, exchange hack.
- Headlines that only *describe* a move ("bitcoin crashes") don't count. They're the move, not a reason for it.

**It doesn't go quiet during a sustained dump or pump.** After an alert, that asset waits 6 hours before alerting again, so one selloff doesn't send you ten notifications. But if the move **keeps growing**, by another 2x normal, it alerts again inside those 6 hours. On Nov 5, 2026 the backtest alerted at 10:25 AM (SPY down 2.5% on the day) and again at 10:55 AM, when the slide had sped up to 9x normal for the hour.

## Backtest

`backtest.py` replays the last ~60 days of 5-minute prices through the exact same code, checking every 30 minutes with the same cooldown. Results for September to mid-November 2026:

| Asset | Alerts | Period | Days it caught |
|---|---|---|---|
| SPY | 6 | Sep 4 – Nov 13 | Fed hike day (Sep 16), Oct 30 selloff, Nov 5 slide (−4.4% day), Nov 9 rebound |
| Bitcoin | 13 | Sep 25 – Nov 14 | both sides of the Oct 29 exchange hack, Nov 4 and Nov 5 |
| Zcash | 12 | Sep 25 – Nov 14 | its 10–11% swings on Oct 15 and Nov 2 |

That's **about 4 alerts a week in total**, and several days (Oct 20, Nov 4, Nov 5) fired on more than one asset at once. Those were the real market days.

One thing the backtest exposed and fixed: SPY's first version fired at almost every market open, because its "last hour" included the overnight gap. Hourly checks now stay inside the trading day, and gaps are judged against a normal *day*. That cut SPY from 12 alerts to 6 and kept every real one.

**What it won't catch:** slow slides over several days, where no single hour or day is unusual. That's on purpose, since a slow slide isn't a reason to interrupt someone. The replay also tests prices only, because past headlines can't be replayed.

Run it yourself:
```
python backtest.py          # replay with the current settings
python backtest.py 5        # stricter: alert only at 5x normal
```

## What you get

**Push 1, within a minute:**
> Market alert: Bitcoin down 2.41% over 4h (3.1x normal)

**Push 2, a few minutes later**, from Claude, with a link to the full report:
> What: Bitcoin down 1.6% in the last hour to 64,540, below this morning's 65,000 low.
> Why: Likely stop-loss selling once 65,000 broke, on top of the weeklong Fed and ETF-outflow pressure.
> Confidence: Low for this hour, medium for the trend.
> Watch: 64,000 and 62,000, Monday's ETF flows.

The first push is the cheap check saying *something is moving*, so you hear about it within a minute. The second comes once Claude has finished researching why, usually 3 to 5 minutes later.

The full report keeps facts and inferences separate, gives at least two competing explanations with evidence against each, checks that a story's timing actually fits the price, and names its sources. It flags what it couldn't verify. Reports are written mid-move, marked LIVE, and saved in `data/moves/live/`. **They're research notes, not trade signals.**

## How it runs

```
GitHub Actions, every 30 min, 8 AM to 11 PM Eastern. Three jobs:
  watch     (no AI, ~15 s)  5-minute prices from Yahoo + Google News headlines
              ├─ nothing unusual → stop (most runs end here, at no cost)
              └─ alert → push 1 (ntfy), save cooldown state
  research  (read-only)     Claude Code researches the move (~3 min, ~$0.70 on an API key)
  publish   (no AI)         commit the report and the day's numbers, push 2 with a link
```

The check itself uses no AI, so quiet days cost nothing. Claude only runs when something fires.

Claude reads web pages, and a page could carry hidden instructions. So it runs in its own job with a **read-only** GitHub token, its only shell command is the data script, and the commits are made by a separate job with no AI.

## Set it up on your own fork

1. Install the [ntfy](https://ntfy.sh) app and subscribe to a long random topic name. Anyone who knows the name can read your alerts. Prefer Telegram or Discord? Both are in the workflow, commented out, with setup steps.
2. Get an Anthropic API key (pay per use, about $0.70 per report). To use a Claude Pro or Max plan instead, run `claude setup-token` and follow the comment in the research job.
3. In your fork: Settings → Secrets and variables → Actions → add `NTFY_TOPIC` and `ANTHROPIC_API_KEY`.
4. Actions tab → market-watch → Run workflow, with **force** ticked. That turns the biggest current move into a test alert, so you can check the whole chain whatever the market is doing.

To try it locally first:
```
pip install -r requirements.txt     # or: uv pip install -r requirements.txt
python watch.py --test              # self-check
python watch.py                     # one live check, prints what it sees
```

All the thresholds are at the top of `watch.py`.

## Things to know

- GitHub starts scheduled runs late, or skips them when busy. Think "about every 30 minutes", not real time.
- The schedule is in UTC and set for winter time, so from March to November it runs an hour later (9 AM to midnight Eastern).
- Yahoo sometimes rate-limits cloud servers. A missing asset is skipped and shows in the log.
- Google News lags the wire services by minutes. A professional news feed is faster, and costs money.
- The headline filter is a keyword list. It will miss some causes and flag some noise.
- Every package is pinned in `requirements.txt`, built from `requirements.in`. If Yahoo changes and yfinance breaks, update on purpose: rerun the compile command at the top of `requirements.in` with `--upgrade`, then run the self-check and the backtest.
