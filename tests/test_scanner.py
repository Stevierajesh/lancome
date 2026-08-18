"""Scanner watchlist and symbol screening."""

import json
import time
from unittest import mock

import pytest

from trader import config
from trader.scanner import Scanner, is_us_equity


class TestIsUsEquity:
    """Alpaca 400s an entire multi-symbol bars request over one bad ticker,
    so news-sourced symbols get screened before they reach the watchlist."""

    @pytest.mark.parametrize("symbol", ["AAPL", "SPY", "F", "GOOGL", "BRK.B", "TSLL"])
    def test_accepts_us_equities(self, symbol):
        assert is_us_equity(symbol) is True

    @pytest.mark.parametrize("symbol", [
        "TSX:ERD", "TSX:PLZ/UN", "TSX:FORA",   # Toronto listings from Benzinga
        "BTCUSD", "ETHUSD", "HYPEUSD", "ZECUSD",  # crypto in position form
        "BTC/USD",                              # crypto in data form
        "", "toolongsymbol", "lower",
    ])
    def test_rejects_everything_else(self, symbol):
        assert is_us_equity(symbol) is False

    def test_rejects_none_safely(self):
        assert is_us_equity(None) is False


@pytest.fixture
def scanner():
    with mock.patch("trader.scanner.ScreenerClient"):
        yield Scanner("key", "secret")


class TestWatchlist:
    def test_news_symbol_is_added_and_scored(self, scanner):
        scanner.add_from_news("AAPL", "Apple beats earnings")
        entry = scanner.get_entry("AAPL")
        assert entry["source"] == "news"
        assert entry["score"] > 1.0
        assert "Apple beats earnings" in entry["headlines"]

    def test_bad_news_symbol_never_enters(self, scanner):
        scanner.add_from_news("TSX:ERD", "Toronto listing")
        assert scanner.get_entry("TSX:ERD") is None
        assert "TSX:ERD" not in scanner.get_watchlist()

    def test_crypto_position_symbol_never_enters(self, scanner):
        scanner.add_from_news("BTCUSD", "Bitcoin news")
        assert scanner.get_watchlist() == []

    def test_repeated_mentions_compound_score(self, scanner):
        for i in range(3):
            scanner.add_from_news("NVDA", f"headline {i}")
        assert scanner.get_entry("NVDA")["score"] == pytest.approx(1.0 + 3 * 0.5)

    def test_headlines_are_capped(self, scanner):
        for i in range(10):
            scanner.add_from_news("NVDA", f"headline {i}")
        assert len(scanner.get_entry("NVDA")["headlines"]) == 5

    def test_source_becomes_both(self, scanner):
        scanner._add_or_refresh("AAPL", "scanner")
        scanner.add_from_news("AAPL", "news too")
        assert scanner.get_entry("AAPL")["source"] == "both"

    def test_watchlist_is_capped_and_ranked(self, scanner):
        for i in range(config.SCANNER_WATCHLIST_MAX + 10):
            scanner._add_or_refresh(f"SYM{i}", "scanner")
        scanner._watchlist["SYM5"].score = 99.0
        watchlist = scanner.get_watchlist()
        assert len(watchlist) == config.SCANNER_WATCHLIST_MAX
        assert watchlist[0] == "SYM5"

    def test_missing_entry_is_none(self, scanner):
        assert scanner.get_entry("NOPE") is None


class TestPrune:
    def test_stale_entries_are_dropped(self, scanner):
        scanner._add_or_refresh("OLD", "scanner")
        scanner._watchlist["OLD"].last_seen = time.time() - config.SCANNER_ENTRY_TTL_SECONDS - 1
        scanner._add_or_refresh("NEW", "scanner")
        scanner.prune()
        assert "OLD" not in scanner._watchlist
        assert "NEW" in scanner._watchlist


class TestStatePersistence:
    def test_round_trip(self, scanner, tmp_path):
        scanner.add_from_news("AAPL", "headline")
        path = tmp_path / "scanner.json"
        path.write_text(json.dumps({"watchlist": scanner.get_all_entries()}))

        with mock.patch("trader.scanner.ScreenerClient"):
            restored = Scanner("k", "s")
        restored.load_state(str(path))
        assert restored.get_entry("AAPL")["score"] == scanner.get_entry("AAPL")["score"]

    def test_stale_entries_are_not_restored(self, scanner, tmp_path):
        scanner._add_or_refresh("OLD", "scanner")
        entries = scanner.get_all_entries()
        entries[0]["last_seen"] = time.time() - config.SCANNER_ENTRY_TTL_SECONDS - 1
        path = tmp_path / "scanner.json"
        path.write_text(json.dumps({"watchlist": entries}))

        with mock.patch("trader.scanner.ScreenerClient"):
            restored = Scanner("k", "s")
        restored.load_state(str(path))
        assert restored.get_watchlist() == []

    def test_missing_file_is_not_fatal(self, scanner, tmp_path):
        scanner.load_state(str(tmp_path / "nope.json"))
        assert scanner.get_watchlist() == []

    def test_corrupt_file_is_not_fatal(self, scanner, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        scanner.load_state(str(path))
        assert scanner.get_watchlist() == []
