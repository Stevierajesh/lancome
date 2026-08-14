"""Main loop: poll bars, evaluate signals, consult judge, place paper trades.

Run:  python -m trader.main
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from . import broker, config, data, judge, risk, signals

os.makedirs(config.LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(config.LOG_DIR, "trader.log")),
    ],
)
log = logging.getLogger("main")

TRADES_FILE = os.path.join(config.LOG_DIR, "trades.jsonl")
STATUS_FILE = os.path.join(config.LOG_DIR, "status.json")


def write_heartbeat(stocks_open: bool):
    now = time.time()
    status = {
        "last_tick": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "next_tick": datetime.fromtimestamp(
            now + config.POLL_INTERVAL_SECONDS, timezone.utc).isoformat(),
        "interval_seconds": config.POLL_INTERVAL_SECONDS,
        "stocks_open": stocks_open,
    }
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(status, f)
    os.replace(tmp, STATUS_FILE)  # atomic so the dashboard never reads a partial file


def record(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


# Position symbols drop the slash: data/order "BTC/USD" ↔ position "BTCUSD"
CRYPTO_BY_POS_SYMBOL = {s.replace("/", ""): s for s in config.CRYPTO_WATCHLIST}


def tick(stocks_open: bool):
    account = broker.get_account()
    positions = broker.get_positions()
    log.info("equity=%.2f cash=%.2f positions=%s stocks_open=%s", account["equity"],
             account["cash"], list(positions) or "none", stocks_open)

    # 1. Hard exits (stop-loss / take-profit) — no judge needed
    for pos_symbol, pos in list(positions.items()):
        is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL
        if not is_crypto and not stocks_open:
            continue  # don't queue stock exits while the market is closed
        reason = risk.exit_reason(pos)
        if reason:
            broker.close_position(pos_symbol)
            record({"event": "exit", "symbol": pos_symbol, "reason": reason, "position": pos})
            del positions[pos_symbol]

    # 2. Evaluate signals: crypto always, stocks only while the market is open
    universe: list[tuple[str, list[dict], bool]] = []  # (symbol, bars, is_crypto)
    crypto_bars = data.get_crypto_bars(config.CRYPTO_WATCHLIST)
    universe += [(s, crypto_bars.get(s, []), True) for s in config.CRYPTO_WATCHLIST]
    if stocks_open:
        stock_bars = data.get_bars(config.WATCHLIST)
        universe += [(s, stock_bars.get(s, []), False) for s in config.WATCHLIST]

    no_new_entries = risk.daily_loss_breached(account)

    for symbol, bars, is_crypto in universe:
        pos_symbol = symbol.replace("/", "")
        signal = signals.evaluate(symbol, bars, holding=pos_symbol in positions)
        if signal is None:
            continue
        if signal.side == "buy" and no_new_entries:
            log.info("skipping %s buy: daily loss limit", symbol)
            continue

        log.info("signal: %s %s (%s) %s", signal.side, symbol, signal.reason, signal.indicators)
        portfolio = {"account": account, "positions": positions}
        verdict = judge.judge_signal(signal, bars, portfolio)
        record({"event": "signal", "symbol": symbol, "side": signal.side,
                "rule": signal.reason, "indicators": signal.indicators, "verdict": verdict})
        log.info("judge: %s (%.2f) — %s", verdict["decision"], verdict["confidence"],
                 verdict["reason"])

        if verdict["decision"] != "approve" or verdict["confidence"] < config.JUDGE_MIN_CONFIDENCE:
            continue

        if signal.side == "buy":
            if is_crypto:
                notional = risk.size_entry_notional(account, positions)
                if notional is None:
                    continue
                broker.submit_crypto_order(symbol, "buy", notional)
                record({"event": "entry", "symbol": symbol, "notional": notional,
                        "price": signal.indicators["price"], "rule": signal.reason})
            else:
                qty = risk.size_entry(account, positions, signal.indicators["price"])
                if qty is None:
                    continue
                broker.submit_market_order(symbol, "buy", qty)
                record({"event": "entry", "symbol": symbol, "qty": qty,
                        "price": signal.indicators["price"], "rule": signal.reason})
            positions[pos_symbol] = {"qty": 0}  # placeholder until next tick
        else:
            broker.close_position(pos_symbol)
            record({"event": "exit", "symbol": symbol, "reason": f"signal: {signal.reason}",
                    "position": positions.get(pos_symbol)})
            positions.pop(pos_symbol, None)


def main():
    log.info("starting paper trader — stocks: %s crypto: %s",
             config.WATCHLIST, config.CRYPTO_WATCHLIST)
    while True:
        stocks_open = False
        try:
            stocks_open = broker.market_is_open()
            tick(stocks_open=stocks_open)
        except Exception:
            log.exception("tick failed")
        write_heartbeat(stocks_open)
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
