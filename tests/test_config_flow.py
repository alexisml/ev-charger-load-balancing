"""Tests for the EV Charger Load Balancing config flow.

Tests cover:
- Successful config entry creation with valid inputs
- Validation error when the power meter entity does not exist
- Default values for voltage and service current
- Single-instance protection (abort if already configured)
"""

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DOMAIN,
    UNAVAILABLE_BEHAVIOR_STOP,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    yield


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """Test a successful config flow with valid inputs."""
    # Create a fake sensor entity so validation passes
    hass.states.async_set("sensor.house_power_w", "3000")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POWER_METER_ENTITY: "sensor.house_power_w",
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "EV Load Balancing"
    assert result["data"] == {
        CONF_POWER_METER_ENTITY: "sensor.house_power_w",
        CONF_VOLTAGE: 230.0,
        CONF_MAX_SERVICE_CURRENT: 32.0,
        CONF_UNAVAILABLE_BEHAVIOR: UNAVAILABLE_BEHAVIOR_STOP,
        CONF_UNAVAILABLE_FALLBACK_CURRENT: 6.0,
    }


async def test_user_flow_entity_not_found(hass: HomeAssistant) -> None:
    """Test config flow shows error when power meter entity does not exist."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POWER_METER_ENTITY: "sensor.nonexistent_power_meter",
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_POWER_METER_ENTITY: "entity_not_found"}


async def test_user_flow_custom_values(hass: HomeAssistant) -> None:
    """Test config flow accepts non-default voltage and service current."""
    hass.states.async_set("sensor.grid_power", "1500")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_POWER_METER_ENTITY: "sensor.grid_power",
            CONF_VOLTAGE: 120.0,
            CONF_MAX_SERVICE_CURRENT: 100.0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_VOLTAGE] == 120.0
    assert result["data"][CONF_MAX_SERVICE_CURRENT] == 100.0


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    """Test config flow aborts when integration is already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_POWER_METER_ENTITY: "sensor.house_power_w",
            CONF_VOLTAGE: 230.0,
            CONF_MAX_SERVICE_CURRENT: 32.0,
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
