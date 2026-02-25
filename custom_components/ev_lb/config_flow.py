"""Config flow for EV Charger Load Balancing."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ACTION_SET_CURRENT,
    CONF_ACTION_START_CHARGING,
    CONF_ACTION_STOP_CHARGING,
    CONF_CHARGER_STATUS_ENTITY,
    CONF_MAX_SERVICE_CURRENT,
    CONF_POWER_METER_ENTITY,
    CONF_UNAVAILABLE_BEHAVIOR,
    CONF_UNAVAILABLE_FALLBACK_CURRENT,
    CONF_VOLTAGE,
    DEFAULT_MAX_SERVICE_CURRENT,
    DEFAULT_UNAVAILABLE_BEHAVIOR,
    DEFAULT_UNAVAILABLE_FALLBACK_CURRENT,
    DEFAULT_VOLTAGE,
    DOMAIN,
    MAX_CHARGER_CURRENT,
    MAX_SERVICE_CURRENT,
    MAX_VOLTAGE,
    MIN_SERVICE_CURRENT,
    MIN_VOLTAGE,
    UNAVAILABLE_BEHAVIOR_IGNORE,
    UNAVAILABLE_BEHAVIOR_SET_CURRENT,
    UNAVAILABLE_BEHAVIOR_STOP,
)
from ._log import get_logger

_LOGGER = get_logger(__name__)


class EvLbConfigFlow(ConfigFlow, domain=DOMAIN):  # pyright: ignore[reportGeneralTypeIssues,reportCallIssue]  # both needed: HA ConfigFlow domain= keyword is unknown without HA type stubs
    """Handle a config flow for EV Charger Load Balancing."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> EvLbOptionsFlow:
        """Return the options flow handler."""
        return EvLbOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step."""
        # Single-instance protection: only one config entry is allowed.
        # Multi-charger and multi-instance support are planned for a future PR
        # (see docs/development-memories/2026-02-19-research-plan.md).
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate power meter entity exists and is a sensor
            entity_id = user_input[CONF_POWER_METER_ENTITY]
            state = self.hass.states.get(entity_id)
            if state is None:
                errors[CONF_POWER_METER_ENTITY] = "entity_not_found"
                _LOGGER.debug(
                    "Config flow: entity %s not found", entity_id,
                )
            else:
                # Validation passed — create the config entry
                _LOGGER.debug(
                    "Config flow: creating entry (meter=%s, voltage=%.0f V, service=%.0f A)",
                    entity_id,
                    user_input.get(CONF_VOLTAGE, DEFAULT_VOLTAGE),
                    user_input.get(CONF_MAX_SERVICE_CURRENT, DEFAULT_MAX_SERVICE_CURRENT),
                )
                return self.async_create_entry(
                    title="EV Load Balancing",
                    data=user_input,
                )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_POWER_METER_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor", device_class="power"),
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
                vol.Required(
                    CONF_UNAVAILABLE_BEHAVIOR,
                    default=DEFAULT_UNAVAILABLE_BEHAVIOR,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(
                                value=UNAVAILABLE_BEHAVIOR_STOP,
                                label="Stop charging (0 A)",
                            ),
                            SelectOptionDict(
                                value=UNAVAILABLE_BEHAVIOR_IGNORE,
                                label="Ignore (keep last value)",
                            ),
                            SelectOptionDict(
                                value=UNAVAILABLE_BEHAVIOR_SET_CURRENT,
                                label="Set a specific current",
                            ),
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="unavailable_behavior",
                    ),
                ),
                vol.Optional(
                    CONF_UNAVAILABLE_FALLBACK_CURRENT,
                    default=DEFAULT_UNAVAILABLE_FALLBACK_CURRENT,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0.0,
                        max=MAX_CHARGER_CURRENT,
                        step=1.0,
                        unit_of_measurement="A",
                        mode=NumberSelectorMode.BOX,
                    ),
                ),
                vol.Optional(CONF_ACTION_SET_CURRENT): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(CONF_ACTION_STOP_CHARGING): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(CONF_ACTION_START_CHARGING): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(CONF_CHARGER_STATUS_ENTITY): EntitySelector(
                    EntitySelectorConfig(domain="sensor"),
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )


class EvLbOptionsFlow(OptionsFlow):
    """Handle options flow for EV Charger Load Balancing.

    Allows users to add, change, or remove action scripts after initial
    setup without needing to delete and re-create the config entry.
    """

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the options flow step."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pre-fill with current values (options take priority, then data)
        current = {**self.config_entry.data, **self.config_entry.options}

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_ACTION_SET_CURRENT,
                    description={
                        "suggested_value": current.get(CONF_ACTION_SET_CURRENT),
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(
                    CONF_ACTION_STOP_CHARGING,
                    description={
                        "suggested_value": current.get(CONF_ACTION_STOP_CHARGING),
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(
                    CONF_ACTION_START_CHARGING,
                    description={
                        "suggested_value": current.get(CONF_ACTION_START_CHARGING),
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain="script"),
                ),
                vol.Optional(
                    CONF_CHARGER_STATUS_ENTITY,
                    description={
                        "suggested_value": current.get(CONF_CHARGER_STATUS_ENTITY),
                    },
                ): EntitySelector(
                    EntitySelectorConfig(domain="sensor"),
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=data_schema,
        )
