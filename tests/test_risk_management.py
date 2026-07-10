import pytest

import risk_management as rm


class TestCalculateStopDistance:
    def test_scales_atr_by_multiplier(self):
        assert rm.calculate_stop_distance(0.0012, 1.5) == pytest.approx(0.0018)

    def test_rejects_nonpositive_atr(self):
        with pytest.raises(ValueError):
            rm.calculate_stop_distance(0, 1.5)
        with pytest.raises(ValueError):
            rm.calculate_stop_distance(-0.001, 1.5)


class TestCalculateTradeLevels:
    def test_buy_levels_bracket_entry_correctly(self):
        levels = rm.calculate_trade_levels("buy", 1.1000, 0.0012, 1.5, 4.5)
        assert levels.stop_distance == pytest.approx(0.0018)
        assert levels.stop_loss == pytest.approx(1.1000 - 0.0018)
        assert levels.take_profit == pytest.approx(1.1000 + 0.0012 * 4.5)

    def test_sell_levels_bracket_entry_correctly(self):
        levels = rm.calculate_trade_levels("sell", 1.1000, 0.0012, 1.5, 4.5)
        assert levels.stop_loss == pytest.approx(1.1000 + 0.0018)
        assert levels.take_profit == pytest.approx(1.1000 - 0.0012 * 4.5)

    def test_rejects_unknown_direction(self):
        with pytest.raises(ValueError):
            rm.calculate_trade_levels("hold", 1.1000, 0.0012, 1.5, 4.5)


class TestCalculatePositionRisk:
    def test_buy_with_stop_below_entry_has_positive_risk(self):
        risk = rm.calculate_position_risk(
            "buy", entry_price=1.1000, stop_loss=1.0982, volume=0.55,
            tick_size=0.00001, tick_value=1.0,
        )
        # (1.1000-1.0982)/0.00001 = 180 ticks * $1.0 * 0.55 lots
        assert risk == pytest.approx(180 * 1.0 * 0.55)

    def test_sell_with_stop_above_entry_has_positive_risk(self):
        risk = rm.calculate_position_risk(
            "sell", entry_price=1.1000, stop_loss=1.1018, volume=0.55,
            tick_size=0.00001, tick_value=1.0,
        )
        assert risk == pytest.approx(180 * 1.0 * 0.55)

    def test_buy_at_breakeven_has_zero_risk(self):
        risk = rm.calculate_position_risk(
            "buy", entry_price=1.1000, stop_loss=1.1000, volume=0.55,
            tick_size=0.00001, tick_value=1.0,
        )
        assert risk == 0.0

    def test_buy_trailed_into_profit_has_zero_risk(self):
        # SL above entry -- worst case is a locked-in gain, not a loss.
        risk = rm.calculate_position_risk(
            "buy", entry_price=1.1000, stop_loss=1.1050, volume=0.55,
            tick_size=0.00001, tick_value=1.0,
        )
        assert risk == 0.0

    def test_sell_trailed_into_profit_has_zero_risk(self):
        risk = rm.calculate_position_risk(
            "sell", entry_price=1.1000, stop_loss=1.0950, volume=0.55,
            tick_size=0.00001, tick_value=1.0,
        )
        assert risk == 0.0

    def test_rejects_unknown_direction(self):
        with pytest.raises(ValueError):
            rm.calculate_position_risk(
                "hold", entry_price=1.1000, stop_loss=1.0982, volume=0.55,
                tick_size=0.00001, tick_value=1.0,
            )


class TestCalculateLotSize:
    """Tick economics mirror the worked example in risk_management.py's
    module docstring: 5-digit EURUSD, tick_size=0.00001, tick_value=$1/lot."""

    def test_worked_example_matches_docstring(self):
        lot = rm.calculate_lot_size(
            balance=10_000.0,
            risk_per_trade=0.01,
            stop_distance_price=0.00180,
            tick_size=0.00001,
            tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        assert lot == pytest.approx(0.55)

    def test_rounds_down_to_volume_step_never_up(self):
        # raw_lot would be 0.5556..., which must round DOWN to 0.55, not 0.56.
        lot = rm.calculate_lot_size(
            balance=10_000.0,
            risk_per_trade=0.01,
            stop_distance_price=0.00180,
            tick_size=0.00001,
            tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        monetary_risk_per_lot = (0.00180 / 0.00001) * 1.0
        actual_risk = lot * monetary_risk_per_lot
        assert actual_risk <= 10_000.0 * 0.01

    def test_returns_none_when_below_volume_min(self):
        lot = rm.calculate_lot_size(
            balance=100.0,  # tiny balance -> tiny risk_amount
            risk_per_trade=0.01,
            stop_distance_price=0.00180,
            tick_size=0.00001,
            tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        assert lot is None

    def test_caps_at_volume_max(self):
        lot = rm.calculate_lot_size(
            balance=10_000_000.0,  # huge balance -> huge computed lot
            risk_per_trade=0.01,
            stop_distance_price=0.00180,
            tick_size=0.00001,
            tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=5.0,
        )
        assert lot == 5.0

    @pytest.mark.parametrize(
        "kwargs",
        [
            dict(balance=0),
            dict(balance=-100),
            dict(stop_distance_price=0),
            dict(stop_distance_price=-0.001),
            dict(tick_size=0),
            dict(tick_value=0),
        ],
    )
    def test_rejects_nonpositive_inputs(self, kwargs):
        base = dict(
            balance=10_000.0,
            risk_per_trade=0.01,
            stop_distance_price=0.00180,
            tick_size=0.00001,
            tick_value=1.0,
            volume_step=0.01,
            volume_min=0.01,
            volume_max=100.0,
        )
        base.update(kwargs)
        with pytest.raises(ValueError):
            rm.calculate_lot_size(**base)
