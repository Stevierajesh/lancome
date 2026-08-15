"""Observability dashboard.

Run:  python -m trader.dashboard   (serves on http://localhost:8600)

Pages:   /          dashboard UI
         /docs      stack documentation
APIs:    /api/events    filtered/paginated event feed from logs/trades.jsonl
         /api/summary   account, positions, derived signal/judge stats
         /api/history   equity curve via Alpaca portfolio history
"""

import json
import logging
import os

from flask import Flask, jsonify, request, send_from_directory

from . import broker, config

log = logging.getLogger("dashboard")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
TRADES_FILE = os.path.join(config.LOG_DIR, "trades.jsonl")
STATUS_FILE = os.path.join(config.LOG_DIR, "status.json")
SCANNER_FILE = os.path.join(config.LOG_DIR, "scanner.json")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


def load_events() -> list[dict]:
    events = []
    if os.path.exists(TRADES_FILE):
        with open(TRADES_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return events


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/docs")
def docs():
    return send_from_directory(STATIC_DIR, "docs.html")


@app.get("/api/events")
def api_events():
    """Filtered, paginated event feed (newest first).

    Query params: symbol, event, decision, q (substring search), since (ISO ts),
    until (ISO ts), limit (default 100, max 1000), offset.
    """
    symbol = request.args.get("symbol", "").upper()
    event_type = request.args.get("event", "")
    decision = request.args.get("decision", "")
    q = request.args.get("q", "").lower()
    since = request.args.get("since", "")
    until = request.args.get("until", "")
    limit = min(int(request.args.get("limit", 100)), 1000)
    offset = max(int(request.args.get("offset", 0)), 0)

    def matches(e: dict) -> bool:
        if symbol and e.get("symbol", "").upper() != symbol:
            return False
        if event_type and e.get("event") != event_type:
            return False
        if decision and (e.get("verdict") or {}).get("decision") != decision:
            return False
        ts = e.get("ts", "")
        if since and ts < since:
            return False
        if until and ts > until:
            return False
        if q and q not in json.dumps(e).lower():
            return False
        return True

    events = load_events()
    events.reverse()  # newest first
    filtered = [e for e in events if matches(e)]

    symbols = sorted({e["symbol"] for e in events if e.get("symbol")})
    return jsonify({
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "symbols": symbols,
        "events": filtered[offset:offset + limit],
    })


@app.get("/api/summary")
def api_summary():
    account, positions, error = None, {}, None
    try:
        account = broker.get_account()
        positions = broker.get_positions()
    except Exception as e:  # dashboard should render even without Alpaca keys
        error = str(e)
        log.warning("alpaca unavailable: %s", e)

    events = load_events()
    signals = [e for e in events if e.get("event") == "signal"]
    approvals = [e for e in signals if (e.get("verdict") or {}).get("decision") == "approve"]
    stats = {
        "signals": len(signals),
        "approved": len(approvals),
        "vetoed": len(signals) - len(approvals),
        "approval_rate": round(len(approvals) / len(signals), 3) if signals else None,
        "entries": sum(1 for e in events if e.get("event") == "entry"),
        "exits": sum(1 for e in events if e.get("event") == "exit"),
        "avg_confidence": round(
            sum((e.get("verdict") or {}).get("confidence", 0) for e in signals) / len(signals), 3
        ) if signals else None,
    }
    bot = None
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE) as f:
                bot = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    # Scanner stats
    news_events = [e for e in events if e.get("event") == "news"]
    scanner_events = [e for e in events if e.get("event") == "scanner"]
    stats["news_events"] = len(news_events)
    stats["scanner_discoveries"] = len(scanner_events)
    stats["news_symbols"] = len({e["symbol"] for e in news_events if e.get("symbol")})

    return jsonify({"account": account, "positions": positions, "stats": stats,
                    "bot": bot, "error": error})


@app.get("/api/scanner")
def api_scanner():
    """Current dynamic watchlist from the scanner."""
    if not os.path.exists(SCANNER_FILE):
        return jsonify({"watchlist": [], "updated_at": None})
    try:
        with open(SCANNER_FILE) as f:
            return jsonify(json.load(f))
    except (json.JSONDecodeError, OSError):
        return jsonify({"watchlist": [], "updated_at": None})


@app.get("/api/news")
def api_news():
    """Recent news events (newest first)."""
    limit = min(int(request.args.get("limit", 50)), 200)
    symbol = request.args.get("symbol", "").upper()
    events = load_events()
    events.reverse()
    news = [e for e in events if e.get("event") == "news"]
    if symbol:
        news = [e for e in news if e.get("symbol", "").upper() == symbol]
    return jsonify({"total": len(news), "events": news[:limit]})


@app.get("/api/history")
def api_history():
    """Equity curve. Query params: period (1D/1W/1M/3M/1A, default 1M),
    timeframe (1Min/5Min/15Min/1H/1D, default 1D)."""
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    period = request.args.get("period", "1M")
    timeframe = request.args.get("timeframe", "1D")
    try:
        hist = broker.get_client().get_portfolio_history(
            GetPortfolioHistoryRequest(period=period, timeframe=timeframe)
        )
        return jsonify({
            "timestamp": hist.timestamp,
            "equity": hist.equity,
            "profit_loss": hist.profit_loss,
            "profit_loss_pct": hist.profit_loss_pct,
            "base_value": float(hist.base_value) if hist.base_value is not None else None,
        })
    except Exception as e:
        log.warning("portfolio history unavailable: %s", e)
        return jsonify({"error": str(e)}), 502


def main():
    import sys
    import threading
    import webbrowser

    logging.basicConfig(level=logging.INFO)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open("http://localhost:8600")).start()
    app.run(host="127.0.0.1", port=8600, debug=False)


if __name__ == "__main__":
    main()
