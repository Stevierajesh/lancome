"""Order execution, portfolio bookkeeping and hard exits.

Regression cover for the 2026-08-18 session:
  - AIXC 422s aborted the remaining watchlist for the cycle
  - SNDQ 403 double-close took down a stock tick
  - the ledger recorded signal prices, not fills
  - the judge was shown pre-trade cash and `{"qty": 0}` positions
"""

from unittest import mock

import pytest

from trader import broker, config, main, risk
from trader.enrichment import CaseFile
from trader.signals import Signal

APPROVE = {"decision": "approve", "confidence": 0.8, "reason": "looks fine"}


def buy_case(symbol="AAPL", price=100.0):
    return CaseFile(symbol=symbol, signal=Signal(symbol, "buy", "test rule", {"price": price}))


def sell_case(symbol="AAPL", price=100.0):
    return CaseFile(symbol=symbol, signal=Signal(symbol, "sell", "test rule", {"price": price}))


@pytest.fixture
def filled(order):
    """Broker patched so orders submit and fill at `price`."""
    def apply(price=100.0):
        return mock.patch.multiple(
            broker,
            submit_market_order=mock.DEFAULT,
            submit_crypto_order=mock.DEFAULT,
            close_position=mock.DEFAULT,
            wait_for_fill=mock.Mock(return_value=price),
        )
    return apply


class TestSubmitGuard:
    def test_rejection_returns_false_and_blocklists(self, errors, ledger):
        with mock.patch.object(broker, "submit_market_order", side_effect=errors.api(422)):
            result = main._submit(broker.submit_market_order, "AIXC", "buy", 10)
        assert result is False
        assert "AIXC" in main._untradable
        assert ledger.of_type("rejected")

    def test_non_422_failures_do_not_blocklist(self, errors, ledger):
        with mock.patch.object(broker, "submit_market_order", side_effect=errors.http(500)):
            assert main._submit(broker.submit_market_order, "AAPL", "buy", 1) is False
        assert "AAPL" not in main._untradable

    def test_slashed_symbol_blocklists_both_forms(self, errors):
        with mock.patch.object(broker, "submit_crypto_order", side_effect=errors.api(422)):
            main._submit(broker.submit_crypto_order, "BTC/USD", "buy", 100)
        assert {"BTC/USD", "BTCUSD"} <= main._untradable

    def test_success_passes_the_order_through(self, order):
        with mock.patch.object(broker, "submit_market_order", return_value=order()) as sub:
            assert main._submit(broker.submit_market_order, "AAPL", "buy", 5) is not None
        sub.assert_called_once_with("AAPL", "buy", 5)


class TestExecuteEntry:
    def test_records_the_fill_not_the_signal_price(self, account, ledger, order):
        """WETO logged a 53.17 entry against a 47.55 fill."""
        with mock.patch.object(broker, "submit_market_order", return_value=order()), \
             mock.patch.object(broker, "wait_for_fill", return_value=47.55):
            main.execute_if_approved(buy_case("WETO", 53.17), APPROVE, {}, account)

        entry = ledger.of_type("entry")[0]
        assert entry["price"] == 53.17        # signal price preserved
        assert entry["fill_price"] == 47.55   # actual fill recorded
        assert ledger.rows()[0]["price"] == "47.55"

    def test_falls_back_to_signal_price_when_unfilled(self, account, ledger, order):
        with mock.patch.object(broker, "submit_market_order", return_value=order()), \
             mock.patch.object(broker, "wait_for_fill", return_value=None):
            main.execute_if_approved(buy_case("AAPL", 100.0), APPROVE, {}, account)
        assert ledger.rows()[0]["price"] == "100.0"

    def test_rejected_order_records_nothing(self, account, ledger, errors):
        positions = {}
        with mock.patch.object(broker, "submit_market_order", side_effect=errors.api(422)):
            main.execute_if_approved(buy_case("AIXC", 3.14), APPROVE, positions, account)

        assert ledger.of_type("entry") == []
        assert ledger.rows() == []
        assert "AIXC" not in positions, "phantom position after a rejected order"

    def test_low_confidence_is_not_executed(self, account, ledger):
        timid = {**APPROVE, "confidence": config.JUDGE_MIN_CONFIDENCE - 0.01}
        with mock.patch.object(broker, "submit_market_order") as sub:
            main.execute_if_approved(buy_case(), timid, {}, account)
        sub.assert_not_called()
        assert ledger.events() == []

    def test_veto_is_not_executed(self, account):
        with mock.patch.object(broker, "submit_market_order") as sub:
            main.execute_if_approved(buy_case(), {"decision": "veto", "confidence": 1.0,
                                                  "reason": "no"}, {}, account)
        sub.assert_not_called()

    def test_daily_loss_limit_blocks_entries(self, account):
        account["equity"] = account["last_equity"] * (1 - config.DAILY_LOSS_LIMIT_PCT)
        with mock.patch.object(broker, "submit_market_order") as sub:
            main.execute_if_approved(buy_case(), APPROVE, {}, account)
        sub.assert_not_called()

    def test_position_cap_blocks_entries(self, account):
        full = {f"S{i}": {} for i in range(config.MAX_OPEN_POSITIONS)}
        with mock.patch.object(broker, "submit_market_order") as sub:
            main.execute_if_approved(buy_case(), APPROVE, full, account)
        sub.assert_not_called()

    def test_missing_signal_is_a_no_op(self, account):
        with mock.patch.object(broker, "submit_market_order") as sub:
            main.execute_if_approved(CaseFile(symbol="AAPL"), APPROVE, {}, account)
        sub.assert_not_called()


class TestExecuteExit:
    def test_already_closed_still_records_the_exit(self, account, ledger, position):
        """close_position returns None on a lost double-close race."""
        positions = {"SNDQ": position()}
        with mock.patch.object(broker, "close_position", return_value=None), \
             mock.patch.object(broker, "wait_for_fill", return_value=None):
            main.execute_if_approved(sell_case("SNDQ"), APPROVE, positions, account)

        assert ledger.of_type("exit")
        assert "SNDQ" not in positions

    def test_rejected_close_keeps_the_position(self, account, ledger, position, errors):
        positions = {"AAPL": position()}
        with mock.patch.object(broker, "close_position", side_effect=errors.http(500)):
            main.execute_if_approved(sell_case("AAPL"), APPROVE, positions, account)

        assert ledger.of_type("exit") == []
        assert "AAPL" in positions, "position dropped despite a failed close"


class TestMarkOpened:
    """Replaces the old `{"qty": 0}` placeholder, which told the judge a
    30%-deployed book was untouched."""

    def test_stock_position_is_fully_described(self, account):
        positions = {}
        main._mark_opened(account, positions, "OSRH", qty=14390, notional=None, price=0.6949)
        entry = positions["OSRH"]
        assert entry["qty"] == 14390
        assert entry["avg_entry_price"] == 0.6949
        assert entry["market_value"] == pytest.approx(14390 * 0.6949)
        assert entry["unrealized_plpc"] == 0.0

    def test_crypto_qty_is_derived_from_notional(self, account):
        positions = {}
        main._mark_opened(account, positions, "BTCUSD", qty=None,
                          notional=9961.83, price=64695.32)
        assert positions["BTCUSD"]["qty"] == pytest.approx(9961.83 / 64695.32)
        assert positions["BTCUSD"]["market_value"] == pytest.approx(9961.83)

    def test_cash_is_drawn_down(self, account):
        start = account["cash"]
        main._mark_opened(account, {}, "AAPL", qty=10, notional=None, price=100.0)
        assert account["cash"] == pytest.approx(start - 1000.0)

    def test_cash_never_goes_negative(self, account):
        account["cash"] = 50.0
        main._mark_opened(account, {}, "AAPL", qty=10, notional=None, price=100.0)
        assert account["cash"] == 0.0

    def test_still_counts_toward_the_cap_without_a_price(self, account):
        positions = {}
        main._mark_opened(account, positions, "X", qty=None, notional=None, price=None)
        assert "X" in positions


class TestPortfolioFreshness:
    def test_refresh_returns_live_data(self):
        fresh_account = {"equity": 9.0, "cash": 8.0, "last_equity": 7.0}
        fresh_positions = {"Z": {"qty": 1}}
        with mock.patch.object(broker, "get_account", return_value=fresh_account), \
             mock.patch.object(broker, "get_positions", return_value=fresh_positions):
            assert main._refresh_portfolio({}, {}) == (fresh_account, fresh_positions)

    def test_refresh_falls_back_to_the_snapshot(self):
        snapshot = ({"cash": 1.0}, {"A": {}})
        with mock.patch.object(broker, "get_account", side_effect=RuntimeError("api down")):
            assert main._refresh_portfolio(*snapshot) == snapshot

    def test_book_stays_truthful_across_a_multi_entry_tick(self, account, ledger, order):
        """Replays the 14:25 tick: by the 4th entry the judge was being shown
        pre-trade cash and three positions holding nothing."""
        positions = {}
        for symbol, price, qty in [("OSRH", 0.6949, 14390), ("NOK", 10.44, 957),
                                   ("QQQ", 718.88, 13)]:
            with mock.patch.object(broker, "submit_market_order", return_value=order()), \
                 mock.patch.object(broker, "wait_for_fill", return_value=price), \
                 mock.patch.object(risk, "size_entry", return_value=qty):
                main.execute_if_approved(buy_case(symbol, price), APPROVE, positions, account)

        assert account["cash"] < 100_000.0, "cash never drawn down"
        assert all(p["market_value"] > 0 for p in positions.values())
        assert all(p["qty"] > 0 for p in positions.values())


class TestHardExits:
    def test_stop_loss_closes(self, account, ledger, position, order):
        positions = {"OSRH": position(plpc=-0.043)}
        with mock.patch.object(broker, "close_position", return_value=order()) as close, \
             mock.patch.object(broker, "wait_for_fill", return_value=0.6593):
            main.check_hard_exits(positions, True, account)

        close.assert_called_once_with("OSRH")
        assert "stop-loss" in ledger.of_type("exit")[0]["reason"]
        assert positions == {}

    def test_take_profit_closes(self, account, ledger, position, order):
        positions = {"SNDQ": position(plpc=0.031)}
        with mock.patch.object(broker, "close_position", return_value=order()), \
             mock.patch.object(broker, "wait_for_fill", return_value=14.26):
            main.check_hard_exits(positions, True, account)
        assert "take-profit" in ledger.of_type("exit")[0]["reason"]

    def test_records_the_exit_fill(self, account, ledger, position, order):
        positions = {"SNDQ": position(plpc=0.031)}
        with mock.patch.object(broker, "close_position", return_value=order()), \
             mock.patch.object(broker, "wait_for_fill", return_value=14.26):
            main.check_hard_exits(positions, True, account)
        assert ledger.of_type("exit")[0]["fill_price"] == 14.26

    def test_untouched_inside_the_band(self, account, position):
        with mock.patch.object(broker, "close_position") as close:
            main.check_hard_exits({"AAPL": position(plpc=0.01)}, True, account)
        close.assert_not_called()

    def test_unpriced_placeholder_is_skipped(self, account):
        """A same-tick entry has no unrealized_plpc yet; reading it would raise."""
        with mock.patch.object(broker, "close_position") as close:
            main.check_hard_exits({"NEW": {"qty": 0}}, True, account)
        close.assert_not_called()

    def test_stocks_are_left_alone_when_the_market_is_shut(self, account, position):
        with mock.patch.object(broker, "close_position") as close:
            main.check_hard_exits({"AAPL": position(plpc=-0.05)}, False, account)
        close.assert_not_called()

    def test_crypto_exits_around_the_clock(self, account, position, order):
        with mock.patch.object(broker, "close_position", return_value=order()) as close, \
             mock.patch.object(broker, "wait_for_fill", return_value=1.0):
            main.check_hard_exits({"BTCUSD": position(plpc=-0.05)}, False, account)
        close.assert_called_once_with("BTCUSD")

    def test_lost_race_still_clears_the_position(self, account, ledger, position):
        with mock.patch.object(broker, "close_position", return_value=None), \
             mock.patch.object(broker, "wait_for_fill", return_value=None):
            positions = {"SNDQ": position(plpc=0.031)}
            main.check_hard_exits(positions, True, account)
        assert positions == {}
        assert ledger.of_type("exit")

    def test_failed_close_leaves_the_position_open(self, account, position, errors):
        positions = {"AAPL": position(plpc=-0.05)}
        with mock.patch.object(broker, "close_position", side_effect=errors.http(500)):
            main.check_hard_exits(positions, True, account)
        assert "AAPL" in positions
