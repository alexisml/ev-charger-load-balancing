"""Sensor platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, get_device_info
from .coordinator import EvLoadBalancerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV LB sensor entities from a config entry."""
    coordinator: EvLoadBalancerCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        [
            EvLbCurrentSetSensor(entry, coordinator),
            EvLbAvailableCurrentSensor(entry, coordinator),
            EvLbLastActionReasonSensor(entry, coordinator),
            EvLbBalancerStateSensor(entry, coordinator),
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

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_current_set"
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value and subscribe to coordinator updates."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
            self._coordinator.current_set_a = float(last.native_value)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_update,
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Update sensor state from coordinator."""
        self._attr_native_value = self._coordinator.current_set_a
        self.async_write_ha_state()


class EvLbAvailableCurrentSensor(RestoreSensor):
    """Sensor showing the computed available current headroom (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "available_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_value = 0.0

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_available_current"
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value and subscribe to coordinator updates."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_update,
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Update sensor state from coordinator."""
        self._attr_native_value = self._coordinator.available_current_a
        self.async_write_ha_state()


class EvLbLastActionReasonSensor(RestoreSensor):
    """Diagnostic sensor showing why the charger current was last changed."""

    _attr_has_entity_name = True
    _attr_translation_key = "last_action_reason"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_value = None

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_last_action_reason"
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value and subscribe to coordinator updates."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_update,
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Update sensor state from coordinator."""
        self._attr_native_value = self._coordinator.last_action_reason
        self.async_write_ha_state()


class EvLbBalancerStateSensor(RestoreSensor):
    """Diagnostic sensor showing the balancer's current operational state.

    Maps to the charger state transitions in the README diagrams:
    stopped, active, adjusting, ramp_up_hold,
    meter_unavailable_stopped, meter_unavailable_fallback,
    meter_unavailable_ignored, disabled.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "balancer_state"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_value = None

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the sensor."""
        self._attr_unique_id = f"{entry.entry_id}_balancer_state"
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value and subscribe to coordinator updates."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                self._coordinator.signal_update,
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Update sensor state from coordinator."""
        self._attr_native_value = self._coordinator.balancer_state
        self.async_write_ha_state()
