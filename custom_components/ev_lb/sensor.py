"""Sensor platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import get_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV LB sensor entities from a config entry."""
    async_add_entities(
        [
            EvLbCurrentSetSensor(entry),
            EvLbAvailableCurrentSensor(entry),
        ]
    )


class EvLbCurrentSetSensor(RestoreSensor):
    """Sensor showing the last requested charging current (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "current_set"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_value = 0.0

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_current_set"
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value


class EvLbAvailableCurrentSensor(RestoreSensor):
    """Sensor showing the computed available current headroom (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "available_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_value = 0.0

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_available_current"
        self._attr_device_info = get_device_info(entry)

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
