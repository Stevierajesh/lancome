"""Deterministic technical signals. These only *propose* trades — the judge decides.

Two tiers:
- Trend signals (SMA crossover, RSI extremes) — slow, high-conviction.
- Reactive signals (volume spike, price gap, momentum burst) — fast, designed
  to work with the scanner and news stream so the bot actually trades on the
  unusual activity it detects.
"""

from dataclasses import dataclass, field

from . import config


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    reason: str
    indicators: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

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


def volume_ratio(bars: list[dict], lookback: int = 20) -> float | None:
    """Current bar volume / average volume over last `lookback` bars."""
    if len(bars) < lookback + 1:
        return None
    recent_vols = [b["v"] for b in bars[-(lookback + 1):-1]]
    avg = sum(recent_vols) / len(recent_vols)
    if avg == 0:
        return None
    return bars[-1]["v"] / avg


def price_gap_pct(bars: list[dict]) -> float | None:
    """Gap between previous close and current open, as a percentage."""
    if len(bars) < 2:
        return None
    prev_close = bars[-2]["c"]
    if prev_close == 0:
        return None
    return (bars[-1]["o"] - prev_close) / prev_close * 100


def momentum(closes: list[float], period: int) -> float | None:
    """Rate of change over `period` bars, as a percentage."""
    if len(closes) < period + 1:
        return None
    old = closes[-(period + 1)]
    if old == 0:
        return None
    return (closes[-1] - old) / old * 100


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(symbol: str, bars: list[dict], holding: bool) -> Signal | None:
    """Return a candidate signal for this symbol, or None.

    Checks reactive signals first (volume spike, gap, momentum) since they
    respond to the kind of activity the scanner and news stream surface.
    Falls back to trend signals (SMA crossover, RSI extremes).
    """
    if len(bars) < 5:
        return None

    closes = [b["c"] for b in bars]
    price = closes[-1]

    vol_r = volume_ratio(bars)
    gap = price_gap_pct(bars)
    mom = momentum(closes, config.MOMENTUM_PERIOD)
    rsi_now = rsi(closes, config.RSI_PERIOD)

    indicators = {"price": price}
    if vol_r is not None:
        indicators["volume_ratio"] = round(vol_r, 2)
    if gap is not None:
        indicators["gap_pct"] = round(gap, 2)
    if mom is not None:
        indicators["momentum_pct"] = round(mom, 2)
    if rsi_now is not None:
        indicators["rsi"] = round(rsi_now, 2)

    # --- Reactive signals (fast) ---

    if not holding:
        if vol_r is not None and vol_r >= config.VOLUME_SPIKE_THRESHOLD:
            if mom is not None and mom > 0:
                return Signal(symbol, "buy",
                              f"volume spike ({vol_r:.1f}x avg) with positive momentum",
                              indicators)

        if gap is not None and gap >= config.GAP_UP_THRESHOLD:
            return Signal(symbol, "buy",
                          f"gap up {gap:.1f}% from previous close", indicators)

        if mom is not None and mom >= config.MOMENTUM_BUY_THRESHOLD:
            if rsi_now is None or rsi_now < config.RSI_OVERBOUGHT:
                return Signal(symbol, "buy",
                              f"momentum burst +{mom:.1f}% over {config.MOMENTUM_PERIOD} bars",
                              indicators)
    else:
        if gap is not None and gap <= -config.GAP_UP_THRESHOLD:
            return Signal(symbol, "sell",
                          f"gap down {gap:.1f}% from previous close", indicators)

        if mom is not None and mom <= -config.MOMENTUM_SELL_THRESHOLD:
            return Signal(symbol, "sell",
                          f"momentum drop {mom:.1f}% over {config.MOMENTUM_PERIOD} bars",
                          indicators)

    # --- Trend signals (slow, need more history) ---

    if len(closes) < config.SMA_SLOW + 2:
        return None

    fast_now = sma(closes, config.SMA_FAST)
    slow_now = sma(closes, config.SMA_SLOW)
    fast_prev = sma(closes[:-1], config.SMA_FAST)
    slow_prev = sma(closes[:-1], config.SMA_SLOW)

    indicators["sma_fast"] = round(fast_now, 4)
    indicators["sma_slow"] = round(slow_now, 4)

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
