# market-watch

Phone alerts when SPY, Bitcoin or Zcash makes an unusual move during the day, followed by a Claude-written report on why it's moving.

```
GitHub Actions, every 30 min, 8 AM to 11 PM Eastern
  └─ watch.py  (no AI, ~30 s): 5-minute prices from Yahoo + Google News headlines
       ├─ nothing unusual → stop
       └─ alert → phone push (ntfy)
                → Claude runs the move-report skill: web research, facts vs inferences, competing explanations
                → report saved to data/moves/live/, second push with the short "why" and a link
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
pip install -r requirements.txt
python watch.py --test      # self-check
python watch.py             # one live check, prints what it sees
python backtest.py          # replay the last 60 days of prices through the same rules
python backtest.py 5        # same, alerting only at 5x normal
```
The replay covers prices only; past headlines can't be replayed.

## Set it up on your own fork
1. Install the [ntfy](https://ntfy.sh) app and subscribe to a long random topic name. Anyone who knows the name can read the alerts.
2. Run `claude setup-token` (Claude Pro or Max) to get a token.
3. In the repo's Settings → Secrets and variables → Actions, add `NTFY_TOPIC` and `CLAUDE_CODE_OAUTH_TOKEN`.
4. Run the workflow once by hand from the Actions tab.

## Things to know
- GitHub starts scheduled runs late, or skips them when busy. Think "about every 30 minutes".
- The schedule is in UTC and set for winter time, so from March to November it runs an hour later.
- Yahoo sometimes rate-limits cloud servers. A missing asset is skipped and shows in the log.
- The headline filter is a keyword list. It will miss some causes and flag some noise.
- Claude gets no git or shell access beyond the data script, because it reads web pages. The workflow does the committing.
- Reports are written mid-move and marked LIVE. They're research notes, not trade signals.
