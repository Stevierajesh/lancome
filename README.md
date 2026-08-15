# Paper Trader

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
  headlines. Symbols mentioned in news are added to the dynamic watchlist; news
  on held positions triggers immediate re-evaluation.
- **Tick loop** (every 5 min) — evaluates the dynamic watchlist (stocks when
  market is open) and crypto pairs (24/7) through the signal engine.

When a signal fires:

1. An **enrichment** step builds a case file: recent bars, news headlines,
   scanner context, and portfolio state.
2. The **LLM judge** reviews the case and approves or vetoes with a confidence
   score. The judge now sees news context, not just price bars.
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
| `trader/signals.py` | SMA crossover + RSI signal engine |
| `trader/risk.py` | Position sizing, caps, stop-loss/take-profit |
| `trader/broker.py` | Alpaca paper trading client wrapper |
| `trader/data.py` | Historical bar fetching (stocks + crypto) |
| `trader/dashboard.py` | Flask dashboard with REST APIs |
| `trader/config.py` | All tunables |

## Tuning

Everything lives in `trader/config.py` — scanner intervals, watchlist caps,
indicator periods, risk limits, judge confidence threshold, and backend choice.
