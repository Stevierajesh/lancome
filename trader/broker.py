"""Thin wrapper over the Alpaca paper-trading account."""

import logging
import time

import requests
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from . import config

log = logging.getLogger("broker")

# alpaca-py surfaces rejections as either APIError or a bare requests HTTPError
# depending on the endpoint, so callers have to handle both.
BrokerError = (APIError, requests.exceptions.HTTPError)

# How long to wait for a market order to report a fill price before giving up
# and falling back to the signal price.
FILL_POLL_SECONDS = 3.0
FILL_POLL_INTERVAL = 0.3

_client = None


def error_status(exc) -> int | None:
    """HTTP status behind a broker exception, or None if it isn't one."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return int(status)
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def get_client() -> TradingClient:
    global _client
    if _client is None:
        _client = TradingClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY, paper=True)
    return _client


def market_is_open() -> bool:
    return get_client().get_clock().is_open


def get_account() -> dict:
    acct = get_client().get_account()
    return {
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "last_equity": float(acct.last_equity),  # equity at previous close
    }


def get_positions() -> dict[str, dict]:
    positions = {}
    for p in get_client().get_all_positions():
        positions[p.symbol] = {
            "qty": float(p.qty),
            "avg_entry_price": float(p.avg_entry_price),
            "current_price": float(p.current_price),
            "unrealized_plpc": float(p.unrealized_plpc),
            "market_value": float(p.market_value),
        }
    return positions


def submit_market_order(symbol: str, side: str, qty: float):
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    result = get_client().submit_order(order_data=order)
    log.info("submitted %s %s x%s (order %s)", side, symbol, qty, result.id)
    return result


def submit_crypto_order(symbol: str, side: str, notional: float):
    """Crypto market order by dollar amount. Requires GTC (DAY is rejected)."""
    order = MarketOrderRequest(
        symbol=symbol,
        notional=round(notional, 2),
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
    )
    result = get_client().submit_order(order_data=order)
    log.info("submitted %s %s $%.2f (order %s)", side, symbol, notional, result.id)
    return result


def close_position(symbol: str):
    """Close a position. Returns None if it was already gone.

    Two code paths can race to close the same symbol (a hard exit and a
    judge-approved sell, or a tick and a news event), and Alpaca answers the
    loser with 403/404. That is a no-op, not a failure worth aborting for.
    """
    try:
        result = get_client().close_position(symbol)
    except BrokerError as e:
        if error_status(e) in (403, 404):
            log.info("position %s already closed", symbol)
            return None
        raise
    log.info("closed position %s", symbol)
    return result


def close_all_positions(cancel_orders: bool = True) -> list[dict]:
    """Liquidate the whole book. Manual reset switch — the bot never calls this.

    Pending orders are cancelled first, otherwise a resting order can fill
    straight back into a position you just flattened. Alpaca reports per-symbol
    status rather than failing as a unit, so partial failures are returned to
    the caller instead of raised.
    """
    results = get_client().close_all_positions(cancel_orders=cancel_orders)
    out = []
    for r in results or []:
        status = getattr(r, "status", None)
        out.append({
            "symbol": getattr(r, "symbol", None),
            "status": status,
            "ok": status in (200, 201, 204),
        })
    closed = sum(1 for r in out if r["ok"])
    log.info("liquidated %d/%d positions (orders cancelled=%s)", closed, len(out), cancel_orders)
    return out


def wait_for_fill(order, timeout: float = FILL_POLL_SECONDS) -> float | None:
    """Poll briefly for a market order's actual fill price.

    Orders come back unfilled, so `filled_avg_price` is None on submit. Without
    this the ledger records the signal-time bar close, which drifts badly on
    wide-spread names — WETO logged a 53.17 entry against a 47.55 fill.
    """
    if order is None:
        return None
    price = getattr(order, "filled_avg_price", None)
    if price:
        return float(price)

    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(FILL_POLL_INTERVAL)
        try:
            fresh = get_client().get_order_by_id(order.id)
        except BrokerError as e:
            log.debug("fill lookup for %s failed: %s", order.id, e)
            return None
        price = getattr(fresh, "filled_avg_price", None)
        if price:
            return float(price)
    log.debug("order %s had no fill price after %.1fs", order.id, timeout)
    return None
