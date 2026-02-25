"""Unit tests for apply_ramp_up_limit.

Covers: increase allowed after cooldown, increase blocked within cooldown,
decrease always allowed, no prior reduction allows increase, exact boundary,
zero cooldown disables hold.
"""

from custom_components.ev_lb.load_balancer import apply_ramp_up_limit


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
