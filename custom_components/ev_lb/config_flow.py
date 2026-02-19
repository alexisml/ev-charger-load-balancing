"""Config flow for EV Charger Load Balancing."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .const import (
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_VOLTAGE,
    DEFAULT_MAX_SERVICE_CURRENT,
    DEFAULT_VOLTAGE,
    DOMAIN,
    MAX_SERVICE_CURRENT,
    MAX_VOLTAGE,
    MIN_SERVICE_CURRENT,
    MIN_VOLTAGE,
)


class EvLbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EV Charger Load Balancing."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step."""
        # Prevent multiple instances — this integration manages all chargers
        # from a single config entry (chargers are added via options flow).
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate power meter entity exists and is a sensor
            entity_id = user_input[CONF_POWER_METER_ENTITY]
            state = self.hass.states.get(entity_id)
            if state is None:
                errors[CONF_POWER_METER_ENTITY] = "entity_not_found"
            else:
                # Validation passed — create the config entry
                return self.async_create_entry(
                    title="EV Load Balancing",
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_POWER_METER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor"),
                ),
                vol.Required(
                    CONF_VOLTAGE,
                    default=DEFAULT_VOLTAGE,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_VOLTAGE,
                        max=MAX_VOLTAGE,
                        step=1.0,
                        unit_of_measurement="V",
                        mode=NumberSelectorMode.BOX,
                    ),
                ),
                vol.Required(
                    CONF_MAX_SERVICE_CURRENT,
                    default=DEFAULT_MAX_SERVICE_CURRENT,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SERVICE_CURRENT,
                        max=MAX_SERVICE_CURRENT,
                        step=1.0,
                        unit_of_measurement="A",
                        mode=NumberSelectorMode.BOX,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )
