"""Hard risk limits — the layer that runs regardless of signals or judge."""

import pytest

from trader import config, risk


class TestDailyLoss:
    def test_not_breached_when_flat(self, account):
        assert risk.daily_loss_breached(account) is False

    def test_breached_at_the_limit(self, account):
        account["equity"] = account["last_equity"] * (1 - config.DAILY_LOSS_LIMIT_PCT)
        assert risk.daily_loss_breached(account) is True

    def test_not_breached_just_inside(self, account):
        account["equity"] = account["last_equity"] * (1 - config.DAILY_LOSS_LIMIT_PCT / 2)
        assert risk.daily_loss_breached(account) is False

    def test_gains_never_breach(self, account):
        account["equity"] = account["last_equity"] * 1.5
        assert risk.daily_loss_breached(account) is False

    def test_zero_prior_equity_does_not_divide_by_zero(self, account):
        account["last_equity"] = 0.0
        assert risk.daily_loss_breached(account) is False


class TestEntryBudget:
    def test_capped_by_position_pct(self, account):
        budget = risk._entry_budget(account, {})
        assert budget == pytest.approx(account["equity"] * config.MAX_POSITION_PCT)

    def test_capped_by_available_cash(self, account):
        account["cash"] = 500.0
        assert risk._entry_budget(account, {}) == 500.0

    def test_refused_at_the_position_cap(self, account):
        full = {f"S{i}": {} for i in range(config.MAX_OPEN_POSITIONS)}
        assert risk._entry_budget(account, full) is None

    def test_allowed_one_below_the_cap(self, account):
        nearly = {f"S{i}": {} for i in range(config.MAX_OPEN_POSITIONS - 1)}
        assert risk._entry_budget(account, nearly) is not None


class TestSizingInvariant:
    """MAX_POSITION_PCT x MAX_OPEN_POSITIONS must stay under 100% of equity.

    _entry_budget clamps to cash, so over-committing doesn't over-trade — it
    starves the last slots into dust orders and makes the cap nominal. This
    caught 13 x 10% = 130%, where the effective cap was ~10.
    """

    def test_config_fits_within_cash(self):
        assert config.MAX_POSITION_PCT * config.MAX_OPEN_POSITIONS <= 1.0

    def test_every_slot_is_fundable(self, account):
        positions, funded = {}, []
        for i in range(config.MAX_OPEN_POSITIONS + 2):
            budget = risk._entry_budget(account, positions)
            if budget is None:
                break
            funded.append(budget)
            account["cash"] -= budget
            positions[f"S{i}"] = {}

        assert len(funded) == config.MAX_OPEN_POSITIONS
        smallest = min(funded)
        largest = max(funded)
        # No slot may be starved into a fraction of a full-size position.
        assert smallest == pytest.approx(largest), "later slots were cash-starved"


class TestSizeEntry:
    def test_returns_whole_shares(self, account):
        qty = risk.size_entry(account, {}, price=100.0)
        assert qty == int(account["equity"] * config.MAX_POSITION_PCT / 100.0)

    def test_none_when_price_exceeds_budget(self, account):
        assert risk.size_entry(account, {}, price=1_000_000.0) is None

    def test_none_at_the_position_cap(self, account):
        full = {f"S{i}": {} for i in range(config.MAX_OPEN_POSITIONS)}
        assert risk.size_entry(account, full, price=10.0) is None


class TestSizeEntryNotional:
    def test_returns_the_budget(self, account):
        assert risk.size_entry_notional(account, {}) == pytest.approx(
            account["equity"] * config.MAX_POSITION_PCT)

    def test_skips_dust(self, account):
        account["cash"] = 5.0
        assert risk.size_entry_notional(account, {}) is None


class TestExitReason:
    def test_stop_loss(self, position):
        assert "stop-loss" in risk.exit_reason(position(plpc=-config.STOP_LOSS_PCT))

    def test_take_profit(self, position):
        assert "take-profit" in risk.exit_reason(position(plpc=config.TAKE_PROFIT_PCT))

    def test_holds_inside_the_band(self, position):
        assert risk.exit_reason(position(plpc=0.01)) is None

    def test_overshoot_still_exits(self, position):
        """Stops are polled, not resting — fills routinely blow through."""
        assert "stop-loss" in risk.exit_reason(position(plpc=-0.043))
