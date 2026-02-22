"""Integration tests for boundary values, edge cases, and invalid inputs.

Tests exercise exact limit values (at, one above, one below), zero values,
negative values, and extreme inputs through the full integration stack
(config entry → coordinator → entities → actions).
"""

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.ev_lb.const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    DOMAIN,
    MAX_CHARGER_CURRENT,
    MIN_CHARGER_CURRENT,
    MIN_EV_CURRENT_MAX,
    MIN_EV_CURRENT_MIN,
    SAFETY_MAX_POWER_METER_W,
    SERVICE_SET_LIMIT,
)
from conftest import (
    POWER_METER,
    SET_CURRENT_SCRIPT,
    STOP_CHARGING_SCRIPT,
    START_CHARGING_SCRIPT,
    setup_integration,
    get_entity_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    hass: HomeAssistant,
    voltage: float = 230.0,
    max_service_a: float = 32.0,
    with_actions: bool = False,
) -> MockConfigEntry:
    """Create a config entry with custom voltage/service current."""
    data = {
        CONF_POWER_METER_ENTITY: POWER_METER,
        CONF_VOLTAGE: voltage,
        CONF_MAX_SERVICE_CURRENT: max_service_a,
    }
    if with_actions:
        data[CONF_ACTION_SET_CURRENT] = SET_CURRENT_SCRIPT
        data[CONF_ACTION_STOP_CHARGING] = STOP_CHARGING_SCRIPT
        data[CONF_ACTION_START_CHARGING] = START_CHARGING_SCRIPT
    return MockConfigEntry(
        domain=DOMAIN,
        data=data,
        title="EV Load Balancing",
    )


# ---------------------------------------------------------------------------
# Number entity boundary values
# ---------------------------------------------------------------------------


class TestMaxChargerCurrentBoundaries:
    """Boundary tests for the max_charger_current number entity (1–80 A).

    Validates behavior at exact limits, one above, one below, and zero/negative.
    """

    async def test_set_exactly_at_minimum_limit(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting max charger current to exactly 1 A (minimum) is accepted and caps charging."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        max_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")
        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Set max to exactly MIN_CHARGER_CURRENT (1 A)
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": MIN_CHARGER_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        # current should be capped, but 1 A < min_ev (6 A) → stop
        assert float(hass.states.get(current_set_id).state) == 0.0

    async def test_set_exactly_at_maximum_limit(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting max charger current to exactly 80 A (maximum) is accepted and stored."""
        await setup_integration(hass, mock_config_entry)

        max_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]

        # Set max to exactly MAX_CHARGER_CURRENT (80 A)
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": MAX_CHARGER_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Entity and coordinator should both reflect 80 A
        assert float(hass.states.get(max_id).state) == MAX_CHARGER_CURRENT
        assert coordinator.max_charger_current == MAX_CHARGER_CURRENT

    async def test_set_one_above_maximum_is_rejected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting max charger current above 80 A is rejected by HA validation."""
        await setup_integration(hass, mock_config_entry)

        max_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")

        # HA's number entity rejects values outside [min, max] range
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "number", "set_value",
                {"entity_id": max_id, "value": MAX_CHARGER_CURRENT + 1},
                blocking=True,
            )


class TestMinEvCurrentBoundaries:
    """Boundary tests for the min_ev_current number entity (1–32 A).

    Validates behavior at exact limits, and verifies that a high minimum
    threshold correctly stops charging when headroom is insufficient.
    """

    async def test_set_exactly_at_minimum_limit(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting min EV current to exactly 1 A (minimum) allows charging at very low headroom."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        min_id = get_entity_id(hass, mock_config_entry, "number", "min_ev_current")
        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # First, create a scenario where the charger stops with default min (6 A):
        # 7130 W → available = 32 - 31 = 1 A → raw_target = 0 + 1 = 1 < 6 → stop
        hass.states.async_set(POWER_METER, "7130")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 0.0

        # Now lower min to 1 A → recompute with meter=7130 → available=1,
        # raw_target=0+1=1, clamped=1, 1≥1 → charge at 1 A
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": min_id, "value": MIN_EV_CURRENT_MIN},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 1.0
        assert hass.states.get(active_id).state == "on"

    async def test_set_exactly_at_maximum_limit(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting min EV current to exactly 32 A (maximum) stops charging when headroom is insufficient."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        min_id = get_entity_id(hass, mock_config_entry, "number", "min_ev_current")
        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Charge at moderate load: 3000 W → 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Set min to 32 A → recompute: raw_target = 18 + 18.96 = 36.96,
        # clamped to min(36.96, 32) = 32, floored = 32, 32 ≥ 32 → charge at 32 A
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": min_id, "value": MIN_EV_CURRENT_MAX},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 32.0

        # Now increase load to 8000 W → available = 32 - 34.78 = -2.78
        # raw_target = 32 + (-2.78) = 29.22, clamped = 29, 29 < 32 → stop
        hass.states.async_set(POWER_METER, "8000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_one_above_maximum_is_rejected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting min EV current above 32 A is rejected by HA validation."""
        await setup_integration(hass, mock_config_entry)

        min_id = get_entity_id(hass, mock_config_entry, "number", "min_ev_current")

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                "number", "set_value",
                {"entity_id": min_id, "value": MIN_EV_CURRENT_MAX + 1},
                blocking=True,
            )


# ---------------------------------------------------------------------------
# set_limit service boundary values
# ---------------------------------------------------------------------------


class TestSetLimitBoundaryValues:
    """Boundary tests for the ev_lb.set_limit service.

    The service schema validates current_a ≥ 0. Values above charger max
    are clamped by the coordinator. Negative values are rejected.
    """

    async def test_set_limit_zero_stops_charging(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Setting limit to exactly 0 A stops charging (below min EV current)."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        calls.clear()

        # Set limit to 0 A → below min → stop
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT, {"current_a": 0.0}, blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"
        stop_calls = [c for c in calls if c.data["entity_id"] == STOP_CHARGING_SCRIPT]
        assert len(stop_calls) >= 1

    async def test_set_limit_exactly_at_min_ev_current(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting limit to exactly 6 A (default min EV current) is accepted and applied."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Set limit to exactly min EV current (6 A)
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT,
            {"current_a": DEFAULT_MIN_EV_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == DEFAULT_MIN_EV_CURRENT
        assert hass.states.get(active_id).state == "on"

    async def test_set_limit_one_below_min_ev_stops(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting limit to 5 A (one below default min EV 6 A) stops charging."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Set limit to one below min
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT,
            {"current_a": DEFAULT_MIN_EV_CURRENT - 1.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_set_limit_above_charger_max_is_clamped(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Setting limit to 100 A (above charger max 32 A) is clamped to 32 A."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        # Set limit far above max charger current
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT, {"current_a": 100.0}, blocking=True,
        )
        await hass.async_block_till_done()

        # Clamped to default max charger current (32 A)
        assert float(hass.states.get(current_set_id).state) == DEFAULT_MAX_CHARGER_CURRENT

    async def test_set_limit_negative_is_rejected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Negative current_a is rejected by the service schema validation."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        before = float(hass.states.get(current_set_id).state)

        # Negative value should raise a validation error
        with pytest.raises(vol.MultipleInvalid):
            await hass.services.async_call(
                DOMAIN, SERVICE_SET_LIMIT,
                {"current_a": -5.0},
                blocking=True,
            )
        await hass.async_block_till_done()

        # State should remain unchanged
        assert float(hass.states.get(current_set_id).state) == before


# ---------------------------------------------------------------------------
# Power meter edge values
# ---------------------------------------------------------------------------


class TestPowerMeterBoundaryValues:
    """Boundary tests for power meter input values.

    Validates behavior with zero power, negative power (generation/export),
    and extreme values that push available current beyond limits.
    """

    async def test_zero_power_gives_max_available(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Zero house power gives full service capacity to the charger (capped at max)."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # First set a non-zero value so the transition to "0" fires an event
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        hass.states.async_set(POWER_METER, "0")
        await hass.async_block_till_done()

        # available = 32 - 0/230 = 32 A → capped at max charger (32 A)
        assert float(hass.states.get(current_set_id).state) == DEFAULT_MAX_CHARGER_CURRENT

    async def test_negative_power_solar_export(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Negative power (solar export) gives more than service capacity but is capped at charger max."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Negative power: exporting 2300 W → available = 32 + 10 = 42 A
        # But capped at charger max (32 A)
        hass.states.async_set(POWER_METER, "-2300")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == DEFAULT_MAX_CHARGER_CURRENT

    async def test_power_exactly_at_service_limit(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """House load exactly at service limit leaves zero available — charging stops."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # 32 A × 230 V = 7360 W → available = 32 - 32 = 0 A → below min → stop
        hass.states.async_set(POWER_METER, "7360")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_power_one_watt_below_stopping_threshold(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """House load just below the point where min EV current (6 A) is available — charger operates at minimum."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # For 6 A available: 32 - P/230 ≥ 6 → P ≤ 5980 W
        # 5980 W → available = 32 - (5980/230) = 32 - 26 = 6 A = min → charge at 6 A
        hass.states.async_set(POWER_METER, "5980")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == DEFAULT_MIN_EV_CURRENT
        assert hass.states.get(active_id).state == "on"

    async def test_power_one_watt_above_stopping_threshold(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """House load just above the point where min EV is unavailable — charging stops."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # 6210 W → available = 32 - (6210/230) = 32 - 27 = 5 A < min (6 A) → stop
        hass.states.async_set(POWER_METER, "6210")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_non_numeric_meter_value_is_ignored(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """A non-numeric meter value (e.g. 'abc') is silently ignored without crashing."""
        await setup_integration(hass, mock_config_entry)

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Send garbage value → should be ignored, state unchanged
        hass.states.async_set(POWER_METER, "abc")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

    async def test_extremely_large_power_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """An extremely large power value (1 MW) is rejected by the safety guardrail."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # 1 MW (> 200 kW safety limit) → rejected as likely sensor error
        # State remains at initial 0.0 A
        hass.states.async_set(POWER_METER, "1000000")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"


# ---------------------------------------------------------------------------
# Charging current at exact boundary between operating and stopping
# ---------------------------------------------------------------------------


class TestChargingCurrentExactBoundaries:
    """Tests for the exact boundary where available current meets min EV current.

    Verifies the at/above/below pattern around the minimum threshold
    with action verification.
    """

    async def test_available_exactly_at_min_with_actions(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Available current exactly at min EV (6 A) charges at that rate with correct actions."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")

        # 5980 W → available = 32 - (5980/230) = 32 - 26 = 6 A = min → charge
        hass.states.async_set(POWER_METER, "5980")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == DEFAULT_MIN_EV_CURRENT
        assert hass.states.get(active_id).state == "on"

        # start_charging + set_current should fire
        start_calls = [c for c in calls if c.data["entity_id"] == START_CHARGING_SCRIPT]
        set_calls = [c for c in calls if c.data["entity_id"] == SET_CURRENT_SCRIPT]
        assert len(start_calls) >= 1
        assert len(set_calls) >= 1
        assert set_calls[-1].data["variables"]["current_a"] == DEFAULT_MIN_EV_CURRENT

    async def test_available_one_amp_above_min_charges(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Available current one amp above min (7 A) charges normally."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")

        # 5750 W → available = 32 - (5750/230) = 32 - 25 = 7 A > min → charge at 7 A
        hass.states.async_set(POWER_METER, "5750")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 7.0

    async def test_available_one_amp_below_min_stops_with_actions(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Available current one amp below min (5 A) stops charging and fires stop action."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry_with_actions, "binary_sensor", "active")

        # 6210 W → available = 32 - (6210/230) = 32 - 27 = 5 A < min (6 A) → stop
        hass.states.async_set(POWER_METER, "6210")
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_available_exactly_at_max_charger_caps(
        self,
        hass: HomeAssistant,
        mock_config_entry_with_actions: MockConfigEntry,
    ) -> None:
        """Available current at exactly charger max (32 A) charges at max."""
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, mock_config_entry_with_actions)
        coordinator = hass.data[DOMAIN][mock_config_entry_with_actions.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry_with_actions, "sensor", "current_set")

        # First set a non-zero value, then 0 W to trigger event
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        calls.clear()
        hass.states.async_set(POWER_METER, "0")
        await hass.async_block_till_done()

        # available = 32 A → capped at max (32 A)
        assert float(hass.states.get(current_set_id).state) == DEFAULT_MAX_CHARGER_CURRENT

    async def test_available_one_above_max_still_caps(
        self, hass: HomeAssistant,
    ) -> None:
        """Available current above charger max is capped — extra headroom is unused."""
        entry = _make_entry(hass, max_service_a=40.0)
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # First set a non-zero value, then 0 W
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        hass.states.async_set(POWER_METER, "0")
        await hass.async_block_till_done()

        # available = 40 A > max charger (32 A) → caps at 32 A
        assert float(hass.states.get(current_set_id).state) == DEFAULT_MAX_CHARGER_CURRENT


# ---------------------------------------------------------------------------
# Safety guardrails — output never exceeds service or charger limits
# ---------------------------------------------------------------------------


class TestOutputNeverExceedsServiceLimit:
    """Verify the charger output never exceeds the service or charger limit.

    Tests both directions: when charger max > service limit the output is
    capped at the service limit, and when service limit > charger max the
    output is capped at the charger max.
    """

    async def test_charger_max_above_service_capped_in_normal_operation(
        self, hass: HomeAssistant,
    ) -> None:
        """When charger max (80 A) > service limit (20 A), output never exceeds 20 A."""
        entry = _make_entry(hass, max_service_a=20.0)
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        # Raise charger max to 80 A
        max_id = get_entity_id(hass, entry, "number", "max_charger_current")
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": MAX_CHARGER_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Low load: 1000 W → available = 20 - 4.35 = 15.65 A
        # Accumulating: raw_target = prev + available could exceed 20 A
        # Safety clamp ensures output ≤ min(80, 20) = 20 A
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        output = float(hass.states.get(current_set_id).state)
        assert output <= 20.0, f"Output {output} A exceeds service limit 20 A"
        assert output > 0.0, "Charger should be active with low load"

    async def test_set_limit_above_service_is_safety_clamped(
        self, hass: HomeAssistant,
    ) -> None:
        """set_limit to 50 A when service limit is 20 A is clamped to 20 A by safety clamp."""
        entry = _make_entry(hass, max_service_a=20.0)
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        # Raise charger max to 80 A so clamp_current doesn't catch it first
        max_id = get_entity_id(hass, entry, "number", "max_charger_current")
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": MAX_CHARGER_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Start charging
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        # set_limit to 50 A — clamp_current caps at 80 A, safety clamp caps at 20 A
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT, {"current_a": 50.0}, blocking=True,
        )
        await hass.async_block_till_done()

        output = float(hass.states.get(current_set_id).state)
        assert output <= 20.0, f"Output {output} A exceeds service limit 20 A"
        assert output == 20.0

    async def test_set_limit_sends_safe_current_to_actions(
        self, hass: HomeAssistant,
    ) -> None:
        """The current_a variable sent to action scripts is safety-clamped to service limit."""
        entry = _make_entry(hass, max_service_a=20.0, with_actions=True)
        calls = async_mock_service(hass, "script", "turn_on")
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        # Raise charger max to 80 A
        max_id = get_entity_id(hass, entry, "number", "max_charger_current")
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": MAX_CHARGER_CURRENT},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Start charging at moderate load — output will be at some value ≤ 20 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")
        current_before = float(hass.states.get(current_set_id).state)
        assert current_before > 0.0
        calls.clear()

        # set_limit to 50 A — exceeds service limit (20 A) so safety clamp kicks in
        # Since this differs from current value, set_current action will fire
        await hass.services.async_call(
            DOMAIN, SERVICE_SET_LIMIT, {"current_a": 50.0}, blocking=True,
        )
        await hass.async_block_till_done()

        output = float(hass.states.get(current_set_id).state)
        assert output <= 20.0, f"Output {output} A exceeds service limit 20 A"

        # Verify the action received the safe value
        set_calls = [
            c for c in calls
            if c.data.get("entity_id") == SET_CURRENT_SCRIPT
        ]
        if set_calls:
            action_current = set_calls[-1].data["variables"]["current_a"]
            assert action_current <= 20.0, (
                f"Action received {action_current} A, exceeds service limit 20 A"
            )

    async def test_fallback_current_capped_at_service_limit(
        self, hass: HomeAssistant,
    ) -> None:
        """Fallback current in set_current mode is capped at service limit, not just charger max."""
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_POWER_METER_ENTITY: POWER_METER,
                CONF_VOLTAGE: 230.0,
                CONF_MAX_SERVICE_CURRENT: 16.0,
                CONF_UNAVAILABLE_BEHAVIOR: "set_current",
                CONF_UNAVAILABLE_FALLBACK_CURRENT: 32.0,
            },
            title="EV Load Balancing",
        )
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Start charging
        hass.states.async_set(POWER_METER, "1000")
        await hass.async_block_till_done()

        # Meter goes unavailable → fallback configured at 32 A
        # but service limit is 16 A → safety clamp to 16 A
        hass.states.async_set(POWER_METER, "unavailable")
        await hass.async_block_till_done()

        output = float(hass.states.get(current_set_id).state)
        assert output <= 16.0, f"Fallback {output} A exceeds service limit 16 A"

    async def test_output_never_exceeds_charger_max(
        self, hass: HomeAssistant,
    ) -> None:
        """When service limit (40 A) > charger max (10 A), output is capped at charger max."""
        entry = _make_entry(hass, max_service_a=40.0)
        await setup_integration(hass, entry)
        coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        # Lower charger max to 10 A
        max_id = get_entity_id(hass, entry, "number", "max_charger_current")
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": 10.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        current_set_id = get_entity_id(hass, entry, "sensor", "current_set")

        # Very low load: 230 W → available = 40 - 1 = 39 A, capped at 10 A
        hass.states.async_set(POWER_METER, "230")
        await hass.async_block_till_done()

        output = float(hass.states.get(current_set_id).state)
        assert output <= 10.0, f"Output {output} A exceeds charger max 10 A"
        assert output == 10.0


# ---------------------------------------------------------------------------
# Power meter safety — reject insane sensor readings
# ---------------------------------------------------------------------------


class TestPowerMeterSafetyGuardrails:
    """Verify insane power meter readings are rejected as sensor errors."""

    async def test_reading_above_200kw_is_rejected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """A power meter reading above 200 kW is rejected and state is unchanged."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Reading above safety limit → rejected, state unchanged
        hass.states.async_set(POWER_METER, str(SAFETY_MAX_POWER_METER_W + 1))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

    async def test_reading_exactly_at_200kw_is_accepted(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """A power meter reading of exactly 200 kW is accepted (within the limit)."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        active_id = get_entity_id(hass, mock_config_entry, "binary_sensor", "active")

        # Exactly 200,000 W → accepted, massive overload → stop
        hass.states.async_set(POWER_METER, str(SAFETY_MAX_POWER_METER_W))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 0.0
        assert hass.states.get(active_id).state == "off"

    async def test_negative_reading_above_200kw_is_rejected(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """A negative power meter reading below -200 kW is rejected as sensor error."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Insane negative reading → rejected
        hass.states.async_set(POWER_METER, str(-(SAFETY_MAX_POWER_METER_W + 1)))
        await hass.async_block_till_done()

        assert float(hass.states.get(current_set_id).state) == 18.0

    async def test_parameter_change_with_insane_meter_is_ignored(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry,
    ) -> None:
        """Changing a parameter when the meter shows an insane value doesn't produce unsafe output."""
        await setup_integration(hass, mock_config_entry)
        coordinator = hass.data[DOMAIN][mock_config_entry.entry_id]["coordinator"]
        coordinator._ramp_up_time_s = 0.0

        current_set_id = get_entity_id(hass, mock_config_entry, "sensor", "current_set")
        max_id = get_entity_id(hass, mock_config_entry, "number", "max_charger_current")

        # Start charging at 18 A
        hass.states.async_set(POWER_METER, "3000")
        await hass.async_block_till_done()
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Set meter to insane value (simulating sensor glitch)
        hass.states.async_set(POWER_METER, "500000")
        await hass.async_block_till_done()

        # State unchanged because reading was rejected
        assert float(hass.states.get(current_set_id).state) == 18.0

        # Now change a parameter — recompute should also skip insane meter value
        await hass.services.async_call(
            "number", "set_value",
            {"entity_id": max_id, "value": 20.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        # Output should still be 18 A (not recomputed with insane meter)
        assert float(hass.states.get(current_set_id).state) == 18.0
