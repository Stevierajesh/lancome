"""Event-driven trading loop.

Combines timer-based polling (scanner, crypto, stocks) with real-time news
events from a WebSocket stream. All producers push to a shared Queue;
the main thread drains it and processes events sequentially.

Run:  python -m trader.main
"""

import csv
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
from .scanner import Scanner, is_us_equity

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
TRADES_CSV = os.path.join(config.LOG_DIR, "trades.csv")
STATUS_FILE = os.path.join(config.LOG_DIR, "status.json")
SCANNER_FILE = os.path.join(config.LOG_DIR, "scanner.json")

CSV_FIELDS = ["timestamp", "symbol", "side", "qty", "notional", "price", "rule", "reason",
              "confidence", "equity", "cash"]

# Position symbols drop the slash: data/order "BTC/USD" ↔ position "BTCUSD"
CRYPTO_BY_POS_SYMBOL = {s.replace("/", ""): s for s in config.CRYPTO_WATCHLIST}

# Signal cooldown: suppress the same signal (symbol+reason) for 30 min after a veto
# so the judge isn't spammed with identical setups every tick.
SIGNAL_COOLDOWN_SECONDS = 1800
_signal_cooldowns: dict[str, float] = {}

# Symbols Alpaca refuses to trade (422 on submit). AIXC gapped 12%, the judge
# approved it twice at ~20s a call, and both orders bounced. Once rejected, a
# symbol is dropped for the rest of the session rather than re-judged.
_untradable: set[str] = set()


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


def record_trade(side: str, symbol: str, price, rule: str, account: dict | None = None,
                 confidence: float = 0, reason: str = "", qty=None, notional=None):
    write_header = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "side": side,
            "qty": qty or "",
            "notional": notional or "",
            "price": price or "",
            "rule": rule,
            "reason": reason,
            "confidence": confidence,
            "equity": account.get("equity", "") if account else "",
            "cash": account.get("cash", "") if account else "",
        })


# ---------------------------------------------------------------------------
# Hard exits (stop-loss / take-profit)
# ---------------------------------------------------------------------------

def check_hard_exits(positions: dict, stocks_open: bool, account: dict):
    for pos_symbol, pos in list(positions.items()):
        is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL
        if not is_crypto and not stocks_open:
            continue
        if "unrealized_plpc" not in pos:
            continue  # placeholder written by a same-tick entry, not priced yet
        reason = risk.exit_reason(pos)
        if reason:
            order = _submit(broker.close_position, pos_symbol)
            if order is False:
                continue
            fill = broker.wait_for_fill(order)
            record({"event": "exit", "symbol": pos_symbol, "reason": reason,
                    "fill_price": fill, "position": pos})
            record_trade("sell", pos_symbol, fill or pos.get("current_price"), reason,
                         account=account, qty=pos.get("qty"))
            del positions[pos_symbol]


# ---------------------------------------------------------------------------
# Execute a trade from a judged case
# ---------------------------------------------------------------------------

def execute_if_approved(case: CaseFile, verdict: dict, positions: dict, account: dict):
    """Place the trade if the judge approved and risk checks pass.

    Broker rejections are contained here: a symbol Alpaca won't accept must not
    take down the rest of the watchlist's evaluation cycle.
    """
    sig = case.signal
    if sig is None:
        return
    if verdict["decision"] != "approve" or verdict["confidence"] < config.JUDGE_MIN_CONFIDENCE:
        return

    symbol = sig.symbol
    pos_symbol = symbol.replace("/", "")
    is_crypto = pos_symbol in CRYPTO_BY_POS_SYMBOL
    signal_price = sig.indicators.get("price")

    if sig.side == "buy":
        if risk.daily_loss_breached(account):
            log.info("skipping %s buy: daily loss limit", symbol)
            return
        if is_crypto:
            notional = risk.size_entry_notional(account, positions)
            if notional is None:
                return
            order = _submit(broker.submit_crypto_order, symbol, "buy", notional)
            if not order:
                return
            fill = broker.wait_for_fill(order)
            record({"event": "entry", "symbol": symbol, "notional": notional,
                    "price": signal_price, "fill_price": fill, "rule": sig.reason})
            record_trade("buy", symbol, fill or signal_price, sig.reason,
                         account=account, confidence=verdict["confidence"],
                         reason=verdict["reason"], notional=notional)
        else:
            if signal_price is None:
                return
            qty = risk.size_entry(account, positions, signal_price)
            if qty is None:
                return
            order = _submit(broker.submit_market_order, symbol, "buy", qty)
            if not order:
                return
            fill = broker.wait_for_fill(order)
            record({"event": "entry", "symbol": symbol, "qty": qty,
                    "price": signal_price, "fill_price": fill, "rule": sig.reason})
            record_trade("buy", symbol, fill or signal_price, sig.reason,
                         account=account, confidence=verdict["confidence"],
                         reason=verdict["reason"], qty=qty)
        positions[pos_symbol] = {"qty": 0}
    else:
        pos = positions.get(pos_symbol, {})
        order = _submit(broker.close_position, pos_symbol)
        # close_position returns None when the position was already gone; that
        # is still a successful exit, so only a raised rejection aborts here.
        if order is False:
            return
        fill = broker.wait_for_fill(order)
        record({"event": "exit", "symbol": symbol, "reason": f"signal: {sig.reason}",
                "fill_price": fill, "position": pos or None})
        record_trade("sell", symbol, fill or pos.get("current_price"), sig.reason,
                     account=account, confidence=verdict["confidence"],
                     reason=verdict["reason"], qty=pos.get("qty"))
        positions.pop(pos_symbol, None)


def _submit(fn, symbol: str, *args):
    """Run a broker call, absorbing per-symbol rejections.

    Returns the order, None when there was nothing to do, or False when the
    order was rejected. Anything Alpaca refuses outright (422) gets the symbol
    blocklisted so we stop paying ~20s of judge time to re-propose it.
    """
    try:
        return fn(symbol, *args)
    except broker.BrokerError as e:
        status = broker.error_status(e)
        if status == 422:
            _untradable.add(symbol)
            _untradable.add(symbol.replace("/", ""))
            log.warning("%s rejected by broker (422) — blocklisted for this session", symbol)
        else:
            log.warning("order for %s failed (%s): %s", symbol, status, e)
        record({"event": "rejected", "symbol": symbol, "status": status, "error": str(e)[:300]})
        return False


# ---------------------------------------------------------------------------
# Tick functions (timer-driven, reuse existing signal logic)
# ---------------------------------------------------------------------------

def tick_crypto(judge: JudgeBase, news_client: NewsClient, scanner: Scanner,
                account: dict, positions: dict):
    """Evaluate crypto watchlist — runs every POLL_INTERVAL_SECONDS."""
    crypto_bars = data.get_crypto_bars(config.CRYPTO_WATCHLIST)
    try:
        crypto_hourly = data.get_crypto_hourly_bars(config.CRYPTO_WATCHLIST)
    except Exception:
        log.debug("crypto hourly bars fetch failed, continuing without")
        crypto_hourly = {}
    for symbol in config.CRYPTO_WATCHLIST:
        if symbol in _untradable:
            continue
        try:
            bars = crypto_bars.get(symbol, [])
            pos_symbol = symbol.replace("/", "")
            sig = signals.evaluate(
                symbol, bars, holding=pos_symbol in positions,
                hourly_bars=crypto_hourly.get(symbol),
            )
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
        except Exception:
            log.exception("evaluation failed for %s — skipping", symbol)


def tick_stocks(watchlist: list[str], judge: JudgeBase, news_client: NewsClient,
                scanner: Scanner, account: dict, positions: dict):
    """Evaluate stock watchlist — dynamic symbols from scanner + seed list."""
    # The bars/quotes fetches below are batched, so one symbol Alpaca rejects
    # takes the whole request down with it. Screen the list before asking.
    watchlist = [s for s in watchlist if is_us_equity(s) and s not in _untradable]
    if not watchlist:
        return
    stock_bars = data.get_bars(watchlist)
    try:
        quotes = data.get_latest_quotes(watchlist)
    except Exception:
        log.debug("quote fetch failed, continuing without")
        quotes = {}
    try:
        hourly = data.get_hourly_bars(watchlist)
    except Exception:
        log.debug("hourly bars fetch failed, continuing without")
        hourly = {}
    spy_bars = stock_bars.get("SPY", [])
    for symbol in watchlist:
        try:
            bars = stock_bars.get(symbol, [])
            pos_symbol = symbol.replace("/", "")
            sig = signals.evaluate(
                symbol, bars, holding=pos_symbol in positions,
                quote=quotes.get(symbol),
                benchmark_bars=spy_bars if symbol != "SPY" else None,
                hourly_bars=hourly.get(symbol),
            )
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
        except Exception:
            log.exception("evaluation failed for %s — skipping", symbol)


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

        if symbol in _untradable or not (is_crypto or is_us_equity(symbol)):
            return

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
    last_exit_check = 0.0
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

            # --- Timer: hard exits (stop-loss / take-profit) ---
            # Runs once per cycle. The crypto and stock ticks share an interval,
            # so when this lived inside both they fired seconds apart on
            # independently fetched position dicts and raced to close the same
            # symbol — the loser got a 403 that killed the whole tick.
            if now - last_exit_check > config.POLL_INTERVAL_SECONDS:
                try:
                    account = broker.get_account()
                    positions = broker.get_positions()
                    check_hard_exits(positions, stocks_open, account)
                except Exception:
                    log.exception("hard exit check failed")
                last_exit_check = now

            # --- Timer: crypto tick (always) ---
            if now - last_crypto_tick > config.POLL_INTERVAL_SECONDS:
                try:
                    account = broker.get_account()
                    positions = broker.get_positions()
                    tick_crypto(judge, news_client, scanner, account, positions)
                except Exception:
                    log.exception("crypto tick failed")
                last_crypto_tick = now

            # --- Timer: stock tick (when market open) ---
            if stocks_open and now - last_stock_tick > config.POLL_INTERVAL_SECONDS:
                try:
                    account = broker.get_account()
                    positions = broker.get_positions()
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
