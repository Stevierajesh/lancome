"""Run context included in judge case files and prompts."""

import pytest

from trader import config
from trader.enrichment import CaseFile, build_run_context
from trader.judge import _build_prompt
from trader.signals import Signal


def test_run_context_includes_target_progress(account, monkeypatch):
    monkeypatch.setattr(config, "TARGET_EQUITY", 125_000.0)

    context = build_run_context(account, {"AAPL": {}, "NVDA": {}}, stocks_open=True)

    assert context["market_date"]
    assert context["stocks_open"] is True
    assert context["open_positions"] == 2
    assert context["target_equity"] == 125_000.0
    assert context["current_equity"] == account["equity"]
    assert context["target_remaining"] == 25_000.0
    assert context["target_progress_pct"] == pytest.approx(0.8)
    assert context["max_open_positions"] == config.MAX_OPEN_POSITIONS
    assert context["stop_loss_pct"] == config.STOP_LOSS_PCT


def test_run_context_omits_progress_when_target_disabled(account, monkeypatch):
    monkeypatch.setattr(config, "TARGET_EQUITY", 0)

    context = build_run_context(account, {}, stocks_open=False)

    assert context["target_equity"] is None
    assert "target_remaining" not in context
    assert "target_progress_pct" not in context


def test_prompt_includes_run_context_without_overriding_risk(account):
    case = CaseFile(
        symbol="AAPL",
        bars=[{"t": "2026-08-18T14:00:00", "o": 100, "h": 101, "l": 99, "c": 100, "v": 1000}],
        signal=Signal("AAPL", "buy", "test rule", {"price": 100}),
        portfolio={"account": account, "positions": {}},
        run_context={
            "market_date": "2026-08-18",
            "target_equity": 125_000.0,
            "target_remaining": 25_000.0,
            "daily_loss_limit_pct": config.DAILY_LOSS_LIMIT_PCT,
        },
    )

    prompt = _build_prompt(case)

    assert "Run context:" in prompt
    assert '"target_equity": 125000.0' in prompt
    assert "Never approve solely because a target exists" in prompt
