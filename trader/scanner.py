"""Market scanner: polls Alpaca screener endpoints and maintains a dynamic watchlist."""

import json
import logging
import os
import time
from dataclasses import dataclass, field

from alpaca.data.historical import ScreenerClient
from alpaca.data.enums import MostActivesBy, MarketType
from alpaca.data.requests import MostActivesRequest, MarketMoversRequest

from . import config

log = logging.getLogger("scanner")


@dataclass
class WatchlistEntry:
    symbol: str
    source: str                          # "scanner", "news", or "both"
    added_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    score: float = 1.0
    headlines: list[str] = field(default_factory=list)
    scanner_meta: dict = field(default_factory=dict)  # volume, pct_change, etc.


class Scanner:
    def __init__(self, api_key: str, secret_key: str):
        self._screener = ScreenerClient(api_key, secret_key)
        self._watchlist: dict[str, WatchlistEntry] = {}

    def poll_screener(self) -> list[str]:
        """Poll most-actives and top movers. Returns list of newly added symbols."""
        new_symbols: list[str] = []
        try:
            actives = self._screener.get_most_actives(
                MostActivesRequest(by=MostActivesBy.VOLUME, top=config.SCANNER_TOP_ACTIVES)
            )
            for item in actives.most_actives:
                if self._add_or_refresh(item.symbol, "scanner",
                                        meta={"volume": item.volume, "trade_count": item.trade_count}):
                    new_symbols.append(item.symbol)
        except Exception as e:
            log.warning("most-actives call failed: %s", e)

        try:
            movers = self._screener.get_market_movers(
                MarketMoversRequest(market_type=MarketType.STOCKS, top=config.SCANNER_TOP_MOVERS)
            )
            for mover in list(movers.gainers or []) + list(movers.losers or []):
                if self._add_or_refresh(mover.symbol, "scanner",
                                        meta={"price": float(mover.price),
                                              "change": float(mover.change),
                                              "pct_change": float(mover.percent_change)}):
                    new_symbols.append(mover.symbol)
        except Exception as e:
            log.warning("market-movers call failed: %s", e)

        if new_symbols:
            log.info("scanner discovered %d new symbols: %s", len(new_symbols), new_symbols)
        return new_symbols

    def add_from_news(self, symbol: str, headline: str):
        """Register a symbol surfaced by the news stream."""
        is_new = self._add_or_refresh(symbol, "news")
        entry = self._watchlist.get(symbol)
        if entry:
            entry.score += 0.5  # news mentions boost score
            entry.headlines.append(headline)
            entry.headlines = entry.headlines[-5:]  # keep last 5
        if is_new:
            log.info("news added %s to watchlist: %s", symbol, headline[:80])

    def get_watchlist(self) -> list[str]:
        """Current dynamic watchlist, sorted by score descending, capped."""
        entries = sorted(self._watchlist.values(), key=lambda e: e.score, reverse=True)
        return [e.symbol for e in entries[:config.SCANNER_WATCHLIST_MAX]]

    def get_entry(self, symbol: str) -> dict | None:
        """Return metadata for a watchlist symbol, or None."""
        entry = self._watchlist.get(symbol)
        if entry is None:
            return None
        return {
            "source": entry.source,
            "score": entry.score,
            "headlines": entry.headlines,
            "scanner_meta": entry.scanner_meta,
            "age_seconds": time.time() - entry.added_at,
        }

    def get_all_entries(self) -> list[dict]:
        """All watchlist entries as dicts, for the dashboard API."""
        entries = sorted(self._watchlist.values(), key=lambda e: e.score, reverse=True)
        return [
            {
                "symbol": e.symbol,
                "source": e.source,
                "score": round(e.score, 2),
                "added_at": e.added_at,
                "last_seen": e.last_seen,
                "headlines": e.headlines,
                "scanner_meta": e.scanner_meta,
            }
            for e in entries[:config.SCANNER_WATCHLIST_MAX]
        ]

    def prune(self):
        """Remove stale entries that haven't been refreshed within TTL."""
        now = time.time()
        stale = [sym for sym, e in self._watchlist.items()
                 if now - e.last_seen > config.SCANNER_ENTRY_TTL_SECONDS]
        for sym in stale:
            del self._watchlist[sym]
        if stale:
            log.info("pruned %d stale symbols: %s", len(stale), stale)

    def load_state(self, path: str):
        """Restore watchlist from a previous scanner.json, pruning stale entries."""
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        now = time.time()
        loaded = 0
        for entry in data.get("watchlist", []):
            last_seen = entry.get("last_seen", 0)
            if now - last_seen > config.SCANNER_ENTRY_TTL_SECONDS:
                continue
            symbol = entry["symbol"]
            self._watchlist[symbol] = WatchlistEntry(
                symbol=symbol,
                source=entry.get("source", "scanner"),
                added_at=entry.get("added_at", now),
                last_seen=last_seen,
                score=entry.get("score", 1.0),
                headlines=entry.get("headlines", []),
                scanner_meta=entry.get("scanner_meta", {}),
            )
            loaded += 1
        if loaded:
            log.info("restored %d watchlist entries from %s", loaded, path)

    def _add_or_refresh(self, symbol: str, source: str, meta: dict | None = None) -> bool:
        """Add symbol or refresh its timestamp. Returns True if newly added."""
        now = time.time()
        if symbol in self._watchlist:
            entry = self._watchlist[symbol]
            entry.last_seen = now
            if source != entry.source:
                entry.source = "both"
            if meta:
                entry.scanner_meta.update(meta)
            return False

        self._watchlist[symbol] = WatchlistEntry(
            symbol=symbol,
            source=source,
            added_at=now,
            last_seen=now,
            scanner_meta=meta or {},
        )
        return True
