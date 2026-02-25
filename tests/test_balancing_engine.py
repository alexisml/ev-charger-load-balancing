"""Tests for the single-charger balancing engine (PR-3).

Tests cover:
- Power meter state changes trigger entity updates
- Target current is computed correctly from available headroom
- Current is capped at the charger maximum
- Charging stops when headroom is below minimum EV current
- Current reductions are instant (no ramp-up delay)
- Current increases are held during ramp-up cooldown
- Load balancing respects the enabled/disabled switch
- Unavailable/unknown power meter applies fallback based on configured behavior
- Three unavailable modes: stop (default), ignore, set_current (capped at min of fallback and max charger current)
- Non-numeric power meter values are ignored
- Runtime changes to max charger current and min EV current trigger immediate recomputation
- Re-enabling the switch triggers immediate recomputation
- Normal computation resumes when meter recovers from unavailable
- Power meter unavailable after HA is fully loaded triggers the configured fallback
- During HA startup, fallback deferred until EVENT_HOMEASSISTANT_STARTED to avoid false positives
  from not-yet-loaded integrations
"""

from unittest.mock import patch, PropertyMock

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_CHARGER_STATUS_ENTITY,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DOMAIN,
    UNAVAILABLE_BEHAVIOR_IGNORE,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
    UNAVAILABLE_BEHAVIOR_STOP,
)
from custom_components.ev_lb.coordinator import EvLoadBalancerCoordinator
from conftest import POWER_METER, setup_integration, get_entity_id


# ---------------------------------------------------------------------------
# Basic target-current computation
# ---------------------------------------------------------------------------


class TestBasicTargetComputation:
    """Verify that power meter changes update the target current sensor."""

    async def test_normal_load_sets_charger_to_available_headroom(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger receives available headroom when it is within safe limits."""
        await setup_integration(hass, mock_config_entry)

        # 5 kW house load at 230 V → ~21.7 A draw → headroom = 32 - 21.7 = 10.3
        # Starting from 0 A, target = 0 + 10.3 = 10.3 → floored to 10 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        state = hass.states.get(current_set_id)
        assert float(state.state) == 10.0

    async def test_low_load_caps_at_charger_maximum(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger is capped at its maximum even when headroom exceeds it."""
        await setup_integration(hass, mock_config_entry)

        # Lower max charger current to 16 A so capping is clearly visible
        max_current_id = get_entity_id(
            hass, mock_config_entry, "number", "max_charger_current"
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 16.0},
            blocking=True,
        )

        # Very low house load → raw target ≈ 31 A → capped at 16 A
        hass.states.async_set(POWER_METER, "100")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        state = hass.states.get(current_set_id)
        assert float(state.state) == 16.0

    async def test_available_current_sensor_updates(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Available current sensor shows the maximum current the EV can safely draw."""
        await setup_integration(hass, mock_config_entry)

        # 3000 W at 230 V (no EV yet, so non_ev = house = 3000 W) → available = 32 - 13.04 = 18.96 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        available_id = get_entity_id(
            hass, mock_config_entry, "sensor", "available_current"
        )
        state = hass.states.get(available_id)
        assert abs(float(state.state) - (32.0 - 3000.0 / 230.0)) < 0.1

    async def test_active_binary_sensor_turns_on(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Active binary sensor turns on when the charger receives current."""
        await setup_integration(hass, mock_config_entry)

        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        active_id = get_entity_id(
            hass, mock_config_entry, "binary_sensor", "active"
        )
        state = hass.states.get(active_id)
        assert state.state == "on"


# ---------------------------------------------------------------------------
# Charger stops when overloaded
# ---------------------------------------------------------------------------


class TestOverloadStopsCharging:
    """Verify charging stops when headroom is below minimum EV current."""

    async def test_charging_stops_when_no_headroom(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charging stops when total household load exceeds the service limit."""
        await setup_integration(hass, mock_config_entry)

        # 9000 W at 230 V ≈ 39.1 A > 32 A service limit → negative headroom
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        active_id = get_entity_id(
            hass, mock_config_entry, "binary_sensor", "active"
        )
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_charging_stops_when_headroom_below_min(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charging stops when available headroom is below minimum EV current (6 A default)."""
        await setup_integration(hass, mock_config_entry)

        # 6500 W at 230 V ≈ 28.3 A → headroom = 32 - 28.3 = 3.7 A < 6 A min
        hass.states.async_set(POWER_METER, "6500")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        assert float(hass.states.get(current_set_id).state) == 0.0


# ---------------------------------------------------------------------------
# Instant reduction
# ---------------------------------------------------------------------------


class TestInstantReduction:
    """Verify current reductions happen immediately without delay."""

    async def test_current_drops_instantly_on_load_increase(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """When household load increases, charger current drops on the next meter event."""
        await setup_integration(hass, mock_config_entry)

        # Step 1: moderate load → charger gets some current
        # 3000 W at 230 V (no EV yet): non_ev = 3000 W → available = 32 - 13.04 = 18.96 → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        first_value = float(hass.states.get(current_set_id).state)
        assert first_value == 18.0

        # Step 2: heavy load (meter includes EV at 18 A = 4140 W) → must reduce
        # 8000 W total: non_ev = 8000 - 18*230 = 3860 W → available = 32 - 16.78 = 15.22 → 15 A
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        second_value = float(hass.states.get(current_set_id).state)
        assert second_value == 15.0
        assert second_value < first_value


# ---------------------------------------------------------------------------
# Ramp-up cooldown
# ---------------------------------------------------------------------------


class TestRampUpCooldown:
    """Verify the ramp-up cooldown prevents current increases after a reduction."""

    async def test_increase_blocked_during_cooldown(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger current is held after a reduction while cooldown is active."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

        # Use a controllable clock
        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        # Step 1: initial load → charger gets 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        initial = float(hass.states.get(current_set_id).state)
        assert initial == 18.0

        # Step 2: heavy load → reduction to 15 A (recorded at t=1001)
        mock_time = 1001.0
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()
        reduced = float(hass.states.get(current_set_id).state)
        assert reduced == 15.0

        # Step 3: load drops but within cooldown → current should be held
        mock_time = 1010.0  # only 9 s after reduction (< 30 s)
        hass.states.async_set(POWER_METER, "3001")
        await hass.async_block_till_done()
        held = float(hass.states.get(current_set_id).state)
        assert held == reduced  # not increased

    async def test_increase_allowed_after_cooldown(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger current can increase once the cooldown period has elapsed."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        # Step 1: initial load → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        initial = float(hass.states.get(current_set_id).state)
        assert initial == 18.0

        # Step 2: heavy load → reduction at t=1001
        mock_time = 1001.0
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()
        reduced = float(hass.states.get(current_set_id).state)
        assert reduced == 15.0

        # Step 3: load drops and cooldown elapsed → should increase
        mock_time = 1032.0  # 31 s after reduction (> 30 s)
        hass.states.async_set(POWER_METER, "3002")
        await hass.async_block_till_done()
        after_cooldown = float(hass.states.get(current_set_id).state)
        assert after_cooldown > reduced


# ---------------------------------------------------------------------------
# Enabled/disabled switch
# ---------------------------------------------------------------------------


class TestEnabledSwitch:
    """Verify load balancing respects the enabled/disabled switch."""

    async def test_disabled_switch_ignores_power_changes(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Power meter changes are ignored when load balancing is disabled."""
        await setup_integration(hass, mock_config_entry)

        switch_id = get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # Disable load balancing
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )

        # Change power meter — should NOT update current_set
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_reenabled_switch_resumes_balancing(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Load balancing resumes when the switch is re-enabled."""
        await setup_integration(hass, mock_config_entry)

        switch_id = get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # Disable then re-enable
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )

        # Now power meter changes should work
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) > 0


# ---------------------------------------------------------------------------
# Power meter edge cases
# ---------------------------------------------------------------------------


class TestPowerMeterEdgeCases:
    """Verify edge cases with unavailable/unknown/invalid power meter values."""

    async def test_unavailable_power_meter_applies_fallback_current(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Unavailable power meter triggers fallback to configured current (default 0 A)."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # First set a valid value — 3000 W at 230 V → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Now set unavailable — should fall back to 0 A (stop charging)
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_unknown_power_meter_applies_fallback_current(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Unknown power meter triggers fallback to configured current (default 0 A)."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # First set a valid value — 3000 W at 230 V → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        hass.states.async_set(POWER_METER, "unknown")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_non_numeric_power_meter_ignored(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Non-numeric power meter state is ignored."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        before = float(hass.states.get(current_set_id).state)

        hass.states.async_set(POWER_METER, "not_a_number")
        await hass.async_block_till_done()
        after = float(hass.states.get(current_set_id).state)

        assert after == before


# ---------------------------------------------------------------------------
# Runtime parameter changes
# ---------------------------------------------------------------------------


class TestRuntimeParameterChanges:
    """Verify that changing number entities immediately triggers recomputation."""

    async def test_lower_max_charger_current_caps_target_immediately(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Lowering the max charger current immediately caps the target without a new meter event."""
        await setup_integration(hass, mock_config_entry)

        max_current_id = get_entity_id(
            hass, mock_config_entry, "number", "max_charger_current"
        )
        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # Set moderate load → charger gets 18 A (at default max 32 A)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Lower max charger current to 10 A → immediate recomputation
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # No new meter event needed — target is already capped at 10 A
        assert float(hass.states.get(current_set_id).state) == 10.0

    async def test_higher_min_ev_current_stops_charging_immediately(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Raising the min EV current threshold immediately stops charging without a new meter event."""
        await setup_integration(hass, mock_config_entry)

        min_current_id = get_entity_id(
            hass, mock_config_entry, "number", "min_ev_current"
        )
        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # Step 1: non-EV load 5520 W → headroom = 8 A → charger starts at 8 A
        hass.states.async_set(POWER_METER, "5520")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 8.0

        # Step 2: simulate realistic meter (non-EV + EV draw = 5520 + 8*230 = 7360)
        hass.states.async_set(POWER_METER, "7360")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 8.0  # stable

        # Step 3: raise min to 10 A → immediate recomputation → 8 A < 10 A → stop
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": min_current_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # No new meter event needed — charging already stopped
        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_switch_reenable_triggers_recomputation(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Re-enabling the switch immediately recomputes from the current power meter value."""
        await setup_integration(hass, mock_config_entry)

        switch_id = get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # Set a power meter value while enabled
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) > 0

        # Disable → state stays (no reset)
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch_id}, blocking=True
        )

        # Change power meter while disabled — ignored
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        # Re-enable → should immediately recompute from the current meter value (5000 W)
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch_id}, blocking=True
        )
        await hass.async_block_till_done()

        # target = prev_set + available = prev + (32 - 5000/230)
        # It should have a value that corresponds to the current meter reading
        value = float(hass.states.get(current_set_id).state)
        assert value > 0

    async def test_parameter_change_silently_skipped_when_meter_state_is_unparsable(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Parameter change while meter state is non-numeric (but not unavailable) is silently skipped."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # Set meter to a value that is not "unavailable"/"unknown" but cannot be parsed as float
        hass.states.async_set(POWER_METER, "not_a_number")
        await hass.async_block_till_done()

        # Trigger async_recompute_from_current_state via a number entity change
        max_current_id = get_entity_id(
            hass, mock_config_entry, "number", "max_charger_current"
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 20.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Integration must not crash; the new parameter is recorded
        assert coordinator.max_charger_current == 20.0


# ---------------------------------------------------------------------------
# Unavailable behavior modes
# ---------------------------------------------------------------------------


class TestUnavailableBehaviorStop:
    """Verify 'stop' mode sets charger to 0 A when meter is unavailable (default)."""

    async def test_stop_mode_sets_zero_on_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """Charger is set to 0 A when meter becomes unavailable in stop mode."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_STOP,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"


class TestUnavailableBehaviorIgnore:
    """Verify 'ignore' mode keeps the last value when meter is unavailable."""

    async def test_ignore_mode_keeps_last_value(
        self, hass: HomeAssistant
    ) -> None:
        """Charger keeps its last computed current when meter becomes unavailable in ignore mode."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_IGNORE,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")

        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"

        # Meter goes unavailable — ignore mode keeps last value
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0
        assert hass.states.get(active_id).state == "on"


class TestUnavailableBehaviorSetCurrent:
    """Verify 'set_current' mode applies min(fallback, max_charger_current) when meter is unavailable."""

    async def test_set_current_mode_caps_at_max_charger_current(
        self, hass: HomeAssistant
    ) -> None:
        """Fallback current is capped at max charger current when it is lower."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 50.0,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Normal: target = 10 A (5000 W at 230 V)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 10.0

        # Meter goes unavailable → fallback 50 A but capped at max_charger_current 32 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 32.0

    async def test_set_current_mode_uses_fallback_when_lower(
        self, hass: HomeAssistant
    ) -> None:
        """Fallback current is used directly when it is lower than the current target."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 6.0,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        active_id = get_entity_id(hass, entry, "binary_sensor", "active")

        # Normal: target = 18 A (3000 W at 230 V)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Meter goes unavailable → fallback 6 A (< 18 A), so use 6 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 6.0
        assert hass.states.get(active_id).state == "on"


class TestMeterRecovery:
    """Verify normal computation resumes when the meter recovers from unavailable."""

    async def test_meter_recovery_resumes_normal_computation(
        self, hass: HomeAssistant
    ) -> None:
        """When the meter recovers from unavailable, normal computation resumes."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 6.0,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Normal operation
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Meter goes unavailable → fallback
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 6.0

        # Meter recovers → resumes normal computation
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        recovered_value = float(hass.states.get(current_set_id).state)
        assert recovered_value > 0


# ---------------------------------------------------------------------------
# Parameter changes while meter is unavailable
# ---------------------------------------------------------------------------


class TestParameterChangeWithUnavailableMeter:
    """Verify fallback limits are enforced when parameters change while meter is unavailable."""

    async def test_set_current_mode_caps_fallback_when_max_charger_lowered(
        self, hass: HomeAssistant
    ) -> None:
        """In set_current mode, lowering max charger current while meter is unavailable adjusts the charger."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 20.0,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        max_current_id = get_entity_id(hass, entry, "number", "max_charger_current")

        # Start charging normally
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Meter goes unavailable → fallback = min(20, 32) = 20 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 20.0

        # Lowering max charger to 8 A while meter is still unavailable
        # → fallback should become min(20, 8) = 8 A
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 8.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 8.0

    async def test_ignore_mode_clamps_current_when_max_charger_lowered(
        self, hass: HomeAssistant
    ) -> None:
        """In ignore mode, lowering max charger current while meter is unavailable adjusts the charger."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_IGNORE,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        max_current_id = get_entity_id(hass, entry, "number", "max_charger_current")

        # Start charging at 18 A (3000 W at 230 V)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Meter goes unavailable → ignore mode keeps 18 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Lowering max charger to 8 A while meter is still unavailable
        # → current must be clamped to 8 A (cannot exceed new charger max)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 8.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 8.0

    async def test_ignore_mode_stops_when_min_raised_above_current(
        self, hass: HomeAssistant
    ) -> None:
        """In ignore mode, raising min EV current above the held value while meter is unavailable stops charging."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_IGNORE,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        min_current_id = get_entity_id(hass, entry, "number", "min_ev_current")

        # Start charging at 8 A with moderate load
        hass.states.async_set(POWER_METER, "5520")
        await hass.async_block_till_done()
        hass.states.async_set(POWER_METER, "7360")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 8.0

        # Meter goes unavailable → ignore mode keeps 8 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 8.0

        # Raising min EV current to 10 A → 8 A < 10 A → charging must stop
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": min_current_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_stop_mode_stays_zero_when_parameter_changes(
        self, hass: HomeAssistant
    ) -> None:
        """In stop mode, changing max charger current while meter is unavailable keeps the charger stopped."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_STOP,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        max_current_id = get_entity_id(hass, entry, "number", "max_charger_current")

        # Start charging then let meter go unavailable → stop
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0

        # Changing max charger while stopped → stays at 0 A
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": max_current_id, "value": 16.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0


# ---------------------------------------------------------------------------
# Startup with unavailable power meter
# ---------------------------------------------------------------------------


class TestStartupWithUnavailableMeter:
    """Verify correct behaviour when the power meter is unavailable when the integration loads.

    In the test environment ``hass.is_running`` is ``True`` (HA is already
    fully started), which is equivalent to loading the integration via the UI
    after HA has started.  The coordinator evaluates meter health synchronously
    on ``async_start`` once entity setup is complete.

    In a real HA startup the equivalent behaviour is triggered by the
    ``EVENT_HOMEASSISTANT_STARTED`` listener registered in ``async_start``,
    which fires after all integrations have loaded so transient unavailability
    during dependency loading is ignored.
    """

    async def test_stop_mode_applies_fallback_when_meter_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """In stop mode, a genuinely unavailable meter sets the charger to 0 A."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_STOP,
            },
            title="EV Load Balancing",
        )
        # Register meter as unavailable BEFORE setup
        hass.states.async_set(POWER_METER, "unavailable")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        meter_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(meter_id).state == "off"
        assert hass.states.get(fallback_id).state == "on"

    async def test_set_current_mode_applies_fallback_when_meter_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """In set_current mode, a genuinely unavailable meter sets the charger to the fallback current."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 10.0,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "unavailable")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        fallback_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        assert float(hass.states.get(current_set_id).state) == 10.0
        assert hass.states.get(fallback_id).state == "on"

    async def test_ignore_mode_keeps_zero_when_meter_unavailable(
        self, hass: HomeAssistant
    ) -> None:
        """In ignore mode, a genuinely unavailable meter keeps the charger at the restored current
        (0 on fresh install)."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_IGNORE,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "unavailable")
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        meter_id = get_entity_id(hass, entry, "binary_sensor", "meter_status")
        fallback_id = get_entity_id(hass, entry, "binary_sensor", "fallback_active")

        # On a fresh install current_set_a restores to 0 — ignore mode keeps it
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(meter_id).state == "off"
        assert hass.states.get(fallback_id).state == "on"

    async def test_meter_healthy_when_valid_reading_present(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Meter status is healthy when a valid reading is present at load time."""
        # setup_integration pre-sets the meter to "0" before setup
        from conftest import setup_integration
        await setup_integration(hass, mock_config_entry)

        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        meter_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "meter_status")

        assert coordinator.meter_healthy is True
        assert hass.states.get(meter_id).state == "on"


# ---------------------------------------------------------------------------
# Deferred startup: coordinator registered before HA finishes loading
# ---------------------------------------------------------------------------


class TestCoordinatorDeferredStartup:
    """Coordinator defers meter health evaluation when HA is still starting up.

    When an integration loads during the HA boot sequence (not via UI after
    startup), ``hass.is_running`` is ``False``.  The coordinator registers a
    one-shot listener for ``EVENT_HOMEASSISTANT_STARTED`` and only evaluates
    meter health once HA reports it has fully loaded, avoiding spurious
    fallback actions from not-yet-registered dependency entities.
    """

    async def test_deferred_startup_registers_ha_started_listener(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Coordinator registers a startup listener instead of checking the meter immediately during HA boot."""
        coordinator = EvLoadBalancerCoordinator(hass, mock_config_entry)

        with patch.object(
            type(hass), "is_running", new_callable=PropertyMock, return_value=False
        ):
            coordinator.async_start()

        # State-change listener is active; meter health has not been evaluated yet
        assert coordinator._unsub_listener is not None
        assert coordinator.meter_healthy is True  # Default, not yet evaluated

        coordinator.async_stop()

    async def test_deferred_startup_applies_fallback_when_meter_unavailable_at_ha_start(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Fallback is applied when the meter is still unavailable when HA finishes loading."""
        hass.states.async_set(POWER_METER, "unavailable")
        coordinator = EvLoadBalancerCoordinator(hass, mock_config_entry)

        with patch.object(
            type(hass), "is_running", new_callable=PropertyMock, return_value=False
        ):
            coordinator.async_start()

        # Fire the HA started event — meter is still unavailable
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED, {})
        await hass.async_block_till_done()

        assert coordinator.meter_healthy is False
        assert coordinator.fallback_active is True
        assert coordinator.current_set_a == 0.0  # Stop mode (default)

        coordinator.async_stop()

    async def test_deferred_startup_no_action_when_coordinator_already_stopped(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Entry unloaded before HA finishes starting — the deferred event callback does nothing."""
        coordinator = EvLoadBalancerCoordinator(hass, mock_config_entry)

        with patch.object(
            type(hass), "is_running", new_callable=PropertyMock, return_value=False
        ):
            coordinator.async_start()

        # Unload the coordinator before HA fires EVENT_HOMEASSISTANT_STARTED
        coordinator.async_stop()
        assert coordinator._unsub_listener is None

        # Fire the event — the guard inside _handle_ha_started should prevent any action
        hass.states.async_set(POWER_METER, "unavailable")
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED, {})
        await hass.async_block_till_done()

        # Coordinator remains in its initial state — callback did nothing
        assert coordinator.meter_healthy is True


# ---------------------------------------------------------------------------
# Charger status sensor
# ---------------------------------------------------------------------------


class TestChargerStatusSensor:
    """Verify the balancer correctly uses the charger status sensor when configured.

    When a charger status sensor is configured, the balancer reads its state to
    determine whether the EV is actively drawing current.  If the sensor reports
    a state other than 'Charging', the EV draw estimate is zeroed so the
    balancer does not over-subtract headroom when the charger is idle.
    """

    async def test_headroom_not_over_subtracted_when_ev_not_charging(
        self, hass: HomeAssistant
    ) -> None:
        """Available headroom reflects full service capacity when EV is not actively charging.

        If the charger reports it is NOT charging (state != 'Charging'), the
        balancer must not subtract the previously commanded current from the
        available headroom.  This prevents the balancer from under-reporting
        headroom when the EV has finished charging or is paused.
        """
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_CHARGER_STATUS_ENTITY: status_entity,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Available")  # EV not charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # 5 kW load at 230 V → 21.7 A draw → headroom = 32 - 21.7 = 10.3 → 10 A
        # EV is not charging, so current_set_a estimate is 0 (not subtracted)
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 10.0

    async def test_headroom_accounts_for_ev_draw_when_charging(
        self, hass: HomeAssistant
    ) -> None:
        """Available headroom correctly isolates non-EV load when EV is actively charging.

        When the charger status sensor reports 'Charging', the balancer subtracts
        the last commanded current from the total service draw to isolate the
        non-EV household load before computing the new target.
        """
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
                CONF_CHARGER_STATUS_ENTITY: status_entity,
            },
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Charging")  # EV actively charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # First reading at 3 kW: current_set starts at 0, so ev_estimate = 0
        # service = 13.04 A, non-EV = 13.04, available = 18.96 → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Second reading at 5 kW: status=Charging, ev_estimate = 18 A
        # service = 21.74 A, non-EV = 21.74 - 18 = 3.74, available = 28.26 → 28 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 28.0

    async def test_no_status_sensor_behaves_as_before(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Balancing is unaffected when no charger status sensor is configured.

        When the status sensor is absent, the coordinator falls back to the
        original behaviour: the last commanded current is always subtracted from
        the service draw.
        """
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )

        # 3 kW → current_set = 18 A (no EV draw estimate since current_set was 0)
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # 5 kW: no sensor → assume EV is drawing 18 A → non-EV = 21.74 - 18 = 3.74
        # available = 32 - 3.74 = 28.26 → 28 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 28.0

    async def test_status_sensor_configured_via_options_flow(
        self, hass: HomeAssistant
    ) -> None:
        """Charger status sensor set via the options flow is honoured by the coordinator.

        Users can configure (or change) the status sensor after initial setup
        via the Configure dialog.  The coordinator must pick up the value from
        options, just like action scripts.
        """
        status_entity = "sensor.ocpp_status"
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 32.0,
            },
            options={CONF_CHARGER_STATUS_ENTITY: status_entity},
            title="EV Load Balancing",
        )
        hass.states.async_set(POWER_METER, "0")
        hass.states.async_set(status_entity, "Available")  # EV not charging
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        assert coordinator._charger_status_entity == status_entity
        assert coordinator._is_ev_charging() is False


# ---------------------------------------------------------------------------
# Overload correction loop
# ---------------------------------------------------------------------------


class TestOverloadCorrectionLoop:
    """The coordinator triggers a rapid correction loop when the system is overloaded.

    When available current drops below zero (consumption exceeds the service
    limit) the coordinator schedules a trigger after *overload_trigger_delay_s*
    seconds and then fires a periodic loop every *overload_loop_interval_s*
    seconds until the overload clears.  This ensures the charger is corrected
    even if the power meter does not report a new state value.
    """

    async def test_overload_loop_not_started_without_overload(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """No overload timers are created when available current is positive."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # 3 kW → available > 0
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None

    async def test_overload_trigger_scheduled_when_overloaded(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A trigger timer is scheduled when the system first becomes overloaded."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Set current so that non-EV load is 0; push power far above service limit
        # service_current = 9000/230 ≈ 39.1 A > 32 A service limit → overloaded
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()

        assert coordinator.available_current_a < 0
        assert coordinator._overload_trigger_unsub is not None
        assert coordinator._overload_loop_unsub is None  # loop not yet started

    async def test_overload_trigger_fires_and_starts_loop(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """After the trigger delay the correction loop starts while still overloaded."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0
        coordinator.overload_loop_interval_s = 5.0

        # Drive the system into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Cancel the real timer and fire the callback directly to avoid lingering timer
        coordinator._overload_trigger_unsub()
        coordinator._overload_trigger_unsub = None
        import homeassistant.util.dt as ha_dt
        coordinator._on_overload_triggered(ha_dt.utcnow())
        await hass.async_block_till_done()

        # Loop should be running since still overloaded
        assert coordinator._overload_loop_unsub is not None

    async def test_overload_timers_cancelled_when_cleared(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """All overload timers are cancelled once available current returns to zero or above."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Drive into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Reduce load — now available > 0
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        assert coordinator.available_current_a > 0
        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None

    async def test_overload_timers_cancelled_on_stop(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Overload timers are cleaned up when the coordinator is stopped."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator.overload_trigger_delay_s = 2.0

        # Drive into overload
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()
        assert coordinator._overload_trigger_unsub is not None

        # Unload the integration
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()

        assert coordinator._overload_trigger_unsub is None
        assert coordinator._overload_loop_unsub is None
