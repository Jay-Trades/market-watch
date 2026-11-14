# market-watch

Phone alerts when SPY, Bitcoin or Zcash makes an unusual move during the day, followed by a Claude-written report on why it's moving.

```
GitHub Actions, every 30 min, 8 AM to 11 PM Eastern. Three jobs:
  watch     (no AI, ~15 s)  5-minute prices from Yahoo + Google News headlines
              ├─ nothing unusual → stop
              └─ alert → phone push (ntfy), save cooldown state
  research  (read-only)     Claude Code runs the move-report skill: web research, facts vs inferences,
                            competing explanations (~3 min). Its GitHub token can't write anything.
  publish   (no AI)         commit report + numbers to data/moves/live/, second push with the "why" and a link
```

## How "unusual" is decided
Each check compares the move over the last 1 hour, 4 hours and 24 hours (SPY: the day so far instead of 24 hours) with every other stretch of the same length over the past ~60 days. Bitcoin's normal hour is far bigger than SPY's, so measuring against each asset's own normal lets one rule cover all three.

- 4x normal or more: alert.
- 2.5x normal if a market headline with a cause word (buyback, rate cut, hack, sanctions, lawsuit...) landed in the last 2 hours.
- A few rare headlines alert with no move at all: emergency rate cut, trading halt, exchange hack.
- After an alert, that asset stays quiet for 6 hours unless the move grows by another 2x normal.

Knobs are at the top of `watch.py`.

## Try it before running it live
```
pip install -r requirements.txt     # or: uv pip install -r requirements.txt
python watch.py --test      # self-check
python watch.py             # one live check, prints what it sees
python backtest.py          # replay the last 60 days of prices through the same rules
python backtest.py 5        # same, alerting only at 5x normal
```
The replay covers prices only; past headlines can't be replayed.

## Set it up on your own fork
1. Install the [ntfy](https://ntfy.sh) app and subscribe to a long random topic name. Anyone who knows the name can read the alerts. Prefer Telegram or Discord? Both are in the workflow, commented out, with setup steps.
2. Get an Anthropic API key (pay per use, about $0.70 per report in testing). To use a Claude Pro/Max plan instead, run `claude setup-token` and follow the comment in the research job.
3. In the repo's Settings → Secrets and variables → Actions, add `NTFY_TOPIC` and `ANTHROPIC_API_KEY`.
4. Actions tab → market-watch → Run workflow, with **force** ticked. That turns the biggest current move into a test alert, so the whole chain runs whatever the market is doing.

## Things to know
- GitHub starts scheduled runs late, or skips them when busy. Think "about every 30 minutes".
- The schedule is in UTC and set for winter time, so from March to November it runs an hour later.
- Yahoo sometimes rate-limits cloud servers. A missing asset is skipped and shows in the log.
- Every package is pinned in `requirements.txt`, built from `requirements.in`, so runs install the same versions every time. If Yahoo changes and yfinance breaks, update on purpose: rerun the compile command at the top of `requirements.in` with `--upgrade`, then the self-check and the replay.
- The headline filter is a keyword list. It will miss some causes and flag some noise.
- Claude gets no git or shell access beyond the data script, because it reads web pages. The workflow does the committing.
- Reports are written mid-move and marked LIVE. They're research notes, not trade signals.
