"""Broker error handling.

Regression cover for two live incidents:
  - a 403 on an already-closed position took down a whole stock tick
  - the ledger recorded signal prices instead of fills (WETO: 53.17 vs 47.55)
"""

from unittest import mock

import pytest
import requests

from trader import broker


@pytest.fixture
def client():
    """Patch the lazily-built TradingClient and hand back the mock."""
    fake = mock.Mock()
    with mock.patch.object(broker, "get_client", return_value=fake):
        yield fake


class TestErrorStatus:
    """alpaca-py raises two different exception shapes; both carry a status."""

    def test_reads_status_from_http_error(self, errors):
        assert broker.error_status(errors.http(422)) == 422

    def test_reads_status_from_api_error(self, errors):
        assert broker.error_status(errors.api(403)) == 403

    def test_returns_none_for_unrelated_exception(self):
        assert broker.error_status(ValueError("nope")) is None

    def test_api_error_without_http_context(self):
        from alpaca.common.exceptions import APIError
        assert broker.error_status(APIError('{"code":1,"message":"x"}')) is None


class TestClosePosition:
    @pytest.mark.parametrize("status", [403, 404])
    def test_already_closed_is_not_an_error(self, client, errors, status):
        """Two paths can race to close one symbol; the loser must not raise."""
        client.close_position.side_effect = errors.http(status)
        assert broker.close_position("SNDQ") is None

    @pytest.mark.parametrize("status", [403, 404])
    def test_tolerates_api_error_shape_too(self, client, errors, status):
        client.close_position.side_effect = errors.api(status)
        assert broker.close_position("SNDQ") is None

    def test_real_failures_still_raise(self, client, errors):
        client.close_position.side_effect = errors.http(500)
        with pytest.raises(requests.exceptions.HTTPError):
            broker.close_position("AAPL")

    def test_success_returns_the_order(self, client, order):
        client.close_position.return_value = order()
        assert broker.close_position("AAPL") is not None


class TestWaitForFill:
    def test_returns_price_already_on_the_order(self, client, order):
        assert broker.wait_for_fill(order(filled="47.55")) == 47.55
        client.get_order_by_id.assert_not_called()

    def test_polls_until_the_fill_lands(self, client, order):
        client.get_order_by_id.return_value = order(filled="47.55")
        assert broker.wait_for_fill(order()) == 47.55
        assert client.get_order_by_id.called

    def test_gives_up_and_returns_none(self, client, order):
        client.get_order_by_id.return_value = order(filled=None)
        assert broker.wait_for_fill(order(), timeout=0.5) is None

    def test_none_order_is_safe(self):
        """close_position returns None when the position was already gone."""
        assert broker.wait_for_fill(None) is None

    def test_lookup_failure_falls_back_to_none(self, client, order, errors):
        client.get_order_by_id.side_effect = errors.http(500)
        assert broker.wait_for_fill(order(), timeout=0.5) is None
