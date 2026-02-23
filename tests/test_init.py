"""Tests for the EV Charger Load Balancing integration setup and unload.

Tests cover:
- Integration loads successfully from a config entry
- Integration unloads successfully
- Config entry data is stored in hass.data
- Service registration is idempotent (no duplicate registration when called twice)
"""

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntryState

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import DOMAIN, SERVICE_SET_LIMIT
from custom_components.ev_lb import _register_services
from conftest import setup_integration


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


async def test_register_services_is_idempotent(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Service registration is safely called a second time without creating a duplicate."""
    await setup_integration(hass, mock_config_entry)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_LIMIT)

    # Calling _register_services again (e.g. from a hypothetical second entry)
    # must not raise and must leave the service registered.
    _register_services(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_SET_LIMIT)
