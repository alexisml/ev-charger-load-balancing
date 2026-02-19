"""Number platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    MAX_CHARGER_CURRENT,
    MIN_CHARGER_CURRENT,
    MIN_EV_CURRENT_MAX,
    MIN_EV_CURRENT_MIN,
    get_device_info,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV LB number entities from a config entry."""
    async_add_entities(
        [
            EvLbMaxChargerCurrentNumber(entry),
            EvLbMinEvCurrentNumber(entry),
        ]
    )


class EvLbMaxChargerCurrentNumber(RestoreNumber):
    """Number entity for the per-charger maximum charging current (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "max_charger_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = MIN_CHARGER_CURRENT
    _attr_native_max_value = MAX_CHARGER_CURRENT
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the number entity."""
        self._attr_unique_id = f"{entry.entry_id}_max_charger_current"
        self._attr_native_value = DEFAULT_MAX_CHARGER_CURRENT
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._attr_native_value = value
        self.async_write_ha_state()


class EvLbMinEvCurrentNumber(RestoreNumber):
    """Number entity for the minimum EV current before shutdown (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "min_ev_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = MIN_EV_CURRENT_MIN
    _attr_native_max_value = MIN_EV_CURRENT_MAX
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the number entity."""
        self._attr_unique_id = f"{entry.entry_id}_min_ev_current"
        self._attr_native_value = DEFAULT_MIN_EV_CURRENT
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value."""
        self._attr_native_value = value
        self.async_write_ha_state()
