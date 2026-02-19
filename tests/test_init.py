"""Tests for the EV Charger Load Balancing integration setup and unload.

Tests cover:
- Integration loads successfully from a config entry
- Integration unloads successfully
- Config entry data is stored in hass.data
"""

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
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


async def test_setup_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Test successful setup of a config entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


async def test_unload_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Test successful unload of a config entry."""
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
