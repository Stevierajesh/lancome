"""Tick loops: symbol screening, per-symbol isolation, judge wiring.

The batched bars/quotes fetches happen once per tick, so a single bad symbol
used to 400 the request and kill the whole cycle. Everything downstream of that
is isolated per symbol.
"""

from unittest import mock

import pytest

from trader import broker, data, main, signals
from trader.enrichment import CaseFile
from trader.signals import Signal

VETO = {"decision": "veto", "confidence": 0.9, "reason": "no"}


@pytest.fixture
def judge():
    j = mock.Mock()
    j.evaluate.return_value = VETO
    return j


@pytest.fixture
def stock_tick(bars, account, monkeypatch):
    """Run tick_stocks with market data stubbed out. Returns evaluated symbols."""
    def run(watchlist, judge, evaluate=None, positions=None, account_factory=None):
        seen = []

        def default_evaluate(symbol, *a, **k):
            seen.append(symbol)
            return Signal(symbol, "buy", "test rule", {"price": 10.0})

        bar_data = {s: bars() for s in watchlist}
        get_account = account_factory or (lambda: dict(account))

        with mock.patch.object(data, "get_bars", return_value=bar_data), \
             mock.patch.object(data, "get_latest_quotes", return_value={}), \
             mock.patch.object(data, "get_hourly_bars", return_value={}), \
             mock.patch.object(signals, "evaluate", side_effect=evaluate or default_evaluate), \
             mock.patch.object(broker, "get_account", side_effect=get_account), \
             mock.patch.object(broker, "get_positions", return_value={}), \
             mock.patch.object(main, "enrich",
                               side_effect=lambda s, sc, nc, a, p: CaseFile(
                                   symbol=s, portfolio={"account": a, "positions": p})), \
             mock.patch.object(broker, "submit_market_order"):
            main.tick_stocks(watchlist, judge, mock.Mock(), mock.Mock(),
                             dict(account), positions if positions is not None else {})
        return seen
    return run


class TestSymbolScreening:
    def test_bad_symbols_never_reach_the_data_call(self, bars, account, judge):
        """One TSX ticker in a batched request 400s the entire response."""
        watchlist = ["AAPL", "TSX:ERD", "BTCUSD", "NVDA"]
        with mock.patch.object(data, "get_bars", return_value={}) as get_bars, \
             mock.patch.object(data, "get_latest_quotes", return_value={}), \
             mock.patch.object(data, "get_hourly_bars", return_value={}):
            main.tick_stocks(watchlist, judge, mock.Mock(), mock.Mock(), account, {})

        requested = get_bars.call_args[0][0]
        assert requested == ["AAPL", "NVDA"]

    def test_blocklisted_symbols_are_dropped(self, bars, account, judge):
        main._untradable.add("AIXC")
        with mock.patch.object(data, "get_bars", return_value={}) as get_bars, \
             mock.patch.object(data, "get_latest_quotes", return_value={}), \
             mock.patch.object(data, "get_hourly_bars", return_value={}):
            main.tick_stocks(["AAPL", "AIXC"], judge, mock.Mock(), mock.Mock(), account, {})
        assert get_bars.call_args[0][0] == ["AAPL"]

    def test_empty_watchlist_makes_no_calls(self, account, judge):
        with mock.patch.object(data, "get_bars") as get_bars:
            main.tick_stocks([], judge, mock.Mock(), mock.Mock(), account, {})
        get_bars.assert_not_called()

    def test_all_bad_watchlist_makes_no_calls(self, account, judge):
        with mock.patch.object(data, "get_bars") as get_bars:
            main.tick_stocks(["TSX:A", "BTCUSD"], judge, mock.Mock(), mock.Mock(), account, {})
        get_bars.assert_not_called()


class TestPerSymbolIsolation:
    def test_one_exploding_symbol_does_not_stop_the_loop(self, stock_tick, judge):
        seen = []

        def evaluate(symbol, *a, **k):
            seen.append(symbol)
            if symbol == "BOOM":
                raise ValueError("malformed bar data")
            return Signal(symbol, "buy", "r", {"price": 10.0})

        stock_tick(["AAPL", "BOOM", "NVDA"], judge, evaluate=evaluate)
        assert seen == ["AAPL", "BOOM", "NVDA"]
        assert judge.evaluate.call_count == 2

    def test_a_failing_judge_does_not_stop_the_loop(self, stock_tick):
        judge = mock.Mock()
        judge.evaluate.side_effect = [RuntimeError("judge exploded"), VETO]
        seen = stock_tick(["AAPL", "NVDA"], judge)
        assert seen == ["AAPL", "NVDA"]


class TestJudgeWiring:
    def test_judge_sees_current_cash_per_signal(self, stock_tick):
        """A tick used to fetch the book once, so later signals in the same
        cycle were judged against pre-trade cash."""
        cash = iter([90_000.0, 80_000.0, 70_000.0])
        seen = []

        judge = mock.Mock()
        judge.evaluate.side_effect = lambda case: (
            seen.append(case.portfolio["account"]["cash"]), VETO)[1]

        stock_tick(["AAPL", "NVDA", "MSFT"], judge,
                   account_factory=lambda: {"equity": 100_000.0, "cash": next(cash),
                                            "last_equity": 100_000.0})
        assert seen == [90_000.0, 80_000.0, 70_000.0]

    def test_no_signal_means_no_judge_call(self, stock_tick, judge):
        stock_tick(["AAPL"], judge, evaluate=lambda *a, **k: None)
        judge.evaluate.assert_not_called()

    def test_veto_sets_a_cooldown(self, stock_tick, judge):
        stock_tick(["AAPL"], judge)
        assert main._on_cooldown("AAPL", "test rule") is True

    def test_cooldown_suppresses_the_repeat(self, stock_tick, judge):
        stock_tick(["AAPL"], judge)
        judge.evaluate.reset_mock()
        stock_tick(["AAPL"], judge)
        judge.evaluate.assert_not_called()

    def test_approval_does_not_set_a_cooldown(self, stock_tick):
        judge = mock.Mock()
        judge.evaluate.return_value = {"decision": "approve", "confidence": 0.9,
                                       "reason": "ok"}
        stock_tick(["AAPL"], judge)
        assert main._on_cooldown("AAPL", "test rule") is False


class TestCooldown:
    def test_expires(self, monkeypatch):
        main._set_cooldown("AAPL", "rule")
        assert main._on_cooldown("AAPL", "rule") is True

        real_time = main.time.time
        monkeypatch.setattr(main.time, "time",
                            lambda: real_time() + main.SIGNAL_COOLDOWN_SECONDS + 1)
        assert main._on_cooldown("AAPL", "rule") is False

    def test_is_scoped_to_symbol_and_rule(self):
        main._set_cooldown("AAPL", "rule A")
        assert main._on_cooldown("AAPL", "rule B") is False
        assert main._on_cooldown("NVDA", "rule A") is False
