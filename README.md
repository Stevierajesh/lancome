# Paper Trader

Hybrid intraday paper-trading bot for Alpaca: deterministic technical signals propose
trades, an LLM judge (via `codex exec`, running on your ChatGPT subscription — no
per-token API cost) approves or vetoes them, and hard risk limits cap everything.

**Paper trading only.** Do not point this at a live account.

## How it works

Every 5 minutes (crypto pairs 24/7; stocks only during market hours):

1. Stop-loss (-2%) / take-profit (+3%) exits on open positions (no judge).
2. Fetch 5-min bars (free IEX feed) for the watchlist.
3. Signals: SMA 9/21 crossover + RSI 14 oversold/overbought.
4. When a signal fires, `codex exec` is asked to approve/veto with a JSON verdict
   (`--output-schema` enforces the shape; any error fails safe to veto).
5. Approved buys are sized at max 10% of equity, max 4 open positions, and blocked
   entirely if the account is down 2% on the day.

All decisions are appended to `logs/trades.jsonl`; runtime logs go to `logs/trader.log`.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then paste your Alpaca PAPER keys
```

Requires the `codex` CLI to be installed and logged in.

## Run

```bash
python -m trader.main        # terminal 1: the bot
python -m trader.dashboard   # terminal 2: dashboard → http://localhost:8600
```

The dashboard (`/`) shows account stats, the equity curve, open positions, and a
filterable/searchable feed of every signal, verdict, entry, and exit. Full stack
documentation is served at `/docs`.

## Tuning

Everything lives in `trader/config.py` — watchlist, indicator periods, risk caps,
judge confidence threshold. Set `JUDGE_ENABLED = False` to run rules-only as a baseline.
