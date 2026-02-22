"""Number platform for EV Charger Load Balancing."""

from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DEFAULT_MAX_CHARGER_CURRENT,
    DEFAULT_MIN_EV_CURRENT,
    DEFAULT_RAMP_UP_TIME,
    DOMAIN,
    MAX_CHARGER_CURRENT,
    MAX_RAMP_UP_TIME,
    MIN_CHARGER_CURRENT,
    MIN_EV_CURRENT_MAX,
    MIN_EV_CURRENT_MIN,
    MIN_RAMP_UP_TIME,
    get_device_info,
)
from .coordinator import EvLoadBalancerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EV LB number entities from a config entry."""
    coordinator: EvLoadBalancerCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    async_add_entities(
        [
            EvLbMaxChargerCurrentNumber(entry, coordinator),
            EvLbMinEvCurrentNumber(entry, coordinator),
            EvLbRampUpTimeNumber(entry, coordinator),
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

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the number entity."""
        self._attr_unique_id = f"{entry.entry_id}_max_charger_current"
        self._attr_native_value = DEFAULT_MAX_CHARGER_CURRENT
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup and sync with coordinator."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._coordinator.max_charger_current = float(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value, notify the coordinator, and trigger recomputation."""
        self._attr_native_value = value
        self._coordinator.max_charger_current = value
        self.async_write_ha_state()
        self._coordinator.async_recompute_from_current_state()


class EvLbMinEvCurrentNumber(RestoreNumber):
    """Number entity for the minimum EV current before shutdown (A)."""

    _attr_has_entity_name = True
    _attr_translation_key = "min_ev_current"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_native_min_value = MIN_EV_CURRENT_MIN
    _attr_native_max_value = MIN_EV_CURRENT_MAX
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the number entity."""
        self._attr_unique_id = f"{entry.entry_id}_min_ev_current"
        self._attr_native_value = DEFAULT_MIN_EV_CURRENT
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup and sync with coordinator."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._coordinator.min_ev_current = float(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Update the current value, notify the coordinator, and trigger recomputation."""
        self._attr_native_value = value
        self._coordinator.min_ev_current = value
        self.async_write_ha_state()
        self._coordinator.async_recompute_from_current_state()


class EvLbRampUpTimeNumber(RestoreNumber):
    """Number entity for the ramp-up cooldown period (seconds).

    After a current reduction, the balancer waits this many seconds before
    allowing the charging current to increase again.  This prevents rapid
    oscillation when household load fluctuates near the service limit.

    Very low values (< 10 s) may cause instability if your household load
    has spikes or is unpredictable.  The recommended minimum is 20–30 s.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ramp_up_time"
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_min_value = MIN_RAMP_UP_TIME
    _attr_native_max_value = MAX_RAMP_UP_TIME
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(
        self, entry: ConfigEntry, coordinator: EvLoadBalancerCoordinator
    ) -> None:
        """Initialise the number entity."""
        self._attr_unique_id = f"{entry.entry_id}_ramp_up_time"
        self._attr_native_value = DEFAULT_RAMP_UP_TIME
        self._attr_device_info = get_device_info(entry)
        self._coordinator = coordinator

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup and sync with coordinator."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self._attr_native_value = last.native_value
        self._coordinator.ramp_up_time_s = float(self._attr_native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Update the ramp-up cooldown and sync with the coordinator."""
        self._attr_native_value = value
        self._coordinator.ramp_up_time_s = value
        self.async_write_ha_state()
        self._coordinator.async_recompute_from_current_state()
