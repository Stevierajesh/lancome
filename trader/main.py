"""Event-driven trading loop.

Combines timer-based polling (scanner, crypto, stocks) with real-time news
events from a WebSocket stream. All producers push to a shared Queue;
the main thread drains it and processes events sequentially.

Run:  python -m trader.main
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from queue import Empty

from alpaca.data.historical import NewsClient

from . import broker, config, data, risk, signals
from .enrichment import CaseFile, enrich
from .events import Event, EventType, event_queue
from .judge import JudgeBase, create_judge
from .news_stream import NewsStreamWorker
from .scanner import Scanner

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
SCANNER_FILE = os.path.join(config.LOG_DIR, "scanner.json")

# Position symbols drop the slash: data/order "BTC/USD" ↔ position "BTCUSD"
CRYPTO_BY_POS_SYMBOL = {s.replace("/", ""): s for s in config.CRYPTO_WATCHLIST}

# Signal cooldown: suppress the same signal (symbol+reason) for 30 min after a veto
# so the judge isn't spammed with identical setups every tick.
SIGNAL_COOLDOWN_SECONDS = 1800
_signal_cooldowns: dict[str, float] = {}


def _on_cooldown(symbol: str, reason: str) -> bool:
    key = f"{symbol}:{reason}"
    expires = _signal_cooldowns.get(key, 0)
    return time.time() < expires


def _set_cooldown(symbol: str, reason: str):
    key = f"{symbol}:{reason}"
    _signal_cooldowns[key] = time.time() + SIGNAL_COOLDOWN_SECONDS


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def write_heartbeat(stocks_open: bool):
    now = time.time()
    status = {
        "last_tick": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "next_tick": datetime.fromtimestamp(
            now + config.POLL_INTERVAL_SECONDS, timezone.utc).isoformat(),
        "interval_seconds": config.POLL_INTERVAL_SECONDS,
        "stocks_open": stocks_open,
    }
    _atomic_write(STATUS_FILE, status)


def write_scanner_state(scanner: Scanner):
    _atomic_write(SCANNER_FILE, {
        "watchlist": scanner.get_all_entries(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def _atomic_write(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def record(event: dict):
    event["ts"] = datetime.now(timezone.utc).isoformat()
    with open(TRADES_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")


# ---------------------------------------------------------------------------
# Hard exits (stop-loss / take-profit) — unchanged from original
# ---------------------------------------------------------------------------

def check_hard_exits(positions: dict, stocks_open: bool):
    for pos_symbol, pos in list(positions.items()):
        is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL
        if not is_crypto and not stocks_open:
            continue
        reason = risk.exit_reason(pos)
        if reason:
            broker.close_position(pos_symbol)
            record({"event": "exit", "symbol": pos_symbol, "reason": reason, "position": pos})
            del positions[pos_symbol]


# ---------------------------------------------------------------------------
# Execute a trade from a judged case
# ---------------------------------------------------------------------------

def execute_if_approved(case: CaseFile, verdict: dict, positions: dict, account: dict):
    """Place the trade if the judge approved and risk checks pass."""
    sig = case.signal
    if sig is None:
        return
    if verdict["decision"] != "approve" or verdict["confidence"] < config.JUDGE_MIN_CONFIDENCE:
        return

    symbol = sig.symbol
    pos_symbol = symbol.replace("/", "")
    is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL

    if sig.side == "buy":
        if risk.daily_loss_breached(account):
            log.info("skipping %s buy: daily loss limit", symbol)
            return
        if is_crypto:
            notional = risk.size_entry_notional(account, positions)
            if notional is None:
                return
            broker.submit_crypto_order(symbol, "buy", notional)
            record({"event": "entry", "symbol": symbol, "notional": notional,
                    "price": sig.indicators.get("price"), "rule": sig.reason})
        else:
            qty = risk.size_entry(account, positions, sig.indicators["price"])
            if qty is None:
                return
            broker.submit_market_order(symbol, "buy", qty)
            record({"event": "entry", "symbol": symbol, "qty": qty,
                    "price": sig.indicators["price"], "rule": sig.reason})
        positions[pos_symbol] = {"qty": 0}
    else:
        broker.close_position(pos_symbol)
        record({"event": "exit", "symbol": symbol, "reason": f"signal: {sig.reason}",
                "position": positions.get(pos_symbol)})
        positions.pop(pos_symbol, None)


# ---------------------------------------------------------------------------
# Tick functions (timer-driven, reuse existing signal logic)
# ---------------------------------------------------------------------------

def tick_crypto(judge: JudgeBase, news_client: NewsClient, scanner: Scanner,
                account: dict, positions: dict):
    """Evaluate crypto watchlist — runs every POLL_INTERVAL_SECONDS."""
    crypto_bars = data.get_crypto_bars(config.CRYPTO_WATCHLIST)
    for symbol in config.CRYPTO_WATCHLIST:
        bars = crypto_bars.get(symbol, [])
        pos_symbol = symbol.replace("/", "")
        sig = signals.evaluate(symbol, bars, holding=pos_symbol in positions)
        if sig is None:
            continue
        if _on_cooldown(symbol, sig.reason):
            continue
        case = CaseFile(
            symbol=symbol,
            bars=bars,
            signal=sig,
            scanner_context=scanner.get_entry(symbol) or {},
            portfolio={"account": account, "positions": positions},
        )
        log.info("signal: %s %s (%s)", sig.side, symbol, sig.reason)
        verdict = judge.evaluate(case)
        record({"event": "signal", "symbol": symbol, "side": sig.side,
                "rule": sig.reason, "indicators": sig.indicators, "verdict": verdict})
        log.info("judge: %s (%.2f) — %s", verdict["decision"], verdict["confidence"],
                 verdict["reason"])
        if verdict["decision"] == "approve":
            execute_if_approved(case, verdict, positions, account)
        else:
            _set_cooldown(symbol, sig.reason)


def tick_stocks(watchlist: list[str], judge: JudgeBase, news_client: NewsClient,
                scanner: Scanner, account: dict, positions: dict):
    """Evaluate stock watchlist — dynamic symbols from scanner + seed list."""
    if not watchlist:
        return
    stock_bars = data.get_bars(watchlist)
    for symbol in watchlist:
        bars = stock_bars.get(symbol, [])
        pos_symbol = symbol.replace("/", "")
        sig = signals.evaluate(symbol, bars, holding=pos_symbol in positions)
        if sig is None:
            continue
        if _on_cooldown(symbol, sig.reason):
            continue
        case = enrich(symbol, scanner, news_client, account, positions)
        case.signal = sig
        case.bars = bars
        log.info("signal: %s %s (%s) %s", sig.side, symbol, sig.reason, sig.indicators)
        verdict = judge.evaluate(case)
        record({"event": "signal", "symbol": symbol, "side": sig.side,
                "rule": sig.reason, "indicators": sig.indicators, "verdict": verdict})
        log.info("judge: %s (%.2f) — %s", verdict["decision"], verdict["confidence"],
                 verdict["reason"])
        if verdict["decision"] == "approve":
            execute_if_approved(case, verdict, positions, account)
        else:
            _set_cooldown(symbol, sig.reason)


# ---------------------------------------------------------------------------
# Event processing (news + scanner hits)
# ---------------------------------------------------------------------------

def process_event(event: Event, judge: JudgeBase, news_client: NewsClient,
                  scanner: Scanner, account: dict, positions: dict):
    if event.type == EventType.NEWS:
        scanner.add_from_news(event.symbol, event.payload.get("headline", ""))
        record({"event": "news", "symbol": event.symbol, **event.payload})

        symbol = event.symbol
        pos_symbol = symbol.replace("/", "")
        is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL

        if is_crypto or broker.market_is_open():
            log.info("news on %s — evaluating", symbol)
            case = enrich(symbol, scanner, news_client, account, positions)
            if case.signal:
                verdict = judge.evaluate(case)
                record({"event": "signal", "symbol": symbol, "side": case.signal.side,
                        "rule": case.signal.reason, "verdict": verdict, "trigger": "news"})
                log.info("news-triggered judge: %s (%.2f) — %s",
                         verdict["decision"], verdict["confidence"], verdict["reason"])
                execute_if_approved(case, verdict, positions, account)

    elif event.type == EventType.SCANNER_HIT:
        record({"event": "scanner", "symbol": event.symbol, **event.payload})


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    judge = create_judge()
    scanner = Scanner(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    scanner.load_state(SCANNER_FILE)
    news_client = NewsClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)

    # Start news WebSocket stream
    news_worker = None
    if config.NEWS_STREAM_ENABLED:
        news_worker = NewsStreamWorker(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
        news_worker.start()

    log.info("starting trader — seed watchlist: %s  crypto: %s  judge: %s  news_stream: %s",
             config.WATCHLIST, config.CRYPTO_WATCHLIST, config.JUDGE_BACKEND,
             config.NEWS_STREAM_ENABLED)

    last_scan = 0.0
    last_crypto_tick = 0.0
    last_stock_tick = 0.0
    stocks_open = False

    try:
        while True:
            now = time.time()

            # --- Timer: scanner poll + news fallback ---
            if now - last_scan > config.SCANNER_INTERVAL_SECONDS:
                try:
                    stocks_open = broker.market_is_open()
                    if stocks_open:
                        new_syms = scanner.poll_screener()
                        for sym in new_syms:
                            event_queue.put_nowait(Event(
                                EventType.SCANNER_HIT, sym,
                                payload=scanner.get_entry(sym) or {},
                            ))
                    scanner.prune()
                    write_scanner_state(scanner)
                except Exception:
                    log.exception("scanner poll failed")

                # REST news fallback when WebSocket is down
                if news_worker and not news_worker.is_streaming:
                    news_worker.poll_rest(news_client)

                last_scan = now

            # --- Timer: crypto tick (always) ---
            if now - last_crypto_tick > config.POLL_INTERVAL_SECONDS:
                try:
                    account = broker.get_account()
                    positions = broker.get_positions()
                    check_hard_exits(positions, stocks_open)
                    tick_crypto(judge, news_client, scanner, account, positions)
                except Exception:
                    log.exception("crypto tick failed")
                last_crypto_tick = now

            # --- Timer: stock tick (when market open) ---
            if stocks_open and now - last_stock_tick > config.POLL_INTERVAL_SECONDS:
                try:
                    account = broker.get_account()
                    positions = broker.get_positions()
                    check_hard_exits(positions, stocks_open)
                    # Merge seed watchlist + scanner discoveries
                    dynamic = list(set(config.WATCHLIST + scanner.get_watchlist()))
                    tick_stocks(dynamic, judge, news_client, scanner, account, positions)
                except Exception:
                    log.exception("stock tick failed")
                last_stock_tick = now

            # --- Drain event queue ---
            try:
                event = event_queue.get(timeout=1.0)
            except Empty:
                write_heartbeat(stocks_open)
                continue

            try:
                account = broker.get_account()
                positions = broker.get_positions()
                process_event(event, judge, news_client, scanner, account, positions)
            except Exception:
                log.exception("event processing failed for %s", event)

            write_heartbeat(stocks_open)

    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        if news_worker:
            news_worker.stop()


if __name__ == "__main__":
    main()
