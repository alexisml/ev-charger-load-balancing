"""Constants for the EV Charger Load Balancing integration."""

DOMAIN = "ev_lb"

# Config keys (used in config_flow and config entry data)
CONF_POWER_METER_ENTITY = "power_meter_entity"
CONF_VOLTAGE = "voltage"
CONF_MAX_SERVICE_CURRENT = "max_service_current"

# Default values
DEFAULT_VOLTAGE = 230.0
DEFAULT_MAX_SERVICE_CURRENT = 32.0

# Validation limits
MIN_VOLTAGE = 100.0
MAX_VOLTAGE = 480.0
MIN_SERVICE_CURRENT = 1.0
MAX_SERVICE_CURRENT = 200.0
