"""Dashboard API — read routes and the one mutating route.

Nothing here touches Alpaca; broker calls are mocked throughout.
"""

import json
from unittest import mock

import pytest

from trader import broker, dashboard


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "TRADES_FILE", str(tmp_path / "trades.jsonl"))
    monkeypatch.setattr(dashboard, "STATUS_FILE", str(tmp_path / "status.json"))
    monkeypatch.setattr(dashboard, "SCANNER_FILE", str(tmp_path / "scanner.json"))
    dashboard.app.config["TESTING"] = True
    with dashboard.app.test_client() as c:
        yield c


@pytest.fixture
def closed_ok():
    """close_all_positions reporting every symbol closed cleanly."""
    def build(*symbols):
        return [{"symbol": s, "status": 200, "ok": True} for s in symbols]
    return build


def liquidate(client, confirm="LIQUIDATE"):
    body = {} if confirm is None else {"confirm": confirm}
    return client.post("/api/liquidate", json=body)


class TestLiquidateGuard:
    """The endpoint flattens a real account — it must not fire by accident."""

    def test_requires_a_confirmation_token(self, client, position):
        with mock.patch.object(broker, "get_positions", return_value={"AAPL": position()}), \
             mock.patch.object(broker, "close_all_positions") as close:
            r = liquidate(client, confirm=None)
        assert r.status_code == 400
        close.assert_not_called()

    def test_rejects_a_wrong_token(self, client, position):
        with mock.patch.object(broker, "get_positions", return_value={"AAPL": position()}), \
             mock.patch.object(broker, "close_all_positions") as close:
            r = liquidate(client, confirm="yes")
        assert r.status_code == 400
        close.assert_not_called()

    def test_rejects_an_empty_body(self, client):
        with mock.patch.object(broker, "close_all_positions") as close:
            r = client.post("/api/liquidate", data="", content_type="application/json")
        assert r.status_code == 400
        close.assert_not_called()

    def test_get_is_not_allowed(self, client):
        """A stray link or prefetch must not be able to trigger it."""
        with mock.patch.object(broker, "close_all_positions") as close:
            assert client.get("/api/liquidate").status_code == 405
        close.assert_not_called()


class TestLiquidate:
    def test_closes_and_cancels_resting_orders(self, client, position, closed_ok):
        positions = {"AAPL": position(), "BTCUSD": position()}
        with mock.patch.object(broker, "get_positions", return_value=positions), \
             mock.patch.object(broker, "close_all_positions",
                               return_value=closed_ok("AAPL", "BTCUSD")) as close:
            r = liquidate(client)

        assert r.status_code == 200
        assert r.get_json()["count"] == 2
        close.assert_called_once_with(cancel_orders=True)

    def test_no_positions_is_a_no_op(self, client):
        with mock.patch.object(broker, "get_positions", return_value={}), \
             mock.patch.object(broker, "close_all_positions") as close:
            r = liquidate(client)

        assert r.status_code == 200
        assert r.get_json()["count"] == 0
        close.assert_not_called()

    def test_partial_failure_is_reported(self, client, position, closed_ok):
        results = closed_ok("AAPL") + [{"symbol": "ILLIQ", "status": 422, "ok": False}]
        with mock.patch.object(broker, "get_positions", return_value={"AAPL": position()}), \
             mock.patch.object(broker, "close_all_positions", return_value=results):
            r = liquidate(client)

        body = r.get_json()
        assert body["count"] == 1
        assert body["failed"] == ["ILLIQ"]

    def test_broker_failure_returns_502(self, client, position):
        with mock.patch.object(broker, "get_positions", return_value={"AAPL": position()}), \
             mock.patch.object(broker, "close_all_positions",
                               side_effect=RuntimeError("alpaca down")):
            r = liquidate(client)
        assert r.status_code == 502
        assert "alpaca down" in r.get_json()["error"]

    def test_unreadable_positions_returns_502(self, client):
        with mock.patch.object(broker, "get_positions", side_effect=RuntimeError("no auth")), \
             mock.patch.object(broker, "close_all_positions") as close:
            r = liquidate(client)
        assert r.status_code == 502
        close.assert_not_called()

    def test_writes_an_audit_event(self, client, position, closed_ok):
        positions = {"AAPL": position(qty=10, plpc=-0.02)}
        with mock.patch.object(broker, "get_positions", return_value=positions), \
             mock.patch.object(broker, "close_all_positions", return_value=closed_ok("AAPL")):
            liquidate(client)

        events = [json.loads(x) for x in
                  open(dashboard.TRADES_FILE).read().splitlines() if x.strip()]
        event = events[-1]
        assert event["event"] == "liquidate"
        assert event["source"] == "dashboard"
        assert event["positions"]["AAPL"]["qty"] == 10
        assert event["ts"]

    def test_no_audit_event_when_nothing_to_close(self, client):
        import os
        with mock.patch.object(broker, "get_positions", return_value={}):
            liquidate(client)
        assert not os.path.exists(dashboard.TRADES_FILE)


class TestCloseAllPositions:
    @pytest.fixture
    def trading_client(self):
        fake = mock.Mock()
        with mock.patch.object(broker, "get_client", return_value=fake):
            yield fake

    def test_normalises_alpaca_responses(self, trading_client):
        trading_client.close_all_positions.return_value = [
            mock.Mock(symbol="AAPL", status=200),
            mock.Mock(symbol="ILLIQ", status=422),
        ]
        results = broker.close_all_positions()
        assert results[0] == {"symbol": "AAPL", "status": 200, "ok": True}
        assert results[1]["ok"] is False

    def test_empty_book_returns_empty_list(self, trading_client):
        trading_client.close_all_positions.return_value = []
        assert broker.close_all_positions() == []

    def test_none_response_is_safe(self, trading_client):
        trading_client.close_all_positions.return_value = None
        assert broker.close_all_positions() == []

    def test_cancel_orders_is_forwarded(self, trading_client):
        trading_client.close_all_positions.return_value = []
        broker.close_all_positions(cancel_orders=False)
        trading_client.close_all_positions.assert_called_once_with(cancel_orders=False)


class TestReadRoutes:
    def test_index_serves(self, client):
        assert client.get("/").status_code == 200

    def test_summary_survives_alpaca_being_down(self, client):
        with mock.patch.object(broker, "get_account", side_effect=RuntimeError("no keys")):
            r = client.get("/api/summary")
        assert r.status_code == 200
        assert r.get_json()["error"]

    def test_events_route(self, client):
        r = client.get("/api/events")
        assert r.status_code == 200
        assert r.get_json()["events"] == []

    def test_scanner_route_without_state_file(self, client):
        assert client.get("/api/scanner").get_json()["watchlist"] == []
