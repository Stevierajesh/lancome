"""Deterministic technical signals. These only *propose* trades — the judge decides."""

from dataclasses import dataclass, field

from . import config


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    reason: str
    indicators: dict = field(default_factory=dict)


def sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for prev, curr in zip(closes[-period - 1:-1], closes[-period:]):
        change = curr - prev
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def evaluate(symbol: str, bars: list[dict], holding: bool) -> Signal | None:
    """Return a candidate signal for this symbol, or None."""
    closes = [b["c"] for b in bars]
    if len(closes) < config.SMA_SLOW + 2:
        return None

    fast_now = sma(closes, config.SMA_FAST)
    slow_now = sma(closes, config.SMA_SLOW)
    fast_prev = sma(closes[:-1], config.SMA_FAST)
    slow_prev = sma(closes[:-1], config.SMA_SLOW)
    rsi_now = rsi(closes, config.RSI_PERIOD)

    indicators = {
        "price": closes[-1],
        "sma_fast": round(fast_now, 4),
        "sma_slow": round(slow_now, 4),
        "rsi": round(rsi_now, 2) if rsi_now is not None else None,
    }

    crossed_up = fast_prev <= slow_prev and fast_now > slow_now
    crossed_down = fast_prev >= slow_prev and fast_now < slow_now

    if not holding:
        if crossed_up and (rsi_now is None or rsi_now < config.RSI_OVERBOUGHT):
            return Signal(symbol, "buy", "SMA fast crossed above slow", indicators)
        if rsi_now is not None and rsi_now < config.RSI_OVERSOLD:
            return Signal(symbol, "buy", f"RSI oversold ({rsi_now:.1f})", indicators)
    else:
        if crossed_down:
            return Signal(symbol, "sell", "SMA fast crossed below slow", indicators)
        if rsi_now is not None and rsi_now > config.RSI_OVERBOUGHT:
            return Signal(symbol, "sell", f"RSI overbought ({rsi_now:.1f})", indicators)

    return None
