#  

Event-driven paper-trading bot for Alpaca. Scans the entire market for unusual
activity, streams real-time news, and feeds both into an LLM judge alongside
technical signals. Hard risk limits cap everything.

**Paper trading only.** Do not point this at a live account.

## How it works

The bot runs three concurrent loops:

- **Scanner** (every 2 min) — polls Alpaca's screener for top movers and most
  active stocks, building a dynamic watchlist that replaces the old hardcoded
  ticker list.
- **News stream** (real-time) — WebSocket subscription to all Alpaca/Benzinga
  headlines. Symbols mentioned in news are added to the dynamic watchlist and
  immediately evaluated through the signal engine (both entries and exits).
- **Tick loop** (every 5 min) — evaluates the dynamic watchlist (stocks when
  market is open) and crypto pairs (24/7) through a three-tier signal engine.

The signal engine runs three tiers in priority order:

1. **Reactive** — volume spikes, price gaps, momentum bursts. Fast signals
   designed to act on scanner and news activity.
2. **Context-aware** — VWAP mean reversion, time-of-day relative volume,
   bid/ask pressure, sector correlation breaks (vs SPY), multi-timeframe
   confirmation (hourly bars).
3. **Trend** — SMA crossover and RSI extremes. Slow, high-conviction fallback.

When a signal fires:

1. An **enrichment** step builds a case file: recent bars, news headlines,
   scanner context, and portfolio state.
2. The **LLM judge** filters the trade — approving unless there's a specific
   reason it will fail. Hard risk limits handle downside separately.
3. **Risk limits** enforce position sizing (10% of equity), max positions (4),
   daily loss halt (-2%), stop-loss (-2%), and take-profit (+3%).

All decisions are appended to `logs/trades.jsonl`; runtime logs go to
`logs/trader.log`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your Alpaca PAPER keys
```

Requires the `codex` CLI to be installed and logged in (default judge backend).
Alternatively, set `JUDGE_BACKEND=ollama` in `.env` and run a local model, or
`JUDGE_BACKEND=none` to auto-approve everything for testing.

## Run

```bash
make run                     # bot + dashboard together
# or separately:
python -m trader.main        # terminal 1: the bot
python -m trader.dashboard   # terminal 2: dashboard → http://localhost:8600
```

## Dashboard

The dashboard shows account stats, equity curve, open positions, the dynamic
watchlist, a news feed, and a filterable event log covering signals, verdicts,
entries, exits, news, and scanner discoveries. Full stack docs at `/docs`.

## Architecture

```
[News WebSocket]  ──→  event queue  ──→  main loop  ──→  enrich  ──→  judge  ──→  execute
[Scanner poll]    ──→  event queue  ──┘
[Tick timers]     ──→  (direct)     ──┘
```

## Modules

| File | Role |
|------|------|
| `trader/main.py` | Event-driven main loop |
| `trader/scanner.py` | Market screener + dynamic watchlist |
| `trader/news_stream.py` | WebSocket news consumer |
| `trader/enrichment.py` | Builds case files (bars + news + signals) for the judge |
| `trader/judge.py` | Pluggable LLM judge (codex, ollama, or none) |
| `trader/events.py` | Event types and thread-safe queue |
| `trader/signals.py` | Three-tier signal engine (reactive, context, trend) |
| `trader/risk.py` | Position sizing, caps, stop-loss/take-profit |
| `trader/broker.py` | Alpaca paper trading client wrapper |
| `trader/data.py` | Historical bars (5-min + hourly), latest quotes |
| `trader/dashboard.py` | Flask dashboard with REST APIs |
| `trader/config.py` | All tunables |

## Tuning

Everything lives in `trader/config.py` — scanner intervals, watchlist caps,
signal thresholds (reactive, context-aware, and trend), risk limits, judge
confidence threshold, and backend choice.
