"""Hard risk limits. These run regardless of what the signals or judge say."""

import logging
import math

from . import config

log = logging.getLogger("risk")


def daily_loss_breached(account: dict) -> bool:
    if account["last_equity"] <= 0:
        return False
    drawdown = (account["last_equity"] - account["equity"]) / account["last_equity"]
    if drawdown >= config.DAILY_LOSS_LIMIT_PCT:
        log.warning("daily loss limit hit (%.2f%%) — no new entries", drawdown * 100)
        return True
    return False


def _entry_budget(account: dict, positions: dict) -> float | None:
    if len(positions) >= config.MAX_OPEN_POSITIONS:
        log.info("max open positions reached (%d)", config.MAX_OPEN_POSITIONS)
        return None
    budget = account["equity"] * config.MAX_POSITION_PCT
    return min(budget, account["cash"])


def size_entry(account: dict, positions: dict, price: float) -> int | None:
    """Return share qty for a new stock entry, or None if not allowed."""
    budget = _entry_budget(account, positions)
    if budget is None:
        return None
    qty = math.floor(budget / price)
    return qty if qty >= 1 else None


def size_entry_notional(account: dict, positions: dict) -> float | None:
    """Return dollar amount for a new crypto entry, or None if not allowed."""
    budget = _entry_budget(account, positions)
    if budget is None or budget < 10:  # skip dust orders
        return None
    return budget


def exit_reason(position: dict) -> str | None:
    """Stop-loss / take-profit check on an open position."""
    plpc = position["unrealized_plpc"]
    if plpc <= -config.STOP_LOSS_PCT:
        return f"stop-loss ({plpc:.2%})"
    if plpc >= config.TAKE_PROFIT_PCT:
        return f"take-profit ({plpc:.2%})"
    return None
