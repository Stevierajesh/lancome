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


def record(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


def tick():
    account = broker.get_account()
    positions = broker.get_positions()
    log.info("equity=%.2f cash=%.2f positions=%s", account["equity"], account["cash"],
             list(positions) or "none")

    # 1. Hard exits (stop-loss / take-profit) — no judge needed
    for symbol, pos in list(positions.items()):
        reason = risk.exit_reason(pos)
        if reason:
            broker.close_position(symbol)
            record({"event": "exit", "symbol": symbol, "reason": reason, "position": pos})
            del positions[symbol]

    # 2. Evaluate signals
    all_bars = data.get_bars(config.WATCHLIST)
    no_new_entries = risk.daily_loss_breached(account)

    for symbol in config.WATCHLIST:
        bars = all_bars.get(symbol, [])
        signal = signals.evaluate(symbol, bars, holding=symbol in positions)
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
            qty = risk.size_entry(account, positions, signal.indicators["price"])
            if qty is None:
                continue
            broker.submit_market_order(symbol, "buy", qty)
            record({"event": "entry", "symbol": symbol, "qty": qty,
                    "price": signal.indicators["price"], "rule": signal.reason})
            positions[symbol] = {"qty": qty}  # placeholder until next tick
        else:
            broker.close_position(symbol)
            record({"event": "exit", "symbol": symbol, "reason": f"signal: {signal.reason}",
                    "position": positions.get(symbol)})
            positions.pop(symbol, None)


def main():
    log.info("starting paper trader — watchlist: %s", config.WATCHLIST)
    while True:
        try:
            if broker.market_is_open():
                tick()
            else:
                log.info("market closed — sleeping")
        except Exception:
            log.exception("tick failed")
        time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
