"""Fetch recent bars from Alpaca's free IEX data feed."""

from datetime import datetime, timedelta, timezone

from alpaca.data.enums import DataFeed
from alpaca.data.historical import CryptoHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

from . import config

_client = None
_crypto_client = None


def _get_client() -> StockHistoricalDataClient:
    global _client
    if _client is None:
        _client = StockHistoricalDataClient(config.ALPACA_API_KEY, config.ALPACA_SECRET_KEY)
    return _client


def _get_crypto_client() -> CryptoHistoricalDataClient:
    global _crypto_client
    if _crypto_client is None:
        _crypto_client = CryptoHistoricalDataClient()  # crypto data needs no keys
    return _crypto_client


def get_bars(symbols: list[str]) -> dict[str, list[dict]]:
    """Return recent bars per symbol as a list of dicts (oldest first).

    Each bar: {t, o, h, l, c, v}
    """
    # Fetch enough calendar time to cover LOOKBACK_BARS of market-hours bars
    start = datetime.now(timezone.utc) - timedelta(days=5)
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(config.BAR_TIMEFRAME_MINUTES, TimeFrameUnit.Minute),
        start=start,
        feed=DataFeed.IEX,
    )
    response = _get_client().get_stock_bars(request)
    return _to_dicts(response, symbols)


def get_crypto_bars(symbols: list[str]) -> dict[str, list[dict]]:
    """Same shape as get_bars, for crypto pairs like 'BTC/USD'. 24/7 data."""
    start = datetime.now(timezone.utc) - timedelta(
        minutes=config.BAR_TIMEFRAME_MINUTES * (config.LOOKBACK_BARS + 5)
    )
    request = CryptoBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(config.BAR_TIMEFRAME_MINUTES, TimeFrameUnit.Minute),
        start=start,
    )
    response = _get_crypto_client().get_crypto_bars(request)
    return _to_dicts(response, symbols)


def _to_dicts(response, symbols: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for symbol in symbols:
        bars = response.data.get(symbol, [])
        out[symbol] = [
            {
                "t": bar.timestamp.isoformat(),
                "o": float(bar.open),
                "h": float(bar.high),
                "l": float(bar.low),
                "c": float(bar.close),
                "v": float(bar.volume),
            }
            for bar in bars
        ][-config.LOOKBACK_BARS:]
    return out
