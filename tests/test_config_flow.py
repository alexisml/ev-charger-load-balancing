"""Tests for the EV Charger Load Balancing config flow.

Tests cover:
- Successful config entry creation with valid inputs
- Validation error when the power meter entity does not exist
- Default values for voltage and service current
- Single-instance protection (abort if already configured)
- Power meter EntitySelector is restricted to power device-class sensors
"""

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import EntitySelector

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ev_lb.const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DOMAIN,
    UNAVAILABLE_BEHAVIOR_STOP,
)


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


async def test_power_meter_selector_filters_by_power_device_class(hass: HomeAssistant) -> None:
    """Test that the power meter field only accepts power device-class sensors.

    Users should only be shown sensors that measure instantaneous power (in
    Watts), preventing accidental selection of unrelated sensors such as
    temperature or humidity.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    schema: vol.Schema = result["data_schema"]
    # Locate the validator for the power meter field
    power_meter_validator = next(
        v
        for k, v in schema.schema.items()
        if isinstance(k, vol.Required) and k.schema == CONF_POWER_METER_ENTITY
    )
    assert isinstance(power_meter_validator, EntitySelector)
    assert power_meter_validator.config.get("device_class") == ["power"]


async def test_options_flow_opens_without_error(
    hass: HomeAssistant, mock_config_entry_no_actions: MockConfigEntry
) -> None:
    """Test that the Configure button opens the options form without a 500 error.

    Regression test for: AttributeError when HA tries to set config_entry on
    EvLbOptionsFlow because OptionsFlow.config_entry is a read-only property
    in newer HA versions.
    """
    mock_config_entry_no_actions.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry_no_actions.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"


async def test_options_flow_saves_action_scripts(
    hass: HomeAssistant, mock_config_entry_no_actions: MockConfigEntry
) -> None:
    """Test that users can set action scripts via the Configure dialog.

    Saving the options form should store the selected scripts so the
    integration can call them when controlling the charger.
    """
    mock_config_entry_no_actions.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry_no_actions.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_ACTION_SET_CURRENT: "script.ev_lb_set_current",
            CONF_ACTION_STOP_CHARGING: "script.ev_lb_stop_charging",
            CONF_ACTION_START_CHARGING: "script.ev_lb_start_charging",
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACTION_SET_CURRENT] == "script.ev_lb_set_current"
    assert result["data"][CONF_ACTION_STOP_CHARGING] == "script.ev_lb_stop_charging"
    assert result["data"][CONF_ACTION_START_CHARGING] == "script.ev_lb_start_charging"
