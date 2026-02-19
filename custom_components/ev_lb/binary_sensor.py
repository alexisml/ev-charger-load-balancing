"""Binary sensor platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import get_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV LB binary sensor entities from a config entry."""
    async_add_entities([EvLbActiveBinarySensor(entry)])


class EvLbActiveBinarySensor(BinarySensorEntity, RestoreEntity):
    """Binary sensor indicating whether load balancing is actively controlling the charger."""

    _attr_has_entity_name = True
    _attr_translation_key = "active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_is_on = False

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the binary sensor."""
        self._attr_unique_id = f"{entry.entry_id}_active"
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state is not None:
            self._attr_is_on = last.state == "on"
