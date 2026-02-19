"""Switch platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    """Set up EV LB switch entities from a config entry."""
    async_add_entities([EvLbEnabledSwitch(entry)])


class EvLbEnabledSwitch(SwitchEntity, RestoreEntity):
    """Switch to enable or disable dynamic load balancing."""

    _attr_has_entity_name = True
    _attr_translation_key = "enabled"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_is_on = True

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the switch."""
        self._attr_unique_id = f"{entry.entry_id}_enabled"
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state is not None:
            self._attr_is_on = last.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on load balancing."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off load balancing."""
        self._attr_is_on = False
        self.async_write_ha_state()
