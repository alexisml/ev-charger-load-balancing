"""Unit tests for the EV charger load-balancing computation logic.

Each test is written from the perspective of what the user observes:
whether the charger receives more or less current, stops charging, or
resumes after a cooldown.

Tests cover:
- compute_available_current: basic, edge cases, negative available
- clamp_current: clamping to min/max, step flooring, returns None below min
- distribute_current: single charger, multi-charger fairness, caps, shutoff,
  disabled state, power sensor unavailable, charger at zero load
- apply_ramp_up_limit: cooldown enforcement, no-op when decreasing or no prior reduction

The computation functions live in custom_components/ev_lb/load_balancer.py.
"""

import pytest
from custom_components.ev_lb.load_balancer import (
    VOLTAGE_DEFAULT,
    compute_available_current,
    clamp_current,
    distribute_current,
    apply_ramp_up_limit,
)


# ---------------------------------------------------------------------------
# compute_available_current
# ---------------------------------------------------------------------------


class TestComputeAvailableCurrentBasic:
    def test_no_ev_load(self):
        """With no EV charging, available = service_limit - house_load."""
        # 5 kW total @ 230 V → ~21.7 A; limit 32 A → ~10.3 A headroom
        available = compute_available_current(
            house_power_w=5000.0,
            max_service_a=32.0,
            voltage_v=230.0,
        )
        assert abs(available - (32.0 - 5000.0 / 230.0)) < 1e-9

    def test_house_power_includes_ev_draw(self):
        """House power includes EV draw; formula uses total consumption directly."""
        # House total 7 kW (including EV): available = 32 - 7000/230 ≈ 1.57 A headroom
        available = compute_available_current(
            house_power_w=7000.0,
            max_service_a=32.0,
            voltage_v=230.0,
        )
        assert abs(available - (32.0 - 7000.0 / 230.0)) < 1e-9

    def test_available_matches_full_capacity(self):
        """When total draw is zero, all capacity is available."""
        available = compute_available_current(
            house_power_w=0.0,
            max_service_a=32.0,
        )
        assert abs(available - 32.0) < 1e-9

    def test_total_draw_exceeds_service_limit(self):
        """Returns negative when total draw already exceeds service limit."""
        # 9 kW @ 230 V ≈ 39.1 A > 32 A limit → negative headroom
        available = compute_available_current(
            house_power_w=9000.0,
            max_service_a=32.0,
            voltage_v=230.0,
        )
        assert available < 0

    def test_uses_default_voltage(self):
        """Default voltage of 230 V is used when not specified."""
        available_default = compute_available_current(
            house_power_w=2300.0,
            max_service_a=32.0,
        )
        available_explicit = compute_available_current(
            house_power_w=2300.0,
            max_service_a=32.0,
            voltage_v=VOLTAGE_DEFAULT,
        )
        assert abs(available_default - available_explicit) < 1e-9

    def test_different_voltage(self):
        """Calculation scales correctly for 120 V systems."""
        available = compute_available_current(
            house_power_w=1200.0,
            max_service_a=100.0,
            voltage_v=120.0,
        )
        assert abs(available - (100.0 - 1200.0 / 120.0)) < 1e-9


# ---------------------------------------------------------------------------
# clamp_current
# ---------------------------------------------------------------------------


class TestClampCurrent:
    def test_available_within_limits(self):
        """Charger receives its target current when headroom is within safe operating limits."""
        result = clamp_current(available_a=20.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result == 20.0

    def test_capped_at_max(self):
        """Charger is capped at its rated maximum even when more headroom is available."""
        result = clamp_current(available_a=40.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result == 32.0

    def test_below_min_returns_none(self):
        """Charging stops rather than operating at unsafe low current when headroom is insufficient."""
        result = clamp_current(available_a=4.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result is None

    def test_exactly_at_min(self):
        """Charger continues charging at exactly the minimum safe current."""
        result = clamp_current(available_a=6.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result == 6.0

    def test_exactly_at_max(self):
        """Charger charges at its rated maximum when headroom exactly matches it."""
        result = clamp_current(available_a=32.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result == 32.0

    def test_step_flooring(self):
        """Target current is rounded down to the nearest 1 A step to match typical charger resolution."""
        # 17.9 A floored to 1 A step → 17 A
        result = clamp_current(
            available_a=17.9, max_charger_a=32.0, min_charger_a=6.0, step_a=1.0
        )
        assert result == 17.0

    def test_custom_step(self):
        """Target current is rounded down to a user-configured step size (e.g. 2 A for coarser chargers)."""
        result = clamp_current(
            available_a=15.0, max_charger_a=32.0, min_charger_a=6.0, step_a=2.0
        )
        assert result == 14.0

    def test_negative_available_returns_none(self):
        """Charging stops immediately when total household load already exceeds the service limit."""
        result = clamp_current(available_a=-5.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result is None

    def test_zero_available_returns_none(self):
        """Charging stops when there is no current headroom remaining on the service limit."""
        result = clamp_current(available_a=0.0, max_charger_a=32.0, min_charger_a=6.0)
        assert result is None


# ---------------------------------------------------------------------------
# distribute_current
# ---------------------------------------------------------------------------


class TestDistributeCurrentSingleCharger:
    def test_single_charger_gets_available(self):
        """Single charger receives the full available current (up to its maximum)."""
        result = distribute_current(available_a=20.0, chargers=[(6.0, 32.0)])
        assert result == [20.0]

    def test_single_charger_capped_at_max(self):
        """Single charger is capped at its rated maximum even when more headroom exists."""
        result = distribute_current(available_a=40.0, chargers=[(6.0, 32.0)])
        assert result == [32.0]

    def test_single_charger_below_min_returns_none(self):
        """Charging stops when available headroom is below the charger's minimum operating current."""
        result = distribute_current(available_a=4.0, chargers=[(6.0, 32.0)])
        assert result == [None]

    def test_single_charger_exactly_min(self):
        """Charger continues charging at exactly the minimum when headroom matches it."""
        result = distribute_current(available_a=6.0, chargers=[(6.0, 32.0)])
        assert result == [6.0]

    def test_empty_charger_list(self):
        """No chargers configured returns an empty allocation list."""
        result = distribute_current(available_a=30.0, chargers=[])
        assert result == []


class TestDistributeCurrentMultiCharger:
    def test_two_chargers_equal_split(self):
        """Two identical chargers receive equal share."""
        result = distribute_current(available_a=24.0, chargers=[(6.0, 16.0), (6.0, 16.0)])
        assert result == [12.0, 12.0]

    def test_one_charger_capped_other_gets_remainder(self):
        """When one charger hits its max, the remainder goes to the other."""
        # Available: 28 A; charger A max 10 A, charger B max 32 A
        # Round 1: fair_share = 14 A; charger A capped at 10 A → remaining = 18 A
        # Round 2: charger B gets all 18 A
        result = distribute_current(available_a=28.0, chargers=[(6.0, 10.0), (6.0, 32.0)])
        assert result[0] == 10.0
        assert result[1] == 18.0

    def test_both_chargers_stopped_when_fair_share_below_min(self):
        """All chargers stop when the fair share falls below minimum for every charger."""
        # Available: 8 A; charger A min 6 A, charger B min 6 A
        # Fair share = 4 A → both below min 6 A → both stopped
        result = distribute_current(available_a=8.0, chargers=[(6.0, 32.0), (6.0, 32.0)])
        # Each fair share is 4 A < 6 A → both stopped
        assert result == [None, None]

    def test_three_chargers_fair_share(self):
        """Three chargers receive equal fair share when none is capped."""
        result = distribute_current(available_a=30.0, chargers=[(6.0, 16.0)] * 3)
        assert result == [10.0, 10.0, 10.0]

    def test_three_chargers_one_capped(self):
        """Three chargers with one capped below fair share."""
        # Available: 30 A; chargers: A max=8, B max=16, C max=16
        # Fair share = 10 A; A capped at 8 A → remaining = 22 A, 2 chargers
        # New fair share = 11 A; B and C each get 11 A
        result = distribute_current(
            available_a=30.0,
            chargers=[(6.0, 8.0), (6.0, 16.0), (6.0, 16.0)],
        )
        assert result[0] == 8.0
        assert result[1] == 11.0
        assert result[2] == 11.0

    def test_total_allocation_does_not_exceed_available(self):
        """Sum of allocated currents never exceeds available current."""
        chargers = [(6.0, 16.0), (6.0, 32.0), (6.0, 10.0)]
        available = 45.0
        result = distribute_current(available_a=available, chargers=chargers)
        total = sum(a for a in result if a is not None)
        assert total <= available + 1e-9  # small float tolerance

    def test_zero_available_all_stopped(self):
        """Zero available current stops all chargers."""
        result = distribute_current(available_a=0.0, chargers=[(6.0, 32.0), (6.0, 32.0)])
        assert result == [None, None]

    def test_negative_available_all_stopped(self):
        """Negative available current stops all chargers."""
        result = distribute_current(
            available_a=-10.0, chargers=[(6.0, 32.0), (6.0, 32.0)]
        )
        assert result == [None, None]


class TestDistributeCurrentStepBehaviour:
    def test_step_applied_to_fair_share(self):
        """Each charger's allocation is floored to the nearest 1 A step."""
        # Available: 25 A; 2 chargers; fair share = 12.5 A → floored to 12 A
        result = distribute_current(
            available_a=25.0,
            chargers=[(6.0, 32.0), (6.0, 32.0)],
            step_a=1.0,
        )
        assert result == [12.0, 12.0]

    def test_custom_step_flooring(self):
        """Each charger's allocation is floored to the user-configured step size."""
        # Available: 25 A; 2 chargers; fair share = 12.5 A → floored to 12 A with 2 A step
        result = distribute_current(
            available_a=25.0,
            chargers=[(6.0, 32.0), (6.0, 32.0)],
            step_a=2.0,
        )
        assert result == [12.0, 12.0]


# ---------------------------------------------------------------------------
# Scenario: load-balancing disabled (external to computation functions)
# ---------------------------------------------------------------------------


class TestDisabledState:
    """When load balancing is disabled the caller should not invoke these
    functions; but the computation layer itself is neutral to enable/disable."""

    def test_compute_still_works_when_lb_disabled(self):
        """The computation layer is stateless; disabling load balancing is enforced by the caller, not here."""
        available = compute_available_current(
            house_power_w=3000.0,
            max_service_a=32.0,
        )
        # 3000 W / 230 V ≈ 13.04 A; available ≈ 32 - 13.04 = 18.96 A → floored to 18 A
        result = distribute_current(available_a=available, chargers=[(6.0, 32.0)])
        assert result[0] == 18.0


# ---------------------------------------------------------------------------
# Scenario: power sensor unavailable / unknown
# ---------------------------------------------------------------------------


class TestPowerSensorUnavailable:
    """The app layer handles unavailable state; computation receives 0.0 as
    the safe fallback.  Verify that 0 W house power leads to a sensible result.
    """

    def test_zero_house_power_with_no_ev(self):
        """When the app falls back to 0 W (e.g., because the power sensor is unavailable), the full service capacity is offered to the charger."""
        available = compute_available_current(
            house_power_w=0.0,
            max_service_a=32.0,
        )
        result = clamp_current(available, max_charger_a=32.0, min_charger_a=6.0)
        assert result == 32.0


# ---------------------------------------------------------------------------
# apply_ramp_up_limit
# ---------------------------------------------------------------------------


class TestApplyRampUpLimit:
    """Tests for the ramp-up cooldown function."""

    def test_increase_allowed_after_cooldown(self):
        """Charger current can increase once the ramp-up cooldown has fully elapsed."""
        last_reduction = 1000.0
        now = 1031.0  # 31 s later > 30 s cooldown
        result = apply_ramp_up_limit(
            prev_a=10.0,
            target_a=16.0,
            last_reduction_time=last_reduction,
            now=now,
            ramp_up_time_s=30.0,
        )
        assert result == 16.0

    def test_increase_blocked_within_cooldown(self):
        """Charger current is held at its previous value while the ramp-up cooldown is still running."""
        last_reduction = 1000.0
        now = 1020.0  # only 20 s later < 30 s cooldown
        result = apply_ramp_up_limit(
            prev_a=10.0,
            target_a=16.0,
            last_reduction_time=last_reduction,
            now=now,
            ramp_up_time_s=30.0,
        )
        assert result == 10.0

    def test_decrease_always_allowed(self):
        """Current reductions are always applied immediately, regardless of the ramp-up cooldown."""
        last_reduction = 1000.0
        now = 1001.0  # only 1 s — well within cooldown
        result = apply_ramp_up_limit(
            prev_a=16.0,
            target_a=10.0,
            last_reduction_time=last_reduction,
            now=now,
            ramp_up_time_s=30.0,
        )
        assert result == 10.0

    def test_no_prior_reduction_increase_allowed(self):
        """On first start (no prior reduction recorded) the charger current can increase freely."""
        result = apply_ramp_up_limit(
            prev_a=10.0,
            target_a=16.0,
            last_reduction_time=None,
            now=1000.0,
            ramp_up_time_s=30.0,
        )
        assert result == 16.0

    def test_same_target_as_prev(self):
        """Holding at the same current level is always allowed (no change, no cooldown applies)."""
        result = apply_ramp_up_limit(
            prev_a=16.0,
            target_a=16.0,
            last_reduction_time=1000.0,
            now=1005.0,
            ramp_up_time_s=30.0,
        )
        assert result == 16.0

    def test_exactly_at_cooldown_boundary(self):
        """Charger current is allowed to increase at exactly the cooldown boundary (boundary is inclusive)."""
        last_reduction = 1000.0
        now = 1030.0  # exactly 30 s elapsed
        result = apply_ramp_up_limit(
            prev_a=10.0,
            target_a=16.0,
            last_reduction_time=last_reduction,
            now=now,
            ramp_up_time_s=30.0,
        )
        assert result == 16.0

    def test_zero_cooldown_always_allows_increase(self):
        """Setting ramp-up time to 0 disables the cooldown and allows instant current increases."""
        result = apply_ramp_up_limit(
            prev_a=10.0,
            target_a=16.0,
            last_reduction_time=1000.0,
            now=1000.0,  # zero elapsed
            ramp_up_time_s=0.0,
        )
        assert result == 16.0
