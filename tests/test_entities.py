"""Tests for the EV Charger Load Balancing entity platforms (PR-2).

Tests cover:
- All entity platforms (sensor, binary_sensor, number, switch) set up correctly
- Entities are linked to the charger device via device_info
- Unique IDs are stable and contain the config entry ID
- Sensor initial values are correct
- Number entities can be updated via async_set_native_value
- Switch entity can be toggled on/off
- All entities are removed on config entry unload
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Create a mock config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: "sensor.house_power_w",
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
        title="EV Load Balancing",
    )


async def _setup_entry(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Add and set up the config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


# ---------------------------------------------------------------------------
# Device registration
# ---------------------------------------------------------------------------


class TestDeviceRegistration:
    """Verify a device entry is created and entities are linked to it."""

    async def test_device_created(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """A device entry is created for the charger."""
        await _setup_entry(hass, mock_config_entry)

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, mock_config_entry.entry_id)}
        )
        assert device is not None
        assert device.name == "EV Charger Load Balancer"
        assert device.manufacturer == "ev_lb"

    async def test_all_entities_linked_to_device(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """All entities share the same device identifier."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(
            ent_reg, mock_config_entry.entry_id
        )
        assert len(entries) == 6  # 2 sensors + 1 binary_sensor + 2 numbers + 1 switch

        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get_device(
            identifiers={(DOMAIN, mock_config_entry.entry_id)}
        )
        for entity_entry in entries:
            assert entity_entry.device_id == device.id


# ---------------------------------------------------------------------------
# Unique IDs
# ---------------------------------------------------------------------------


class TestUniqueIds:
    """Verify unique IDs are stable and deterministic."""

    async def test_unique_ids_contain_entry_id(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Each entity's unique ID starts with the config entry ID."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entries = er.async_entries_for_config_entry(
            ent_reg, mock_config_entry.entry_id
        )
        expected_suffixes = {
            "current_set",
            "available_current",
            "active",
            "max_charger_current",
            "min_ev_current",
            "enabled",
        }
        actual_suffixes = set()
        for entity_entry in entries:
            assert entity_entry.unique_id.startswith(mock_config_entry.entry_id)
            suffix = entity_entry.unique_id[len(mock_config_entry.entry_id) + 1 :]
            actual_suffixes.add(suffix)
        assert actual_suffixes == expected_suffixes


# ---------------------------------------------------------------------------
# Sensor entities
# ---------------------------------------------------------------------------


class TestSensorEntities:
    """Verify sensor entity initial states."""

    async def test_current_set_sensor_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Current-set sensor starts at 0."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_current_set"
        )
        assert entry is not None
        state = hass.states.get(entry)
        assert state is not None
        assert float(state.state) == 0.0

    async def test_available_current_sensor_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Available-current sensor starts at 0."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get_entity_id(
            "sensor", DOMAIN, f"{mock_config_entry.entry_id}_available_current"
        )
        assert entry is not None
        state = hass.states.get(entry)
        assert state is not None
        assert float(state.state) == 0.0


# ---------------------------------------------------------------------------
# Binary sensor entity
# ---------------------------------------------------------------------------


class TestBinarySensorEntity:
    """Verify binary sensor entity initial state."""

    async def test_active_binary_sensor_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Active binary sensor starts as off."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{mock_config_entry.entry_id}_active"
        )
        assert entry is not None
        state = hass.states.get(entry)
        assert state is not None
        assert state.state == "off"


# ---------------------------------------------------------------------------
# Number entities
# ---------------------------------------------------------------------------


class TestNumberEntities:
    """Verify number entity initial values and set-value behavior."""

    async def test_max_charger_current_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Max charger current number starts at default."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_max_charger_current"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert float(state.state) == DEFAULT_MAX_CHARGER_CURRENT

    async def test_min_ev_current_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Min EV current number starts at default."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_min_ev_current"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert float(state.state) == DEFAULT_MIN_EV_CURRENT

    async def test_max_charger_current_set_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Max charger current number can be set to a new value."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_max_charger_current"
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 16.0},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert float(state.state) == 16.0

    async def test_min_ev_current_set_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Min EV current number can be set to a new value."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "number", DOMAIN, f"{mock_config_entry.entry_id}_min_ev_current"
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entity_id, "value": 8.0},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert float(state.state) == 8.0


# ---------------------------------------------------------------------------
# Switch entity
# ---------------------------------------------------------------------------


class TestSwitchEntity:
    """Verify switch entity initial state and toggle behavior."""

    async def test_enabled_switch_initial_value(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Enabled switch starts as on."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "switch", DOMAIN, f"{mock_config_entry.entry_id}_enabled"
        )
        assert entity_id is not None
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "on"

    async def test_enabled_switch_turn_off(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Enabled switch can be turned off."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "switch", DOMAIN, f"{mock_config_entry.entry_id}_enabled"
        )
        await hass.services.async_call(
            "switch",
            "turn_off",
            {"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state.state == "off"

    async def test_enabled_switch_turn_on_after_off(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """Enabled switch can be turned back on."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entity_id = ent_reg.async_get_entity_id(
            "switch", DOMAIN, f"{mock_config_entry.entry_id}_enabled"
        )
        assert entity_id is not None
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": entity_id}, blocking=True,
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id}, blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state.state == "on"


# ---------------------------------------------------------------------------
# Unload
# ---------------------------------------------------------------------------


class TestUnload:
    """Verify entities are removed on config entry unload."""

    async def test_entities_removed_on_unload(
        self, hass: HomeAssistant, mock_config_entry: MockConfigEntry
    ) -> None:
        """All entity states become unavailable when the config entry is unloaded."""
        await _setup_entry(hass, mock_config_entry)

        ent_reg = er.async_get(hass)
        entries_before = er.async_entries_for_config_entry(
            ent_reg, mock_config_entry.entry_id
        )
        assert len(entries_before) == 6

        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_config_entry.state is ConfigEntryState.NOT_LOADED

        # After unload, entity states should be unavailable
        for entity_entry in entries_before:
            state = hass.states.get(entity_entry.entity_id)
            assert state is None or state.state == "unavailable"
