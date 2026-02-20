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
"""

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
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

POWER_METER = "sensor.house_power_w"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry with default config."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: POWER_METER,
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
        title="EV Load Balancing",
    )


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration and create the power meter sensor."""
    hass.states.async_set(POWER_METER, "0")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _get_entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, suffix: str
) -> str:
    """Look up entity_id from the entity registry."""
    ent_reg = er.async_get(hass)
    entity_id = ent_reg.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert entity_id is not None
    return entity_id


# ---------------------------------------------------------------------------
# Basic target-current computation
# ---------------------------------------------------------------------------


class TestBasicTargetComputation:
    """Verify that power meter changes update the target current sensor."""

    async def test_normal_load_sets_charger_to_available_headroom(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger receives available headroom when it is within safe limits."""
        await _setup(hass, mock_config_entry)

        # 5 kW house load at 230 V → ~21.7 A draw → headroom = 32 - 21.7 = 10.3
        # Starting from 0 A, target = 0 + 10.3 = 10.3 → floored to 10 A
        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        state = hass.states.get(current_set_id)
        assert float(state.state) == 10.0

    async def test_low_load_caps_at_charger_maximum(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charger is capped at its maximum even when headroom exceeds it."""
        await _setup(hass, mock_config_entry)

        # Lower max charger current to 16 A so capping is clearly visible
        max_current_id = _get_entity_id(
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

        current_set_id = _get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        state = hass.states.get(current_set_id)
        assert float(state.state) == 16.0

    async def test_available_current_sensor_updates(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Available current sensor reflects the computed headroom."""
        await _setup(hass, mock_config_entry)

        # 3000 W at 230 V → 13.04 A → headroom = 32 - 13.04 = 18.96
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        available_id = _get_entity_id(
            hass, mock_config_entry, "sensor", "available_current"
        )
        state = hass.states.get(available_id)
        assert abs(float(state.state) - (32.0 - 3000.0 / 230.0)) < 0.1

    async def test_active_binary_sensor_turns_on(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Active binary sensor turns on when the charger receives current."""
        await _setup(hass, mock_config_entry)

        hass.states.async_set(POWER_METER, "5000")
        await hass.async_block_till_done()

        active_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        # 9000 W at 230 V ≈ 39.1 A > 32 A service limit → negative headroom
        hass.states.async_set(POWER_METER, "9000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        active_id = _get_entity_id(
            hass, mock_config_entry, "binary_sensor", "active"
        )
        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_charging_stops_when_headroom_below_min(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Charging stops when available headroom is below minimum EV current (6 A default)."""
        await _setup(hass, mock_config_entry)

        # 6500 W at 230 V ≈ 28.3 A → headroom = 32 - 28.3 = 3.7 A < 6 A min
        hass.states.async_set(POWER_METER, "6500")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        # Step 1: moderate load → charger gets some current
        # 3000 W at 230 V → available = 18.96, target = 0 + 18.96 → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
            hass, mock_config_entry, "sensor", "current_set"
        )
        first_value = float(hass.states.get(current_set_id).state)
        assert first_value == 18.0

        # Step 2: heavy load exceeding service limit → negative headroom → drops
        # 8000 W at 230 V → available = -2.78, target = 18 + (-2.78) = 15.22 → 15 A
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
        await _setup(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 30.0

        # Use a controllable clock
        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        # Step 1: initial load → charger gets 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 30.0

        mock_time = 1000.0

        def fake_monotonic():
            return mock_time

        coordinator._time_fn = fake_monotonic

        # Step 1: initial load → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        switch_id = _get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        switch_id = _get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        max_current_id = _get_entity_id(
            hass, mock_config_entry, "number", "max_charger_current"
        )
        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        min_current_id = _get_entity_id(
            hass, mock_config_entry, "number", "min_ev_current"
        )
        current_set_id = _get_entity_id(
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
        await _setup(hass, mock_config_entry)

        switch_id = _get_entity_id(
            hass, mock_config_entry, "switch", "enabled"
        )
        current_set_id = _get_entity_id(
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

        current_set_id = _get_entity_id(hass, entry, "sensor", "current_set")
        active_id = _get_entity_id(hass, entry, "binary_sensor", "active")

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

        current_set_id = _get_entity_id(hass, entry, "sensor", "current_set")
        active_id = _get_entity_id(hass, entry, "binary_sensor", "active")

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

        current_set_id = _get_entity_id(hass, entry, "sensor", "current_set")

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

        current_set_id = _get_entity_id(hass, entry, "sensor", "current_set")
        active_id = _get_entity_id(hass, entry, "binary_sensor", "active")

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

        current_set_id = _get_entity_id(hass, entry, "sensor", "current_set")

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
