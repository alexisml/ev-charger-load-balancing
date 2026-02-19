"""Constants for the EV Charger Load Balancing integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.helpers.device_registry import DeviceInfo

DOMAIN = "ev_lb"

# Platforms to set up
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Config keys (used in config_flow and config entry data)
CONF_POWER_METER_ENTITY = "power_meter_entity"
CONF_VOLTAGE = "voltage"
CONF_MAX_SERVICE_CURRENT = "max_service_current"

# Default values
DEFAULT_VOLTAGE = 230.0
DEFAULT_MAX_SERVICE_CURRENT = 32.0
DEFAULT_MAX_CHARGER_CURRENT = 32.0
DEFAULT_MIN_EV_CURRENT = 6.0

# Validation limits
MIN_VOLTAGE = 100.0
MAX_VOLTAGE = 480.0
MIN_SERVICE_CURRENT = 1.0
MAX_SERVICE_CURRENT = 200.0
MIN_CHARGER_CURRENT = 1.0
MAX_CHARGER_CURRENT = 80.0
MIN_EV_CURRENT_MIN = 1.0
MIN_EV_CURRENT_MAX = 32.0


def get_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return shared DeviceInfo for all entities in a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="EV Charger Load Balancer",
        manufacturer="ev_lb",
        model="Virtual Load Balancer",
        entry_type=None,
    )
