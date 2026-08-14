"""Thin wrapper over the Alpaca paper-trading account."""

import logging

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from . import config

log = logging.getLogger("broker")

_client = None


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
    result = get_client().close_position(symbol)
    log.info("closed position %s", symbol)
    return result
