"""Shared fixtures.

Two things must happen before any `trader.*` import:
  - LANCOME_LOG_DIR is redirected, because trader.main opens logs/trader.log at
    import time and would otherwise append to the live bot's log.
  - The Alpaca keys are blanked, so a test that slips past a mock fails loudly
    on auth instead of quietly hitting the real paper account.
"""

import os
import tempfile

os.environ["LANCOME_LOG_DIR"] = tempfile.mkdtemp(prefix="lancome-test-logs-")
os.environ["ALPACA_API_KEY"] = ""
os.environ["ALPACA_SECRET_KEY"] = ""

import types  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402
from alpaca.common.exceptions import APIError  # noqa: E402


# ---------------------------------------------------------------------------
# Broker error shapes
# ---------------------------------------------------------------------------

def http_error(status: int) -> requests.exceptions.HTTPError:
    """The shape alpaca-py leaks from raise_for_status() on some endpoints."""
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def api_error(status: int, message: str = "rejected") -> APIError:
    """The shape alpaca-py raises from the trading endpoints."""
    return APIError(f'{{"code":1,"message":"{message}"}}', http_error(status))


@pytest.fixture
def errors():
    return types.SimpleNamespace(http=http_error, api=api_error)


# ---------------------------------------------------------------------------
# Account / position / order doubles
# ---------------------------------------------------------------------------

@pytest.fixture
def account():
    return {"equity": 100_000.0, "cash": 100_000.0, "last_equity": 100_000.0}


@pytest.fixture
def position():
    def build(qty=100.0, entry=10.0, now=10.0, plpc=0.0):
        return {
            "qty": qty,
            "avg_entry_price": entry,
            "current_price": now,
            "unrealized_plpc": plpc,
            "market_value": qty * now,
        }
    return build


@pytest.fixture
def order():
    """An order that reports no fill until polled — like a real market order."""
    def build(order_id="o1", filled=None):
        return types.SimpleNamespace(id=order_id, filled_avg_price=filled)
    return build


@pytest.fixture
def bars():
    """`count` flat bars, with optional closes/volumes overriding the tail."""
    def build(count=30, price=100.0, volume=1_000.0, closes=None, volumes=None):
        out = []
        for i in range(count):
            close = price if closes is None else closes[i]
            vol = volume if volumes is None else volumes[i]
            out.append({
                "t": f"2026-08-18T{14 + i // 12:02d}:{(i % 12) * 5:02d}:00",
                "o": close, "h": close * 1.001, "l": close * 0.999,
                "c": close, "v": vol,
            })
        return out
    return build


# ---------------------------------------------------------------------------
# Ledger isolation — every test that records writes to its own tmpdir
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Redirect the JSONL/CSV ledgers and expose readers for them."""
    import csv
    import json

    from trader import main

    jsonl = tmp_path / "trades.jsonl"
    csv_path = tmp_path / "trades.csv"
    monkeypatch.setattr(main, "TRADES_FILE", str(jsonl))
    monkeypatch.setattr(main, "TRADES_CSV", str(csv_path))

    class Ledger:
        def events(self):
            if not jsonl.exists():
                return []
            return [json.loads(x) for x in jsonl.read_text().splitlines() if x.strip()]

        def of_type(self, kind):
            return [e for e in self.events() if e.get("event") == kind]

        def rows(self):
            if not csv_path.exists():
                return []
            with open(csv_path) as f:
                return list(csv.DictReader(f))

    return Ledger()


@pytest.fixture(autouse=True)
def clean_module_state():
    """Reset the module-level caches that leak between tests."""
    from trader import main

    main._untradable.clear()
    main._signal_cooldowns.clear()
    yield
    main._untradable.clear()
    main._signal_cooldowns.clear()
