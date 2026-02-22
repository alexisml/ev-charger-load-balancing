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
CONF_UNAVAILABLE_BEHAVIOR = "unavailable_behavior"
CONF_UNAVAILABLE_FALLBACK_CURRENT = "unavailable_fallback_current"

# Action config keys (script entity IDs for charger control)
CONF_ACTION_SET_CURRENT = "action_set_current"
CONF_ACTION_STOP_CHARGING = "action_stop_charging"
CONF_ACTION_START_CHARGING = "action_start_charging"

# Unavailable behavior options
UNAVAILABLE_BEHAVIOR_STOP = "stop"
UNAVAILABLE_BEHAVIOR_IGNORE = "ignore"
UNAVAILABLE_BEHAVIOR_SET_CURRENT = "set_current"
DEFAULT_UNAVAILABLE_BEHAVIOR = UNAVAILABLE_BEHAVIOR_STOP

# Default values
DEFAULT_VOLTAGE = 230.0
DEFAULT_MAX_SERVICE_CURRENT = 32.0
DEFAULT_MAX_CHARGER_CURRENT = 32.0
DEFAULT_MIN_EV_CURRENT = 6.0
DEFAULT_RAMP_UP_TIME = 30.0  # Seconds — cooldown before allowing current increase
DEFAULT_UNAVAILABLE_FALLBACK_CURRENT = 6.0  # Fallback current for "set_current" mode

# Dispatcher signal template — format with entry_id
SIGNAL_UPDATE_FMT = f"{DOMAIN}_update_{{entry_id}}"

# Ramp-up time config key (exposed as a number entity)
CONF_RAMP_UP_TIME = "ramp_up_time"

# Validation limits
MIN_VOLTAGE = 100.0
MAX_VOLTAGE = 480.0
MIN_SERVICE_CURRENT = 1.0
MAX_SERVICE_CURRENT = 200.0
MIN_CHARGER_CURRENT = 1.0
MAX_CHARGER_CURRENT = 80.0
MIN_EV_CURRENT_MIN = 1.0
MIN_EV_CURRENT_MAX = 32.0
MIN_RAMP_UP_TIME = 5.0  # Seconds — absolute minimum (very low values risk oscillation)
MAX_RAMP_UP_TIME = 5 * 60  # Seconds — 5 minutes maximum

# Safety guardrails — defense-in-depth limits that should never be exceeded
# regardless of user configuration or sensor values.
SAFETY_MAX_POWER_METER_W = 200_000.0  # 200 kW — reject readings above this as sensor errors

# Service name
SERVICE_SET_LIMIT = "set_limit"

# Diagnostic: reason for the last balancer action
REASON_POWER_METER_UPDATE = "power_meter_update"
REASON_MANUAL_OVERRIDE = "manual_override"
REASON_FALLBACK_UNAVAILABLE = "fallback_unavailable"
REASON_PARAMETER_CHANGE = "parameter_change"

# Balancer operational states — correspond to README state diagrams
STATE_STOPPED = "stopped"
STATE_ACTIVE = "active"
STATE_ADJUSTING = "adjusting"
STATE_RAMP_UP_HOLD = "ramp_up_hold"
STATE_DISABLED = "disabled"

# Event types for notable conditions
EVENT_METER_UNAVAILABLE = f"{DOMAIN}_meter_unavailable"
EVENT_OVERLOAD_STOP = f"{DOMAIN}_overload_stop"
EVENT_CHARGING_RESUMED = f"{DOMAIN}_charging_resumed"
EVENT_FALLBACK_ACTIVATED = f"{DOMAIN}_fallback_activated"
EVENT_ACTION_FAILED = f"{DOMAIN}_action_failed"

# Persistent notification ID templates (one per entry)
NOTIFICATION_METER_UNAVAILABLE_FMT = f"{DOMAIN}_meter_unavailable_{{entry_id}}"
NOTIFICATION_OVERLOAD_STOP_FMT = f"{DOMAIN}_overload_stop_{{entry_id}}"
NOTIFICATION_FALLBACK_ACTIVATED_FMT = f"{DOMAIN}_fallback_activated_{{entry_id}}"


def get_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return shared DeviceInfo for all entities in a config entry."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="EV Charger Load Balancer",
        manufacturer="ev_lb",
        model="Virtual Load Balancer",
        entry_type=None,
    )
