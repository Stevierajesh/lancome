"""Deterministic technical signals. These only *propose* trades — the judge decides.

Three tiers:
- Reactive signals (volume spike, price gap, momentum burst) — fast, designed
  to work with the scanner and news stream.
- Context signals (VWAP, relative volume, bid/ask pressure, sector correlation,
  multi-timeframe) — use richer data when available.
- Trend signals (SMA crossover, RSI extremes) — slow, high-conviction fallback.
"""

from dataclasses import dataclass, field
from datetime import datetime

from . import config


@dataclass
class Signal:
    symbol: str
    side: str  # "buy" or "sell"
    reason: str
    indicators: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Indicator helpers — basic
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
# Indicator helpers — context signals
# ---------------------------------------------------------------------------

def vwap(bars: list[dict]) -> float | None:
    """Volume-weighted average price from bars. Resets daily."""
    today = None
    cum_tp_vol = 0.0
    cum_vol = 0.0
    for b in bars:
        ts = b.get("t", "")
        try:
            day = ts[:10]
        except (TypeError, IndexError):
            continue
        if day != today:
            today = day
            cum_tp_vol = 0.0
            cum_vol = 0.0
        typical = (b["h"] + b["l"] + b["c"]) / 3
        vol = b["v"]
        cum_tp_vol += typical * vol
        cum_vol += vol
    if cum_vol == 0:
        return None
    return cum_tp_vol / cum_vol


def vwap_deviation_pct(bars: list[dict]) -> float | None:
    """(price - VWAP) / VWAP * 100."""
    v = vwap(bars)
    if v is None or v == 0:
        return None
    return (bars[-1]["c"] - v) / v * 100


def relative_volume_tod(bars: list[dict]) -> float | None:
    """Current bar volume vs average volume at the same time-of-day slot on prior days."""
    if len(bars) < 20:
        return None
    current = bars[-1]
    try:
        current_dt = datetime.fromisoformat(current["t"])
        current_slot = (current_dt.hour, current_dt.minute)
    except (ValueError, TypeError, KeyError):
        return None

    matching_vols = []
    for b in bars[:-1]:
        try:
            dt = datetime.fromisoformat(b["t"])
            if (dt.hour, dt.minute) == current_slot and dt.date() != current_dt.date():
                matching_vols.append(b["v"])
        except (ValueError, TypeError, KeyError):
            continue

    if len(matching_vols) < 2:
        return None
    avg = sum(matching_vols) / len(matching_vols)
    if avg == 0:
        return None
    return current["v"] / avg


def bid_ask_imbalance(quote: dict | None) -> float | None:
    """bid_size / (bid_size + ask_size). >0.5 = buying pressure, <0.5 = selling."""
    if not quote:
        return None
    bid_sz = quote.get("bid_size", 0) or 0
    ask_sz = quote.get("ask_size", 0) or 0
    total = bid_sz + ask_sz
    if total == 0:
        return None
    return bid_sz / total


def correlation_break_pct(bars: list[dict], benchmark_bars: list[dict],
                          lookback: int = 0) -> float | None:
    """Symbol return minus benchmark return over the last `lookback` bars."""
    lookback = lookback or config.CORRELATION_LOOKBACK
    if len(bars) < lookback + 1 or len(benchmark_bars) < lookback + 1:
        return None
    sym_old = bars[-(lookback + 1)]["c"]
    sym_now = bars[-1]["c"]
    bench_old = benchmark_bars[-(lookback + 1)]["c"]
    bench_now = benchmark_bars[-1]["c"]
    if sym_old == 0 or bench_old == 0:
        return None
    sym_ret = (sym_now - sym_old) / sym_old * 100
    bench_ret = (bench_now - bench_old) / bench_old * 100
    return sym_ret - bench_ret


def hourly_trend(hourly_bars: list[dict] | None) -> str | None:
    """'bullish', 'bearish', or None based on hourly SMA cross."""
    if not hourly_bars or len(hourly_bars) < config.HOURLY_SMA_SLOW + 2:
        return None
    closes = [b["c"] for b in hourly_bars]
    fast_now = sma(closes, config.HOURLY_SMA_FAST)
    slow_now = sma(closes, config.HOURLY_SMA_SLOW)
    fast_prev = sma(closes[:-1], config.HOURLY_SMA_FAST)
    slow_prev = sma(closes[:-1], config.HOURLY_SMA_SLOW)
    if fast_prev <= slow_prev and fast_now > slow_now:
        return "bullish"
    if fast_prev >= slow_prev and fast_now < slow_now:
        return "bearish"
    if fast_now > slow_now:
        return "bullish"
    if fast_now < slow_now:
        return "bearish"
    return None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(symbol: str, bars: list[dict], holding: bool, *,
             quote: dict | None = None,
             benchmark_bars: list[dict] | None = None,
             hourly_bars: list[dict] | None = None) -> Signal | None:
    """Return a candidate signal for this symbol, or None.

    Checks reactive signals first, then context signals (when extra data is
    provided), then trend signals as fallback.
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

    # Context indicators (computed even if not used as standalone signals)
    vwap_dev = vwap_deviation_pct(bars)
    rvol = relative_volume_tod(bars)
    imbalance = bid_ask_imbalance(quote)
    corr_break = correlation_break_pct(bars, benchmark_bars) if benchmark_bars else None
    h_trend = hourly_trend(hourly_bars)

    if vwap_dev is not None:
        indicators["vwap_deviation_pct"] = round(vwap_dev, 2)
    if rvol is not None:
        indicators["relative_volume_tod"] = round(rvol, 2)
    if imbalance is not None:
        indicators["bid_ask_imbalance"] = round(imbalance, 2)
    if corr_break is not None:
        indicators["correlation_break_pct"] = round(corr_break, 2)
    if h_trend is not None:
        indicators["hourly_trend"] = h_trend
    if quote:
        indicators["spread_pct"] = round(quote.get("spread_pct", 0), 3)

    spread_ok = not quote or quote.get("spread_pct", 0) <= config.SPREAD_MAX_PCT

    # === REACTIVE SIGNALS (fast) ===

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

    # === CONTEXT SIGNALS (need extra data) ===

    # VWAP mean reversion / overextension
    if vwap_dev is not None:
        if not holding and vwap_dev <= config.VWAP_DEVIATION_BUY_PCT:
            if mom is None or mom > -config.MOMENTUM_SELL_THRESHOLD:
                return Signal(symbol, "buy",
                              f"VWAP mean reversion (price {vwap_dev:.1f}% below VWAP)",
                              indicators)
        if holding and vwap_dev >= config.VWAP_DEVIATION_SELL_PCT:
            return Signal(symbol, "sell",
                          f"overextended above VWAP (+{vwap_dev:.1f}%)", indicators)

    # Relative volume time-of-day
    if rvol is not None and rvol >= config.RELATIVE_VOLUME_TOD_THRESHOLD:
        if not holding and mom is not None and mom > 0:
            return Signal(symbol, "buy",
                          f"unusual volume for time of day ({rvol:.1f}x normal) with uptick",
                          indicators)
        if holding and mom is not None and mom < 0:
            return Signal(symbol, "sell",
                          f"unusual volume for time of day ({rvol:.1f}x normal) with downtick",
                          indicators)

    # Bid/ask pressure
    if imbalance is not None and spread_ok:
        if not holding and imbalance >= config.BID_ASK_IMBALANCE_THRESHOLD:
            return Signal(symbol, "buy",
                          f"heavy bid-side pressure ({imbalance:.0%} bid imbalance)",
                          indicators)
        if holding and imbalance <= (1 - config.BID_ASK_IMBALANCE_THRESHOLD):
            return Signal(symbol, "sell",
                          f"heavy ask-side pressure ({1-imbalance:.0%} ask imbalance)",
                          indicators)

    # Sector correlation break
    if corr_break is not None:
        if not holding and corr_break >= config.CORRELATION_BREAK_PCT:
            return Signal(symbol, "buy",
                          f"outperforming benchmark by {corr_break:.1f}% (idiosyncratic strength)",
                          indicators)
        if holding and corr_break <= -config.CORRELATION_BREAK_PCT:
            return Signal(symbol, "sell",
                          f"underperforming benchmark by {abs(corr_break):.1f}% (idiosyncratic weakness)",
                          indicators)

    # Multi-timeframe confirmation
    if h_trend is not None:
        if not holding and h_trend == "bullish":
            if mom is not None and mom > 0:
                return Signal(symbol, "buy",
                              "hourly trend bullish with 5-min momentum confirming",
                              indicators)
        if holding and h_trend == "bearish":
            if mom is not None and mom < 0:
                return Signal(symbol, "sell",
                              "hourly trend bearish with 5-min momentum confirming",
                              indicators)

    # === TREND SIGNALS (slow, need more history) ===

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
