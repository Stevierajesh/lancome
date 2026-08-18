"""Indicator maths and the three-tier signal engine."""

import pytest

from trader import config, signals


class TestSMA:
    def test_mean_of_the_window(self):
        assert signals.sma([1, 2, 3, 4, 5], 5) == 3.0

    def test_uses_only_the_tail(self):
        assert signals.sma([100, 1, 2, 3], 3) == 2.0

    def test_none_when_short(self):
        assert signals.sma([1, 2], 5) is None


class TestRSI:
    def test_all_gains_pins_to_100(self):
        assert signals.rsi(list(range(1, 20)), 14) == 100.0

    def test_all_losses_pins_to_zero(self):
        assert signals.rsi(list(range(20, 1, -1)), 14) == pytest.approx(0.0)

    def test_flat_series_is_neutral_by_convention(self):
        """No losses means no denominator — the code pins to 100."""
        assert signals.rsi([10.0] * 20, 14) == 100.0

    def test_none_when_short(self):
        assert signals.rsi([1, 2, 3], 14) is None

    def test_bounded(self):
        closes = [10, 12, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18, 17, 19]
        assert 0.0 <= signals.rsi(closes, 14) <= 100.0


class TestVolumeRatio:
    def test_spike_against_the_average(self, bars):
        b = bars(count=25, volume=100.0)
        b[-1]["v"] = 250.0
        assert signals.volume_ratio(b, lookback=20) == pytest.approx(2.5)

    def test_none_when_short(self, bars):
        assert signals.volume_ratio(bars(count=5), lookback=20) is None

    def test_none_when_no_prior_volume(self, bars):
        b = bars(count=25, volume=0.0)
        b[-1]["v"] = 100.0
        assert signals.volume_ratio(b, lookback=20) is None


class TestPriceGap:
    def test_gap_up(self, bars):
        b = bars(count=3, price=100.0)
        b[-1]["o"] = 102.0
        assert signals.price_gap_pct(b) == pytest.approx(2.0)

    def test_gap_down(self, bars):
        b = bars(count=3, price=100.0)
        b[-1]["o"] = 98.0
        assert signals.price_gap_pct(b) == pytest.approx(-2.0)

    def test_none_when_short(self, bars):
        assert signals.price_gap_pct(bars(count=1)) is None


class TestMomentum:
    def test_rate_of_change(self):
        assert signals.momentum([100, 101, 102, 103, 104, 110], 5) == pytest.approx(10.0)

    def test_none_when_short(self):
        assert signals.momentum([100, 101], 5) is None

    def test_zero_base_is_safe(self):
        assert signals.momentum([0, 0, 0, 0, 0, 10], 5) is None


class TestVWAP:
    def test_flat_series_equals_price(self, bars):
        assert signals.vwap(bars(count=10, price=100.0)) == pytest.approx(100.0)

    def test_resets_each_day(self):
        b = [{"t": "2026-08-17T14:00:00", "o": 1, "h": 50, "l": 50, "c": 50, "v": 1000},
             {"t": "2026-08-18T14:00:00", "o": 1, "h": 100, "l": 100, "c": 100, "v": 1000}]
        assert signals.vwap(b) == pytest.approx(100.0)

    def test_none_without_volume(self, bars):
        assert signals.vwap(bars(count=5, volume=0.0)) is None

    def test_deviation_is_signed(self, bars):
        b = bars(count=10, price=100.0)
        b[-1]["c"] = 110.0
        assert signals.vwap_deviation_pct(b) > 0


class TestBidAskImbalance:
    def test_balanced(self):
        assert signals.bid_ask_imbalance({"bid_size": 50, "ask_size": 50}) == 0.5

    def test_bid_heavy(self):
        assert signals.bid_ask_imbalance({"bid_size": 80, "ask_size": 20}) == 0.8

    def test_none_without_a_quote(self):
        assert signals.bid_ask_imbalance(None) is None

    def test_none_when_empty(self):
        assert signals.bid_ask_imbalance({"bid_size": 0, "ask_size": 0}) is None


class TestCorrelationBreak:
    def test_outperformance_is_positive(self, bars):
        symbol = bars(count=15, closes=[100 + i for i in range(15)])
        bench = bars(count=15, price=100.0)
        assert signals.correlation_break_pct(symbol, bench, lookback=10) > 0

    def test_underperformance_is_negative(self, bars):
        symbol = bars(count=15, closes=[100 - i for i in range(15)])
        bench = bars(count=15, price=100.0)
        assert signals.correlation_break_pct(symbol, bench, lookback=10) < 0

    def test_none_when_short(self, bars):
        assert signals.correlation_break_pct(bars(count=3), bars(count=3), lookback=10) is None


class TestHourlyTrend:
    def test_bullish_when_fast_leads(self, bars):
        assert signals.hourly_trend(bars(count=20, closes=[100 + i * 2 for i in range(20)])) \
            == "bullish"

    def test_bearish_when_fast_lags(self, bars):
        assert signals.hourly_trend(bars(count=20, closes=[100 - i * 2 for i in range(20)])) \
            == "bearish"

    def test_none_without_history(self, bars):
        assert signals.hourly_trend(bars(count=3)) is None

    def test_none_when_absent(self):
        assert signals.hourly_trend(None) is None


class TestEvaluate:
    def test_no_signal_without_history(self, bars):
        assert signals.evaluate("AAPL", bars(count=3), holding=False) is None

    def test_volume_spike_fires_a_buy(self, bars):
        closes = [100.0] * 24 + [103.0]
        b = bars(count=25, closes=closes, volume=100.0)
        b[-1]["v"] = 100.0 * config.VOLUME_SPIKE_THRESHOLD + 50
        sig = signals.evaluate("AAPL", b, holding=False)
        assert sig.side == "buy"
        assert "volume spike" in sig.reason

    def test_reactive_outranks_trend(self, bars):
        """Tier order matters: a gap must win over a slower SMA cross."""
        b = bars(count=40, price=100.0)
        b[-1]["o"] = 100.0 * (1 + config.GAP_UP_THRESHOLD / 100) + 1
        sig = signals.evaluate("AAPL", b, holding=False)
        assert "gap up" in sig.reason

    def test_holding_flips_the_direction(self, bars):
        b = bars(count=40, price=100.0)
        b[-1]["o"] = 100.0 * (1 - config.GAP_UP_THRESHOLD / 100) - 1
        sig = signals.evaluate("AAPL", b, holding=True)
        assert sig.side == "sell"

    def test_indicators_are_attached(self, bars):
        b = bars(count=40, price=100.0)
        b[-1]["o"] = 110.0
        sig = signals.evaluate("AAPL", b, holding=False)
        assert sig.indicators["price"] == 100.0
        assert "gap_pct" in sig.indicators

    def test_quote_adds_spread_context(self, bars):
        b = bars(count=40, price=100.0)
        b[-1]["o"] = 110.0
        quote = {"bid_size": 50, "ask_size": 50, "spread_pct": 0.05}
        sig = signals.evaluate("AAPL", b, holding=False, quote=quote)
        assert sig.indicators["spread_pct"] == 0.05
        assert "bid_ask_imbalance" in sig.indicators

    def test_flat_market_produces_nothing(self, bars):
        assert signals.evaluate("AAPL", bars(count=40, price=100.0), holding=False) is None
